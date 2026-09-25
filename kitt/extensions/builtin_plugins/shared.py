"""Read-only implementations backing KITT's bundled first-party plugins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

_IGNORED = {".git", ".kitt", ".venv", "venv", "node_modules", "target", "build", "dist", ".gradle", ".next", "__pycache__"}


def _root(ctx) -> Path:
    root = getattr(ctx, "workspace_root", None)
    if root is None:
        raise RuntimeError("builtin plugin requires workspace_root")
    return Path(root).resolve()


def _read(path: Path, limit: int = 262144) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _files(root: Path, suffixes: set[str] | None = None, limit: int = 4000) -> Iterable[Path]:
    count = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _IGNORED]
        for name in names:
            path = Path(current) / name
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            try:
                path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            yield path
            count += 1
            if count >= limit:
                return


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return path.name


def _run(root: Path, argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=3.0,
            check=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout[:131072] if result.returncode == 0 else ""


def _intel(root: Path) -> dict[str, Any]:
    markers = {
        "pyproject.toml": ("python", "python"), "pom.xml": ("java", "maven"),
        "build.gradle": ("jvm", "gradle"), "build.gradle.kts": ("kotlin", "gradle"),
        "package.json": ("javascript", "node"), "Cargo.toml": ("rust", "cargo"),
        "go.mod": ("go", "go"), "composer.json": ("php", "composer"),
        "Gemfile": ("ruby", "bundler"), "pubspec.yaml": ("dart", "dart"),
        "Package.swift": ("swift", "swiftpm"),
    }
    ext = {".py":"python", ".java":"java", ".kt":"kotlin", ".js":"javascript", ".ts":"typescript", ".tsx":"typescript", ".rs":"rust", ".go":"go", ".cs":"csharp", ".c":"c", ".cpp":"cpp", ".rb":"ruby", ".php":"php", ".swift":"swift", ".dart":"dart", ".sql":"sql"}
    langs, builds, found, counts = set(), set(), [], {}
    for name, (lang, build) in markers.items():
        if (root / name).is_file():
            found.append(name); langs.add(lang); builds.add(build)
    for path in _files(root, limit=2500):
        suffix = path.suffix.lower()
        if suffix in ext:
            langs.add(ext[suffix]); counts[suffix] = counts.get(suffix, 0) + 1
    text = "\n".join(_read(root / name) for name in markers).lower()
    probes = {"spring-boot":"spring-boot", "angular":"@angular/core", "react":"\"react\"", "nextjs":"\"next\"", "vue":"\"vue\"", "fastapi":"fastapi", "django":"django"}
    return {"languages":sorted(langs), "build_systems":sorted(builds), "frameworks":sorted(k for k,v in probes.items() if v in text), "markers":sorted(found), "extension_counts":dict(sorted(counts.items(), key=lambda x:(-x[1], x[0]))[:16])}


def _changed(root: Path, args: dict[str, Any]) -> list[str]:
    if isinstance(args.get("paths"), list):
        return [str(x).strip().replace("\\", "/") for x in args["paths"] if str(x).strip()][:128]
    names = [x.strip() for x in _run(root, ["git", "diff", "--name-only", "HEAD"]).splitlines() if x.strip()]
    for line in _run(root, ["git", "status", "--porcelain=v1", "--untracked-files=all"]).splitlines():
        value = line[3:].strip() if len(line) > 3 else ""
        if value and value not in names:
            names.append(value)
    return names[:128]


def _test_impact(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    changed = _changed(root, args)
    stems = {Path(x).stem.lower().removeprefix("test_").removesuffix("_test") for x in changed}
    scored = []
    for path in _files(root, {".py", ".java", ".kt", ".js", ".ts", ".tsx", ".rs", ".go", ".cs"}, 6000):
        rel, name = _rel(root, path), path.name.lower(); low = rel.lower()
        if not ("/test/" in f"/{low}" or "/tests/" in f"/{low}" or name.startswith("test_") or "_test." in name or name.endswith("test.java") or ".spec." in name or ".test." in name):
            continue
        score = sum(8 if s and s in name else 3 if s and s in low else 0 for s in stems)
        scored.append((score, rel))
    scored.sort(key=lambda x:(-x[0], x[1]))
    tests = [p for s,p in scored if s > 0][:64] or [p for _,p in scored[:24]]
    return {"changed_paths":changed, "candidate_tests":tests, "strategy":"targeted-first" if tests else "verification-fallback"}


def _lsp(root: Path) -> dict[str, Any]:
    mapping = {"python":["basedpyright-langserver", "pyright-langserver", "pylsp"], "java":["jdtls"], "kotlin":["kotlin-language-server"], "typescript":["typescript-language-server"], "javascript":["typescript-language-server"], "rust":["rust-analyzer"], "go":["gopls"], "c":["clangd"], "cpp":["clangd"], "csharp":["csharp-ls"], "ruby":["ruby-lsp"], "swift":["sourcekit-lsp"]}
    servers, seen = [], set()
    for lang in _intel(root)["languages"]:
        for command in mapping.get(lang, []):
            if command in seen: continue
            seen.add(command); found = shutil.which(command)
            servers.append({"language":lang, "command":command, "available":bool(found), "path":found})
            if found: break
    return {"servers":servers, "execution":"discover only; launch through policy-governed runtime"}


def _openapi(root: Path) -> dict[str, Any]:
    specs = []
    for path in _files(root, {".json", ".yaml", ".yml"}, 2500):
        rel, text = _rel(root, path), _read(path)
        if "openapi" not in rel.lower() and "swagger" not in rel.lower() and not re.search(r"(?mi)^\s*(openapi|swagger)\s*:", text[:8192]): continue
        version = re.search(r"(?mi)^\s*(?:openapi|swagger)\s*:\s*[\"']?([^\"'\s]+)", text)
        specs.append({"path":rel, "version":version.group(1) if version else "unknown", "paths_detected":len(re.findall(r"(?m)^\s{0,8}/[^:\n]+:\s*$", text))})
        if len(specs) >= 32: break
    return {"specs":specs}


def _migration(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    paths = []
    requested = args.get("paths") if isinstance(args.get("paths"), list) else None
    for path in _files(root, {".sql", ".xml", ".yaml", ".yml"}, 5000):
        rel = _rel(root, path).lower()
        if requested and _rel(root, path) not in {str(x) for x in requested}: continue
        if requested or any(x in rel for x in ("migration", "flyway", "liquibase", "db/changelog")): paths.append(path)
        if len(paths) >= 256: break
    rules = [("drop_table", r"\bdrop\s+table\b", "critical"), ("drop_column", r"\bdrop\s+column\b", "high"), ("truncate", r"\btruncate\b", "critical"), ("unique_index", r"\bcreate\s+unique\s+index\b", "medium")]
    rank = {"low":0, "medium":1, "high":2, "critical":3}; risk = "low"; findings = []
    for path in paths:
        text = _read(path)
        for kind, pattern, severity in rules:
            if re.search(pattern, text, re.I):
                findings.append({"path":_rel(root, path), "kind":kind, "severity":severity})
                if rank[severity] > rank[risk]: risk = severity
    return {"files_scanned":len(paths), "risk":risk, "findings":findings[:128], "destructive":risk in {"high", "critical"}}


def _worktree(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    rows, current = [], {}
    for line in _run(root, ["git", "worktree", "list", "--porcelain"]).splitlines():
        if not line.strip():
            if current: rows.append(current); current = {}
            continue
        key, _, value = line.partition(" ")
        if key in {"worktree", "HEAD", "branch"}: current[key] = value
    if current: rows.append(current)
    branch = str(args.get("branch") or "kitt/child-agent"); base = str(args.get("base") or "HEAD"); path = str(args.get("path") or ".kitt/worktrees/child-agent")
    return {"existing":rows[:64], "proposed_create_argv":["git", "worktree", "add", "-b", branch, path, base], "execution":"execute via kitt_runtime process.run; plugin never mutates directly"}


def _quality(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    from kitt.tools.build_detector import BuildDetector
    changed = _changed(root, args); full = bool(args.get("full", False)); steps = BuildDetector(str(root)).plan_verification(changed, full=full)
    return {"changed_paths":changed, "mode":"full" if full else "targeted", "steps":[{"name":s.name, "argv":s.argv, "timeout_seconds":s.timeout_seconds} for s in steps[:16]]}


def _dependencies(root: Path) -> dict[str, Any]:
    items = []
    package = root / "package.json"
    if package.is_file():
        try: data = json.loads(_read(package)); deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception: deps = {}
        items.append({"path":"package.json", "dependencies":len(deps)})
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try: deps = tomllib.loads(_read(pyproject)).get("project", {}).get("dependencies", [])
        except Exception: deps = []
        items.append({"path":"pyproject.toml", "dependencies":len(deps) if isinstance(deps, list) else 0})
    pom = root / "pom.xml"
    if pom.is_file(): items.append({"path":"pom.xml", "dependencies":len(re.findall(r"<dependency>", _read(pom)))})
    locks = [n for n in ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "go.sum") if (root / n).is_file()]
    return {"manifests":items, "lockfiles":locks}


def _ci(root: Path) -> dict[str, Any]:
    systems = []
    gh = root / ".github" / "workflows"
    if gh.is_dir():
        workflows = [{"path":_rel(root,p), "actions":re.findall(r"uses:\s*([^\s#]+)", _read(p))[:64]} for p in sorted(list(gh.glob("*.yml"))+list(gh.glob("*.yaml")))[:64]]
        systems.append({"name":"github-actions", "workflows":workflows})
    for path, name in ((".gitlab-ci.yml", "gitlab-ci"), ("Jenkinsfile", "jenkins"), ("azure-pipelines.yml", "azure-pipelines")):
        if (root / path).is_file(): systems.append({"name":name, "path":path})
    return {"systems":systems}


def _container(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    descriptors, findings = [], []
    for path in _files(root, limit=3000):
        rel = _rel(root, path); low = rel.lower(); name = path.name.lower()
        if not (
            name.startswith("dockerfile")
            or name == "containerfile"
            or "compose" in low
            or any(x in f"/{low}" for x in ("/k8s/", "/kubernetes/", "/helm/"))
        ):
            continue
        descriptors.append(rel); text = _read(path)
        for kind, pattern in (
            ("privileged", r"(?i)privileged:\s*true"),
            ("docker_socket", r"/var/run/docker\.sock"),
            ("latest_tag", r"(?i)(?:image:|from)\s+\S+:latest\b"),
            ("host_network", r"(?i)(?:network_mode:\s*host|hostNetwork:\s*true)"),
        ):
            if re.search(pattern, text):
                findings.append({"path": rel, "kind": kind})

    runtime_commands = {
        "docker": "docker",
        "podman": "podman",
        "kubectl": "kubectl",
        "helm": "helm",
    }
    runtimes = {}
    for name, command in runtime_commands.items():
        executable = shutil.which(command)
        runtimes[name] = {"available": bool(executable), "path": executable}

    action = str(args.get("action") or "inspect").strip().lower()
    runtime = str(args.get("runtime") or "").strip().lower()
    service = str(args.get("service") or "").strip()
    namespace = str(args.get("namespace") or "").strip()
    resource = str(args.get("resource") or "pods").strip()
    manifest = str(args.get("manifest") or "").strip().replace("\\", "/")

    safe_token = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
    if service and not safe_token.fullmatch(service):
        service = ""
    if namespace and not safe_token.fullmatch(namespace):
        namespace = ""
    if resource and not safe_token.fullmatch(resource):
        resource = "pods"
    if manifest and (
        manifest.startswith("/")
        or ".." in Path(manifest).parts
        or not safe_token.fullmatch(manifest)
    ):
        manifest = ""

    proposed_argv: list[str] | None = None
    risk = "read-only"
    compose_actions = {
        "compose-config", "compose-ps", "compose-logs", "compose-build",
        "compose-up", "compose-down", "compose-restart",
    }
    if action in compose_actions:
        engine = (
            runtime
            if runtime in {"docker", "podman"}
            else ("docker" if runtimes["docker"]["available"] else "podman")
        )
        verb = action.removeprefix("compose-")
        proposed_argv = [engine, "compose", verb]
        if verb == "up":
            proposed_argv.append("-d")
        if service and verb in {"logs", "build", "up", "restart"}:
            proposed_argv.append(service)
        risk = "read-only" if verb in {"config", "ps", "logs"} else "state-changing"
    elif action in {
        "k8s-get", "k8s-describe", "k8s-diff", "k8s-apply", "k8s-rollout-status"
    }:
        if action == "k8s-get":
            proposed_argv = ["kubectl", "get", resource]
        elif action == "k8s-describe":
            proposed_argv = ["kubectl", "describe", resource]
        elif action in {"k8s-diff", "k8s-apply"} and manifest:
            proposed_argv = ["kubectl", action.removeprefix("k8s-"), "-f", manifest]
        elif action == "k8s-rollout-status":
            proposed_argv = ["kubectl", "rollout", "status", resource]
        if proposed_argv and namespace:
            proposed_argv.extend(["-n", namespace])
        risk = "state-changing" if action == "k8s-apply" else "read-only"

    return {
        "descriptors": descriptors[:128],
        "findings": findings[:128],
        "runtimes": runtimes,
        "requested_action": action,
        "proposed_argv": proposed_argv,
        "risk": risk,
        "execution": (
            "execute proposed_argv only through kitt_runtime process.run; "
            "PolicyEngine and approval remain authoritative"
        ),
    }

def _release(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    versions = []
    for name in ("pyproject.toml", "Cargo.toml"):
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', _read(root / name))
        if match: versions.append({"path":name, "version":match.group(1)})
    kind = str(args.get("bump") or "patch").lower(); kind = kind if kind in {"major", "minor", "patch"} else "patch"
    def bump(v):
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", v)
        if not match: return None
        a,b,c = map(int, match.groups()); return f"{a+1}.0.0" if kind == "major" else f"{a}.{b+1}.0" if kind == "minor" else f"{a}.{b}.{c+1}"
    return {"bump":kind, "versions":[{**x, "next_version":bump(x["version"])} for x in versions], "mutates":False}


def _mcp_ids(root: Path, needle: str) -> list[str]:
    ids = set()
    for path in (Path.home()/".kitt"/"mcp.json", root/".kitt"/"mcp.json"):
        try: data = json.loads(_read(path))
        except Exception: continue
        if isinstance(data, dict):
            for key, value in data.items():
                if needle in (str(key)+json.dumps(value)).lower(): ids.add(str(key))
    return sorted(ids)


def _github(root: Path) -> dict[str, Any]:
    remote = _run(root, ["git", "remote", "get-url", "origin"]).strip()
    if "://" in remote:
        try:
            p = urlsplit(remote); remote = urlunsplit((p.scheme, p.hostname or "", p.path, p.query, p.fragment))
        except Exception: pass
    return {"origin":remote or None, "mcp_servers":_mcp_ids(root, "github"), "network_actions":"opt-in"}


def _database(root: Path) -> dict[str, Any]:
    text = "\n".join(_read(root/n) for n in ("pom.xml", "build.gradle", "pyproject.toml", "package.json")).lower()
    tech = [x for x in ("postgresql", "mysql", "mariadb", "sqlite", "mongodb", "redis", "flyway", "liquibase", "prisma") if x in text]
    return {"technologies":tech, "migration_risk":_migration(root, {})["risk"], "connections":"not opened by default"}


def _browser(root: Path) -> dict[str, Any]:
    text = (_read(root/"package.json")+_read(root/"pyproject.toml")).lower()
    return {"frameworks":[x for x in ("playwright", "puppeteer", "selenium", "cypress") if x in text], "mcp_servers":_mcp_ids(root, "browser")}


def _cloud(root: Path) -> dict[str, Any]:
    signals, files = set(), []
    for path in _files(root, {".tf", ".yaml", ".yml", ".json"}, 4000):
        rel = _rel(root,path).lower()
        if path.suffix == ".tf": signals.add("terraform"); files.append(rel)
        if "cloudformation" in rel or path.name.lower() == "template.yaml": signals.add("aws-cloudformation"); files.append(rel)
        if any(x in f"/{rel}" for x in ("/k8s/", "/kubernetes/", "/helm/")): signals.add("kubernetes"); files.append(rel)
    return {"platform_signals":sorted(signals), "descriptors":sorted(set(files))[:128], "credentials":"never read by default"}


def _observability(root: Path) -> dict[str, Any]:
    text = "\n".join(_read(root/n) for n in ("pyproject.toml", "package.json", "pom.xml", "build.gradle", "Cargo.toml")).lower()
    names = ("opentelemetry", "sentry", "datadog", "prometheus", "grafana", "newrelic")
    return {"detected":[x for x in names if x in text]}


_SPECS = {
    "project-intel":("project_intel", "Inspect languages, frameworks and build systems.", {}),
    "test-impact":("test_impact", "Plan targeted tests for changed files.", {"paths":"optional changed paths"}),
    "lsp":("lsp_inspect", "Discover language servers without launching them.", {}),
    "openapi":("openapi_inspect", "Inspect OpenAPI/Swagger contracts.", {}),
    "migration-guard":("migration_guard", "Detect risky database migrations.", {"paths":"optional migration paths"}),
    "git-worktree":("git_worktree", "Inspect worktrees and return a governed creation plan.", {"branch":"optional branch", "base":"optional base", "path":"optional path"}),
    "quality-report":("quality_report", "Build the deterministic verification plan.", {"paths":"optional changed paths", "full":"full verification"}),
    "dependency-audit":("dependency_audit", "Inspect dependency manifests and lockfiles.", {}),
    "ci":("ci_inspect", "Inspect local CI configuration.", {}),
    "container":(
        "container_inspect",
        "Inspect Docker/Podman/Kubernetes descriptors, local runtimes and governed command plans.",
        {
            "action":"inspect|compose-config|compose-ps|compose-logs|compose-build|compose-up|compose-down|compose-restart|k8s-get|k8s-describe|k8s-diff|k8s-apply|k8s-rollout-status",
            "runtime":"optional docker|podman",
            "service":"optional compose service",
            "namespace":"optional Kubernetes namespace",
            "resource":"optional Kubernetes resource",
            "manifest":"optional workspace-relative manifest path",
        },
    ),
    "release":("release_plan", "Produce a non-mutating release/version plan.", {"bump":"major|minor|patch"}),
    "github":("github_inspect", "Inspect local GitHub integration state.", {}),
    "database":("database_inspect", "Inspect database technologies without connecting.", {}),
    "browser":("browser_inspect", "Inspect browser automation without launching it.", {}),
    "cloud":("cloud_inspect", "Inspect cloud/IaC signals without credentials/network.", {}),
    "observability":("observability_inspect", "Inspect local observability integrations.", {}),
}


def _execute(plugin_id: str, root: Path, args: dict[str, Any]) -> dict[str, Any]:
    fn = {"project-intel":lambda:_intel(root), "test-impact":lambda:_test_impact(root,args), "lsp":lambda:_lsp(root), "openapi":lambda:_openapi(root), "migration-guard":lambda:_migration(root,args), "git-worktree":lambda:_worktree(root,args), "quality-report":lambda:_quality(root,args), "dependency-audit":lambda:_dependencies(root), "ci":lambda:_ci(root), "container":lambda:_container(root,args), "release":lambda:_release(root,args), "github":lambda:_github(root), "database":lambda:_database(root), "browser":lambda:_browser(root), "cloud":lambda:_cloud(root), "observability":lambda:_observability(root)}.get(plugin_id)
    if fn is None: raise ValueError(f"unknown builtin plugin: {plugin_id}")
    return fn()


def install(ctx, plugin_id: str):
    if plugin_id not in _SPECS: raise ValueError(f"unknown builtin plugin: {plugin_id}")
    root = _root(ctx); name, description, schema = _SPECS[plugin_id]
    def handler(args=None):
        return json.dumps(_execute(plugin_id, root, args if isinstance(args, dict) else {}), ensure_ascii=False, sort_keys=True)
    ctx.tools.register(name, handler, description=description, schema=schema)
    return None
