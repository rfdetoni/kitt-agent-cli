from __future__ import annotations

import unittest

from kitt.runtime.core_runtime import (
    OPERATION_REGISTRY as CORE_OPERATION_REGISTRY,
    OPERATION_SPECS as CORE_OPERATION_SPECS,
    RuntimeOperationSpec,
)
from kitt.runtime.safe_runtime import (
    OPERATION_REGISTRY as SAFE_OPERATION_REGISTRY,
    OPERATION_SPECS as SAFE_OPERATION_SPECS,
)


class RuntimeOperationRegistryTests(unittest.TestCase):
    def test_core_catalog_is_immutable_and_safe_runtime_extends_without_mutation(self):
        self.assertIs(CORE_OPERATION_SPECS, CORE_OPERATION_REGISTRY)
        self.assertIs(SAFE_OPERATION_SPECS, SAFE_OPERATION_REGISTRY)
        self.assertNotIn("repo.write_file", CORE_OPERATION_SPECS)
        self.assertIn("repo.write_file", SAFE_OPERATION_SPECS)
        self.assertFalse(hasattr(CORE_OPERATION_SPECS, "update"))

        with self.assertRaises(TypeError):
            CORE_OPERATION_SPECS["test.operation"] = RuntimeOperationSpec(
                "test.operation", None
            )

    def test_extension_is_copy_on_write_and_rejects_duplicate_registration(self):
        extended = CORE_OPERATION_REGISTRY.extend(
            {"test.operation": RuntimeOperationSpec("test.operation", None)}
        )
        self.assertIn("test.operation", extended)
        self.assertNotIn("test.operation", CORE_OPERATION_REGISTRY)

        with self.assertRaises(ValueError):
            CORE_OPERATION_REGISTRY.extend(
                {"repo.read": RuntimeOperationSpec("repo.read", None)}
            )


if __name__ == "__main__":
    unittest.main()
