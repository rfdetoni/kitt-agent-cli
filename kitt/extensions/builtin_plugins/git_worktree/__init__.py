"""Child-agent Git worktree planning."""
from kitt.extensions.builtin_plugins.shared import install


def setup(ctx):
    return install(ctx, "git-worktree")
