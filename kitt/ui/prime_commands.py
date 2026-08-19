from __future__ import annotations

import json


async def handle_prime_command(app, command_id: str, arg: str) -> bool:
    """Handle Prime Architecture commands. Returns True only when implemented."""
    conv = app.runtime.history.get_or_create_active()
    conv_id = conv["id"]

    if command_id == "child_inspect":
        if not arg:
            app._show_result("Usage: /child-inspect <id>")
            return True
        child = app.runtime.children.inspect(arg.strip(), conversation_id=conv_id, workspace_id=app.runtime.workspace_id)
        app._show_result(json.dumps(child.__dict__, ensure_ascii=False, default=str, indent=2) if child else "Child not found.")
        return True

    if command_id == "child_message":
        child_id, sep, message = arg.partition(" ")
        if not sep:
            app._show_result("Usage: /child-msg <id> <message>")
            return True
        child = app.runtime.children.inspect(child_id, conversation_id=conv_id, workspace_id=app.runtime.workspace_id)
        if not child:
            app._show_result("Child not found.")
            return True
        msg = app.runtime.children.send_message(
            conversation_id=conv_id, parent_id=conv_id, child_id=child_id,
            sender_id=conv_id, recipient_id=child_id, payload={"text": message},
        )
        app._show_result(f"Message sent: {msg.id}")
        return True

    if command_id == "child_retain":
        ok = app.runtime.children.retain(arg.strip(), conversation_id=conv_id, workspace_id=app.runtime.workspace_id)
        app._show_result("Child retained." if ok else "Child not found or not retainable.")
        return True

    if command_id == "child_cancel":
        ok = app.runtime.children.cancel(arg.strip(), conversation_id=conv_id, workspace_id=app.runtime.workspace_id)
        app._show_result("Child cancelled." if ok else "Child not found or already terminal.")
        return True

    if command_id == "goal_pause":
        goal_id = arg.strip()
        goal = app.runtime.goals.pause(goal_id, conversation_id=conv_id)
        app._show_result(f"Goal {goal_id} paused." if goal else "Goal not found.")
        return True

    if command_id == "goal_resume":
        goal_id = arg.strip()
        goal = app.runtime.goals.resume(goal_id, conversation_id=conv_id)
        app._show_result(f"Goal {goal_id} resumed." if goal else "Goal not found.")
        return True

    if command_id == "attach":
        if not arg:
            app._show_result("Usage: /attach <session-id>")
            return True
        session_id = arg.strip()
        conv = app.runtime.history.repo.get_conversation(session_id)
        if not conv or conv.get("workspace_id") != app.runtime.workspace_id:
            app._show_result("Session not found in current workspace.")
            return True
        ok = await app.bridge.attach_session(session_id)
        if ok:
            app.runtime.history.active_conversation = conv
        app._show_result(f"Attached to {session_id}." if ok else "Attach failed.")
        return True

    if command_id == "detach":
        await app.bridge.detach_session()
        app._show_result("Detached from daemon session.")
        return True

    if command_id == "runtime_state":
        snap = app.runtime.snapshot()
        app._show_result(json.dumps(snap.__dict__, ensure_ascii=False, default=str, indent=2))
        return True

    if command_id == "artifact":
        aid = arg.strip()
        if not aid:
            app._show_result("Usage: /artifact <id>")
            return True
        try:
            text = app.runtime.registry.artifact_tools.read_text(
                aid, workspace_id=app.runtime.workspace_id, conversation_id=conv_id
            )
            app._show_result(text)
        except Exception as exc:
            app._show_result(f"Artifact unavailable: {exc}")
        return True

    return False
