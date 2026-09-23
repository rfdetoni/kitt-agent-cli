"""Database migration risk guard."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "migration-guard")
