# Read-only CLI surface check in disposable CI; no credentials or cloud requests.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'EPHEMERAL_HOSTED_WINDOWS_REQUIRED' }
$version = '1.39.0'
$url = 'https://storage.yandexcloud.net/yandexcloud-yc/release/' + $version + '/windows/amd64/yc.exe'
$root = Join-Path $env:RUNNER_TEMP ('yc59-cli-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $root)
try {
    $binary = Join-Path $root 'yc.exe'
    $request = [Net.HttpWebRequest]::Create($url)
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 5000
    $request.AllowAutoRedirect = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $response = $request.GetResponse()
    try {
        if ($response.ContentLength -gt 268435456) { throw 'YC_CLI_BINARY_LIMIT' }
        $inputStream = $response.GetResponseStream()
        $output = [IO.File]::Open($binary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] 65536
            $total = 0L
            while (($count = $inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                $total += $count
                if ($total -gt 268435456 -or $watch.Elapsed.TotalSeconds -gt 60) { throw 'YC_CLI_DOWNLOAD_LIMIT' }
                $output.Write($buffer,0,$count)
            }
            $output.Flush($true)
        } finally { $output.Dispose(); $inputStream.Dispose() }
    } finally { $response.Dispose() }
    if ((Get-Item -LiteralPath $binary).Length -gt 268435456) { throw 'YC_CLI_BINARY_LIMIT' }
    $hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    $held = [IO.File]::Open($binary,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    function Read-Surface([string]$Arguments) {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $binary
        $start.Arguments = $Arguments
        $start.WorkingDirectory = $root
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.EnvironmentVariables.Clear()
        $start.EnvironmentVariables['SystemRoot'] = $env:SystemRoot
        $start.EnvironmentVariables['PATH'] = Join-Path $env:SystemRoot 'System32'
        $start.EnvironmentVariables['USERPROFILE'] = $root
        $start.EnvironmentVariables['APPDATA'] = $root
        $start.EnvironmentVariables['LOCALAPPDATA'] = $root
        $start.EnvironmentVariables['YC_CONFIG_DIR'] = $root
        $process = [Diagnostics.Process]::Start($start)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        try {
            if (-not $process.WaitForExit(10000)) { $process.Kill(); throw 'YC_CLI_SURFACE_TIMEOUT' }
            if ($process.ExitCode -ne 0) { throw 'YC_CLI_SURFACE_FAILED' }
            $text = $stdout.Result + $stderr.Result
            if ($text.Length -gt 262144) { throw 'YC_CLI_SURFACE_OUTPUT_LIMIT' }
            return $text
        } finally { $process.Dispose() }
    }
    try {
        $actualVersion = (Read-Surface 'version').Trim()
        if ($actualVersion -notmatch '\b1\.39\.0\b' -or $actualVersion -notmatch 'windows/amd64') { throw 'YC_CLI_VERSION_MISMATCH' }
        $surfaces = @()
        foreach ($kind in @('instance','disk','snapshot')) {
            $help = Read-Surface ('compute ' + $kind + ' get --help')
            foreach ($flag in @('--id','--cloud-id','--folder-id','--format','--retry','--no-user-output','--no-browser')) {
                if (-not $help.Contains($flag)) { throw ('YC_CLI_REQUIRED_FLAG_MISSING: ' + $flag) }
            }
            if (-not $help.Contains('json-rest')) { throw 'YC_CLI_JSON_REST_UNAVAILABLE' }
            $surfaces += ('compute.' + $kind + '.get')
        }
        @{schema='yc59-cli-surface/v1'; source_url=$url; sha256=$hash;
          size=(Get-Item -LiteralPath $binary).Length; version_output=$actualVersion;
          verified_commands=$surfaces; cloud_requests=0; credentials_supplied=$false;
          installed_acceptance=$false} | ConvertTo-Json -Compress
    } finally { $held.Dispose() }
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
