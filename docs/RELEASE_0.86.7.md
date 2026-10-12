# Agent CLI 0.86.7 — interactive VM creation helper

## Changes

- Adds `kitt vm create` (alias `kitt vm wizard`) to guide operators through
  Windows or Linux reverse-engineering guest creation.
- Prompts for operating system and an existing user-supplied ISO or standalone
  QCOW2 path. Validates source format and file existence, then delegates to
  the existing Windows/Linux QEMU setup assistants for resources and consent.
- Supports automation with `--os`, `--source`, `--yes`; Windows uses `--variant`
  and Linux uses `--distro`/`--tools`.
- Does not download guest OS images, modify the source image, auto-install
  Windows/Linux, enable host networking or alter ordinary `kitt` coding mode.

## Validation

Focused tests cover both OS routes, interactive questions, noninteractive
mode, unsupported formats, path mismatches, and normal coding-agent parsing.
Release-critical CI must pass before promotion.

## Compatibility

No dependency, KAP/1, MCP or Reverse Proxy protocol changes.
Agent CLI 0.86.7 replaces 0.86.6 in the coordinated ecosystem release.
See `docs/VM_CREATION_HELPER.md`.
