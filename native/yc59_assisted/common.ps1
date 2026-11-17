$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$SeedSha='6ffaf03f930dae1d40c7d775b74dac9a76113fb11451da14eb5c314427188065'
$Artifact='sha256-14f643cb899c6bcec43cb2fa417e952d9c9d1a0ba13c5fb6e23a2d1e9e9ea6df'
$PSExe=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Python=$ClientPython
$Public=Join-Path ([Environment]::GetFolderPath('CommonDocuments')) 'YC59_Assisted_v1'
$Anchor=Join-Path $OwnerProfile 'AppData\Local\agent-toolchain-yc-owner'
$Release=Join-Path $Anchor ('releases\'+$Artifact+'-'+$SeedSha.Substring(0,16)+'-r6')
function Need([bool]$Value,[string]$Code){if(-not $Value){throw $Code}}
function Quote([string]$Value){return "'"+$Value.Replace("'","''")+"'"}
function Hash([string]$Path){return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function TextNew([string]$Path,[string]$Text){
    $s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$b=[Text.UTF8Encoding]::new($false).GetBytes($Text);$s.Write($b,0,$b.Length);$s.Flush($true)}finally{$s.Dispose()}
}
function JsonNew([string]$Path,$Value){
    $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    TextNew $temp ($Value|ConvertTo-Json -Depth 12 -Compress)
    [IO.File]::Move($temp,$Path)
}
function Json([string]$Path){
    $i=Get-Item -LiteralPath $Path -Force
    Need (-not $i.PSIsContainer -and -not($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $i.Length -lt 65536) 'YC59_UNSAFE_JSON_FILE'
    return ([IO.File]::ReadAllText($Path)|ConvertFrom-Json)
}
function FreshDirectory([string]$Path,[string]$Own,[bool]$Shared=$false){
    Need (-not (Test-Path -LiteralPath $Path)) 'YC59_NEW_DIRECTORY_REQUIRED'
    NoReparse ([IO.Path]::GetDirectoryName($Path))
    $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new($Own))
    $who=if($Shared){@($OwnerSid,$AgentSid,'S-1-5-18','S-1-5-32-544')}else{@($Own,$AdminSid,'S-1-5-18')}
    foreach($sid in @($who|Select-Object -Unique)){
        [void]$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    [void][IO.Directory]::CreateDirectory($Path,$acl)
}
function NoReparse([string]$Path){
    $part=Get-Item -LiteralPath $Path -Force
    while($null -ne $part){Need (-not($part.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'YC59_REPARSE_POINT_REFUSED';$part=$part.Parent}
}
function Actor([string]$Expected,[bool]$Admin=$false){
    $i=[Security.Principal.WindowsIdentity]::GetCurrent();$p=[Security.Principal.WindowsPrincipal]::new($i)
    Need ($i.User.Value -ceq $Expected) 'YC59_WRONG_ACCOUNT'
    Need ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -eq $Admin) 'YC59_ELEVATION_MISMATCH'
    Need ([Diagnostics.Process]::GetCurrentProcess().SessionId -gt 0) 'YC59_INTERACTIVE_SESSION_REQUIRED'
}
function Personal {
    $prefix=[Environment]::GetFolderPath('UserProfile').TrimEnd('\')+'\'
    Need ([IO.Path]::GetFullPath($PackageRoot).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) 'YC59_PERSONAL_PACKAGE_REQUIRED'
    NoReparse $PackageRoot
    foreach($name in @('PYTHONHOME','PYTHONPATH','PYTHONSTARTUP','AGENT_TOOLCHAIN_DATA_DIR','AGENT_TOOLCHAIN_BIN_DIR','AGENT_TOOLCHAIN_STATE_DIR','AGENT_TOOLCHAIN_UPDATE_REF')){
        Need ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'YC59_AMBIENT_OVERRIDE_REFUSED'
    }
}
function CheckPython {Need ((Hash $Python) -ceq $ClientPythonSha256) 'YC59_PYTHON_CHANGED'}
function Capture([string]$Executable,[string]$Arguments){
    $info=[Diagnostics.ProcessStartInfo]::new($Executable,$Arguments);$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $p=[Diagnostics.Process]::new();$p.StartInfo=$info
    try{Need ($p.Start()) 'YC59_CHILD_START_FAILED';$out=$p.StandardOutput.ReadToEndAsync();$err=$p.StandardError.ReadToEndAsync();$p.WaitForExit();return @{rc=$p.ExitCode;stdout=$out.Result;stderr=$err.Result}}finally{$p.Dispose()}
}
function CommandArgument([string]$Value){
    Need (-not $Value.Contains('"') -and -not $Value.EndsWith('\')) 'YC59_BAD_NATIVE_ARGUMENT'
    return '"'+$Value+'"'
}
function Loader([string]$Role,[string]$RunReport='',[int]$Child=0,[string]$RootOverride=''){
    $loaderRoot=if($RootOverride){$RootOverride}else{$PackageRoot}
    $path=Join-Path $loaderRoot 'launch.ps1';$sha=$LaunchSha
    return '$ErrorActionPreference=''Stop'';$b=[IO.File]::ReadAllBytes('+(Quote $path)+');'+
      '$h=[Security.Cryptography.SHA256]::Create();try{$s=([BitConverter]::ToString($h.ComputeHash($b))).Replace(''-'','''').ToLowerInvariant()}finally{$h.Dispose()};'+
      'if($s -cne '+(Quote $sha)+'){throw ''YC59_LOADER_CHANGED''};& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($b).TrimStart([char]0xFEFF)))'+
      ' -PackageRoot '+(Quote $loaderRoot)+' -Mode '+(Quote $Role)+' -Report '+(Quote $RunReport)+' -TargetPid '+$Child+' -ExpectedLaunchSha '+(Quote $sha)+$TrustedArguments
}
function StartRole([string]$Role,[string]$RunReport='',[int]$Child=0,[bool]$Hidden=$false){
    $arguments='-NoLogo -NoProfile -STA -Command '+(Loader $Role $RunReport $Child)
    if($Hidden){
        $info=[Diagnostics.ProcessStartInfo]::new($PSExe,$arguments);$info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $p=[Diagnostics.Process]::new();$p.StartInfo=$info;Need ($p.Start()) 'YC59_HELPER_START_FAILED';return $p
    }
    return Start-Process -FilePath $PSExe -ArgumentList $arguments -PassThru
}
function SeedCommand([string]$Mode,[string]$Extra=''){
    Need ($Mode -in @('Check','Run','AuditAgent')) 'YC59_BAD_NATIVE_MODE'
    $path=Join-Path $PackageRoot 'owner-seed.ps1'
    $args=if($Mode -eq 'AuditAgent'){$Extra}else{' -Destination '+(Quote $Release)}
    return '$ErrorActionPreference=''Stop'';$b=[IO.File]::ReadAllBytes('+(Quote $path)+');'+
      '$h=[Security.Cryptography.SHA256]::Create();try{$s=([BitConverter]::ToString($h.ComputeHash($b))).Replace(''-'','''').ToLowerInvariant()}finally{$h.Dispose()};'+
      'if($s -cne '+(Quote $SeedSha)+'){throw ''YC59_SEED_CHANGED''};& ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($b).TrimStart([char]0xFEFF)))'+
      ' -SeedBytes $b -Mode '+$Mode+' -AgentSid '+(Quote $AgentSid)+$args
}
function WaitFile([string]$Path,$Process,[int]$Seconds=600){
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::Exists($Path)){
        if($null -ne $Process){Need (-not $Process.HasExited) 'YC59_HELPER_EXITED_BEFORE_READY'}
        Need ($watch.Elapsed.TotalSeconds -lt $Seconds) 'YC59_MACHINE_PREPARATION_TIMEOUT_NO_RUN'
        Start-Sleep -Milliseconds 200
    }
}
function CheckGroups {
    Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PackageRoot 'group-read.cs')))
    $blocked=@('S-1-5-32-544','S-1-5-32-547','S-1-5-32-548','S-1-5-32-549','S-1-5-32-550','S-1-5-32-551','S-1-5-32-552','S-1-5-32-556','S-1-5-32-578')
    foreach($sid in @($OwnerSid,$AgentSid)){
        $groups=@([Yc59ReadGroups]::Read($sid))
        Need (@($groups|Where-Object{$_.sid -ceq 'S-1-5-32-545'}).Count -eq 1 -and @($groups|Where-Object{$_.sid -in $blocked}).Count -eq 0) 'YC59_STANDARD_ACCOUNTS_REQUIRED'
    }
}
