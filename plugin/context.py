"""context manager for clangd client

- read and write are separate process
"""


import json
import os
import re
import subprocess
import threading

from io import BytesIO, SEEK_SET, SEEK_END
from queue import Queue
from typing import Union


class JSONTransport:
    """JSON transport message

    Params:
    - data : mapped data
    """

    def __init__(self, data: dict):
        data.update({"jsonrpc": "2.0"})
        self.data = data

    def __repr__(self):
        return str(self.data)

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
            buf.write(b"\r\n")
            buf.write(message)
            return buf.getvalue()

    @classmethod
    def from_string(cls, message: str):
        raise NotImplementedError("from_string not implemented")


class RequestMessage(JSONTransport):
    """Request message

    Params:
    - id : Union[int,str]
    - method : str
    - params : Optional[dict]
    """

    def __init__(self, id_: int, method: str, params: dict = None):
        if not params:
            params = {}
        super().__init__({"id": id_, "method": method, "params": params})

    @classmethod
    def from_string(cls, message: str):
        decoded = json.loads(message)
        return cls(decoded["id"], decoded["method"], decoded.get("params"))


class ResponseMessage(JSONTransport):
    """Response message

    Params:
    - id : Union[int, str]
    - result : Optional[dict, list]
    - error : Optional[dict]
    """

    def __init__(self, id_: int, result: object = None, error: dict = None):
        super().__init__({"id": id_, "result": result, "error": error})

    @classmethod
    def from_string(cls, message: str):
        decoded = json.loads(message)
        return cls(decoded["id"], decoded.get("result"), decoded.get("error"))


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
            self.buffer.seek(0, SEEK_END)
            # print("write cur:", self.buffer.tell())
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
        """read stream data"""

        with self._lock:

            # in reading, _read_cur always point to beginning of header
            self.buffer.seek(0, SEEK_SET)

            _content_length = self._get_content_length(self.buffer)
            content = self.buffer.read(_content_length)

            recv_len = len(content)
            if recv_len < _content_length:
                # print("waiting content")
                return b""

            elif recv_len > _content_length:
                raise InvalidMessage(
                    "content overflow, want %d expected %d"
                    % (_content_length, recv_len)
                )

            # print("content:", content)

            # tell current cursor
            _read_cur = self.buffer.tell()

            # replacement buffer
            temp = BytesIO()

            # check if any left in buffer
            tail = self.buffer.readline()
            if tail:
                self.buffer.seek(_read_cur, SEEK_SET)
                temp.write(self.buffer.read())

            # replace buffer
            self.buffer = temp

            return content


class Context:
    """process context"""

    def __init__(self):

        self.process: subprocess.Popen = None
        self.stdout_thread: threading.Thread = None
        self.stderr_thread: threading.Thread = None

        self._queue = Queue()
        self._stream = Stream()

        self._start_clangd()
        self.listen()

    def _start_clangd(self):
        """clangd process"""

        self.process = subprocess.Popen(
            ["clangd", "--log=info", "--offset-encoding=utf-8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ,
            bufsize=0,
        )

    def write(self, message: Union[bytes, JSONTransport]):
        """write message to stream"""

        stdin: BytesIO = self.process.stdin

        if isinstance(message, JSONTransport):
            message = message.to_bytes()

        stdin.write(message)
        stdin.flush()

    def exec_command(self, method: str, params: Union[dict, list, None]):
        """exec client commands"""
        pass

    def listen(self):
        """listen stream"""

        self.stdout_thread = threading.Thread(
            target=self._listen_stdout, args=(self.process.stdout,), daemon=True
        )
        self.stderr_thread = threading.Thread(
            target=self._listen_stderr, args=(self.process.stderr,), daemon=True
        )
        self.queue_thread = threading.Thread(target=self._handle_queue, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        self.queue_thread.start()

    def exit(self):
        self.process.kill()
        self.stdout_thread.join()
        self.stderr_thread.join()
        self.queue_thread.join()

    def __del__(self):
        self.exit()

    def _listen_stdout(self, stdout: BytesIO):
        while True:
            line = stdout.read(2048)
            if not line:
                break

            self._queue.put(line)

        # print("exit loop stdout")

    def _listen_stderr(self, stderr: BytesIO):
        while True:
            line = stderr.readline()
            # print("stderr line:", line)
            if not line:
                break

        # print("exit loop stderr")

    def _handle_queue(self):
        while True:
            line = self._queue.get()
            # print("queue line:", line)

            self._stream.write(line)
            self._handle_message()

    def _handle_message(self):
        while True:
            # print(self._stream.read())
            try:
                content = self._stream.read()
            except EOFError:
                break

            if not content:
                break

            print("message:", content)
            rm = ResponseMessage.from_string(content.decode("utf-8"))
            print(rm)
