"""context manager for clangd client

- read and write are separate process
"""


import json
import logging
import os
import re
import subprocess
import threading
import abc

from io import BytesIO, SEEK_SET, SEEK_END
from typing import Callable, Optional, Union

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class JSONMessage:
    """JSON message

    Params:
    - data : mapped data
    """

    def __init__(self, data: dict):
        data.update({"jsonrpc": "2.0"})
        self.data = data

    def __repr__(self):
        return str(self.data)

    @property
    def id_(self):
        return self.data.get("id")

    @id_.setter
    def id_(self, value):
        self.data["id"] = value

    def _to_string(self):
        return json.dumps(self.data)

    def to_string(self):
        """dump to string"""
        return self._to_string()

    def to_bytes(self):
        """dump to bytes"""
        with BytesIO() as buf:
            message = self._to_string().encode("utf-8")
            buf.write(str.encode("Content-Length: %s\r\n" % len(message), "ascii"))
            buf.write(b"\r\n")  # content separator
            buf.write(message)
            buf.write(os.linesep.encode("ascii"))
            return buf.getvalue()

    @classmethod
    def from_string(cls, message: str):
        """new JSONMessage from string"""
        return cls(json.loads(message))

    def is_request(self) -> bool:
        """is request message"""
        return "method" in self.data

    def is_response(self) -> bool:
        """is response message"""
        return "result" in self.data or "error" in self.data


class RequestMessage(JSONMessage):
    """Request message

    Params:
    - id : Optional[Union[int,str]], should not defined for notification
    - method : str
    - params : Optional[dict], should be json object or {}
    """

    def __init__(self, id_: int, method: str, params: dict = None):
        data = {"method": method, "params": params or {}}
        if id_ is not None:
            data["id"] = id_

        super().__init__(data)

    @property
    def method(self):
        return self.data["method"]

    @method.setter
    def method(self, value):
        self.data["method"] = value

    @property
    def params(self):
        return self.data["params"]

    @params.setter
    def params(self, value):
        self.data["params"] = value

    @classmethod
    def from_string(cls, message: str):
        """new RequestMessage from string"""
        decoded = json.loads(message)
        return cls(decoded.get("id"), decoded["method"], decoded.get("params"))

    @classmethod
    def from_json_transport(cls, message: JSONMessage):
        """new RequestMessage from JSONMessage"""
        data = message.data
        return cls(data.get("id"), data["method"], data.get("params"))


class ResponseMessage(JSONMessage):
    """Response message

    Params:
    - id : Union[int, str], should be defined for result
    - result : Optional[dict, list], should not defined if error
    - error : Optional[dict], should not defined if result available
    """

    def __init__(self, id_: int, result: object = None, error: dict = None):
        data = {"id": id_}
        if error:
            data["error"] = error
        else:
            # result only defined if no error
            data["result"] = result

        super().__init__(data)

    @property
    def result(self):
        return self.data.get("result")

    @result.setter
    def result(self, value):
        self.data["result"] = value

    @property
    def error(self):
        return self.data.get("error")

    @error.setter
    def error(self, value):
        self.data["error"] = value

    @classmethod
    def from_string(cls, message: str):
        """new ResponseMessage from string"""
        decoded = json.loads(message)
        return cls(decoded.get("id"), decoded.get("result"), decoded.get("error"))

    @classmethod
    def from_json_transport(cls, message: JSONMessage):
        """new ResponseMessage from JSONMessage"""
        data = message.data
        return cls(data.get("id"), data.get("result"), data.get("error"))


class InvalidMessage(ValueError):
    """message error"""


class Stream:
    r"""stream object

    This class handle JSONRPC stream format
        '<header>\r\n<content>'
    
    Header items must seperated by '\r\n'
    """

    def __init__(self):
        self.buffer = BytesIO()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        """write stream data"""

        with self._lock:
            # write always on end of file
            self.buffer.seek(0, SEEK_END)
            self.buffer.write(data)

    def _get_content_length(self, buf: BytesIO):
        """get Content-Length"""

        header = []
        while True:
            line = buf.readline()
            if not line:
                raise EOFError("End Of File")

            if line == b"\r\n":
                break
            header.append(line)

        if not header:
            raise InvalidMessage("unable parse header")

        s_header = b"".join(header).decode("utf-8")
        pattern = re.compile(r"^Content\-Length: (\d+)")

        for line in s_header.splitlines():
            match = pattern.match(line)
            if match:
                return int(match.group(1))

        raise InvalidMessage("unable find Content-Length")

    def read(self) -> bytes:
        """read stream data

        Return:
            content or empty bytes

        Raises:
            InvalidMessage
            EOFError
        """

        with self._lock:

            # in reading, read always start from beginning of header
            self.buffer.seek(0, SEEK_SET)

            _content_length = self._get_content_length(self.buffer)
            content = self.buffer.read(_content_length)

            recv_len = len(content)
            if recv_len < _content_length:
                # wait content
                LOGGER.debug("content incomplete, wait next")
                return b""

            elif recv_len > _content_length:
                raise InvalidMessage(
                    "content overflow, want %d expected %d"
                    % (_content_length, recv_len)
                )

            # tell current cursor
            read_cur = self.buffer.tell()

            # replacement buffer
            temp = BytesIO()

            # check if any left in buffer
            tail = self.buffer.readline()
            if tail:
                self.buffer.seek(read_cur, SEEK_SET)
                temp.write(self.buffer.read())

            # replace buffer
            self.buffer = temp

            return content


class Transport(abc.ABC):
    """Abstraction transport"""

    @abc.abstractmethod
    def __init__(self, process_cmd: list):
        """init"""

    @abc.abstractmethod
    def register_command(self, method: str, handler: Callable[[ResponseMessage], None]):
        """register command handler"""

    @abc.abstractmethod
    def notify(self, message: RequestMessage):
        """notify message to server, not wait response"""

    @abc.abstractmethod
    def request(self, message: RequestMessage):
        """request to server, wait response"""

    @abc.abstractmethod
    def exec_command(self, method: str, params: Optional[Union[ResponseMessage, dict]]):
        """exec command triggered by server message"""

    @abc.abstractmethod
    def listen(self):
        """listen emitted message from server"""

    @abc.abstractmethod
    def exit(self):
        """exit process and terminate server"""


class StandardIO(Transport):
    """standard io Transport implementation"""

    def __init__(self, process_cmd: list):

        # init process
        self.server_process: subprocess.Popen = self._init_process(process_cmd)

        self.command_map = {}
        self._stream = Stream()

        # listener
        self.stdout_thread: threading.Thread = None
        self.stderr_thread: threading.Thread = None
        self.listen()

        # request
        self.request_map = {}

    def register_command(self, method: str, handler: Callable[[ResponseMessage], None]):
        LOGGER.info("register_command")
        self.command_map[method] = handler

    def _init_process(self, command):
        LOGGER.info("_init_process")

        startupinfo = None
        if os.name == "nt":
            # if on Windows, hide process window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW

        LOGGER.debug("command: %s", command)
        process = subprocess.Popen(
            # ["clangd", "--log=info", "--offset-encoding=utf-8"],
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ,
            bufsize=0,  # no buffering
            startupinfo=startupinfo,
        )
        return process

    def _write(self, message: JSONMessage):
        LOGGER.info("_write to stdin")
        bmessage = message.to_bytes()
        LOGGER.debug("write:\n%s", bmessage)
        self.server_process.stdin.write(bmessage)
        self.server_process.stdin.flush()

    def notify(self, message: RequestMessage):
        LOGGER.info("notify")
        self._write(message)

    def request(self, message: RequestMessage):
        LOGGER.info("request")
        self.request_map[message.id_] = message.method
        self._write(message)

    def exec_command(self, method: str, params: object):
        LOGGER.info("exec_command")
        try:
            handle = self.command_map[method]
        except KeyError as err:
            LOGGER.debug("unregistered command: %s", err)

        else:
            try:
                handle(params)
            except Exception as err:
                LOGGER.debug("exec_command error: \n%s", err)

    def _handle_stdout_message(self):
        LOGGER.info("_handle_stdout_message")
        while True:
            try:
                content = self._stream.read()
            except EOFError:
                LOGGER.debug("EOF")
                break

            if not content:
                break

            message = JSONMessage.from_string(content.decode("utf-8"))
            LOGGER.debug("message: %s", message)

            if message.is_response():
                try:
                    method = self.request_map.pop(message.id_)

                except KeyError as err:
                    LOGGER.debug("invalid response id: %s", str(err))
                    LOGGER.debug("registered request: %s", self.request_map)

                else:
                    response = ResponseMessage.from_json_transport(message)
                    self.exec_command(method, response)

            elif message.is_request():
                # exec server command
                command = RequestMessage.from_json_transport(message)
                self.exec_command(command.method, command.params)

            else:
                LOGGER.debug("invalid message: %s", message)

    def _listen_stdout(self):
        while True:
            stdout = self.server_process.stdout
            line = stdout.read(2048)
            if not line:
                break

            LOGGER.debug("line: %s", line)
            self._stream.write(line)
            self._handle_stdout_message()

    def _listen_stderr(self):
        while True:
            stderr = self.server_process.stderr
            line = stderr.read(2048)
            if not line:
                break
            try:
                stderr_message = line.decode()
            except UnicodeDecodeError as err:
                LOGGER.error(err)
            else:
                LOGGER.debug("stderr:\n%s", stderr_message)

    def listen(self):
        LOGGER.info("listen")
        self.stdout_thread = threading.Thread(target=self._listen_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._listen_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()

    def exit(self):
        """exit process"""

        LOGGER.info("exit")

        self.server_process.kill()
        self.stdout_thread.join()
        self.stderr_thread.join()

    def __del__(self):
        self.exit()
