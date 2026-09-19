"""Compatibility facade for the canonical completion guard.

The implementation now lives in :mod:`kitt.core.completion_guard` so runtime
composition has one guard brain and one source of truth.
"""
from kitt.core.completion_guard import _ProgressAwareExecutionLedger, install_completion_guard

__all__ = ["_ProgressAwareExecutionLedger", "install_completion_guard"]
