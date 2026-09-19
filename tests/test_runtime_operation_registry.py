from __future__ import annotations

import unittest

from kitt.runtime.core_runtime import (
    OPERATION_REGISTRY,
    OPERATION_SPECS,
    RuntimeOperationSpec,
)
from kitt.runtime.safe_runtime import (
    OPERATION_REGISTRY as SAFE_OPERATION_REGISTRY,
    OPERATION_SPECS as SAFE_OPERATION_SPECS,
)


class RuntimeOperationRegistryTests(unittest.TestCase):
    def test_runtime_catalog_has_one_immutable_authoritative_view(self):
        self.assertIs(OPERATION_SPECS, OPERATION_REGISTRY)
        self.assertIs(SAFE_OPERATION_REGISTRY, OPERATION_REGISTRY)
        self.assertIs(SAFE_OPERATION_SPECS, OPERATION_SPECS)
        self.assertIn("repo.write_file", OPERATION_SPECS)
        self.assertIn("repo.definition", OPERATION_SPECS)
        self.assertIn("security.scan", OPERATION_SPECS)
        self.assertEqual(
            OPERATION_SPECS["process.run"].sandbox_profile,
            "workspace-write",
        )
        self.assertFalse(hasattr(OPERATION_SPECS, "update"))

        with self.assertRaises(TypeError):
            OPERATION_SPECS["test.operation"] = RuntimeOperationSpec(
                "test.operation", None
            )

    def test_extension_is_copy_on_write_and_rejects_duplicate_registration(self):
        extended = OPERATION_REGISTRY.extend(
            {"test.operation": RuntimeOperationSpec("test.operation", None)}
        )
        self.assertIn("test.operation", extended)
        self.assertNotIn("test.operation", OPERATION_REGISTRY)

        with self.assertRaises(ValueError):
            OPERATION_REGISTRY.extend(
                {"repo.read": RuntimeOperationSpec("repo.read", None)}
            )


if __name__ == "__main__":
    unittest.main()
