import sys
import tty
import termios
from typing import List, Tuple

class InteractiveMenuSelector:
    """Zero-dependency terminal menu selector with arrow key navigation and bold '>' indicator."""

    @staticmethod
    def select(title: str, items: List[Tuple[str, str]], default_index: int = 0) -> str:
        """
        items: List of (command_name, description) tuples.
        Returns selected command_name string or empty string if cancelled.
        """
        if not items or not sys.stdin.isatty():
            return ""

        selected_idx = default_index
        num_items = len(items)

        def render(first_time: bool = False):
            if not first_time:
                # Move cursor back up (num_items + 2) lines to overwrite menu cleanly
                sys.stdout.write(f"\033[{num_items + 2}A")

            sys.stdout.write(f"\r\033[K\033[1;37m{title}\033[0m\r\n")
            for idx, (cmd, desc) in enumerate(items):
                sys.stdout.write("\r\033[K")
                if idx == selected_idx:
                    sys.stdout.write(f" \033[1;31m>\033[0m \033[1;33m{cmd:<15}\033[0m \033[90m-\033[0m \033[1;37m{desc}\033[0m\r\n")
                else:
                    sys.stdout.write(f"   \033[33m{cmd:<15}\033[0m \033[90m-\033[0m \033[37m{desc}\033[0m\r\n")
            sys.stdout.write("\r\033[K\033[90m(Use \033[33m↑/↓\033[90m arrows to navigate, \033[33mEnter\033[90m to select, \033[33mEsc\033[90m to cancel)\033[0m\r\n")
            sys.stdout.flush()

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            # Use cbreak mode instead of raw mode to preserve proper carriage returns (\r)
            tty.setcbreak(fd)
            # Hide cursor during selection
            sys.stdout.write("\033[?25l")
            render(first_time=True)

            while True:
                ch = sys.stdin.read(1)
                if ch in ('\r', '\n'):
                    break
                elif ch == '\x1b':
                    # Read escape sequence or single Esc
                    next1 = sys.stdin.read(1)
                    if next1 == '[':
                        next2 = sys.stdin.read(1)
                        if next2 == 'A':
                            selected_idx = (selected_idx - 1) % num_items
                            render()
                        elif next2 == 'B':
                            selected_idx = (selected_idx + 1) % num_items
                            render()
                    else:
                        selected_idx = -1
                        break
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            # Restore cursor visibility and position
            sys.stdout.write("\033[?25h\r\n")
            sys.stdout.flush()

        if 0 <= selected_idx < num_items:
            return items[selected_idx][0]
        return ""
