import unittest

from kitt.evals.retrieval import run_eval


class TestRetrievalEval(unittest.TestCase):
    def test_retrieval_eval_reports_ground_truth_metrics(self):
        result = run_eval()

        self.assertEqual(result["cases"], 5)
        self.assertGreaterEqual(result["recall_at_5"], 1.0)
        self.assertGreaterEqual(result["mrr"], 0.9)
        self.assertEqual(result["index"]["schema_version"], "2")
        self.assertTrue(all(item["ok"] for item in result["details"]))


if __name__ == "__main__":
    unittest.main()
