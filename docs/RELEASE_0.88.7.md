# Agent CLI 0.88.7 — managed reverse-engineering lab

- Adds operator-only kitt re lab doctor/status/setup/up/down/verify/recreate.
- Linux RE full toolbox is isolated and reuses the existing provisioner and image.
- Windows uses only official Dockur Windows with an operator-pinned registry digest and immutable image ID.
- New Windows has unique private credentials and OEM-first-install OpenSSH + SHA-pinned probe runner.
- The first fixed Win32 probe is now compared with a separately isolated, short-lived Docker Wine candidate (not a VM).
- Destructive reset is scoped, validates KITT-owned immutable Docker IDs and paths and requires a short-lived confirmation token obtained by a read-only plan plus Windows license approval when reinstalling.
- Windows readiness requires live authenticated probe.info with matching manifest. Windows/KVM E2E is not verified by CI, and this release must not claim live Windows functionality until operator-side verification passes.
- Root installer integration and ecosystem pins are released independently after this Agent revision is merged.
