# Reverse-proxy adapter review — 2026-09-08

KITT exposes its existing host tool surface as native API tools to the reverse
proxy. The adapter now preserves the compact runtime's operation catalog when
removing the legacy text contract. Native calls retain their identity through
the CLI loop and return results to the corresponding browser conversation.

## Corrected findings

| Severity / confidence | Location | Problem and impact | Fix and validation |
| --- | --- | --- | --- |
| High / high | `kitt/tools/protocol.py` | Legacy heuristics could interpret code/XML inside canonical arguments as another call. | Parse the complete canonical envelope first and validate its shape; tests preserve embedded calls, tags, empty arguments and custom tool names. |
| High / high | `kitt/llm/providers/kitt_reverse_proxy.py` | Truncated/error SSE could produce executable calls, while unexpected names were not checked against the request. | Require `[DONE]`, reject malformed/error events, validate the tool allowlist and single-call contract before yielding a call. |
| Medium / high | Same adapter | Removing the legacy tool contract lost the runtime operation catalog. | Derive the native schema enum from `OPERATION_SPECS`; test against actual registry definitions. |
| Medium / high | Existing test clients | Mocks lacked the current `session_key` parameter and broke five baseline tests. | Align fake client signatures; full suite passes. |

Tool results remain untrusted data. Existing host approvals and containment are
unchanged. Protocol errors are typed as non-connection failures. The adapter
limits accepted stream bytes to 4 MiB and assembled arguments to 64 KiB, with
bounded line reads before decoding. No dependencies or additional tool-execution
paths were introduced.

## Validation

- Python: 791 tests and 17 subtests pass; compileall passes.
- Rust workspace/all features: 2 tests pass using `CC=/usr/bin/gcc`. The user's
  default compiler wrapper failed on a missing Zig cache object; no global
  compiler settings were changed.
- Packaging clean-room guard passes.
- Actual Chromium fixture plus the real proxy HTTP/SSE server and installed
  Python adapter completed a host read and tool-result round trip.
- Editable installation already targets this checkout; installed `kitt --help`
  succeeds outside the workspace.

Supporting component checks also passed: protocol Rust (3) and TypeScript
fixtures (6), memory Rust (15), toolbox Rust (1), assistant Rust (42), workers
Python (16). Live authenticated provider sessions and Windows/macOS remain
unverified. Detailed review focused on critical contracts and execution paths,
not every source line in the eight-repository workspace.
