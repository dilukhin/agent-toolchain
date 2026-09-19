# Disposable GitHub Windows runner only. Never run against a user installation.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This test requires a disposable GitHub Actions Windows runner.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The fixture requires an administrator to create its temporary standard user.'
}
$python = (Get-Command python -ErrorAction Stop).Source
& $python -c 'import sys; assert sys.version_info >= (3,13), sys.version; print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13+ is required to reproduce the legacy mkdir(0700) DACL.' }
$nonce = [guid]::NewGuid().ToString('N')
$userName = 'atc53_' + $nonce.Substring(0, 12)
$root = Join-Path $env:RUNNER_TEMP ('core access ' + $nonce)
$userCreated = $false
$rootCreated = $false
try {
    # No credentials are written to disk, arguments, or logs.
    $password = ConvertTo-SecureString ('aA1!' + [guid]::NewGuid().ToString('N')) -AsPlainText -Force
    $user = New-LocalUser -Name $userName -Password $password -AccountNeverExpires
    $userCreated = $true
    $usersGroup = Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $usersGroup -Member $user
    $credential = [PSCredential]::new("$env:COMPUTERNAME\$userName", $password)
    [void](New-Item -ItemType Directory -Path $root)
    $rootCreated = $true
    # Rights are set only on this newly created test root, before publication.
    $acl = Get-Acl -LiteralPath $root
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $user.SID, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $root -AclObject $acl
    $source = Join-Path $root 'source'
    [void](New-Item -ItemType Directory -Path $source)
    Get-ChildItem -LiteralPath (Split-Path $PSScriptRoot -Parent) -Force |
        Where-Object { $_.Name -notin @('.git', '__pycache__') } |
        Copy-Item -Destination $source -Recurse -Force
    @{ root = $root; environment = 'disposable-github-actions' } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $root 'fixture.json') -Encoding UTF8
    $worker = Join-Path $source 'tests\core_access_windows_fixture.py'
    function Run-StandardUser([string] $mode) {
        $out = Join-Path $root ($mode + '.out')
        $err = Join-Path $root ($mode + '.err')
        $proc = Start-Process -FilePath $python -Credential $credential -LoadUserProfile `
            -ArgumentList @('-B', "`"$worker`"", "`"$root`"", $mode) `
            -WorkingDirectory $root -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        # Retain the native handle before exit; PS 5.1 can otherwise lose ExitCode.
        $processHandle = $proc.Handle
        if (-not $proc.WaitForExit(120000)) {
            $proc.Kill()
            throw "Fixture child timed out: $mode"
        }
        $proc.WaitForExit()
        Get-Content -LiteralPath $out -ErrorAction SilentlyContinue | Write-Host
        Get-Content -LiteralPath $err -ErrorAction SilentlyContinue | Write-Host
        if ($proc.ExitCode -ne 0) { throw "Standard-user fixture failed: $mode, exit $($proc.ExitCode)" }
    }
    & $python -B $worker $root legacy
    if ($LASTEXITCODE -ne 0) { throw 'Legacy publication failed unexpectedly.' }
    $oldCore = Join-Path $root 'legacy\data\core'
    if (-not (Get-Acl -LiteralPath $oldCore).AreAccessRulesProtected) { throw 'Legacy DACL reproduction did not occur.' }
    Run-StandardUser 'legacy-denied'

    & $python -B $worker $root publish
    if ($LASTEXITCODE -ne 0) { throw 'Current publication failed unexpectedly.' }
    $core = Join-Path $root 'current\data\core'
    if ((Get-Acl -LiteralPath $core).AreAccessRulesProtected) { throw 'New core unexpectedly has protected DACL.' }
    Run-StandardUser 'verify'

    # Explicit negative fixtures. Restore only the ACL we changed, on our objects.
    foreach ($target in @($core, (Join-Path $core '.agent-toolchain-managed-core.json'),
            (Join-Path $core 'config_data.json'), (Join-Path $root 'current\bin\toolchainctl.cmd'))) {
        $original = Get-Acl -LiteralPath $target
        $denied = Get-Acl -LiteralPath $target
        if (Test-Path -LiteralPath $target -PathType Container) {
            $denyRule = [Security.AccessControl.FileSystemAccessRule]::new(
                $user.SID, 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Deny')
        } else {
            $denyRule = [Security.AccessControl.FileSystemAccessRule]::new($user.SID, 'ReadAndExecute', 'Deny')
        }
        $denied.AddAccessRule($denyRule)
        Set-Acl -LiteralPath $target -AclObject $denied
        $expectedSddl = (Get-Acl -LiteralPath $target).Sddl
        try {
            Run-StandardUser 'denied'
            if ((Get-Acl -LiteralPath $target).Sddl -ne $expectedSddl) { throw 'Bootstrap changed denied ACL.' }
        } finally {
            Set-Acl -LiteralPath $target -AclObject $original
        }
    }
    Run-StandardUser 'verify'
    Write-Host 'PASS admin publication -> standard-user access; legacy DACL reproduced; denied ACLs preserved.'
} finally {
    if ($userCreated) { Remove-LocalUser -Name $userName }
    if ($rootCreated) { Remove-Item -LiteralPath $root -Recurse -Force }
}
