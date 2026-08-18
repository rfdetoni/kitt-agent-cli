---
name: rtk
description: Rust Token Killer CLI output optimization proxy rules (conditional)
version: 1.0.0
author: K.I.T.T. Core
---

# RTK Protocol (Conditional Execution)
- Use `rtk` proxy wrapper (`rtk <command>`) ONLY when the `rtk` binary is present and available in the system PATH.
- If `rtk` is NOT present or fails with 'command not found', execute commands directly without `rtk` to avoid errors.
