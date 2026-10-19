from __future__ import annotations

import argparse
import asyncio
import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version
import os
import sys
from dataclasses import replace
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.logging import configure_logging, debug_event, get_logger
from kitt.history.migrations import IncompatibleSchemaError
from kitt.update_check import notify_if_update_available
from kitt.ui.capabilities import create_backend
from kitt.ui.fallback import HeadlessUI


logger = get_logger(__name__)


def _agent_version() -> str:
    try:
        return package_version("kitt-agent-cli")
    except PackageNotFoundError:
        from kitt import __version__
        return __version__


def _log_runtime_identity() -> None:
    debug_event(
        logger,
        "agent.startup",
        version=_agent_version(),
        package_path=str(Path(__file__).resolve()),
        executable=str(Path(sys.executable).resolve()),
        pid=os.getpid(),
    )


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _missing_companion(feature: str, package: str) -> int:
    print(
        f"{feature} requires the separately owned {package} companion. "
        "Use the K.I.T.T. ecosystem/Agent installer or install that companion package.",
        file=sys.stderr,
    )
    return 2


def _default_log_level() -> int:
    raw = os.getenv("KITT_LOG_LEVEL", "").strip()
    if not raw:
        return 1 if os.getenv("KITT_DEBUG_LOG", "").strip() else 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("KITT_LOG_LEVEL must be 0, 1 or 2") from exc
    if value not in {0, 1, 2}:
        raise ValueError("KITT_LOG_LEVEL must be 0, 1 or 2")
    return value


def _configure_debug_log(args) -> Path | None:
    level = int(getattr(args, "log_level", 0))
    requested = str(getattr(args, "log_file", "") or "").strip()
    if not requested:
        requested = os.getenv("KITT_LOG_FILE", "").strip() or os.getenv("KITT_DEBUG_LOG", "").strip()
    if level > 0 and not requested:
        requested = str(Path(args.root).resolve() / ".kitt" / "logs" / "agent-cli.log")

    path = configure_logging(level=level, path=requested or None)
    os.environ["KITT_LOG_LEVEL"] = str(level)
    log_content = str(getattr(args, "log_content", "") or "").strip().lower()
    if not log_content:
        log_content = "full" if level >= 2 else "metadata"
    os.environ["KITT_LOG_CONTENT"] = log_content
    if path is not None:
        os.environ["KITT_LOG_FILE"] = str(path)
        os.environ["KITT_DEBUG_LOG"] = str(path)
    debug_event(
        logger,
        "cli.logging.configured",
        level=level,
        content=log_content,
        path=str(path) if path is not None else None,
        root=str(Path(args.root).resolve()),
    )
    return path


def _add_common_options(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    """Add position-independent CLI options.

    Subparsers suppress defaults so values parsed before the subcommand are not
    overwritten. The root parser owns canonical defaults.
    """

    default = (lambda value: value) if defaults else (lambda _value: argparse.SUPPRESS)
    parser.add_argument("--root", default=default("."), help="Workspace root")
    parser.add_argument(
        "--no-history",
        action="store_true",
        default=default(False),
        help="Disable persistent history/state for this invocation",
    )
    parser.add_argument(
        "--ui",
        choices=["auto", "tui", "plain"],
        default=default("auto"),
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        default=default(False),
        help="Alias for --ui plain",
    )
    parser.add_argument(
        "--no-animation",
        action="store_true",
        default=default(False),
    )
    parser.add_argument(
        "--log-level",
        type=int,
        choices=[0, 1, 2],
        default=default(_default_log_level()),
        help="Logging: 0=normal, 1=debug, 2=full trace",
    )
    parser.add_argument(
        "--log-file",
        default=default(
            os.getenv("KITT_LOG_FILE", "").strip()
            or os.getenv("KITT_DEBUG_LOG", "").strip()
            or None
        ),
        help="Diagnostic log file (default with level>0: <workspace>/.kitt/logs/agent-cli.log)",
    )
    parser.add_argument(
        "--log-content",
        choices=["none", "metadata", "full"],
        default=default(os.getenv("KITT_LOG_CONTENT", "").strip() or None),
        help=(
            "Managed reverse-proxy log content policy "
            "(default: full with --log-level 2, otherwise metadata)"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common, defaults=False)

    parser = argparse.ArgumentParser(
        prog="kitt",
        description="K.I.T.T. autonomous coding agent",
    )
    _add_common_options(parser, defaults=True)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_agent_version()}",
    )
    parser.add_argument("-p", "--print", dest="prompt", help="Print one response and exit")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Plan and run -p as a durable task contract",
    )
    parser.add_argument(
        "--contract-yes",
        action="store_true",
        help="Start a newly planned contract without a separate confirmation step",
    )
    parser.add_argument(
        "--contract-resume",
        metavar="GOAL_ID",
        default=None,
        help="Resume a blocked or paused contract goal",
    )
    parser.add_argument(
        "--allow",
        dest="contract_allow",
        default=None,
        help="Contract capabilities: comma-separated read,write,run,all",
    )
    parser.add_argument(
        "--contract-max-attempts",
        type=int,
        default=5,
        help="Maximum scheduler attempts per contract item (default: 5)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    models_parser = subparsers.add_parser(
        "models",
        parents=[common],
        help="List and inspect models from catalog/providers",
    )
    models_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider name (e.g. openai, anthropic, ollama)",
    )
    models_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh catalog from Models.dev",
    )
    models_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Display full model metadata and capabilities",
    )

    auth_parser = subparsers.add_parser(
        "auth",
        parents=[common],
        help="Manage provider authentication credentials",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_action", help="Auth action")
    auth_login = auth_sub.add_parser("login", parents=[common], help="Log in to a provider")
    auth_login.add_argument("provider", help="Provider to authenticate (e.g. openai, anthropic)")
    auth_login.add_argument("--method", default="api_key", help="Auth method (api_key, env, session)")
    auth_sub.add_parser("list", parents=[common], help="List authenticated providers")
    auth_logout = auth_sub.add_parser("logout", parents=[common], help="Log out from a provider")
    auth_logout.add_argument("provider", help="Provider to log out")

    plugins_parser = subparsers.add_parser(
        "plugins",
        parents=[common],
        help="Manage plugins and extensions",
    )
    plugins_parser.add_argument(
        "plugin_action",
        nargs="?",
        default="list",
        choices=["list", "inspect", "enable", "disable", "reload", "trust", "untrust"],
        help="Plugin action",
    )
    plugins_parser.add_argument("plugin_name", nargs="?", default=None, help="Target plugin name")

    agents_parser = subparsers.add_parser(
        "agents", parents=[common], help="Manage specialist agent personas"
    )
    agents_parser.add_argument("agent_action", nargs="?", default="list",
        choices=["list", "create", "edit", "inspect", "select", "enable", "disable", "model"])
    agents_parser.add_argument("agent_name", nargs="?", default=None)
    agents_parser.add_argument("--role", default="investigator",
        choices=["code", "creative", "investigator", "critic", "tester"])
    agents_parser.add_argument("--persona", default=None)
    agents_parser.add_argument("--display-name", default=None)
    agents_parser.add_argument("--tags", default="")
    agents_parser.add_argument("--mode", default="auto", choices=["auto", "preferred", "fixed"])
    agents_parser.add_argument("--primary", default="", help="Name of existing TaskRouter model profile")
    agents_parser.add_argument("--fallbacks", default="", help="Comma-separated allowed fallback profiles")
    agents_parser.add_argument("--reasoning", default="auto", choices=["auto", "low", "medium", "high"])
    agents_parser.add_argument("--temperature", type=float, default=None)
    agents_parser.add_argument("--output-tokens", type=int, default=None)
    agents_parser.add_argument("--require-tools", action="store_true")
    agents_parser.add_argument("--local-only", action="store_true")

    mcp_parser = subparsers.add_parser(
        "mcp",
        parents=[common],
        help="Manage Model Context Protocol servers",
    )
    mcp_parser.add_argument(
        "mcp_action",
        nargs="?",
        default="list",
        choices=["list", "inspect", "trust", "untrust", "connect", "disconnect", "tools", "resources"],
        help="MCP action",
    )
    mcp_parser.add_argument("server_name", nargs="?", default=None, help="Target MCP server name")

    daemon_parser = subparsers.add_parser(
        "daemon",
        parents=[common],
        help="Manage persistent background KITT Daemon",
    )
    daemon_parser.add_argument(
        "daemon_action",
        nargs="?",
        default="status",
        choices=["start", "run", "stop", "status"],
        help="Daemon action",
    )

    for remote_name in ("remote", "web"):
        remote_parser = subparsers.add_parser(
            remote_name,
            parents=[common],
            help="Serve the KITT web interface for local/private-network control",
        )
        remote_parser.add_argument(
            "--lan",
            action="store_true",
            help="Bind to all interfaces for trusted-LAN access",
        )
        remote_parser.add_argument(
            "--host",
            default=None,
            help="Explicit bind host (default: 127.0.0.1 or 0.0.0.0 with --lan)",
        )
        remote_parser.add_argument(
            "--port",
            type=int,
            default=None,
            help="HTTP port (Control Center/default: 7337; 0 chooses a free port)",
        )
        remote_parser.add_argument(
            "--pairing-ttl",
            type=float,
            default=None,
            help="Pairing-code lifetime in seconds (Control Center/default: 900)",
        )
        remote_parser.add_argument(
            "--session-ttl",
            type=float,
            default=None,
            help="Web-session lifetime in seconds (Control Center/default: 43200)",
        )
        remote_parser.add_argument("--tls-cert", default=None, help="PEM certificate path")
        remote_parser.add_argument("--tls-key", default=None, help="PEM private-key path")

    rpc_parser = subparsers.add_parser(
        "rpc",
        parents=[common],
        help="Serve line-oriented JSON-RPC over the host-owned runtime",
    )
    rpc_parser.add_argument(
        "--no-start-services",
        action="store_true",
        help="Do not start schedulers/extensions while serving RPC",
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        parents=[common],
        help="List active and saved KITT sessions with runtime state",
    )
    sessions_parser.add_argument("--all", action="store_true", dest="show_all")
    sessions_parser.add_argument("--json", action="store_true", dest="json_output")
    sessions_parser.add_argument("--limit", type=int, default=20)

    incident_parser = subparsers.add_parser(
        "incident",
        parents=[common],
        help="Reconstruct noteworthy runtime events from local structured logs",
    )
    incident_parser.add_argument(
        "--since",
        default="1h",
        help="Duration (30m, 2h, 1d) or ISO timestamp",
    )
    incident_parser.add_argument(
        "--session",
        default=None,
        help="Filter by session/conversation/turn id",
    )
    incident_parser.add_argument("--json", action="store_true", dest="json_output")
    incident_parser.add_argument("--limit", type=int, default=100)

    attach_parser = subparsers.add_parser(
        "attach",
        parents=[common],
        help="Attach to a running KITT session in daemon",
    )
    attach_parser.add_argument("session", help="Session ID or prefix to attach")

    subparsers.add_parser("detach", parents=[common], help="Detach from current daemon session")

    resume_parser = subparsers.add_parser(
        "resume",
        parents=[common],
        help="Resume an existing KITT session",
    )
    resume_parser.add_argument("session", help="Session ID to resume")

    evolve_parser = subparsers.add_parser(
        "evolve",
        parents=[common],
        help="Offline staged self-evolution of managed KITT skills",
    )
    evolve_sub = evolve_parser.add_subparsers(
        dest="evolve_action",
        help="Evolution action",
    )

    evolve_skill = evolve_sub.add_parser(
        "skill",
        parents=[common],
        help="Evolve one managed skill and stage only if holdout improves",
    )
    evolve_skill.add_argument("skill_name", help="Managed skill name")
    evolve_skill.add_argument(
        "--source",
        choices=["synthetic", "history", "golden"],
        default="synthetic",
        help="Evaluation dataset source",
    )
    evolve_skill.add_argument(
        "--dataset",
        default=None,
        help="Golden JSONL path when --source golden",
    )
    evolve_skill.add_argument("--generations", type=int, default=1)
    evolve_skill.add_argument("--population", type=int, default=2)
    evolve_skill.add_argument(
        "--max-calls",
        type=int,
        default=48,
        help="Hard LLM-call budget for the complete evolution run",
    )
    evolve_skill.add_argument(
        "--cases",
        type=int,
        default=10,
        help="Synthetic case count (6-24)",
    )

    evolve_runs = evolve_sub.add_parser(
        "runs",
        parents=[common],
        help="List recent evolution runs",
    )
    evolve_runs.add_argument("--limit", type=int, default=30)

    evolve_show = evolve_sub.add_parser(
        "show",
        parents=[common],
        help="Inspect one evolution run and candidates",
    )
    evolve_show.add_argument("run_id")

    evolve_promote = evolve_sub.add_parser(
        "promote",
        parents=[common],
        help="Explicitly promote a STAGED candidate",
    )
    evolve_promote.add_argument("run_id")

    evolve_reject = evolve_sub.add_parser(
        "reject",
        parents=[common],
        help="Reject a staged/unpromoted evolution run",
    )
    evolve_reject.add_argument("run_id")

    evolve_opportunities = evolve_sub.add_parser(
        "opportunities",
        parents=[common],
        help="Mine Dreaming memory and recent failures for evolution opportunities",
    )
    evolve_opportunities.add_argument("--limit", type=int, default=10)

    learn_parser = subparsers.add_parser(
        "learn",
        parents=[common],
        help="Inspect local optimization evidence without auto-applying changes",
    )
    learn_sub = learn_parser.add_subparsers(
        dest="learn_action",
        help="Learning action",
    )
    learn_sub.add_parser(
        "suggest",
        parents=[common],
        help="Show evidence-backed optimization candidates",
    )
    learn_experiment = learn_sub.add_parser(
        "experiment",
        parents=[common],
        help="Manage local control/candidate measurement windows",
    )
    learn_experiment_sub = learn_experiment.add_subparsers(
        dest="learn_experiment_action",
        help="Experiment action",
    )
    learn_start = learn_experiment_sub.add_parser(
        "start",
        parents=[common],
        help="Start the control arm for a feature",
    )
    learn_start.add_argument("feature")
    learn_switch = learn_experiment_sub.add_parser(
        "switch",
        parents=[common],
        help="Switch an experiment to control or candidate",
    )
    learn_switch.add_argument("feature")
    learn_switch.add_argument("arm", choices=["control", "candidate"])
    learn_report = learn_experiment_sub.add_parser(
        "report",
        parents=[common],
        help="Compare observed experiment arms",
    )
    learn_report.add_argument("feature")

    re_parser = subparsers.add_parser(
        "re", parents=[common], help="Manage lazy reverse-engineering environments",
    )
    re_actions = re_parser.add_subparsers(dest="re_action", required=True)
    re_status = re_actions.add_parser("status", help="Inspect RE environments without starting them")
    re_status.add_argument("--check-oracle", action="store_true",
        help="Opt in to read-only SSH readiness verification of Dockur Windows")
    re_status.add_argument("--hosts", type=Path, help="Private operator-owned guest hosts.json")
    re_status.add_argument("--manifest-sha256", help="Expected SHA-256 of Windows guest probe manifest")
    re_oracle = re_actions.add_parser("oracle", help="Inspect authenticated Windows Oracle readiness")
    re_oracle_actions = re_oracle.add_subparsers(dest="oracle_action", required=True)
    re_oracle_doctor = re_oracle_actions.add_parser("doctor",
        help="Read-only SSH handshake; does not run any probes or change Windows")
    re_oracle_doctor.add_argument("--hosts", type=Path, help="Private operator hosts.json")
    re_oracle_doctor.add_argument("--manifest-sha256", required=True,
        help="Expected SHA-256 of the Windows guest probe manifest")
    re_oracle_bootstrap = re_oracle_actions.add_parser("bootstrap",
        help="Unattended disposable Dockur Windows 11 OEM setup (requires explicit authorization)")
    re_oracle_bootstrap.add_argument("--recreate", action="store_true",
        help="DESTROYS the managed Windows guest disk, then reinstalls Windows 11")
    re_oracle_bootstrap.add_argument("--yes", action="store_true",
        help="Confirm Windows guest install or destruction")
    re_oracle_bootstrap.add_argument("--accept-license", action="store_true",
        help="Confirm operator has a valid Windows license and approves Microsoft downloads")
    re_oracle_pilot = re_oracle_actions.add_parser("pilot",
        help="Run only the KITT-authored pinned Win32 event probe on Windows and Docker Wine")
    re_oracle_pilot.add_argument("--hosts", type=Path, help="Private operator-owned SSH guest hosts.json")
    re_oracle_pilot.add_argument("--manifest-sha256", required=True)
    re_oracle_pilot.add_argument("--yes", action="store_true", help="Approve this one fixed benign probe")
    re_cleanroom = re_actions.add_parser("cleanroom",
        help="Validate, seal and verify reviewed black-box API contracts (never executes agents)")
    re_clean_actions = re_cleanroom.add_subparsers(dest="cleanroom_action", required=True)
    re_clean_validate = re_clean_actions.add_parser("validate", help="Reject leaked or malformed API behavior")
    re_clean_validate.add_argument("contract_file", type=Path)
    re_clean_seal = re_clean_actions.add_parser("seal", help="Seal independently reviewed contract")
    re_clean_seal.add_argument("contract_file", type=Path)
    re_clean_seal.add_argument("--reviewer", required=True,
        help="Independent human reviewer identifier; an assertion, not a verified identity")
    re_clean_seal.add_argument("--state-root", type=Path, default=None)
    re_clean_verify = re_clean_actions.add_parser("verify", help="Verify independent pinned digest")
    re_clean_verify.add_argument("run_id")
    re_clean_verify.add_argument("--sha256", required=True, dest="expected_sha256")
    re_clean_verify.add_argument("--state-root", type=Path, default=None)
    re_clean_build = re_clean_actions.add_parser("build-image",
        help="Build an entirely offline model + llama-cli OCI implementer from local assets")
    re_clean_build.add_argument("--base-image", required=True,
        help="Pre-cached local sha256 image ID with Python, Git and Wine build dependencies")
    re_clean_build.add_argument("--model", type=Path, required=True)
    re_clean_build.add_argument("--llama-cli", type=Path, required=True)
    re_clean_build.add_argument("--engine", choices=["docker", "podman"], default="docker")

    re_clean_run = re_clean_actions.add_parser("run-offline",
        help="Run SHA-pinned offline implementer OCI image without network or analyst mounts")
    re_clean_run.add_argument("run_id")
    re_clean_run.add_argument("--sha256", required=True, dest="expected_sha256")
    re_clean_run.add_argument("--wine-source", type=Path, required=True,
        help="Separate operator-approved Wine/ReactOS checkout, never the analyst workspace")
    re_clean_run.add_argument("--image", required=True, help="Local trusted sha256:<digest> image ID")
    re_clean_run.add_argument("--engine", choices=["docker", "podman"], default="docker")
    re_clean_run.add_argument("--state-root", type=Path, default=None)
    re_clean_run.add_argument("--approve", action="store_true", help="Explicit human run approval")

    re_clean_pipeline = re_clean_actions.add_parser("run",
        help="Execute signed contract, isolated offline implementer and independently signed tests")
    re_clean_pipeline.add_argument("run_id")
    re_clean_pipeline.add_argument("--sha256", dest="expected_sha256", required=True)
    re_clean_pipeline.add_argument("--wine-source", required=True, type=Path)
    re_clean_pipeline.add_argument("--image", required=True, dest="implementer_image")
    re_clean_pipeline.add_argument("--validator-image", required=True)
    re_clean_pipeline.add_argument("--test-script", required=True, type=Path)
    re_clean_pipeline.add_argument("--test-signature", required=True, type=Path)
    re_clean_pipeline.add_argument("--test-sha256", required=True)
    re_clean_pipeline.add_argument("--engine", choices=["docker", "podman"], default="docker")
    re_clean_pipeline.add_argument("--state-root", type=Path, default=None)
    re_clean_pipeline.add_argument("--require-fixed-oracle-pilot", action="store_true")
    re_clean_pipeline.add_argument("--approve", action="store_true",
        help="Independent signed review is mandatory in addition to operator approval")

    re_setup = re_actions.add_parser("setup", help="Prepare or inspect an RE environment")
    re_setup.add_argument("system", choices=["windows", "linux", "rea", "toolbox"])
    re_setup.add_argument("--runtime", choices=["auto", "docker", "podman"], default="auto")
    re_setup.add_argument("--source", type=Path)
    re_setup.add_argument("--viewer-port", type=int, help="Local noVNC port (defaults: 18006/18007)")
    re_setup.add_argument("--probe-port", type=int, help="Local-only SSH probe port (defaults: 18008/18009)")
    re_enroll = re_actions.add_parser("enroll", help="Approve an existing VM profile")
    re_enroll.add_argument("system", choices=["windows", "linux"])
    re_enroll.add_argument("profile", type=Path)
    re_enroll.add_argument("--auto", action="store_true", help="Permit lazy startup for lab missions")
    re_auto = re_actions.add_parser("auto", help="Enable or disable lazy lab startup")
    re_auto.add_argument("system", choices=["windows", "linux"])
    re_auto.add_argument("state", choices=["on", "off"])
    for re_operation in ("start", "stop"):
        re_command = re_actions.add_parser(re_operation)
        re_command.add_argument("system", choices=["windows", "linux"])
    re_shell = re_actions.add_parser("shell", help="Open an operator terminal in the managed Linux RE toolbox")
    re_shell.add_argument("system", choices=["linux"])

    re_lab = re_actions.add_parser("lab", help="Explicit managed Linux RE + Dockur Windows lab")
    re_lab_actions = re_lab.add_subparsers(dest="lab_action", required=True)
    re_recreate = re_lab_actions.add_parser("recreate",
        help="Explicitly reset only KITT-owned lab resources with a recent token")
    re_recreate.add_argument("--state-root", type=Path, default=None)
    re_recreate.add_argument("--scope", choices=["windows", "linux", "wine", "all"], required=True)
    re_recreate.add_argument("--destroy-data", action="store_true")
    re_recreate.add_argument("--dry-run", "--plan", action="store_true")
    re_recreate.add_argument("--confirm", default=None,
        help="Recent HMAC token from recreate --dry-run; never implied by --yes")
    re_recreate.add_argument("--accept-license", action="store_true",
        help="Permit reinstalling a Windows guest after an explicitly approved reset")
    stage = re_lab_actions.add_parser(
        "stage-wine", help="Operator-only clone official Wine-Staging on host into guest shared workspace")
    stage.add_argument("--state-root", type=Path, default=None)
    stage.add_argument("--approve", action="store_true",
                       help="Approve the fixed GitHub clone on the networked host")
    upgrade = re_lab_actions.add_parser(
        "upgrade-linux", help="Replace managed Linux toolbox with locally prepared full image; preserve workspace and Windows")
    upgrade.add_argument("--state-root", type=Path, default=None)
    upgrade.add_argument("--approve", action="store_true",
                         help="Confirm managed Linux container replacement; never deletes workspace")
    for action in ("doctor", "status", "setup", "up", "down", "verify"):
        command = re_lab_actions.add_parser(action)
        command.add_argument("--state-root", type=Path, default=None,
            help="Private re-lab directory under the operator home")
        if action == "status":
            command.add_argument("--json", action="store_true", dest="lab_json",
                help="Stable JSON status (the default)")
        if action in ("up", "down"):
            command.add_argument("--dry-run", "--plan", action="store_true",
                help="Show current state and proposed lifecycle action without mutation")
        if action in ("setup",):
            command.add_argument("--runtime", choices=["docker"], default="docker")
            command.add_argument("--windows", choices=["11"], default="11")
            command.add_argument("--linux-profile", choices=["full"], default="full")
            command.add_argument("--yes", action="store_true", help="Approve non-destructive provisioning")
            command.add_argument("--accept-license", action="store_true",
                help="Confirm a valid Windows license and authorized downloads")
            command.add_argument("--dry-run", "--plan", action="store_true",
                help="Show preflight and intended operation without mutation")
        if action == "verify":
            command.add_argument("--full", action="store_true",
                help="Also run the fixed bounded Win32 event pilot on Windows and Wine")

    vm_parser = subparsers.add_parser(
        "vm", parents=[common], help="Run autonomous KITT inside an isolated QEMU guest",
    )
    vm_commands = vm_parser.add_subparsers(dest="vm_action", required=True)
    create = vm_commands.add_parser("create", aliases=["wizard"],
                                    help="Interactive Windows/Linux VM helper; asks for ISO/QCOW2 path")
    create.add_argument("--os", choices=["windows", "linux"], dest="vm_os")
    create.add_argument("--source", type=Path, help="User-provided ISO or standalone QCOW2")
    create.add_argument("--kind", choices=["iso", "qcow2"])
    create.add_argument("--destination", type=Path)
    create.add_argument("--memory-mib", type=int, default=4096)
    create.add_argument("--cpus", type=int, default=2)
    create.add_argument("--disk-gib", type=int, default=64)
    create.add_argument("--variant", choices=["official", "tiny11", "tiny11-core", "custom"])
    create.add_argument("--distro", choices=["debian", "ubuntu", "kali", "fedora", "arch", "other"])
    create.add_argument("--tools", choices=["minimal", "re", "wine", "full"])
    create.add_argument("--yes", action="store_true", help="Skip prompts; requires --os and --source")
    create.add_argument("--backend", choices=["native", "docker", "podman"])
    create.add_argument("--port", type=int, default=18006, help="Host-only noVNC web port for Docker/Podman guests")
    create.add_argument("--probe-port", type=int, default=0, help="Optional host-only SSH guest probe port (0=disabled)")
    containers = vm_commands.add_parser("container", help="QEMU guests and Linux RE toolbox in Docker/Podman")
    container_commands = containers.add_subparsers(dest="container_action", required=True)
    container_build = container_commands.add_parser("build", help="Build QEMU runner or preinstalled Linux RE toolbox image")
    container_build.add_argument("--runtime", choices=["auto", "docker", "podman"], default="auto")
    container_build.add_argument("--image", choices=["qemu", "toolbox"], required=True)
    container_build.add_argument("--profile", choices=["base", "wine", "full"], default="full")
    container_pull = container_commands.add_parser("pull", help="Fetch published QEMU and Linux RE images")
    container_pull.add_argument("--runtime", choices=["auto", "docker", "podman"], default="auto")
    container_pull.add_argument("--image", choices=["qemu", "toolbox"], required=True)
    container_pull.add_argument("--profile", choices=["base", "wine", "full"], default="full")
    container_doctor = container_commands.add_parser("doctor", help="Check Docker/Podman and KVM")
    container_doctor.add_argument("--runtime", choices=["auto", "docker", "podman"], default="auto")
    for op in ("start", "stop", "logs", "status", "credentials"):
        command = container_commands.add_parser(op)
        command.add_argument("--profile", type=Path, required=True)
        if op == "start":
            command.add_argument("--install", action="store_true", help="Boot ISO for first OS installation")
            command.add_argument("--allow-tcg", action="store_true", help="Allow very slow software emulation")
    container_snapshot = container_commands.add_parser("snapshot", help="Offline VM baseline save/restore/list")
    container_snapshot.add_argument("--profile", type=Path, required=True)
    container_snapshot.add_argument("--action", choices=["save", "restore", "list"], required=True)
    container_snapshot.add_argument("--name", default="baseline")
    container_snapshot.add_argument("--yes", action="store_true", help="Explicit approval required for restore")
    container_toolbox = container_commands.add_parser("toolbox", help="Open RE tools shell (trusted files only)")
    container_toolbox.add_argument("--runtime", choices=["auto", "docker", "podman"], default="auto")
    container_toolbox.add_argument("--profile", choices=["base", "wine", "full"], default="full")
    container_toolbox.add_argument("--workspace", type=Path, required=True)

    vm_lab = vm_commands.add_parser("lab", help="Host-controlled Windows/Linux differential probe broker")
    vm_lab_commands = vm_lab.add_subparsers(dest="lab_action", required=True)
    lab_doctor = vm_lab_commands.add_parser("doctor")
    lab_doctor.add_argument("--hosts", type=Path, required=True)
    lab_run = vm_lab_commands.add_parser("run")
    lab_run.add_argument("--hosts", type=Path, required=True)
    lab_run.add_argument("--mission", type=Path, required=True)
    lab_run.add_argument("--workspace", type=Path, required=True)

    vm_run = vm_commands.add_parser("run", help="Start a disposable, pinned guest")
    vm_run.add_argument("--profile", type=Path, required=True)
    vm_run.add_argument("--goal", required=True)
    vm_doctor = vm_commands.add_parser("doctor", help="Check VM tools and pinned image")
    vm_doctor.add_argument("--profile", type=Path, required=True)
    windows_parser = vm_commands.add_parser("windows", help="Guide Windows VM creation from your own ISO/QCOW2")
    windows_commands = windows_parser.add_subparsers(dest="windows_action", required=True)
    windows_prepare = windows_commands.add_parser("prepare", help="Create offline Windows VM")
    windows_prepare.add_argument("--source", type=Path)
    windows_prepare.add_argument("--kind", choices=["iso", "qcow2"])
    windows_prepare.add_argument("--variant", choices=["official", "tiny11", "tiny11-core", "custom"])
    windows_prepare.add_argument("--destination", type=Path)
    windows_prepare.add_argument("--memory-mib", type=int, default=4096)
    windows_prepare.add_argument("--cpus", type=int, default=2)
    windows_prepare.add_argument("--disk-gib", type=int, default=64)
    windows_prepare.add_argument("--ovmf-code", type=Path)
    windows_prepare.add_argument("--ovmf-vars", type=Path)
    windows_prepare.add_argument("--yes", action="store_true")
    for vm_action in ("install", "start"):
        windows_command = windows_commands.add_parser(vm_action)
        windows_command.add_argument("--profile", type=Path, required=True)
        windows_command.add_argument("--vnc-display", type=int, default=1)
    linux_parser = vm_commands.add_parser("linux", help="Create a separate Linux reverse-engineering guest")
    linux_commands = linux_parser.add_subparsers(dest="linux_action", required=True)
    linux_prepare = linux_commands.add_parser("prepare", help="Create Linux RE guest from user ISO/QCOW2")
    linux_prepare.add_argument("--source", type=Path)
    linux_prepare.add_argument("--kind", choices=["iso", "qcow2"])
    linux_prepare.add_argument("--distro", choices=["debian", "ubuntu", "kali", "fedora", "arch", "other"])
    linux_prepare.add_argument("--tools", choices=["minimal", "re", "wine", "full"])
    linux_prepare.add_argument("--destination", type=Path)
    linux_prepare.add_argument("--memory-mib", type=int, default=4096)
    linux_prepare.add_argument("--cpus", type=int, default=2)
    linux_prepare.add_argument("--disk-gib", type=int, default=64)
    linux_prepare.add_argument("--yes", action="store_true")
    for linux_action in ("install", "start"):
        linux_command = linux_commands.add_parser(linux_action)
        linux_command.add_argument("--profile", type=Path, required=True)
        linux_command.add_argument("--vnc-display", type=int, default=2)
        linux_command.add_argument("--network", action="store_true", help="Explicit temporary NAT for package setup only")

    doctor_parser = subparsers.add_parser(
        "doctor",
        parents=[common],
        help="Run system diagnostics and manage local state",
    )
    doctor_parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Explicitly reset incompatible SQLite database state to Schema V1",
    )
    return parser


async def _run_contract_mode(runtime, args) -> int:
    from kitt.goals.contract_commands import (
        create_planned_contract,
        render_contract,
        resume_contract,
        schedule_contract,
    )

    if args.contract_resume:
        goal = runtime.goals.get(args.contract_resume)
        if goal is None or not runtime.goals.contract_items(goal.id):
            print(f"Contract goal not found: {args.contract_resume}", file=sys.stderr)
            return 2
        resumed = resume_contract(runtime, goal.id)
        if resumed is None:
            print(f"Unable to resume contract: {goal.id}", file=sys.stderr)
            return 2
        goal = resumed
        print(render_contract(runtime, goal.id))
    else:
        if not args.contract or not str(args.prompt or "").strip():
            print("--contract requires -p/--print with a non-empty prompt", file=sys.stderr)
            return 2
        conversation = runtime.history.get_or_create_active()
        try:
            goal, _items = await asyncio.to_thread(
                create_planned_contract,
                runtime,
                conversation_id=conversation["id"],
                objective=args.prompt,
                allow=args.contract_allow,
                max_attempts=max(1, int(args.contract_max_attempts)),
                start_paused=not bool(args.contract_yes),
            )
        except (ValueError, RuntimeError) as exc:
            print(f"Contract planning failed: {exc}", file=sys.stderr)
            return 2
        print(render_contract(runtime, goal.id))
        if not args.contract_yes:
            print(
                f"Contract saved but not started. Review it, then run: "
                f"kitt --contract-resume {goal.id}",
            )
            return 0
        if not schedule_contract(runtime, goal.id):
            print(f"Unable to schedule contract goal {goal.id}", file=sys.stderr)
            return 2

    terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "PAUSED_BUDGET_EXCEEDED"}
    last_state = ""
    while True:
        goal = runtime.goals.get(goal.id)
        if goal is None:
            print("Contract goal disappeared from persistent state.", file=sys.stderr)
            return 2
        if goal.state != last_state:
            print(f"[contract] {goal.id}: {goal.state}")
            last_state = goal.state
        if goal.state in terminal:
            print(render_contract(runtime, goal.id))
            return 0 if goal.state == "SUCCEEDED" else 1
        if goal.state == "WAITING_APPROVAL":
            print(
                "Contract is waiting for a host approval. Approve it from an interactive "
                "KITT session, then use --contract-resume.",
                file=sys.stderr,
            )
            return 3

        results = await asyncio.to_thread(runtime.goal_scheduler.check_and_execute_due)
        for entry in results:
            status = str(entry.get("status") or "")
            contract = entry.get("contract") or {}
            local_id = contract.get("local_id") or ""
            suffix = f" {local_id}" if local_id else ""
            print(f"[contract]{suffix}: {status}")
        if not results:
            await asyncio.sleep(0.1)


async def async_main(args) -> int:
    base_config = RuntimeConfig.from_env()
    persistent = not args.no_history
    resident_available = _module_available("kitt.daemon.client")
    daemon_enabled = bool(base_config.daemon_enabled and resident_available)
    daemon_authoritative = bool(daemon_enabled and persistent)
    contract_mode = bool(
        getattr(args, "contract", False)
        or getattr(args, "contract_resume", None)
    )
    if contract_mode and not persistent:
        print(
            "Contract mode requires persistent state; --no-history is incompatible.",
            file=sys.stderr,
        )
        return 2
    if contract_mode and daemon_authoritative:
        print(
            "Contract mode requires the local runtime to own the GoalScheduler. "
            "Disable the resident daemon for this invocation; no local fallback is used.",
            file=sys.stderr,
        )
        return 2
    config = replace(
        base_config,
        history_enabled=persistent,
        persistence_enabled=persistent,
        daemon_enabled=daemon_enabled,
        frontend_only=daemon_authoritative,
        scheduler_enabled=False if contract_mode else base_config.scheduler_enabled,
    )
    runtime = KittRuntime.build(args.root, config=config)
    # A new KITT invocation starts logically blank. Saved history remains
    # available through explicit history/resume commands.
    runtime.history.begin_fresh_session()
    backend = None
    if not contract_mode:
        backend = (
            HeadlessUI(runtime, args.prompt)
            if args.prompt is not None
            else create_backend(
                runtime,
                "plain" if args.plain else args.ui,
                no_animation=args.no_animation,
            )
        )
    code = 1
    errors = []
    try:
        await runtime.start()
        code = (
            await _run_contract_mode(runtime, args)
            if contract_mode
            else await backend.run_async()
        )
    finally:
        if backend is not None:
            try:
                await backend.shutdown()
            except BaseException as exc:
                errors.append(exc)
        try:
            await runtime.aclose()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("Shutdown failed: " + "; ".join(map(str, errors)))
    return code


async def _run_rpc_mode(args) -> int:
    config = RuntimeConfig.from_env()
    if bool(getattr(args, "no_start_services", False)):
        config = replace(
            config,
            scheduler_enabled=False,
            wake_scheduler_enabled=False,
            frontend_only=False,
        )
    runtime = KittRuntime.build(args.root, config=config)
    try:
        await runtime.start()
        await asyncio.to_thread(runtime.rpc.serve_lines, sys.stdin, sys.stdout)
        return 0
    finally:
        await runtime.aclose()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_debug_log(args)
    _log_runtime_identity()
    notify_if_update_available(component="agent-cli")

    if args.subcommand == "models":
        from kitt.cli.commands import handle_models_command

        return handle_models_command(
            provider=args.provider,
            refresh=args.refresh,
            verbose=args.verbose,
        )

    if args.subcommand == "auth":
        from kitt.cli.commands import handle_auth_command

        action = getattr(args, "auth_action", "list") or "list"
        return handle_auth_command(
            action=action,
            provider=getattr(args, "provider", None),
            method=getattr(args, "method", "api_key"),
        )

    if args.subcommand == "agents":
        from kitt.agents.helper import handle_agents_command
        return handle_agents_command(args.agent_action, args.agent_name, args.root, args)

    if args.subcommand == "plugins":
        from kitt.cli.commands import handle_plugins_command

        return handle_plugins_command(
            action=args.plugin_action,
            name=args.plugin_name,
            root_dir=args.root,
        )

    if args.subcommand == "mcp":
        from kitt.cli.commands import handle_mcp_command

        return handle_mcp_command(
            action=args.mcp_action,
            server=args.server_name,
            root_dir=args.root,
        )

    if args.subcommand == "daemon":
        if not _module_available("kitt.daemon.process"):
            return _missing_companion("Daemon management", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_daemon_command

        return handle_daemon_command(action=args.daemon_action, root_dir=args.root)

    if args.subcommand in {"remote", "web"}:
        if not _module_available("kitt.remote.cli"):
            return _missing_companion("Remote/Web control", "kitt-assistant-runtime")
        from kitt.remote.cli import run_remote_command

        return run_remote_command(
            root_dir=args.root,
            lan=bool(args.lan),
            host=args.host,
            port=args.port,
            pairing_ttl=args.pairing_ttl,
            session_ttl=args.session_ttl,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
        )

    if args.subcommand == "rpc":
        return asyncio.run(_run_rpc_mode(args))

    if args.subcommand == "sessions":
        from kitt.cli.commands import handle_sessions_command

        return handle_sessions_command(
            root_dir=args.root,
            limit=args.limit,
            show_all=bool(args.show_all),
            json_output=bool(args.json_output),
        )

    if args.subcommand == "incident":
        from kitt.cli.commands import handle_incident_command

        return handle_incident_command(
            root_dir=args.root,
            since=args.since,
            session=args.session,
            limit=args.limit,
            json_output=bool(args.json_output),
        )

    if args.subcommand == "attach":
        if not _module_available("kitt.daemon.client"):
            return _missing_companion("Daemon session attach", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_attach_command

        return handle_attach_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "detach":
        print("\033[90mDetached from session.\033[0m")
        return 0

    if args.subcommand == "resume":
        if not _module_available("kitt.daemon.client"):
            return _missing_companion("Daemon session resume", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_resume_command

        return handle_resume_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "evolve":
        if not _module_available("kitt.evolution.cli"):
            return _missing_companion("Self-evolution", "kitt-evolution")
        from kitt.evolution.cli import handle_evolve_command

        return handle_evolve_command(args)

    if args.subcommand == "learn":
        from kitt.cli.commands import handle_learn_command

        return handle_learn_command(args)

    if args.subcommand == "re":
        from kitt import re_environment
        from kitt.vm_supervisor import VMError
        import json as _json
        try:
            if args.re_action == "cleanroom":
                from kitt import cleanroom_handoff
                if args.cleanroom_action == "validate":
                    result = cleanroom_handoff.validate(args.contract_file)
                elif args.cleanroom_action == "seal":
                    result = cleanroom_handoff.seal(
                        args.contract_file, reviewer=args.reviewer, state_root=args.state_root)
                elif args.cleanroom_action == "verify":
                    result = cleanroom_handoff.verify(
                        args.run_id, expected_sha256=args.expected_sha256,
                        state_root=args.state_root)
                elif args.cleanroom_action == "build-image":
                    from kitt.cleanroom_image_builder import build_image
                    from kitt.cleanroom_runner import CleanRoomEnvironmentError
                    try:
                        result = build_image(
                            base_image=args.base_image, model=args.model,
                            llama_cli=args.llama_cli, engine=args.engine)
                    except CleanRoomEnvironmentError as exc:
                        result = {"status": "BLOCKED_ENV", "reason": str(exc)}
                elif args.cleanroom_action == "run":
                    from kitt.cleanroom_pipeline import run_pipeline
                    from kitt.cleanroom_runner import CleanRoomEnvironmentError
                    try:
                        result = run_pipeline(
                            args.run_id, expected_sha256=args.expected_sha256,
                            wine_source=args.wine_source,
                            implementer_image=args.implementer_image,
                            validator_image=args.validator_image,
                            test_script=args.test_script,
                            test_signature=args.test_signature,
                            test_sha256=args.test_sha256,
                            state_root=args.state_root,
                            engine=args.engine, approved=args.approve,
                            require_fixed_oracle_pilot=args.require_fixed_oracle_pilot)
                    except (CleanRoomEnvironmentError, PermissionError, ValueError) as exc:
                        result = {"status": "BLOCKED_ENV", "reason": str(exc)}
                else:
                    from kitt.cleanroom_runner import run_offline_implementer, CleanRoomEnvironmentError
                    try:
                        result = run_offline_implementer(
                            args.run_id, expected_sha256=args.expected_sha256,
                            wine_source=args.wine_source, image=args.image,
                            state_root=args.state_root, engine=args.engine,
                            approved=args.approve)
                    except CleanRoomEnvironmentError as exc:
                        result = {"status": "BLOCKED_ENV", "reason": str(exc)}
                print(_json.dumps(result, ensure_ascii=False))
                return (0 if result["status"] in {"VALID_NEEDS_INDEPENDENT_REVIEW",
                                                   "SEALED_NOT_EXECUTABLE",
                                                   "VERIFIED_NOT_EXECUTABLE",
                                                   "IMAGE_BUILT_NOT_ATTESTED"}
                        else 2)
            if args.re_action == "lab":
                from kitt import re_lab
                action = args.lab_action
                if action == "recreate":
                    from kitt import re_lab_reset
                    if args.dry_run:
                        result = re_lab_reset.plan(
                            state_root=args.state_root, scope=args.scope,
                            destroy_data=args.destroy_data)
                    else:
                        result = re_lab_reset.recreate(
                            state_root=args.state_root, scope=args.scope,
                            confirm=args.confirm, destroy_data=args.destroy_data,
                            accept_license=args.accept_license)
                elif action == "doctor":
                    result = re_lab.doctor(state_root=args.state_root)
                elif action == "status":
                    result = re_lab.status(state_root=args.state_root)
                elif action == "setup":
                    if args.dry_run:
                        result = {"status": "PLAN_ONLY", "preflight": re_lab.doctor(
                            state_root=args.state_root),
                            "operations": ["reuse/create Linux RE full toolbox",
                                           "prepare official Dockur image and OEM",
                                           "install Windows 11 guest only if missing"],
                            "destructive": False}
                    else:
                        result = re_lab.setup(
                            state_root=args.state_root, runtime=args.runtime,
                            windows=args.windows, linux_profile=args.linux_profile,
                            yes=args.yes, accept_license=args.accept_license)
                elif action == "verify":
                    result = re_lab.verify(state_root=args.state_root, full=args.full)
                elif action == "stage-wine":
                    from kitt.re_source_stage import stage_wine
                    result = stage_wine(state_root=args.state_root, approve=args.approve)
                elif action == "upgrade-linux":
                    result = re_lab.upgrade_linux(
                        state_root=args.state_root, approve=args.approve)
                else:
                    if args.dry_run:
                        result = {"status": "PLAN_ONLY", "action": action,
                                  "current": re_lab.status(state_root=args.state_root),
                                  "destructive": False}
                    else:
                        result = re_lab.operate(action, state_root=args.state_root)
                print(_json.dumps(result, ensure_ascii=False))
                if action == "doctor":
                    return 0 if result["ready_for_setup"] else 2
                if action == "verify":
                    return 0 if result.get("oracle_ready") and (
                        not args.full or result.get("pilot", {}).get("status") == "PASS_SCENARIO"
                    ) else 2
                return 0
            if args.re_action == "status":
                result = re_environment.status()
                if args.check_oracle:
                    from kitt import re_oracle
                    verified = re_oracle.doctor(hosts=args.hosts,
                        manifest_sha256=args.manifest_sha256 or "")
                    result["guests"]["windows"]["oracle_ready"] = verified["oracle_ready"]
                    result["guests"]["windows"]["oracle_check"] = verified
            elif args.re_action == "oracle":
                if args.oracle_action == "bootstrap":
                    from kitt import re_oracle_bootstrap
                    result = re_oracle_bootstrap.bootstrap(
                        recreate=args.recreate, yes=args.yes, accept_license=args.accept_license)
                    print(_json.dumps(result, ensure_ascii=False))
                    return 0
                if args.oracle_action == "pilot":
                    from kitt import re_oracle_pilot
                    result = re_oracle_pilot.pilot(manifest_sha256=args.manifest_sha256,
                        hosts=args.hosts, yes=args.yes)
                    print(_json.dumps(result, ensure_ascii=False))
                    return 0 if result["status"] == "PASS_SCENARIO" else 1 if result["status"] == "FAIL_SCENARIO" else 2
                from kitt import re_oracle
                result = re_oracle.doctor(hosts=args.hosts,
                    manifest_sha256=args.manifest_sha256)
                print(_json.dumps(result, ensure_ascii=False))
                return 0 if result["oracle_ready"] else 2
            elif args.re_action == "setup":
                result = re_environment.setup(
                    args.system, runtime=args.runtime, source=args.source,
                    viewer_port=args.viewer_port, probe_port=args.probe_port,
                )
            elif args.re_action == "enroll":
                result = re_environment.enroll(args.system, args.profile, auto=args.auto)
            elif args.re_action == "auto":
                result = re_environment.set_auto(args.system, args.state == "on")
            elif args.re_action == "start":
                result = re_environment.ensure(args.system)
            elif args.re_action == "shell":
                return re_environment.shell(args.system)
            else:
                result = re_environment.stop(args.system)
            print(_json.dumps(result, ensure_ascii=False))
            return 0
        except (VMError, OSError, ValueError) as exc:
            print(f"RE environment error: {exc}", file=sys.stderr)
            return 2

    if args.subcommand == "vm":
        from kitt.vm_supervisor import VMError, run_vm_job, inspect_profile
        import json as _json
        try:
            if args.vm_action == "lab":
                from kitt import vm_broker
                if args.lab_action == "doctor":
                    report = vm_broker.doctor(args.hosts)
                else:
                    from kitt.re_environment import managed_lab_guests
                    with managed_lab_guests():
                        report = vm_broker.run_lab_mission(
                            workspace=args.workspace, mission=args.mission,
                            hosts=args.hosts,
                        )
                print(_json.dumps(report, ensure_ascii=False))
                return 2 if report.get("blocked", 0) else 0
            if args.vm_action == "container":
                from kitt import vm_container
                if args.container_action in {"build", "pull", "doctor", "toolbox"}:
                    args.runtime = vm_container.resolve_runtime(args.runtime)
                if args.container_action == "build":
                    result = vm_container.build_image(runtime=args.runtime, image=args.image, profile=args.profile)
                elif args.container_action == "pull":
                    result = vm_container.pull_image(runtime=args.runtime, image=args.image, profile=args.profile)
                elif args.container_action == "doctor":
                    result = vm_container.doctor(args.runtime)
                elif args.container_action == "toolbox":
                    return vm_container.toolbox(runtime=args.runtime, profile=args.profile,
                                                workspace=args.workspace)
                elif args.container_action == "credentials":
                    result = vm_container.credentials(args.profile)
                elif args.container_action == "snapshot":
                    if args.action == "restore" and not args.yes:
                        raise VMError("Restoring replaces the VM disk; pass --yes to confirm")
                    result = vm_container.snapshot(args.profile, action=args.action, name=args.name)
                elif args.container_action == "start":
                    result = vm_container.start_vm(args.profile, install=args.install,
                                                    allow_tcg=args.allow_tcg)
                else:
                    result = vm_container.operate(args.profile, args.container_action)
                print(_json.dumps(result, ensure_ascii=False))
                return 0
            if args.vm_action in ("create", "wizard"):
                from kitt.vm_helper import create_vm
                system, profile = create_vm(
                    system=args.vm_os, source=args.source, kind=args.kind,
                    destination=args.destination, memory_mib=args.memory_mib,
                    cpus=args.cpus, disk_gib=args.disk_gib, variant=args.variant,
                    distro=args.distro, tools=args.tools, yes=args.yes,
                    backend=args.backend, port=args.port, probe_port=args.probe_port,
                )
                config = _json.loads(profile.read_text("utf-8")) if profile.is_file() else {}
                is_container = config.get("runtime") in ("docker", "podman")
                already_installed = args.kind == "qcow2" or (args.source is not None and args.source.suffix.lower() == ".qcow2")
                action = "start" if already_installed else "install"
                next_cmd = (f"kitt vm container start --profile {profile}" +
                            (" --install" if action == "install" else "")
                            if is_container else f"kitt vm {system} {action} --profile {profile}")
                print(_json.dumps({"created": True, "system": system,
                                   "profile": str(profile), "next": next_cmd},
                                  ensure_ascii=False))
                return 0
            if args.vm_action == "linux":
                from kitt.vm_linux import wizard as linux_wizard, boot as linux_boot
                if args.linux_action == "prepare":
                    profile = linux_wizard(
                        source=args.source, kind=args.kind, distro=args.distro,
                        tools=args.tools, destination=args.destination,
                        memory_mib=args.memory_mib, cpus=args.cpus,
                        disk_gib=args.disk_gib, yes=args.yes,
                    )
                    print(_json.dumps({"created": True, "profile": str(profile),
                                       "next": "kitt vm linux install --profile " + str(profile)}))
                    return 0
                linux_boot(args.profile, install=args.linux_action == "install",
                           vnc_display=args.vnc_display, network=args.network)
                return 0
            if args.vm_action == "windows":
                from kitt.vm_windows import wizard, boot
                if args.windows_action == "prepare":
                    profile = wizard(
                        source=args.source, kind=args.kind, destination=args.destination,
                        variant=args.variant, memory_mib=args.memory_mib, cpus=args.cpus,
                        disk_gib=args.disk_gib, ovmf_code=args.ovmf_code,
                        ovmf_vars=args.ovmf_vars, yes=args.yes,
                    )
                    print(_json.dumps({"created": True, "profile": str(profile),
                                        "next": "kitt vm windows install --profile " + str(profile)}))
                    return 0
                boot(args.profile, install=args.windows_action == "install",
                     vnc_display=args.vnc_display)
                return 0
            if args.vm_action == "doctor":
                result = inspect_profile(args.profile)
                print(_json.dumps(result))
                return 0
            result = run_vm_job(args.profile, args.goal)
            print(_json.dumps(result, ensure_ascii=False))
            return 0 if result["guest"]["status"] == "PASS" else 2
        except (VMError, OSError, ValueError) as exc:
            print(f"VM supervision blocked: {exc}", file=sys.stderr)
            return 2

    if args.subcommand == "doctor":
        from kitt.cli.commands import handle_doctor_command

        return handle_doctor_command(
            root_dir=args.root,
            reset_state=bool(getattr(args, "reset_state", False)),
        )

    try:
        return asyncio.run(async_main(args))
    except IncompatibleSchemaError as exc:
        print(f"K.I.T.T. state schema error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
