"""REA-ready toolbox must work as non-root without host HOME or runtime socket."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitt.vm_container import toolbox


def test_full_toolbox_has_private_writable_home_for_rea(tmp_path: Path):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    with (
        patch("kitt.vm_container._runtime", return_value="podman"),
        patch("kitt.vm_container._image_id", return_value="sha256:" + "a" * 64),
        patch("kitt.vm_container.sys.stdin.isatty", return_value=True),
        patch("kitt.vm_container.subprocess.run", side_effect=run),
    ):
        assert toolbox(runtime="podman", profile="full", workspace=tmp_path) == 0

    argv = calls[0]
    assert argv[-2:] == ["sha256:" + "a" * 64, "/bin/sh"]
    assert "HOME=/tmp" in argv
    assert "XDG_CACHE_HOME=/tmp/.cache" in argv
    assert "XDG_CONFIG_HOME=/tmp/.config" in argv
    assert "/tmp:rw,nosuid,nodev,size=1g,mode=1777" in argv
    assert "--userns=keep-id" in argv
    assert "--network" in argv and "none" in argv
    assert "--cap-drop=ALL" in argv
    assert str(tmp_path) in " ".join(argv)
    assert "--privileged" not in argv
    assert "/var/run/docker.sock" not in " ".join(argv)
