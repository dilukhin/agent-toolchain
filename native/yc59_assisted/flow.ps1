function PublicDirectory {
    Need ([IO.Directory]::Exists($Public)) 'YC59_AGENT_SETUP_FIRST'
    $i=Get-Item -LiteralPath $Public -Force;$a=Get-Acl -LiteralPath $Public
    Need (-not($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $a.AreAccessRulesProtected) 'YC59_COORDINATION_DIRECTORY_INVALID'
    $rules=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Need ($rules.Count -eq 4 -and $a.GetOwner([Security.Principal.SecurityIdentifier]).Value -in @($OwnerSid,$AgentSid)) 'YC59_COORDINATION_ACL_CHANGED'
    foreach($sid in @($OwnerSid,$AgentSid,'S-1-5-18','S-1-5-32-544')){
        $r=@($rules|Where-Object{$_.IdentityReference.Value -ceq $sid})
        Need ($r.Count -eq 1 -and $r[0].AccessControlType -eq 'Allow' -and $r[0].FileSystemRights -eq 'FullControl') 'YC59_COORDINATION_ACL_CHANGED'
    }
}
function ReportDirectory {
    $base=Join-Path $OwnerProfile 'Documents\YC59_Assisted_Owner'
    if(-not [IO.Directory]::Exists($base)){FreshDirectory $base $OwnerSid}
    $p=Join-Path $base ([guid]::NewGuid().ToString('N'));FreshDirectory $p $OwnerSid;return $p
}
function CheckReport([string]$Path,[bool]$Legacy=$false){
    $relative=if($Legacy){'Documents\YC59_Owner_R11'}else{'Documents\YC59_Assisted_Owner'}
    $base=Join-Path $OwnerProfile $relative
    Need ($Path -cmatch ('^'+[regex]::Escape($base)+'\\[0-9a-f]{32}$')) 'YC59_REPORT_PATH_OUT_OF_SCOPE'
    NoReparse $Path
    $i=Get-Item -LiteralPath $Path -Force;$a=Get-Acl -LiteralPath $Path
    Need ($i.PSIsContainer -and -not($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $a.AreAccessRulesProtected -and $a.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $OwnerSid) 'YC59_PRIVATE_REPORT_REQUIRED'
    $rules=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Need ($rules.Count -eq 3) 'YC59_PRIVATE_REPORT_ACL_CHANGED'
    foreach($sid in @($OwnerSid,$AdminSid,'S-1-5-18')){
        $r=@($rules|Where-Object{$_.IdentityReference.Value -ceq $sid})
        Need ($r.Count -eq 1 -and $r[0].AccessControlType -eq 'Allow' -and $r[0].FileSystemRights -eq 'FullControl') 'YC59_PRIVATE_REPORT_ACL_CHANGED'
    }
}
function CheckOldSessions {
    # A fresh package never deletes, resets or imports an earlier consent.
    # Within this workflow any native attempt blocks another preparation unless
    # the owner-side authenticated rejection/expiry proof was saved after closure.
    foreach($relative in @('Documents\YC59_Assisted_Owner','Documents\YC59_Owner_R11')){
    $base=Join-Path $OwnerProfile $relative
    if([IO.Directory]::Exists($base)){
        NoReparse $base
        foreach($dir in @(Get-ChildItem -LiteralPath $base -Directory)){
            if(-not [IO.File]::Exists((Join-Path $dir.FullName 'native-started.json'))){continue}
            CheckReport $dir.FullName ($relative -ceq 'Documents\YC59_Owner_R11')
            $done=Join-Path $dir.FullName 'safe-to-prepare-again.json'
            Need ([IO.File]::Exists($done)) 'YC59_PRIOR_OUTCOME_REQUIRES_DIAGNOSIS_NO_RETRY'
            $proof=Json $done
            Need ($proof.schema -ceq 'yc59-repeat-preparation/v1' -and $proof.native_host_closed -eq $true -and $proof.request_never_consumable -eq $true) 'YC59_PRIOR_REPEAT_PROOF_INVALID'
            $trusted=Join-Path $dir.FullName 'trusted-no-consume.json'
            Need ((Hash $trusted) -ceq $proof.proof_sha256 -and (Json $trusted).state -in @('rejected','expired') -and [IO.File]::Exists((Join-Path $dir.FullName 'native-exit.json')) -and -not [IO.File]::Exists((Join-Path $dir.FullName 'owner-result.json'))) 'YC59_PRIOR_CANCELLATION_NOT_PROVEN'
        }
    }
    }
    $path=Join-Path $Anchor 'active.json'
    if(-not [IO.File]::Exists($path)){return}
    $active=Json $path
    Need ($active.schema -ceq 'yc-owner-active/v1' -and $active.phase -in @('owner_process_started','closed') -and $active.owner_sid -ceq $OwnerSid -and $active.destination -ieq $Release -and $active.seed_sha256 -ceq $SeedSha -and $active.artifact_id -ceq $Artifact.Replace('sha256-','sha256:')) 'YC59_ACTIVE_BINDING_CHANGED'
    if($active.phase -ceq 'owner_process_started'){
        $old=$null;try{$old=[Diagnostics.Process]::GetProcessById([int]$active.server_pid)}catch [ArgumentException]{}
        if($null -ne $old){try{Need ($old.HasExited) 'YC59_PRIOR_OWNER_PROCESS_STILL_EXISTS'}finally{$old.Dispose()}}
    }
}
function PreviousServerPid {
    $path=Join-Path $Anchor 'active.json'
    if([IO.File]::Exists($path)){return [int](Json $path).server_pid}
    return 0
}
function InstalledCheck {
    $entry=Join-Path $env:LOCALAPPDATA 'agent-toolchain\bin\toolchainctl.cmd'
    $cmd=Join-Path $env:SystemRoot 'System32\cmd.exe'
    $r=Capture $cmd ('/d /s /c ""'+$entry+'" --bootstrap-access-check"')
    Need ($r.rc -eq 0) 'YC59_INSTALLED_ACCESS_CHECK_FAILED'
    $value=$r.stdout|ConvertFrom-Json
    Need ($value.non_elevated -eq $true -and $value.fingerprint -cmatch '^[0-9a-f]{64}$') 'YC59_INSTALLED_CORE_CHANGED'
    $r=Capture $cmd ('/d /s /c ""'+$entry+'" yc-owner check"')
    Need ($r.rc -eq 0) 'YC59_CLIENT_CHECK_FAILED'
    $v=$r.stdout|ConvertFrom-Json
    Need ($v.status -ceq 'staged_verified' -and $v.source_ref -ceq '1f7d89d9a8cd2352c6df865e8915f66171475972' -and $v.artifact_id -ceq 'sha256:96ab79c0779f4804f63ebcc7e505fff8750657afa08965969bfabbda16db6c29') 'YC59_CLIENT_PIN_CHANGED'
}
function ReadInputs {
    NoReparse $Root
    Check-PrivateDirectory $Root
    $saved=Read-Current;Need ($null -ne $saved) 'YC59_PREPARED_INPUTS_MISSING'
    Need ($saved.selection.relationships_checked -eq $true) 'YC59_INPUT_RELATIONSHIPS_NOT_VERIFIED'
    $secure=Open-Token $saved
    return @{saved=$saved;secure=$secure}
}
function AwaitPrompt([string]$Prompt,$Watch,[int]$Seconds=600){
    while(-not [Yc59ConsoleR11]::At($Prompt)){
        Need ($Watch.Elapsed.TotalSeconds -lt $Seconds) 'YC59_CONSOLE_PROMPT_TIMEOUT_NO_RETRY'
        Start-Sleep -Milliseconds 60
    }
}
function FixtureBridge {
    Actor $OwnerSid;CheckReport $Report
    Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PackageRoot 'console-bridge.cs')))
    [Yc59ConsoleR11]::Attach($TargetPid)
    try {
        $w=[Diagnostics.Stopwatch]::StartNew()
        $pairs=@(@('IAM-токен владельца (ввод скрыт): ','SYNTHETIC-CAPITALS-314-AbZ'),@('Облако — ID: ','b1g-CLOUD-01'),@('Каталог — ID: ','b1g-FOLDER-02'),@('ВМ — ID: ','fv4-VM-03'),@('Диск — ID: ','fv4-DISK-04'),@('Снимок — ID: ','fd8-SNAPSHOT-05'))
        $fieldIndex=0
        foreach($pair in $pairs){AwaitPrompt $pair[0] $w 240;[Yc59ConsoleR11]::Line($pair[0],$pair[1]);$fieldIndex++}
        WaitFile (Join-Path $Report 'fixture-result.json') $null 60
        $values=Json (Join-Path $Report 'fixture-result.json')
        Need ($values.all_values_exact -eq $true -and $values.synthetic_only -eq $true) 'YC59_FIXTURE_VALUES_DONT_MATCH'
        while(-not [Yc59ConsoleR11]::Text().Contains('YC59_FIXTURE_OK')){Need ($w.Elapsed.TotalSeconds -lt 300) 'YC59_FIXTURE_FAILED';Start-Sleep -Milliseconds 80}
        Need (-not [Yc59ConsoleR11]::Text().Contains('SYNTHETIC-CAPITALS-314-AbZ')) 'YC59_FIXTURE_SECRET_ECHOED'
        $ap=ApprovalFromConsole ([Yc59ConsoleR11]::Text())
        Need ($null -ne $ap -and $ap.request_id -ceq '12345678-1234-1234-1234-123456789abc' -and [Yc59ConsoleR11]::Text().Contains('SHA-256 запроса: '+('a'*64))) 'YC59_FIXTURE_APPROVAL_DISPLAY_UNREADABLE'
        JsonNew (Join-Path $Report 'console-selftest-ok.json') @{schema='yc59-console-selftest/v1';synthetic_only=$true;token_hidden=$true;all_five_ids_exact=$true;cloud_invocations=0}
    } catch {
        $errorSummary=SafeError $_
        JsonNew (Join-Path $Report 'fixture-bridge-failure.json') @{error=$errorSummary;field_index=$fieldIndex;synthetic_only=$true}
        JsonNew (Join-Path $Report 'console-metrics-FixtureBridge.json') ([Yc59ConsoleR11]::Snapshot())
        throw
    } finally {[Yc59ConsoleR11]::Close();TextNew (Join-Path $Report 'fixture-close') 'close'}
}
function SafeError($Record){
    $e=$Record.Exception;while($e.InnerException){$e=$e.InnerException}
    $name=if($e.Message -cmatch '^YC59_[A-Z0-9_]+$'){$e.Message}else{'YC59_AUTOMATION_STOPPED_L'+[string]$Record.InvocationInfo.ScriptLineNumber}
    return @{code=$name;exception_type=$e.GetType().FullName;hresult=$e.HResult;line=$Record.InvocationInfo.ScriptLineNumber}
}
function FixtureHost {
    Actor $OwnerSid;CheckReport $Report
    $python=Join-Path $Release 'payload\python\python.exe'
    Need ([IO.File]::Exists($python)) 'YC59_PROTECTED_FIXTURE_PYTHON_MISSING'
    $arg='-I -S -B '+(CommandArgument (Join-Path $PackageRoot 'fixture.py'))+' '+(CommandArgument $Report)
    $info=[Diagnostics.ProcessStartInfo]::new($python,$arg);$info.UseShellExecute=$false
    $p=[Diagnostics.Process]::new();$p.StartInfo=$info;$started=$false
    try{
        Need ($p.Start()) 'YC59_FIXTURE_START_FAILED';$started=$true;[void]$p.Handle
        JsonNew (Join-Path $Report 'fixture-child.json') @{pid=$p.Id;started_ticks=$p.StartTime.ToUniversalTime().Ticks;host_pid=$PID}
        $watch=[Diagnostics.Stopwatch]::StartNew();$stopped=$false
        while(-not $p.WaitForExit(100)){
            if([IO.File]::Exists((Join-Path $Report 'fixture-close')) -or $watch.Elapsed.TotalSeconds -gt 300){$p.Kill();$p.WaitForExit();$stopped=$true;break}
        }
        JsonNew (Join-Path $Report 'fixture-exit.json') @{exit_code=$p.ExitCode;stopped_by_controller=$stopped;synthetic_only=$true}
        WaitFile (Join-Path $Report 'fixture-close') $null 60
        Need ($p.ExitCode -eq 0) 'YC59_FIXTURE_PYTHON_FAILED'
    }finally{if($started -and -not $p.HasExited){$p.Kill();$p.WaitForExit()};$p.Dispose()}
}
function Broker {
    Actor $AdminSid $true;CheckReport $Report
    JsonNew (Join-Path $Report 'broker-ready.json') @{schema='yc59-audit-broker/v1';pid=$PID;administrator_sid=$AdminSid;read_only=$true}
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::Exists((Join-Path $Report 'audit-request.json'))){
        if([IO.File]::Exists((Join-Path $Report 'cancel'))){return}
        Need ($watch.Elapsed.TotalHours -lt 12) 'YC59_BROKER_IDLE_EXPIRED_NO_RUN';Start-Sleep -Milliseconds 200
    }
    $r=Json (Join-Path $Report 'audit-request.json')
    Need ($r.schema -ceq 'yc59-audit-request/v1' -and $r.nonce -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') 'YC59_AUDIT_NONCE_INVALID'
    Need ($r.path -cmatch ('^'+[regex]::Escape($Release)+'\\session-[0-9a-f]{32}\\capabilities\.txt$')) 'YC59_AUDIT_PATH_OUT_OF_SCOPE'
    Need (-not [IO.File]::Exists($r.path)) 'YC59_AUDIT_ALREADY_EXISTS'
    $extra=' -OwnerSid '+(Quote $OwnerSid)+' -Nonce '+(Quote $r.nonce)+' -AuditPath '+(Quote $r.path)
    $r=Capture $PSExe ('-NoLogo -NoProfile -Command '+(SeedCommand 'AuditAgent' $extra))
    Need ($r.rc -eq 0) 'YC59_NATIVE_AUDIT_REFUSED'
    $v=$r.stdout|ConvertFrom-Json;Need ($v.status -ceq 'audit_written' -and $v.cloud_invocations -eq 0) 'YC59_NATIVE_AUDIT_RESULT_INVALID'
    JsonNew (Join-Path $Report 'audit-done.json') @{status='audit_written';cloud_invocations=0}
}
function NativeHost {
    Actor $OwnerSid;CheckReport $Report
    Write-Host 'Проверка и ввод данных выполняются автоматически. Ничего не копируйте из строк проверки.'
    Write-Host 'При появлении запроса прочитайте его. Фраза согласия будет в буфере: вставьте её правой кнопкой мыши или через меню «Вставить» и нажмите Enter.'
    & $PSExe -NoLogo -NoProfile -Command (SeedCommand 'Run')
    $rc=$LASTEXITCODE
    JsonNew (Join-Path $Report 'native-exit.json') @{exit_code=$rc;native_parent_returned=$true}
    Write-Host ('Защищённый процесс завершился: '+$rc+'. Результат сохраняется автоматически.')
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while(-not [IO.File]::Exists((Join-Path $Report 'native-close'))){
        if($watch.Elapsed.TotalMinutes -gt 15){return};Start-Sleep -Milliseconds 200
    }
}
function OwnerBridge {
    Actor $OwnerSid;CheckReport $Report
    $inputs=ReadInputs;$child=$null
    try {
        JsonNew (Join-Path $Report 'bridge-ready.json') @{schema='yc59-input-bridge/v1';pid=$PID;token_decrypted=$true;selection_sha256=(Hash (Join-Path $inputs.saved.directory 'selection.json'))}
        $idle=[Diagnostics.Stopwatch]::StartNew()
        $controller=Json (Join-Path $Report 'controller.json')
        while(-not [IO.File]::Exists((Join-Path $Report 'native-pid.json'))){
            if([IO.File]::Exists((Join-Path $Report 'cancel'))){return}
            $live=[Diagnostics.Process]::GetProcessById([int]$controller.pid)
            Need ($live.StartTime.ToUniversalTime().Ticks -eq [long]$controller.started_ticks -and $idle.Elapsed.TotalHours -lt 12) 'YC59_CONTROLLER_CLOSED_BEFORE_RUN'
            $live.Dispose();Start-Sleep -Milliseconds 200
        }
        $n=Json (Join-Path $Report 'native-pid.json');$p=[Diagnostics.Process]::GetProcessById([int]$n.pid)
        Need ($p.SessionId -eq [Diagnostics.Process]::GetCurrentProcess().SessionId -and $p.ProcessName -ieq 'powershell') 'YC59_NATIVE_CONSOLE_PROCESS_CHANGED'
        Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PackageRoot 'console-bridge.cs')))
        [Yc59ConsoleR11]::Attach([int]$n.pid)
        $w=[Diagnostics.Stopwatch]::StartNew()
        $session=$null
        while($null -eq $session){
            $text=[Yc59ConsoleR11]::Text()
            if($text.Contains('Owner SID: '+$OwnerSid) -and $text.Contains('Agent SID: '+$AgentSid) -and $text -cmatch 'Nonce: ([0-9a-f-]{36})New audit file: (.+?\\session-([0-9a-f]{32})\\capabilities\.txt)'){
                $nonce=$Matches[1];$audit=$Matches[2];$session='session-'+$Matches[3]
                Need ($audit -ceq (Join-Path (Join-Path $Release $session) 'capabilities.txt')) 'YC59_NATIVE_AUDIT_PATH_CHANGED'
                JsonNew (Join-Path $Report 'audit-request.json') @{schema='yc59-audit-request/v1';nonce=$nonce;path=$audit}
                JsonNew (Join-Path $Report 'session.json') @{session=$session}
                break
            }
            Need ($w.Elapsed.TotalSeconds -lt 160) 'YC59_NATIVE_AUDIT_PROMPT_NOT_FOUND';Start-Sleep -Milliseconds 80
        }
        $prompt='IAM-токен владельца (ввод скрыт): '
        AwaitPrompt $prompt $w 600
        $config=Json (Join-Path $Report 'config.json')
        $activeWait=[Diagnostics.Stopwatch]::StartNew()
        do{
            $active=Json (Join-Path $Anchor 'active.json')
            if($active.server_pid -ne $config.previous_server_pid){break}
            Need ($activeWait.Elapsed.TotalSeconds -lt 60) 'YC59_NEW_NATIVE_CHECKPOINT_NOT_READY'
            Start-Sleep -Milliseconds 80
        }while($true)
        Need ($active.schema -ceq 'yc-owner-active/v1' -and $active.phase -ceq 'owner_process_started' -and $active.owner_sid -ceq $OwnerSid -and $active.destination -ieq $Release -and $active.seed_sha256 -ceq $SeedSha -and $active.artifact_id -ceq $Artifact.Replace('sha256-','sha256:')) 'YC59_LIVE_NATIVE_BINDING_CHANGED'
        $child=[Diagnostics.Process]::GetProcessById([int]$active.server_pid)
        Need ($child.SessionId -eq $p.SessionId -and $child.MainModule.FileName -ieq (Join-Path $Release 'payload\python\python.exe')) 'YC59_PROTECTED_PYTHON_CHANGED'
        [void]$child.Handle # Hold the exact process object; never target a recycled PID.
        $raw=Plain $inputs.secure
        try{[Yc59ConsoleR11]::Line($prompt,$raw)}finally{$raw=$null;$inputs.secure.Dispose();$inputs.secure=$null}
        foreach($pair in @(@('Облако','cloud_id'),@('Каталог','folder_id'),@('ВМ','vm_id'),@('Диск','disk_id'),@('Снимок','snapshot_id'))){
            $prompt=$pair[0]+' — ID: ';AwaitPrompt $prompt $w 600
            [Yc59ConsoleR11]::Line($prompt,($inputs.saved.selection.($pair[1])))
        }
        JsonNew (Join-Path $Report 'inputs-delivered.json') @{schema='yc59-input-delivery/v1';secret_logged=$false;manual_approval_required=$true;server_pid=$active.server_pid}
        $shared=$config.shared
        $approvalCopied=$false;$request=$null;$decisionClock=$null;$receiptSaved=$false;$cancelled=$false
        while(-not [IO.File]::Exists((Join-Path $Report 'native-exit.json'))){
            $text=[Yc59ConsoleR11]::Text()
            $ap=ApprovalFromConsole $text
            if(-not $approvalCopied -and $null -ne $ap){
                $phrase=$ap.phrase;$rid=$ap.request_id
                WaitFile (Join-Path $shared 'request.json') $null 5;$request=Json (Join-Path $shared 'request.json')
                Need ($request.request_id -ceq $rid -and $request.server_pid -eq $active.server_pid -and $request.operation_sha256 -cmatch '^[0-9a-f]{64}$') 'YC59_APPROVAL_REQUEST_CHANGED'
                Need ($text.Contains('SHA-256 запроса: '+$request.operation_sha256)) 'YC59_APPROVAL_HASH_NOT_DISPLAYED'
                Add-Type -AssemblyName System.Windows.Forms
                $clip=[Windows.Forms.DataObject]::new();$clip.SetData([Windows.Forms.DataFormats]::UnicodeText,$phrase)
                foreach($format in @('CanIncludeInClipboardHistory','CanUploadToCloudClipboard')){$ms=[IO.MemoryStream]::new([byte[]](0,0,0,0));$clip.SetData($format,$ms)}
                [Windows.Forms.Clipboard]::SetDataObject($clip,$true)
                JsonNew (Join-Path $Report 'approval-ready.json') @{request_id=$rid;operation_sha256=$request.operation_sha256;phrase_copied=$true;approval_automated=$false}
                $approvalCopied=$true;$decisionClock=[Diagnostics.Stopwatch]::StartNew()
            }
            $beforeConsume=$false
            if([IO.File]::Exists((Join-Path $shared 'agent-done.json'))){$agentDone=Json (Join-Path $shared 'agent-done.json');$beforeConsume=($agentDone.status -ceq 'STOPPED_BEFORE_CONSUME' -and $agentDone.consume_attempted -eq $false)}
            if($approvalCopied -and -not $cancelled -and ($beforeConsume -or $decisionClock.Elapsed.TotalSeconds -gt 75)){
                $proof=Join-Path $Report 'trusted-no-consume.json'
                $runtime=Join-Path $Release 'payload\component\runtime'
                $args='-I -S -B '+(CommandArgument (Join-Path $PackageRoot 'reject-only.py'))+' '+(CommandArgument $runtime)+' '+$active.server_pid+' '+(CommandArgument (Join-Path $shared 'request.json'))+' '+(CommandArgument $proof)+' '+(CommandArgument $OwnerSid)
                $rejected=Capture $OwnerPython $args
                if($rejected.rc -eq 0){
                    $v=Json $proof
                    EndCancelledChild $child $v $request ([int]$active.server_pid)
                }
                $cancelled=$true
            }
            # Canonical owner JSON is a complete line. Screen wrapping is removed by Text().
            if(-not $receiptSaved){
                $start=$text.IndexOf('{"component_id":', [StringComparison]::Ordinal)
                if($start -ge 0){
                    # Balanced JSON extraction is implemented below, without arbitrary screen logging.
                    $json=ExtractReceipt $text.Substring($start)
                    if($json){$r=$json|ConvertFrom-Json;Need ($r.schema -ceq 'yc-owner-result/v1' -and $r.component_id -ceq $Artifact.Replace('sha256-','sha256:')) 'YC59_OWNER_RECEIPT_INVALID';TextNew (Join-Path $Report 'owner-result.json') $json;$receiptSaved=$true}
                }
            }
            Need ($w.Elapsed.TotalMinutes -lt 14) 'YC59_OWNER_BRIDGE_WAIT_EXPIRED'
            Start-Sleep -Milliseconds 100
        }
        JsonNew (Join-Path $Report 'bridge-done.json') @{schema='yc59-owner-bridge-completion/v1';receipt_saved=$receiptSaved;approval_automated=$false}
    } finally {
        if($null -ne $inputs.secure){$inputs.secure.Dispose()};if($null -ne $child){$child.Dispose()};if('Yc59ConsoleR11' -as [type]){[Yc59ConsoleR11]::Close()}
    }
}
function EndCancelledChild($Child,$Proof,$Request,[int]$ServerPid){
    Need ($Proof.schema -ceq 'yc59-trusted-no-consume/v1' -and $Proof.state -in @('rejected','expired') -and $Proof.request_id -ceq $Request.request_id -and $Proof.operation_sha256 -ceq $Request.operation_sha256 -and $Proof.server_pid -eq $ServerPid -and $Proof.owner_sid -ceq $OwnerSid -and $Child.Id -eq $ServerPid) 'YC59_CANCELLATION_PROOF_CHANGED'
    if(-not $Child.HasExited){$Child.Kill();Need ($Child.WaitForExit(30000)) 'YC59_CANCELLED_PROCESS_DID_NOT_EXIT'}
}
function ExtractReceipt([string]$Text){
    $depth=0;$quoted=$false;$escape=$false
    for($j=0;$j -lt $Text.Length;$j++){
        $c=$Text[$j]
        if($quoted){if($escape){$escape=$false}elseif($c -eq '\'){$escape=$true}elseif($c -eq '"'){$quoted=$false};continue}
        if($c -eq '"'){$quoted=$true}elseif($c -eq '{'){$depth++}elseif($c -eq '}'){$depth--;if($depth -eq 0){return $Text.Substring(0,$j+1)}}
    }
    return $null
}
function ApprovalFromConsole([string]$Text){
    if($Text -cmatch 'Для согласия введите «(РАЗРЕШИТЬ ОДИН РАЗ ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))». Любой другой ответ — отказ:'){
        return @{phrase=$Matches[1];request_id=$Matches[2]}
    }
    return $null
}
function AgentSetup {
    Actor $AgentSid;Personal;CheckPython;CheckGroups;InstalledCheck
    $base=Join-Path $env:LOCALAPPDATA 'agent-toolchain-yc59-assisted'
    $legacyLatch=Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'YC59_Agent_R5\read\read_attempted.json'
    GuardConsumeMarkers (Join-Path $base 'read_attempted.json') $legacyLatch
    if(-not [IO.Directory]::Exists($base)){FreshDirectory $base $AgentSid};NoReparse $base
    GuardConsumeMarkers (Join-Path $base 'read_attempted.json') $legacyLatch
    $agentReports=Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'YC59_Assisted_Agent'
    if(-not [IO.Directory]::Exists($agentReports)){FreshDirectory $agentReports $AgentSid}
    $Report=Join-Path $agentReports ([guid]::NewGuid().ToString('N'));Set-Variable -Name Report -Value $Report -Scope 1;FreshDirectory $Report $AgentSid
    $lease=[IO.File]::Open((Join-Path $base 'waiter.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try {
        if(-not [IO.Directory]::Exists($Public)){FreshDirectory $Public $AgentSid $true};PublicDirectory
        Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PackageRoot 'console-bridge.cs')))
        $seen=@{}
        # Never replay an old published request on listener startup.
        foreach($file in @(Get-ChildItem -LiteralPath $Public -File -Filter 'request-*.json')){
            Need ($file.Name -cmatch '^request-([0-9a-f]{32})\.json$') 'YC59_OLD_COORDINATION_ID_INVALID';$seen[$Matches[1]]=$true
        }
        Write-Host 'AGENT_LISTENER_READY. Оставьте это окно открытым. Через смену пользователя перейдите на владельца.'
        Write-Host 'До запуска владельца защищённого таймера нет. Возвращаться сюда во время испытания не нужно.'
        Write-Host ('Результаты агента без секретов: '+$Report)
        $idle=[Diagnostics.Stopwatch]::StartNew()
        while($idle.Elapsed.TotalHours -lt 12){
            foreach($file in @(Get-ChildItem -LiteralPath $Public -File -Filter 'request-*.json')){
                Need ($file.Name -cmatch '^request-([0-9a-f]{32})\.json$') 'YC59_RUN_ID_INVALID'
                $id=$Matches[1]
                if(-not $seen.ContainsKey($id)){
                    $cfg=Json $file.FullName;Need ($cfg.run_id -ceq $id) 'YC59_COORDINATION_BINDING_CHANGED'
                    $seen[$cfg.run_id]=$true
                    AgentRequest $cfg.run_id $base
                    if([IO.File]::Exists((Join-Path $base 'read_attempted.json'))){Write-Host 'AGENT_STOPPED_AFTER_ONE_CONSUME_ATTEMPT. Итог проверяет окно владельца; нового чтения не будет.';return}
                }
            }
            Start-Sleep -Milliseconds 150
        }
        Write-Host 'AGENT_IDLE_EXPIRED. Активных запросов нет.'
    } finally {$lease.Dispose()}
}
function AgentRequest([string]$RunId,[string]$Base){
    $shared=Join-Path $Public $RunId;Need ([IO.Directory]::Exists($shared)) 'YC59_COORDINATION_RUN_MISSING'
    Need (-not [IO.File]::Exists((Join-Path $Base 'read_attempted.json'))+' '+(CommandArgument $OwnerSid)+' '+(CommandArgument $AgentSid)) 'YC59_PREVIOUS_CONSUME_ATTEMPT_STOP'
    $sha=[Security.Cryptography.SHA256]::Create();try{$digest=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes("yc59.owner.endpoint/v1`n"+$OwnerSid)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    $pipe='\\.\pipe\yc59-'+$digest.Substring(0,32)+'-agent'
    $present=[Yc59ConsoleR11]::WaitNamedPipe($pipe,1);$err=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Need (-not $present -and $err -eq 2) 'YC59_PREEXISTING_ENDPOINT_STOP'
    JsonNew (Join-Path $shared 'agent-ready.json') @{schema='yc59-agent-ready/v1';agent_sid=$AgentSid;session_id=[Diagnostics.Process]::GetCurrentProcess().SessionId;run_id=$RunId;pid=$PID}
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while(-not [Yc59ConsoleR11]::WaitNamedPipe($pipe,1)){
        if([IO.File]::Exists((Join-Path $shared 'cancel'))){return}
        Need ($watch.Elapsed.TotalHours -lt 12) 'YC59_AGENT_IDLE_EXPIRED_NO_CONSUME';Start-Sleep -Milliseconds 100
    }
    $data=Join-Path $env:LOCALAPPDATA 'agent-toolchain';$core=Join-Path $data 'core'
    $args='-I -S -B '+(CommandArgument (Join-Path $PackageRoot 'client-adapter.py'))+' '+(CommandArgument $core)+' '+(CommandArgument $data)+' '+(CommandArgument $shared)+' '+(CommandArgument (Join-Path $Base 'read_attempted.json'))+' '+(CommandArgument $OwnerSid)+' '+(CommandArgument $AgentSid)
    $r=Capture $Python $args
    TextNew (Join-Path $shared 'agent-exit.txt') ([string]$r.rc)
}
function Owner {
    Actor $OwnerSid;Personal;CheckGroups;PublicDirectory;CheckOldSessions
    $Report=ReportDirectory
    Set-Variable -Name Report -Value $Report -Scope 1
    JsonNew (Join-Path $Report 'controller.json') @{pid=$PID;started_ticks=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks}
    Write-Host ('Результаты без секретов: '+$Report)
    $check=Capture $PSExe ('-NoLogo -NoProfile -Command '+(SeedCommand 'Check'))
    Need ($check.rc -eq 0) 'YC59_NATIVE_CHECK_FAILED';$v=$check.stdout|ConvertFrom-Json
    Need ($v.status -ceq 'prepared_verified' -and $v.cloud_invocations -eq 0 -and $v.python_started -eq $false) 'YC59_NATIVE_CHECK_NOT_CONFIRMED'
    Write-Host 'Автоматическая проверка ввода на вымышленных данных. Токен пока не открывается.'
    $fixture=StartRole 'FixtureHost' $Report
    try{
        $test=StartRole 'FixtureBridge' $Report $fixture.Id $true
        WaitFile (Join-Path $Report 'console-selftest-ok.json') $test 360
        $test.WaitForExit();Need ($test.ExitCode -eq 0) 'YC59_CONSOLE_SELFTEST_FAILED_NO_RUN'
    }finally{
        if(-not [IO.File]::Exists((Join-Path $Report 'fixture-close'))){TextNew (Join-Path $Report 'fixture-close') 'close'}
        Need ($fixture.WaitForExit(30000)) 'YC59_SYNTHETIC_FIXTURE_DID_NOT_CLOSE'
    }
    Need ($fixture.ExitCode -eq 0) 'YC59_FIXTURE_HOST_FAILED_NO_RUN'
    Write-Host 'CONSOLE_SELFTEST_OK: скрытый ввод и пять ID проверены автоматически.'
    # Prepare the real token bridge before any native Run/attempt marker.
    $bridge=StartRole 'Bridge' $Report 0 $true
    WaitFile (Join-Path $Report 'bridge-ready.json') $bridge 600
    $data=ReadInputs
    try{Display-Ids $data.saved.selection}finally{$data.secure.Dispose()}
    Write-Host 'Будет прочитана только информация об этой ВМ, диске и снимке. Ресурсы облака не изменяются. Проверка восстановления не выполняется.'
    $bridgeReady=Json (Join-Path $Report 'bridge-ready.json')
    $shownSelectionHash=Hash (Join-Path $data.saved.directory 'selection.json')
    Need ($bridgeReady.pid -eq $bridge.Id -and $bridgeReady.token_decrypted -eq $true -and $bridgeReady.selection_sha256 -ceq $shownSelectionHash) 'YC59_INPUT_BRIDGE_NOT_READY'
    $rid=[IO.Path]::GetFileName($Report);$shared=Join-Path $Public $rid;Set-Variable -Name SharedRun -Value $shared -Scope 1;FreshDirectory $shared $OwnerSid $true
    JsonNew (Join-Path $Report 'config.json') @{shared=$shared;previous_server_pid=(PreviousServerPid)}
    JsonNew (Join-Path $Public ('request-'+$rid+'.json')) @{run_id=$rid}
    Write-Host 'Ожидается ответ ранее запущенного AgentSetup; переключение учётной записи не требуется.'
    WaitFile (Join-Path $shared 'agent-ready.json') $null 600
    $ready=Json (Join-Path $shared 'agent-ready.json')
    Need ($ready.run_id -ceq $rid -and $ready.agent_sid -ceq $AgentSid -and $ready.session_id -ne [Diagnostics.Process]::GetCurrentProcess().SessionId) 'YC59_AGENT_SESSION_NOT_DISTINCT'
    Write-Host 'Агент запущен автоматически в сеансе агента. Переключаться туда не нужно.'
    Write-Host 'Сейчас подтвердите обычный запрос UAC учётными данными выбранного администратора. Таймер защищённого сеанса ещё не запущен.'
    $broker=Start-Process -FilePath $PSExe -Verb RunAs -ArgumentList ('-NoLogo -NoProfile -Command '+(Loader 'Broker' $Report)) -PassThru
    WaitFile (Join-Path $Report 'broker-ready.json') $broker 600
    Write-Host 'Данные и проверка прав готовы. Приготовьтесь прочитать запрос в новом окне.'
    Write-Host 'Для разового согласия фраза будет скопирована автоматически. В новом окне достаточно вставить её правой кнопкой мыши и нажать Enter.'
    Need ((Read-Host 'Когда будете готовы, введите RUN; любой другой ответ — отмена') -ceq 'RUN') 'YC59_CANCELLED_BEFORE_NATIVE_RUN'
    Need (-not $bridge.HasExited -and -not $broker.HasExited) 'YC59_PREPARED_HELPER_EXITED_NO_RUN'
    $latest=Read-Current
    Need ($null -ne $latest -and (Hash (Join-Path $latest.directory 'selection.json')) -ceq $shownSelectionHash) 'YC59_INPUT_SELECTION_CHANGED_BEFORE_RUN'
    CheckOldSessions
    JsonNew (Join-Path $Report 'native-started.json') @{schema='yc59-native-attempt/v1';run_id=$rid;automatic_retry=$false}
    $native=StartRole 'NativeHost' $Report
    JsonNew (Join-Path $Report 'native-pid.json') @{pid=$native.Id}
    Write-Host 'Новое окно выполняет проверку и ввод автоматически. Разрешение даёте только вы.'
    WaitFile (Join-Path $Report 'native-exit.json') $null 900
    WaitFile (Join-Path $Report 'bridge-done.json') $bridge 30
    WaitFile (Join-Path $shared 'agent-exit.txt') $null 45
    TextNew (Join-Path $Report 'native-close') 'close';$native.WaitForExit()
    $exit=Json (Join-Path $Report 'native-exit.json')
    $receipt=Join-Path $Report 'owner-result.json'
    if([IO.File]::Exists($receipt)){
        Need ($exit.exit_code -eq 0 -and $exit.native_parent_returned -eq $true) 'YC59_NATIVE_EXIT_FAILED_NO_RETRY'
        Need ([IO.File]::Exists((Join-Path $shared 'agent-result.json'))) 'YC59_AGENT_RECEIPT_MISSING_NO_RETRY'
        $active=Json (Join-Path $Anchor 'active.json')
        $args='-I -S -B '+(CommandArgument (Join-Path $PackageRoot 'compare-results.py'))+' '+(CommandArgument $receipt)+' '+(CommandArgument (Join-Path $shared 'agent-result.json'))+' '+$active.server_pid+' '+[Diagnostics.Process]::GetCurrentProcess().SessionId+' '+(CommandArgument (Join-Path $Report 'agent-result.json'))+' '+(CommandArgument $OwnerSid)
        $checked=Capture $OwnerPython $args
        Need ($checked.rc -eq 0) 'YC59_RECEIPTS_DONT_MATCH_NO_RETRY'
        JsonNew (Join-Path $Report 'status.json') @{status='RESULTS_READY_PENDING_INDEPENDENT_REVIEW';installed_acceptance=$false;restore_verified=$false;automatic_retry=$false}
        Write-Host 'RESULTS_READY_PENDING_INDEPENDENT_REVIEW. Пришлите только owner-result.json, agent-result.json и status.json из папки результатов.'
    }elseif([IO.File]::Exists((Join-Path $Report 'trusted-no-consume.json'))){
        $proof=Json (Join-Path $Report 'trusted-no-consume.json');$session=(Json (Join-Path $Report 'session.json')).session
        Need ($proof.state -in @('rejected','expired')) 'YC59_TRUSTED_NO_CONSUME_PROOF_INVALID'
        JsonNew (Join-Path $Report 'safe-to-prepare-again.json') @{schema='yc59-repeat-preparation/v1';native_host_closed=$true;request_never_consumable=$true;session=$session;proof_sha256=(Hash (Join-Path $Report 'trusted-no-consume.json'));automatic_retry=$false}
        Write-Host 'REQUEST_CANCELLED_WITHOUT_READ. Разрешение не использовано. Можно снова запустить этот же режим Owner; новый архив и удаление отметок не нужны.'
    }else{throw 'YC59_NATIVE_OUTCOME_REQUIRES_DIAGNOSIS_NO_RETRY'}
    Write-Host ('Результаты без секретов: '+$Report)
}
function ExportReport([string]$Path){
    $names=@('console-selftest-ok.json','owner-result.json','agent-result.json','status.json','trusted-no-consume.json','safe-to-prepare-again.json','native-exit.json','bridge-done.json','inputs-delivered.json','approval-ready.json','bridge-ready.json','audit-done.json','native-started.json','session.json','fixture-bridge-failure.json','console-metrics-FixtureBridge.json','console-metrics-Bridge.json','fixture-child.json','fixture-exit.json','fixture-start.json','fixture-result.json','fixture-value-0.json','fixture-value-1.json','fixture-value-2.json','fixture-value-3.json','fixture-value-4.json','fixture-value-5.json')
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip=[IO.Compression.ZipFile]::Open(($Path+'.zip'),[IO.Compression.ZipArchiveMode]::Create)
    try{
        foreach($f in @(Get-ChildItem -LiteralPath $Path -File)){
            if($f.Name -in $names -or $f.Name -cmatch '^failure-[A-Za-z]+-[0-9a-f]{32}\.json$'){
                Need (-not($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $f.Length -lt 65536) 'YC59_REPORT_FILE_INVALID'
                [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$f.FullName,$f.Name)
            }
        }
    } finally {$zip.Dispose()}
    Write-Host ('Архив результатов без токена: '+$Path+'.zip')
}

$exitCode=0
try {
    switch($Mode){
        'AgentSetup' {AgentSetup}
        'Owner' {Owner}
        'Broker' {Broker}
        'NativeHost' {NativeHost}
        'Bridge' {OwnerBridge}
        'FixtureBridge' {FixtureBridge}
        'FixtureHost' {FixtureHost}
        default {throw 'YC59_ROLE_REFUSED'}
    }
} catch {
    $exitCode=2;$summary=SafeError $_;$code=$summary.code
    # Do not print exception messages, captured console text or raw process output.
    if($Report -and [IO.Directory]::Exists($Report)){
        try{JsonNew (Join-Path $Report ('failure-'+$Mode+'-'+[guid]::NewGuid().ToString('N')+'.json')) @{schema='yc59-r11-stop/v1';version='managed-v1';mode=$Mode;code=$code;exception_type=$summary.exception_type;hresult=$summary.hresult;line=$summary.line;installed_acceptance=$false}}catch{}
        if($Mode -in @('Bridge','FixtureBridge') -and ('Yc59ConsoleR11' -as [type])){try{JsonNew (Join-Path $Report ('console-metrics-'+$Mode+'.json')) ([Yc59ConsoleR11]::Snapshot())}catch{}}
        if($Mode -eq 'Owner'){
            if(-not [IO.File]::Exists((Join-Path $Report 'console-selftest-ok.json')) -and -not [IO.File]::Exists((Join-Path $Report 'fixture-close'))){try{TextNew (Join-Path $Report 'fixture-close') 'close'}catch{}}
            try{TextNew (Join-Path $Report 'cancel') 'cancel'}catch{}
            try{if($SharedRun){TextNew (Join-Path $SharedRun 'cancel') 'cancel'}}catch{}
        }
    }
    if($Mode -notin @('Bridge','FixtureBridge')){Write-Host $code;if($Report){Write-Host ('Результаты: '+$Report)}}
}
if($Mode -in @('Owner','AgentSetup') -and $Report -and [IO.Directory]::Exists($Report)){
    try{ExportReport $Report}catch{Write-Host 'YC59_REPORT_ZIP_UNAVAILABLE';Write-Host ('Файлы результатов: '+$Report)}
}
exit $exitCode
