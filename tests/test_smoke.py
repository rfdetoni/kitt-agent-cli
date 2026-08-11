import unittest

class TestSmoke(unittest.TestCase):
    def test_imports(self):
        import kitt.domain.entities
        import kitt.context_engine.engine
        import kitt.context_engine.parser
        import kitt.context_engine.graph
        import kitt.context_engine.agents_reader
        import kitt.context_filter.semantic_filter
        import kitt.context_filter.prompt_budget
        import kitt.context_filter.schema
        import kitt.context_filter.deterministic_extractor
        import kitt.context_filter.fallback
        import kitt.context_filter.context_planner
        import kitt.edit_format.parser
        import kitt.edit_format.applier
        import kitt.edit_format.changeset
        import kitt.router.router
        import kitt.router.model_selector
        import kitt.memory.memory_manager
        import kitt.skills.skill_manager
        import kitt.llm.client
        import kitt.tools.policy_engine
        import kitt.tools.agent_loop
        import kitt.tools.build_detector
        import kitt.tools.log_reducer
        import kitt.cli.repl
        import kitt.cli.main
        self.assertTrue(True)

if __name__ == '__main__':
    unittest.main()
