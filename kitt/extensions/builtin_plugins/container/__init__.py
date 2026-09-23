"""Container and orchestration inspection."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "container")
