import os
import sys
import shutil

class TerminalCapabilities:
    @staticmethod
    def is_tty() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    @staticmethod
    def supports_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        return TerminalCapabilities.is_tty()

    @staticmethod
    def get_size() -> tuple[int, int]:
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines

    @staticmethod
    def clear_screen():
        if sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
