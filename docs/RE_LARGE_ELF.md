# KITT RE: large ELF inventory

A user RE workspace contained a roughly 292 MB Linux ELF while the existing
read-only binary_inspect tool limits complete in-memory PE/ELF reads to 64 MiB.
The opt-in restricted KITT RE MCP server now offers elf_inspect_stream for
workspace-relative ELF files from 64 bytes through 512 MiB. It reads in 1 MiB
chunks without executing or modifying the file and returns format, architecture,
endianness, byte size and SHA256. POSIX directory FDs reject symlink traversal.
It detects size/mtime changes during the pass; a single pass is NOT an atomic
filesystem snapshot. This tool does not parse sections or reconstruct code.

Use native Ghidra/Frida guest analysis with separately authorized host process
execution, bounded resources, a clean output directory and independent evidence
for full decompilation. Do not claim inventory alone completes the requested RE
or validates code equivalence. The original binary must remain unchanged.
