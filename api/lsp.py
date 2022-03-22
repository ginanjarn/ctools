"""LSP implementation"""

import json
import logging
import os
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Callable, Any, Union

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class ContentIncomplete(ValueError):
    """expected size less than defined"""


class ContentOverflow(ValueError):
    """expected size greater than defined"""


class ServerOffline(Exception):
    """server offline"""


class RPCMessage(dict):
    """rpc message"""

    def __init__(self, mapping=None, **kwargs):
        kwargs["jsonrpc"] = "2.0"
        super().__init__(kwargs)
        if mapping:
            self.update(mapping)

    def to_bytes(self) -> bytes:
        message_str = json.dumps(self)
        message_encoded = message_str.encode("utf-8")

        header = f"Content-Length: {len(message_encoded)}"
        return b"\r\n\r\n".join([header.encode("ascii"), message_encoded])

    _content_length_pattern = re.compile(r"^Content-Length: (\d+)$", flags=re.MULTILINE)

    @staticmethod
    def get_content_length(s: str):
        match = RPCMessage._content_length_pattern.match(s)
        if match:
            return int(match.group(1))
        raise ValueError(f"Unable get Content-Length from \n'{s}'")

    @classmethod
    def from_bytes(cls, b: bytes, /):
        try:
            header, content = b.split(b"\r\n\r\n")
        except Exception as err:
            raise ValueError(f"Unable get Content-Length, {err}")

        defined_length = cls.get_content_length(header.decode("ascii"))
        expected_length = len(content)

        if expected_length < defined_length:
            raise ContentIncomplete(
                f"want {defined_length}, expected {expected_length}"
            )
        elif expected_length > defined_length:
            raise ContentIncomplete(
                f"want {defined_length}, expected {expected_length}"
            )

        message_str = content.decode("utf-8")
        return cls(json.loads(message_str))

    @classmethod
    def notification(cls, method, params):
        return cls(method=method, params=params)

    @classmethod
    def request(cls, id_, method, params):
        return cls({"id": id_, "method": method, "params": params})

    @classmethod
    def cancel_request(cls, id_):
        return cls({"id": id_})

    @property
    def params(self):
        return self.get("params")

    @property
    def error(self):
        return self.get("error")

    @property
    def result(self):
        return self.get("result")


class AbstractTransport(ABC):
    """abstract transport"""

    @abstractmethod
    def request(self, message: RPCMessage):
        """request message"""

    @abstractmethod
    def notify(self, message: RPCMessage):
        """notify message"""

    @abstractmethod
    def cancel_request(self, message: RPCMessage):
        """cancel request"""

    @abstractmethod
    def run_command(self, method: str, params: Dict[str, Any]):
        """run command"""

    @abstractmethod
    def register_command(self, method: str, callable: Callable[[RPCMessage], None]):
        """register command"""

    @abstractmethod
    def terminate(self):
        """terminate"""


class LSPClient:
    """LSP client"""

    def __init__(self):
        self.transport: AbstractTransport = None

        # server status
        self.server_running = False
        self.server_capabilities = {}

        # project status
        self.is_initialized = False
        self.cached_document = {}

        # request
        self.request_id = 0
        # document version
        self.document_version = 0

    def get_request_id(self):
        self.request_id += 1
        return self.request_id

    def get_document_version(self, reset=False):
        if reset:
            self.document_version = 0
        else:
            self.document_version += 1
        return self.document_version

    def run_server(self, clangd="clangd", *args):
        commands = [clangd]
        commands.extend(args)
        try:
            self.transport = StandardIO(commands)
            self._register_commands()

        except Exception as err:
            LOGGER.error("running server error", exc_info=True)
        else:
            self.server_running = True

    # server message handler

    def handle_initialize(self, params: RPCMessage):
        """handle initialize"""

    def handle_textDocument_completion(self, params: RPCMessage):
        """handle document completion"""

    def handle_textDocument_hover(self, params: RPCMessage):
        """handle document hover"""

    def handle_textDocument_formatting(self, params: RPCMessage):
        """handle document formatting"""

    def handle_textDocument_semanticTokens_full(self, params: RPCMessage):
        """handle document semantic tokens"""

    def handle_workspace_applyEdit(self, params: RPCMessage):
        """handle workspace apply edit"""

    def handle_textDocument_documentLink(self, params: RPCMessage):
        """handle document link"""

    def handle_textDocument_documentSymbol(self, params: RPCMessage):
        """handle document symbol"""

    def handle_textDocument_codeAction(self, params: RPCMessage):
        """handle document code action"""

    def handle_S_progress(self, params: RPCMessage):
        """handle progress"""

    def handle_textDocument_publishDiagnostics(self, params: RPCMessage):
        """handle publish diagnostic"""

    def handle_window_workDoneProgress_create(self, params):
        """handle work progress done create"""

    def handle_textDocument_prepareRename(self, params: RPCMessage):
        """handle document prepare rename"""

    def handle_textDocument_rename(self, params: RPCMessage):
        """handle document rename"""

    def handle_textDocument_definition(self, params: RPCMessage):
        """handle document definition"""

    def handle_textDocument_declaration(self, params: RPCMessage):
        """handle document definition"""

    def _register_commands(self):
        self.transport.register_command("initialize", self.handle_initialize)
        self.transport.register_command(
            "textDocument/publishDiagnostics",
            self.handle_textDocument_publishDiagnostics,
        )
        self.transport.register_command(
            "window/workDoneProgress/create", self.handle_window_workDoneProgress_create
        )
        self.transport.register_command(
            "textDocument/documentLink", self.handle_textDocument_documentLink
        )
        self.transport.register_command(
            "textDocument/hover", self.handle_textDocument_hover
        )
        self.transport.register_command(
            "textDocument/completion", self.handle_textDocument_completion
        )
        self.transport.register_command(
            "textDocument/formatting", self.handle_textDocument_formatting
        )
        self.transport.register_command(
            "textDocument/documentSymbol", self.handle_textDocument_documentSymbol
        )
        self.transport.register_command(
            "textDocument/codeAction", self.handle_textDocument_codeAction
        )
        self.transport.register_command("$/progress", self.handle_S_progress)
        self.transport.register_command(
            "textDocument/semanticTokens/full",
            self.handle_textDocument_semanticTokens_full,
        )
        self.transport.register_command(
            "workspace/applyEdit", self.handle_workspace_applyEdit
        )
        self.transport.register_command(
            "textDocument/prepareRename", self.handle_textDocument_prepareRename
        )
        self.transport.register_command(
            "textDocument/rename", self.handle_textDocument_rename
        )
        self.transport.register_command(
            "textDocument/declaration", self.handle_textDocument_declaration
        )
        self.transport.register_command(
            "textDocument/definition", self.handle_textDocument_definition
        )

    def exit(self):
        self.transport.terminate()


class StandardIO(AbstractTransport):
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

    def register_command(self, method: str, handler: Callable[[RPCMessage], None]):
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

    def _write(self, message: RPCMessage):
        LOGGER.info("_write to stdin")

        bmessage = message.to_bytes()
        LOGGER.debug("write:\n%s", bmessage)
        self.server_process.stdin.write(bmessage)
        self.server_process.stdin.flush()

    def notify(self, message: RPCMessage):
        LOGGER.info("notify")

        self._write(message)

    def request(self, message: RPCMessage):
        LOGGER.info("request")

        self.request_map[message["id"]] = message["method"]
        self._write(message)

    def cancel_request(self, message: RPCMessage):
        LOGGER.info("cancel request")

        try:
            del self.request_map[message["id"]]
        except KeyError as err:
            LOGGER.debug("request canceled: %s", err)
        except TypeError as err:
            LOGGER.error(f"TypeError, {message}, {self.request_map}")
        else:
            self._write(message)

    def run_command(self, method: str, params: Union[Dict[str, Any], List[Any]]):
        LOGGER.info("run_command")

        LOGGER.debug(f"method: {method}, params: {params}")

        try:
            func = self.command_map[method]
        except KeyError:
            LOGGER.error("method not found: '%s'", method)

        else:
            try:
                func(params)
            except Exception as err:
                LOGGER.debug("run_command error: \n%s", err)

    def _process_response_message(self, message: RPCMessage):
        """process server response message"""

        LOGGER.debug("message: %s", message)

        method = message.get("method")
        message_id = message.get("id")
        if method:
            # exec server request
            self.run_command(method, message["params"])

        elif message_id:
            # exec response map to request id
            try:
                method = self.request_map.pop(message_id)

            except KeyError as err:
                LOGGER.error("request id not found: '%s'", err)
                LOGGER.debug("all request: %s", self.request_map)

            else:
                self.run_command(method, message)

        else:
            LOGGER.debug("invalid message: %s", message)

    def _listen_stdout(self):
        """listen stdout task"""

        stdout = self.server_process.stdout
        buffer = []

        while True:
            try:
                message = RPCMessage.from_bytes(b"".join(buffer))
            except ContentIncomplete:
                pass
            except (ContentOverflow, ValueError):
                buffer = []
            else:
                self._process_response_message(message)
                buffer = []

            buf = stdout.read(2048)
            if not buf:
                return

            buffer.append(buf)

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

    def terminate(self):
        """terminate process"""

        LOGGER.info("terminate")

        self.server_process.kill()
        self.stdout_thread.join()
        self.stderr_thread.join()

    def __del__(self):
        self.terminate()
