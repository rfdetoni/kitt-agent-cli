# Agent CLI 0.87.11 — bounded compact KAP/2 finals

- Plan prompt uses tagged `K2 F @json: {...} K2/END` instead of
  indentation-dependent nested task arrays.
- Protocol SDK pin advances to 0.13.3
  (`cc3b51dd0e868adaab4ed7a0069c4dbf9aa08c42`).
- Strict schema, authorization, host tools and KAP/2 version negotiation
  remain the same. No provider DOM parsing in the Agent.
- Runtime version, pyproject and uv.lock agree on 0.87.11.
