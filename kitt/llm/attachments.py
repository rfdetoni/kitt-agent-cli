from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Iterable

MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 40 * 1024 * 1024

_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ALLOWED_MIME = {
    *_IMAGE_MIME,
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}
_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}
_BINARY_ATTACHMENT_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
}


class AttachmentError(ValueError):
    pass


def is_binary_attachment_path(path: str) -> bool:
    return Path(path).suffix.lower() in _BINARY_ATTACHMENT_SUFFIXES


def _resolve_workspace_file(root: Path, raw_path: str) -> tuple[str, Path]:
    candidate = Path(str(raw_path).strip())
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise AttachmentError(f"Attachment is outside the workspace: {raw_path}") from exc
    if not resolved.is_file():
        raise AttachmentError(f"Attachment does not exist or is not a file: {relative}")
    return relative, resolved


def _mime_for(path: Path) -> str:
    mime = _EXTENSION_MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or ""
    mime = mime.lower()
    if mime not in _ALLOWED_MIME:
        raise AttachmentError(f"Unsupported attachment type: {path.name} ({mime or 'unknown MIME'})")
    return mime


def build_content_parts(root_dir: str | Path, prompt: str, paths: Iterable[str]) -> list[dict[str, Any]]:
    root = Path(root_dir).resolve()
    unique = list(dict.fromkeys(str(path).strip() for path in paths if str(path).strip()))
    if len(unique) > MAX_ATTACHMENTS:
        raise AttachmentError(f"At most {MAX_ATTACHMENTS} attachments are allowed per turn")

    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    total = 0
    for raw_path in unique:
        relative, resolved = _resolve_workspace_file(root, raw_path)
        size = resolved.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {relative}")
        total += size
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentError(f"Attachments exceed {MAX_TOTAL_ATTACHMENT_BYTES} total bytes")

        mime = _mime_for(resolved)
        encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"
        if mime in _IMAGE_MIME:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            parts.append({
                "type": "input_file",
                "filename": resolved.name,
                "file_data": data_url,
            })
    return parts


def attach_to_first_user_message(
    root_dir: str | Path,
    messages: list[dict[str, Any]],
    paths: Iterable[str],
) -> list[dict[str, Any]]:
    """Return a wire-only message copy with attachments on the first user turn.

    Internal KITT history remains text-only; base64 is introduced only at the
    provider boundary so token budgeting, retrieval, persistence and memory are
    not polluted by binary payloads.
    """
    normalized = [dict(message) for message in messages]
    for message in normalized:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        message["content"] = build_content_parts(root_dir, message["content"], paths)
        return normalized
    raise AttachmentError("Cannot attach files because no textual user message is available")
