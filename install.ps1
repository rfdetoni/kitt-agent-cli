param(
  [string]$Ref = $(if ($env:KITT_AGENT_REF) { $env:KITT_AGENT_REF } else { 'main' }),
  [switch]$NoNative,
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Repo = if ($env:KITT_AGENT_REPO) { $env:KITT_AGENT_REPO } else { 'https://github.com/rfdetoni/kitt-agent-cli.git' }
$AssistantRepo = if ($env:KITT_ASSISTANT_REPO) { $env:KITT_ASSISTANT_REPO } else { 'https://github.com/rfdetoni/kitt-assistant.git' }
$WorkersRepo = if ($env:KITT_AI_WORKERS_REPO) { $env:KITT_AI_WORKERS_REPO } else { 'https://github.com/rfdetoni/kitt-ai-workers.git' }
$ToolboxRepo = if ($env:KITT_TOOLBOX_REPO) { $env:KITT_TOOLBOX_REPO } else { 'https://github.com/rfdetoni/kitt-toolbox.git' }
$AssistantRef = if ($env:KITT_ASSISTANT_REF) { $env:KITT_ASSISTANT_REF } else { 'main' }
$WorkersRef = if ($env:KITT_AI_WORKERS_REF) { $env:KITT_AI_WORKERS_REF } else { 'main' }
$ToolboxRef = if ($env:KITT_TOOLBOX_REF) { $env:KITT_TOOLBOX_REF } else { 'main' }
$Root = if ($env:KITT_AGENT_HOME) { $env:KITT_AGENT_HOME } else { Join-Path $env:LOCALAPPDATA 'KITT\agent-cli' }
$Bin = if ($env:KITT_BIN_DIR) { $env:KITT_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'KITT\bin' }
$Src = Join-Path $Root 'src'
$AssistantSrc = Join-Path $Root 'assistant'
$WorkersSrc = Join-Path $Root 'ai-workers'
$ToolboxSrc = Join-Path $Root 'toolbox'
$Venv = Join-Path $Root 'venv'
$Dist = Join-Path $Root 'dist-native'
$Launcher = Join-Path $Bin 'kitt.cmd'

if ($Uninstall) {
  Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $Launcher -Force -ErrorAction SilentlyContinue
  Write-Host 'K.I.T.T. Agent CLI removed.'
  exit 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is required' }
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw 'Python 3.12+ is required' }
& $Python.Source -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ required'"

function Sync-Repo([string]$RepoUrl, [string]$RepoRef, [string]$Dest) {
  if (-not (Test-Path (Join-Path $Dest '.git'))) {
    Remove-Item -LiteralPath $Dest -Recurse -Force -ErrorAction SilentlyContinue
    & git clone --filter=blob:none --no-checkout $RepoUrl $Dest
    if ($LASTEXITCODE -ne 0) { throw "git clone failed: $RepoUrl" }
  }
  & git -C $Dest remote set-url origin $RepoUrl
  & git -C $Dest fetch --force --depth 1 origin $RepoRef
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed: $RepoRef" }
  & git -C $Dest checkout --detach --force FETCH_HEAD
  if ($LASTEXITCODE -ne 0) { throw "git checkout failed: $RepoRef" }
  & git -C $Dest clean -ffd
}

New-Item -ItemType Directory -Force -Path $Root, $Bin | Out-Null
Sync-Repo $Repo $Ref $Src
Sync-Repo $AssistantRepo $AssistantRef $AssistantSrc
Sync-Repo $WorkersRepo $WorkersRef $WorkersSrc

$Vpy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Vpy)) {
  Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
  & $Python.Source -m venv $Venv
}
& $Vpy -m pip install --disable-pip-version-check -U pip wheel | Out-Null

# Install the portable control plane first, then its separately owned companion packages.
& $Vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall $Src
if ($LASTEXITCODE -ne 0) { throw 'K.I.T.T. Agent installation failed' }
$AssistantRuntime = Join-Path $AssistantSrc 'packages\kitt-assistant-runtime'
$EvolutionPkg = Join-Path $WorkersSrc 'packages\kitt-evolution'
$EvalsPkg = Join-Path $WorkersSrc 'packages\kitt-evals'
& $Vpy -m pip install --disable-pip-version-check --no-deps --upgrade --force-reinstall $AssistantRuntime $EvolutionPkg $EvalsPkg
if ($LASTEXITCODE -ne 0) { throw 'K.I.T.T. companion package installation failed' }

$Native = $false
if (-not $NoNative -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
  try {
    Sync-Repo $ToolboxRepo $ToolboxRef $ToolboxSrc
    Remove-Item -LiteralPath $Dist -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Dist | Out-Null
    & $Vpy -m pip install --disable-pip-version-check -U 'maturin>=1.8,<2' | Out-Null
    & $Vpy (Join-Path $ToolboxSrc 'packaging\build_native_release.py') --out $Dist
    if ($LASTEXITCODE -ne 0) { throw 'native build failed' }
    $Wheel = Get-ChildItem -Path $Dist -Filter '*.whl' | Select-Object -First 1
    if (-not $Wheel) { throw 'native wheel not produced' }
    & $Vpy -m pip install --disable-pip-version-check --no-deps --force-reinstall $Wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw 'native install failed' }
    $Native = $true
  } catch {
    Write-Warning "Shared native acceleration unavailable; keeping portable Python backend. $($_.Exception.Message)"
  }
}

$KittExe = Join-Path $Venv 'Scripts\kitt.exe'
Set-Content -Path $Launcher -Encoding Ascii -Value "@echo off`r`n`"$KittExe`" %*"
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$Parts = @($UserPath -split ';' | Where-Object { $_ })
if ($Parts -notcontains $Bin) {
  [Environment]::SetEnvironmentVariable('Path', (($Parts + $Bin) -join ';'), 'User')
  $env:Path = "$Bin;$env:Path"
}
& $KittExe --help | Out-Null
& $Vpy -c "import kitt.daemon.client, kitt.remote.server, kitt.evolution, kitt.evals.corpus; print('KITT companion packages: ok')"
if ($LASTEXITCODE -ne 0) { throw 'K.I.T.T. companion package smoke test failed' }
$Backend = & $Vpy -c "from kitt.native.bridge import NativeCodeEngine; print(NativeCodeEngine(r'$Src').status.backend)"
Write-Host "K.I.T.T. Agent CLI installed/updated at $Root (backend: $Backend; native wheel: $Native)."
Write-Host 'Open a new terminal and run: kitt'
