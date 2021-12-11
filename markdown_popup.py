"""this module used to show markdown popup

This module use third party commonmark module to parse content

"""

import logging
import os

import sublime
import sublime_plugin

from .third_party.commonmark import commonmark

LOGGER = logging.getLogger(__name__)
# LOGGER.setLevel(logging.DEBUG)  # module logging level
STREAM_HANDLER = logging.StreamHandler()
LOG_TEMPLATE = "%(levelname)s %(asctime)s %(filename)s:%(lineno)s  %(message)s"
STREAM_HANDLER.setFormatter(logging.Formatter(LOG_TEMPLATE))
LOGGER.addHandler(STREAM_HANDLER)

try:
    file_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(file_dir, "asset", "markdown-style.css")) as file:
        STYLE = "<style>\n%s</style>\n" % file.read()

except FileNotFoundError:
    LOGGER.warning("stylesheet not found", exc_info=True)
    STYLE = ""


class CtoolsMarkdownPopupCommand(sublime_plugin.TextCommand):
    """show popup from markdown content"""

    def fix_pre_code(self, source: str):
        """fix unsupport pre tag"""

        is_pre = False
        for line in source.splitlines(keepends=True):
            if "<pre>" in line:
                LOGGER.debug("enter pre")
                is_pre = True

            if "</pre>" in line:
                LOGGER.debug("exit pre")
                is_pre = False

            if is_pre:
                LOGGER.debug(line)
                line = line.replace("\n", "<br />").replace("  ", "&nbsp;&nbsp;")

            yield line

    def run(self, edit: sublime.Edit, content: str, location: int):
        if not content:
            return

        parsed_markdown = commonmark(content, format="html")
        parsed_markdown = "".join(self.fix_pre_code(parsed_markdown))
        content = f"{STYLE}<div class='markdown-body'>\n{parsed_markdown}</div>"
        LOGGER.debug("content:\n%s", content)

        self.view.show_popup(
            content=content,
            location=location,
            max_width=1024,
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
        )
