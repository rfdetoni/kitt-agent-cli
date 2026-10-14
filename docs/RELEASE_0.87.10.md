# Agent CLI 0.87.10 — Python compute via KAP/2

The new `sandbox.python` host operation accepts `code<<PY` in an ordinary
`K2 T kitt_runtime` envelope. It delegates to the existing `python_compute`
tool with the `compute.safe` capability, host policy, bounded AST interpreter,
process isolation and strict resource limits. Results are structured.
KAP/2 syntax, Protocol SDKs, authorization and transport are unchanged.

Unsafe Python features (imports, filesystem, networking, shell, reflection,
classes and user-defined functions) remain disabled. Persistent Python
sessions and batches are not part of this MVP.
