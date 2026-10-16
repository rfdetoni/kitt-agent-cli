# Fixed protocol: no shell, uploads, downloads, scripts or arbitrary arguments.
$ErrorActionPreference = 'Stop'
$root = 'C:\KittOracle'
function Hash([byte[]]$raw) {
    $s=[Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($s.ComputeHash($raw))).Replace('-','').ToLowerInvariant() }
    finally { $s.Dispose() }
}
function Json($v) { ConvertTo-Json -InputObject $v -Depth 6 -Compress }
try {
    $line=[Console]::ReadLine()
    if (!$line -or [Text.Encoding]::UTF8.GetByteCount($line) -gt 4096) { throw 'frame' }
    $request=ConvertFrom-Json -InputObject $line
    if ((@($request.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'method,probe,version' -or $request.version -ne 1 -or
        $request.method -cnotin @('probe.info','probe.run')) { throw 'request' }
    $mf=Join-Path $root 'manifest.json'
    $raw=[IO.File]::ReadAllBytes($mf)
    if ($raw.Length -gt 65536) { throw 'manifest size' }
    $manifest=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($raw))
    if ((@($manifest.PSObject.Properties.Name) -join ',') -cne 'probes') { throw 'manifest schema' }
    $entries=@($manifest.probes.PSObject.Properties)
    if ($entries.Count -lt 1 -or $entries.Count -gt 16) { throw 'count' }
    $allowed=@{}
    foreach ($item in $entries) {
        $entry=$item.Value
        if ($item.Name -cnotmatch '^[A-Za-z0-9_-]{1,64}$' -or
            (@($entry.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'path,sha256' -or
            $entry.path -cnotmatch '^[A-Za-z0-9_-]{1,60}\.exe$' -or $entry.sha256 -cnotmatch '^[a-f0-9]{64}$') {
            throw 'registry'
        }
        $path=Join-Path $root $entry.path
        $file=Get-Item -LiteralPath $path -ErrorAction Stop
        if ($file.Length -gt 8388608 -or (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'file' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw 'hash' }
        $allowed[$item.Name]=@{file=$path; hash=$entry.sha256}
    }
    if ($request.method -ceq 'probe.info') {
        if ($null -ne $request.probe) { throw 'probe not null' }
        $guest=[ordered]@{platform='Windows';release=[Environment]::OSVersion.Version.ToString();
            architecture=if([Environment]::Is64BitOperatingSystem){'AMD64'}else{'x86'};
            manifest_sha256=Hash $raw;probe_count=$allowed.Count}
        [Console]::Out.WriteLine((Json ([ordered]@{version=1;guest=$guest})))
        exit 0
    }
    if ($request.probe -isnot [string] -or !$allowed.ContainsKey($request.probe)) { throw 'probe id' }
    $probe=$allowed[$request.probe]
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $si=New-Object Diagnostics.ProcessStartInfo
    $si.FileName=$probe.file
    $si.WorkingDirectory=$root
    $si.UseShellExecute=$false
    $si.CreateNoWindow=$true
    $si.RedirectStandardOutput=$true
    $si.RedirectStandardError=$true
    $proc=New-Object Diagnostics.Process
    $proc.StartInfo=$si
    [void]$proc.Start()
    $outTask=$proc.StandardOutput.ReadToEndAsync()
    $errTask=$proc.StandardError.ReadToEndAsync()
    $timedOut= -not $proc.WaitForExit(15000)
    if($timedOut){$proc.Kill();$proc.WaitForExit()}
    [void][Threading.Tasks.Task]::WaitAll(@($outTask,$errTask),1000)
    $timer.Stop()
    $stdout=[string]$outTask.Result
    $stderr=[string]$errTask.Result
    $truncate=$stdout.Length -gt 65536 -or $stderr.Length -gt 65536
    $stdout=$stdout.Substring(0,[Math]::Min(65536,$stdout.Length))
    $stderr=$stderr.Substring(0,[Math]::Min(65536,$stderr.Length))
    $receipt=[ordered]@{duration_ms=[Math]::Round($timer.Elapsed.TotalMilliseconds,2);
        exit_code=if($timedOut){-1}else{[int]$proc.ExitCode};platform='Windows';probe=[string]$request.probe;
        probe_sha256=[string]$probe.hash;release=[Environment]::OSVersion.Version.ToString();
        schema_version=1;stderr=$stderr;stdout=$stdout;timed_out=[bool]$timedOut;truncated=[bool]$truncate}
    $receipt['sha256']=Hash ([Text.Encoding]::UTF8.GetBytes((Json $receipt)))
    [Console]::Out.WriteLine((Json ([ordered]@{version=1;receipt=$receipt})))
    exit 0
} catch {
    [Console]::Out.WriteLine('{"version":1,"error":"Oracle protocol/integrity check failed"}')
    exit 2
}
