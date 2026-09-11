from __future__ import annotations

from dataclasses import replace
from types import MethodType
from typing import Any, Iterator

from kitt.llm.attachments import (
    AttachmentError,
    attach_to_first_user_message,
    is_binary_attachment_path,
)


def _is_kitt_reverse_proxy(client: Any) -> bool:
    profile = getattr(client, "profile", None)
    backend = str(getattr(profile, "backend", "") or "").strip().lower()
    protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
    return backend in {"kitt-reverse-proxy", "kitt-proxy"} or protocol == "kitt-reverse-proxy"


def _path_key(value: str) -> str:
    return str(value).strip().lstrip("@").replace("\\", "/")


def _retrieval_prompt(prompt: str, attachments: tuple[str, ...]) -> str:
    """Remove explicit attachment references from retrieval-only prompt text."""
    sanitized = str(prompt)
    for path in attachments:
        normalized = _path_key(path)
        if not normalized:
            continue
        sanitized = sanitized.replace(f"@{path}", " ")
        sanitized = sanitized.replace(f"@{normalized}", " ")
    return " ".join(sanitized.split())


def install_attachment_runtime(processor: Any) -> None:
    """Install wire-only attachments without changing KITT's text history model."""
    if getattr(processor, "_attachment_runtime_installed", False):
        return

    original_run = processor.run_turn
    original_build_context = processor._build_context
    original_loop = processor._execute_tool_loop
    original_stream = processor._stream_execution_response
    processor._attachment_paths_by_turn = {}
    processor._attachment_wire_sent = set()

    def run_turn(self, cmd) -> Iterator:
        explicit = set(getattr(cmd, "explicit_files", ()) or ())
        implicit_attachments = {path for path in explicit if is_binary_attachment_path(path)}
        attachments = set(getattr(cmd, "attachments", ()) or ()) | implicit_attachments
        if attachments != set(getattr(cmd, "attachments", ()) or ()) or implicit_attachments:
            cmd = replace(
                cmd,
                explicit_files=explicit - implicit_attachments,
                attachments=attachments,
            )

        if attachments:
            self._attachment_paths_by_turn[cmd.turn_id] = tuple(sorted(attachments))
        try:
            yield from original_run(cmd)
        finally:
            self._attachment_paths_by_turn.pop(cmd.turn_id, None)
            self._attachment_wire_sent.discard(cmd.turn_id)

    def build_context(self, cmd, task, plan, exe_profile, sf_client):
        attachments = tuple(self._attachment_paths_by_turn.get(cmd.turn_id, ()))
        if not attachments:
            return original_build_context(cmd, task, plan, exe_profile, sf_client)

        attachment_keys = {_path_key(path) for path in attachments}
        filtered_paths = [
            path for path in task.paths
            if _path_key(path) not in attachment_keys
        ]
        retrieval_task = replace(task, paths=filtered_paths)
        retrieval_cmd = replace(
            cmd,
            prompt=_retrieval_prompt(cmd.prompt, attachments),
            explicit_files={
                path for path in cmd.explicit_files
                if _path_key(path) not in attachment_keys
            },
        )
        return original_build_context(
            retrieval_cmd,
            retrieval_task,
            plan,
            exe_profile,
            sf_client,
        )

    def execute_tool_loop(
        self,
        cmd,
        request,
        exe_profile,
        exe_client,
        workspace_id,
        security_context,
    ) -> Iterator:
        attachments = tuple(self._attachment_paths_by_turn.get(cmd.turn_id, ()))
        if attachments and not _is_kitt_reverse_proxy(exe_client):
            raise AttachmentError(
                "File attachments currently require a kitt-reverse-proxy execution profile"
            )
        yield from original_loop(
            cmd,
            request,
            exe_profile,
            exe_client,
            workspace_id,
            security_context,
        )

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

    processor.run_turn = MethodType(run_turn, processor)
    processor._build_context = MethodType(build_context, processor)
    processor._execute_tool_loop = MethodType(execute_tool_loop, processor)
    processor._stream_execution_response = MethodType(stream_execution_response, processor)
    processor._attachment_runtime_installed = True


__all__ = ["install_attachment_runtime"]
