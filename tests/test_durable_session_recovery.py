import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from kitt.history.service import HistoryService
from kitt.history.context_builder import HistoryContextBuilder
from kitt.ui import session_commands


class DurableSessionRecoveryTests(unittest.TestCase):
    def test_resume_old_conversation_by_id_and_index_with_workspace_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = HistoryService(tmp)
            oldest = service.new_conversation("Windows API Observatory")
            for index in range(65):
                service.new_conversation(f"Task {index}")
            self.assertEqual(service.resume_conversation(oldest["id"][:9])["id"], oldest["id"])
            self.assertEqual(service.resume_conversation("66")["id"], oldest["id"])
            self.assertIsNone(service.resume_conversation("999"))
            self.assertIsNone(service.resume_conversation("abc"))
            other = service.new_conversation("Current")
            with tempfile.TemporaryDirectory() as other_tmp:
                other_service = HistoryService(other_tmp)
                self.assertIsNone(other_service.resume_conversation(other["id"]))
                other_service.close()
            service.close()

    def test_big_prompt_is_stored_and_included_as_a_bounded_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = HistoryService(tmp)
            conversation = first.new_conversation()
            payload = "# Windows API Observatory\n" + "A" * 45000 + "\nFINAL: produce auditable evidence"
            first.repo.save_message(conversation["id"], "turn-one", "user", payload)
            self.assertEqual(first.list_history()[0]["title"], "Windows API Observatory")
            first.close()
            second = HistoryService(tmp)
            restored = second.get_or_create_active()
            self.assertEqual(restored["id"], conversation["id"])
            self.assertEqual(second.repo.get_messages_for_conversation(restored["id"])[0]["content"], payload)
            excerpt = HistoryContextBuilder(second.tree).build(restored["id"], max_tokens=1200)
            self.assertIn("Windows API Observatory", excerpt)
            self.assertIn("FINAL: produce auditable evidence", excerpt)
            self.assertLessEqual(len(excerpt), 4800)
            second.close()


class SessionMenuRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_does_not_claim_success_when_switch_is_rejected(self):
        old = {"id":"old","title":"Old"}
        new = {"id":"new","title":"New"}
        history = SimpleNamespace(
            active_conversation=old,
            resume_conversation=lambda key: new
        )
        ui = SimpleNamespace(
            state=SimpleNamespace(is_thinking=False),
            bridge=SimpleNamespace(is_active=False),
            runtime=SimpleNamespace(history=history),
            _run_blocking=AsyncMock(side_effect=lambda fn,*args: fn(*args)),
            _load_conversation=AsyncMock(return_value=False),
            _show_result=MagicMock()
        )
        await session_commands.handle_resume_command(ui, "new")
        self.assertIs(history.active_conversation, old)
        self.assertIn("not completed", ui._show_result.call_args.args[0])

    async def test_fork_switches_to_cloned_transcript(self):
        conv = {"id":"fork","title":"Windows (Fork)"}
        history = SimpleNamespace(fork_conversation=lambda title_suffix: conv)
        ui = SimpleNamespace(
            runtime=SimpleNamespace(history=history),
            _run_blocking=AsyncMock(side_effect=lambda fn,**kwargs: fn(**kwargs)),
            _load_conversation=AsyncMock(return_value=True),
            _show_result=MagicMock()
        )
        await session_commands.handle_fork_command(ui, "")
        ui._load_conversation.assert_awaited_once_with(conv)
