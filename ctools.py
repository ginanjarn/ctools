"""ctools main app"""

import logging
import os
import queue
import threading
import time

from urllib.request import pathname2url, url2pathname
from typing import List, Iterable, Union, Dict, Iterator

import sublime
import sublime_plugin

from .api import lsp
from .third_party import mistune


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


class DocumentURI(str):
    """document uri"""

    @classmethod
    def from_path(cls, file_name):
        """from file name"""
        return cls("file:%s" % pathname2url(file_name))

    def to_path(self) -> str:
        """convert to path"""
        return url2pathname(self.lstrip("file:"))


# custom kind
KIND_PATH = (sublime.KIND_ID_NAVIGATION, "p", "")
KIND_VALUE = (sublime.KIND_ID_NAVIGATION, "u", "")

_KIND_MAP = {
    1: sublime.KIND_NAVIGATION,
    2: sublime.KIND_FUNCTION,
    3: sublime.KIND_FUNCTION,
    4: sublime.KIND_FUNCTION,
    5: sublime.KIND_VARIABLE,
    6: sublime.KIND_VARIABLE,
    7: sublime.KIND_TYPE,
    8: sublime.KIND_TYPE,
    9: sublime.KIND_NAMESPACE,
    10: sublime.KIND_VARIABLE,
    11: KIND_VALUE,
    12: KIND_VALUE,
    13: sublime.KIND_TYPE,
    14: sublime.KIND_KEYWORD,
    15: sublime.KIND_SNIPPET,
    16: KIND_VALUE,
    17: KIND_PATH,
    18: sublime.KIND_NAVIGATION,
    19: KIND_PATH,
    20: sublime.KIND_VARIABLE,
    21: sublime.KIND_VARIABLE,
    22: sublime.KIND_TYPE,
    23: sublime.KIND_AMBIGUOUS,
    24: sublime.KIND_MARKUP,
    25: sublime.KIND_TYPE,
}


class CompletionList(sublime.CompletionList):
    """CompletionList"""

    @staticmethod
    def build_completion(rpc_items: Dict[str, object]):
        """build completion item"""

        for item in rpc_items:

            # additional changes, ex: include library
            changes = item.get("additionalTextEdits", [])

            # completion text
            text_changes = item["textEdit"]
            # sublime text remove existing completion word
            text_changes["range"]["end"] = text_changes["range"]["start"]

            # include completion path ended with `"` or `>`
            if item["kind"] == 17:
                text_changes["newText"] = text_changes["newText"].strip('">')

            changes.append(text_changes)

            yield sublime.CompletionItem.command_completion(
                trigger=item["filterText"],
                command="ctools_apply_document_change",
                args={"changes": changes},
                annotation=item["label"],
                kind=_KIND_MAP.get(item["kind"], sublime.KIND_AMBIGUOUS),
            )

    @classmethod
    def from_rpc(cls, completion_items: List[dict]):
        """load from rpc"""

        LOGGER.debug("completion_list: %s", completion_items)

        return cls(
            completions=list(cls.build_completion(completion_items))
            if completion_items
            else [],
            flags=sublime.INHIBIT_WORD_COMPLETIONS
            | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
        )


class DiagnosticItem:
    """diagnostic item"""

    def __init__(self, region: sublime.Region, severity: int, message: str):
        self.region = region
        self.severity = severity
        self.message = message

    @classmethod
    def from_rpc(cls, view: sublime.View, *, diagnostic: dict):
        """from rpc"""
        range_ = diagnostic["range"]
        start = view.text_point(range_["start"]["line"], range_["start"]["character"])
        end = view.text_point(range_["end"]["line"], range_["end"]["character"])
        return cls(
            sublime.Region(start, end), diagnostic["severity"], diagnostic["message"]
        )


class Diagnostics:
    """Diagnostic hold diagnostic data at view"""

    region_key = {
        1: "ctools.error",
        2: "ctools.warning",
        3: "ctools.information",
        4: "ctools.hint",
    }

    def __init__(self, view: sublime.View):
        self.view = view
        self.window = self.view.window()
        self.message_map = {}
        self.outputpanel_name = f"ctools:{self.view.file_name()}"

    def add_regions(
        self, key: str, regions: List[sublime.Region], *, error_region: bool = False
    ):
        """add syntax highlight regions"""

        self.view.add_regions(
            key=key,
            regions=regions,
            scope="Comment",
            icon="circle" if error_region else "dot",
            flags=(
                sublime.DRAW_NO_FILL
                | sublime.DRAW_NO_OUTLINE
                | sublime.DRAW_SOLID_UNDERLINE
            ),
        )

    def set_diagnostics(self, diagnostics: List[dict]):
        """set diagnostic

        * set message_map
        * apply syntax highlight
        """

        adapted_diagnostic = [
            DiagnosticItem.from_rpc(self.view, diagnostic=diagnostic)
            for diagnostic in diagnostics
        ]

        error_region = []
        warning_region = []
        information_region = []
        hint_region = []

        for diagnostic in adapted_diagnostic:
            row, col = self.view.rowcol(diagnostic.region.a)
            self.message_map[(row, col)] = diagnostic.message

            if diagnostic.severity == 1:
                error_region.append(diagnostic.region)
            elif diagnostic.severity == 2:
                warning_region.append(diagnostic.region)
            elif diagnostic.severity == 3:
                information_region.append(diagnostic.region)
            elif diagnostic.severity == 4:
                hint_region.append(diagnostic.region)

        # clear if any highlight in view
        self.erase_highlight()

        self.add_regions(
            key=self.region_key[1], regions=error_region, error_region=True
        )
        self.add_regions(key=self.region_key[2], regions=warning_region)
        self.add_regions(key=self.region_key[3], regions=information_region)
        self.add_regions(key=self.region_key[4], regions=hint_region)

    def erase_highlight(self):
        """erase highlight"""

        for _, value in Diagnostics.region_key.items():
            self.view.erase_regions(value)

    def show_panel(self) -> None:
        """show output panel"""

        def build_message(mapping: Dict[tuple, str]):
            short_name = os.path.basename(self.view.file_name())
            for key, val in mapping.items():
                row, col = key
                yield f"{short_name}:{row+1}:{col} {val}"

        if self.message_map:

            # create new panel
            panel = self.window.create_output_panel(self.outputpanel_name)
            message = "\n".join(build_message(self.message_map))

            panel.set_read_only(False)
            panel.run_command(
                "append", {"characters": message},
            )

        self.window.run_command(
            "show_panel", {"panel": f"output.{self.outputpanel_name}"}
        )

    def destroy_panel(self):
        """destroy output panel"""
        self.window.destroy_output_panel(self.outputpanel_name)


class ChangeItem:
    """this class hold change data"""

    def __init__(self, region: sublime.Region, old_text: str, new_text: str):
        self.region = region
        self.old_text = old_text
        self.new_text = new_text

        # cursor position move
        self.cursor_move = len(new_text) - region.size()

    def get_region(self, cursor_move: int = 0):
        """get region with adjusted position to cursor move"""
        return sublime.Region(self.region.a + cursor_move, self.region.b + cursor_move)

    def __repr__(self):
        return (
            f"ChangeItem({repr(self.region)}, "
            f"{repr(self.old_text)}, {repr(self.new_text)}, "
            f"{self.cursor_move})"
        )

    @classmethod
    def from_rpc(cls, view: sublime.View, *, change: Dict):
        """from rpc"""

        range_ = change["range"]
        new_text = change["newText"]

        start = view.text_point(range_["start"]["line"], range_["start"]["character"])
        end = view.text_point(range_["end"]["line"], range_["end"]["character"])

        region = sublime.Region(start, end)
        old_text = view.substr(region)
        return cls(region, old_text, new_text)


class DocumentChangeSync:
    """Document change sync prevent multiple file changes at same time"""

    _lock = threading.Lock()

    def __init__(self):
        self.busy = False

    def set_busy(self):
        with self._lock:
            self.busy = True

    def set_finished(self):
        with self._lock:
            self.busy = False


DOCUMENT_CHANGE_SYNC = DocumentChangeSync()


class CtoolsApplyDocumentChangeCommand(sublime_plugin.TextCommand):
    """apply document change to view"""

    def run(self, edit: sublime.Edit, changes: list):
        LOGGER.info("CtoolsApplyDocumentChangeCommand")

        LOGGER.debug("apply changes to %s", self.view.file_name())
        LOGGER.debug("changes: %s", changes)

        list_change_item: List[ChangeItem] = [
            ChangeItem.from_rpc(self.view, change=change) for change in changes
        ]

        def sort_by_region(item: ChangeItem):
            return item.region

        # prevent change collision
        list_change_item.sort(key=sort_by_region)

        # this hold cursor movement
        cursor_move = 0

        for change in list_change_item:
            region = change.get_region(cursor_move)
            self.view.erase(edit, region)
            self.view.insert(edit, region.a, change.new_text)
            cursor_move += change.cursor_move

        DOCUMENT_CHANGE_SYNC.set_finished()


class ActiveDocument:
    """commands to active view"""

    def __init__(self):

        self.completion_result = None
        self._window: sublime.Window = None
        self._view: sublime.View = None

    @property
    def window(self):
        if self._window is None:
            self.window = sublime.active_window()
            return self._window
        return self._window

    @window.setter
    def window(self, value):
        self._window = value

    @property
    def view(self):
        if self._view is None:
            self._view = self.window.active_view()
        return self._view

    @view.setter
    def view(self, value):
        self._view = value

    def get_completion_result(self):
        result = self.completion_result
        self.completion_result = None
        return result

    def show_completions(self, completions: List[dict]):
        completion_list = CompletionList.from_rpc(completions)
        self.completion_result = completion_list

        self.view.run_command(
            "auto_complete",
            {
                "disable_auto_insert": True,
                "next_completion_if_showing": False,
                "auto_complete_commit_on_tab": True,
            },
        )

    def hide_completion(self):
        self.view.run_command("hide_auto_complete")

    @staticmethod
    def adapt_minihtml(lines: str) -> Iterator[str]:
        """adapt sublime minihtml tag

        Not all html tag implemented
        """
        pre_tag = False
        for line in lines.splitlines():

            if line.startswith("<pre>"):
                pre_tag = True
            elif pre_tag and line.endswith("</pre>"):
                pre_tag = False

            line = line.replace("  ", "&nbsp;&nbsp")
            line = f"{line}<br />" if pre_tag else line

            yield line

    def show_popup(self, documentation):
        contents = documentation["contents"]["value"]
        kind = documentation["contents"]["kind"]
        start = documentation["range"]["start"]
        location = self.view.text_point(start["line"], start["character"])

        if kind == "markdown":
            contents = mistune.markdown(contents)

        contents = "\n".join(self.adapt_minihtml(contents))
        self.view.show_popup(
            contents, flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY, location=location
        )

    def show_code_action(self, action_params: List[dict]):
        def on_done(index=-1):
            if index > -1:
                self.view.run_command(
                    "ctools_workspace_exec_command",
                    {"params": action_params[index]["command"]},
                )

        items = [item["title"] for item in action_params]
        self.window.show_quick_panel(items, on_done)

    def apply_document_change(self, changes: List[dict]):

        # wait until view loaded
        while True:
            LOGGER.debug("loading %s", self.view.file_name())
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        self.view.run_command("ctools_apply_document_change", {"changes": changes})

    def prepare_rename(self, params):
        start = params.result["start"]
        end = params.result["end"]
        placeholder = self.view.substr(
            sublime.Region(
                self.view.text_point(start["line"], start["character"]),
                self.view.text_point(end["line"], end["character"]),
            )
        )

        self.input_rename(start["line"], start["character"], placeholder)

    def input_rename(self, row, col, placeholder: str):
        def apply_rename(new_name):
            self.view.run_command(
                "ctools_rename", {"row": row, "col": col, "new_name": new_name}
            )

        self.window.show_input_panel(
            caption="rename",
            initial_text=placeholder,
            on_done=apply_rename,
            on_change=None,
            on_cancel=None,
        )

    def goto(self, params: List[dict]):
        LOGGER.debug("goto: %s", params)

        def get_location(location: Dict[str, object]):
            path = DocumentURI(location["uri"]).to_path()
            start = location["range"]["start"]
            row, col = start["line"] + 1, start["character"] + 1
            return f"{path}:{row}:{col}"

        paths = [get_location(item) for item in params]

        def on_select(index=-1):
            if index > -1:
                self.window.open_file(paths[index], flags=sublime.ENCODED_POSITION)

        self.window.show_quick_panel(
            items=paths, on_select=on_select, flags=sublime.MONOSPACE_FONT
        )


class Document:
    """Document handler"""

    def __init__(self, file_name: str, *, open_file: bool = False):
        view = sublime.active_window().find_open_file(file_name)
        if not view and open_file:
            view = sublime.active_window().open_file(file_name)

        self.view = view
        self.window = view.window()

    def apply_document_change(self, changes: List[dict]):

        # wait until view loaded
        while True:
            LOGGER.debug("loading %s", self.view.file_name())
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        # self.window.focus_view(self.view)
        self.view.run_command("ctools_apply_document_change", {"changes": changes})

    def apply_diagnostics(self, diagnostics_item: List[dict]):

        LOGGER.debug("apply diagnostics to: %s", self.view.file_name())
        diagnostics = Diagnostics(self.view)
        diagnostics.set_diagnostics(diagnostics_item)
        diagnostics.show_panel()

    def clear_diagnostics(self):
        diagnostic = Diagnostics(self.view)
        try:
            diagnostic.erase_highlight()
            diagnostic.destroy_panel()

        except Exception as err:
            LOGGER.error(err)

    def show_diagnostics(self):
        diagnostic = Diagnostics(self.view)
        diagnostic.show_panel()

    def set_status(self, message: str):
        self.view.set_status("ctools_status", message)

    def erase_status(self):
        self.view.erase_status("ctools_status")


ACTIVE_DOCUMENT: ActiveDocument = ActiveDocument()


class ServerOffline(Exception):
    """server not running"""


class ClangdClient(lsp.LSPClient):
    """LSP client listener"""

    def __init__(self):
        super().__init__()
        self.transport: lsp.AbstractTransport = None

        self.completion_commit_character = []

    def server_running(self):
        return bool(self.transport)

    def _hide_completion(self, character: str):
        LOGGER.info("_hide_completion")

        if character in self.completion_commit_character:
            ACTIVE_DOCUMENT.hide_completion()

    def shutdown_server(self):
        LOGGER.debug("shutdown_server")
        if self.transport:
            self.transport.terminate()

    def handle_initialize(self, params: lsp.RPCMessage):
        LOGGER.info("handle_initialize")

        LOGGER.debug("params: %s", params)
        # FIXME: handle initialize
        # ------------------------
        if params.error:
            LOGGER.error(params.error)

        capabilities = params.result["capabilities"]

        self.completion_commit_character = capabilities["completionProvider"][
            "allCommitCharacters"
        ]
        self.is_initialized = True

        # notify if initialized
        self.transport.notify(lsp.RPCMessage.notification("initialized"))

    def handle_textDocument_completion(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_completion")

        completion_items = params.result["items"]
        ACTIVE_DOCUMENT.show_completions(completion_items)

    def handle_textDocument_hover(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_hover")

        if params.error:
            LOGGER.error(params.error)

        ACTIVE_DOCUMENT.show_popup(params.result)

    def handle_textDocument_formatting(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_formatting")

        changes = params.result
        try:
            ACTIVE_DOCUMENT.apply_document_change(changes)
        except Exception as err:
            LOGGER.error(err)

    def handle_textDocument_semanticTokens_full(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_semanticTokens_full")
        LOGGER.debug(params)

    def handle_textDocument_documentLink(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_documentLink")
        LOGGER.debug(params)

    def handle_textDocument_documentSymbol(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_documentSymbol")
        LOGGER.debug(params)

    def handle_textDocument_codeAction(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_codeAction")
        LOGGER.debug(params)
        ACTIVE_DOCUMENT.show_code_action(params.result)

    def handle_textDocument_publishDiagnostics(self, params: Dict[str, object]):
        LOGGER.info("handle_textDocument_publishDiagnostics")

        diagnostics = params["diagnostics"]
        LOGGER.debug(diagnostics)

        document = Document(DocumentURI(params["uri"]).to_path())
        document.clear_diagnostics()

        if not diagnostics:
            return

        document.apply_diagnostics(diagnostics)

    def handle_window_workDoneProgress_create(self, params):
        LOGGER.info("handle_window_workDoneProgress_create")
        LOGGER.debug(params)

    # def _textDocument_clangd_fileStatus(self, params):
    #     LOGGER.info("_textDocument_clangd_fileStatus")
    #     LOGGER.debug(params)

    def handle_S_progress(self, params):
        LOGGER.info("handle_S_progress")
        LOGGER.debug(params)

    def _apply_edit_changes(self, edit_changes: Dict[str, dict]):
        LOGGER.info("_apply_edit_changes")

        if not edit_changes:
            LOGGER.debug("nothing changed")
            return

        for file_name, text_changes in edit_changes.items():
            LOGGER.debug("try apply changes to %s", file_name)

            while True:
                if DOCUMENT_CHANGE_SYNC.busy:
                    LOGGER.debug("busy")
                    time.sleep(0.5)
                    continue
                break

            LOGGER.debug("apply changes to: %s", file_name)
            DOCUMENT_CHANGE_SYNC.set_busy()
            document = Document(DocumentURI(file_name).to_path(), open_file=True)

            try:
                document.apply_document_change(text_changes)

            except Exception as err:
                LOGGER.error(err)

            finally:
                DOCUMENT_CHANGE_SYNC.set_finished()

            LOGGER.debug("finish apply to: %s", file_name)

    def handle_workspace_applyEdit(self, params: Dict[str, object]):
        LOGGER.info("handle_workspace_applyEdit")

        try:
            changes = params["edit"]["changes"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                self._apply_edit_changes(changes)
            except Exception as err:
                LOGGER.error("error apply edit_changes: %s", repr(err))

    def handle_textDocument_prepareRename(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_prepareRename")
        LOGGER.debug("params: %s", params)

        ACTIVE_DOCUMENT.prepare_rename(params)

    def handle_textDocument_rename(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_rename")
        LOGGER.debug("params: %s", params)

        try:
            changes = params.result["changes"]
        except Exception as err:
            LOGGER.error(repr(err))
        else:
            try:
                self._apply_edit_changes(changes)
            except Exception as err:
                LOGGER.error("error apply edit_changes: %s", repr(err))

    def handle_textDocument_definition(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_definition")
        LOGGER.debug("params: %s", params)
        ACTIVE_DOCUMENT.goto(params.result)

    def handle_textDocument_declaration(self, params: lsp.RPCMessage):
        LOGGER.info("handle_textDocument_declaration")
        LOGGER.debug("params: %s", params)
        ACTIVE_DOCUMENT.goto(params.result)

    def cancelRequest(self):
        self.transport.cancel_request(lsp.RPCMessage.cancel_request(self.request_id))

    def initialize(self, project_path: str):
        """initialize server"""

        LOGGER.info("initialize")

        if not self.server_running:
            raise ServerOffline

        params = {
            "capabilities": {
                "textDocument": {
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
                    "hover": {"contentFormat": ["markdown", "plaintext"],},
                }
            }
        }
        self.transport.request(
            lsp.RPCMessage.request(self.get_request_id(), "initialize", params)
        )

    def textDocument_didOpen(self, file_name: str, source: str):
        LOGGER.info("textDocument_didOpen")

        if not self.server_running:
            raise ServerOffline

        params = {
            "textDocument": {
                "languageId": "cpp",
                "text": source,
                "uri": DocumentURI.from_path(file_name),
                "version": self.get_document_version(),
            }
        }
        self.transport.notify(
            lsp.RPCMessage.notification("textDocument/didOpen", params)
        )

    def textDocument_didChange(self, path: str, changes: dict):
        LOGGER.info("textDocument_didChange")

        if not self.server_running:
            raise ServerOffline

        params = {
            "contentChanges": changes,
            "textDocument": {
                "uri": DocumentURI.from_path(path),
                "version": self.get_document_version(),
            },
        }
        LOGGER.debug("didChange: %s", params)
        self._hide_completion(changes[0]["text"])
        self.transport.notify(
            lsp.RPCMessage.notification("textDocument/didChange", params)
        )

    def textDocument_didClose(self, path: str):
        LOGGER.info("textDocument_didClose")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(path)}}
        self.transport.notify(
            lsp.RPCMessage.notification("textDocument/didClose", params)
        )

    def textDocument_didSave(self, path: str):
        LOGGER.info("textDocument_didSave")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(path)}}
        self.transport.notify(
            lsp.RPCMessage.notification("textDocument/didSave", params)
        )

    def textDocument_completion(self, path: str, row: int, col: int):
        LOGGER.info("textDocument_completion")

        if not self.server_running:
            raise ServerOffline

        params = {
            "context": {"triggerKind": 1},  # TODO: adapt KIND
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/completion", params
            )
        )

    def textDocument_hover(self, path: str, row: int, col: int):
        LOGGER.info("textDocument_hover")

        if not self.server_running:
            raise ServerOffline
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(self.get_request_id(), "textDocument/hover", params)
        )

    def textDocument_formatting(self, path, tab_size=2):
        LOGGER.info("textDocument_formatting")

        if not self.server_running:
            raise ServerOffline

        params = {
            "options": {"insertSpaces": True, "tabSize": tab_size},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/formatting", params
            )
        )

    def textDocument_semanticTokens_full(self, path: str):
        LOGGER.info("textDocument_semanticTokens_full")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/semanticTokens/full", params
            )
        )

    def textDocument_documentLink(self, path: str):
        LOGGER.info("textDocument_documentLink")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/documentLink", params
            )
        )

    def textDocument_documentSymbol(self, path: str):
        LOGGER.info("textDocument_documentSymbol")

        if not self.server_running:
            raise ServerOffline

        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/documentSymbol", params
            )
        )

    def textDocument_codeAction(
        self, path: str, start_line: int, start_col: int, end_line: int, end_col: int
    ):
        LOGGER.info("textDocument_codeAction")

        if not self.server_running:
            raise ServerOffline

        params = {
            "context": {"diagnostics": []},
            "range": {
                "end": {"character": end_col, "line": end_line},
                "start": {"character": start_col, "line": start_line},
            },
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        LOGGER.debug("codeAction params: %s", params)
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/codeAction", params
            )
        )

    def workspace_executeCommand(self, params: dict):

        if not self.server_running:
            raise ServerOffline

        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "workspace/executeCommand", params
            )
        )

    def textDocument_prepareRename(self, path, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/prepareRename", params
            )
        )

    def textDocument_rename(self, path, row, col, new_name):

        if not self.server_running:
            raise ServerOffline

        params = {
            "newName": new_name,
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(self.get_request_id(), "textDocument/rename", params)
        )

    def textDocument_definition(self, path, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/definition", params
            )
        )

    def textDocument_declaration(self, path, row, col):

        if not self.server_running:
            raise ServerOffline

        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            lsp.RPCMessage.request(
                self.get_request_id(), "textDocument/declaration", params
            )
        )


CLANGD_CLIENT = ClangdClient()


def plugin_loaded():
    settigs_basename = "C++.sublime-settings"
    settings: sublime.Settings = sublime.load_settings(settigs_basename)
    settings.set("index_files", False)
    settings.set("show_definitions", False)
    sublime.save_settings(settigs_basename)


def plugin_unloaded():
    CLANGD_CLIENT.shutdown_server()


def get_project_path(file_name: str):
    if not file_name:
        raise ValueError("invalid file_name: %s" % file_name)

    folders = [
        folder
        for folder in sublime.active_window().folders()
        if file_name.startswith(folder)
    ]
    if not folders:
        return os.path.dirname(file_name)
    return max(folders)


REQUEST_LOCK = threading.Lock()


def pipe(func):
    def wrapper(*args, **kwargs):
        if REQUEST_LOCK.locked():
            return None

        with REQUEST_LOCK:
            return func(*args, **kwargs)

    return wrapper


def valid_source(view: sublime.View) -> bool:
    return view.match_selector(0, "source.c++") or view.match_selector(0, "source.c")


def valid_identifier(view: sublime.View, location: int):
    if view.match_selector(location, "string") or view.match_selector(
        location, "comment"
    ):
        return False
    return True


class EventListener(sublime_plugin.EventListener):
    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> Union[CompletionList, None]:

        if not (valid_source(view) and valid_identifier(view, locations[0])):
            return None

        completions = ACTIVE_DOCUMENT.get_completion_result()
        if completions:
            return completions

        thread = threading.Thread(
            target=self.on_query_completions_task, args=(view, locations)
        )
        thread.start()

        ACTIVE_DOCUMENT.hide_completion()
        return None

    @pipe
    def on_query_completions_task(self, view, locations):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()
        row, col = view.rowcol(locations[0])

        try:
            ACTIVE_DOCUMENT.view = view
            CLANGD_CLIENT.textDocument_completion(file_name, row, col)

        except ServerOffline:
            CLANGD_CLIENT.run_server()
            CLANGD_CLIENT.initialize(get_project_path(file_name))
            CLANGD_CLIENT.textDocument_didOpen(file_name, source)

            CLANGD_CLIENT.textDocument_completion(file_name, row, col)

    def on_hover(self, view: sublime.View, point: int, hover_zone: int) -> None:
        if not valid_source(view):
            return

        if not (hover_zone == sublime.HOVER_TEXT and valid_identifier(view, point)):
            # LOGGER.debug("currently only support HOVER_TEXT")
            return

        if point == view.size():
            return

        thread = threading.Thread(target=self.on_hover_text_task, args=(view, point))
        thread.start()

    @pipe
    def on_hover_text_task(self, view, point):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()
        row, col = view.rowcol(point)

        try:
            ACTIVE_DOCUMENT.view = view
            CLANGD_CLIENT.textDocument_hover(file_name, row, col)

        except ServerOffline:
            CLANGD_CLIENT.run_server()
            CLANGD_CLIENT.initialize(get_project_path(file_name))
            CLANGD_CLIENT.textDocument_didOpen(file_name, source)

            CLANGD_CLIENT.textDocument_hover(file_name, row, col)

    def on_activated_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and CLANGD_CLIENT.is_initialized):
            return

        # show diagnostic
        document = Document(file_name)
        document.show_diagnostics()

        source = view.substr(sublime.Region(0, view.size()))
        try:
            CLANGD_CLIENT.textDocument_didOpen(file_name, source)
            # set current active view
            ACTIVE_DOCUMENT.view = view
        except ServerOffline:
            pass

    def on_close(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and CLANGD_CLIENT.is_initialized):
            return

        try:
            CLANGD_CLIENT.textDocument_didClose(file_name)
            # reset active view
            ACTIVE_DOCUMENT.view = None
        except ServerOffline:
            pass

    def on_post_save_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (valid_source(view) and CLANGD_CLIENT.is_initialized):
            return

        try:
            CLANGD_CLIENT.textDocument_didSave(file_name)
        except ServerOffline:
            pass


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed_async(self, changes: List[sublime.TextChange]):

        file_name = self.buffer.file_name()
        view = self.buffer.primary_view()

        if not (valid_source(view) and CLANGD_CLIENT.is_initialized):
            return

        LOGGER.info("on_text_changed_async")

        content_changes = []
        for change in changes:
            start: sublime.HistoricPosition = change.a
            end: sublime.HistoricPosition = change.b

            content_changes.append(
                {
                    "range": {
                        "end": {"character": end.col, "line": end.row},
                        "start": {"character": start.col, "line": start.row},
                    },
                    "rangeLength": change.len_utf8,
                    "text": change.str,
                }
            )

        try:
            CLANGD_CLIENT.cancelRequest()
            CLANGD_CLIENT.textDocument_didChange(file_name, content_changes)
        except ServerOffline:
            pass


class CtoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("CtoolsDocumentFormattingCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            try:
                CLANGD_CLIENT.textDocument_formatting(self.view.file_name())
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsCodeActionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("CtoolsDocumentFormattingCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            location = self.view.sel()[0]
            start_row, start_col = self.view.rowcol(location.a)
            end_row, end_col = self.view.rowcol(location.b)

            try:
                CLANGD_CLIENT.textDocument_codeAction(
                    self.view.file_name(), start_row, start_col, end_row, end_col
                )
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsWorkspaceExecCommandCommand(sublime_plugin.TextCommand):
    def run(self, edit, params):
        LOGGER.info("CtoolsWorkspaceExecCommandCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            try:
                CLANGD_CLIENT.workspace_executeCommand(params)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, row, col, new_name):
        LOGGER.info("CtoolsRenameCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            file_name = self.view.file_name()
            try:
                CLANGD_CLIENT.textDocument_rename(file_name, row, col, new_name)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsPrepareRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsPrepareRenameCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                CLANGD_CLIENT.textDocument_prepareRename(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsGotoDefinitionCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                CLANGD_CLIENT.textDocument_definition(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized


class CtoolsGotoDeclarationCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsGotoDeclarationCommand")

        if valid_source(self.view) and CLANGD_CLIENT.is_initialized:
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            try:
                CLANGD_CLIENT.textDocument_declaration(file_name, row, col)
            except ServerOffline:
                pass

    def is_visible(self):
        return valid_source(self.view) and CLANGD_CLIENT.is_initialized
