"""this module handle commands to Sublime Text"""

import logging
import os
import queue
import re
import threading

# from functools import wraps
from urllib.request import pathname2url, url2pathname
from typing import List, Iterable, Union

import sublime
import sublime_plugin

from .plugin import context

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)


# completion
COMPLETION_QUEUE = queue.Queue()

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


def show_completion(view: sublime.View):
    """show completion"""

    view.run_command(
        "auto_complete",
        {
            "disable_auto_insert": True,
            "next_completion_if_showing": False,
            "auto_complete_commit_on_tab": True,
        },
    )


def hide_completion(view: sublime.View):
    """hide completion"""
    view.run_command("hide_auto_complete")


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


class CtoolsSetTaskProgress(sublime_plugin.TextCommand):
    """progess status message"""

    def run(self, edit: sublime.Edit, progress: int = -1):
        LOGGER.info("CtoolsSetTaskProgress")

        view: sublime.View = self.view
        if progress == -1:
            view.set_status("CTOOLS", "ctools: ready ")

        elif -1 < progress <= 100:
            status = ("=" * int(progress / 10)).ljust(10, "-")
            view.set_status("CTOOLS", "ctools: [%s] " % status)

        else:
            raise ValueError(
                "invalid progress range, want -1 < progress <= 100, expected %d"
                % progress
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

    def set_diagnostics(self, diagnostics: List[DiagnosticItem]):
        """set diagnostic"""

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

        SyntaxHighlight(self.view, key=self.region_key[1], regions=error_region).apply()
        SyntaxHighlight(
            self.view, key=self.region_key[2], regions=warning_region
        ).apply()
        SyntaxHighlight(
            self.view, key=self.region_key[3], regions=information_region
        ).apply()
        SyntaxHighlight(self.view, key=self.region_key[4], regions=hint_region).apply()

    @staticmethod
    def erase_highlight(view: sublime.View):
        """erase highlight"""
        for _, value in Diagnostics.region_key.items():
            view.erase_regions(value)


# Singleton of diagnostics
_DIAGNOSTICS = None


class CtoolsApplyDiagnosticsCommand(sublime_plugin.TextCommand):
    """apply diagnostic"""

    def run(self, edit: sublime.Edit, diagnostics: list):
        LOGGER.info("CtoolsApplyDiagnosticsCommand")

        global _DIAGNOSTICS
        _DIAGNOSTICS = Diagnostics(self.view)
        _DIAGNOSTICS.erase_highlight(self.view)
        _DIAGNOSTICS.set_diagnostics(diagnostics)


class CtoolsClearDiagnosticsCommand(sublime_plugin.TextCommand):
    """clear diagnostic"""

    def run(self, edit: sublime.Edit):
        LOGGER.info("CtoolsClearDiagnosticsCommand")

        global _DIAGNOSTICS
        if _DIAGNOSTICS:
            _DIAGNOSTICS.erase_highlight(self.view)
            _DIAGNOSTICS = None


class DocumentURI(str):
    """document uri"""

    @classmethod
    def from_path(cls, file_name):
        """from file name"""
        return cls("file:%s" % pathname2url(file_name))

    def to_path(self) -> str:
        """convert to path"""
        return url2pathname(self.lstrip("file:"))


class CompletionList(list):
    """CompletionList"""

    @classmethod
    def from_rpc(cls, completion_items: List[dict]):
        """load from rpc"""

        completions = [
            sublime.CompletionItem(
                trigger=completion["filterText"],
                annotation=completion["label"],
                completion=completion["insertText"],
                completion_format=sublime.COMPLETION_FORMAT_SNIPPET,
                kind=_KIND_MAP.get(completion["kind"], sublime.KIND_AMBIGUOUS),
            )
            for completion in completion_items
        ]

        return cls(completions)


_VIEW: sublime.View = None


def get_view(file_name: str = ""):
    """get view"""

    if _VIEW and _VIEW.file_name() == file_name:
        return _VIEW

    window: sublime.Window = sublime.active_window()
    if os.path.isfile(file_name):
        return window.open_file(file_name)

    return window.active_view()


class ViewCommand:
    """execute command to client view"""

    def __init__(self, view: sublime.View):
        self.view = view

    @classmethod
    def from_path(cls, file_name: str):
        """view from file path"""
        return cls(get_view(file_name))

    @classmethod
    def from_active_view(cls):
        """from active view"""
        return cls(get_view())

    def show_completion(self, completion_items: List[dict]):
        """show completion"""

        completions = CompletionList.from_rpc(completion_items)
        LOGGER.debug("completions: %s", completions)

        # show
        if completions:
            COMPLETION_QUEUE.put_nowait(completions)
            show_completion(self.view)

    def show_popup(
        self,
        content: str,
        location: Union[int, Iterable[int]],
        *,
        markup_kind: str = "plaintext",
    ):
        """show popup"""
        LOGGER.info("show_popup")

        if isinstance(location, Iterable):
            location = self.view.text_point(location[0], location[1])

        LOGGER.debug(location)
        self.view.run_command(
            "ctools_markdown_popup", {"content": content, "location": location}
        )

    def apply_document_change(self, changes: dict):
        """apply document change"""
        self.view.run_command("ctools_apply_document_change", {"changes": changes})

    def apply_diagnostics(self, diagnostics: dict):
        """apply diagnostics"""
        self.view.run_command("ctools_apply_diagnostics", {"diagnostics": diagnostics})


class ClientHandler:
    """ClientHandler"""

    def __init__(self, process_cmd: list):

        LOGGER.debug("server process cmd: %s", process_cmd)
        self.transport: context.Transport = context.StandardIO(process_cmd)
        self._register_commands()

        self.active_file = ""
        self.completion_commit_character = []

    def _hide_completion(self, character: str):
        LOGGER.info("_hide_completion")
        if character in self.completion_commit_character:
            view_command = ViewCommand.from_path(self.active_file)
            hide_completion(view_command.view)

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
        view_command = ViewCommand.from_path(self.active_file)
        view_command.show_completion(completion_items)

    def _handle_textDocument_hover(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_hover")

        if params.error:
            LOGGER.error(params.error)

        contents = params.result["contents"]["value"]
        kind = params.result["contents"]["kind"]
        start = params.result["range"]["start"]
        location = (start["line"], start["character"])

        LOGGER.debug("markup kind: %s", kind)

        view_command = ViewCommand.from_path(self.active_file)
        view_command.show_popup(contents, location, markup_kind=kind)

    def _handle_textDocument_formatting(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_formatting")

        changes = params.result
        view_command = ViewCommand.from_path(self.active_file)
        view_command.apply_document_change(changes)

    def _handle_textDocument_semanticTokens_full(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_semanticTokens_full")
        print(params)

    def _handle_textDocument_documentLink(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_documentLink")
        print(params)

    def _handle_textDocument_documentSymbol(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_documentSymbol")
        print(params)

    def _handle_textDocument_codeAction(self, params: context.ResponseMessage):
        LOGGER.info("_handle_textDocument_codeAction")
        print(params)

    def _textDocument_publishDiagnostics(self, params: dict):
        LOGGER.info("_textDocument_publishDiagnostics")

        LOGGER.debug(params)
        diagnostics = params["diagnostics"]
        file_name = DocumentURI(params["uri"]).to_path()
        view_command = ViewCommand.from_path(file_name)
        view_command.apply_diagnostics(diagnostics)

    def _window_workDoneProgress_create(self, params):
        LOGGER.info("_window_workDoneProgress_create")
        print(params)

    def _textDocument_clangd_fileStatus(self, params):
        LOGGER.info("_textDocument_clangd_fileStatus")
        print(params)

    def _S_progress(self, params):
        LOGGER.info("_S_progress")
        print(params)

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


class ClientContext(ClientHandler):
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
        self.transport.request(
            context.RequestMessage(self.request_id(), "textDocument/codeAction", params)
        )


class Clangd:
    """This class define server state"""

    def __init__(self):
        self.client_ctx: ClientContext = None

        self.active_workspace = ""
        self.active_file = ""

    def shutdown_server(self):
        if self.client_ctx:
            self.client_ctx.shutdown_server()

    def is_initialized(self) -> bool:

        # client context is None if not initialized
        return bool(self.client_ctx)

    def initialize(self, workspace_folder, active_file, source=""):
        LOGGER.debug(
            "initialize, workspace_folder: %s, active_file: %s",
            workspace_folder,
            active_file,
        )

        if not self.client_ctx:
            self.client_ctx = ClientContext(
                [
                    "clangd",
                    "-log=verbose",
                    "--offset-encoding=utf-8",
                    r"--query-driver=C:\TDM-GCC-64\bin\gcc.exe",
                ]
            )

        self.active_workspace = workspace_folder
        self.client_ctx.initialize(self.active_workspace)

        self.open_file(active_file, source)
        # self.document_link(active_file)

    def change_workspace_directory(self, path):
        LOGGER.info("change_workspace_directory")

        if self.active_workspace != path:
            self.active_workspace = path
            # FIXME: continue

    def open_file(self, file_name, source=""):
        LOGGER.info("open file")

        LOGGER.debug("file name: %s, source: %s", file_name, source)
        if self.active_file != file_name:
            self.active_file = file_name
            if not source:
                with open(file_name) as file:
                    source = file.read()

            self.client_ctx.textDocument_didOpen(file_name, source)

    def document_change(self, path, changes: dict):
        LOGGER.info("document_change")

        if self.active_file != path:
            LOGGER.debug("not edit document")
            return

        LOGGER.debug("path: %s, changes: %s", path, changes)
        self.client_ctx.textDocument_didChange(path, changes)

    def document_link(self, file_name):
        LOGGER.info("document_link")

        LOGGER.debug("file_name: %s", file_name)
        self.client_ctx.textDocument_documentLink(file_name)

    def hover(self, file_name, row, col):
        LOGGER.info("hover")

        LOGGER.debug("file_name: %s, row: %s, col: %s", file_name, row, col)
        self.client_ctx.textDocument_hover(file_name, row, col)

    def completion(self, file_name, row, col):
        LOGGER.info("completion")

        LOGGER.debug("file_name: %s, row: %s, col: %s", file_name, row, col)
        self.client_ctx.textDocument_completion(file_name, row, col)

    def document_formatting(self, file_name):
        LOGGER.info("document_formatting")

        LOGGER.debug("file_name: %s", file_name)
        self.client_ctx.textDocument_formatting(file_name)


CLIENT = Clangd()


def plugin_loaded():
    """on plugin loaded"""
    pass


def plugin_unloaded():
    """on plugin unloaded"""
    if CLIENT.is_initialized():
        CLIENT.shutdown_server()


def workspace_folder(view: sublime.View):
    """get folder of active view"""

    file_name: str = view.file_name()
    if not file_name:
        raise ValueError("unable get active view file name")

    window: sublime.Window = view.window()
    folders = [folder for folder in window.folders() if file_name.startswith(folder)]
    if folders:
        return max(folders)
    return os.path.dirname(file_name)


# FIXME: CURRENTLY ONLY HANDLE CPP
class EventListener(sublime_plugin.EventListener):
    """Handle event emitted by view and window"""

    indentifier_pattern = re.compile(r"[a-zA-Z_]\w*")

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ):
        """on_query_completions"""

        if not view.match_selector(0, "source.c++"):
            return None

        try:
            completion = COMPLETION_QUEUE.get_nowait()
        except queue.Empty:
            completion = None

        if completion:
            LOGGER.debug("completion available")
            LOGGER.debug("completion: %s", completion)
            return (
                completion,
                sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS,
            )

        word = view.word(locations[0])
        LOGGER.debug("location: %s", word)

        thread = threading.Thread(target=self.query_completions, args=(view, word.a,))
        thread.start()
        hide_completion(view)

    def query_completions(self, view: sublime.View, location: int):
        LOGGER.info("query_completions")

        file_name = view.file_name()

        if not CLIENT.is_initialized():
            source = view.substr(sublime.Region(0, view.size()))
            CLIENT.initialize(workspace_folder(view), file_name, source)
            return

        row, col = view.rowcol(location)
        CLIENT.completion(file_name, row, col)

    def on_hover(self, view: sublime.View, point: int, hover_zone: int):
        """on hover"""

        if not view.match_selector(0, "source.c++"):
            return

        if hover_zone == sublime.HOVER_TEXT:
            thread = threading.Thread(target=self.hover_text, args=(view, point))
            thread.start()

        if hover_zone == sublime.HOVER_GUTTER:
            LOGGER.info("on HOVER_GUTTER")
            self.hover_gutter(view, point)

    def hover_gutter(self, view: sublime.View, point: int):
        LOGGER.info("on_hover gutter")

        if _DIAGNOSTICS:
            row, _ = view.rowcol(point)
            message = _DIAGNOSTICS.get_message(row)
            LOGGER.debug("diagnostic message: %s", message)
            location = (row, 0)

            ViewCommand(view).show_popup(message, location)

    def hover_text(self, view: sublime.View, point: int):
        LOGGER.info("on_hover text")

        file_name = view.file_name()
        if not CLIENT.is_initialized():
            source = view.substr(sublime.Region(0, view.size()))
            CLIENT.initialize(workspace_folder(view), file_name, source)
            return
        row, col = view.rowcol(point)
        CLIENT.hover(file_name, row, col)


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed_async(self, changes: List[sublime.TextChange]):
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

        if CLIENT.is_initialized():
            CLIENT.document_change(self.buffer.file_name(), content_changes)


class CtoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        LOGGER.info("CtoolsDocumentFormattingCommand")

        if CLIENT:
            file_name = self.view.file_name()
            CLIENT.document_formatting(file_name)
