import tempfile
import time
import unittest
from pathlib import Path

from kitt.index.repository import RepositoryIndex
from kitt.history.database import HistoryDatabase


class TestScaleBenchmark(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db = HistoryDatabase(str(self.root))

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_repository_index_scale_1k_files(self):
        """Synthetic repo scale benchmark (1,000 files)."""
        num_files = 1000
        src_dir = self.root / "src"
        src_dir.mkdir(parents=True)

        for i in range(num_files):
            file_path = src_dir / f"module_{i:04d}.py"
            file_path.write_text(
                f"""
# Module {i}
def compute_item_{i:04d}(val: int) -> int:
    return val * {i}

class Handler_{i:04d}:
    def process(self):
        return compute_item_{i:04d}(10)
""",
                encoding="utf-8",
            )

        start = time.perf_counter()
        idx = RepositoryIndex(str(self.root))
        stats = idx.build_or_update()
        duration = time.perf_counter() - start

        # Assertions: 1k files indexed under 5 seconds
        self.assertGreaterEqual(stats.get("scanned", 0), num_files)
        self.assertLess(duration, 5.0, f"Indexing 1k files took {duration:.2f}s (must be < 5s)")

        # Query performance
        q_start = time.perf_counter()
        loc = idx.find_symbol_location("compute_item_0050")
        q_duration = time.perf_counter() - q_start
        self.assertIsNotNone(loc)
        self.assertIn("0050", loc["path"])
        self.assertLess(q_duration, 0.1, f"Search query took {q_duration:.4f}s (must be < 100ms)")
        idx.close()
