"""Debian bootstrap trust and privilege boundaries."""
import hashlib
import json
from unittest.mock import patch

import pytest

from kitt.vm_debian import IMAGE, VMError, cloud_config, expected_sha512, prepare_debian


PUBLIC = "ssh-ed25519 AAAA fixture"
PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n-----END OPENSSH PRIVATE KEY-----\n"


def test_cloud_config_uses_pinned_host_key_and_root_owned_forced_command():
    cfg = cloud_config(PUBLIC, "a" * 40, host_private=PRIVATE, host_public=PUBLIC)
    value = json.loads(cfg.split("\n", 1)[1])
    assert value["users"] == [{"name": "kitt", "shell": "/bin/bash", "lock_passwd": True, "sudo": False}]
    assert value["ssh_keys"]["ed25519_private"] == PRIVATE
    assert value["ssh_keys"]["ed25519_public"] == "ssh-ed25519 AAAA"
    files = {file["path"]: file for file in value["write_files"]}
    auth = files["/etc/ssh/kitt-authorized-keys"]
    assert auth["owner"] == "root:root"
    assert auth["content"].startswith("restrict ssh-ed25519 ")
    ssh = files["/etc/ssh/sshd_config.d/00-kitt.conf"]["content"]
    assert "ForceCommand /opt/kitt/venv/bin/python -m kitt.vm_worker" in ssh
    assert "DisableForwarding yes" in ssh
    assert "PermitRootLogin no" in ssh
    assert "NOPASSWD" not in cfg
    with pytest.raises(VMError):
        cloud_config(PUBLIC, "main", host_private=PRIVATE, host_public=PUBLIC)


def test_checksum_pin_mismatch_stops_before_download(tmp_path):
    with patch("kitt.vm_debian.sys.platform", "linux"), patch(
        "kitt.vm_debian._program", return_value="/bin/mock"
    ), patch("kitt.vm_debian.expected_sha512", return_value="a" * 128), patch(
        "kitt.vm_debian.download_debian"
    ) as download:
        with pytest.raises(VMError, match="operator pin"):
            prepare_debian(tmp_path / "vm", agent_sha="a" * 40, image_sha512="b" * 128)
        download.assert_not_called()


def test_official_checksum_parser_rejects_duplicate_entries():
    line = (hashlib.sha512(b"fixture").hexdigest() + "  " + IMAGE + "\n").encode()
    with patch("kitt.vm_debian._read_https", return_value=line + line):
        with pytest.raises(VMError):
            expected_sha512()
