from __future__ import annotations

from types import MethodType
from typing import Any, Iterator

from kitt.llm.attachments import AttachmentError, attach_to_first_user_message


def _is_kitt_reverse_proxy(client: Any) -> bool:
    profile = getattr(client, "profile", None)
    backend = str(getattr(profile, "backend", "") or "").strip().lower()
    protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
    return backend in {"kitt-reverse-proxy", "kitt-proxy"} or protocol == "kitt-reverse-proxy"


def install_attachment_runtime(processor: Any) -> None:
    """Install wire-only attachments without changing KITT's text history model."""
    if getattr(processor, "_attachment_runtime_installed", False):
        return

    original_loop = processor._execute_tool_loop
    original_stream = processor._stream_execution_response
    processor._attachment_paths_by_turn = {}
    processor._attachment_wire_sent = set()

    def execute_tool_loop(
        self,
        cmd,
        request,
        exe_profile,
        exe_client,
        workspace_id,
        security_context,
    ) -> Iterator:
        attachments = tuple(sorted(getattr(cmd, "attachments", ()) or ()))
        if attachments and not _is_kitt_reverse_proxy(exe_client):
            raise AttachmentError(
                "File attachments currently require a kitt-reverse-proxy execution profile"
            )
        if attachments:
            self._attachment_paths_by_turn[cmd.turn_id] = attachments
        try:
            yield from original_loop(
                cmd,
                request,
                exe_profile,
                exe_client,
                workspace_id,
                security_context,
            )
        finally:
            self._attachment_paths_by_turn.pop(cmd.turn_id, None)
            self._attachment_wire_sent.discard(cmd.turn_id)

    def stream_execution_response(
        self,
        exe_client,
        messages,
        system_prompt,
        *,
        turn_id,
        started_at,
        session_key=None,
    ):
        wire_messages = messages
        paths = self._attachment_paths_by_turn.get(turn_id)
        if paths and turn_id not in self._attachment_wire_sent:
            wire_messages = attach_to_first_user_message(
                self.root_path,
                list(messages),
                paths,
            )
            self._attachment_wire_sent.add(turn_id)
        yield from original_stream(
            exe_client,
            wire_messages,
            system_prompt,
            turn_id=turn_id,
            started_at=started_at,
            session_key=session_key,
        )

    processor._execute_tool_loop = MethodType(execute_tool_loop, processor)
    processor._stream_execution_response = MethodType(stream_execution_response, processor)
    processor._attachment_runtime_installed = True


__all__ = ["install_attachment_runtime"]
