"""Independent bounded artifact frames, with raw fallback for incompressible data."""
from __future__ import annotations

import struct

MAX_CHUNK = 64 * 1024
_HEADER = struct.Struct("!BI")


def encode_frame(chunk: bytes, *, compress: bool = True) -> bytes:
    if not isinstance(chunk, bytes) or not 1 <= len(chunk) <= MAX_CHUNK:
        raise ValueError("invalid artifact chunk size")
    if compress:
        from compression import zstd
        encoded = zstd.compress(chunk, level=1)
        if len(encoded) < len(chunk):
            return _HEADER.pack(1, len(chunk)) + encoded
    return _HEADER.pack(0, len(chunk)) + chunk


def decode_frame(frame: bytes) -> bytes:
    if not isinstance(frame, bytes) or not _HEADER.size < len(frame) <= _HEADER.size + MAX_CHUNK:
        raise ValueError("invalid artifact frame size")
    mode, size = _HEADER.unpack_from(frame)
    if not 1 <= size <= MAX_CHUNK:
        raise ValueError("invalid declared chunk size")
    payload = frame[_HEADER.size:]
    if mode == 0:
        if len(payload) != size:
            raise ValueError("artifact frame size mismatch")
        return payload
    if mode != 1 or len(payload) >= size:
        raise ValueError("invalid artifact compression mode or size")
    from compression import zstd
    try:
        info = zstd.get_frame_info(payload)
        if info.decompressed_size != size or info.dictionary_id != 0:
            raise ValueError("invalid compressed artifact frame metadata")
        decoder = zstd.ZstdDecompressor(options={zstd.DecompressionParameter.window_log_max: 16})
        raw = decoder.decompress(payload, max_length=size + 1)
    except zstd.ZstdError as exc:
        raise ValueError("invalid compressed artifact frame") from exc
    if len(raw) != size or not decoder.eof or decoder.unused_data:
        raise ValueError("artifact frame size mismatch or trailing data")
    return raw
