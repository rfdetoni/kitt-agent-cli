# K.I.T.T. Agent CLI — Quick Start

This guide gets a new user from zero to a validated coding session without requiring knowledge of KITT internals.

## 1. Requirements

- Python 3.14+
- Git
- A model/provider reachable either directly or through KITT Reverse Proxy
- Rust/Cargo is optional for the standalone Agent; native acceleration remains optional

For the complete ecosystem, prefer the installer in `rfdetoni/kitt`.

## 2. Install

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.ps1 | iex
```

To explicitly avoid native acceleration, use `--no-native` on POSIX or `-NoNative` on PowerShell.

## 3. Verify the installation

```bash
kitt --version
kitt doctor
kitt models
```

The current supported interpreter policy is documented in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## 4. Open a workspace

```bash
kitt --root /path/to/project
```

Inside the TUI, use `Ctrl+P` to discover actions and `F12` to configure the provider/model.

A useful first read-only request is:

> Inspect this repository, identify the main modules and explain how requests flow from the CLI to the runtime. Do not modify files.

Then try a bounded implementation request such as:

> Add a unit test for the existing validation behavior. Inspect the relevant implementation first, make the smallest change and run the targeted test.

KITT should gather evidence before mutating files and should report the validation it actually executed.

## 5. Reverse Proxy

When using KITT Reverse Proxy, start or select the service from the **KITT Reverse Proxy** contextual panel or configure the provider through the F12 model/provider wizard.

KITT supports assigning different endpoints to Context, Principal/Code and Validation roles. This is useful when one web provider is used for repository/context reasoning and another for code execution.

See [docs/REVERSE_PROXY_CONTROL.md](docs/REVERSE_PROXY_CONTROL.md).

## 6. Approvals and autonomy

KITT keeps filesystem, process, network and control-plane mutations behind runtime policy.

Use the autonomy controls from the TUI or `/autonomy`. Read-only work remains distinct from mutation-capable work. A pending human approval remains actionable until it is decided, cancelled or invalidated by changed mutation preconditions.

Do not interpret an autonomy mode as bypassing the runtime capability/security boundary.

## 7. Validation

For repository changes, ask KITT to run the smallest useful validation first and then broaden only when needed.

For KITT itself:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m compileall -q kitt tests
python packaging/verify_cleanroom.py
```

## 8. Accessibility

- Keyboard navigation is the canonical interaction path.
- `F10` or `/mouse` toggles mouse reporting.
- Set `NO_COLOR=1` for a color-free interface.
- Set `KITT_HIGH_CONTRAST=1` for the high-contrast palette.
- `TERM=dumb` uses plain terminal styling.

See [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md).

## 9. Next reading

- [README.md](README.md) — project overview
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution workflow
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Agent architecture
- [docs/DOMAIN_ARCHITECTURE.md](docs/DOMAIN_ARCHITECTURE.md) — ecosystem bounded contexts
- [docs/RUNTIME_REFERENCE.md](docs/RUNTIME_REFERENCE.md) — model-facing runtime operations
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — benchmark methodology
