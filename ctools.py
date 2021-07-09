import sublime
import sublime_plugin

KEYWORDS = (
    "auto",
    "break",
    "case",
    "char",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extern",
    "float",
    "for",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "register",
    "restrict",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "struct",
    "switch",
    "typedef",
    "union",
    "unsigned",
    "void",
    "volatile",
    "while",
    "_Alignas",
    "_Alignof",
    "_Atomic",
    "_Bool",
    "_Complex",
    "_Generic",
    "_Imaginary",
    "_Noreturn",
    "_Static_assert",
    "_Thread_local",
)


class Completions:
    """completion result structure"""

    def __init__(self):
        self.completions = KEYWORDS

    def build_completion(self):
        for item in KEYWORDS:
            yield sublime.CompletionItem(trigger=item, kind=sublime.KIND_KEYWORD)

    def to_sublime(self):
        return list(self.build_completion())


def valid_source(view: sublime.View, location: int = 0):
    """check if valid c source"""
    return view.match_selector(location, "source.c")


class Event(sublime_plugin.ViewEventListener):
    """Event handler"""

    def __init__(self, view: sublime.View):
        self.view = view

    def on_query_completions(self, prefix: str, locations):
        if not valid_source(self.view):
            return

        return Completions().to_sublime()

    # def on_hover(self, point: int, hover_zone):
    #     pass

    # def on_modified(self):
    #     pass

    # def on_activated(self):
    #     pass

    # def on_pre_close(self):
    #     pass

    # def on_pre_save_async(self) -> None:
    #     pass

    # def on_post_save_async(self) -> None:
    #     pass
