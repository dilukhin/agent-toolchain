param(
 [Parameter(Mandatory=$true)][string]$PackageRoot,
 [Parameter(Mandatory=$true)][ValidateSet('AgentSetup','Owner','Broker','NativeHost','Bridge','FixtureHost','FixtureBridge')][string]$Mode,
 [Parameter(Mandatory=$true)][string]$OwnerSid,
 [Parameter(Mandatory=$true)][string]$AgentSid,
 [Parameter(Mandatory=$true)][string]$AdminSid,
 [Parameter(Mandatory=$true)][string]$OwnerProfile,
 [Parameter(Mandatory=$true)][string]$InputRoot,
 [Parameter(Mandatory=$true)][string]$ClientPython,
 [Parameter(Mandatory=$true)][string]$ClientPythonSha256,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedLaunchSha,
 [string]$Report='', [int]$TargetPid=0
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$packageLeases=[Collections.Generic.List[IDisposable]]::new()
try {
    if('Yc59PackageLease' -as [type]){throw 'YC59_FRESH_POWERSHELL_REQUIRED'}
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class Yc59PackageLease {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sa,uint c,uint f,IntPtr t);
    public static SafeFileHandle Directory(string path) {
        // Metadata-only handles do not participate in all sharing checks.
        // GENERIC_READ includes directory listing; FILE_SHARE_READ excludes rename/write.
        var h=CreateFile(path,0x80000000,1,IntPtr.Zero,3,0x02200000,IntPtr.Zero);
        if(h.IsInvalid){h.Dispose();throw new IOException("YC59_PACKAGE_DIRECTORY_LEASE_FAILED");}
        if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0){h.Dispose();throw new IOException("YC59_PACKAGE_REPARSE_REFUSED");}
        return h;
    }
}
'@
    # Hold every ancestor against rename; payload files additionally deny writes.
    $ancestors=[Collections.Generic.Stack[string]]::new()
    $part=Get-Item -LiteralPath $PackageRoot -Force
    while($null -ne $part){$ancestors.Push($part.FullName);$part=$part.Parent}
    foreach($ancestor in $ancestors){$packageLeases.Add([Yc59PackageLease]::Directory($ancestor))}
    function LeasedBytes([string]$Name){
        $path=Join-Path $PackageRoot $Name
        if((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'YC59_PACKAGE_REPARSE_REFUSED'}
        $f=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $packageLeases.Add($f)
        if($f.Length -le 0 -or $f.Length -gt 131072){throw 'YC59_PACKAGE_FILE_SIZE'}
        $b=[byte[]]::new([int]$f.Length);$offset=0
        while($offset -lt $b.Length){$n=$f.Read($b,$offset,$b.Length-$offset);if($n -le 0){throw 'YC59_PACKAGE_SHORT_READ'};$offset+=$n}
        return ,$b
    }
    function BytesHash([byte[]]$Bytes){
        $h=[Security.Cryptography.SHA256]::Create()
        try{return ([BitConverter]::ToString($h.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}
    }
    $LaunchSha=$ExpectedLaunchSha
    if((BytesHash (LeasedBytes 'launch.ps1')) -cne $LaunchSha){throw 'YC59_LAUNCH_CHANGED'}
    $pinBytes=LeasedBytes 'pins.json'
    if((BytesHash $pinBytes) -cne 'b15cb78be409fec91ef924869ac537ceea1a4e40cad2e6f3aa51bb27804d5c61'){throw 'YC59_PACKAGE_MANIFEST_CHANGED'}
    $pins=[Text.Encoding]::UTF8.GetString($pinBytes)|ConvertFrom-Json
    $sources=@{}
    foreach($p in $pins.PSObject.Properties){
        if($p.Name -cnotmatch '^[a-z0-9-]+\.(ps1|cs|py)$'){throw 'YC59_BAD_PIN_NAME'}
        $bytes=LeasedBytes $p.Name
        if((BytesHash $bytes) -cne $p.Value){throw 'YC59_PACKAGE_FILE_CHANGED'}
        if($p.Name -in @('parameters.ps1','common.ps1','saved-inputs.ps1','flow.ps1')){
            $sources[$p.Name]=[Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
        }
    }
    . ([scriptblock]::Create($sources['parameters.ps1']))
    Initialize-TrustedParameters
    . ([scriptblock]::Create($sources['common.ps1']))
    $TrustedArguments=''
    foreach($name in @('OwnerSid','AgentSid','AdminSid','OwnerProfile','InputRoot','ClientPython','ClientPythonSha256')){
        $TrustedArguments+=' -'+$name+' '+(Quote (Get-Variable -Name $name -ValueOnly))
    }
    $SharedRun=$null
    . ([scriptblock]::Create($sources['saved-inputs.ps1']))
    . ([scriptblock]::Create($sources['flow.ps1']))
} catch {Write-Host 'YC59_PACKAGE_ENTRY_REFUSED';exit 2}
finally {foreach($lease in $packageLeases){$lease.Dispose()}}
