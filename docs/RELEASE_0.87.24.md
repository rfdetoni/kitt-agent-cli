# Agent CLI 0.87.24 — WebChat capture failure handling

## Change
- When the reverse-proxy reports `ui_timeout`, explain that no KAP/2
  action was delivered to the host; the submission may already have occurred.
  Require the operator to inspect the authenticated WebChat session before
  manually retrying. No silent model turn replay or relaxation of KAP validation.
- Pin Python `kitt-protocol` Git requirement to
  `cc3b51dd0e868adaab4ed7a0069c4dbf9aa08c42` rather than a mutable branch.
- No KAP/2 grammar change. Compatible with Reverse Proxy 5.3.15.

## Regression and verification
`python -m pytest tests/test_kitt_reverse_proxy_compat.py` checks the
SSE `ui_timeout` classification and existing fail-closed handling.
This does not establish an authenticated browser end-to-end pass.
