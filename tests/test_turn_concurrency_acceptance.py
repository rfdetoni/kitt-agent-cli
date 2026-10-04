from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnCompleted, TurnStarted
from kitt.core.turn_processor import TurnProcessor
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.native.coordinator import LeaseRequest, WorkspaceCoordinator


async def _collect(stream):
    events = []
    async for event in stream:
        events.append(event)
    return events


class TurnConcurrencyAcceptanceTests(unittest.TestCase):
    def test_same_conversation_turns_are_serialized(self):
        async def scenario():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                processor = TurnProcessor(root_dir=tmp)
                release_first = threading.Event()
                started: list[str] = []

                def fake_run_turn(cmd):
                    started.append(cmd.turn_id)
                    yield TurnStarted(
                        turn_id=cmd.turn_id,
                        conversation_id=cmd.conversation_id,
                        prompt=cmd.prompt,
                    )
                    if cmd.turn_id == "turn-1":
                        release_first.wait(timeout=5)
                    yield TurnCompleted(response=cmd.turn_id)

                processor.run_turn = fake_run_turn
                try:
                    first = processor.arun_turn(
                        TurnCommand("conv-1", "first", turn_id="turn-1")
                    )
                    self.assertIsInstance(await anext(first), TurnStarted)

                    second = processor.arun_turn(
                        TurnCommand("conv-1", "second", turn_id="turn-2")
                    )
                    second_started = asyncio.create_task(anext(second))
                    await asyncio.sleep(0.1)
                    self.assertFalse(second_started.done())
                    self.assertEqual(started, ["turn-1"])

                    release_first.set()
                    await asyncio.wait_for(_collect(first), timeout=1)
                    self.assertIsInstance(
                        await asyncio.wait_for(second_started, timeout=1),
                        TurnStarted,
                    )
                    self.assertEqual(started, ["turn-1", "turn-2"])
                    await asyncio.wait_for(_collect(second), timeout=1)
                finally:
                    release_first.set()
                    processor.close()

        asyncio.run(scenario())

    def test_different_conversations_execute_in_parallel(self):
        async def scenario():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                processor = TurnProcessor(root_dir=tmp)
                release = threading.Event()

                def fake_run_turn(cmd):
                    yield TurnStarted(
                        turn_id=cmd.turn_id,
                        conversation_id=cmd.conversation_id,
                        prompt=cmd.prompt,
                    )
                    release.wait(timeout=5)
                    yield TurnCompleted(response=cmd.turn_id)

                processor.run_turn = fake_run_turn
                try:
                    first = processor.arun_turn(
                        TurnCommand("conv-a", "first", turn_id="turn-a")
                    )
                    second = processor.arun_turn(
                        TurnCommand("conv-b", "second", turn_id="turn-b")
                    )
                    first_started, second_started = await asyncio.wait_for(
                        asyncio.gather(anext(first), anext(second)),
                        timeout=1,
                    )
                    self.assertIsInstance(first_started, TurnStarted)
                    self.assertIsInstance(second_started, TurnStarted)
                finally:
                    release.set()
                    await asyncio.gather(
                        _collect(first),
                        _collect(second),
                        return_exceptions=True,
                    )
                    processor.close()

        asyncio.run(scenario())

    def test_conflicting_resource_waiters_are_fifo_without_deadlock(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = HistoryDatabase(tmp)
            repo = HistoryRepository(db)
            workspace = repo.get_or_create_workspace(tmp)
            coordinator = WorkspaceCoordinator(
                tmp,
                tmp,
                db,
                workspace["id"],
            )
            acquired: list[str] = []
            errors: list[BaseException] = []
            resource = LeaseRequest("path:shared.txt", "WRITE", "test")

            coordinator.acquire_many([resource], "owner-0")

            def waiter(owner: str):
                try:
                    coordinator.acquire_many(
                        [resource],
                        owner,
                        wait_timeout=3,
                    )
                    acquired.append(owner)
                    time.sleep(0.03)
                    coordinator.release_owner(owner)
                except BaseException as exc:  # test thread must surface failures
                    errors.append(exc)

            first = threading.Thread(target=waiter, args=("owner-1",))
            first.start()

            deadline = time.time() + 1
            while time.time() < deadline:
                with db.get_connection() as conn:
                    queued = conn.execute(
                        """SELECT 1 FROM coordination_wait_queue
                           WHERE workspace_id=? AND owner_id=?""",
                        (workspace["id"], "owner-1"),
                    ).fetchone()
                if queued:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(queued)

            second = threading.Thread(target=waiter, args=("owner-2",))
            second.start()
            time.sleep(0.05)
            coordinator.release_owner("owner-0")

            first.join(timeout=4)
            second.join(timeout=4)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(acquired, ["owner-1", "owner-2"])
            db.close()


if __name__ == "__main__":
    unittest.main()
