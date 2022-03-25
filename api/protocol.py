"""lsp protocol"""

from urllib.request import pathname2url, url2pathname


class DocumentURI(str):
    """document uri"""

    @classmethod
    def from_path(cls, file_name):
        """from file name"""
        return cls("file:%s" % pathname2url(file_name))

    def to_path(self) -> str:
        """convert to path"""
        return url2pathname(self.lstrip("file:"))


def initialize_params(**kwargs):
    """initialize params"""
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
    params.update(kwargs)
    return params


def textDocument_didOpen_params(*, file_name: str, source: str, document_version):
    params = {
        "textDocument": {
            "languageId": "cpp",
            "text": source,
            "uri": DocumentURI.from_path(file_name),
            "version": document_version,
        }
    }
    return params


def textDocument_didChange_params(*, file_name: str, changes: dict):
    params = {
        "contentChanges": changes,
        "textDocument": {
            "uri": DocumentURI.from_path(file_name),
            "version": 0,
        },
    }
    return params


def textDocument_didClose_params(*, file_name: str):
    params = {"textDocument": {"uri": DocumentURI.from_path(file_name)}}
    return params


def textDocument_didSave_params(*, file_name: str):
    params = {"textDocument": {"uri": DocumentURI.from_path(file_name)}}
    return params


def textDocument_completion_params(*, file_name: str, row: int, col: int):
    params = {
        "context": {"triggerKind": 1},  # TODO: adapt KIND
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_hover_params(*, file_name: str, row: int, col: int):
    params = {
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_formatting_params(*, file_name, tab_size=2):
    params = {
        "options": {"insertSpaces": True, "tabSize": tab_size},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_semanticTokens_full_params(*, file_name: str):
    params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
    return params


def textDocument_documentLink_params(*, file_name: str):
    params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
    return params


def textDocument_documentSymbol_params(*, file_name: str):
    params = {"textDocument": {"uri": DocumentURI.from_path(file_name),}}
    return params


def textDocument_codeAction_params(
    *, file_name: str, start_line: int, start_col: int, end_line: int, end_col: int
):
    params = {
        "context": {"diagnostics": []},
        "range": {
            "end": {"character": end_col, "line": end_line},
            "start": {"character": start_col, "line": start_line},
        },
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def workspace_executeCommand_params(params: dict):
    return params


def textDocument_prepareRename_params(*, file_name, row, col):
    params = {
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_rename_params(*, file_name, row, col, new_name):
    params = {
        "newName": new_name,
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_definition_params(*, file_name, row, col):
    params = {
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params


def textDocument_declaration_params(*, file_name, row, col):
    params = {
        "position": {"character": col, "line": row},
        "textDocument": {"uri": DocumentURI.from_path(file_name)},
    }
    return params
