# Agent CLI 0.83.19

The final cross-path review found a second explicit UI action that saved a non-default model endpoint without granting trust: model selection after authentication and the no-auth/local-provider picker. Both now use the existing pending-selection application path. The port-3001 regression reproduced the same Refusing provider egress failure even after managed Reverse Proxy role binding was corrected in 0.83.18.

The explicit pending selection grants exact-origin, provider-scoped trust through the existing private store before router persistence. A failed trust write remains a visible UI error; inherited endpoints are not automatically trusted. Workspace loading, service listing/start and other origins/providers remain outside this authorization path. Credential identity and OAuth official-origin restrictions remain enforced by credential resolution.

Both paths reproduced the refusal before correction. The regressions exercise the real pending-selection helper and consumer endpoint policy, and checks neighboring ports remain denied. Package/source/uv lock metadata agree at 0.83.19. No new dependency, wire contract or blanket local-network exemption is introduced.
