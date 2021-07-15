import sublime
import sublime_plugin

C_KEYWORDS = (
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
    "int",
    "long",
    "register",
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
)

C99_KEYWORDS = (
    "inline",  # c99
    "restrict",  # c99
    "_Bool",  # c99
    "_Complex",  # c99
    "_Imaginary",  # c99
)

C11_KEYWORDS = (
    "_Alignas",  # c11
    "_Alignof",  # c11
    "_Atomic",  # c11
    "_Generic",  # c11
    "_Noreturn",  # c11
    "_Static_assert",  # c11
    "_Thread_local",  # c11
)

CPP_KEYWORDS = (
    "and",
    "and_eq",
    "asm",
    "bitand",
    "bitor",
    "bool",
    "catch",
    "class",
    "compl",
    "const_cast",
    "delete",
    "dynamic_cast",
    "explicit",
    "export",
    "false",
    "friend",
    "inline",
    "mutable",
    "namespace",
    "new",
    "not",
    "not_eq",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "reinterpret_cast",
    "static_cast",
    "template",
    "this",
    "throw",
    "true",
    "try",
    "typeid",
    "typename",
    "using",
    "virtual",
    "wchar_t",
    "xor",
    "xor_eq",
)

CPP11_KEYWORDS = (
    "alignas",
    "alignof",
    "char16_t",
    "char32_t",
    "constexpr",
    "decltype",
    "notexcept",
    "nullptr",
    "static_assert",
    "thread_local",
)

PREPROCESSOR_DIRECTIVES = [
    "#include",
    "#define",
    "defined",
    "#undef",
    "#if",
    "#ifdef",
    "#ifndef",
    "#else",
    "#endif",
    "#error",
    "#pragma",
]

PREDEFINED_CONSTANT = [
    "__LINE__",
    "__FILE__",
    "__DATE__",
    "__TIME__",
    "__STDC__",
]


class CType:
    C = "c"
    CPP = "cpp"


class Completions:
    """completion result structure"""

    def __init__(self, ctype=None):
        self.ctype = ctype

    def build_completion(self):

        # return empty list for invalid ctype
        if not self.ctype:
            return []

        # keyword shared both C and C++
        for item in C_KEYWORDS:
            yield sublime.CompletionItem(trigger=item, kind=sublime.KIND_KEYWORD)

        # keyword for C source and header file
        if self.ctype == CType.C:
            for item in C99_KEYWORDS:
                yield sublime.CompletionItem(
                    trigger=item, kind=sublime.KIND_KEYWORD, details="C99"
                )

            for item in C11_KEYWORDS:
                yield sublime.CompletionItem(
                    trigger=item, kind=sublime.KIND_KEYWORD, details="C11"
                )

        # keyword for C++ source and header file
        if self.ctype == CType.CPP:
            for item in CPP_KEYWORDS:
                yield sublime.CompletionItem(trigger=item, kind=sublime.KIND_KEYWORD)

            for item in CPP11_KEYWORDS:
                yield sublime.CompletionItem(
                    trigger=item,
                    completion=item + " ",
                    kind=sublime.KIND_KEYWORD,
                    details="CPP11",
                )

        for item in PREPROCESSOR_DIRECTIVES:
            yield sublime.CompletionItem(
                trigger=item,
                completion=item + " ",
                kind=sublime.KIND_NAVIGATION,
                details="Preprocessor directives",
            )

        for item in PREDEFINED_CONSTANT:
            yield sublime.CompletionItem(
                trigger=item, kind=sublime.KIND_VARIABLE, details="Predefined constant"
            )

    def to_sublime(self):
        def by_trigger(c: sublime.CompletionItem):
            return c.trigger

        completions = list(self.build_completion())
        completions.sort(key=by_trigger)  # short by trigger
        return completions


def source_type(view: sublime.View, location: int = 0):
    """check if valid c source"""

    file_name = view.file_name()
    if not file_name:
        return None

    if file_name.endswith(".h"):
        return CType.C

    if file_name.endswith(".hpp"):
        return CType.CPP

    if view.match_selector(location, "source.c"):
        return CType.C

    if view.match_selector(location, "source.c++"):
        return CType.CPP

    return None


class Event(sublime_plugin.ViewEventListener):
    """Event handler"""

    def __init__(self, view: sublime.View):
        self.view = view

    def on_query_completions(self, prefix: str, locations):
        ctype = source_type(self.view)
        if not ctype:
            return None

        return Completions(ctype).to_sublime()

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
