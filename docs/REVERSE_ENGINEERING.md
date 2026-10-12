# KITT reverse-engineering foundation (experimental)

This feature is **opt-in**. It is not a Windows emulator, decompiler, debugger,
or autonomous production-ready binary execution harness.

## Scope delivered

The bundled \`kitt-reverse-engineering\` plugin registers a local stdio MCP
server through the existing \`MCPAPI.register_stdio\` lifecycle, with these
read-only model-facing tools:

| MCP tool | Behavior |
| --- | --- |
| \`mcp.reverse-engineering.binary_inspect\` | PE/ELF header metadata + SHA-256; max file size 64 MiB |
| \`mcp.reverse-engineering.evidence_compare\` | Deterministic comparison of two JSON observations |
| \`mcp.reverse-engineering.evidence_probe_compare\` | Verify SHA-256-tagged probe receipts and compare JSON observations |

The tools use the existing KITT registry and MCP capability/approval policy.
No arbitrary shell, debugger attach, instrumentation, or remote process
execution operation is exposed by this plugin.

## Enable locally

\`\`\`sh
kitt plugins enable kitt-reverse-engineering
kitt plugins inspect kitt-reverse-engineering
\`\`\`

Restart KITT to activate the plugin-managed MCP lifecycle. The server
uses the current Python interpreter, remains workspace-scoped, and has an
explicit \`allow_tools\` list. Do not expose it as a public network endpoint.

In interactive mode, ask KITT to call the MCP tool
\`mcp.reverse-engineering.binary_inspect\` on a relative path to a sample PE/ELF.

## Disposable Windows/Linux VM probes

The lab operator, not the model, installs this package and the test binaries
inside **disposable isolated VMs**. Do not run unknown binaries on the host.
Do not mount host credentials or writable source trees into the VM. Disable
outbound networking by default and restore a known clean snapshot per test.

Prepare a probe file \`event_probe.exe\` in an operator-controlled directory
and independently record its SHA-256. Example manifest:

\`\`\`json
{
  "probes": {
    "event_auto_reset": {
      "path": "event_probe.exe",
      "sha256": "REPLACE_WITH_REAL_64_CHARACTER_LOWERCASE_SHA256"
    }
  }
}
\`\`\`

On Windows use \`Get-FileHash -Algorithm SHA256\`; on Linux use \`sha256sum\`.
Windows example source: \`examples/reverse_engineering/windows_event_probe.c\`.

Compile with a Windows compiler and the official Windows SDK, for example:

\`\`\`powershell
cl /nologo /W4 examples\reverse_engineering\windows_event_probe.c /Fe:event_probe.exe
\`\`\`

Run inside the **Windows guest**, using an installed KITT Agent CLI environment:

\`\`\`powershell
python -m kitt.extensions.builtin_plugins.reverse_engineering.probe_runner --probe-root C:\probes --manifest C:\probes\manifest.json --probe event_auto_reset
\`\`\`

On Linux, use a separate manifest that hashes its **own** guest entry point
(for example a pinned test binary or controlled Wine wrapper). Do not assume
native executables on Windows and Linux have the same binary hash. The same
logical probe ID connects comparable observations.

The runner prints one JSON receipt containing platform, code, output,
runtime duration and a SHA-256 digest. It requires a manifest-pinned file,
has a 15-second timeout and limits reported stdout/stderr to 64 KiB each.
POSIX process groups are terminated after the probe. On Windows, descendants
may survive a process timeout: disposable VM teardown is required as an
isolation and cleanup boundary. Temporary files bound output *memory* but do
not enforce a guest filesystem quota.

The receipts are **not signed attestations**: the SHA-256 can detect accidental
tampering but not a malicious or compromised guest. The operator must secure
the machine image, manifest, transmission and capture path.

Use \`mcp.reverse-engineering.evidence_probe_compare\` with two receipts to
verify the recorded hashes and compare each probe's stdout JSON. Both probe
processes must exit successfully, without timeout or truncated output, and
emit exactly one JSON object. The comparator reports differing JSON paths.
Do not compare raw timing, PIDs, memory addresses, or other nondeterministic
fields without an explicitly designed normalizer.

## Future extensions (not implemented in this release)

- Isolated, authorized Ghidra Headless and Frida adapters with lifecycle/timeout supervision.
- Windows and Linux remote runner provisioning, snapshots, attestation and secure evidence transport.
- Source-level Wine/ReactOS correction loops with independently executed E2E checks.
- Persisted evidence database, retention/PII controls and KITT Memory semantic indexing.
- Cross-platform VM-based end-to-end CI, version bump and coordinated ecosystem release.

These must be validated against real external infrastructure before being
called available. A passing Python unit test does not prove that a Windows API
has been replicated correctly.

## Validation

\`\`\`sh
python -m pytest -q tests/test_reverse_engineering_mcp.py \
  tests/test_reverse_engineering_probe_runner.py \
  tests/test_reverse_engineering_probe_evidence.py \
  tests/test_reverse_engineering_plugin_registration.py
\`\`\`
