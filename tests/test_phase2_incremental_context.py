import unittest
import tempfile
from pathlib import Path
from kitt.context_engine.parser import SymbolParser
from kitt.context_engine.graph import ContextRanker
from kitt.context_engine.engine import ContextEngine

class TestPhase2IncrementalContext(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.parser = SymbolParser()
        self.ranker = ContextRanker()
        self.engine = ContextEngine()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_java_and_python_parser(self):
        py_file = self.root_path / "app.py"
        py_file.write_text("class MainApp:\n  def process(self):\n    helper_func()\n", encoding='utf-8')

        java_file = self.root_path / "UserService.java"
        java_file.write_text("public class UserService {\n  public void findUser() {\n    Repository.query();\n  }\n}\n", encoding='utf-8')

        py_tags = self.parser.extract_file_tags(py_file, "app.py")
        java_tags = self.parser.extract_file_tags(java_file, "UserService.java")

        self.assertIsNotNone(py_tags)
        self.assertTrue(any(t.name == "MainApp" for t in py_tags.tags))

        self.assertIsNotNone(java_tags)
        self.assertTrue(any(t.name == "UserService" for t in java_tags.tags))
        self.assertTrue(any(t.name == "findUser" for t in java_tags.tags))

    def test_pagerank_graph_ranking(self):
        f1 = self.root_path / "controller.py"
        f1.write_text("from service import UserService\nclass Controller:\n  def run(self):\n    UserService()\n", encoding='utf-8')

        f2 = self.root_path / "service.py"
        f2.write_text("class UserService:\n  def find(self):\n    pass\n", encoding='utf-8')

        ft1 = self.parser.extract_file_tags(f1, "controller.py")
        ft2 = self.parser.extract_file_tags(f2, "service.py")

        ranked = self.ranker.rank_files([ft1, ft2], focus_files=["controller.py"], focus_symbols=["UserService"])
        self.assertTrue(len(ranked) > 0)
        self.assertEqual(ranked[0], "controller.py")

    def test_index_caching(self):
        f = self.root_path / "module.py"
        f.write_text("def foo(): pass\n", encoding='utf-8')

        # First run populates cache
        b1 = self.engine.get_relevant_context("foo", root_dir=self.tmp_dir.name)
        self.assertTrue(len(b1) > 0)

        cache_file = self.root_path / ".kitt" / "cache" / "index_cache.json"
        self.assertTrue(cache_file.exists())

        # Second run without changes reuses cache
        b2 = self.engine.get_relevant_context("foo", root_dir=self.tmp_dir.name)
        self.assertTrue(len(b2) > 0)

if __name__ == '__main__':
    unittest.main()
