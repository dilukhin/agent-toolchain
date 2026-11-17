function Initialize-TrustedParameters {
    foreach($sid in @($OwnerSid,$AgentSid,$AdminSid)){
        if($sid -cnotmatch '^S-1-5-21-[0-9]+-[0-9]+-[0-9]+-[0-9]+$'){throw 'YC59_LOCAL_ACCOUNT_SID_REQUIRED'}
        [void][Security.Principal.SecurityIdentifier]::new($sid)
    }
    if(@($OwnerSid,$AgentSid,$AdminSid|Select-Object -Unique).Count -ne 3){throw 'YC59_DISTINCT_ACCOUNTS_REQUIRED'}
    if($ClientPythonSha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'YC59_CLIENT_PYTHON_PIN_REQUIRED'}
    foreach($path in @($PackageRoot,$OwnerProfile,$InputRoot,$ClientPython)){
        if($path -cnotmatch '^[A-Za-z]:\\' -or $path.Contains('"') -or $path.EndsWith('\')){throw 'YC59_LOCAL_PATH_REQUIRED'}
        foreach($c in $path.ToCharArray()){if([char]::IsControl($c)){throw 'YC59_PATH_CONTROL_REFUSED'}}
        if([IO.Path]::GetFullPath($path) -cne $path){throw 'YC59_CANONICAL_PATH_REQUIRED'}
    }
    # The owner profile comes from the OS, not coordination files or a caller's SID label.
    $key='HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\'+$OwnerSid
    $observed=[Microsoft.Win32.Registry]::GetValue($key,'ProfileImagePath',$null)
    if($null -eq $observed -or [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables([string]$observed)) -ine $OwnerProfile){throw 'YC59_OWNER_PROFILE_NOT_CONFIRMED'}
}
