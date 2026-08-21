from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TextDelta, TurnCancelled, TurnCompleted, TurnEvent, TurnFailed

_END = object()


class TurnEventBridge:
    """TUI bridge.

    When daemon mode is enabled this object keeps the old public API but routes
    turns through DaemonUIBridge. Local execution remains available only when
    explicitly configured.
    """

    def __init__(self, runtime, on_event: Callable[[TurnEvent], None],
                 invalidate: Callable[[], None], max_queue: int = 128):
        self.runtime = runtime
        self.on_event = on_event
        self.invalidate = invalidate
        self.max_queue = max_queue
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kitt-turn")
        self._queue = queue.Queue(maxsize=max_queue)
        self._consumer = None
        self._producer_future = None
        self._closed = threading.Event()
        self._active_turn_id = None
        self._active_conversation_id = None
        self._no_history = False
        self._accumulated_assistant_text = ""
        self._turn_generation = 0
        self._saved_roles = set()
        self.invalidation_count = 0
        self.event_count = 0
        self._last_invalidate = 0.0
        self._pending_invalidate = None
        self._daemon_bridge = None
        self._daemon_terminal = None

    @property
    def active_turn_id(self):
        return self._active_turn_id

    @property
    def is_active(self):
        if self._daemon_bridge is not None:
            return self._active_turn_id is not None
        return self._consumer is not None and not self._consumer.done()

    @property
    def daemon_mode(self) -> bool:
        return self._daemon_bridge is not None

    def _deliver(self, event):
        self.event_count += 1
        if isinstance(event, TurnCompleted):
            self._persist_assistant_response_if_needed(event.response)
        if event.__class__.__name__ == "TurnStarted" and getattr(event, "turn_id", ""):
            self._active_turn_id = event.turn_id
        self.on_event(event)
        if isinstance(event, (TurnCompleted, TurnFailed, TurnCancelled)) or event.__class__.__name__ == "TurnBlocked":
            self._active_turn_id = None
            if self._daemon_terminal:
                self._daemon_terminal.set()
        self._request_invalidate()

    async def _ensure_daemon(self, conversation_id: str) -> bool:
        config = getattr(self.runtime, "config", None)
        if not getattr(config, "daemon_enabled", False):
            return False
        if self._daemon_bridge is not None:
            if self._daemon_bridge.attached_session_id == conversation_id:
                return True
            if await self._daemon_bridge.attach(conversation_id):
                self._daemon_terminal = asyncio.Event()
                return True
            await self._daemon_bridge.close()
            self._daemon_bridge = None
        from kitt.ui.daemon_bridge import DaemonUIBridge
        bridge = DaemonUIBridge(str(self.runtime.canonical_root), event_sink=self._deliver)
        if not await bridge.connect():
            if not getattr(config, "daemon_auto_start", True):
                return False
            from kitt.daemon.process import start_daemon_detached
            result = await asyncio.to_thread(start_daemon_detached, str(self.runtime.canonical_root))
            if result.get("status") != "ok" or not await bridge.connect():
                if getattr(config, "daemon_local_fallback", False):
                    return False
                raise RuntimeError(result.get("error", "Unable to start/connect KITT daemon"))
        if not await bridge.attach(conversation_id):
            # Session should already exist because the TUI creates it in the shared DB.
            if getattr(config, "daemon_local_fallback", False):
                await bridge.close()
                return False
            raise RuntimeError(f"Daemon refused session attach: {conversation_id}")
        self._daemon_bridge = bridge
        self._daemon_terminal = asyncio.Event()
        # Sync frontend-selected reasoning before the first daemon turn.
        try:
            await self._daemon_bridge.set_reasoning(
                int(getattr(self.runtime.processor, "reasoning_effort", 50))
            )
        except Exception:
            pass
        return True

    async def start(self, prompt, conversation_id, explicit_files=frozenset(),
                    no_history=False, mode="auto"):
        if self._closed.is_set() or self.is_active:
            raise RuntimeError("A turn is already active")
        self._active_conversation_id = conversation_id
        self._no_history = no_history
        self._accumulated_assistant_text = ""

        if await self._ensure_daemon(conversation_id):
            turn_id = await self._daemon_bridge.submit_turn(
                prompt, mode=mode, explicit_files=explicit_files, no_history=no_history
            )
            if not turn_id:
                self._active_turn_id = None
                raise RuntimeError("Daemon rejected turn submission")
            self._active_turn_id = turn_id
            return turn_id

        cmd = TurnCommand(
            conversation_id=conversation_id, prompt=prompt,
            explicit_files=set(explicit_files), no_history=no_history, mode=mode,
        )
        self._active_turn_id = cmd.turn_id
        if not no_history:
            try:
                self.runtime.history.repo.save_message(conversation_id, cmd.turn_id, "user", prompt)
                self._saved_roles.add((cmd.turn_id, "user"))
            except Exception:
                pass
        self._turn_generation += 1
        gen = self._turn_generation
        loop = asyncio.get_running_loop()
        self._consumer = loop.create_task(self._consume(gen))
        self._producer_future = loop.run_in_executor(
            self._executor, self._produce, gen, self.runtime.processor.run_turn(cmd)
        )
        return cmd.turn_id

    async def continue_turn(self, turn_id, grant):
        if self._daemon_bridge:
            ok = await self._daemon_bridge.continue_turn(grant)
            if not ok:
                raise RuntimeError("Daemon rejected approved continuation")
            self._active_turn_id = turn_id
            return
        if self._consumer and not self._consumer.done():
            await self._consumer
        self._turn_generation += 1
        gen = self._turn_generation
        self._active_turn_id = turn_id
        loop = asyncio.get_running_loop()
        self._consumer = loop.create_task(self._consume(gen))
        self._producer_future = loop.run_in_executor(
            self._executor, self._produce, gen,
            self.runtime.processor.continue_turn(turn_id, grant)
        )

    async def ensure_daemon(self, conversation_id: str) -> bool:
        return await self._ensure_daemon(conversation_id)

    async def resolve_approval(self, approval_id: str, allow: bool) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon approval authority is not active")
        return await self._daemon_bridge.approval_action(approval_id, allow)

    async def list_approvals(self) -> list[dict]:
        if not self._daemon_bridge:
            return []
        return await self._daemon_bridge.list_approvals()

    async def remember_approval(self, tool_name: str, scope: str) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon approval authority is not active")
        return await self._daemon_bridge.remember_approval(tool_name, scope)

    async def execute_direct_tool(self, tool_name: str, args: dict) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon tool authority is not active")
        return await self._daemon_bridge.execute_direct_tool(tool_name, args)

    async def undo(self) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon authority is not active")
        return await self._daemon_bridge.undo()

    async def set_reasoning(self, value: int) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon authority is not active")
        return await self._daemon_bridge.set_reasoning(value)

    async def set_autonomy(self, preset: str) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon authority is not active")
        return await self._daemon_bridge.set_autonomy(preset)

    async def reload_router(self) -> dict:
        if not self._daemon_bridge:
            raise RuntimeError("Daemon authority is not active")
        return await self._daemon_bridge.reload_router()

    async def attach_session(self, session_id: str) -> bool:
        if not self._daemon_bridge:
            return await self._ensure_daemon(session_id)
        await self._daemon_bridge.detach()
        return await self._daemon_bridge.attach(session_id)

    async def detach_session(self) -> None:
        if self._daemon_bridge:
            await self._daemon_bridge.detach()

    async def cancel(self, reason="Cancelled by user"):
        turn_id = self._active_turn_id
        if self._daemon_bridge:
            if turn_id:
                await self._daemon_bridge.cancel_turn(turn_id)
            self._active_turn_id = None
            return
        self._turn_generation += 1
        self._active_turn_id = None
        if turn_id:
            for event in self.runtime.processor.cancel_turn(
                turn_id, reason, conversation_id=self._active_conversation_id
            ):
                self._deliver(event)
        else:
            self._deliver(TurnCancelled(reason=reason))
        if self._producer_future and not self._producer_future.done():
            self._producer_future.cancel()
        if self._consumer and not self._consumer.done():
            self._consumer.cancel()
        self._consumer = None
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self.invalidate()

    def _drop_oldest_non_critical(self):
        try:
            item = self._queue.get_nowait()
            if isinstance(item, tuple) and isinstance(item[1], (TurnCompleted, TurnFailed)):
                self._queue.put_nowait(item)
        except Exception:
            pass

    def _produce(self, gen: int, events: Iterable[TurnEvent]):
        try:
            for event in events:
                if self._closed.is_set() or gen != self._turn_generation:
                    break
                try:
                    self._queue.put((gen, event), timeout=5)
                except queue.Full:
                    self._drop_oldest_non_critical()
                    self._queue.put((gen, event), timeout=1)
        except BaseException as exc:
            try:
                self._queue.put((gen, TurnFailed(error=str(exc))), timeout=1)
            except Exception:
                pass
        finally:
            try:
                self._queue.put((gen, _END), timeout=1)
            except Exception:
                pass

    async def _consume(self, gen: int):
        try:
            while True:
                try:
                    item_gen, item = self._queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue
                if item_gen != self._turn_generation:
                    continue
                if item is _END:
                    break
                if isinstance(item, TextDelta):
                    self._accumulated_assistant_text += item.delta
                self._deliver(item)
        finally:
            self._active_turn_id = None

    def _persist_assistant_response_if_needed(self, response=""):
        if self._daemon_bridge or self._no_history or not self._active_turn_id or not self._active_conversation_id:
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

    def _request_invalidate(self):
        now = time.monotonic()
        if now - self._last_invalidate >= 1 / 30:
            self._last_invalidate = now
            self.invalidation_count += 1
            self.invalidate()
        elif self._pending_invalidate is None:
            try:
                delay = 1 / 30 - (now - self._last_invalidate)
                self._pending_invalidate = asyncio.get_running_loop().call_later(delay, self._flush_invalidate)
            except RuntimeError:
                pass

    def _flush_invalidate(self):
        self._pending_invalidate = None
        if not self._closed.is_set():
            self._last_invalidate = time.monotonic()
            self.invalidation_count += 1
            self.invalidate()

    async def shutdown(self, timeout=3.0):
        self._closed.set()
        if self._pending_invalidate:
            self._pending_invalidate.cancel()
        if self._daemon_bridge:
            # Detach only. Never stop daemon/background work on TUI shutdown.
            await self._daemon_bridge.close()
            self._daemon_bridge = None
            self._active_turn_id = None
            self._executor.shutdown(wait=False, cancel_futures=True)
            return
        await self.cancel("UI shutdown")
        self._executor.shutdown(wait=False, cancel_futures=True)
