param(
  [string]$Ref = $(if ($env:KITT_AGENT_REF) { $env:KITT_AGENT_REF } else { 'main' }),
  [switch]$NoNative,
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Repo = if ($env:KITT_AGENT_REPO) { $env:KITT_AGENT_REPO } else { 'https://github.com/rfdetoni/kitt-agent-cli.git' }
$Root = if ($env:KITT_AGENT_HOME) { $env:KITT_AGENT_HOME } else { Join-Path $env:LOCALAPPDATA 'KITT\agent-cli' }
$Bin = if ($env:KITT_BIN_DIR) { $env:KITT_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'KITT\bin' }
$Src = Join-Path $Root 'src'
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

New-Item -ItemType Directory -Force -Path $Root, $Bin | Out-Null
if (-not (Test-Path (Join-Path $Src '.git'))) {
  Remove-Item -LiteralPath $Src -Recurse -Force -ErrorAction SilentlyContinue
  & git clone --filter=blob:none --no-checkout $Repo $Src
}
& git -C $Src remote set-url origin $Repo
& git -C $Src fetch --force --depth 1 origin $Ref
& git -C $Src checkout --detach --force FETCH_HEAD
& git -C $Src clean -ffd

$Vpy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Vpy)) {
  Remove-Item -LiteralPath $Venv -Recurse -Force -ErrorAction SilentlyContinue
  & $Python.Source -m venv $Venv
}
& $Vpy -m pip install --disable-pip-version-check -U pip wheel | Out-Null

$Native = $false
if (-not $NoNative -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
  Remove-Item -LiteralPath $Dist -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $Dist | Out-Null
  try {
    & $Vpy -m pip install --disable-pip-version-check -U 'maturin>=1.8,<2' | Out-Null
    & $Vpy (Join-Path $Src 'packaging\build_native_release.py') --out $Dist
    if ($LASTEXITCODE -ne 0) { throw 'native build failed' }
    $Wheel = Get-ChildItem -Path $Dist -Filter '*.whl' | Select-Object -First 1
    if (-not $Wheel) { throw 'native wheel not produced' }
    & $Vpy -m pip install --disable-pip-version-check --force-reinstall $Wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw 'native install failed' }
    $Native = $true
  } catch {
    Write-Warning "Native build unavailable; using portable Python package. $($_.Exception.Message)"
  }
}
if (-not $Native) {
  & $Vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall $Src
  if ($LASTEXITCODE -ne 0) { throw 'K.I.T.T. installation failed' }
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
$Backend = & $Vpy -c "from kitt.native.bridge import NativeCodeEngine; print(NativeCodeEngine(r'$Src').status.backend)"
Write-Host "K.I.T.T. Agent CLI installed/updated at $Root (backend: $Backend)."
Write-Host 'Open a new terminal and run: kitt'
