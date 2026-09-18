import unittest
from types import SimpleNamespace

from kitt.context.query_plan import QueryPlanner
from kitt.context.rag.native_search import NativeLexicalRetriever


class _FakeNativeEngine:
    status = SimpleNamespace(available=True, backend="rust")

    def search(self, *args, **kwargs):
        return {
            "hits": [
                {
                    "path": "kitt-reverse-proxy-full.log",
                    "line": 2,
                    "text": "MeuFazTudo trace",
                    "before": [],
                    "after": [],
                    "score": 1.0,
                },
                {
                    "path": ".kitt/logs/agent-cli.log",
                    "line": 3,
                    "text": "MeuFazTudo internal trace",
                    "before": [],
                    "after": [],
                    "score": 1.0,
                },
                {
                    "path": "backend/src/main/java/com/meufaztudo/App.java",
                    "line": 1,
                    "text": "class MeuFazTudoApp {}",
                    "before": [],
                    "after": [],
                    "score": 1.0,
                },
            ]
        }


class NativeSearchLogFilterTests(unittest.TestCase):
    def test_native_context_excludes_logs_and_kitt_internal_paths(self):
        retriever = NativeLexicalRetriever(_FakeNativeEngine())
        candidates = retriever.retrieve(
            "MeuFazTudo",
            QueryPlanner.plan("MeuFazTudo"),
            working_set_paths=set(),
            max_results=10,
            context_lines=1,
            token_budget=1000,
        )

        self.assertEqual(
            [candidate.path for candidate in candidates],
            ["backend/src/main/java/com/meufaztudo/App.java"],
        )


if __name__ == "__main__":
    unittest.main()
