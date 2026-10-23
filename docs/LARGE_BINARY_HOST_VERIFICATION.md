# Bounded host verification for large workspace binaries

The default WorkspaceFileSystem.read() remains bounded to 8 MiB, including all
source parsing and tool-result content. For **non-source files above 8 MiB**, the
PostEditValidator now calls WorkspaceFileSystem.hash_regular(), which opens the
actual workspace file using the same no-follow/scope protections as read(), and
streams at most 512 MiB, in 1 MiB chunks.

This independently computes bytes, SHA256 and stable filesystem metadata
without using agent-provided claims or keeping the entire binary in RAM. Files
that change identity, size, mtime or ctime during the scan fail closed. Larger
artifacts report a bounded error; no global read cap has been increased.

A successful stream fingerprint confirms complete readability and SHA256 of
the original at scan time. It is **not** a signature, does not inherently
compare against an operator-pinned expected hash, and does not prove Ghidra
decompilation or behavioral equivalence. The independent RE validator should
still compare the measured SHA256 and size with the trusted inventory/manifest.

For the 292232560-byte `2.1.308` ELF, this removes the false
`workspace-read: Workspace file exceeds 8388608 bytes` syntax-gate blocker
while retaining syntax enforcement on actual source paths. Validate on the
original workstation: a merged CI test with fixture data is not a receipt
from the user's RE host environment.
