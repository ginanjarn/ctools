"""context manager for clangd client

- read and write are separate process
"""

import abc
import json
import logging
import os
import re
import subprocess
import threading
from typing import Callable, Optional, Union


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class JSONRPCMessage(dict):
    """json rpc message handler"""

    def __init__(self, **kwargs):
        super().__init__({"jsonrpc": "2.0"})
        self.update(kwargs)

    def __getattr__(self, key, default=None):
        return self.get(key)

    def to_str(self):
        """dump to string"""
        return json.dumps(self)

    def to_bytes(self):
        """dump to bytes"""

        str_message = self.to_str()
        encoded_message = str_message.encode("utf-8")
        header = "Content-Length: %d\r\n" % (len(encoded_message))
        return b"\r\n".join([header.encode("ascii"), encoded_message])

    @classmethod
    def from_str(cls, message):
        """new message from string"""
        return cls(**json.loads(message))

    @classmethod
    def request(cls, id_, method, params=None):
        """new request message"""

        params = params or {}
        return cls(**{"id": id_, "method": method, "params": params})

    @classmethod
    def notification(cls, method, params=None):
        """new notification message"""

        params = params or {}
        # notification message not define id
        return cls(method=method, params=params)

    @classmethod
    def response(cls, id_, *, result=None, error=None):
        """new response message"""

        resp = {"id": id_, "result": result}

        # error only defined if error available
        if error:
            resp["error"] = error

        return cls(**resp)


class InvalidMessage(ValueError):
    """message invalid"""


class ContentIncomplete(ValueError):
    """content size less than defined size"""


class ContentOverflow(ValueError):
    """content size greater than defined size"""


class Stream:
    r"""stream object

    This class handle JSONRPC stream format
        '<header>\r\n<content>'
    
    Header items must end with '\r\n'
    """

    def __init__(self):
        self.buffered = []

    def put(self, data: bytes) -> None:
        """put data to stream buffer"""

        self.buffered.append(data)

    def get_content(self) -> str:
        """get content"""

        buffered = b"".join(self.buffered)

        try:
            header, content = buffered.split(b"\r\n\r\n")
        except ValueError as err:
            LOGGER.debug("unable get header, err: %s", err)
            raise InvalidMessage("unable get 'Content-Length'")

        match = re.match(
            r"^Content-Length: (\d+)", header.decode("ascii"), flags=re.MULTILINE
        )
        if not match:
            raise InvalidMessage("unable get 'Content-Length'")

        valid_length = int(match.group(1))
        content_length = len(content)

        if content_length < valid_length:
            raise ContentIncomplete(
                "want length: %d, expected: %d" % (valid_length, content_length)
            )
        if content_length > valid_length:
            raise ContentOverflow(
                "want length: %d, expected: %d" % (valid_length, content_length)
            )

        return content.decode("utf-8")


class Transport(abc.ABC):
    """Abstraction transport"""

    @abc.abstractmethod
    def __init__(self, process_cmd: list):
        """init"""

    @abc.abstractmethod
    def register_command(self, method: str, handler: Callable[[JSONRPCMessage], None]):
        """register command handler"""

    @abc.abstractmethod
    def notify(self, message: JSONRPCMessage):
        """notify message to server, not wait response"""

    @abc.abstractmethod
    def request(self, message: JSONRPCMessage):
        """request to server, wait response"""

    @abc.abstractmethod
    def exec_command(self, method: str, params: Optional[Union[JSONRPCMessage, dict]]):
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

        # listener
        self.stdout_thread: threading.Thread = None
        self.stderr_thread: threading.Thread = None
        self.listen()

        # request
        self.request_map = {}

    def register_command(self, method: str, handler: Callable[[JSONRPCMessage], None]):
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

    def _write(self, message: JSONRPCMessage):
        LOGGER.info("_write to stdin")

        bmessage = message.to_bytes()
        LOGGER.debug("write:\n%s", bmessage)
        self.server_process.stdin.write(bmessage)
        self.server_process.stdin.flush()

    def notify(self, message: JSONRPCMessage):
        LOGGER.info("notify")

        self._write(message)

    def request(self, message: JSONRPCMessage):
        LOGGER.info("request")

        self.request_map[message.id] = message.method
        self._write(message)

    def exec_command(self, method: str, params: object):
        LOGGER.info("exec_command")

        try:
            handle = self.command_map[method]
        except KeyError:
            LOGGER.error("method not found: '%s', params: %s", method, params)

        else:
            try:
                handle(params)
            except Exception as err:
                LOGGER.debug("exec_command error: \n%s", err)

    def _process_stdout_message(self, content: str):
        """process stdout message"""

        message = JSONRPCMessage.from_str(content)
        LOGGER.debug("message: %s", message)

        if message.method:
            # exec server request
            self.exec_command(message.method, message.params)

        elif message.id:
            # exec response map to request id
            try:
                method = self.request_map.pop(message.id)

            except KeyError as err:
                LOGGER.error("request id not found: '%s'", err)
                LOGGER.debug("all request: %s", self.request_map)

            else:
                # FIXME: handle this response:
                #     {'id': 6, 'jsonrpc': '2.0', 'result': None}

                self.exec_command(method, message)

        else:
            LOGGER.debug("invalid message: %s", message)

    def _listen_stdout(self):
        """listen stdout task"""

        stdout = self.server_process.stdout
        stream = Stream()
        while True:

            try:
                content = stream.get_content()

            except ContentIncomplete as err:
                LOGGER.debug("error: %s", err)

            except (InvalidMessage, ContentOverflow) as err:
                LOGGER.debug("invalid message: %s", err)

                # reset with new Stream object
                stream = Stream()
            else:
                LOGGER.debug("content: %s", content)

                self._process_stdout_message(content)

                # reset with new Stream object
                stream = Stream()

            buf = stdout.read(2048)
            if not buf:
                LOGGER.debug("stdout closed")
                return

            stream.put(buf)

    def _listen_stderr(self):
        """listen stderr task"""

        while True:
            stderr = self.server_process.stderr
            line = stderr.read(2048)

            if not line:
                LOGGER.debug("stderr closed")
                return

            try:
                LOGGER.debug("stderr:\n%s", line)
            except UnicodeDecodeError as err:
                LOGGER.error(err)

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
