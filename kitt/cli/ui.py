from typing import List

try:
    from prompt_toolkit import prompt
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.styles import Style
    HAS_PROMPT_TOOLKIT = True

    PROMPT_STYLE = Style.from_dict({
        'completion-menu': 'bg:default fg:#8a8a8a',
        'completion-menu.completion': 'bg:default fg:#8a8a8a',
        'completion-menu.meta.completion': 'bg:default fg:#8a8a8a',
        'completion-menu.completion.current': 'bg:#a4b5fd fg:#000000 bold',
        'completion-menu.meta.completion.current': 'bg:#a4b5fd fg:#000000',
        'scrollbar.background': 'bg:default',
        'scrollbar.button': 'bg:#8a8a8a',
    })
except ImportError:
    HAS_PROMPT_TOOLKIT = False

def prompt_dropdown(message: str, options: List[str], default: str = "") -> str:
    """Prompt user with a dropdown autocomplete menu inline."""
    if not HAS_PROMPT_TOOLKIT:
        try:
            from kitt.cli.menu_selector import InteractiveMenuSelector
            items = [(opt, "") for opt in options]
            sel = InteractiveMenuSelector.select(message, items)
            return sel or default
        except Exception:
            return input(message).strip() or default

    from prompt_toolkit.application.current import get_app

    def pre_run():
        app = get_app()
        b = app.current_buffer
        if b.complete_state is None:
            b.start_completion(select_first=False)

    completer = WordCompleter(options, ignore_case=True, match_middle=True)
    try:
        res = prompt(
            message,
            completer=completer,
            complete_while_typing=True,
            reserve_space_for_menu=5,
            style=PROMPT_STYLE,
            pre_run=pre_run
        )
        return res.strip() or default
    except (EOFError, KeyboardInterrupt):
        return default
