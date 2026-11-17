param([string]$Python,[string]$Package,[string]$Report)
$ErrorActionPreference='Stop'
& $Python -I -S -B (Join-Path $Package 'fixture.py') $Report
$rc=$LASTEXITCODE
[IO.File]::WriteAllText((Join-Path $Report 'host-exit.txt'),[string]$rc)
$clock=[Diagnostics.Stopwatch]::StartNew()
while(-not [IO.File]::Exists((Join-Path $Report 'close'))){
    if($clock.Elapsed.TotalSeconds -gt 60){exit 2};Start-Sleep -Milliseconds 50
}
exit $rc
