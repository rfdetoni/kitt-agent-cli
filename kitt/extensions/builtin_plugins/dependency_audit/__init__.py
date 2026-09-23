"""Dependency and lockfile auditing."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "dependency-audit")
