from types import SimpleNamespace

import pytest

from kitt.tools.build_detector import VerificationStep
from kitt.validation.orchestrator import VerificationOrchestrator


def test_required_check_without_executor_is_unavailable(tmp_path, monkeypatch):
    verifier = VerificationOrchestrator(tmp_path, full_enabled=True)
    monkeypatch.setattr(
        verifier, "_plan", lambda *a: [VerificationStep("go.tests", ["go", "test"], 30)]
    )
    report = verifier.verify([], check_ids=["go.tests"])
    assert report.status == "UNAVAILABLE" and not report.ok


@pytest.mark.parametrize(
    "timed_out,cancelled,status",
    [(True, False, "TIMED_OUT"), (False, True, "CANCELLED"), (False, False, "PASS")],
)
def test_status_comes_from_process_result(tmp_path, monkeypatch, timed_out, cancelled, status):
    runner = SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(
            returncode=0, timed_out=timed_out, cancelled=cancelled, stdout="PASS", stderr=""
        )
    )
    verifier = VerificationOrchestrator(tmp_path, runner, full_enabled=True)
    monkeypatch.setattr(
        verifier, "_plan", lambda *a: [VerificationStep("go.tests", ["go", "test"], 30)]
    )
    report = verifier.verify([])
    assert report.status == status
    assert report.ok == (status == "PASS")


def test_disabled_required_check_does_not_become_not_applicable(tmp_path, monkeypatch):
    verifier = VerificationOrchestrator(tmp_path, full_enabled=False)
    monkeypatch.setattr(
        verifier,
        "_plan",
        lambda paths, full: [VerificationStep("go.tests", ["go", "test"], 30)] if full else [],
    )
    assert verifier.verify([]).status == "SKIPPED"


def test_catalog_construction_has_no_network_egress(tmp_path, monkeypatch):
    from kitt.llm.catalog import ProviderCatalogService

    def unexpected(*a, **k):
        pytest.fail("catalog construction must not contact remote HTTP")

    monkeypatch.setattr("kitt.llm.catalog.secure_urlopen", unexpected)
    assert ProviderCatalogService(cache_dir=str(tmp_path)).providers()
