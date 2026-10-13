"""REA plugin workflow guidance must not bypass the governed MCP boundary."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from kitt.extensions.builtin_plugins.rea import WORKFLOWS, setup


class FakeTools:
    def __init__(self):
        self.registered = {}

    def register(self, name, handler, **kwargs):
        self.registered[name] = (handler, kwargs)


class FakeMCP:
    def __init__(self):
        self.registered = []

    def list_servers(self):
        return []

    def register_stdio(self, *args, **kwargs):
        self.registered.append((args, kwargs))
        return {"registered": True}

    async def unregister_owned(self):
        return None


def _plugin():
    ctx = SimpleNamespace(
        tools=FakeTools(), mcp=FakeMCP(),
        logger=SimpleNamespace(info=lambda *args: None),
    )
    handle = setup(ctx)
    return ctx, handle


def test_workflow_maps_evidence_based_analysis_without_executing():
    ctx, _ = _plugin()
    assert set(ctx.tools.registered) == {"rea_inspect", "rea_workflow"}
    workflow, options = ctx.tools.registered["rea_workflow"]
    schema = options["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["target_type"]["enum"]) == set(WORKFLOWS)
    for name in ("native", "javascript", "electron", "managed",
                 "comparison", "crash", "network"):
        result = json.loads(workflow({"target_type": name}))
        assert result["target_type"] == name
        assert result["mcp_prefix"] == "mcp.rea."
        assert result["suggested_tools"]
        assert result["execution"].startswith("guidance_only")
        assert result["guest_access"] == "no implicit VM network or shell access"
    assert json.loads(workflow({"target_type": "unsupported"}))["error"] == "unsupported_target_type"
    # These identifiers are published in REA 6.4.0 MCP contracts, not guesses.
    assert "open_binary" not in WORKFLOWS["native"]["tools"]
    assert "compare_functions" not in WORKFLOWS["comparison"]["tools"]
    assert "inspect_packet_capture" not in WORKFLOWS["network"]["tools"]
    assert "inspect_pcap_capture" in WORKFLOWS["network"]["tools"]
    assert "compare_managed_members" in WORKFLOWS["comparison"]["tools"]
    assert "inspect_native_load_image" in WORKFLOWS["native"]["tools"]



def test_plugin_uses_only_local_pinned_rea_mcp_and_skips_when_absent():
    ctx, handle = _plugin()
    with patch("kitt.extensions.builtin_plugins.rea.shutil.which", return_value=None):
        asyncio.run(handle.start())
    assert ctx.mcp.registered == []
    with patch("kitt.extensions.builtin_plugins.rea.shutil.which", return_value="/opt/rea/bin/rea"):
        asyncio.run(handle.start())
    args, kwargs = ctx.mcp.registered[0]
    assert args == ("rea", "/opt/rea/bin/rea")
    assert kwargs["args"] == ["mcp"]
    assert kwargs["trust"] == "restricted"
    assert kwargs["max_output_bytes"] <= 4 * 1024 * 1024
    asyncio.run(handle.stop())
