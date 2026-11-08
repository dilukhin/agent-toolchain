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
    function Get-VerifiedFixtureFile([string]$Url,[string]$Path,[string]$Hash,[long]$Size) {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Timeout = 10000; $request.ReadWriteTimeout = 5000; $request.AllowAutoRedirect = $false
        $watch = [Diagnostics.Stopwatch]::StartNew()
        $response = $request.GetResponse()
        try {
            if ($response.ContentLength -gt $Size) { throw 'FIXTURE_SOURCE_SIZE_LIMIT' }
            $source = $response.GetResponseStream()
            $target = [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try {
                $buffer = New-Object byte[] 65536
                $total = 0L
                while (($count = $source.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $total += $count
                    if ($total -gt $Size -or $watch.Elapsed.TotalSeconds -gt 60) { throw 'FIXTURE_SOURCE_DOWNLOAD_LIMIT' }
                    $target.Write($buffer,0,$count)
                }
                $target.Flush($true)
            } finally { $target.Dispose(); $source.Dispose() }
        } finally { $response.Dispose() }
        if ((Get-Item -LiteralPath $Path).Length -ne $Size -or
            (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) { throw 'FIXTURE_SOURCE_DIGEST_MISMATCH' }
    }
    function Protect-FixtureSource([string]$Path) {
        foreach ($item in @((Get-Item -LiteralPath $Path)) + @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
            if ($item.PSIsContainer) {
                Set-Acl -LiteralPath $item.FullName -AclObject $readable
            } else {
                $acl = [Security.AccessControl.FileSecurity]::new()
                $acl.SetAccessRuleProtection($true,$false)
                $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
                foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
                    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                        [Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
                }
                $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($ownerUser.SID,'ReadAndExecute','Allow'))
                Set-Acl -LiteralPath $item.FullName -AclObject $acl
            }
        }
    }
    $component = Join-Path $root 'component'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($component)
    $componentPins = Get-Content (Join-Path $PSScriptRoot 'yc59_context_component_pins.json') -Raw | ConvertFrom-Json
    foreach ($row in $componentPins.manifest.files) {
        $sourcePath = $componentPins.source_map.($row.path)
        if ($row.path -notmatch '^[A-Za-z0-9_./-]+$' -or $row.path.Contains('..') -or -not $sourcePath) { throw 'FIXTURE_PIN_PATH_INVALID' }
        $target = Join-Path $component $row.path
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
        Get-VerifiedFixtureFile ('https://raw.githubusercontent.com/dilukhin/opencode_permissions/' +
            $componentPins.source_ref + '/' + $sourcePath) $target $row.sha256 $row.size
    }
    [IO.File]::WriteAllText((Join-Path $component 'manifest.json'),$componentPins.manifest_json,[Text.UTF8Encoding]::new($false))
    Protect-FixtureSource $component
    $yc = Join-Path $root 'yc.exe'
    Get-VerifiedFixtureFile 'https://storage.yandexcloud.net/yandexcloud-yc/release/1.39.0/windows/amd64/yc.exe' `
        $yc '7dc58200b8ecba6913ec3f9961d0fda21ee3b67fa6972dad0cffdde5198acb94' 196325832
    Protect-FixtureSource $yc
    $supervisor = Join-Path $root 'supervisor.ps1'
    $supervisorText = @'
param([string]$Source,[string]$AgentSid,[string]$Python,[string]$Exchange,[string]$Component,[string]$Yc)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition ([IO.File]::ReadAllText($Source)) -Language CSharp
$work = Join-Path $Exchange 'private-run'
[YcOwnerNative.Guard]::CreatePrivateDirectory($work)
$entry = Join-Path $work 'entry.py'
$config = Join-Path $work 'config.json'
$entryText = @"
import json, os, pathlib, subprocess, sys, time
p = pathlib.Path(sys.argv[1]).parent
def publish(name, text):
    temporary = p/(name + '.new')
    temporary.write_text(text)
    os.replace(temporary, p/name)
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
allowed = {'PATH','SYSTEMROOT','TEMP','TMP','USERPROFILE','LC_CTYPE'}
assert set(os.environ).issubset(allowed), sorted(set(os.environ) - allowed)
mode = json.loads(pathlib.Path(sys.argv[1]).read_text())['mode']
if mode == 'context':
    data = json.loads(pathlib.Path(sys.argv[1]).read_text())
    sys.path.insert(0, str(pathlib.Path(data['component'])/'runtime'))
    from yc_owner_windows_context import WindowsReadContext
    from yc_read_projection import SnapshotSelection
    from yc_snapshot_read import ReadError
    marker = 'SYNTHETIC_IAM_TOKEN_NEVER_FOR_CLOUD'
    sources = WindowsReadContext(selection=SnapshotSelection('synthetic-folder','synthetic-vm','synthetic-disk','synthetic-snapshot'),
        cloud_id='synthetic-cloud', observed_monotonic=time.monotonic(), yc_path=data['yc'], cwd=str(p),
        native_policy_path=str(pathlib.Path(data['component'])/'policy/native/rules.v1.json'),
        yc_policy_path=str(pathlib.Path(data['component'])/'policy/yc/yc_policy.v1.json'),
        native_sha256=data['native_sha'], yc_policy_sha256=data['yc_policy_sha'], iam_token=marker)
    try:
        operation = sources.profile.operation()
        assert marker not in json.dumps(operation)
        assert operation['scope']['snapshot_id'] == 'synthetic-snapshot'
        assert operation['execution']['executable_identity'].startswith('win-file:')
        assert sources.veto.check(sources.current()) == 'ask'
        for argv in operation['execution']['argv']:
            try:
                sources.start(operation['execution'], tuple([*argv, '--help']))
                raise AssertionError('UNBOUND_ARGV_ACCEPTED')
            except ReadError as error:
                assert error.code == 'YC_OWNER_SPAWN_BINDING_CHANGED'
    finally:
        sources.close()
    try:
        sources.current()
        raise AssertionError('CLOSED_CONTEXT_ACCEPTED')
    except ReadError:
        pass
    publish('context.pass', 'PROTECTED_CONTEXT_PASS')
elif mode == 'timeout':
    time.sleep(60)
else:
    publish('ready', str(os.getpid()))
    until = time.monotonic() + 30
    while not (p/'release').exists():
        if time.monotonic() > until: raise RuntimeError('TEST_RELEASE_TIMEOUT')
        time.sleep(.05)
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'])
    publish('descendant.pid', str(child.pid))
    while not (p/'finish').exists():
        if time.monotonic() > until: raise RuntimeError('TEST_FINISH_TIMEOUT')
        time.sleep(.05)
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
$contextConfig = Join-Path $work 'context.json'
$pins = Get-Content (Join-Path $Component 'manifest.json') -Raw | ConvertFrom-Json
$nativePin = $pins.files | Where-Object { $_.path -eq 'policy/native/rules.v1.json' }
$ycPin = $pins.files | Where-Object { $_.path -eq 'policy/yc/yc_policy.v1.json' }
[IO.File]::WriteAllText($contextConfig, (@{mode='context';component=$Component;yc=$Yc;
    native_sha=$nativePin.sha256;yc_policy_sha=$ycPin.sha256} | ConvertTo-Json -Compress))
$lease = New-Object YcOwnerNative.FileLease
try {
    [void]$lease.HoldTree($Python)
    [void]$lease.HoldTree($Component)
    [void]$lease.HoldFile($Yc,'7dc58200b8ecba6913ec3f9961d0fda21ee3b67fa6972dad0cffdde5198acb94')
    [void]$lease.HoldTree($work)
    $child = $lease.StartPython((Join-Path $Python 'python.exe'),$entry,$contextConfig,$work,$AgentSid)
    if ($child.Wait(30000) -ne 0) { throw 'PROTECTED_CONTEXT_FAILED' }
    if ([IO.File]::ReadAllText((Join-Path $work 'context.pass')) -ne 'PROTECTED_CONTEXT_PASS') { throw 'CONTEXT_RESULT_MISSING' }
} finally { $lease.Dispose() }
[Console]::WriteLine('PROTECTED_SUPERVISOR_PASS')
'@
    [IO.File]::WriteAllText($supervisor, $supervisorText)
    $attacker = Join-Path $root 'attacker.ps1'
    $attackerText = @'
param([int]$Target,[string]$NoQuery = 'false')
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
foreach ($right in @(1,2,8,16,32,64,128,256,512,1024,2048,0x20000,0x40000,0x80000)) {
    $handle = [Attacker]::OpenProcess($right,$false,$Target)
    if ($handle -ne [IntPtr]::Zero) { [void][Attacker]::CloseHandle($handle); throw "PROCESS_RIGHT_GRANTED: $right" }
}
$query = [Attacker]::OpenProcess(0x1000,$false,$Target)
if ($NoQuery -eq 'true') {
    if ($query -ne [IntPtr]::Zero) { [void][Attacker]::CloseHandle($query); throw 'DESCENDANT_QUERY_GRANTED' }
    [Console]::WriteLine('AGENT_MUTATION_DENIED_PASS')
    exit 0
}
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
                   ('"'+$python+'"'),('"'+$exchange+'"'),('"'+$component+'"'),('"'+$yc+'"'))
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
    $descendantFile = Join-Path $exchange 'private-run\descendant.pid'
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $descendantFile)) {
        if ($ordinary.HasExited -or $watch.Elapsed.TotalSeconds -gt 10) { throw 'DESCENDANT_NOT_STARTED' }
        Start-Sleep -Milliseconds 50
    }
    $descendantId = [int]([IO.File]::ReadAllText($descendantFile))
    $attack = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList @('-NoProfile','-File',('"'+$attacker+'"'),$descendantId,'true') -Credential $agentCredential `
        -LoadUserProfile -WorkingDirectory $root -RedirectStandardOutput $attackOut -RedirectStandardError $attackErr -PassThru
    $attackHandle = $attack.Handle
    if (-not $attack.WaitForExit(15000)) { $attack.Kill(); throw 'DESCENDANT_SECURITY_TEST_TIMEOUT' }
    $attack.WaitForExit()
    if ($attack.ExitCode -ne 0 -or (Get-Content -LiteralPath $attackOut -Raw).Trim() -ne 'AGENT_MUTATION_DENIED_PASS') {
        Get-Content -LiteralPath $attackErr | Write-Host
        throw 'DESCENDANT_DEFAULT_DACL_FAILED'
    }
    [IO.File]::WriteAllText((Join-Path $exchange 'private-run\finish'), 'finish')
    if (-not $ordinary.WaitForExit(45000)) { $ordinary.Kill(); throw 'SUPERVISED_STOP_TIMEOUT' }
    $ordinary.WaitForExit()
    if ($ordinary.ExitCode -ne 0 -or (Get-Content -LiteralPath $supervisedOut -Raw).Trim() -ne 'PROTECTED_SUPERVISOR_PASS') {
        Get-Content -LiteralPath $supervisedErr | Write-Host
        throw 'PROTECTED_SUPERVISOR_FAILED'
    }
    # Exercise the real managed seed, not a Python model of its state machine.
    $cache = Join-Path $root 'managed-cache'
    [void][IO.Directory]::CreateDirectory($cache)
    [IO.File]::Copy($archive,(Join-Path $cache 'python.zip'))
    [IO.File]::Copy($yc,(Join-Path $cache 'yc.exe'))
    $candidatePath = Join-Path $root 'candidate.json'
    [IO.File]::Copy((Join-Path $repo 'native/yc_owner_candidate.json'),$candidatePath)
    $candidate = Get-Content -LiteralPath $candidatePath -Raw | ConvertFrom-Json
    foreach ($row in $candidate.files) {
        $target = Join-Path (Join-Path $cache 'component') $row.path
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
        Get-VerifiedFixtureFile ('https://raw.githubusercontent.com/dilukhin/opencode_permissions/'+
            $candidate.source_ref+'/'+$candidate.sources.($row.path)) $target $row.sha256 $row.size
    }
    Protect-FixtureSource $cache
    $managedSeed = Join-Path $root 'managed.ps1'
    $managedDriver = Join-Path $root 'managed-driver.ps1'
    [IO.File]::Copy((Join-Path $repo 'native/yc_owner_managed.ps1'),$managedSeed)
    [IO.File]::Copy((Join-Path $PSScriptRoot 'yc59_managed_fixture_driver.ps1'),$managedDriver)
    $managedOut=Join-Path $exchange 'managed.out'
    $managedErr=Join-Path $exchange 'managed.err'
    $arguments=@('-NoProfile','-File',('"'+$managedDriver+'"'),('"'+$managedSeed+'"'),
        ('"'+$copiedSource+'"'),('"'+$candidatePath+'"'),$user.SID.Value,('"'+$cache+'"'),
        ('"'+(Join-Path $exchange 'managed-root')+'"'))
    $ordinary=Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $arguments -Credential $credential -LoadUserProfile -WorkingDirectory $exchange `
        -RedirectStandardOutput $managedOut -RedirectStandardError $managedErr -PassThru
    $ordinaryHandle=$ordinary.Handle
    if (-not $ordinary.WaitForExit(240000)) { $ordinary.Kill(); throw 'MANAGED_DRIVER_TIMEOUT' }
    $ordinary.WaitForExit()
    if ($ordinary.ExitCode -ne 0 -or ([IO.File]::ReadAllText($managedOut)).Trim() -ne 'MANAGED_OWNER_ACTIVATION_PASS') {
        Get-Content -LiteralPath $managedOut | Write-Host
        Get-Content -LiteralPath $managedErr | Write-Host
        throw 'MANAGED_DRIVER_FAILED'
    }
    Write-Host 'PASS native protection and managed prepare/check/disable/reactivation, exact files, pending refusal and owner-wide lock. No cloud/approval/installed host.'
} catch {
    [Console]::Error.WriteLine('NATIVE_FIXTURE_FAILURE: ' + $_.Exception.Message)
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    throw
} finally {
    # Only processes created and retained by this disposable fixture. Kill the
    # native parent first; its kill-on-close job releases all child file leases.
    foreach ($ownedProcess in @($ordinary,$attack)) {
        if ($ownedProcess -and -not $ownedProcess.HasExited) {
            $ownedProcess.Kill()
            [void]$ownedProcess.WaitForExit(5000)
        }
    }
    if ($created) { Remove-LocalUser -Name $userName }
    if ($ownerCreated) { Remove-LocalUser -Name $ownerName }
    if ($rootCreated -and (Test-Path -LiteralPath $root)) { Remove-Item -LiteralPath $root -Recurse -Force }
}
