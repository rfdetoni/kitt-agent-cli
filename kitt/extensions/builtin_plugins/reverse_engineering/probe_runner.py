"""Run hash-pinned, operator-provisioned probes INSIDE a disposable test VM.

Not remotely exposed. The manifest and VM are owned by the lab operator, not the model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import signal
import subprocess
import tempfile
import time

MAX_MANIFEST_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_PROBE_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 15
NAME_RE = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
HEX_RE = re.compile(r"[a-f0-9]{64}\Z")


class ProbeError(ValueError):
    pass


def _relative(path: str) -> Path:
    if not isinstance(path, str) or not path or '\0' in path:
        raise ProbeError('Invalid probe path')
    posix, win = Path(path), PureWindowsPath(path)
    if posix.is_absolute() or win.is_absolute() or win.drive or '..' in posix.parts:
        raise ProbeError('Probe path must be relative')
    return posix


def _pin_file(root: Path, path: str, expected: str) -> Path:
    if not isinstance(expected, str) or not HEX_RE.fullmatch(expected):
        raise ProbeError('Manifest must pin SHA-256')
    rel = _relative(path)
    target = root.joinpath(rel)
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProbeError('Symlinks not permitted')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ProbeError('Outside probe root')
    if not target.is_file() or target.stat().st_size > MAX_PROBE_BYTES:
        raise ProbeError('Invalid probe file')
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ProbeError('Probe binary changed since registration')
    return target


def run_probe(root: Path, manifest_file: Path, name: str, *, wine_binary: Path | None = None) -> dict:
    """Only call from an already isolated VM, never on an uncontrolled host."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ProbeError('Invalid probe name')
    root = root.resolve(strict=True)
    manifest_file = manifest_file.resolve(strict=True)
    if not manifest_file.is_file() or manifest_file.stat().st_size > MAX_MANIFEST_BYTES:
        raise ProbeError('Invalid manifest')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    entry = manifest.get('probes', {}).get(name)
    if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
        raise ProbeError('Probe is not registered in manifest')
    path = _pin_file(root, entry['path'], entry['sha256'])
    t0 = time.monotonic()
    timeout = False
    if wine_binary is not None:
        if os.name != 'posix':
            raise ProbeError("Wine guest execution requires Linux")
        wine_binary = wine_binary.resolve(strict=True)
        if not wine_binary.is_file() or not os.access(wine_binary, os.X_OK):
            raise ProbeError("Wine executable must be installed by the VM operator")
    with tempfile.TemporaryDirectory(prefix="kitt-wine-prefix-") as prefix:
        guest_env = {k: v for k, v in os.environ.items()
                     if k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
        argv = [str(path)]
        if wine_binary is not None:
            argv = [str(wine_binary), str(path)]
            guest_env['WINEPREFIX'] = prefix
            guest_env['WINEDEBUG'] = '-all'
            guest_env['HOME'] = prefix
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                argv, cwd=root, stdin=subprocess.DEVNULL,
                stdout=stdout_file, stderr=stderr_file,
                start_new_session=(os.name == 'posix'), env=guest_env,
            )
            try:
                code = process.wait(timeout=60 if wine_binary is not None else MAX_SECONDS)
            except subprocess.TimeoutExpired:
                code, timeout = None, True
            finally:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait()
            stdout_file.seek(0, os.SEEK_END)
            stdout_size = stdout_file.tell()
            stderr_file.seek(0, os.SEEK_END)
            stderr_size = stderr_file.tell()
            stdout_file.seek(0)
            stderr_file.seek(0)
            output = stdout_file.read(MAX_OUTPUT_BYTES)
            error = stderr_file.read(MAX_OUTPUT_BYTES)
            truncated = stdout_size > MAX_OUTPUT_BYTES or stderr_size > MAX_OUTPUT_BYTES
    # Receipts identify observations, but are not cryptographic attestation of the guest.
    record = {'schema_version': 1, 'probe': name, 'probe_sha256': entry['sha256'],
              'platform': platform.system() + ('+Wine' if wine_binary is not None else ''), 'release': platform.release(),
              'exit_code': code, 'timed_out': timeout, 'truncated': truncated,
              'duration_ms': round((time.monotonic() - t0) * 1000, 2),
              'stdout': output.decode('utf-8', 'replace'),
              'stderr': error.decode('utf-8', 'replace')}
    canonical = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    record['sha256'] = hashlib.sha256(canonical).hexdigest()
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--probe', required=True)
    parser.add_argument('--wine-binary', type=Path, help='Operator-installed Wine binary (Linux VM only)')
    args = parser.parse_args()
    try:
        print(json.dumps(run_probe(args.probe_root, args.manifest, args.probe, wine_binary=args.wine_binary)))
    except (ProbeError, OSError, ValueError) as ex:
        print(json.dumps({'error': str(ex)}))
        raise SystemExit(2) from ex


if __name__ == '__main__':
    main()
