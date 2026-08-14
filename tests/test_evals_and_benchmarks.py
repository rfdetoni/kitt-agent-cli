import unittest
from kitt.router.models import ModelCapabilities
from kitt.evals.corpus import EvalRunner
from kitt.benchmarks.context_benchmark import run_once
from kitt.evals.retrieval import run_eval

class TestEvalsAndBenchmarks(unittest.TestCase):
    def test_eval_runner_accuracy_and_ablations(self):
        caps = {
            "local_small": ModelCapabilities(
                profile_name="local_small", tier="small", input_context_limit=8192, max_output_tokens=2048,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.9, code_edit_score=0.8,
                reasoning_score=0.75, languages=("py",), is_local=True, privacy_class="local"
            ),
            "cloud_large": ModelCapabilities(
                profile_name="cloud_large", tier="large", input_context_limit=32768, max_output_tokens=4096,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.95, code_edit_score=0.95,
                reasoning_score=0.95, languages=("py",), is_local=False, privacy_class="cloud"
            )
        }

        runner = EvalRunner()
        metrics = runner.run_routing_eval(caps)
        self.assertGreaterEqual(metrics["intent_accuracy"], 0.75)
        self.assertGreaterEqual(metrics["routing_accuracy"], 0.75)

        ablations = runner.run_ablations(caps)
        self.assertEqual(len(ablations), 5)
        self.assertIn("1_deterministic", ablations)
        self.assertIn("5_large_direct", ablations)

    def test_retrieval_eval_and_context_benchmark_entrypoints(self):
        eval_metrics = run_eval()
        bench = run_once(10)

        self.assertEqual(eval_metrics["recall_at_5"], 1.0)
        self.assertEqual(bench["files"], 10)
        self.assertEqual(bench["top_path"], "pkg/mod_9.py")

if __name__ == "__main__":
    unittest.main()
