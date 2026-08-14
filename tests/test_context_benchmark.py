import unittest

from kitt.benchmarks.context_benchmark import run_once


class TestContextBenchmark(unittest.TestCase):
    def test_context_benchmark_reports_ready_when_fixture_fits_limit(self):
        result = run_once(10)

        self.assertEqual(result["files"], 10)
        self.assertEqual(result["cold"]["state"], "READY")
        self.assertEqual(result["warm"]["state"], "READY")
        self.assertEqual(result["top_path"], "pkg/mod_9.py")


if __name__ == "__main__":
    unittest.main()
