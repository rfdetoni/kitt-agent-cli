from __future__ import annotations

import json
from dataclasses import asdict


class RuntimeRPC:
    """Line-oriented JSON-RPC adapter over the existing KittRuntime."""

    def __init__(self, runtime):
        self.runtime = runtime

    def _conversation(self, params):
        value = str(params.get("conversation_id") or "").strip()
        if value:
            return value
        active = self.runtime.history.get_active_read_only()
        if not active:
            active = self.runtime.history.get_or_create_active()
        return str(active["id"])

    def _runtime_control(self, method, params):
        # Local operator RPC only. No model-facing runtime operation exposes
        # these lifecycle methods, and explicit conversation scope is mandatory.
        conversation_id = str(params.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ValueError("runtime control requires an explicit conversation_id")
        conversation = self.runtime.history.repo.get_conversation(conversation_id)
        if not conversation or conversation.get("workspace_id") != self.runtime.workspace_id:
            raise PermissionError("conversation is outside the active workspace")
        manager = self.runtime.conversation_runtimes.for_conversation(conversation_id)
        config = params.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("runtime config must be an object")
        if method == "runtime.status":
            binding = manager.binding()
        elif method == "runtime.health":
            return manager.health()
        elif method == "runtime.operation_status":
            return manager.operation_status()
        elif method == "runtime.acknowledge_interrupted":
            if params.get("confirmed_backend_idle") is not True:
                raise ValueError("operator must confirm the backend is idle")
            manager.acknowledge_interrupted_operation(str(params["operation_id"]))
            return {"ok": True}
        elif method == "runtime.hibernate_idle":
            return {"hibernated": manager.hibernate_idle()}
        elif method == "runtime.provision":
            if manager.binding() is not None:
                raise RuntimeError("conversation already has a runtime; terminate it first")
            binding = manager.provision(str(params["backend"]), config)
        elif method == "runtime.attach":
            if manager.binding() is not None:
                raise RuntimeError("conversation already has a runtime")
            binding = manager.attach(str(params["backend"]), str(params["runtime_id"]), config)
        elif method == "runtime.pause":
            binding = manager.pause()
        elif method == "runtime.resume":
            binding = manager.resume()
        elif method == "runtime.snapshot":
            return manager.snapshot_state()
        elif method == "runtime.artifact_push":
            return manager.push_artifact(
                self.runtime.artifacts, str(params["artifact_id"]),
                allow_sensitive=params.get("allow_sensitive") is True,
            )
        elif method == "runtime.artifact_pull":
            return manager.pull_artifact(
                self.runtime.artifacts, str(params["sha256"]),
                summary=str(params.get("summary") or "Remote runtime artifact"),
            )
        elif method == "runtime.terminate":
            manager.terminate()
            return {"ok": True}
        else:
            raise KeyError(f"unknown RPC method: {method}")
        if binding is None:
            return {"binding": None, "state": "UNBOUND"}
        return {"binding": {**asdict(binding), "backend": binding.backend.value}}

    def dispatch(self, method, params):
        if method.startswith("runtime."):
            return self._runtime_control(method, params)
        conversation_id = self._conversation(params)

        if method == "agents.list":
            return self.runtime.children.observe_agents(conversation_id)
        if method == "agents.send":
            message, mode = self.runtime.children.send_agent_message(
                conversation_id,
                sender=str(params.get("sender") or "parent"),
                recipient=str(params.get("recipient") or ""),
                message=params.get("message", ""),
                delivery_mode=str(params.get("delivery_mode") or "AUTO"),
            )
            return {"message_id": message.id, "delivery_mode": mode}
        if method == "agents.passivate":
            return {
                "ok": self.runtime.children.passivate(
                    str(params["child_id"]),
                    conversation_id=conversation_id,
                    workspace_id=self.runtime.workspace_id,
                )
            }
        if method == "agents.revive":
            child = self.runtime.children.revive(
                str(params["child_id"]),
                task=params.get("task"),
                conversation_id=conversation_id,
                workspace_id=self.runtime.workspace_id,
            )
            return {"id": child.id, "state": child.state}

        if method == "schedule.create":
            schedule_id = self.runtime.wake_scheduler.schedule(
                workspace_id=self.runtime.workspace_id,
                conversation_id=conversation_id,
                prompt=str(params["prompt"]),
                run_at=params.get("run_at"),
                interval_seconds=params.get("interval_seconds"),
                cron_expr=params.get("cron_expr"),
            )
            return {"id": schedule_id}
        if method == "schedule.list":
            return self.runtime.wake_scheduler.list(conversation_id)
        if method == "schedule.cancel":
            return {"ok": self.runtime.wake_scheduler.cancel(str(params["id"]))}
        if method == "heartbeat.set":
            schedule_id = self.runtime.wake_scheduler.set_heartbeat(
                workspace_id=self.runtime.workspace_id,
                conversation_id=conversation_id,
                prompt=str(
                    params.get("prompt")
                    or "Continue the active goal from current evidence."
                ),
                interval_seconds=float(params.get("interval_seconds") or 60),
            )
            return {"id": schedule_id}

        if method == "refine.prepare":
            refinement_id, preview = self.runtime.refiner.prepare(
                params.get("proposal") or {},
                workspace_id=self.runtime.workspace_id,
                conversation_id=conversation_id,
            )
            return {"id": refinement_id, "preview": preview}
        if method == "refine.apply":
            return self.runtime.refiner.apply(str(params["id"]))
        if method == "refine.rollback":
            return {"ok": self.runtime.refiner.rollback(str(params["id"]))}

        if method == "session.export":
            return self.runtime.history.export_conversation(
                conversation_id,
                fmt=str(params.get("format") or "json"),
                include_tree=bool(params.get("include_tree", True)),
            )

        raise KeyError(f"unknown RPC method: {method}")

    def handle(self, request):
        request_id = request.get("id")
        try:
            method = str(request.get("method") or "").strip()
            params = request.get("params") or {}
            if not method or not isinstance(params, dict):
                raise ValueError("RPC request requires method and object params")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": self.dispatch(method, params),
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    def serve_lines(self, source, sink):
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            else:
                response = self.handle(request)
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
