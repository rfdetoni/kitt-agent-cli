#!/usr/bin/env python3
from __future__ import annotations

from kitt.runtime.core_runtime import OPERATION_SPECS


HEADER = """# KITT Runtime Operation Reference

This file is generated from the authoritative runtime operation registry.

Regenerate it with:

```bash
python scripts/generate_runtime_reference.py > docs/RUNTIME_REFERENCE.md
```

The table documents authority metadata. Operation-specific argument validation remains owned by the runtime/handler implementing the operation; callers must use the model-facing tool contract and examples supplied by the Agent.

| Operation | Capability | Policy action | Sensitive | Risk | Sandbox | Resume tool |
| --- | --- | --- | ---: | ---: | --- | --- |
"""


def render_runtime_reference() -> str:
    rows = []
    for name, spec in sorted(OPERATION_SPECS.items()):
        rows.append(
            "| {name} | {capability} | {policy} | {sensitive} | {risk} | {sandbox} | {resume} |".format(
                name=name,
                capability=spec.required_capability or "-",
                policy=spec.policy_tool_action or "-",
                sensitive="yes" if spec.sensitive else "no",
                risk=spec.risk_cost,
                sandbox=spec.sandbox_profile or "-",
                resume=spec.resume_tool_name or "-",
            )
        )
    return HEADER + "\n".join(rows) + "\n"


def main() -> None:
    print(render_runtime_reference(), end="")


if __name__ == "__main__":
    main()
