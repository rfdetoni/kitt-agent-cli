"""Minimal hello world plugin entrypoint."""
from __future__ import annotations


def setup(ctx):
    ctx.logger.info("Hello plugin initialized successfully!")

    def on_app_started(payload, hook_ctx):
        ctx.logger.info("App started observed: %s", payload)

    ctx.hooks.register("app.started", on_app_started, priority=10)
    return None
