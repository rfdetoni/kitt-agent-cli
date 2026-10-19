# Agent CLI 0.88.16 — private Wine home for isolated differential pilot

## Root cause

Windows Oracle's authenticated `event_auto_reset` probe returned exit 0 and
`{"before":258,"signaled":0,"consumed":258}`. The isolated Wine process
returned exit 1 because `HOME=/tmp` was not owned by the caller UID;
`/tmp` is a root-owned Docker tmpfs. The generic validator hid this
execution failure.

## Changes

- Create a private `/tmp/kitt-home` with `umask 077` using a fixed
  no-input `/bin/sh -c` preamble before execing
  `/usr/lib/wine/wine64 /probes/event_probe.exe`.
- Set `HOME=/tmp/kitt-home` and `WINEPREFIX=/tmp/kitt-home/.wine`; preserve
  non-root host UID/GID, `--read-only`, `--network none`, `--cap-drop ALL`,
  resource limits and read-only probe copy.
- Validate the Windows reference receipt first and classify a nonzero Wine
  process exit without returning untrusted stdout/stderr.
- Add mocked regression tests for the hardened Docker arguments and receipt.
- Bump Python package, fallback and uv.lock to `0.88.16`. Protocol SHA pin
  `ac9baf8b4b8f03e839471c7bbb7d5e52e970a9ec` remains unchanged.

CI does not execute a live Windows/KVM + Wine differential pilot. Final
`PASS_SCENARIO` must come from the operator's host verification.
