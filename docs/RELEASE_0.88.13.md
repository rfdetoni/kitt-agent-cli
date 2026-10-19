# Agent CLI 0.88.13 — Debian Wine + WineHQ Staging RE toolbox

- **Guest image 0.86.11**: Debian `wine` and native `wine64` remain a baseline, with real WineHQ `wine-staging` installed separately under `/opt/wine-staging`. WineHQ's signed Debian Trixie repository is restricted with `Signed-By` and a checked 40-digit public-key fingerprint. Both amd64 and i386 Staging payloads are required for the official launcher.
- Build checks execute `wine --version`, `wine64 --version`, `wine-staging --version` and record exact installed package revisions in `/usr/share/kitt/re-wine-packages.lock`. The image is separate from the host and model-owned workspaces.
- `kitt re lab upgrade-linux --approve` replaces an existing *validated and owned* Linux toolbox from the new pinned local image only after networkless preflight; preserves the bind-mounted workspace and Windows container and attempts rollback by immutable old-image ID.
- Python regressions cover authorization, before-remove preflight, source preservation, rollback and image contracts. The CI image workflow tests both wine and full profiles.
- [Guest documentation](RE_WINE_STAGING.md).

No Windows Oracle credentials or samples are baked into the image. WineHQ binary packages do not automatically include an editable Wine-Staging source tree. Do not assume Windows/Wine conformance until a guest pilot is run in the operator's laboratory.
