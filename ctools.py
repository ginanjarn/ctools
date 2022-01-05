"""ctools main app"""

import logging
import os
import queue
import threading
import time

from urllib.request import pathname2url, url2pathname
from typing import List, Iterable, Union, Dict

import sublime
import sublime_plugin

from .plugin import context


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

    @classmethod
    def from_rpc(cls, completion_items: List[dict]):
        """load from rpc"""

        LOGGER.debug("completion_list: %s", completion_items)

        completions = [
            sublime.CompletionItem(
                trigger=completion["filterText"].strip('">')
                if completion["kind"] == 17
                else completion["insertText"],
                annotation=completion["label"].strip('">')
                if completion["kind"] == 17
                else completion["label"],
                completion=completion["insertText"].strip('">')
                if completion["kind"] == 17
                else completion["insertText"],
                completion_format=sublime.COMPLETION_FORMAT_SNIPPET,
                kind=_KIND_MAP.get(completion["kind"], sublime.KIND_AMBIGUOUS),
            )
            for completion in sorted(completion_items, key=lambda item: item["score"])
        ]

        return cls(
            completions,
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


class SyntaxHighlight:
    """handle syntax highlight"""

    def __init__(
        self,
        view: sublime.View,
        *,
        key: str,
        regions: List[sublime.Region],
        scope: str = "Comment",
        icon: str = "dot",
    ):
        self.view = view
        self.region_key = key
        self.regions = regions
        self.scope = scope
        self.icon = icon
        self.flags = (
            sublime.DRAW_NO_FILL
            | sublime.DRAW_NO_OUTLINE
            | sublime.DRAW_SOLID_UNDERLINE
        )

    def apply(self):
        """apply highlight"""

        self.view.add_regions(
            key=self.region_key,
            regions=self.regions,
            scope=self.scope,
            icon=self.icon,
            flags=self.flags,
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
        self.message_map = {}

    def get_message(self, lineno: int) -> str:
        """get message"""
        return self.message_map.get(lineno)

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
            row, _ = self.view.rowcol(diagnostic.region.a)
            self.message_map[row] = diagnostic.message

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

        SyntaxHighlight(self.view, key=self.region_key[1], regions=error_region).apply()
        SyntaxHighlight(
            self.view, key=self.region_key[2], regions=warning_region
        ).apply()
        SyntaxHighlight(
            self.view, key=self.region_key[3], regions=information_region
        ).apply()
        SyntaxHighlight(self.view, key=self.region_key[4], regions=hint_region).apply()

    def erase_highlight(self):
        """erase highlight"""

        for _, value in Diagnostics.region_key.items():
            self.view.erase_regions(value)


class ChangeItem:
    """this class hold change data"""

    def __init__(self, region: sublime.Region, new_text: str):
        self.region = region
        self.new_text = new_text

        # cursor position move
        self.cursor_move = len(new_text) - region.size()

    def get_region(self, cursor_move: int = 0):
        """get region with adjusted position to cursor move"""
        return sublime.Region(self.region.a + cursor_move, self.region.b + cursor_move)

    def __repr__(self):
        return str(
            {
                "cursor_move": self.cursor_move,
                "region": str(self.region),
                "new_text": self.new_text,
            }
        )

    @classmethod
    def from_rpc(cls, view: sublime.View, *, range_: dict, new_text: str):
        """from rpc"""

        start = view.text_point(range_["start"]["line"], range_["start"]["character"])
        end = view.text_point(range_["end"]["line"], range_["end"]["character"])
        return cls(sublime.Region(start, end), new_text)


class DocumentChangeSync:
    def __init__(self):
        self.busy = False

    def set_busy(self):
        self.busy = True

    def set_finished(self):
        self.busy = False


DOCUMENT_CHANGE_SYNC = DocumentChangeSync()


class CtoolsApplyDocumentChangeCommand(sublime_plugin.TextCommand):
    """apply document change to view"""

    def run(self, edit: sublime.Edit, changes: list):
        LOGGER.info("CtoolsApplyDocumentChangeCommand")

        view: sublime.View = self.view

        list_change_item: List[ChangeItem] = [
            ChangeItem.from_rpc(
                view, range_=change["range"], new_text=change["newText"]
            )
            for change in changes
        ]

        # this hold cursor movement
        cursor_move = 0

        for change in list_change_item:
            region = change.get_region(cursor_move)
            view.erase(edit, region)
            view.insert(edit, region.a, change.new_text)
            cursor_move += change.cursor_move

        DOCUMENT_CHANGE_SYNC.set_finished()


class ViewCommand:
    """commands to active view"""

    def __init__(self):
        self.window: sublime.Window = sublime.active_window()
        self.view: sublime.View = self.window.active_view()

        self.completion_queue = queue.Queue(1)
        self.diagnostics_map = {}

    def get_completion_result(self):
        try:
            return self.completion_queue.get_nowait()
        except queue.Empty:
            return None

    def open_file(self, file_name: str, focus_view=True):
        LOGGER.debug("open_file: %s", file_name)
        if self.view and self.view.file_name() == file_name:
            return

        self.window = sublime.active_window()

        view = self.window.find_open_file(file_name)
        if not view:
            view = self.window.open_file(file_name)

        self.view = view

    def focus_view(self):
        self.window.focus_view(self.view)

    def close(self, file_name: str):
        if self.view.file_name() != file_name:
            return

        self.view.close()

        self.window = sublime.active_window()
        self.view = self.window.active_view()

    def show_completions(self, completions: List[dict]):
        completion_list = CompletionList.from_rpc(completions)
        try:
            self.completion_queue.put_nowait(completion_list)
        except queue.Full:
            pass

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

    def show_popup(self, content: str, location: Union[int, Iterable]):
        if isinstance(location, Iterable):
            location = self.view.text_point(location[0], location[1])

        self.view.run_command(
            "ctools_markdown_popup", {"content": content, "location": location}
        )

    def show_code_action(self, action_params: List[dict]):
        # action_params
        # [
        #     {
        #         "arguments": [
        #             {
        #                 "file": "file:///C:/Users/ginanjar/cproject/cpptools/main.cpp",
        #                 "selection": {
        #                     "end": {"character": 39, "line": 8},
        #                     "start": {"character": 39, "line": 8},
        #                 },
        #                 "tweakID": "AddUsing",
        #             }
        #         ],
        #         "command": "clangd.applyTweak",
        #         "title": "Add using-declaration for endl and remove qualifier",
        #     }
        # ],

        def on_done(index=-1):
            if index > -1:
                self.view.run_command(
                    "ctools_workspace_exec_command", {"params": action_params[index]}
                )

        items = [item["title"] for item in action_params]
        self.window.show_quick_panel(items, on_done, flags=sublime.MONOSPACE_FONT)

    def apply_document_change(self, changes: List[dict]):
        while True:
            LOGGER.debug("loading %s", self.view.file_name())
            if self.view.is_loading():
                time.sleep(0.5)
                continue
            break

        self.view.run_command("ctools_apply_document_change", {"changes": changes})

    def apply_diagnostics(self, file_name: str, diagnostics_item: List[dict]):
        if file_name != self.view.file_name():
            LOGGER.debug("invalid view, want %s, active %s", file_name, self.view.file_name())
            return

        LOGGER.debug("apply diagnostics to: %s", file_name)
        diagnostics = Diagnostics(self.view)
        diagnostics.set_diagnostics(diagnostics_item)
        LOGGER.debug("diagnostic message map: %s", diagnostics.message_map)
        self.diagnostics_map = diagnostics.message_map

    def clear_diagnostics(self):
        LOGGER.debug("clear_diagnostics to %s", self.view.file_name())
        Diagnostics(self.view).erase_highlight()
        self.diagnostics_map = {}

    def set_status(self, message: str):
        self.view.set_status("ctools_status", message)

    def erase_status(self):
        self.view.erase_status("ctools_status")

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
        # params
        # [
        #     {
        #         "range": {
        #             "end": {"character": 25, "line": 0},
        #             "start": {"character": 21, "line": 0},
        #         },
        #         "uri": "file:///C:/Users/ginanjar/cproject/cpptools/meteor.cpp",
        #     }
        # ]

        items = params

        file_names_encoded = [
            "{file_name}:{row}:{col}".format(
                file_name=DocumentURI(item["uri"]).to_path(),
                row=item["range"]["start"]["line"] + 1,
                col=item["range"]["start"]["character"] + 1,
            )
            for item in params
        ]

        def on_done(index=-1):
            if index > -1:
                LOGGER.debug("selected item: %s", file_names_encoded[index])

                view = self.window.find_open_file(
                    DocumentURI(items[index]["uri"]).to_path()
                )

                if not view:
                    self.window.open_file(
                        file_names_encoded[index], sublime.ENCODED_POSITION
                    )
                    return

                startpoint = view.text_point(
                    items[index]["range"]["start"]["line"],
                    items[index]["range"]["start"]["character"],
                )
                region = sublime.Region(startpoint, startpoint)
                self.window.focus_view(view)
                view.sel().clear()
                view.sel().add(region)
                view.show(region)

        self.window.show_quick_panel(
            file_names_encoded, on_done, flags=sublime.MONOSPACE_FONT
        )


VIEW_COMMAND: ViewCommand = ViewCommand()


class LSPClientListener:
    """LSP client listener"""

    def __init__(self, process_cmd: list):

        LOGGER.debug("server process cmd: %s", process_cmd)
        self.transport: context.Transport = context.StandardIO(process_cmd)
        self._register_commands()

        self.active_file = ""
        self.completion_commit_character = []

    def _hide_completion(self, character: str):
        LOGGER.info("_hide_completion")
        if character in self.completion_commit_character:
            VIEW_COMMAND.hide_completion()

    def shutdown_server(self):
        LOGGER.debug("shutdown_server")
        self.transport.exit()

    def _handle_initialize(self, params: context.ResponseMessage):
        LOGGER.info("_handle_initialize")

        LOGGER.debug("params: %s", params)
        # FIXME: handle initialize
        # ------------------------
        if params.error:
            LOGGER.error(params.error)

        capabilities = params.result["capabilities"]

        self.completion_commit_character = capabilities["completionProvider"][
            "allCommitCharacters"
        ]

        # notify if initialized
        self.transport.notify(context.RequestMessage(None, "initialized"))

    def _handle_textDocument_completion(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_completion")

        completion_items = params.result["items"]
        VIEW_COMMAND.show_completions(completion_items)

    def _handle_textDocument_hover(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_hover")

        if params.error:
            LOGGER.error(params.error)

        contents = params.result["contents"]["value"]
        kind = params.result["contents"]["kind"]
        start = params.result["range"]["start"]
        location = (start["line"], start["character"])

        LOGGER.debug("markup kind: %s", kind)

        VIEW_COMMAND.show_popup(contents, location)

    def _handle_textDocument_formatting(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_formatting")

        changes = params.result
        VIEW_COMMAND.apply_document_change(changes)

    def _handle_textDocument_semanticTokens_full(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_semanticTokens_full")
        LOGGER.debug(params)

    def _handle_textDocument_documentLink(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_documentLink")
        LOGGER.debug(params)

    def _handle_textDocument_documentSymbol(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_documentSymbol")
        LOGGER.debug(params)

    def _handle_textDocument_codeAction(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_codeAction")
        LOGGER.debug(params)
        # {
        #     "id": 5,
        #     "result": [
        #         {
        #             "arguments": [
        #                 {
        #                     "file": "file:///C:/Users/ginanjar/cproject/cpptools/main.cpp",
        #                     "selection": {
        #                         "end": {"character": 39, "line": 8},
        #                         "start": {"character": 39, "line": 8},
        #                     },
        #                     "tweakID": "AddUsing",
        #                 }
        #             ],
        #             "command": "clangd.applyTweak",
        #             "title": "Add using-declaration for endl and remove qualifier",
        #         }
        #     ],
        #     "jsonrpc": "2.0",
        # }
        VIEW_COMMAND.show_code_action(params.result)

    def _textDocument_publishDiagnostics(self, params: dict):
        LOGGER.info("_textDocument_publishDiagnostics")

        diagnostics = params["diagnostics"]
        LOGGER.debug(diagnostics)
        if not diagnostics:
            VIEW_COMMAND.clear_diagnostics()
            return

        file_name = DocumentURI(params["uri"]).to_path()
        VIEW_COMMAND.open_file(file_name, focus_view=False)
        VIEW_COMMAND.apply_diagnostics(file_name, diagnostics)

    def _window_workDoneProgress_create(self, params):
        LOGGER.info("_window_workDoneProgress_create")
        LOGGER.debug(params)

    def _textDocument_clangd_fileStatus(self, params):
        LOGGER.info("_textDocument_clangd_fileStatus")
        LOGGER.debug(params)

    def _S_progress(self, params):
        LOGGER.info("_S_progress")
        LOGGER.debug(params)

    def _apply_multiple_file_changes(self, file_changes: Dict[str, dict]):
        LOGGER.info("_apply_multiple_file_changes")

        if not file_changes:
            LOGGER.debug("nothing changed")
            return

        for file_name, text_changes in file_changes.items():
            try:
                while True:
                    LOGGER.debug("try apply changes")
                    if DOCUMENT_CHANGE_SYNC.busy:
                        LOGGER.debug("busy")
                        time.sleep(0.5)
                        continue

                    LOGGER.debug("apply changes to: %s", file_name)
                    DOCUMENT_CHANGE_SYNC.set_busy()
                    VIEW_COMMAND.open_file(DocumentURI(file_name).to_path())
                    VIEW_COMMAND.apply_document_change(text_changes)
                    LOGGER.debug("go to break")
                    break

            except Exception as err:
                LOGGER.error(err)

            finally:
                DOCUMENT_CHANGE_SYNC.set_finished()

            LOGGER.debug("finish apply to: %s", file_name)

    def _workspace_applyEdit(self, params):
        LOGGER.info("_workspace_applyEdit")

        if not params:
            return

        try:
            changes = params["edit"]["changes"]

        except Exception as err:
            LOGGER.error(repr(err))
            return

        self._apply_multiple_file_changes(changes)

    def _textDocument_prepareRename(self, params: context.ResponseMessage):
        LOGGER.info("_textDocument_prepareRename")
        LOGGER.debug("params: %s", params)

        view: sublime.View = VIEW_COMMAND.view
        start = params.result["start"]
        end = params.result["end"]
        placeholder = view.substr(
            sublime.Region(
                view.text_point(start["line"], start["character"]),
                view.text_point(end["line"], end["character"]),
            )
        )

        VIEW_COMMAND.input_rename(start["line"], start["character"], placeholder)

    def _textDocument_rename(self, params: context.ResponseMessage):
        LOGGER.info("textDocument_rename")
        LOGGER.debug("params: %s", params)

        try:
            changes = params.result["changes"]
        except Exception as err:
            LOGGER.error(repr(err))
            return

        self._apply_multiple_file_changes(changes)

    def _textDocument_definition(self, params: context.ResponseMessage):
        LOGGER.info("textDocument_definition")
        LOGGER.debug("params: %s", params)
        # {
        #     "id": 30,
        #     "jsonrpc": "2.0",
        #     "result": [
        #         {
        #             "range": {
        #                 "end": {"character": 9, "line": 0},
        #                 "start": {"character": 4, "line": 0},
        #             },
        #             "uri": "file:///C:/Users/ginanjar/cproject/cpptools/meteor.cpp",
        #         }
        #     ],
        # }
        VIEW_COMMAND.goto(params.result)

    def _textDocument_declaration(self, params: context.ResponseMessage):
        LOGGER.info("textDocument_declaration")
        LOGGER.debug("params: %s", params)

        # {
        #     "id": 42,
        #     "jsonrpc": "2.0",
        #     "result": [
        #         {
        #             "range": {
        #                 "end": {"character": 25, "line": 0},
        #                 "start": {"character": 21, "line": 0},
        #             },
        #             "uri": "file:///C:/Users/ginanjar/cproject/cpptools/meteor.cpp",
        #         }
        #     ],
        # }
        VIEW_COMMAND.goto(params.result)

    def _register_commands(self):
        self.transport.register_command("initialize", self._handle_initialize)
        self.transport.register_command(
            "textDocument/publishDiagnostics", self._textDocument_publishDiagnostics
        )
        self.transport.register_command(
            "window/workDoneProgress/create", self._window_workDoneProgress_create
        )
        self.transport.register_command(
            "textDocument/clangd.fileStatus", self._textDocument_clangd_fileStatus
        )
        self.transport.register_command(
            "textDocument/documentLink", self._handle_textDocument_documentLink
        )
        self.transport.register_command(
            "textDocument/hover", self._handle_textDocument_hover
        )
        self.transport.register_command(
            "textDocument/completion", self._handle_textDocument_completion
        )
        self.transport.register_command(
            "textDocument/formatting", self._handle_textDocument_formatting
        )
        self.transport.register_command(
            "textDocument/documentSymbol", self._handle_textDocument_documentSymbol
        )
        self.transport.register_command(
            "textDocument/codeAction", self._handle_textDocument_codeAction
        )
        self.transport.register_command("$/progress", self._S_progress)
        self.transport.register_command(
            "textDocument/semanticTokens/full",
            self._handle_textDocument_semanticTokens_full,
        )
        self.transport.register_command(
            "workspace/applyEdit", self._workspace_applyEdit
        )
        self.transport.register_command(
            "textDocument/prepareRename", self._textDocument_prepareRename
        )
        self.transport.register_command(
            "textDocument/rename", self._textDocument_rename
        )
        self.transport.register_command(
            "textDocument/declaration", self._textDocument_declaration
        )
        self.transport.register_command(
            "textDocument/definition", self._textDocument_definition
        )


class ClientContext(LSPClientListener):
    """ClientContext"""

    def __init__(self, process_cmd: list):
        super().__init__(process_cmd)

        self._request_id = 0
        self._document_version = 0

    def request_id(self):
        self._request_id += 1
        return self._request_id

    def document_version(self):
        self._document_version += 1
        return self._document_version

    def initialize(self, project_path: str):
        """initialize server"""

        LOGGER.info("_cmd_initialize")

        params = {
            "capabilities": {
                "textDocument": {
                    "hover": {
                        "contentFormat": ["markdown", "plaintext"],
                        "dynamicRegistration": True,
                    },
                }
            }
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "initialize", params)
        )

    def textDocument_didOpen(self, path: str, source: str):
        LOGGER.info("_cmd_textDocument_didOpen")

        # set active file name
        self.active_file = path

        params = {
            "textDocument": {
                "languageId": "cpp",
                "text": source,
                "uri": DocumentURI.from_path(path),
                "version": self.document_version(),
            }
        }
        self.transport.notify(
            context.RequestMessage(None, "textDocument/didOpen", params)
        )

    def textDocument_didChange(self, path: str, changes: dict):
        LOGGER.info("_cmd_textDocument_didChange")

        params = {
            "contentChanges": changes,
            "textDocument": {
                "uri": DocumentURI.from_path(path),
                "version": self.document_version(),
            },
        }
        self._hide_completion(changes[0]["text"])
        self.transport.notify(
            context.RequestMessage(None, "textDocument/didChange", params)
        )

    def textDocument_didClose(self, path: str):
        LOGGER.info("_cmd_textDocument_didClose")

        params = {"textDocument": {"uri": DocumentURI.from_path(path)}}
        self.transport.notify(
            context.RequestMessage(None, "textDocument/didClose", params)
        )

    def textDocument_didSave(self, path: str):
        LOGGER.info("_cmd_textDocument_didSave")

        params = {"textDocument": {"uri": DocumentURI.from_path(path)}}
        self.transport.notify(
            context.RequestMessage(None, "textDocument/didSave", params)
        )

    def textDocument_completion(self, path: str, row: int, col: int):
        LOGGER.info("_cmd_textDocument_completion")

        params = {
            "context": {"triggerKind": 1},  # TODO: adapt KIND
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/completion", params)
        )

    def textDocument_hover(self, path: str, row: int, col: int):
        LOGGER.info("_cmd_textDocument_hover")
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/hover", params)
        )

    def textDocument_formatting(self, path, tab_size=2):
        LOGGER.info("_cmd_textDocument_formatting")
        params = {
            "options": {"insertSpaces": True, "tabSize": tab_size},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/formatting", params)
        )

    def textDocument_semanticTokens_full(self, path: str):
        LOGGER.info("_cmd_textDocument_semanticTokens_full")
        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "textDocument/semanticTokens/full", params
            )
        )

    def textDocument_documentLink(self, path: str):
        LOGGER.info("_cmd_textDocument_documentLink")
        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "textDocument/documentLink", params
            )
        )

    def textDocument_documentSymbol(self, path: str):
        LOGGER.info("_cmd_textDocument_documentSymbol")
        params = {"textDocument": {"uri": DocumentURI.from_path(path),}}
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "textDocument/documentSymbol", params
            )
        )

    def textDocument_codeAction(
        self, path: str, start_line: int, start_col: int, end_line: int, end_col: int
    ):
        LOGGER.info("_cmd_textDocument_codeAction")
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
            context.RequestMessage(self.request_id(), "textDocument/codeAction", params)
        )

    def workspace_executeCommand(self, params: dict):
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "workspace/executeCommand", params
            )
        )

    def textDocument_prepareRename(self, path, row, col):
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "textDocument/prepareRename", params
            )
        )

    def textDocument_rename(self, path, row, col, new_name):
        params = {
            "newName": new_name,
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/rename", params)
        )

    def textDocument_definition(self, path, row, col):
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/definition", params)
        )

    def textDocument_declaration(self, path, row, col):
        params = {
            "position": {"character": col, "line": row},
            "textDocument": {"uri": DocumentURI.from_path(path)},
        }
        self.transport.request(
            context.RequestMessage(
                self.request_id(), "textDocument/declaration", params
            )
        )


class ClangdClient:
    def __init__(self):
        self.context = None

    def is_initialized(self):
        return bool(self.context)

    def initialize(self, workspace_directory: str, file_name: str, source: str):
        self.context = ClientContext(["clangd"])
        self.context.initialize(workspace_directory)

        self.didOpen(file_name, source)

    def terminate(self):
        if not self.context:
            return

        self.context.shutdown_server()

    def didOpen(self, file_name: str, source: str):
        if not self.context:
            raise Exception("uninitialized")

        VIEW_COMMAND.open_file(file_name)
        self.context.textDocument_didOpen(file_name, source)

    def didClose(self, file_name: str):
        if not self.context:
            raise Exception("uninitialized")

        VIEW_COMMAND.close(file_name)
        self.context.textDocument_didClose(file_name)

    def didSave(self, file_name: str):
        if not self.context:
            raise Exception("uninitialized")

        self.context.textDocument_didSave(file_name)


CLANGD_CLIENT = ClangdClient()


class ClangdTweakCommand(sublime_plugin.TextCommand):
    def run(self, edit, params):
        CLANGD_CLIENT.clangd_applyTweak(params)


def plugin_loaded():
    settigs_basename = "C++.sublime-settings"
    settings: sublime.Settings = sublime.load_settings(settigs_basename)
    settings.set("index_files", False)
    settings.set("show_definitions", False)
    sublime.save_settings(settigs_basename)


def plugin_unloaded():
    CLANGD_CLIENT.terminate()


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


def is_valid_source(file_name: str):
    if not file_name:
        return False

    _, ext = os.path.splitext(file_name)
    if ext in {".c", ".h", ".cpp", ".hpp"}:
        return True

    return False


class EventListener(sublime_plugin.EventListener):
    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> Union[CompletionList, None]:

        if not is_valid_source(view.file_name()):
            return None

        completions = VIEW_COMMAND.get_completion_result()
        if completions:
            return completions

        thread = threading.Thread(
            target=self.on_query_completions_task, args=(view, locations)
        )
        thread.start()

        VIEW_COMMAND.hide_completion()
        return None

    @pipe
    def on_query_completions_task(self, view, locations):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()

        if not CLANGD_CLIENT.is_initialized():
            CLANGD_CLIENT.initialize(get_project_path(file_name), file_name, source)

        row, col = view.rowcol(locations[0])
        CLANGD_CLIENT.context.textDocument_completion(file_name, row, col)

    def on_hover(self, view: sublime.View, point: int, hover_zone: int) -> None:
        if not is_valid_source(view.file_name()):
            return

        if hover_zone != sublime.HOVER_TEXT:
            # LOGGER.debug("currently only support HOVER_TEXT")
            return

        thread = threading.Thread(target=self.on_hover_text_task, args=(view, point))
        thread.start()

    @pipe
    def on_hover_text_task(self, view, point):
        source = view.substr(sublime.Region(0, view.size()))
        file_name = view.file_name()

        if not CLANGD_CLIENT.is_initialized():
            CLANGD_CLIENT.initialize(get_project_path(file_name), file_name, source)

        row, col = view.rowcol(point)
        CLANGD_CLIENT.context.textDocument_hover(file_name, row, col)

    def on_activated_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (is_valid_source(file_name) and CLANGD_CLIENT.is_initialized()):
            return

        source = view.substr(sublime.Region(0, view.size()))
        CLANGD_CLIENT.didOpen(file_name, source)

    def on_close(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (is_valid_source(file_name) and CLANGD_CLIENT.is_initialized()):
            return

        CLANGD_CLIENT.didClose(file_name)

    def on_post_save_async(self, view: sublime.View) -> None:
        file_name = view.file_name()
        if not (is_valid_source(file_name) and CLANGD_CLIENT.is_initialized()):
            return

        CLANGD_CLIENT.didSave(file_name)


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed_async(self, changes: List[sublime.TextChange]):
        LOGGER.info("on_text_changed_async")

        file_name = self.buffer.file_name()

        if not (is_valid_source(file_name) and CLANGD_CLIENT.is_initialized()):
            return

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

        CLANGD_CLIENT.context.textDocument_didChange(file_name, content_changes)


class CtoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("CtoolsDocumentFormattingCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            CLANGD_CLIENT.context.textDocument_formatting(self.view.file_name())

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsCodeActionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("CtoolsDocumentFormattingCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            location = self.view.sel()[0]
            start_row, start_col = self.view.rowcol(location.a)
            end_row, end_col = self.view.rowcol(location.b)
            CLANGD_CLIENT.context.textDocument_codeAction(
                self.view.file_name(), start_row, start_col, end_row, end_col
            )

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsWorkspaceExecCommandCommand(sublime_plugin.TextCommand):
    def run(self, edit, params):
        LOGGER.info("CtoolsWorkspaceExecCommandCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            CLANGD_CLIENT.context.workspace_executeCommand(params)

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, row, col, new_name):
        LOGGER.info("CtoolsRenameCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():

            file_name = self.view.file_name()
            CLANGD_CLIENT.context.textDocument_rename(file_name, row, col, new_name)

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsPrepareRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsPrepareRenameCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            CLANGD_CLIENT.context.textDocument_prepareRename(file_name, row, col)

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsGotoDefinitionCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            CLANGD_CLIENT.context.textDocument_definition(file_name, row, col)

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()


class CtoolsGotoDeclarationCommand(sublime_plugin.TextCommand):
    def run(self, edit, location=None):
        LOGGER.info("CtoolsGotoDeclarationCommand")

        if is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized():
            file_name = self.view.file_name()

            if location is None:
                location = self.view.sel()[0].a

            row, col = self.view.rowcol(location)
            CLANGD_CLIENT.context.textDocument_declaration(file_name, row, col)

    def is_visible(self):
        return is_valid_source(self.view.file_name()) and CLANGD_CLIENT.is_initialized()
