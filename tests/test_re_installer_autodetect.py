"""Installer RE provisioning decision tested without doing an installation."""
import os
from pathlib import Path
import subprocess


def test_installer_fresh_linux_auto_provisions_and_opt_out(tmp_path):
    installer = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
    start = installer.index("AUTO_RE_TOOLS=0\n")
    end = installer.index("if [[ $PREPARE_SEMANTIC -eq 1 ]];", start)
    snippet = installer[start:end]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\n[ "$1" = info ] && exit "$DOCKER_INFO_EXIT"\nexit 2\n')
    docker.chmod(0o755)
    launcher = bin_dir / "kitt"
    launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$INSTALL_CALLS"\n')
    launcher.chmod(0o755)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
               HOME=str(tmp_path), LAUNCHER=str(launcher),
               INSTALL_CALLS=str(tmp_path / "calls.log"),
               WITH_RE_TOOLS="0", SKIP_RE_TOOLS="0", DOCKER_INFO_EXIT="0")

    def call(**changes):
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", snippet],
                                env=dict(env, **changes), capture_output=True, text=True,
                                check=False, timeout=20)
        assert result.returncode == 0, result.stderr
        return result

    assert "NOT provisioned" not in call().stderr
    assert (tmp_path / "calls.log").read_text().strip() == "re lab images prepare --runtime docker"
    (tmp_path / "calls.log").unlink()
    assert "NOT provisioned" not in call(SKIP_RE_TOOLS="1").stderr
    assert not (tmp_path / "calls.log").exists()
    missing = call(DOCKER_INFO_EXIT="1")
    assert "NOT provisioned" in missing.stderr
    assert "docker info" in missing.stderr
    assert "--with-re-tools" in missing.stderr
    assert not (tmp_path / "calls.log").exists()
    # Explicit provisioning keeps a fail-closed invocation even when Docker is down.
    assert "NOT provisioned" not in call(WITH_RE_TOOLS="1", DOCKER_INFO_EXIT="1").stderr
    assert (tmp_path / "calls.log").exists()
