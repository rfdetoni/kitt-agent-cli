from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class KeyBinding:
    action: str
    sequences: tuple[tuple[str, ...], ...]
    description: str
    scope: str = "global"
    command_id: str | None = None
    discoverable: bool = True

    @property
    def keys(self) -> tuple[str, ...]:
        """Compatibility/introspection view that preserves complete shortcut chords."""
        return tuple(" ".join(sequence) for sequence in self.sequences)

    @property
    def label(self) -> str:
        def pretty(sequence: tuple[str, ...]) -> str:
            labels = {
                "c-x": "Ctrl+X",
                "c-p": "Ctrl+P",
                "c-o": "Ctrl+O",
                "c-t": "Ctrl+T",
                "c-c": "Ctrl+C",
                "c-d": "Ctrl+D",
                "c-right": "Ctrl+→",
                "c-left": "Ctrl+←",
                "s-tab": "Shift+Tab",
                "escape": "Esc",
                "enter": "Enter",
                "pageup": "PgUp",
                "pagedown": "PgDn",
            }
            return " ".join(labels.get(key, key.upper() if key.startswith("f") else key) for key in sequence)
        return " / ".join(pretty(sequence) for sequence in self.sequences)


class KeyMap:
    """Runtime source of truth for shortcuts, help labels and palette annotations."""

    def __init__(self) -> None:
        self.bindings: dict[str, KeyBinding] = {}
        self._captured_counter = 0
        self._register_defaults()

    def _register_defaults(self) -> None:
        defaults = (
            KeyBinding("palette", (("c-p",),), "Abrir Command Palette"),
            KeyBinding("new_session", (("c-x", "n"),), "Nova conversa", command_id="new"),
            KeyBinding("toggle_sidebar", (("c-x", "b"),), "Alternar sidebar", command_id="sidebar"),
            KeyBinding("agents", (("c-x", "a"),), "Painel de agentes", command_id="tasks"),
            KeyBinding("help", (("f1",),), "Ajuda", command_id="help"),
            KeyBinding("models", (("f12",),), "Configurar modelos e provedores", command_id="setup_models"),
            KeyBinding("mode", (("f4",), ("c-t",)), "Alternar modo CODE/PLAN/ASK", command_id="mode"),
            KeyBinding("mouse", (("f10",),), "Alternar mouse TUI/seleção nativa", command_id="mouse"),
            KeyBinding("collapse_tool", (("c-o",),), "Expandir/recolher último bloco de ferramenta"),
            KeyBinding("reasoning_up", (("c-right",),), "Aumentar reasoning", command_id="reasoning"),
            KeyBinding("reasoning_down", (("c-left",),), "Reduzir reasoning", command_id="reasoning"),
            KeyBinding("cancel", (("c-c",),), "Cancelar turno/overlay", command_id="cancel"),
        )
        for spec in defaults:
            self.bindings[spec.action] = spec

    def bind(self, key_bindings, action: str, *, filter=None, eager: bool = False):
        spec = self.bindings[action]

        def decorator(handler):
            options = {"eager": eager}
            if filter is not None:
                options["filter"] = filter
            for sequence in spec.sequences:
                key_bindings.add(*sequence, **options)(handler)
            return handler

        return decorator

    def capture(self, key_bindings) -> None:
        """Capture every contextual binding so no active shortcut is invisible to KeyMap."""
        known = {sequence for spec in self.bindings.values() for sequence in spec.sequences}
        for binding in key_bindings.bindings:
            sequence = tuple(
                getattr(key, "value", str(key)).lower().replace("keys.", "")
                for key in binding.keys
            )
            if not sequence or sequence in known:
                continue
            self._captured_counter += 1
            action = f"context_{self._captured_counter}"
            self.bindings[action] = KeyBinding(
                action=action,
                sequences=(sequence,),
                description="Atalho contextual",
                scope="context",
                discoverable=False,
            )
            known.add(sequence)

    def label_for_command(self, command_id: str) -> str:
        labels = [
            spec.label
            for spec in self.bindings.values()
            if spec.command_id == command_id and spec.discoverable
        ]
        return " · ".join(labels)

    def get_help_list(self, *, include_contextual: bool = False) -> list[tuple[str, str, str]]:
        return [
            (spec.action, spec.label, spec.description)
            for spec in self.bindings.values()
            if spec.discoverable or include_contextual
        ]

    def __len__(self) -> int:
        return len(self.bindings)
