# KITT Agent CLI 0.88.36

Fix a real host error from Ubuntu installations where `/etc/resolv.conf` is a symlink into systemd-resolved's `/run` tree: `bwrap: Can't mount on symlink destination /etc/resolv.conf`. The process sandbox still hides `/run`; it no longer attempts to bind-mount over `/etc/resolv.conf` itself. Network-enabled profiles reintroduce only the canonical resolver target under `/run`; network-isolated profiles do not restore DNS files. Regular `/etc/hosts` and `/etc/nsswitch.conf` remain visible from the read-only root. Preserve all 0.88.35 Bubblewrap candidate discovery and functional AppArmor probe rules.

Validation: focused resolver-symlink unit tests, Agent PR Checks, Linux Docker/Podman/Windows smokes and immutable root ecosystem pins. A real run on the affected Ubuntu workstation is still needed to confirm the full sandbox command and WebChat response capture.
