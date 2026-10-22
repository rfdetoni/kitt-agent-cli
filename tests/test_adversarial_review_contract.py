"""Regression: reviewer prompt and the verified coverage schema must agree."""
import json
from types import SimpleNamespace

from kitt.goals.review import AdversarialCodeReviewer, _ALLOWED_CATEGORIES


def _approved_review(areas):
    return json.dumps({
        "status": "APPROVED",
        "summary": "Inventory verified without required findings",
        "reviewed_areas": areas,
        "findings": [],
        "approval_evidence": "Compared the supplied inventory and deterministic checks; all categories evaluated.",
    })


def _review(response, captured=None):
    def call(_system, prompt):
        if captured is not None:
            captured.append(prompt)
        return response

    return AdversarialCodeReviewer(call).review(
        objective="Validate ELF inventory",
        success_criteria=["Record exact ELF sections"],
        verification=SimpleNamespace(checks=[]),
        change_snapshot='diff --git a/inventory.json b/inventory.json\\n+{"sections":42}',
    )


def test_prompt_names_all_categories_and_example_satisfies_parser():
    required = sorted(_ALLOWED_CATEGORIES)
    prompts = []
    result = _review(_approved_review(required), prompts)
    assert result.approved, result.feedback
    assert 'reviewed_areas MUST contain every exact category ID' in prompts[0]
    assert json.dumps(required) in prompts[0]
    assert '"reviewed_areas":' in prompts[0]


def test_annotated_category_names_are_normalized_without_inventing_coverage():
    required = sorted(_ALLOWED_CATEGORIES)
    result = _review(_approved_review([f"{area.title()}: assessed" for area in required]))
    assert result.approved, result.feedback
    assert set(result.reviewed_areas) == set(required)


def test_former_noncanonical_review_does_not_silently_advance_contract():
    # The observed output named only four areas in prose; it does not
    # establish coverage of the other production risk categories.
    observed = [
        "Correctness: formato ELF64, arquitetura, dependencias e simbolos",
        "Maintainability: JSON estruturado",
        "Security: preservacao do hash, protecoes NX e RELRO",
        "Tests: validacoes deterministicas",
    ]
    result = _review(_approved_review(observed))
    assert not result.approved
    assert result.status == "INVALID_REVIEW"
    assert "without covering all production review areas" in result.feedback
    assert set(result.reviewed_areas) == {"correctness", "security", "maintainability", "tests"}
