from __future__ import annotations

from kitt.backend_ir.service import BackendService
from kitt.surfaces.service import SurfaceService


def test_backend_ir_validates_plans_and_compiles_without_executing_code():
    service = BackendService()
    module = {
        "id": "billing",
        "revision": 0,
        "resources": [
            {
                "id": "Invoice",
                "kind": "entity",
                "spec": {"fields": {"id": "uuid", "amount": "number"}},
            },
            {
                "id": "create_invoice",
                "kind": "endpoint",
                "spec": {
                    "method": "POST",
                    "path": "/invoices",
                    "operation": "create_invoice",
                    "depends_on": ["Invoice"],
                },
            },
        ],
    }
    assert service.validate(module) == []
    plan = service.plan(module)
    assert len(plan.operations) == 2
    compiled = service.compile(module, "python")
    assert compiled["ok"] is True
    assert {item["path"] for item in compiled["files"]} == {
        "backend.ir.json",
        "backend_contracts.py",
    }


def test_surface_actions_are_semantic_and_revisioned():
    events = []
    service = SurfaceService(
        event_callback=lambda name, payload: events.append((name, payload))
    )
    surface = service.publish(
        {
            "id": "confirm",
            "catalog_id": "kitt.core.v1",
            "root": "root",
            "components": [
                {
                    "id": "root",
                    "component": "Button",
                    "props": {"label": "Apply", "action": "apply"},
                }
            ],
        }
    )
    assert surface["revision"] == 1
    action = service.action("confirm", "root", "apply", {"source": "test"})
    assert action["action"] == "apply"
    assert any(name == "SurfaceAction" for name, _ in events)
