# KITT disposable Windows Oracle, executed by Dockur OEM only on first setup.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\KittOracle'
$oem = 'C:\OEM'
New-Item -Path $root -ItemType Directory -Force | Out-Null
[IO.File]::WriteAllText("$root\\bootstrap.status", 'PROVISIONING')
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
}
if (!(Test-Path 'C:\Windows\System32\OpenSSH\sshd.exe')) {
    throw 'Windows OpenSSH.Server is not installed; check Features-on-Demand'
}
$account = 'kittprobe'
if (!(Get-LocalUser -Name $account -ErrorAction SilentlyContinue)) {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    $secret = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force
    New-LocalUser -Name $account -Password $secret -PasswordNeverExpires -Description 'KITT probe-only service account' | Out-Null
}
$administrators = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop)
if (@($administrators | Where-Object { $_.Name -match '(\\|^)kittprobe$' }).Count -ne 0) {
    throw 'KITT probe account must not be an administrator'
}
foreach ($name in @('kitt-guest-rpc.ps1','event_probe.exe','manifest.json')) {
    Copy-Item -LiteralPath (Join-Path $oem $name) -Destination $root -Force
}
Copy-Item -LiteralPath (Join-Path $oem 'client_ed25519.pub') -Destination "$root\authorized_keys" -Force
$sshRoot = 'C:\ProgramData\ssh'
New-Item -Path $sshRoot -ItemType Directory -Force | Out-Null
$hostKey = Join-Path $sshRoot 'ssh_host_ed25519_key'
Copy-Item -LiteralPath (Join-Path $oem 'ssh_host_ed25519_key') -Destination $hostKey -Force
& icacls.exe $hostKey /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot lock SSH host key ACL' }
& icacls.exe $root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' 'kittprobe:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot lock probe directory ACL' }
& icacls.exe "$root\authorized_keys" /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' 'kittprobe:R' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot lock SSH authorized keys ACL' }
$config = @'
Port 22
AddressFamily inet
HostKey C:/ProgramData/ssh/ssh_host_ed25519_key
PasswordAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
AllowUsers kittprobe
PermitTTY no
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
MaxSessions 1
Match User kittprobe
    AuthorizedKeysFile C:/KittOracle/authorized_keys
    ForceCommand powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/KittOracle/kitt-guest-rpc.ps1
'@
[IO.File]::WriteAllText((Join-Path $sshRoot 'sshd_config'), $config, (New-Object Text.UTF8Encoding($false)))
& 'C:\Windows\System32\OpenSSH\sshd.exe' -t -f (Join-Path $sshRoot 'sshd_config')
if ($LASTEXITCODE -ne 0) { throw 'Invalid OpenSSH configuration' }
Set-Service -Name sshd -StartupType Automatic
Get-NetFirewallRule -DisplayName 'KITT Oracle SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'KITT Oracle SSH' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress '172.30.0.0/16' -Profile Any | Out-Null
if ((Get-Service sshd).Status -eq 'Running') { Restart-Service sshd } else { Start-Service sshd }
# Dockur injects OEM files into C:\OEM. Remove the copied host private key.
Remove-Item -LiteralPath (Join-Path $oem 'ssh_host_ed25519_key') -Force
[IO.File]::WriteAllText("$root\bootstrap.status", 'READY')
