import sublime
import sublime_plugin


C_KEYWORDS = (
    ("auto", ""),
    ("break", ""),
    ("case", ""),
    ("char", ""),
    ("const", ""),
    ("continue", ""),
    ("default", ""),
    ("do", ""),
    ("double", ""),
    ("else", ""),
    ("enum", ""),
    ("extern", ""),
    ("float", ""),
    ("for", ""),
    ("goto", ""),
    ("if", ""),
    ("int", ""),
    ("long", ""),
    ("register", ""),
    ("return", ""),
    ("short", ""),
    ("signed", ""),
    ("sizeof", ""),
    ("static", ""),
    ("struct", ""),
    ("switch", ""),
    ("typedef", ""),
    ("union", ""),
    ("unsigned", ""),
    ("void", ""),
    ("volatile", ""),
    ("while", ""),
    ("inline", "C99"),  # c99
    ("restrict", "C99"),  # c99
    ("_Bool", "C99"),  # c99
    ("_Complex", "C99"),  # c99
    ("_Imaginary", "C99"),  # c99
    ("_Alignas", "C11"),  # c11
    ("_Alignof", "C11"),  # c11
    ("_Atomic", "C11"),  # c11
    ("_Generic", "C11"),  # c11
    ("_Noreturn", "C11"),  # c11
    ("_Static_assert", "C11"),  # c11
    ("_Thread_local", "C11"),  # c11
)


CPP_KEYWORDS = (
    ("alignas", ""),
    ("alignof", ""),
    ("and", ""),
    ("and_eq", ""),
    ("asm", ""),
    ("auto", ""),
    ("bitand", ""),
    ("bitor", ""),
    ("bool", ""),
    ("break", ""),
    ("case", ""),
    ("catch", ""),
    ("char", ""),
    ("char8_t", ""),
    ("char16_t", ""),
    ("char32_t", ""),
    ("class", ""),
    ("compl", ""),
    ("const", ""),
    ("const_cast", ""),
    ("consteval", ""),
    ("constexpr", ""),
    ("continue", ""),
    ("decltype", ""),
    ("default", ""),
    ("delete", ""),
    ("do", ""),
    ("double", ""),
    ("dynamic_cast", ""),
    ("else", ""),
    ("enum", ""),
    ("explicit", ""),
    ("extern", ""),
    ("false", ""),
    ("float", ""),
    ("for", ""),
    ("friend", ""),
    ("goto", ""),
    ("if", ""),
    ("inline", ""),
    ("int", ""),
    ("long", ""),
    ("mutable", ""),
    ("namespace", ""),
    ("new", ""),
    ("noexcept", ""),
    ("not", ""),
    ("not_eq", ""),
    ("nullptr", ""),
    ("operator", ""),
    ("or", ""),
    ("or_eq", ""),
    ("private", ""),
    ("protected", ""),
    ("public", ""),
    ("register", ""),
    ("return", ""),
    ("short", ""),
    ("signed", ""),
    ("sizeof", ""),
    ("static", ""),
    ("static_assert", ""),
    ("static_cast", ""),
    ("struct", ""),
    ("switch", ""),
    ("template", ""),
    ("this", ""),
    ("thread_local", ""),
    ("throw", ""),
    ("true", ""),
    ("try", ""),
    ("typedef", ""),
    ("typeid", ""),
    ("typename", ""),
    ("union", ""),
    ("unsigned", ""),
    ("using", ""),
    ("virtual", ""),
    ("void", ""),
    ("volatile", ""),
    ("wchar_t", ""),
    ("while", ""),
    ("xor", ""),
    ("xor_eq", ""),
    ("concept", "CPP20"),
    ("constinit", "CPP20"),
    ("co_await", "CPP20"),
    ("co_return", "CPP20"),
    ("co_yield", "CPP20"),
    ("export", "CPP20"),
    ("requires", "CPP20"),
)


PREPROCESSOR_DIRECTIVES = [
    "#define",
    "#elif",
    "#else",
    "#endif",
    "#error",
    "#if",
    "#ifdef",
    "#ifndef",
    "#import",
    "#include",
    "#line",
    "#pragma",
    "#undef",
    "#using",
]


PREDEFINED_MACROS = (
    "__cplusplus",
    "__DATE__",
    "__FILE__",
    "__LINE__",
    "__STDC__",
    "__STDC_HOSTED__",
    "__STDC_NO_ATOMICS__",
    "__STDC_NO_COMPLEX__",
    "__STDC_NO_THREADS__",
    "__STDC_NO_VLA__",
    "__STDC_VERSION__",
    "__STDCPP_THREADS__",
    "__TIME__",
)


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

        # keyword for C source and header file
        if self.ctype == CType.C:
            yield from (
                sublime.CompletionItem(
                    trigger=item, kind=sublime.KIND_KEYWORD, details=c_ver
                )
                for (item, c_ver) in C_KEYWORDS
            )

        # keyword for C++ source and header file
        if self.ctype == CType.CPP:
            yield from (
                sublime.CompletionItem(
                    trigger=item, kind=sublime.KIND_KEYWORD, details=cpp_ver,
                )
                for (item, cpp_ver) in CPP_KEYWORDS
            )

        yield from (
            sublime.CompletionItem(
                trigger=item,
                kind=sublime.KIND_NAVIGATION,
                details="Preprocessor directives",
            )
            for item in PREPROCESSOR_DIRECTIVES
        )

        yield from (
            sublime.CompletionItem(
                trigger=item, kind=sublime.KIND_VARIABLE, details="Predefined macros"
            )
            for item in PREDEFINED_MACROS
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
