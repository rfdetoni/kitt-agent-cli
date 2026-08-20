"""KITT-owned native acceleration, memory intelligence and workspace coordination.

This package is a clean-room implementation designed for KITT.  The optional
Rust extension accelerates deterministic hot paths; every public capability has
an in-process Python fallback so KITT remains usable on unsupported platforms.
"""
from .bridge import NativeCodeEngine, NativeEngineStatus
from .runtime import NativeSubsystem

__all__ = ["NativeCodeEngine", "NativeEngineStatus", "NativeSubsystem"]
