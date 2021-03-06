"""LSP implementation"""

import json
import logging
import os
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Callable
from urllib.parse import urlparse, urlunparse
from urllib.request import pathname2url, url2pathname

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class InvalidMessage(ValueError):
    """message not comply to jsonrpc 2.0 specification"""


class ContentIncomplete(ValueError):
    """expected size less than defined"""


class ContentOverflow(ValueError):
    """expected size greater than defined"""


class ServerOffline(Exception):
    """server offline"""


class DocumentURI(str):
    """document uri"""

    @classmethod
    def from_path(cls, file_name):
        """from file name"""
        return cls(urlunparse(("file", "", pathname2url(file_name), "", "", "")))

    def to_path(self) -> str:
        """convert to path"""
        return url2pathname(urlparse(self).path)


class RPCMessage(dict):
    """rpc message"""

    JSONRPC_VERSION = "2.0"
    HEADER_ENCODING = "ascii"
    CONTENT_ENCODING = "utf-8"

    def __init__(self, mapping=None, **kwargs):
        super().__init__(kwargs)
        if mapping:
            self.update(mapping)

    @classmethod
    def from_str(cls, s: str, /):
        return cls(json.loads(s))

    def to_bytes(self) -> bytes:
        self["jsonrpc"] = self.JSONRPC_VERSION
        message_str = json.dumps(self)
        message_encoded = message_str.encode(self.CONTENT_ENCODING)

        header = f"Content-Length: {len(message_encoded)}"
        return b"\r\n\r\n".join([header.encode(self.HEADER_ENCODING), message_encoded])

    _content_length_pattern = re.compile(r"^Content-Length: (\d+)$", flags=re.MULTILINE)

    @staticmethod
    def get_content_length(s: str):
        if found := RPCMessage._content_length_pattern.search(s):
            return int(found.group(1))
        raise ValueError(f"Unable get Content-Length from \n'{s}'")

    @classmethod
    def from_bytes(cls, b: bytes, /):
        try:
            header, content = b.split(b"\r\n\r\n")
        except (ValueError, TypeError, AttributeError) as err:
            raise InvalidMessage(
                f"Unable get Content-Length, {repr(err)}, content: {repr(b)}"
            ) from err

        defined_length = cls.get_content_length(header.decode(cls.HEADER_ENCODING))
        expected_length = len(content)

        if expected_length < defined_length:
            raise ContentIncomplete(
                f"want {defined_length}, expected {expected_length}"
            )
        elif expected_length > defined_length:
            raise ContentOverflow(f"want {defined_length}, expected {expected_length}")

        try:
            message_str = content.decode(cls.CONTENT_ENCODING)
            message = json.loads(message_str)

            if message["jsonrpc"] != cls.JSONRPC_VERSION:
                raise ValueError("invalid jsonrpc version")

        except Exception as err:
            raise InvalidMessage(err) from err
        else:
            return cls(message)

    @classmethod
    def notification(cls, method, params):
        return cls({"method": method, "params": params})

    @classmethod
    def request(cls, id_, method, params):
        return cls({"id": id_, "method": method, "params": params})

    @classmethod
    def response(cls, id_, result=None, error=None):
        c = cls({"id": id_})
        if result is not None:
            c["result"] = result
        if error is not None:
            c["error"] = error
        return c

    @classmethod
    def cancel_request(cls, id_):
        return cls({"method": "$/cancelRequest", "params": {"id": id_}})

    @property
    def method(self):
        return self.get("method")

    @property
    def params(self):
        return self.get("params")

    @property
    def error(self):
        return self.get("error")

    @property
    def result(self):
        return self.get("result")


class Stream:
    r"""stream object

    This class handle JSONRPC stream format
        '<header>\r\n<content>'
    
    Header items must seperated by '\r\n'
    """

    def __init__(self, content: bytes = b""):
        self.buffer = [content] if content else []
        self._lock = threading.Lock()

    def put(self, data: bytes) -> None:
        """put stream data"""
        with self._lock:
            self.buffer.append(data)

    _content_length_pattern = re.compile(r"^Content-Length: (\d+)$", flags=re.MULTILINE)

    def _get_content_length(self, headers: bytes) -> int:
        """get Content-Length"""

        if found := self._content_length_pattern.search(headers.decode("ascii")):
            return int(found.group(1))
        raise ValueError("unable find Content-Length")

    def get_content(self) -> bytes:
        """read stream data

        Returns
        ------
        content: bytes

        Raises:
        -------
        InvalidMessage
        EOFError
        ContentIncomplete
        """

        with self._lock:

            buffers = b"".join(self.buffer)
            separator = b"\r\n\r\n"

            if not buffers:
                raise EOFError("buffer empty")

            try:
                header_end = buffers.index(separator)
                content_length = self._get_content_length(buffers[:header_end])

            except ValueError as err:
                # clean up buffer
                self.buffer = []

                LOGGER.error(err)
                LOGGER.debug("buffer: %s", buffers)
                raise InvalidMessage(f"header error: {repr(err)}") from err

            start_index = header_end + len(separator)
            end_index = start_index + content_length
            content = buffers[start_index:end_index]
            recv_len = len(content)

            if recv_len < content_length:
                raise ContentIncomplete(f"want: {content_length}, expected: {recv_len}")

            # replace buffer
            self.buffer = [buffers[end_index:]]
            return content


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
    def respond(self, message: RPCMessage):
        """respond request"""

    @abstractmethod
    def register_command(self, method: str, callable: Callable[[RPCMessage], None]):
        """register command"""

    @abstractmethod
    def handle_received_message(self, message: RPCMessage):
        """handle received message"""

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

        self.initialize_options = {}

        # request
        self.request_id = 0
        # active document
        self.active_document = ""
        self.source = ""
        # document version
        self.document_version_map = {}

    def reset_session(self):

        # terminate server
        if self.transport:
            self.transport.terminate()

        self.transport: AbstractTransport = None

        # server status
        self.server_running = False
        self.server_capabilities = {}

        # project status
        self.is_initialized = False

        self.initialize_options = {}

        # request
        self.request_id = 0
        # active document
        self.active_document = ""
        self.source = ""
        # document version
        self.document_version_map = {}

    def get_request_id(self):
        self.request_id += 1
        return self.request_id

    def get_document_version(
        self, file_name: str, *, reset: bool, increment: bool = True
    ):
        if reset:
            self.document_version_map[file_name] = 0

        cur_version = self.document_version_map.get(file_name, 0)
        if not increment:
            return cur_version

        cur_version += 1
        self.document_version_map[file_name] = cur_version
        return cur_version

    def run_server(self, executable="", *args):
        raise NotImplementedError()

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

    def handle_workspace_semanticTokens_refresh(self, params: RPCMessage):
        """handle workspace semanticTokens refresh request"""

    def handle_workspace_applyEdit(self, params: RPCMessage):
        """handle workspace apply edit"""

    def handle_client_registerCapability(self, params: RPCMessage):
        """handle client registerCapability"""

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

    def handle_workspace_configuration(self, params):
        """handle workspace configuration"""

    def handle_window_workDoneProgress_create(self, params):
        """handle work progress done create"""

    def handle_window_showMessage(self, message: RPCMessage):
        """handle show message"""

    def handle_window_logMessage(self, message: RPCMessage):
        """handle log message"""

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
            "workspace/configuration", self.handle_workspace_configuration
        )
        self.transport.register_command(
            "window/workDoneProgress/create", self.handle_window_workDoneProgress_create
        )
        self.transport.register_command(
            "window/showMessage", self.handle_window_showMessage
        )
        self.transport.register_command(
            "window/logMessage", self.handle_window_logMessage
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
            "workspace/semanticTokens/refresh",
            self.handle_workspace_semanticTokens_refresh,
        )
        self.transport.register_command(
            "workspace/applyEdit", self.handle_workspace_applyEdit
        )
        self.transport.register_command(
            "client/registerCapability", self.handle_client_registerCapability
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

    def cancelRequest(self):
        self.transport.cancel_request()

    def initialize(self, project_path: str):
        """initialize server"""

        LOGGER.info("initialize")

        if not self.server_running:
            raise ServerOffline

        params = {
            "capabilities": {
                "general": {
                    "markdown": {"parser": "marked", "version": "1.1.0"},
                    "regularExpressions": {"engine": "ECMAScript", "version": "ES2020"},
                    "staleRequestSupport": {
                        "cancel": True,
                        "retryOnContentModified": [
                            "textDocument/semanticTokens/full",
                            "textDocument/semanticTokens/range",
                            "textDocument/semanticTokens/full/delta",
                        ],
                    },
                },
                "textDocument": {
                    "callHierarchy": {"dynamicRegistration": True},
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "refactor.rewrite",
                                    "source",
                                    "source.organizeImports",
                                ]
                            }
                        },
                        "dataSupport": True,
                        "disabledSupport": True,
                        "dynamicRegistration": True,
                        "honorsChangeAnnotations": False,
                        "isPreferredSupport": True,
                        "resolveSupport": {"properties": ["edit"]},
                    },
                    "codeLens": {"dynamicRegistration": True},
                    "colorProvider": {"dynamicRegistration": True},
                    "completion": {
                        "completionItem": {
                            "commitCharactersSupport": True,
                            "deprecatedSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                            "insertReplaceSupport": True,
                            "insertTextModeSupport": {"valueSet": [1]},
                            "labelDetailsSupport": True,
                            "preselectSupport": True,
                            "resolveSupport": {
                                "properties": [
                                    "documentation",
                                    "detail",
                                    "additionalTextEdits",
                                ]
                            },
                            "snippetSupport": False,
                            "tagSupport": {"valueSet": [1]},
                        },
                        "completionItemKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                            ]
                        },
                        "contextSupport": True,
                        "dynamicRegistration": True,
                        "editsNearCursor": True,
                    },
                    "declaration": {"dynamicRegistration": True, "linkSupport": True},
                    "definition": {"dynamicRegistration": True, "linkSupport": True},
                    "documentHighlight": {"dynamicRegistration": True},
                    "documentLink": {
                        "dynamicRegistration": True,
                        "tooltipSupport": True,
                    },
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                        "labelSupport": True,
                        "symbolKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                                26,
                            ]
                        },
                        "tagSupport": {"valueSet": [1]},
                    },
                    "foldingRange": {
                        "dynamicRegistration": True,
                        "lineFoldingOnly": True,
                        "rangeLimit": 5000,
                    },
                    "formatting": {"dynamicRegistration": True},
                    "hover": {
                        "contentFormat": ["markdown", "plaintext"],
                        "dynamicRegistration": True,
                    },
                    "implementation": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                    "linkedEditingRange": {"dynamicRegistration": True},
                    "onTypeFormatting": {"dynamicRegistration": True},
                    "publishDiagnostics": {
                        "codeDescriptionSupport": True,
                        "dataSupport": True,
                        "relatedInformation": True,
                        "tagSupport": {"valueSet": [1, 2]},
                        "versionSupport": False,
                    },
                    "rangeFormatting": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "rename": {
                        "dynamicRegistration": True,
                        "honorsChangeAnnotations": True,
                        "prepareSupport": True,
                        "prepareSupportDefaultBehavior": 1,
                    },
                    "selectionRange": {"dynamicRegistration": True},
                    "semanticTokens": {
                        "dynamicRegistration": True,
                        "formats": ["relative"],
                        "multilineTokenSupport": False,
                        "overlappingTokenSupport": False,
                        "requests": {"full": {"delta": True}, "range": True},
                        "tokenModifiers": [
                            "declaration",
                            "definition",
                            "readonly",
                            "static",
                            "deprecated",
                            "abstract",
                            "async",
                            "modification",
                            "documentation",
                            "defaultLibrary",
                        ],
                        "tokenTypes": [
                            "namespace",
                            "type",
                            "class",
                            "enum",
                            "interface",
                            "struct",
                            "typeParameter",
                            "parameter",
                            "variable",
                            "property",
                            "enumMember",
                            "event",
                            "function",
                            "method",
                            "macro",
                            "keyword",
                            "modifier",
                            "comment",
                            "string",
                            "number",
                            "regexp",
                            "operator",
                        ],
                    },
                    "signatureHelp": {
                        "contextSupport": True,
                        "dynamicRegistration": True,
                        "signatureInformation": {
                            "activeParameterSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                        },
                    },
                    "synchronization": {
                        "didSave": True,
                        "dynamicRegistration": True,
                        "willSave": True,
                        "willSaveWaitUntil": True,
                    },
                    "typeDefinition": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                },
                "window": {
                    "showDocument": {"support": True},
                    "showMessage": {
                        "messageActionItem": {"additionalPropertiesSupport": True}
                    },
                    "workDoneProgress": True,
                },
                "workspace": {
                    "applyEdit": True,
                    "codeLens": {"refreshSupport": True},
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                    "executeCommand": {"dynamicRegistration": True},
                    "fileOperations": {
                        "didCreate": True,
                        "didDelete": True,
                        "didRename": True,
                        "dynamicRegistration": True,
                        "willCreate": True,
                        "willDelete": True,
                        "willRename": True,
                    },
                    "semanticTokens": {"refreshSupport": True},
                    "symbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {
                            "valueSet": [
                                1,
                                2,
                                3,
                                4,
                                5,
                                6,
                                7,
                                8,
                                9,
                                10,
                                11,
                                12,
                                13,
                                14,
                                15,
                                16,
                                17,
                                18,
                                19,
                                20,
                                21,
                                22,
                                23,
                                24,
                                25,
                                26,
                            ]
                        },
                        "tagSupport": {"valueSet": [1]},
                    },
                    "workspaceEdit": {
                        "changeAnnotationSupport": {"groupsOnLabel": True},
                        "documentChanges": True,
                        "failureHandling": "textOnlyTransactional",
                        "normalizesLineEndings": True,
                        "resourceOperations": ["create", "rename", "delete"],
                    },
                    "workspaceFolders": True,
                },
            },
            "clientInfo": {"name": "Sublime Text", "version": "4126"},
            # "initializationOptions": {"clangdFileStatus": True, "fallbackFlags": []},
            "locale": "en-us",
            "processId": os.getpid(),
            "rootPath": project_path,
            "rootUri": DocumentURI.from_path(project_path),
            "trace": "off",
            "workspaceFolders": [
                {
                    "name": os.path.basename(project_path),
                    "uri": DocumentURI.from_path(project_path),
                }
            ],
        }
        if self.initialize_options:
            params.update(self.initialize_options)

        self.transport.request(
            RPCMessage.request(self.get_request_id(), "initialize", params)
        )

    def initialized(self):
        LOGGER.info("initialized")
        params = {}
        self.transport.notify(RPCMessage.notification("initialized", params))
        self.is_initialized = True

    def textDocument_didOpen(self, file_name: str, source: str):
        LOGGER.info("textDocument_didOpen")

        if not self.server_running:
            raise ServerOffline

        if self.active_document == file_name and self.source == source:
            LOGGER.debug("document already opened")
            return

        self.active_document = file_name
        self.source = source

        params = {
            "textDocument": {
                "languageId": "cpp",
                "text": source,
                "uri": DocumentURI.from_path(file_name),
                "version": self.get_document_version(file_name, reset=True),
            }
        }
        self.transport.notify(RPCMessage.notification("textDocument/didOpen", params))

    def textDocument_didChange(self, file_name: str, changes: dict):
        LOGGER.info("textDocument_didChange")

        if not self.server_running:
            raise ServerOffline

        params = {
            "contentChanges": changes,
            "textDocument": {
                "uri": DocumentURI.from_path(file_name),
                "version": self.get_document_version(file_name, reset=False),
            },
        }
        LOGGER.debug("didChange: %s", params)
        self._hide_completion(changes[0]["text"])
        self.transport.notify(RPCMessage.notification("textDocument/didChange", params))

    def textDocument_didClose(self, file_name: str):
        LOGGER.info("textDocument_didClose")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(file_name)}}
        self.transport.notify(RPCMessage.notification("textDocument/didClose", params))
        self.active_document = ""

    def textDocument_didSave(self, file_name: str):
        LOGGER.info("textDocument_didSave")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(file_name)}}
        self.transport.notify(RPCMessage.notification("textDocument/didSave", params))

    def textDocument_completion(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_completion")

        if not self.server_running:
            raise ServerOffline

        params = {
            "context": {"triggerKind": 1},  # TODO: adapt KIND
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/completion", params)
        )

    def textDocument_hover(self, file_name: str, row: int, col: int):
        LOGGER.info("textDocument_hover")

        if not self.server_running:
            raise ServerOffline
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/hover", params)
        )

    def textDocument_formatting(self, file_name, tab_size=2):
        LOGGER.info("textDocument_formatting")

        if not self.server_running:
            raise ServerOffline

        params = {
            "options": {"insertSpaces": True, "tabSize": tab_size},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/formatting", params)
        )

    def textDocument_semanticTokens_full(self, file_name: str):
        LOGGER.info("textDocument_semanticTokens_full")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "textDocument/semanticTokens/full", params
            )
        )

    def textDocument_documentLink(self, file_name: str):
        LOGGER.info("textDocument_documentLink")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "textDocument/documentLink", params
            )
        )

    def textDocument_documentSymbol(self, file_name: str):
        LOGGER.info("textDocument_documentSymbol")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "textDocument/documentSymbol", params
            )
        )

    def textDocument_codeAction(
        self,
        file_name: str,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        diagnostics=None,
    ):
        LOGGER.info("textDocument_codeAction")

        if not self.server_running:
            raise ServerOffline

        diagnostics = [] if not diagnostics else diagnostics
        params = {
            "context": {"diagnostics": diagnostics},
            "range": {
                "end": {"character": end_col, "line": end_line},
                "start": {"character": start_col, "line": start_line},
            },
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        LOGGER.debug("codeAction params: %s", params)
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/codeAction", params)
        )

    def workspace_executeCommand(self, params: dict):

        if not self.server_running:
            raise ServerOffline

        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "workspace/executeCommand", params
            )
        )

    def textDocument_prepareRename(self, file_name, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "textDocument/prepareRename", params
            )
        )

    def textDocument_rename(self, file_name, row, col, new_name):

        if not self.server_running:
            raise ServerOffline

        params = {
            "newName": new_name,
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/rename", params)
        )

    def textDocument_definition(self, file_name, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(self.get_request_id(), "textDocument/definition", params)
        )

    def textDocument_declaration(self, file_name, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(file_name)},
        }
        self.transport.request(
            RPCMessage.request(
                self.get_request_id(), "textDocument/declaration", params
            )
        )


class StandardIO(AbstractTransport):
    """standard io Transport implementation"""

    BUFFER_LENGTH = 4096

    def __init__(self, process_cmd: list):

        # init process
        self.server_process: subprocess.Popen = self._init_process(process_cmd)
        self.command_map = {}
        self.listen()

        # hold request method map
        self.request_map = {}

    def register_command(self, method: str, handler: Callable[[RPCMessage], None]):
        LOGGER.info(f"register_command {method}")
        self.command_map[method] = handler

    def _init_process(self, command):
        LOGGER.info("_init_process")

        startupinfo = None
        if os.name == "nt":
            # if on Windows, hide process window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW

        LOGGER.debug("command: %s", command)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ,
                bufsize=0,  # no buffering
                startupinfo=startupinfo,
            )
        except FileNotFoundError as err:
            raise FileNotFoundError(f"'{command[0]}' not found in PATH") from err
        except Exception as err:
            raise Exception(f"run server error: {err}") from err
        return process

    def send_message(self, message: RPCMessage):
        LOGGER.debug(f"Send >> {message}")

        bmessage = message.to_bytes()
        self.server_process.stdin.write(bmessage)
        self.server_process.stdin.flush()

    def notify(self, message: RPCMessage):
        LOGGER.info("notify")
        self.send_message(message)

    def respond(self, message: RPCMessage):
        LOGGER.info("respond")
        self.send_message(message)

    def request(self, message: RPCMessage):
        LOGGER.info("request")

        self.request_map[message["id"]] = message["method"]
        self.send_message(message)

    def cancel_request(self):
        LOGGER.info("cancel request")

        for request_id, _ in self.request_map.items():
            message = RPCMessage.cancel_request(id_=request_id)
            self.send_message(message)

        self.request_map = {}

    def handle_received_message(self, message: RPCMessage):
        """handle received message"""

        method = message.get("method")
        message_id = message.get("id")

        if not method:
            # if no method, find method in request_map
            try:
                method = self.request_map.pop(message_id)
            except KeyError as err:
                raise ValueError(
                    f"invalid response, {message_id} not in {self.request_map}"
                ) from err

        try:
            func = self.command_map[method]
        except KeyError as err:
            raise ValueError(f"method not found {err}") from err

        try:
            func(message)
        except Exception as err:
            raise Exception(f"error execute {method}({message})") from err

    def _process_stream(self, stream: Stream):
        """process stream"""

        while True:
            content = stream.get_content()
            message = RPCMessage.from_str(content.decode())
            LOGGER.debug(f"Received << {message}")

            try:
                self.handle_received_message(message)

            except Exception:
                LOGGER.error("error process message", exc_info=True)

    def _listen_stdout(self):
        """listen stdout task"""

        stream = Stream()

        while True:
            buf = self.server_process.stdout.read(self.BUFFER_LENGTH)
            if not buf:
                LOGGER.debug("stdout closed")
                return

            stream.put(buf)
            try:
                self._process_stream(stream)
            except (EOFError, ContentIncomplete):
                pass
            except Exception as err:
                LOGGER.error(err)

    def _listen_stderr(self):
        """listen stderr task"""

        while True:
            buf = self.server_process.stderr.read(self.BUFFER_LENGTH)
            if not buf:
                LOGGER.debug("stderr closed")
                return

            try:
                LOGGER.debug("stderr:\n%s", buf)
            except UnicodeDecodeError as err:
                LOGGER.error(err)

    def listen(self):
        """listen PIPE"""
        LOGGER.info("listen")

        stdout_thread = threading.Thread(target=self._listen_stdout, daemon=True)
        stderr_thread = threading.Thread(target=self._listen_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

    def terminate(self):
        """terminate process"""
        LOGGER.info("terminate")
        self.server_process.terminate()
