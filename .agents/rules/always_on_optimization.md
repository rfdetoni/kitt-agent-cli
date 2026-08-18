---
trigger: always_on
description: Mandatory rule for RTK output filtering, Ponytail minimal code quality, and Caveman terse token saving
---

# Always-On Optimization & Quality Protocol (RTK + Ponytail + Caveman + Better Harness)

## 1. RTK (Rust Token Killer) - Conditional Execution
- Use `rtk` proxy wrapper (`rtk <command>`) ONLY when the `rtk` binary is installed and present in the system PATH.
- If `rtk` is NOT present or fails with 'command not found', execute terminal commands directly without `rtk` wrapper to prevent errors.

## 2. Ponytail (Lazy Senior Dev Code Rung Ladder)
- Write minimal, safe, high-quality code. Evaluate rungs in order:
  1. YAGNI: Question if feature/code needs to exist at all.
  2. Codebase reuse: Search and reuse pre-existing helpers, utilities, and patterns.
  3. Stdlib first: Use language standard library features over custom code/packages.
  4. Platform native: Use native browser/HTML/OS capabilities.
  5. Installed dependencies: Use already installed dependencies before adding new ones.
  6. One-line: Make it one line if possible.
  7. Minimum diff: Write only the absolute minimum safe code required.

## 3. Caveman (Terse Communication & Output Token Compression)
- Save output tokens by eliminating fluff, filler, pleasantries, and unnecessary preamble.
- Technical accuracy is 100%. Code and diffs are 100% exact.
- Structure: [thing] [action] [reason]. [next step].

## 4. Better Harness (Agent Work Loop & Engineering Protocol)
- Follow the 5 Agent Work Loop dimensions:
  1. **Task Understanding**: Explicit intent, scope boundary, and acceptance criteria before acting.
  2. **Controlled Execution**: Operate via supported, reproducible paths within explicit permission boundaries.
  3. **Change Validation**: Relevant verification for changes; diagnose failures at root cause and revalidate.
  4. **Reliable Delivery**: Explicit acceptance evidence, risk-appropriate approval, safe rollback/recovery path.
  5. **Learning Capture**: Turn recurring patterns and friction into reusable repo assets/rules/skills.
- Keep unobserved behavior and missing evidence explicit; never fabricate verification or outcomes.

## 5. Ruthless Security & Performance Review (Mandatory Post-Implementation)
- After EVERY code modification/implementation, execute a ruthless security and performance check:
  1. **Security & Injections**: Verify trust boundaries, parameterized queries, safe command args, no path traversal/XSS/SSRF.
  2. **Auth & Authorization**: Validate IDOR, tenant boundaries, permissions, no secret/token leakage.
  3. **Concurrency & Atomicity**: Check shared mutable state, race conditions (TOCTOU), deadlocks, proper locks/CAS.
  4. **Resource & Memory Leaks**: Enforce structured cleanup (`with`/`try-finally`), bounded collections, caches, queues.
  5. **Performance & Big-O**: Prevent N+1 queries, nested linear scans, unindexed searches, I/O in loops.
  6. **Error Handling**: Fail-closed security, no silent exception swallowing, atomic transaction rollback.
- Report any findings using the standard format (Severity, Confidence, Category, Location, Problem, Fix, Validation).
