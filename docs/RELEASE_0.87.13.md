# Agent CLI 0.87.13 — KAP/2 host tool dispatch parity

Follow-up to Goals host-tool-first 0.87.12. The model is now told to
emit K2 T kitt_runtime, but the Agent CLI tool loop used the legacy
`parse_tool_call` bridge, which didn't decode KAP/2 tool envelopes.
Consequently a correct tool request would be treated as no tool call.

`kitt/tools/protocol.py` now delegates **only** the K2 T transport
decoding to the installed canonical `kitt-protocol` SDK
(`decode_kap2_frame`), then forwards the tool name and structured
arguments through the existing runtime dispatch/policy path. No tool
permissions are granted by the parser. K2 F remains a final result, not
a tool call. Malformed/competing/flattened K2 T envelopes are rejected.

Regression tests cover nested operation arguments and malformed cases.
Updated pyproject.toml, uv.lock and kitt/__init__.py consistently to
0.87.13. An authenticated WebChat execution is still required to show
the first real host receipt for T00.
