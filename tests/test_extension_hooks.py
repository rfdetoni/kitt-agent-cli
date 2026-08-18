"""Tests for HookRegistry priority ordering, interceptor pipelines, timeouts, and reentrancy protection."""
import asyncio
import unittest

from kitt.extensions.errors import HookReentrancyError, HookTimeoutError
from kitt.extensions.hooks.models import HookContext, HookResult
from kitt.extensions.hooks.registry import HookRegistry


class TestExtensionHooks(unittest.TestCase):

    def setUp(self):
        self.hooks = HookRegistry()

    def test_priority_ordering_and_pipeline_transformation(self):
        async def _test():
            def step_low(val):
                return val + ["low_priority"]

            def step_high(val):
                return val + ["high_priority"]

            def step_medium(val):
                return val + ["medium_priority"]

            self.hooks.register("test.pipe", step_low, priority=10)
            self.hooks.register("test.pipe", step_high, priority=100)
            self.hooks.register("test.pipe", step_medium, priority=50)

            res = await self.hooks.run_pipeline("test.pipe", [])
            self.assertEqual(res.value, ["high_priority", "medium_priority", "low_priority"])

        asyncio.run(_test())

    def test_stop_chain_interception(self):
        async def _test():
            def first(val):
                return HookResult(value=val + ["first"], stop=True)

            def second(val):
                return val + ["second"]

            self.hooks.register("test.stop", first, priority=100)
            self.hooks.register("test.stop", second, priority=50)

            res = await self.hooks.run_pipeline("test.stop", [])
            self.assertEqual(res.value, ["first"])
            self.assertTrue(res.stop)

        asyncio.run(_test())

    def test_hook_timeout_handling(self):
        async def _test():
            async def slow_handler(val):
                await asyncio.sleep(0.5)
                return val

            self.hooks.register("test.timeout", slow_handler, timeout_seconds=0.05, fail_closed=True)
            with self.assertRaises(HookTimeoutError):
                await self.hooks.run_pipeline("test.timeout", "initial")

        asyncio.run(_test())

    def test_reentrancy_protection(self):
        async def _test():
            async def loop_handler(val):
                return await self.hooks.run_pipeline("test.loop", val)

            self.hooks.register("test.loop", loop_handler, fail_closed=True)
            with self.assertRaises(HookReentrancyError):
                await self.hooks.run_pipeline("test.loop", "seed")

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
