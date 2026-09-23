"""Release planning."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "release")
