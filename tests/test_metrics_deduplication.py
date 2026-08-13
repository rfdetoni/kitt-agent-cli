import unittest
from kitt.metrics.collector import MetricsCollector
from kitt.metrics.models import TurnMetrics

class TestMetricsDeduplication(unittest.TestCase):
    def test_deduplicate_turn_records(self):
        collector = MetricsCollector()
        m1 = TurnMetrics(turn_id="turn_101", conversation_id="conv_1", actual_input_tokens=100, actual_output_tokens=20)
        m2 = TurnMetrics(turn_id="turn_101", conversation_id="conv_1", actual_input_tokens=100, actual_output_tokens=20)
        
        collector.record(m1)
        collector.record(m2)

        summary = collector.get_summary()
        self.assertEqual(summary["total_turns"], 1)
        self.assertEqual(collector.rejected_duplicates, 1)
        collector.close()

if __name__ == "__main__":
    unittest.main()
