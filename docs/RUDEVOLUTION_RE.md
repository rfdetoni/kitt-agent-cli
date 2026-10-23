# ruDevolution in KITT RE (opt-in planning)

Reviewed source: https://github.com/ruvnet/rudevolution
main commit bc9a413966aef2270bc37a033283b02f193002c9.
The Rust examples/run_on_cli.rs accepts a local JavaScript bundle and --output-dir.

The reverse-engineering MCP plugin remains disabled by default. After
operator enablement, rudevolution_plan accepts four required fields:
- path: local workspace-relative .js/.mjs/.cjs file, max 32 MiB
- executable: absolute path to operator-built Rust binary
- binary_sha256: lowercase SHA256 pin of executable bytes
- output_dir: fresh output under an existing workspace parent

The tool only returns argv, cwd, digests and warnings. It never executes
an input, downloads packages, or creates output. The host must independently
authorize process.run, recheck both hashes immediately before use, and
enforce a strong resource-bounded/network-denied sandbox. The upstream Rust
example MAY INVOKE NODE during output syntax validation: not pure static
analysis. No npx fallback is allowed: ruvector npm is a separate tool.

Results are heuristic module/name proposals. Witnesses verify integrity
relative to separately trusted source bytes, NOT behavioral equivalence.
Do not transfer reconstructed implementations across clean-room boundaries
without reviewed interface specifications and independent differential evidence.
PE/ELF tools and Windows Oracle workflows are unchanged.
