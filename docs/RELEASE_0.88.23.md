# Agent CLI 0.88.23 — Allow-all approval UX

- The approval modal's **A** shortcut enables autonomous mode for the **current workspace**, instead of remembering only the current tool.
- **w** keeps the narrower remember-this-tool-in-workspace behavior.
- Approval of the pending action precedes the autonomy change. This preserves the authority snapshot bound to that pending action.
- Daemon errors when changing autonomy are surfaced; a consumed approval is not reopened.
- Existing ASK and DENY choices in the Autonomy menu remain operator-controlled. Automatic process execution still requires a functional strong host sandbox where applicable; network/control-plane capabilities and path boundaries do not change.

## Regression checks
`python -m pytest -q tests/test_workspace_allow_all_approval.py tests/test_autonomy_policy.py tests/test_approval_authority_resume.py`
`python -m compileall -q kitt tests`
