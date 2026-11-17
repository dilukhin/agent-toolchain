$ApproverSid=$OwnerSid
$Root=$InputRoot
function Field($Object,[string]$Name){if($null -eq $Object){return $null};$p=$Object.PSObject.Properties[$Name];if($p){return $p.Value};return $null}

function Id($Value){Need ($Value -is [string] -and $Value -cmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$') 'YC59_INPUT_ID_INVALID';return $Value}

function Digest([string]$Path){return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}

function Check-PrivateDirectory([string]$Path){
    $item=Get-Item -LiteralPath $Path -Force
    Need ($item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'YC59_INPUT_UNSAFE_DIRECTORY'
    $acl=Get-Acl -LiteralPath $Path
    Need ($acl.AreAccessRulesProtected -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $AdminSid) 'YC59_INPUT_DIRECTORY_OWNER_OR_INHERITANCE'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Need ($rules.Count -eq 3) 'YC59_INPUT_DIRECTORY_RULE_COUNT'
    foreach($who in @($AdminSid,'S-1-5-18',$ApproverSid)){
        $rights=if($who -ceq $ApproverSid){[Security.AccessControl.FileSystemRights]([int][Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [int][Security.AccessControl.FileSystemRights]::Synchronize)}else{[Security.AccessControl.FileSystemRights]::FullControl}
        $r=@($rules|Where-Object{$_.IdentityReference.Value -ceq $who})
        Need ($r.Count -eq 1 -and $r[0].AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $r[0].FileSystemRights -eq $rights -and $r[0].InheritanceFlags -eq [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit') 'YC59_INPUT_DIRECTORY_ACL_CHANGED'
    }
}

function Read-Small([string]$Path){
    Check-InputFile $Path
    $f=Get-Item -LiteralPath $Path -Force
    Need (-not $f.PSIsContainer -and -not ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $f.Length -lt 65536) 'YC59_INPUT_STORED_FILE_INVALID'
    return [IO.File]::ReadAllText($Path)
}

function Read-Current {
    $p=Join-Path $Root 'current.json'
    if(-not [IO.File]::Exists($p)){return $null}
    $ptr=(Read-Small $p)|ConvertFrom-Json
    Need ($ptr.schema -ceq 'yc59-dima-input-pointer/v1' -and $ptr.session -cmatch '^[0-9a-f]{32}$') 'YC59_INPUT_POINTER_INVALID'
    $dir=Join-Path $Root $ptr.session;Check-PrivateDirectory $dir
    $selection=(Read-Small (Join-Path $dir 'selection.json'))|ConvertFrom-Json
    Validate-Selection $selection
    Need ($selection.dima_sid -ceq $AdminSid) 'YC59_INPUT_SAVED_ACTOR_MISMATCH'
    Need ((Digest (Join-Path $dir 'token.dpapi')) -ceq $selection.token_file_sha256) 'YC59_INPUT_TOKEN_FILE_CHANGED'
    return @{directory=$dir;selection=$selection}
}


function Validate-Selection($s){
    Need ((Field $s 'schema') -ceq 'yc59-dima-inputs/v1') 'YC59_INPUT_SELECTION_SCHEMA'
    foreach($k in @('cloud_id','folder_id','vm_id','disk_id','snapshot_id')){[void](Id (Field $s $k))}
    Need ((Field $s 'token_file_sha256') -cmatch '^[0-9a-f]{64}$') 'YC59_INPUT_TOKEN_HASH_INVALID'
}

function Open-Token($saved){
    Add-Type -AssemblyName System.Security
    $text=Read-Small (Join-Path $saved.directory 'token.dpapi')
    $entropy=[Text.Encoding]::UTF8.GetBytes('YC59 manual input transfer v1')
    $bytes=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($text),$entropy,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    try{
        $plain=[Text.Encoding]::UTF8.GetString($bytes)
        Need ($plain.Length -ge 20 -and $plain.Length -le 8192 -and $plain -cmatch '^\S+$') 'YC59_INPUT_STORED_TOKEN_INVALID'
        return ConvertTo-SecureString -String $plain -AsPlainText -Force
    }finally{[Array]::Clear($bytes,0,$bytes.Length);$plain=$null}
}

function Plain([Security.SecureString]$Secure){
    $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try{return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
}

function Display-Ids($s){
    Write-Host ('Облако: '+$s.cloud_id);Write-Host ('Каталог: '+$s.folder_id)
    Write-Host ('ВМ: '+$s.vm_id);Write-Host ('Диск: '+$s.disk_id);Write-Host ('Снимок: '+$s.snapshot_id)
}

function Check-InputFile([string]$Path){
    $acl=Get-Acl -LiteralPath $Path
    Need ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $AdminSid) 'YC59_INPUT_FILE_OWNER_CHANGED'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Need ($rules.Count -eq 3) 'YC59_INPUT_FILE_ACL_CHANGED'
    foreach($sid in @($AdminSid,'S-1-5-18',$ApproverSid)){
        $rights=if($sid -ceq $ApproverSid){[Security.AccessControl.FileSystemRights]([int][Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [int][Security.AccessControl.FileSystemRights]::Synchronize)}else{[Security.AccessControl.FileSystemRights]::FullControl}
        $r=@($rules|Where-Object{$_.IdentityReference.Value -ceq $sid})
        Need ($r.Count -eq 1 -and $r[0].AccessControlType -eq 'Allow' -and $r[0].FileSystemRights -eq $rights) 'YC59_INPUT_FILE_ACL_CHANGED'
    }
}
