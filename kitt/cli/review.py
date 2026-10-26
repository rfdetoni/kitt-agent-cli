"""Human-facing review CLI. Policy never grants permissions through a flag."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

from kitt.config.settings import RuntimeConfig
from kitt.core.runtime import KittRuntime
from kitt.runtime.state import RuntimeStateStore
from kitt.runtime.workspace_review import WorkspaceReview
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext


def run_review_command(args) -> int:
    config = replace(
        RuntimeConfig.from_env(), daemon_enabled=False, frontend_only=False,
        scheduler_enabled=False, wake_scheduler_enabled=False,
    )
    runtime = KittRuntime.build(args.root, config=config)
    try:
        conversation_id = args.conversation_id
        if conversation_id is not None:
            record = runtime.history.repo.get_conversation(conversation_id)
            if not record or record.get("workspace_id") != runtime.workspace_id:
                raise ValueError("conversation not found in current workspace")
        else:
            active = runtime.history.get_active_read_only()
            if not active:
                raise ValueError("no active workspace conversation")
            conversation_id = active["id"]

        state = RuntimeStateStore(runtime.database, runtime.workspace_id, conversation_id)
        service = WorkspaceReview(SimpleNamespace(root=runtime.canonical_root, state=state))
        context = ExecutionSecurityContext.create_user_context(
            workspace_id=runtime.workspace_id,
            conversation_id=conversation_id,
            capabilities={CAP_REPO_READ, CAP_REPO_WRITE},
        )
        context = replace(context, principal_type="LOCAL_OPERATOR",
                          principal_id="local_review_cli", origin="HUMAN_DIRECT")

        if args.review_action == "list":
            reviews = []
            for item in state.list_keys():
                key = item["key"]
                if not key.startswith("review_"):
                    continue
                try:
                    review = service._load(key, context)
                    reviews.append({
                        "review_id": key, "status": review["status"],
                        "paths": [entry["path"] for entry in review["files"]],
                    })
                except (PermissionError, ValueError):
                    continue
            print(json.dumps(reviews[:100], ensure_ascii=False, indent=2))
            return 0

        if not args.review_id:
            raise ValueError("review ID required")
        manifest = service.authorization_manifest({"review_id": args.review_id}, context)
        if args.review_action == "diff":
            view = service.diff({"review_id": args.review_id}, context)
            print("Review:", view["review_id"], "Status:", view["status"])
            for change in view["changes"]:
                print(change["diff"][:16384])
            return 0

        for item in manifest["files"]:
            verdict = runtime.policy.evaluate_tool(
                "write_file",
                {"path": item["path"], "review_id": manifest["review_id"],
                 "before_sha256": item["before_sha256"],
                 "after_sha256": item["after_sha256"]},
                origin="USER", conversation_id=conversation_id,
                workspace_id=runtime.workspace_id,
                executable_identity="LOCAL_OPERATOR:local_review_cli",
            )
            if verdict != "ALLOW":
                raise PermissionError(
                    "Host policy requires explicit per-file ALLOW before mutation: " + item["path"]
                )

        if not args.yes:
            raise PermissionError("Review mutation requires explicit --yes and per-file ALLOW")
        print("Authorizing:", args.review_action, args.review_id)
        for item in manifest["files"]:
            print("  ", item["path"])
        action = service.apply if args.review_action == "apply" else service.rewind
        print(json.dumps(action({"review_id": args.review_id}, context), ensure_ascii=False))
        return 0
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"Review failed: {exc}", file=sys.stderr)
        return 2
    finally:
        runtime.close()
