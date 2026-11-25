from kitt.core.completion_guard import _ProgressAwareExecutionLedger, build_completion_contract
from kitt.core.turn_events import ToolCompleted, ToolStarted


def test_validation_evidence_does_not_survive_mutation_or_failed_retry():
    ledger = _ProgressAwareExecutionLedger()
    args = {"operation": "process.run", "arguments": {"argv": ["npm", "run", "build"]}}

    def validate(call_id, success):
        ledger.start(ToolStarted(tool_name="kitt_runtime", args=args, call_id=call_id))
        ledger.complete(ToolCompleted(tool_name="kitt_runtime", success=success,
                                     output="build result", call_id=call_id))

    validate("first", True)
    assert ledger.validation_succeeded
    validate("second", False)
    assert not ledger.validation_succeeded
    validate("third", True)
    assert ledger.validation_succeeded
    ledger.start(ToolStarted(tool_name="kitt_runtime", call_id="edit",
                            args={"operation": "repo.write_file",
                                  "arguments": {"path": "src.py", "content": "pass"}}))
    ledger.complete(ToolCompleted(tool_name="kitt_runtime", success=True,
                                 output="written", call_id="edit"))
    assert not ledger.validation_succeeded
    assert not ledger.validated_scopes


def test_explicit_test_request_requires_validation_without_creation():
    assert build_completion_contract("Teste o projeto com npm run build").require_validation
    assert not build_completion_contract("Explain how tests work").require_validation
