# KITT Agent CLI 0.85.0 — Structured Agent results

Agent contract v3 carries plans and review/validation/completion reports directly as objects in content. Ordinary answers remain strings; tool actions keep content null. The Proxy performs standard OpenAI content serialization in host code, and Agent decodes one complete result through Protocol 0.10.0.

Planning, high-risk pre-mutation review, independent validation, adversarial review and autonomous completion share one strict decoder. Their textual KITT_* report markers and permissive raw-decode extraction are removed. Exactly one bare or JSON-fenced object is accepted. Duplicate keys, extra prose, multiple objects, non-finite values and non-object results fail closed. Domain validation and read-only authority remain unchanged.

Agent rejects discovered incompatible Proxy versions before dispatch. Install Agent 0.85.0, Proxy 5.0.0, Protocol 0.10.0 and aligned consumers together. No v2 negotiation or prefixed-report fallback is provided. WebChat token ownership, native token budgets and operational/security controls remain unchanged.

270 Agent tests passed with one skipped and 18 subtests; compilation, critical Ruff and clean-room checks passed. Regressions preserve long planning/review objectives, reject conflicting verdicts/trailing prose, and block v2 capability discovery before dispatch. Consumer and clean-install CI validate composition. No fresh authenticated Gemini session was executed locally.
