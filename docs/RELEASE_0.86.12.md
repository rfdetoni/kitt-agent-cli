# Agent CLI 0.86.12 — REA MCP contract accuracy

## Corrections

- Fix guided `rea_workflow` suggestions to use real upstream REA 6.4.0 MCP tool IDs: `inspect_pcap_capture` instead of nonexistent `inspect_packet_capture`; `inspect_analysis_view` in comparisons instead of nonexistent `compare_functions`; native analysis uses `analyze_function`, `get_call_graph`, `inspect_native_load_image` rather than nonexistent `open_binary`.
- Clarify that the optional `kitt-rea` plugin itself never downloads or installs REA: the **KITT root `--preset full`** already installs pinned REA separately, unless `--skip-rea` is given. `--with-rea` also works for other root presets.
- Keep Agent coding mode unchanged and preserve REA 6.4.0/Node 24 in the existing Linux RE `full` Docker/Podman image. Image version remains **0.86.10** because no image contents were changed.
- Bump Agent CLI to 0.86.12 and bundled `kitt-rea` plugin metadata to 1.1.1; align uv.lock.

## Validation

Focused `rea_workflow` regression checks prevent unsupported tool names from being suggested. The previous Agent 0.86.11 release already validated the REA-included full container build; this is a small code/contract correction, not a new image build.
