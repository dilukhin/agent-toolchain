# Native primitives only on a disposable hosted runner. No installed-host task.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'EPHEMERAL_HOSTED_WINDOWS_REQUIRED' }
$repo = Split-Path $PSScriptRoot -Parent
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $repo 'native\yc_owner_native.cs'))) -Language CSharp
function Expect-Code([scriptblock]$Action, [string]$Code) {
    try { & $Action } catch {
        $item = $_.Exception
        while ($item.InnerException) { $item = $item.InnerException }
        if ($item.Message -ne $Code) { throw }
        return
    }
    throw "EXPECTED_REFUSAL_MISSING: $Code"
}
$nonce = [guid]::NewGuid().ToString('N')
$userName = 'ycboot_' + $nonce.Substring(0, 10)
$root = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('yc-native-' + $nonce)
$created = $false
$ownerCreated = $false
$rootCreated = $false
try {
    $password = ConvertTo-SecureString ('aA1!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
    $user = New-LocalUser -Name $userName -Password $password -AccountNeverExpires
    $created = $true
    Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545') -Member $user
    [YcOwnerNative.Guard]::AssertLocalAgentAccount($user.SID.Value)
    $owner = [YcOwnerNative.Guard]::OwnerSid
    Expect-Code { [YcOwnerNative.Guard]::AssertLocalAgentAccount($owner) } 'YC_OWNER_STANDARD_AGENT_ACCOUNT_REQUIRED'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($root)
    $rootCreated = $true
    Expect-Code { [YcOwnerNative.Guard]::CreatePrivateDirectory($root) } 'YC_OWNER_DESTINATION_EXISTS'
    $ownerName = 'ycown_' + $nonce.Substring(0, 10)
    $ownerPassword = ConvertTo-SecureString ('aA1!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
    $ownerUser = New-LocalUser -Name $ownerName -Password $ownerPassword -AccountNeverExpires
    $ownerCreated = $true
    Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545') -Member $ownerUser
    # Verify lookup/LSA rights also from the ordinary owner account, not just admin CI.
    $readable = [Security.AccessControl.DirectorySecurity]::new()
    $readable.SetAccessRuleProtection($true, $false)
    $readable.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $readable.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    $readable.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($ownerUser.SID,
        'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $root -AclObject $readable
    $exchange = Join-Path $root 'exchange'
    [void](New-Item -ItemType Directory -Path $exchange)
    $exchangeAcl = Get-Acl -LiteralPath $exchange
    $exchangeAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($ownerUser.SID,
        'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $exchange -AclObject $exchangeAcl
    $copiedSource = Join-Path $root 'native.cs'
    [IO.File]::WriteAllText($copiedSource, [IO.File]::ReadAllText((Join-Path $repo 'native\yc_owner_native.cs')))
    $checker = Join-Path $root 'check-rights.ps1'
    $checkerText = @'
param([string]$Source,[string]$Sid,[string]$Audit,[string]$Nonce)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition ([IO.File]::ReadAllText($Source)) -Language CSharp
[YcOwnerNative.Guard]::VerifyCapabilityAudit($Sid,$Nonce,$Audit)
try {
    [YcOwnerNative.Guard]::VerifyCapabilityAudit($Sid, [guid]::NewGuid().ToString('D'), $Audit)
    throw 'WRONG_NONCE_ACCEPTED'
} catch {
    if ($_.Exception.GetBaseException().Message -ne 'YC_OWNER_CAPABILITY_BINDING_INVALID') { throw }
}
$forged = Join-Path $PWD 'forged-capabilities.txt'
[IO.File]::WriteAllBytes($forged,[IO.File]::ReadAllBytes($Audit))
try {
    [YcOwnerNative.Guard]::VerifyCapabilityAudit($Sid,$Nonce,$forged)
    throw 'ORDINARY_FILE_ACCEPTED_AS_ADMIN_AUDIT'
} catch {
    if ($_.Exception.GetBaseException().Message -ne 'YC_OWNER_ADMIN_AUDIT_REQUIRED') { throw }
}
[Console]::WriteLine('STANDARD_OWNER_RIGHTS_PASS')
'@
    [IO.File]::WriteAllText($checker, $checkerText)
    $audit = Join-Path $root 'capabilities.txt'
    $auditNonce = [guid]::NewGuid().ToString('D')
    [YcOwnerNative.Guard]::WriteCapabilityAudit($user.SID.Value, $ownerUser.SID.Value, $auditNonce, $audit)
    Expect-Code { [YcOwnerNative.Guard]::WriteCapabilityAudit($user.SID.Value, $ownerUser.SID.Value, $auditNonce, $audit) } 'YC_OWNER_AUDIT_OUTPUT_EXISTS'
    $credential = [PSCredential]::new(($env:COMPUTERNAME + '\' + $ownerName), $ownerPassword)
    $out = Join-Path $exchange 'ordinary.out'
    $err = Join-Path $exchange 'ordinary.err'
    $arguments = @('-NoProfile', '-File', ('"' + $checker + '"'), ('"' + $copiedSource + '"'), $user.SID.Value,
                   ('"' + $audit + '"'), $auditNonce)
    $ordinary = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $arguments -Credential $credential -LoadUserProfile -WorkingDirectory $exchange `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $ordinaryHandle = $ordinary.Handle
    if (-not $ordinary.WaitForExit(30000)) { $ordinary.Kill(); throw 'STANDARD_OWNER_RIGHTS_TIMEOUT' }
    $ordinary.WaitForExit()
    if ($ordinary.ExitCode -ne 0 -or (Get-Content -LiteralPath $out -Raw).Trim() -ne 'STANDARD_OWNER_RIGHTS_PASS') {
        Get-Content -LiteralPath $err | Write-Host
        throw 'STANDARD_OWNER_RIGHTS_FAILED'
    }
    $payload = Join-Path $root 'payload'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($payload)
    $file = Join-Path $payload 'runtime.py'
    [IO.File]::WriteAllText($file, 'raise RuntimeError("MUST_NOT_EXECUTE")')
    $lease = New-Object YcOwnerNative.FileLease
    try {
        $pins = @($lease.HoldTree($payload))
        if ($pins.Count -ne 1) { throw 'FILESET_MISMATCH' }
        $blocked = $false
        try { [IO.File]::WriteAllText($file, 'changed') } catch [IO.IOException] { $blocked = $true }
        if (-not $blocked) { throw 'FILE_WRITE_NOT_BLOCKED_BY_LEASE' }
        $blocked = $false
        try { [IO.Directory]::Move($payload, ($payload + '-moved')) } catch [IO.IOException] { $blocked = $true }
        if (-not $blocked) { throw 'PARENT_RENAME_NOT_BLOCKED_BY_LEASE' }
    } finally { $lease.Dispose() }
    $lease = New-Object YcOwnerNative.FileLease
    try { Expect-Code { $lease.HoldFile($file, ('a' * 64)) } 'YC_OWNER_FILE_HASH_MISMATCH' }
    finally { $lease.Dispose() }
    $original = Get-Acl -LiteralPath $payload
    $unsafe = Get-Acl -LiteralPath $payload
    $unsafe.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID,
        'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $payload -AclObject $unsafe
    try {
        $lease = New-Object YcOwnerNative.FileLease
        try { Expect-Code { $lease.HoldTree($payload) } 'YC_OWNER_AGENT_WRITABLE_PATH' }
        finally { $lease.Dispose() }
    } finally { Set-Acl -LiteralPath $payload -AclObject $original }
    $hard = Join-Path $payload 'alias.py'
    [void](New-Item -ItemType HardLink -Path $hard -Target $file)
    try {
        $lease = New-Object YcOwnerNative.FileLease
        try { Expect-Code { $lease.HoldFile($file, $null) } 'YC_OWNER_HARDLINK_FORBIDDEN' }
        finally { $lease.Dispose() }
    } finally { Remove-Item -LiteralPath $hard }
    $other = Join-Path $root 'other'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($other)
    $link = Join-Path $payload 'junction'
    [void](New-Item -ItemType Junction -Path $link -Target $other)
    try {
        $lease = New-Object YcOwnerNative.FileLease
        try { Expect-Code { $lease.HoldTree($payload) } 'YC_OWNER_REPARSE_OR_FILE_TYPE' }
        finally { $lease.Dispose() }
    } finally { [IO.Directory]::Delete($link) }
    $archive = Join-Path $root 'python.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-amd64.zip' -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne
        'f05e28d161c6b15af64a7cb7f08b4a22b3a6b03eee71baee24ea557b3bdd5798') { throw 'OFFICIAL_PYTHON_ARCHIVE_MISMATCH' }
    $python = Join-Path $root 'python'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($python)
    Expand-Archive -LiteralPath $archive -DestinationPath $python
    $lease = New-Object YcOwnerNative.FileLease
    try {
        $pins = @($lease.HoldTree($python))
        if ($pins.Count -lt 10) { throw 'OFFICIAL_PYTHON_FILESET_INCOMPLETE' }
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = Join-Path $python 'python.exe'
        $start.Arguments = '-I -S -c "import sys; assert sys.version_info[:3] == (3,14,2); print(''NATIVE_PROTECTED_PYTHON_PASS'')"'
        $start.WorkingDirectory = $python
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.EnvironmentVariables.Clear()
        $start.EnvironmentVariables['SystemRoot'] = $env:SystemRoot
        $start.EnvironmentVariables['PATH'] = Join-Path $env:SystemRoot 'System32'
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit(10000)) { $process.Kill(); throw 'SYNTHETIC_PYTHON_TIMEOUT' }
        if ($process.ExitCode -ne 0 -or $process.StandardOutput.ReadToEnd().Trim() -ne 'NATIVE_PROTECTED_PYTHON_PASS') { throw 'SYNTHETIC_PYTHON_FAILED' }
    } finally { $lease.Dispose() }
    # The production supervisor must run as the ordinary owner; admin is only
    # this disposable fixture's orchestrator. Test attacker is the other account.
    # This exact tree was created by this fixture. Convert its creator-specific
    # ownership/allow ACE to the administrative source used by a different owner.
    Set-Acl -LiteralPath $python -AclObject $readable
    foreach ($item in Get-ChildItem -LiteralPath $python -Recurse -Force) {
        $itemAcl = [Security.AccessControl.FileSecurity]::new()
        $itemAcl.SetAccessRuleProtection($true, $false)
        $itemAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
            $itemAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'Allow'))
        }
        $itemAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($ownerUser.SID, 'ReadAndExecute', 'Allow'))
        Set-Acl -LiteralPath $item.FullName -AclObject $itemAcl
    }
    $rootAcl = Get-Acl -LiteralPath $root
    $rootAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID,
        'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $root -AclObject $rootAcl
    $supervisor = Join-Path $root 'supervisor.ps1'
    $supervisorText = @'
param([string]$Source,[string]$AgentSid,[string]$Python,[string]$Exchange)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition ([IO.File]::ReadAllText($Source)) -Language CSharp
$work = Join-Path $Exchange 'private-run'
[YcOwnerNative.Guard]::CreatePrivateDirectory($work)
$entry = Join-Path $work 'entry.py'
$config = Join-Path $work 'config.json'
$entryText = @"
import json, os, pathlib, subprocess, sys, time
p = pathlib.Path(sys.argv[1]).parent
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
allowed = {'PATH','SYSTEMROOT','TEMP','TMP','USERPROFILE','LC_CTYPE'}
assert set(os.environ).issubset(allowed), sorted(set(os.environ) - allowed)
mode = json.loads(pathlib.Path(sys.argv[1]).read_text())['mode']
if mode == 'timeout':
    time.sleep(60)
else:
    (p/'ready').write_text(str(os.getpid()))
    until = time.monotonic() + 30
    while not (p/'release').exists():
        if time.monotonic() > until: raise RuntimeError('TEST_RELEASE_TIMEOUT')
        time.sleep(.05)
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'])
    (p/'descendant.pid').write_text(str(child.pid))
"@
[IO.File]::WriteAllText($entry,$entryText)
[IO.File]::WriteAllText($config,'{"mode":"normal"}')
$lease = New-Object YcOwnerNative.FileLease
try {
    [void]$lease.HoldTree($Python)
    [void]$lease.HoldTree($work)
    $child = $lease.StartPython((Join-Path $Python 'python.exe'),$entry,$config,$work,$AgentSid)
    [IO.File]::WriteAllText((Join-Path $Exchange 'supervised.pid'), [string]$child.Pid)
    if ($child.Wait(45000) -ne 0) { throw 'SUPERVISED_CHILD_FAILED' }
} finally { $lease.Dispose() }
$descendant = [int]([IO.File]::ReadAllText((Join-Path $work 'descendant.pid')))
try { $still = Get-Process -Id $descendant -ErrorAction Stop } catch { $still = $null }
if ($still) { throw 'DESCENDANT_SURVIVED_JOB_CLOSE' }
$timeoutConfig = Join-Path $work 'timeout.json'
[IO.File]::WriteAllText($timeoutConfig,'{"mode":"timeout"}')
$lease = New-Object YcOwnerNative.FileLease
try {
    [void]$lease.HoldTree($Python)
    [void]$lease.HoldTree($work)
    $child = $lease.StartPython((Join-Path $Python 'python.exe'),$entry,$timeoutConfig,$work,$AgentSid)
    try { [void]$child.Wait(500); throw 'PROCESS_TIMEOUT_NOT_ENFORCED' }
    catch { if ($_.Exception.GetBaseException().Message -ne 'YC_OWNER_PROCESS_TIMEOUT') { throw } }
} finally { $lease.Dispose() }
try { $still = Get-Process -Id $child.Pid -ErrorAction Stop } catch { $still = $null }
if ($still) { throw 'TIMEOUT_CHILD_SURVIVED' }
[Console]::WriteLine('PROTECTED_SUPERVISOR_PASS')
'@
    [IO.File]::WriteAllText($supervisor, $supervisorText)
    $attacker = Join-Path $root 'attacker.ps1'
    $attackerText = @'
param([int]$Target)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Attacker {
    [DllImport("kernel32.dll",SetLastError=true)] public static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("advapi32.dll",SetLastError=true)] public static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
}
"@
foreach ($right in @(1,8,16,32,64,128,256,512,1024,2048,0x20000,0x40000,0x80000)) {
    $handle = [Attacker]::OpenProcess($right,$false,$Target)
    if ($handle -ne [IntPtr]::Zero) { [void][Attacker]::CloseHandle($handle); throw "PROCESS_RIGHT_GRANTED: $right" }
}
$query = [Attacker]::OpenProcess(0x1000,$false,$Target)
if ($query -eq [IntPtr]::Zero) { throw 'IDENTITY_QUERY_DENIED' }
try {
    foreach ($right in @(1,2,4,0x10,0x20,0x40,0x80,0x100,0x20000,0x40000,0x80000)) {
        $token = [IntPtr]::Zero
        if ([Attacker]::OpenProcessToken($query,$right,[ref]$token)) {
            [void][Attacker]::CloseHandle($token); throw "TOKEN_RIGHT_GRANTED: $right"
        }
    }
    $token = [IntPtr]::Zero
    if (-not [Attacker]::OpenProcessToken($query,8,[ref]$token)) { throw 'TOKEN_IDENTITY_QUERY_DENIED' }
    [void][Attacker]::CloseHandle($token)
} finally { [void][Attacker]::CloseHandle($query) }
[Console]::WriteLine('AGENT_MUTATION_DENIED_PASS')
'@
    [IO.File]::WriteAllText($attacker, $attackerText)
    $supervisedOut = Join-Path $exchange 'supervised.out'
    $supervisedErr = Join-Path $exchange 'supervised.err'
    $arguments = @('-NoProfile','-File',('"'+$supervisor+'"'),('"'+$copiedSource+'"'),$user.SID.Value,
                   ('"'+$python+'"'),('"'+$exchange+'"'))
    $ordinary = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $arguments -Credential $credential -LoadUserProfile -WorkingDirectory $exchange `
        -RedirectStandardOutput $supervisedOut -RedirectStandardError $supervisedErr -PassThru
    $ordinaryHandle = $ordinary.Handle
    $ready = Join-Path $exchange 'private-run\ready'
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $ready)) {
        if ($ordinary.HasExited -or $watch.Elapsed.TotalSeconds -gt 30) {
            Get-Content -LiteralPath $supervisedErr | Write-Host
            throw 'SUPERVISED_START_FAILED'
        }
        Start-Sleep -Milliseconds 100
    }
    $childId = [int]([IO.File]::ReadAllText($ready))
    $agentCredential = [PSCredential]::new(($env:COMPUTERNAME+'\'+$userName),$password)
    $attackOut = Join-Path $env:RUNNER_TEMP ('agent-'+$nonce+'.out')
    $attackErr = Join-Path $env:RUNNER_TEMP ('agent-'+$nonce+'.err')
    $attack = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList @('-NoProfile','-File',('"'+$attacker+'"'),$childId) -Credential $agentCredential `
        -LoadUserProfile -WorkingDirectory $root -RedirectStandardOutput $attackOut -RedirectStandardError $attackErr -PassThru
    $attackHandle = $attack.Handle
    if (-not $attack.WaitForExit(20000)) { $attack.Kill(); throw 'AGENT_MUTATION_TEST_TIMEOUT' }
    $attack.WaitForExit()
    if ($attack.ExitCode -ne 0 -or (Get-Content -LiteralPath $attackOut -Raw).Trim() -ne 'AGENT_MUTATION_DENIED_PASS') {
        Get-Content -LiteralPath $attackErr | Write-Host
        throw 'AGENT_MUTATION_TEST_FAILED'
    }
    [IO.File]::WriteAllText((Join-Path $exchange 'private-run\release'), 'release')
    if (-not $ordinary.WaitForExit(30000)) { $ordinary.Kill(); throw 'SUPERVISED_STOP_TIMEOUT' }
    $ordinary.WaitForExit()
    if ($ordinary.ExitCode -ne 0 -or (Get-Content -LiteralPath $supervisedOut -Raw).Trim() -ne 'PROTECTED_SUPERVISOR_PASS') {
        Get-Content -LiteralPath $supervisedErr | Write-Host
        throw 'PROTECTED_SUPERVISOR_FAILED'
    }
    Write-Host 'PASS native file/process/token protection, ordinary owner supervisor, hostile account denial, timeout and descendant cleanup. No YC/approval/installed host.'
} finally {
    if ($created) { Remove-LocalUser -Name $userName }
    if ($ownerCreated) { Remove-LocalUser -Name $ownerName }
    if ($rootCreated -and (Test-Path -LiteralPath $root)) { Remove-Item -LiteralPath $root -Recurse -Force }
}
