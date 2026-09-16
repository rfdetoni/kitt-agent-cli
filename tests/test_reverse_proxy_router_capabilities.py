import json
import tempfile
import unittest
from pathlib import Path

from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.router.router import TaskRouter


class TestReverseProxyRouterCapabilities(unittest.TestCase):
    def test_load_migrates_reverse_proxy_supports_tools_to_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".kitt-router.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "context": {
                                "backend": "kitt-reverse-proxy",
                                "model": "gemini-web",
                                "base_url": "http://127.0.0.1:3000",
                                "protocol": "kitt-reverse-proxy",
                                "supports_tools": False,
                                "supports_json": True,
                            },
                            "execute": {
                                "backend": "kitt-reverse-proxy",
                                "model": "gemini-web",
                                "base_url": "http://127.0.0.1:3000",
                                "supports_tools": False,
                                "supports_json": True,
                            },
                        },
                        "routing": {
                            "context-gather": "context",
                            "code-generation": "execute",
                            "code-edit": "execute",
                            "chat": "execute",
                        },
                        "custom_providers": [],
                    }
                ),
                encoding="utf-8",
            )

            router = TaskRouter(str(root))

            self.assertTrue(router.config.profiles["context"].supports_tools)
            self.assertTrue(router.config.profiles["execute"].supports_tools)
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIs(persisted["profiles"]["context"]["supports_tools"], True)
            self.assertIs(persisted["profiles"]["execute"]["supports_tools"], True)

    def test_save_never_persists_reverse_proxy_as_tool_incapable(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = TaskRouter(tmp)
            router.config = RouterConfig(
                profiles={
                    "execute": ModelProfile(
                        backend="kitt-reverse-proxy",
                        model="gemini-web",
                        base_url="http://127.0.0.1:3000",
                        supports_tools=False,
                    )
                },
                routing={"chat": "execute"},
            )

            router.save_config(tmp)

            self.assertTrue(router.config.profiles["execute"].supports_tools)
            persisted = json.loads((Path(tmp) / ".kitt-router.json").read_text(encoding="utf-8"))
            self.assertIs(persisted["profiles"]["execute"]["supports_tools"], True)

    def test_protocol_alias_is_normalized_even_for_custom_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".kitt-router.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "execute": {
                                "backend": "custom",
                                "protocol": "kitt-reverse-proxy",
                                "model": "gemini-web",
                                "base_url": "http://127.0.0.1:3000",
                                "supports_tools": False,
                            }
                        },
                        "routing": {"chat": "execute"},
                        "custom_providers": [],
                    }
                ),
                encoding="utf-8",
            )

            router = TaskRouter(str(root))

            self.assertTrue(router.config.profiles["execute"].supports_tools)
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIs(persisted["profiles"]["execute"]["supports_tools"], True)


if __name__ == "__main__":
    unittest.main()
