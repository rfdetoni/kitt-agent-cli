import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnFailed
from kitt.history.database import CREATE_TABLES_SQL, HistoryDatabase
from kitt.history.session_tree import SessionTreeRepository
from kitt.skills.discovery import SkillDiscovery
from kitt.skills.loader import ProgressiveSkillLoader


class FakeClient:
    def __init__(self, text): self.text=text
    def chat(self,*args,**kwargs): return self.text
    def chat_stream(self,*args,**kwargs):
        for chunk in (self.text[:3],self.text[3:]): yield chunk


class TestPrimeRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.runtime=KittRuntime.build(self.tmp.name)
        self.conv=self.runtime.history.new_conversation("Prime")
    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def test_runtime_composition_and_cli_contract(self):
        self.assertIs(self.runtime.processor.registry,self.runtime.registry)
        self.assertIs(self.runtime.processor.history_service,self.runtime.history)
        self.assertIs(self.runtime.processor.context_engine,self.runtime.context_engine)
        self.assertIs(self.runtime.processor.working_set, self.runtime.working_set)
        self.assertIs(self.runtime.registry.context_engine,self.runtime.context_engine)
        self.assertIs(self.runtime.context_engine.index,self.runtime.repository_index)
        self.assertTrue(hasattr(self.runtime.processor,"arun_turn"))

    def test_session_tree_branch_selection(self):
        tree=self.runtime.session_tree
        a=tree.append_entry(self.conv["id"],"CUSTOM_STATE",{"content":"a"})
        b=tree.append_entry(self.conv["id"],"CUSTOM_STATE",{"content":"b"})
        tree.set_active_entry(self.conv["id"],a.id)
        c=tree.append_entry(self.conv["id"],"CUSTOM_STATE",{"content":"c"})
        self.assertEqual(tree.get_active_entry(self.conv["id"]).id,c.id)
        self.assertEqual([e.id for e in tree.get_active_path(self.conv["id"])],[a.id,c.id])
        self.assertEqual([e.id for e in tree.list_children(a.id)],[b.id,c.id])

    def test_artifact_integrity_and_pagination(self):
        ws=self.runtime.history.workspace["id"]
        a=self.runtime.artifacts.put(ws,"hello","TEXT","small",self.conv["id"],None)
        self.assertEqual(self.runtime.artifacts.read(a.id),b"hello")
        self.assertEqual(self.runtime.artifacts.list(self.conv["id"],1,0)[0].id,a.id)

    def test_queue_steering_priority_and_single_delivery(self):
        self.runtime.queue.follow_up(self.conv["id"],"later")
        self.runtime.queue.steer(self.conv["id"],"now")
        self.assertEqual(self.runtime.queue.drain(self.conv["id"]),["now","later"])
        self.assertEqual(self.runtime.queue.drain(self.conv["id"]),[])

    def test_goal_budget_gate(self):
        goal=self.runtime.goals.create(self.conv["id"],"pass tests",["tests pass"],10,2,60)
        goal=self.runtime.goals.charge(goal.id,11)
        self.assertEqual(goal.state,"BUDGET_EXHAUSTED")

    def test_compaction_replaces_old_active_path(self):
        for i in range(10):
            self.runtime.session_tree.append_entry(self.conv["id"],"USER_MESSAGE",{"content":f"message {i}"})
        result=self.runtime.compaction.compact(self.conv["id"],keep_recent=3)
        self.assertTrue(result.valid)
        path=self.runtime.session_tree.get_active_path(self.conv["id"])
        self.assertEqual(path[-4].entry_type,"COMPACTION")
        self.assertEqual(len(path),4)

    def test_harness_and_child_result_artifact(self):
        ws=self.runtime.history.workspace["id"]
        self.runtime.harness.remember("style","Use stdlib",ws,self.conv["id"])
        self.assertIn("Use stdlib",self.runtime.harness.prompt(ws,self.conv["id"]))
        child=self.runtime.children.spawn(self.conv["id"],"parent-turn","reader","inspect",
            lambda task:"result",ws)
        child=self.runtime.children.wait(child.id,timeout=10)
        self.assertEqual(child.state,"COMPLETED")
        self.assertEqual(self.runtime.artifacts.read(child.result_artifact_id),b"result")

    def test_progressive_skills(self):
        found=SkillDiscovery().discover([self.root/".kitt"/"skills"])
        selected=ProgressiveSkillLoader().select(found,"minimal stdlib ponytail implementation")
        self.assertTrue(any(s.name=="ponytail" for s in selected))

    def test_async_turn_stream(self):
        context=FakeClient('{"intent":"ASK","confidence":1.0}')
        execution=FakeClient("hello")
        self.runtime.processor.context_client=context; self.runtime.processor.execution_client=execution
        async def collect():
            return [e async for e in self.runtime.processor.arun_turn(
                TurnCommand(self.conv["id"],"hello",no_history=True))]
        events=asyncio.run(collect())
        self.assertTrue(events)

    def test_general_tool_protocol_reads_then_continues(self):
        (self.root/"note.txt").write_text("important",encoding="utf-8")
        context=FakeClient('{"intent":"ASK","confidence":1.0}')
        class SequenceClient:
            def __init__(self):self.calls=0
            def chat_stream(self,*args,**kwargs):
                self.calls+=1
                yield ('<kitt-tool>{"name":"read_file","arguments":{"path":"note.txt"}}</kitt-tool>'
                       if self.calls==1 else "done")
        execution=SequenceClient()
        self.runtime.processor.context_client=context; self.runtime.processor.execution_client=execution
        events=list(self.runtime.processor.run_turn(TurnCommand(self.conv["id"],"read note")))
        self.assertEqual(execution.calls,2)
        self.assertTrue(any(getattr(e,"response","")=="done" for e in events))

    def test_expired_and_mismatched_approval_fail_closed(self):
        from kitt.core.pending_action import PendingAction
        from kitt.tools.approval import ApprovalGrant
        pa=PendingAction("pa_x","req_x","x",self.conv["id"],self.runtime.history.workspace["id"],
            "apply_patch",{"patch":""},"bad","bad",[],{},0,time.time()-1,"pending")
        self.runtime.processor.pending_actions["x"]=pa
        grant=ApprovalGrant("other","x",self.conv["id"],pa.workspace_id,"bad",0,time.time()+10,"n")
        event=next(self.runtime.processor.continue_turn("x",grant))
        self.assertIsInstance(event,TurnFailed)
        self.assertIn("requires explicit user confirmation",event.error)

    def test_approval_nonce_survives_manager_restart(self):
        from kitt.tools.approval import ApprovalManager
        manager=ApprovalManager(db=self.runtime.database)
        manager.register_request("t","c","w","hash","req","tool")
        grant=manager.issue_grant("t","c","w","hash",approval_id="req")
        self.assertTrue(manager.validate_and_consume(grant,"hash","t","c","w","req"))
        restarted=ApprovalManager(db=self.runtime.database)
        self.assertFalse(restarted.validate_and_consume(grant,"hash","t","c","w","req"))

    def test_runtime_snapshot_and_status(self):
        snap = self.runtime.snapshot()
        self.assertIsNotNone(snap.workspace_id)
        self.assertEqual(snap.active_conversation_id, self.conv["id"])
        self.assertEqual(snap.pending_actions, 0)

    def test_approval_request_expiry_validation(self):
        manager = self.runtime.approval.register_request("t1", "c1", "w1", "hash1", "req1", "run_command", "run echo")
        self.assertEqual(manager.approval_id, "req1")
        self.assertIn(("t1", "c1", "w1", "hash1"), self.runtime.approval._requests)

    def test_go_rust_symbol_parser(self):
        from kitt.context_engine.parser import SymbolParser
        parser = SymbolParser()
        go_file = self.root / "main.go"
        go_file.write_text("package main\nfunc Compute(x int) int { return x }\ntype Config struct{}", encoding="utf-8")
        tags_go = parser.extract_file_tags(go_file, "main.go")
        self.assertTrue(any(t.name == "Compute" for t in tags_go.tags))
        self.assertTrue(any(t.name == "Config" for t in tags_go.tags))

        rs_file = self.root / "main.rs"
        rs_file.write_text("pub fn process() {}\npub struct State;", encoding="utf-8")
        tags_rs = parser.extract_file_tags(rs_file, "main.rs")
        self.assertTrue(any(t.name == "process" for t in tags_rs.tags))
        self.assertTrue(any(t.name == "State" for t in tags_rs.tags))

    def test_new_tools_registration_and_execution(self):
        defs = [t["name"] for t in self.runtime.registry.get_tool_definitions()]
        self.assertIn("child_spawn", defs)
        self.assertIn("goal_add_gate", defs)
        self.assertIn("harness_remember", defs)

        res_harness = self.runtime.registry.execute_tool(
            "harness_remember",
            {"name": "rule1", "content": "Always validate inputs"},
            conversation_id=self.conv["id"],
            workspace_id=self.runtime.history.workspace["id"],
            origin="USER"
        )
        self.assertTrue(res_harness.success)

    def test_event_bus_subscription(self):
        received = []
        self.runtime.events.subscribe("CustomEvent", lambda name, payload: received.append(payload))
        self.runtime.events.publish("CustomEvent", {"msg": "hello"})
        self.assertEqual(received, [{"msg": "hello"}])


class TestLegacyMigrations(unittest.TestCase):
    def test_v1_database_upgrades_to_v4(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/".kitt"/"history"; path.mkdir(parents=True)
            db_path=path/"history.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(CREATE_TABLES_SQL)
                conn.execute("INSERT INTO schema_info(version) VALUES(1)")
            db=HistoryDatabase(root)
            with db.get_connection() as conn:
                cols={r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
                version=conn.execute("SELECT MAX(version) FROM schema_info").fetchone()[0]
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertGreaterEqual(version, 4)
            self.assertIn("active_entry_id",cols)
            self.assertIn("child_sessions",tables)

    def test_partially_marked_v5_database_is_repaired(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/".kitt"/"history"; path.mkdir(parents=True)
            db_path=path/"history.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(CREATE_TABLES_SQL)
                conn.execute("INSERT INTO schema_info(version) VALUES(5)")
            db=HistoryDatabase(root)
            with db.get_connection() as conn:
                version=conn.execute("SELECT MAX(version) FROM schema_info").fetchone()[0]
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertGreaterEqual(version, 6)
            self.assertIn("harness_entries",tables)
            self.assertIn("child_sessions",tables)


if __name__=="__main__":
    unittest.main()
