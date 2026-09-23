"""Deterministic quality and evidence reporting."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "quality-report")
