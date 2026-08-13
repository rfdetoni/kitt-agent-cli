import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Theme:
    name: str = "K.I.T.T. Knight Rider"
    background: str = "#08090B"
    surface: str = "#111318"
    surface_raised: str = "#181B20"
    primary: str = "#E31B23"        # KITT Red
    primary_bright: str = "#FF2A2A" # Active Scanner
    secondary: str = "#FFB000"      # Amber Warning / User
    accent: str = "#4CC9F0"         # Cyan Context
    text: str = "#E6E6E6"
    text_muted: str = "#7E858F"
    border: str = "#30343B"
    success: str = "#55D187"
    warning: str = "#FFB000"
    error: str = "#FF4D57"

    def style_dict(self) -> dict[str, str]:
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            return {name: "" for name in (
                "background surface surface.raised primary primary.bright secondary accent text text.muted border "
                "success warning error user assistant tool status status.busy selection scrollbar"
            ).split()}
        return {
            "background": f"bg:{self.background} {self.text}", "surface": f"bg:{self.surface} {self.text}",
            "surface.raised": f"bg:{self.surface_raised} {self.text}", "primary": self.primary,
            "primary.bright": f"{self.primary_bright} bold", "secondary": self.secondary,
            "accent": self.accent, "text": self.text, "text.muted": self.text_muted,
            "border": self.border, "success": self.success, "warning": self.warning, "error": self.error,
            "user": self.secondary, "assistant": self.text, "tool": self.accent,
            "status": f"bg:{self.surface_raised} {self.text_muted}",
            "status.busy": f"bg:{self.surface_raised} {self.primary_bright} bold",
            "selection": f"bg:{self.primary} {self.text}", "scrollbar": f"bg:{self.border} {self.text_muted}",
        }

    def prompt_toolkit_style(self):
        from prompt_toolkit.styles import Style
        return Style.from_dict(self.style_dict())

    @staticmethod
    def _ansi_rgb(hex_color: str) -> str:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        return f"\033[38;2;{r};{g};{b}m"

    def format_error(self, text: str) -> str:
        return f"{self._ansi_rgb(self.error)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def format_success(self, text: str) -> str:
        return f"{self._ansi_rgb(self.success)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def format_warning(self, text: str) -> str:
        return f"{self._ansi_rgb(self.warning)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def format_primary(self, text: str) -> str:
        return f"{self._ansi_rgb(self.primary)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def format_secondary(self, text: str) -> str:
        return f"{self._ansi_rgb(self.secondary)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def format_muted(self, text: str) -> str:
        return f"{self._ansi_rgb(self.text_muted)}{text}\033[0m" if not os.environ.get("NO_COLOR") else text

    def scanner_frame(self, step: int, width: int = 16) -> str:
        pos = step % (width * 2 - 2)
        if pos >= width:
            pos = (width * 2 - 2) - pos
        chars = [" "] * width
        chars[pos] = "█"
        if pos > 0:
            chars[pos - 1] = "▓"
        if pos < width - 1:
            chars[pos + 1] = "▓"
        return "".join(chars)

DEFAULT_THEME = Theme()
