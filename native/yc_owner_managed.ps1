# Run only from exact bytes verified by the owner in a fresh system PowerShell.
param(
    [ValidateSet('Prepare','Check','Run','Disable','AuditAgent')][string]$Mode = 'Check',
    [Parameter(Mandatory=$true)][string]$AgentSid,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$NativeSource,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [string]$Cache,
    [string]$OwnerSid,
    [string]$Nonce
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$NativeSha256 = '4088e014b0e4632e591e6ac485436fa477d6749fb208497e8ba0c37926bdc6ec'
$CandidateSha256 = 'f6a27a4fed7111194d5d3418f2955b9baa52919ab3ec8c926b75b2264461895d'
$CandidateId = 'sha256:14f643cb899c6bcec43cb2fa417e952d9c9d1a0ba13c5fb6e23a2d1e9e9ea6df'
$SourceRef = '1f7d89d9a8cd2352c6df865e8915f66171475972'
$PythonHash = 'f05e28d161c6b15af64a7cb7f08b4a22b3a6b03eee71baee24ea557b3bdd5798'
$YcHash = '7dc58200b8ecba6913ec3f9961d0fda21ee3b67fa6972dad0cffdde5198acb94'
$YcSize = 196325832
$Utf8 = [Text.UTF8Encoding]::new($false)

function Require([bool]$Ok,[string]$Code) { if (-not $Ok) { throw $Code } }
function Read-Bytes([string]$Path,[long]$Limit) {
    $s = [IO.File]::Open($Path,'Open','Read','Read')
    try {
        Require ($s.Length -le $Limit) 'YC_OWNER_INPUT_LIMIT'
        $raw = New-Object byte[] ([int]$s.Length)
        $offset = 0
        while ($offset -lt $raw.Length) {
            $count = $s.Read($raw,$offset,$raw.Length-$offset)
            Require ($count -gt 0) 'YC_OWNER_SHORT_READ'
            $offset += $count
        }
        return ,$raw
    } finally { $s.Dispose() }
}
function Write-New([string]$Path,[byte[]]$Raw) {
    $s = [IO.File]::Open($Path,'CreateNew','Write','None')
    try { $s.Write($Raw,0,$Raw.Length); $s.Flush($true) } finally { $s.Dispose() }
}
function Json-Bytes($Value) { return ,$Utf8.GetBytes(($Value | ConvertTo-Json -Depth 12 -Compress)) }
function Get-Exact([string]$Relative,[string]$Url,[string]$Path,[string]$Hash,[long]$Size) {
    if ($Cache) {
        $raw = Read-Bytes (Join-Path $Cache $Relative) $Size
        Require (($Size -eq 16777216 -or $raw.Length -eq $Size) -and
            [YcOwnerNative.Guard]::Hash($raw) -eq $Hash) 'YC_OWNER_SOURCE_MISMATCH'
        Write-New $Path $raw
        return
    }
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Timeout = 10000; $request.ReadWriteTimeout = 5000; $request.AllowAutoRedirect = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $response = $request.GetResponse()
    try {
        Require ($response.ContentLength -le $Size) 'YC_OWNER_DOWNLOAD_LIMIT'
        $source = $response.GetResponseStream()
        $target = [IO.File]::Open($Path,'CreateNew','Write','None')
        try {
            $buffer = New-Object byte[] 65536
            $total = 0L
            while (($count = $source.Read($buffer,0,$buffer.Length)) -gt 0) {
                $total += $count
                Require ($total -le $Size -and $watch.Elapsed.TotalSeconds -le 60) 'YC_OWNER_DOWNLOAD_LIMIT'
                $target.Write($buffer,0,$count)
            }
            $target.Flush($true)
        } finally { $target.Dispose(); $source.Dispose() }
    } finally { $response.Dispose() }
    $raw = Read-Bytes $Path $Size
    Require (($Size -eq 16777216 -or $raw.Length -eq $Size) -and
        [YcOwnerNative.Guard]::Hash($raw) -eq $Hash) 'YC_OWNER_SOURCE_MISMATCH'
}
function Expand-Python([string]$Release,[bool]$Extract) {
    $raw = Read-Bytes (Join-Path $Release 'python.zip') 16777216
    Require ([YcOwnerNative.Guard]::Hash($raw) -eq $PythonHash) 'YC_OWNER_PYTHON_ARCHIVE_MISMATCH'
    $memory = [IO.MemoryStream]::new($raw,$false)
    $zip = [IO.Compression.ZipArchive]::new($memory,[IO.Compression.ZipArchiveMode]::Read)
    try {
        Require ($zip.Entries.Count -le 256) 'YC_OWNER_ARCHIVE_LIMIT'
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $expected = @{}
        $total = 0L
        foreach ($item in $zip.Entries) {
            Require ($item.FullName -match '^[A-Za-z0-9_.-]+$' -and $item.FullName -notin @('.','..') -and
                $names.Add($item.FullName)) 'YC_OWNER_ARCHIVE_PATH_INVALID'
            $total += $item.Length
            Require ($total -le 67108864) 'YC_OWNER_ARCHIVE_LIMIT'
            $stream = $item.Open()
            $buffer = [IO.MemoryStream]::new()
            try { $stream.CopyTo($buffer); $bytes=$buffer.ToArray() }
            finally { $stream.Dispose(); $buffer.Dispose() }
            $expected[$item.FullName] = @{size=$bytes.Length;sha256=[YcOwnerNative.Guard]::Hash($bytes)}
            if ($Extract) { Write-New (Join-Path (Join-Path $Release 'python') $item.FullName) $bytes }
        }
        foreach ($name in @('python.exe','python314.dll','python314.zip','python314._pth')) {
            Require ($names.Contains($name)) 'YC_OWNER_PYTHON_INCOMPLETE'
        }
        return $expected
    } finally { $zip.Dispose(); $memory.Dispose() }
}
function Hold-Release($Lease,[string]$Release) {
    [void]$Lease.HoldFile((Join-Path $Release 'python.zip'),$PythonHash)
    $expected = Expand-Python $Release $false
    $python = Join-Path $Release 'python'
    $pins = @($Lease.HoldTree($python))
    Require ($pins.Count -eq $expected.Count + 1) 'YC_OWNER_PYTHON_FILESET_MISMATCH'
    # HoldTree returns every file held so far, including python.zip.
    foreach ($pin in $pins) {
        if ($pin.Path -eq (Join-Path $Release 'python.zip')) { continue }
        $name = $pin.Path.Substring($python.Length+1)
        Require ($expected.ContainsKey($name) -and $expected[$name].size -eq $pin.Size -and
            $expected[$name].sha256 -eq $pin.Sha256) 'YC_OWNER_PYTHON_FILE_MISMATCH'
    }
    $component = Join-Path $Release 'component'
    $before = $pins.Count
    $all = @($Lease.HoldTree($component))
    Require ($all.Count -eq $before + $Manifest.files.Count + 1) 'YC_OWNER_COMPONENT_FILESET_MISMATCH'
    $rows = @{}
    foreach ($row in $Manifest.files) { $rows[$row.path] = $row }
    foreach ($pin in $all) {
        if (-not $pin.Path.StartsWith($component+'\',[StringComparison]::OrdinalIgnoreCase)) { continue }
        $relative = $pin.Path.Substring($component.Length+1).Replace('\','/')
        if ($relative -eq 'manifest.json') {
            Require ($pin.Sha256 -eq $CandidateSha256) 'YC_OWNER_MANIFEST_MISMATCH'
        } else {
            Require ($rows.ContainsKey($relative) -and $pin.Size -eq $rows[$relative].size -and
                $pin.Sha256 -eq $rows[$relative].sha256) 'YC_OWNER_COMPONENT_FILE_MISMATCH'
        }
    }
    $yc = $Lease.HoldFile((Join-Path $Release 'yc.exe'),$YcHash)
    Require ($yc.Size -eq $YcSize) 'YC_OWNER_YC_SIZE_MISMATCH'
    $top = @([IO.Directory]::GetFileSystemEntries($Release) | ForEach-Object { [IO.Path]::GetFileName($_) } | Sort-Object)
    Require (($top -join '|') -ceq 'component|python|python.zip|yc.exe') 'YC_OWNER_RELEASE_FILESET_MISMATCH'
}
function Read-State([string]$Path) {
    $lease = New-Object YcOwnerNative.FileLease
    try {
        [void]$lease.HoldFile($Path,$null)
        return ($Utf8.GetString((Read-Bytes $Path 16384)) | ConvertFrom-Json)
    } finally { $lease.Dispose() }
}
function Check-Root {
    $marker = Read-State (Join-Path $Destination 'owner.json')
    Require ($marker.schema -eq 'yc-owner-managed/v1' -and $marker.owner_sid -eq [YcOwnerNative.Guard]::OwnerSid -and
        $marker.agent_sid -eq $AgentSid) 'YC_OWNER_ROOT_BINDING_MISMATCH'
    $journal = Read-State (Join-Path $Destination 'deployment.json')
    Require ($journal.phase -eq 'complete') 'YC_OWNER_DEPLOYMENT_PENDING'
    $active = Read-State (Join-Path $Destination 'active.json')
    Require ($active.generation -eq $journal.generation -and $active.component_id -eq $journal.component_id -and
        $active.enabled -eq $journal.enabled) 'YC_OWNER_STATE_MISMATCH'
    return $active
}
function Publish-State($Value) {
    $journal = @{schema='yc-owner-deployment/v1';phase='prepared';generation=$Value.generation;
        component_id=$Value.component_id;enabled=$Value.enabled}
    $pending = Join-Path $Destination ('journal-'+$Value.generation+'.new')
    Write-New $pending (Json-Bytes $journal)
    $path = Join-Path $Destination 'deployment.json'
    if ([IO.File]::Exists($path)) { [IO.File]::Replace($pending,$path,$null) }
    else { [IO.File]::Move($pending,$path) }
    $next = Join-Path $Destination ('active-'+$Value.generation+'.new')
    Write-New $next (Json-Bytes $Value)
    $activePath = Join-Path $Destination 'active.json'
    if ([IO.File]::Exists($activePath)) {
        $backup = Join-Path (Join-Path $Destination 'history') ($Value.generation+'.json')
        [IO.File]::Replace($next,$activePath,$backup)
    } else { [IO.File]::Move($next,$activePath) }
    $journal.phase = 'complete'
    Write-New $pending (Json-Bytes $journal)
    [IO.File]::Replace($pending,$path,$null)
}

$mutex = $null; $locked = $false; $lease = $null
try {
    Require ([Environment]::OSVersion.Platform -eq 'Win32NT' -and [IntPtr]::Size -eq 8) 'YC_OWNER_WINDOWS_X64_REQUIRED'
    Require (-not ('YcOwnerNative.Guard' -as [type])) 'YC_OWNER_FRESH_NATIVE_HOST_REQUIRED'
    $native = Read-Bytes $NativeSource 131072
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = ([BitConverter]::ToString($sha.ComputeHash($native))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    Require ($digest -eq $NativeSha256) 'YC_OWNER_NATIVE_SOURCE_DIGEST_MISMATCH'
    Add-Type -TypeDefinition ([Text.Encoding]::UTF8.GetString($native)) -Language CSharp
    if ($Mode -eq 'AuditAgent') {
        [YcOwnerNative.Guard]::WriteCapabilityAudit($AgentSid,$OwnerSid,$Nonce,$Destination)
        exit 0
    }
    [YcOwnerNative.Guard]::AssertStandardOwner($AgentSid)
    Add-Type -AssemblyName System.IO.Compression
    $manifestRaw = Read-Bytes $ManifestPath 16384
    Require ([YcOwnerNative.Guard]::Hash($manifestRaw) -eq $CandidateSha256) 'YC_OWNER_CANDIDATE_PIN_MISMATCH'
    $Manifest = $Utf8.GetString($manifestRaw) | ConvertFrom-Json
    Require ($Manifest.artifact_id -eq $CandidateId -and $Manifest.source_ref -eq $SourceRef -and
        $Manifest.execution_enabled -eq $true -and $Manifest.required_bootstrap_profile -eq 'yc-owner-native/v1') 'YC_OWNER_CANDIDATE_INVALID'
    $Destination = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    $rootLease = [YcOwnerNative.FileLease]::ParentChain([IO.Path]::GetDirectoryName($Destination))
    try {
        $security = [Security.AccessControl.MutexSecurity]::new()
        $security.SetAccessRuleProtection($true,$false)
        $me = [Security.Principal.SecurityIdentifier]::new([YcOwnerNative.Guard]::OwnerSid)
        $security.SetOwner($me)
        foreach ($sid in @($me.Value,'S-1-5-18','S-1-5-32-544')) {
            $security.AddAccessRule([Security.AccessControl.MutexAccessRule]::new(
                [Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
        }
        $created = $false
        $mutex = [Threading.Mutex]::new($false,('Global\agent-toolchain-yc-owner-'+$me.Value),[ref]$created,$security)
        $acl = $mutex.GetAccessControl()
        Require ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $me.Value -and
            $acl.AreAccessRulesProtected) 'YC_OWNER_MUTEX_UNOWNED'
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            Require ($rule.IdentityReference.Value -in @($me.Value,'S-1-5-18','S-1-5-32-544') -and
                $rule.AccessControlType -eq 'Allow') 'YC_OWNER_MUTEX_UNOWNED'
        }
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { throw 'YC_OWNER_PREVIOUS_PROCESS_LOST' }
        Require $locked 'YC_OWNER_ALREADY_RUNNING'
        $release = Join-Path (Join-Path $Destination 'releases') $CandidateId.Replace(':','-')
        if ($Mode -eq 'Prepare') {
            $existing = [IO.Directory]::Exists($Destination)
            if ($existing) {
                $active = Check-Root
                if ($active.component_id -eq $CandidateId) {
                    $lease = New-Object YcOwnerNative.FileLease
                    Hold-Release $lease $release
                    $changed = -not $active.enabled
                    if ($changed) { Publish-State @{schema='yc-owner-active/v1';component_id=$CandidateId;generation=[guid]::NewGuid().ToString('N');enabled=$true} }
                    [void](Check-Root)
                    @{status='active_verified';component_id=$CandidateId;changed=$changed;installed_acceptance=$false} | ConvertTo-Json -Compress
                    exit 0
                }
            } else {
                [YcOwnerNative.Guard]::CreatePrivateDirectory($Destination)
                Write-New (Join-Path $Destination 'owner.json') (Json-Bytes @{schema='yc-owner-managed/v1';owner_sid=$me.Value;agent_sid=$AgentSid})
                foreach ($dir in @('releases','history','sessions')) { [YcOwnerNative.Guard]::CreatePrivateDirectory((Join-Path $Destination $dir)) }
            }
            # Record intent BEFORE downloads/extraction. Interrupted preparation cannot be adopted.
            $generation = [guid]::NewGuid().ToString('N')
            $initial = Join-Path $Destination ('prepare-'+$generation+'.new')
            Write-New $initial (Json-Bytes @{phase='prepared';generation=$generation;component_id=$CandidateId})
            $journalPath = Join-Path $Destination 'deployment.json'
            if ($existing) { [IO.File]::Replace($initial,$journalPath,$null) } else { [IO.File]::Move($initial,$journalPath) }
            [YcOwnerNative.Guard]::CreatePrivateDirectory($release)
            foreach ($dir in @('python','component')) { [YcOwnerNative.Guard]::CreatePrivateDirectory((Join-Path $release $dir)) }
            Get-Exact 'python.zip' 'https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-amd64.zip' (Join-Path $release 'python.zip') $PythonHash 16777216
            [void](Expand-Python $release $true)
            Get-Exact 'yc.exe' 'https://storage.yandexcloud.net/yandexcloud-yc/release/1.39.0/windows/amd64/yc.exe' (Join-Path $release 'yc.exe') $YcHash $YcSize
            foreach ($row in $Manifest.files) {
                $target = Join-Path (Join-Path $release 'component') $row.path
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
                $source = $Manifest.sources.($row.path)
                Get-Exact ('component/'+$row.path) ('https://raw.githubusercontent.com/dilukhin/opencode_permissions/'+$SourceRef+'/'+$source) $target $row.sha256 $row.size
            }
            Write-New (Join-Path (Join-Path $release 'component') 'manifest.json') $manifestRaw
            $lease = New-Object YcOwnerNative.FileLease
            Hold-Release $lease $release
            Publish-State @{schema='yc-owner-active/v1';component_id=$CandidateId;generation=$generation;enabled=$true}
            [void](Check-Root)
            @{status='active_verified';component_id=$CandidateId;changed=$true;python_started=$false;cloud_invocations=0;installed_acceptance=$false} | ConvertTo-Json -Compress
            exit 0
        }
        $active = Check-Root
        Require ($active.component_id -eq $CandidateId) 'YC_OWNER_UNKNOWN_ACTIVE_RELEASE'
        if ($Mode -eq 'Disable') {
            if ($active.enabled) { Publish-State @{schema='yc-owner-active/v1';component_id=$CandidateId;generation=[guid]::NewGuid().ToString('N');enabled=$false} }
            [void](Check-Root)
            @{status='disabled';previous_release_preserved=$true;cloud_invocations=0} | ConvertTo-Json -Compress
            exit 0
        }
        Require ($active.enabled -eq $true) 'YC_OWNER_DISABLED'
        $lease = New-Object YcOwnerNative.FileLease
        Hold-Release $lease $release
        [void]$lease.HoldFile((Join-Path $Destination 'owner.json'),$null)
        [void]$lease.HoldFile((Join-Path $Destination 'active.json'),$null)
        [void]$lease.HoldFile((Join-Path $Destination 'deployment.json'),$null)
        if ($Mode -eq 'Check') {
            @{status='active_verified';component_id=$CandidateId;python_started=$false;cloud_invocations=0;installed_acceptance=$false} | ConvertTo-Json -Compress
            exit 0
        }
        Require (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) 'YC_OWNER_LOCAL_TERMINAL_REQUIRED'
        $session = Join-Path (Join-Path $Destination 'sessions') ([guid]::NewGuid().ToString('N'))
        [YcOwnerNative.Guard]::CreatePrivateDirectory($session)
        $auditPath = Join-Path $session 'capabilities.txt'
        $challenge = [guid]::NewGuid().ToString('D')
        [Console]::WriteLine('Required one-time read-only administrator AuditAgent step:')
        [Console]::WriteLine(('AgentSid={0}; OwnerSid={1}; Nonce={2}; Destination={3}' -f $AgentSid,$me.Value,$challenge,$auditPath))
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while (-not [IO.File]::Exists($auditPath)) {
            Require ($watch.Elapsed.TotalSeconds -lt 180) 'YC_OWNER_CAPABILITY_AUDIT_TIMEOUT'
            Start-Sleep -Milliseconds 200
        }
        [YcOwnerNative.Guard]::VerifyCapabilityAudit($AgentSid,$challenge,$auditPath)
        $cwd = Join-Path $session 'work'
        [YcOwnerNative.Guard]::CreatePrivateDirectory($cwd)
        $config = Join-Path $session 'bootstrap.json'
        Write-New $config (Json-Bytes @{schema='yc-owner-bootstrap/v1';owner_sid=$me.Value;agent_sid=$AgentSid;
            component_id=$CandidateId;component_manifest_sha256=$CandidateSha256;yc_path=(Join-Path $release 'yc.exe');
            cwd=$cwd;pipe_prefix=('yc59-'+[guid]::NewGuid().ToString('N'))})
        [void]$lease.HoldTree($session)
        $entry = Join-Path $release 'component/runtime/approval_owner/entry.py'
        $child = $lease.StartPython((Join-Path $release 'python/python.exe'),$entry,$config,$cwd,$AgentSid)
        $exit = $child.Wait(240000)
        Require ($exit -eq 0) 'YC_OWNER_SESSION_FAILED'
    } finally { $rootLease.Dispose() }
} catch {
    $code = $_.Exception.GetBaseException().Message
    if ($code -notmatch '^YC_OWNER_[A-Z0-9_]+$') { $code='YC_OWNER_MANAGED_REFUSED' }
    [Console]::Error.WriteLine($code)
    exit 2
} finally {
    if ($lease) { $lease.Dispose() }
    if ($locked) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
