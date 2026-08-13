from __future__ import annotations

import asyncio
import queue
import sys
import threading

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TextDelta, ToolStarted, TurnCompleted, TurnFailed
from kitt.ui.commands import CommandRegistry
from kitt.ui.theme import DEFAULT_THEME


async def _stream_iterator(iterator):
    event_queue: queue.Queue = queue.Queue(maxsize=64)
    sentinel = object()

    def produce():
        try:
            for item in iterator:
                event_queue.put(item)
        finally:
            event_queue.put(sentinel)

    threading.Thread(target=produce, daemon=True).start()
    while True:
        try:
            item = event_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.01)
            continue
        if item is sentinel:
            break
        yield item


def _write_final_response(write, streamed: str, response: str) -> str:
    if not response:
        return streamed
    if not streamed:
        write(response)
    elif response.startswith(streamed):
        write(response[len(streamed):])
    elif response.strip() != streamed.strip():
        write("\n" + response)
    return response


class PlainLineUI:
    """Standard-library fallback. No prompt_toolkit import or alternate screen."""

    def __init__(self, runtime, reason: str | None = None, input_stream=None, output_stream=None):
        self.runtime = runtime
        self.reason = reason
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.commands = CommandRegistry()
        self._shutdown = False

    def _write(self, text: str) -> None:
        self.output.write(text)
        self.output.flush()

    def print_banner(self) -> None:
        title = "K.I.T.T. Agent CLI — SYSTEM ONLINE"
        if getattr(self.output, "isatty", lambda: False)():
            title = DEFAULT_THEME.format_primary(title)
        self._write(title + "\n")
        if self.reason:
            self._write(f"TUI unavailable: {self.reason}. Using plain mode.\n")
        self._write(f"Workspace: {self.runtime.canonical_root}\nType /help or /quit.\n\n")

    async def _readline(self) -> str:
        self._write("kitt>: ")
        return await asyncio.to_thread(self.input.readline)

    async def run_async(self) -> int:
        self.print_banner()
        while not self._shutdown:
            line = await self._readline()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                break
            if text == "/help":
                self._write("\n".join(f"{c.aliases[0]} - {c.description}" for c in self.commands.commands.values()) + "\n")
                continue
            await self.run_turn(text)
        return 0

    async def run_turn(self, prompt: str) -> None:
        conversation = self.runtime.history.get_or_create_active()
        command = TurnCommand(conversation_id=conversation["id"], prompt=prompt, no_history=not self.runtime.config.history_enabled)
        iterator = self.runtime.processor.run_turn(command)
        full_response = ""
        async for event in _stream_iterator(iterator):
            if isinstance(event, TextDelta):
                self._write(event.delta)
                full_response += event.delta
            elif isinstance(event, ToolStarted):
                self._write(f"\n[Tool: {event.tool_name}]\n")
            elif isinstance(event, ApprovalRequired):
                self._write(f"\nApproval required: {event.tool_name} [{event.action_hash[:8]}] (y/N): ")
                answer = (await asyncio.to_thread(self.input.readline)).strip().lower()
                if answer in {"y", "yes"}:
                    grant = self.runtime.approval.issue_grant(
                        event.turn_id, conversation["id"], event.workspace_id, event.action_hash,
                        approval_id=event.approval_request_id,
                    )
                    resumed_iterator = self.runtime.processor.continue_turn(event.turn_id, grant)
                    async for resumed in _stream_iterator(resumed_iterator):
                        if isinstance(resumed, TextDelta):
                            self._write(resumed.delta)
                            full_response += resumed.delta
                        elif isinstance(resumed, TurnCompleted):
                            full_response = _write_final_response(self._write, full_response, resumed.response)
                        elif isinstance(resumed, TurnFailed): self._write(f"\nError: {resumed.error}")
                else:
                    for _ in self.runtime.processor.cancel_turn(event.turn_id, "Approval denied"):
                        pass
            elif isinstance(event, TurnCompleted):
                full_response = _write_final_response(self._write, full_response, event.response)
            elif isinstance(event, TurnFailed):
                self._write(f"\nError: {event.error}")
        self._write("\n")

    async def shutdown(self) -> None:
        self._shutdown = True


class HeadlessUI:
    def __init__(self, runtime, prompt: str, output_stream=None):
        self.runtime, self.prompt, self.output = runtime, prompt, output_stream or sys.stdout

    async def run_async(self) -> int:
        conversation = self.runtime.history.get_or_create_active()
        command = TurnCommand(conversation_id=conversation["id"], prompt=self.prompt, no_history=not self.runtime.config.history_enabled)
        failed = False
        full_response = ""
        iterator = self.runtime.processor.run_turn(command)
        async for event in _stream_iterator(iterator):
            if isinstance(event, TextDelta):
                self.output.write(event.delta); self.output.flush()
                full_response += event.delta
            elif isinstance(event, TurnCompleted):
                full_response = _write_final_response(
                    lambda text: (self.output.write(text), self.output.flush()),
                    full_response,
                    event.response,
                )
            elif isinstance(event, TurnFailed): self.output.write(f"Error: {event.error}\n"); failed = True
            elif isinstance(event, ApprovalRequired): self.output.write("Approval required; rerun interactively.\n"); failed = True
        return 1 if failed else 0

    async def shutdown(self) -> None: pass
