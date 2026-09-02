from __future__ import annotations

import dataclasses
from kitt.core.runtime_config import RuntimeConfig
from kitt.settings.control_center import SettingsDescriptorProvider

# Derived or process-only fields that do not represent persistent Control Center settings
NON_PERSISTENT_OR_DERIVED_FIELDS = frozenset({
    "frontend_only",
})


def test_runtime_config_descriptor_coverage() -> None:
    config_fields = {f.name for f in dataclasses.fields(RuntimeConfig)} - NON_PERSISTENT_OR_DERIVED_FIELDS
    sections = SettingsDescriptorProvider.get_sections()
    descriptor_keys = set()
    for section in sections:
        for field in section.get("fields", []):
            descriptor_keys.add(field["key"])

    missing = config_fields - descriptor_keys
    assert not missing, f"Missing RuntimeConfig fields in Control Center descriptors: {sorted(missing)}"


def test_descriptor_unique_keys_and_valid_defaults() -> None:
    sections = SettingsDescriptorProvider.get_sections()
    seen_keys: set[str] = set()
    for section in sections:
        for field in section.get("fields", []):
            key = field["key"]
            assert key not in seen_keys, f"Duplicate field key '{key}' in SettingsDescriptorProvider"
            seen_keys.add(key)
            assert "label" in field and field["label"]
            assert "type" in field and field["type"]
            assert "apply_mode" in field and field["apply_mode"]
            default_val = field.get("default")
            if "minimum" in field and "maximum" in field and isinstance(default_val, (int, float)):
                assert field["minimum"] <= default_val <= field["maximum"], (
                    f"Default {default_val} out of bounds [{field['minimum']}, {field['maximum']}] for key {key}"
                )
