from __future__ import annotations

from queue import Empty, Full, Queue
from threading import RLock
from typing import Any


_MAX_PROGRESS_EVENTS = 256
_PROGRESS_EVENT_NAMES = {
    "FilterCompleted",
    "ContextResolved",
    "ContextBuildCompleted",
    "BudgetApplied",
    "ModelSelected",
    "ThinkingStarted",
    "ThinkingCompleted",
    "ToolCallProposed",
    "ToolStarted",
    "ToolCompleted",
    "EditApplied",
    "MetricsRecorded",
    "ChildAgentSpawned",
    "ChildAgentProgress",
    "ChildAgentFinished",
}
_CHANNELS: dict[str, Queue[Any]] = {}
_LOCK = RLock()


def open_goal_progress(goal_id: str) -> None:
    with _LOCK:
        _CHANNELS.setdefault(str(goal_id), Queue(maxsize=_MAX_PROGRESS_EVENTS))


def close_goal_progress(goal_id: str) -> None:
    with _LOCK:
        _CHANNELS.pop(str(goal_id), None)


def publish_goal_progress(goal_id: str, event: Any) -> None:
    if event.__class__.__name__ not in _PROGRESS_EVENT_NAMES:
        return
    with _LOCK:
        channel = _CHANNELS.get(str(goal_id))
    if channel is None:
        return
    try:
        channel.put_nowait(event)
    except Full:
        try:
            channel.get_nowait()
        except Empty:
            pass
        try:
            channel.put_nowait(event)
        except Full:
            pass


def drain_goal_progress(goal_id: str) -> list[Any]:
    with _LOCK:
        channel = _CHANNELS.get(str(goal_id))
    if channel is None:
        return []

    events: list[Any] = []
    while len(events) < _MAX_PROGRESS_EVENTS:
        try:
            events.append(channel.get_nowait())
        except Empty:
            break
    return events
