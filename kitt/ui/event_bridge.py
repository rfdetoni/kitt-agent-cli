from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    TextDelta, TurnCompleted, TurnEvent, TurnFailed,
)

_END = object()


class TurnEventBridge:
    """Bounded sync-iterator bridge; runtime never blocks UI event loop."""

    def __init__(self, runtime, on_event: Callable[[TurnEvent], None], invalidate: Callable[[], None], max_queue: int = 128):
        self.runtime = runtime
        self.on_event = on_event
        self.invalidate = invalidate
        self.max_queue = max_queue
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kitt-turn")
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max_queue)
        self._consumer: asyncio.Task | None = None
        self._producer_future: asyncio.Future | None = None
        self._closed = threading.Event()
        self._active_turn_id: str | None = None
        self._active_conversation_id: str | None = None
        self._no_history: bool = False
        self._accumulated_assistant_text: str = ""
        self._turn_generation: int = 0
        self._saved_roles: set[tuple[str, str]] = set()  # (turn_id, role)
        self.invalidation_count = 0
        self.event_count = 0
        self._last_invalidate = 0.0
        self._pending_invalidate: asyncio.TimerHandle | None = None

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn_id

    @property
    def is_active(self) -> bool:
        return self._consumer is not None and not self._consumer.done()

    def _produce(self, gen: int, events: Iterable[TurnEvent]) -> None:
        try:
            for event in events:
                if self._closed.is_set() or gen != self._turn_generation:
                    break
                self._queue.put((gen, event))
        except BaseException as exc:
            self._queue.put((gen, TurnFailed(error=str(exc))))
        finally:
            self._queue.put((gen, _END))

    async def _consume(self, gen: int) -> None:
        loop = asyncio.get_running_loop()
        pending_delta = ""
        try:
            while True:
                raw = await loop.run_in_executor(None, self._queue.get)
                if not isinstance(raw, tuple) or len(raw) != 2:
                    continue
                item_gen, item = raw
                if item_gen != self._turn_generation:
                    continue
                if item is _END:
                    if pending_delta:
                        self._deliver(TextDelta(delta=pending_delta))
                    break
                if isinstance(item, TextDelta):
                    pending_delta += item.delta
                    self._accumulated_assistant_text += item.delta
                    while len(pending_delta) < 8192:
                        try:
                            following_raw = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(following_raw, tuple) and len(following_raw) == 2:
                            f_gen, following = following_raw
                            if f_gen != self._turn_generation:
                                continue
                            if isinstance(following, TextDelta):
                                pending_delta += following.delta
                                self._accumulated_assistant_text += following.delta
                                continue
                            self._deliver(TextDelta(delta=pending_delta))
                            pending_delta = ""
                            if following is _END:
                                return
                            self._handle_event(following)
                            break
                        break
                    if pending_delta:
                        self._deliver(TextDelta(delta=pending_delta))
                        pending_delta = ""
                else:
                    if pending_delta:
                        self._deliver(TextDelta(delta=pending_delta))
                        pending_delta = ""
                    self._handle_event(item)
        finally:
            self._active_turn_id = None

    def _handle_event(self, event: TurnEvent) -> None:
        if isinstance(event, TurnCompleted):
            self._persist_assistant_response_if_needed(response=event.response)
        self._deliver(event)

    def _persist_assistant_response_if_needed(self, response: str = "") -> None:
        if self._no_history or not self._active_turn_id or not self._active_conversation_id:
            return
        key = (self._active_turn_id, "assistant")
        if key in self._saved_roles:
            return
        text = response.strip() or self._accumulated_assistant_text.strip()
        if text:
            try:
                self.runtime.history.repo.save_message(
                    self._active_conversation_id, self._active_turn_id, "assistant", text
                )
                self._saved_roles.add(key)
            except Exception:
                pass

    def _deliver(self, event: TurnEvent) -> None:
        self.event_count += 1
        self.on_event(event)
        self._request_invalidate()

    def _request_invalidate(self) -> None:
        now = time.monotonic()
        if now - self._last_invalidate >= 1 / 30:
            self._last_invalidate = now
            self.invalidation_count += 1
            self.invalidate()
        elif self._pending_invalidate is None:
            delay = 1 / 30 - (now - self._last_invalidate)
            try:
                self._pending_invalidate = asyncio.get_running_loop().call_later(delay, self._flush_invalidate)
            except RuntimeError:
                pass

    def _flush_invalidate(self) -> None:
        self._pending_invalidate = None
        if self._closed.is_set():
            return
        self._last_invalidate = time.monotonic()
        self.invalidation_count += 1
        self.invalidate()

    async def start(self, prompt: str, conversation_id: str, explicit_files=frozenset(), no_history: bool = False) -> str:
        if self._closed.is_set() or (self._consumer and not self._consumer.done()):
            raise RuntimeError("A turn is already active")
        cmd = TurnCommand(conversation_id=conversation_id, prompt=prompt, explicit_files=set(explicit_files), no_history=no_history)
        self._turn_generation += 1
        gen = self._turn_generation
        self._active_turn_id = cmd.turn_id
        self._active_conversation_id = conversation_id
        self._no_history = no_history
        self._accumulated_assistant_text = ""
        if not no_history:
            try:
                self.runtime.history.repo.save_message(conversation_id, cmd.turn_id, "user", prompt)
                self._saved_roles.add((cmd.turn_id, "user"))
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        self._consumer = loop.create_task(self._consume(gen), name=f"kitt-ui-events-{gen}")
        self._producer_future = loop.run_in_executor(
            self._executor, self._produce, gen, self.runtime.processor.run_turn(cmd)
        )
        return cmd.turn_id

    async def continue_turn(self, turn_id: str, grant) -> None:
        if self._consumer and not self._consumer.done():
            await self._consumer
        self._turn_generation += 1
        gen = self._turn_generation
        self._active_turn_id = turn_id
        loop = asyncio.get_running_loop()
        self._consumer = loop.create_task(self._consume(gen), name=f"kitt-ui-events-{gen}")
        self._producer_future = loop.run_in_executor(
            self._executor, self._produce, gen, self.runtime.processor.continue_turn(turn_id, grant)
        )

    async def cancel(self, reason: str = "Cancelled by user") -> None:
        if self._active_turn_id:
            try:
                events = list(self.runtime.processor.cancel_turn(self._active_turn_id, reason))
                for event in events:
                    self._deliver(event)
            except Exception:
                pass
        self._turn_generation += 1
        self._active_turn_id = None
        if self._consumer and not self._consumer.done():
            self._consumer.cancel()
        self._consumer = None

    async def shutdown(self, timeout: float = 3.0) -> None:
        self._closed.set()
        if self._pending_invalidate:
            self._pending_invalidate.cancel()
            self._pending_invalidate = None
        await self.cancel("UI shutdown")
        if self._consumer and not self._consumer.done():
            try:
                await asyncio.wait_for(self._consumer, timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._consumer.cancel()
        if self._producer_future and not self._producer_future.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._producer_future), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        self._executor.shutdown(wait=True, cancel_futures=True)
