# Bootstrap seed MUST be verified by the owner in a fresh OS PowerShell session.
# It does not trust an agent-writable Python to validate its own interpreter.
param(
    [ValidateSet('Check','PreparePython','AuditAgent')][string]$Mode = 'Check',
    [Parameter(Mandatory=$true)][string]$AgentSid,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$NativeSource,
    [string]$OwnerSid,
    [string]$Nonce
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$NativeSha256 = '67a2b8f709682dd7eefbbab8ae428c4114b213e825b7195720fa5f16dee2cc93'
$PythonUrl = 'https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-amd64.zip'
$PythonArchiveSha256 = 'f05e28d161c6b15af64a7cb7f08b4a22b3a6b03eee71baee24ea557b3bdd5798'

function Hash-Bytes([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
function Read-LockedBytes([string]$Path, [int]$Limit) {
    $file = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($file.Length -gt $Limit) { throw 'YC_OWNER_SEED_INPUT_LIMIT' }
        $raw = New-Object byte[] ([int]$file.Length)
        $offset = 0
        while ($offset -lt $raw.Length) {
            $count = $file.Read($raw, $offset, $raw.Length - $offset)
            if ($count -le 0) { throw 'YC_OWNER_SEED_SHORT_READ' }
            $offset += $count
        }
        return ,$raw
    } finally { $file.Dispose() }
}
try {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or [IntPtr]::Size -ne 8) { throw 'YC_OWNER_WINDOWS_X64_REQUIRED' }
    if ('YcOwnerNative.Guard' -as [type]) { throw 'YC_OWNER_FRESH_NATIVE_HOST_REQUIRED' }
    # Compile only the exact verified bytes, never a mutable source path.
    $native = Read-LockedBytes $NativeSource 131072
    if ((Hash-Bytes $native) -ne $NativeSha256) { throw 'YC_OWNER_NATIVE_SOURCE_DIGEST_MISMATCH' }
    Add-Type -TypeDefinition ([Text.Encoding]::UTF8.GetString($native)) -Language CSharp
    if ($Mode -eq 'AuditAgent') {
        [YcOwnerNative.Guard]::WriteCapabilityAudit($AgentSid, $OwnerSid, $Nonce, $Destination)
        @{schema='yc-owner-capability-audit/v1'; status='audit_written';
          cloud_invocations=0; deployment_authorized=$false} | ConvertTo-Json -Compress
        exit 0
    }
    [YcOwnerNative.Guard]::AssertInteractiveOwner($AgentSid)
    # The ordinary owner cannot query LSA rights. A one-time administrator reads
    # them; no account/group/privilege changes and no permanent privileged service.
    $requestRoot = $Destination + '.native-request-' + [guid]::NewGuid().ToString('N')
    [YcOwnerNative.Guard]::CreatePrivateDirectory($requestRoot)
    $capabilityFile = Join-Path $requestRoot 'capabilities.txt'
    $challenge = [guid]::NewGuid().ToString('D')
    [Console]::WriteLine('Required one-time native eligibility audit (read-only administrator step).')
    [Console]::WriteLine('Owner SID: ' + [YcOwnerNative.Guard]::OwnerSid)
    [Console]::WriteLine('Agent SID: ' + $AgentSid)
    [Console]::WriteLine('Nonce: ' + $challenge)
    [Console]::WriteLine('New audit file: ' + $capabilityFile)
    $auditWatch = [Diagnostics.Stopwatch]::StartNew()
    while (-not [IO.File]::Exists($capabilityFile)) {
        if ($auditWatch.Elapsed.TotalSeconds -ge 180) { throw 'YC_OWNER_CAPABILITY_AUDIT_TIMEOUT' }
        Start-Sleep -Milliseconds 200
    }
    [YcOwnerNative.Guard]::VerifyCapabilityAudit($AgentSid, $challenge, $capabilityFile)
    if ($Mode -eq 'Check') {
        $lease = New-Object YcOwnerNative.FileLease
        try {
            $pins = @($lease.HoldTree($Destination))
            @{ schema='yc-owner-native-check/v1'; status='protected_tree_observed';
               files=$pins.Count; python_started=$false; cloud_invocations=0;
               deployment_authorized=$false } | ConvertTo-Json -Compress
        } finally { $lease.Dispose() }
        exit 0
    }
    # Fresh owner-private directory only. Existing installation/ACLs are never adopted.
    [YcOwnerNative.Guard]::CreatePrivateDirectory($Destination)
    $journal = Join-Path $Destination 'prepare.json'
    [IO.File]::WriteAllText($journal, '{"schema":1,"component":"yc-owner-python","phase":"prepared"}', [Text.UTF8Encoding]::new($false))
    $archive = Join-Path $Destination 'python-3.14.2-embed-amd64.zip'
    $request = [Net.HttpWebRequest]::Create($PythonUrl)
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 5000
    $request.AllowAutoRedirect = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $response = $request.GetResponse()
    try {
        $inputStream = $response.GetResponseStream()
        $output = [IO.File]::Open($archive, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] 65536
            $total = 0
            while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $total += $count
                if ($total -gt 16777216 -or $watch.Elapsed.TotalSeconds -gt 60) { throw 'YC_OWNER_PYTHON_DOWNLOAD_LIMIT' }
                $output.Write($buffer, 0, $count)
            }
            $output.Flush($true)
        } finally { $output.Dispose(); $inputStream.Dispose() }
    } finally { $response.Dispose() }
    $raw = Read-LockedBytes $archive 16777216
    if ((Hash-Bytes $raw) -ne $PythonArchiveSha256) { throw 'YC_OWNER_PYTHON_ARCHIVE_DIGEST_MISMATCH' }
    $python = Join-Path $Destination 'python'
    [YcOwnerNative.Guard]::CreatePrivateDirectory($python)
    Add-Type -AssemblyName System.IO.Compression
    $memory = [IO.MemoryStream]::new($raw, $false)
    $zip = [IO.Compression.ZipArchive]::new($memory, [IO.Compression.ZipArchiveMode]::Read)
    try {
        if ($zip.Entries.Count -gt 256) { throw 'YC_OWNER_PYTHON_ARCHIVE_LIMIT' }
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $expanded = 0L
        foreach ($entry in $zip.Entries) {
            # Official embeddable distribution has a flat, exact namespace.
            if ($entry.FullName -notmatch '^[A-Za-z0-9_.-]+$' -or $entry.FullName -in @('.', '..') -or
                -not $names.Add($entry.FullName)) { throw 'YC_OWNER_PYTHON_ARCHIVE_PATH_INVALID' }
            $expanded += $entry.Length
            if ($expanded -gt 67108864) { throw 'YC_OWNER_PYTHON_ARCHIVE_LIMIT' }
            $target = [IO.File]::Open((Join-Path $python $entry.FullName), [IO.FileMode]::CreateNew,
                                     [IO.FileAccess]::Write, [IO.FileShare]::None)
            $source = $entry.Open()
            try { $source.CopyTo($target); $target.Flush($true) }
            finally { $source.Dispose(); $target.Dispose() }
        }
        if (-not $names.Contains('python.exe') -or -not $names.Contains('python314.dll') -or
            -not $names.Contains('python314.zip') -or -not $names.Contains('python314._pth')) {
            throw 'YC_OWNER_PYTHON_ARCHIVE_INCOMPLETE'
        }
    } finally { $zip.Dispose(); $memory.Dispose() }
    $lease = New-Object YcOwnerNative.FileLease
    try {
        $pins = @($lease.HoldTree($python))
        $manifest = @{schema='yc-owner-python/v1'; archive_sha256=$PythonArchiveSha256;
                      python_version='3.14.2'; files=@($pins | ForEach-Object {
                          @{path=[IO.Path]::GetFileName($_.Path); sha256=$_.Sha256; size=$_.Size}
                      }); execution_enabled=$false}
        [IO.File]::WriteAllText((Join-Path $Destination 'python-manifest.json'), ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    } finally { $lease.Dispose() }
    $complete = Join-Path $Destination 'prepare.complete'
    [IO.File]::WriteAllText($complete, '{"schema":1,"component":"yc-owner-python","phase":"complete"}', [Text.UTF8Encoding]::new($false))
    [IO.File]::Replace($complete, $journal, $null)
    @{schema='yc-owner-native-prepare/v1'; status='python_prepared'; python_started=$false;
      cloud_invocations=0; deployment_authorized=$false} | ConvertTo-Json -Compress
    exit 0
} catch {
    $errorItem = $_.Exception
    while ($errorItem.InnerException) { $errorItem = $errorItem.InnerException }
    $code = $errorItem.Message
    if ($code -notmatch '^YC_OWNER_[A-Z0-9_]+$') { $code = 'YC_OWNER_NATIVE_BOOTSTRAP_REFUSED' }
    [Console]::Error.WriteLine($code)
    exit 2
}
