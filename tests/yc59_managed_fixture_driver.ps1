# Called only by the disposable hosted Windows fixture, as its ordinary owner.
param([string]$Seed,[string]$Source,[string]$Manifest,[string]$AgentSid,[string]$Cache,[string]$Destination)
$ErrorActionPreference='Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'EPHEMERAL_HOSTED_WINDOWS_REQUIRED' }
function Invoke-Managed([string]$Mode,[int]$Expected,[string]$ExpectedCode='') {
    $out=Join-Path $PWD ('managed-'+[guid]::NewGuid().ToString('N')+'.out')
    $err=$out+'.err'
    $args=@('-NoProfile','-File',('"'+$Seed+'"'),'-Mode',$Mode,'-AgentSid',$AgentSid,
        '-Destination',('"'+$Destination+'"'),'-NativeSource',('"'+$Source+'"'),
        '-ManifestPath',('"'+$Manifest+'"'),'-Cache',('"'+$Cache+'"'))
    $p=Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $args -WorkingDirectory $PWD -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $handle=$p.Handle
    if (-not $p.WaitForExit(120000)) { $p.Kill(); throw 'MANAGED_FIXTURE_TIMEOUT' }
    $p.WaitForExit()
    if ($p.ExitCode -ne $Expected) {
        Get-Content -LiteralPath $out | Write-Host
        Get-Content -LiteralPath $err | Write-Host
        throw ('MANAGED_UNEXPECTED_EXIT_'+$Mode)
    }
    if ($ExpectedCode) {
        if (([IO.File]::ReadAllText($err)).Trim() -ne $ExpectedCode) {
            Get-Content -LiteralPath $err | Write-Host
            throw ('MANAGED_UNEXPECTED_REFUSAL_'+$Mode)
        }
    }
    if ($Expected -eq 0) { return ([IO.File]::ReadAllText($out) | ConvertFrom-Json) }
}
$prepared=Invoke-Managed 'Prepare' 0
if ($prepared.status -ne 'active_verified' -or -not $prepared.changed -or $prepared.python_started -or
    $prepared.cloud_invocations -ne 0 -or $prepared.installed_acceptance) { throw 'MANAGED_PREPARE_RESULT_INVALID' }
$release=Join-Path (Join-Path $Destination 'releases') $prepared.component_id.Replace(':','-')
$Cache=$Cache+'-MUST-NOT-BE-READ'
$again=Invoke-Managed 'Prepare' 0
if ($again.changed) { throw 'MANAGED_REPEAT_CHANGED_STATE' }
[void](Invoke-Managed 'Check' 0)
# No redirected/automated terminal may reach the real entry or request credentials.
[void](Invoke-Managed 'Run' 2 'YC_OWNER_LOCAL_TERMINAL_REQUIRED')
$entry=Join-Path $release 'component/runtime/approval_owner/entry.py'
$original=[IO.File]::ReadAllBytes($entry)
[IO.File]::AppendAllText($entry,'# drift')
[void](Invoke-Managed 'Check' 2 'YC_OWNER_COMPONENT_FILE_MISMATCH')
[void](Invoke-Managed 'Prepare' 2 'YC_OWNER_COMPONENT_FILE_MISMATCH')
[IO.File]::WriteAllBytes($entry,$original)
$extra=Join-Path $release 'python/foreign.dll'
[IO.File]::WriteAllText($extra,'SYNTHETIC_MUST_NOT_LOAD')
[void](Invoke-Managed 'Check' 2 'YC_OWNER_PYTHON_FILESET_MISMATCH')
[IO.File]::Delete($extra) # disposable fixture-created sentinel only
$journal=Join-Path $Destination 'deployment.json'
$original=[IO.File]::ReadAllBytes($journal)
[IO.File]::WriteAllText($journal,'{"phase":"prepared"}')
[void](Invoke-Managed 'Prepare' 2 'YC_OWNER_DEPLOYMENT_PENDING')
if ([IO.File]::ReadAllText($journal) -ne '{"phase":"prepared"}') { throw 'PARTIAL_JOURNAL_REPAIRED' }
[IO.File]::WriteAllBytes($journal,$original)
$disabled=Invoke-Managed 'Disable' 0
if ($disabled.status -ne 'disabled' -or -not [IO.File]::Exists($entry)) { throw 'MANAGED_DISABLE_LOST_RELEASE' }
[void](Invoke-Managed 'Check' 2 'YC_OWNER_DISABLED')
$reactivated=Invoke-Managed 'Prepare' 0
if (-not $reactivated.changed) { throw 'MANAGED_REACTIVATION_NOT_RECORDED' }
[void](Invoke-Managed 'Check' 0)
if (@([IO.Directory]::GetFiles((Join-Path $Destination 'history'))).Count -ne 2) { throw 'MANAGED_CHECKPOINTS_MISSING' }
# Cross-process, cross-root lock uses the same protected owner-wide kernel object.
$me=[Security.Principal.WindowsIdentity]::GetCurrent().User
$security=[Security.AccessControl.MutexSecurity]::new()
$security.SetAccessRuleProtection($true,$false);$security.SetOwner($me)
foreach ($sid in @($me.Value,'S-1-5-18','S-1-5-32-544')) {
    $security.AddAccessRule([Security.AccessControl.MutexAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
}
$created=$false
$mutex=[Threading.Mutex]::new($false,('Global\agent-toolchain-yc-owner-'+$me.Value),[ref]$created,$security)
try {
    if (-not $mutex.WaitOne(0)) { throw 'FIXTURE_MUTEX_UNEXPECTED_OWNER' }
    try { [void](Invoke-Managed 'Check' 2 'YC_OWNER_ALREADY_RUNNING') }
    finally { $mutex.ReleaseMutex() }
} finally { $mutex.Dispose() }
[Console]::WriteLine('MANAGED_OWNER_ACTIVATION_PASS')
