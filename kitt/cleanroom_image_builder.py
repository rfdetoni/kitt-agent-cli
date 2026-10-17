"""Offline OCI image builder for independently supplied local models and tools.

Never pulls images or downloads a model. The result is an immutable OCI image ID;
building an image does not establish trusted model provenance or license rights.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from kitt.cleanroom_runner import CleanRoomEnvironmentError

_IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
_MAX_MODEL = 24 * 1024**3
_MAX_LLAMA = 256 * 1024**2


def _verified_file(path: Path, limit: int) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise CleanRoomEnvironmentError("offline build asset must be absolute regular non-symlink file")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise CleanRoomEnvironmentError("offline build asset is empty, oversized or not a file")
    return path.resolve(strict=True)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_image(
    *, base_image: str, model: Path, llama_cli: Path, engine: str = "docker",
    timeout_seconds: int = 1200,
) -> dict:
    if not isinstance(base_image, str) or _IMAGE.fullmatch(base_image) is None:
        raise CleanRoomEnvironmentError("base image must be pre-cached sha256:<digest>")
    if engine not in {"docker", "podman"}:
        raise CleanRoomEnvironmentError("unsupported local OCI engine")
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 3600:
        raise CleanRoomEnvironmentError("invalid bounded image build timeout")
    model = _verified_file(model, _MAX_MODEL)
    llama_cli = _verified_file(llama_cli, _MAX_LLAMA)
    dockerfile = Path(__file__).resolve().parent / "cleanroom_image" / "Dockerfile"
    entrypoint = dockerfile.parent / "implementer.py"
    if not dockerfile.is_file() or not entrypoint.is_file():
        raise CleanRoomEnvironmentError("KITT packaged offline image recipe missing")
    executable = shutil.which(engine)
    if not executable:
        raise CleanRoomEnvironmentError("OCI engine missing")
    prefix = ([executable, "--context", "default"] if engine == "docker"
              else [executable, "--remote=false"])
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()),
           "LANG": "C", "LC_ALL": "C", "DOCKER_BUILDKIT": "0"}
    try:
        inspected = subprocess.run(
            [*prefix, "image", "inspect", "--format", "{{.Id}}", base_image],
            capture_output=True, env=env, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanRoomEnvironmentError("base image inspection failed") from exc
    if inspected.returncode or inspected.stdout.decode("ascii", "replace").strip() != base_image:
        raise CleanRoomEnvironmentError("pinned base image is absent locally")
    with tempfile.TemporaryDirectory(prefix="kitt-cleanroom-build-") as scratch:
        root = Path(scratch)
        root.chmod(0o700)
        shutil.copyfile(dockerfile, root / "Dockerfile")
        shutil.copyfile(entrypoint, root / "implementer.py")
        shutil.copyfile(llama_cli, root / "llama-cli")
        shutil.copyfile(model, root / "implementer.gguf")
        iid = root / "image.id"
        out, err = root / "build.out", root / "build.err"
        with out.open("wb") as stdout, err.open("wb") as stderr:
            try:
                built = subprocess.run(
                    [*prefix, "build", "--pull=false", "--network=none", "--no-cache",
                     "--build-arg", "BASE_IMAGE=" + base_image,
                     "--iidfile", str(iid), "-f", str(root / "Dockerfile"), str(root)],
                    env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    timeout=timeout_seconds, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CleanRoomEnvironmentError(
                    "offline OCI image build failed/timed out; inspect local build processes"
                ) from exc
        if built.returncode or not iid.is_file() or iid.is_symlink():
            raise CleanRoomEnvironmentError("offline OCI image build failed")
        image = iid.read_text("ascii").strip()
        if not _IMAGE.fullmatch(image):
            raise CleanRoomEnvironmentError("OCI build did not produce an immutable ID")
        return {
            "status": "IMAGE_BUILT_NOT_ATTESTED",
            "image": image,
            "base_image": base_image,
            "model_sha256": _hash(model),
            "llama_cli_sha256": _hash(llama_cli),
            "recipe_sha256": _hash(dockerfile),
            "entrypoint_sha256": _hash(entrypoint),
            "network": "none",
            "source_provenance_verified": False,
        }
