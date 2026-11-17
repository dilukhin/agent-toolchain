$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert([bool]$Value,[string]$Name){if(-not $Value){throw ('TEST_FAILED_'+$Name)}}
function Refuses([scriptblock]$Action,[string]$Name){$failed=$false;try{& $Action}catch{$failed=$true};Assert $failed $Name}
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$package=Join-Path $root 'native\yc59_assisted'
foreach($script in @(Get-ChildItem -LiteralPath $package -Filter '*.ps1')){
    $tokens=$null;$errors=$null
    # Match the real pinned loader: decode UTF-8 explicitly, including the
    # unchanged seed which intentionally has no BOM. ParseFile uses legacy ACP.
    $source=[Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($script.FullName)).TrimStart([char]0xFEFF)
    [void][Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
    Assert ($errors.Count -eq 0) ('parse_'+$script.Name)
}
# Exercise trusted parameter validation with an existing OS profile. No accounts are created.
. (Join-Path $package 'parameters.ps1')
$OwnerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$AgentSid='S-1-5-21-1-2-3-1002';$AdminSid='S-1-5-21-1-2-3-1003'
$OwnerProfile=[Environment]::GetFolderPath('UserProfile')
$PackageRoot=$package;$InputRoot=Join-Path $OwnerProfile 'synthetic-input'
$ClientPython='C:\synthetic\python.exe';$ClientPythonSha256='a'*64
Initialize-TrustedParameters
$original=$AgentSid;$AgentSid=$OwnerSid
Refuses {Initialize-TrustedParameters} 'duplicate_actor';$AgentSid=$original
$original=$OwnerProfile;$OwnerProfile=Join-Path $OwnerProfile 'wrong-profile'
Refuses {Initialize-TrustedParameters} 'unconfirmed_profile';$OwnerProfile=$original
$original=$InputRoot;$InputRoot='\\server\share'
Refuses {Initialize-TrustedParameters} 'unc_path';$InputRoot=$original

# Compile the actual held-directory primitive and verify Windows rename exclusion.
$launcher=[IO.File]::ReadAllText((Join-Path $package 'launch.ps1'))
$match=[regex]::Match($launcher,"(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@")
Assert $match.Success 'lease_source_found'
Add-Type -TypeDefinition $match.Groups[1].Value
$temp=Join-Path $env:RUNNER_TEMP ('yc59-assisted-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($temp)
. (Join-Path $package 'common.ps1')
$currentLatch=Join-Path $temp 'current-latch';$legacyLatch=Join-Path $temp 'legacy-latch'
GuardConsumeMarkers $currentLatch $legacyLatch
[IO.File]::WriteAllText($legacyLatch,'unknown old consume outcome')
Refuses {GuardConsumeMarkers $currentLatch $legacyLatch} 'legacy_consume_cannot_be_bypassed'
# Load definitions without the workflow dispatcher. A previous R11b native
# attempt without an owner cancellation proof must stop the managed wrapper.
$tokens=$null;$errors=$null
$flow=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText((Join-Path $package 'flow.ps1')),[ref]$tokens,[ref]$errors)
foreach($definition in $flow.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){
    . ([scriptblock]::Create($definition.Extent.Text))
}
$savedProfile=$OwnerProfile;$savedAnchor=$Anchor
try{
    $OwnerProfile=$temp;$Anchor=Join-Path $temp 'native-anchor'
    [void][IO.Directory]::CreateDirectory((Join-Path $temp 'Documents'))
    $history=Join-Path $temp 'Documents\YC59_Owner_R11';FreshDirectory $history $OwnerSid
    $run=Join-Path $history ('a'*32);FreshDirectory $run $OwnerSid
    JsonNew (Join-Path $run 'native-started.json') @{synthetic_only=$true}
    Refuses {CheckOldSessions} 'legacy_unknown_native_attempt_cannot_be_bypassed'
}finally{$OwnerProfile=$savedProfile;$Anchor=$savedAnchor}
$lease=[Yc59PackageLease]::Directory($temp)
try{Refuses {[IO.Directory]::Move($temp,$temp+'-moved')} 'held_directory_cannot_move'}finally{$lease.Dispose()}
$file=Join-Path $temp 'held.txt';[IO.File]::WriteAllText($file,'synthetic')
$held=[IO.File]::Open($file,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{Refuses {[IO.File]::WriteAllText($file,'changed')} 'held_file_cannot_change'}finally{$held.Dispose()}

# Use the unchanged counted-buffer reader against a real Windows console.
# Only fixture.py's six public synthetic values are sent; no approval is typed.
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $package 'console-bridge.cs')))
$python=(& py -3 -c 'import sys; print(sys.executable)').Trim()
Assert ([IO.File]::Exists($python)) 'fixture_python_exists'
$ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$hostScript=Join-Path $PSScriptRoot 'yc59_assisted_console_host.ps1'
$args='-NoLogo -NoProfile -File "'+$hostScript+'" -Python "'+$python+'" -Package "'+$package+'" -Report "'+$temp+'"'
$child=Start-Process -FilePath $ps -ArgumentList $args -PassThru
try{
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::Exists((Join-Path $temp 'fixture-start.json'))){Assert (-not $child.HasExited -and $clock.Elapsed.TotalSeconds -lt 30) 'fixture_started';Start-Sleep -Milliseconds 50}
    [Yc59ConsoleR11]::Attach($child.Id)
    $pairs=@(@('IAM-токен владельца (ввод скрыт): ','SYNTHETIC-CAPITALS-314-AbZ'),@('Облако — ID: ','b1g-CLOUD-01'),@('Каталог — ID: ','b1g-FOLDER-02'),@('ВМ — ID: ','fv4-VM-03'),@('Диск — ID: ','fv4-DISK-04'),@('Снимок — ID: ','fd8-SNAPSHOT-05'))
    foreach($pair in $pairs){
        while(-not [Yc59ConsoleR11]::At($pair[0])){Assert (-not $child.HasExited -and $clock.Elapsed.TotalSeconds -lt 45) 'exact_prompt';Start-Sleep -Milliseconds 30}
        [Yc59ConsoleR11]::Line($pair[0],$pair[1])
    }
    while(-not [IO.File]::Exists((Join-Path $temp 'host-exit.txt'))){Assert ($clock.Elapsed.TotalSeconds -lt 50) 'fixture_completed';Start-Sleep -Milliseconds 50}
    $result=[IO.File]::ReadAllText((Join-Path $temp 'fixture-result.json'))|ConvertFrom-Json
    Assert ($result.all_values_exact -eq $true -and $result.synthetic_only -eq $true -and $result.cloud_invocations -eq 0) 'six_values_exact'
    Assert (-not [Yc59ConsoleR11]::Text().Contains('SYNTHETIC-CAPITALS-314-AbZ')) 'synthetic_secret_not_echoed'
    Assert ([IO.File]::ReadAllText((Join-Path $temp 'host-exit.txt')) -ceq '0') 'fixture_exit_zero'
    [IO.File]::WriteAllText((Join-Path $temp 'close'),'close')
    Assert ($child.WaitForExit(10000) -and $child.ExitCode -eq 0) 'host_exit_zero'
}finally{
    [Yc59ConsoleR11]::Close()
    if(-not $child.HasExited){$child.Kill();$child.WaitForExit()}
    $child.Dispose()
}
Write-Output 'YC59_ASSISTED_WINDOWS_SYNTHETIC_PASS'
