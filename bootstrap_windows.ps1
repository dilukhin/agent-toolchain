param(
    [switch]$SkipPathUpdate
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$SourceDir = $PSScriptRoot
if ($env:AGENT_TOOLCHAIN_DATA_DIR) {
    $DataRoot = $env:AGENT_TOOLCHAIN_DATA_DIR
} elseif ($env:LOCALAPPDATA) {
    $DataRoot = Join-Path $env:LOCALAPPDATA "agent-toolchain"
} else {
    $DataRoot = Join-Path $env:USERPROFILE ".local\share\agent-toolchain"
}
if ($env:AGENT_TOOLCHAIN_BIN_DIR) {
    $BinDir = $env:AGENT_TOOLCHAIN_BIN_DIR
} elseif ($env:LOCALAPPDATA) {
    $BinDir = Join-Path $env:LOCALAPPDATA "agent-toolchain\bin"
} else {
    $BinDir = Join-Path $env:USERPROFILE ".local\bin"
}
$CoreDir = Join-Path $DataRoot "core"
$Entrypoint = Join-Path $BinDir "toolchainctl.cmd"
$Marker = "agent-toolchain-core-v1"
$MarkerFile = ".agent-toolchain-managed-core"
$EntrypointMarker = "@REM agent-toolchain:managed-core-entrypoint:v1"

$RequiredFiles = @(
    "toolchainctl.py",
    "setup_core.py",
    "setup_lib.py",
    "setup_manifest.py",
    "setup_migration.py",
    "setup_runtime.py",
    "setup_runtime_legacy.py",
    "setup_managed_tools.py",
    "setup_inventory.py",
    "setup_tools.py",
    "config_data.json"
)
foreach ($name in $RequiredFiles) {
    $path = Join-Path $SourceDir $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing bootstrap source file: $path"
    }
}
foreach ($name in @("templates", "skills\remote-long-running")) {
    $path = Join-Path $SourceDir $name
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Missing bootstrap source directory: $path"
    }
}

$py = Get-Command py -ErrorAction SilentlyContinue
$PythonExe = $null
$PythonPrefix = @()
if ($py) {
    $PythonExe = $py.Source
    $PythonPrefix = @("-3")
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python 3.10+ is required to bootstrap agent-toolchain." }
    $PythonExe = $python.Source
}
& $PythonExe @PythonPrefix -B -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 2)"
if ($LASTEXITCODE -ne 0) { throw "agent-toolchain requires Python 3.10+ for its stdlib-only core." }

if (Test-Path -LiteralPath $Entrypoint) {
    $first = Get-Content -LiteralPath $Entrypoint -TotalCount 1
    if ($first -ne $EntrypointMarker) {
        throw "Refusing to replace foreign toolchainctl entrypoint: $Entrypoint"
    }
}
if (Test-Path -LiteralPath $CoreDir) {
    $owner = Join-Path $CoreDir $MarkerFile
    if (-not (Test-Path -LiteralPath $owner -PathType Leaf) -or ((Get-Content -LiteralPath $owner -Raw).Trim() -ne $Marker)) {
        throw "Refusing to replace core directory without exact agent-toolchain ownership marker: $CoreDir"
    }
}

New-Item -ItemType Directory -Force -Path $DataRoot, $BinDir | Out-Null
$Staging = Join-Path $DataRoot (".core.tmp-" + $PID + "-" + [guid]::NewGuid().ToString("N"))
$Backup = $null
try {
    New-Item -ItemType Directory -Path $Staging | Out-Null
    foreach ($name in $RequiredFiles) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $name) -Destination (Join-Path $Staging $name)
    }
    Copy-Item -LiteralPath (Join-Path $SourceDir "templates") -Destination (Join-Path $Staging "templates") -Recurse
    New-Item -ItemType Directory -Path (Join-Path $Staging "skills") | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceDir "skills\remote-long-running") -Destination (Join-Path $Staging "skills\remote-long-running") -Recurse
    Set-Content -LiteralPath (Join-Path $Staging $MarkerFile) -Value $Marker -Encoding ASCII

    $CompileFiles = $RequiredFiles | Where-Object { $_ -like "*.py" } | ForEach-Object { Join-Path $Staging $_ }
    & $PythonExe @PythonPrefix -B -m py_compile @CompileFiles
    if ($LASTEXITCODE -ne 0) { throw "Staged agent-toolchain core failed Python compile validation." }

    if (Test-Path -LiteralPath $CoreDir) {
        $Backup = Join-Path $DataRoot ("core.previous." + (Get-Date -Format "yyyyMMddHHmmss") + "." + $PID)
        if (Test-Path -LiteralPath $Backup) { throw "Bootstrap backup path already exists: $Backup" }
        Move-Item -LiteralPath $CoreDir -Destination $Backup
    }
    try {
        Move-Item -LiteralPath $Staging -Destination $CoreDir
        $Staging = $null
    } catch {
        if ($Backup -and (Test-Path -LiteralPath $Backup) -and -not (Test-Path -LiteralPath $CoreDir)) {
            Move-Item -LiteralPath $Backup -Destination $CoreDir
            $Backup = $null
        }
        throw
    }
} finally {
    if ($Staging -and (Test-Path -LiteralPath $Staging)) {
        Remove-Item -LiteralPath $Staging -Recurse -Force
    }
}

$ToolchainPy = Join-Path $CoreDir "toolchainctl.py"
$prefixText = if ($PythonPrefix.Count -gt 0) { " " + ($PythonPrefix -join " ") } else { "" }
$entryText = @"
$EntrypointMarker
@echo off
@"$PythonExe"$prefixText -B "$ToolchainPy" %*
"@
[System.IO.File]::WriteAllText($Entrypoint, $entryText, [System.Text.UTF8Encoding]::new($true))

Write-Host ("configured        agent-toolchain core  " + $CoreDir)
Write-Host ("configured        toolchainctl entrypoint  " + $Entrypoint)
if ($Backup) { Write-Host ("info              previous managed core retained  " + $Backup) }

if (-not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($userPath) { $entries = @($userPath -split ';' | Where-Object { $_ }) }
    $already = $false
    foreach ($entry in $entries) {
        if ($entry.TrimEnd('\') -ieq $BinDir.TrimEnd('\')) { $already = $true; break }
    }
    if (-not $already) {
        $newUserPath = if ($userPath) { $userPath.TrimEnd(';') + ";" + $BinDir } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
        Write-Host ("configured        user PATH  added " + $BinDir)
    } else {
        Write-Host ("up-to-date        user PATH  " + $BinDir)
    }
    $processEntries = @($env:Path -split ';')
    if (-not ($processEntries | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
        $env:Path = $env:Path.TrimEnd(';') + ";" + $BinDir
    }
}

& $Entrypoint --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Installed toolchainctl entrypoint failed validation." }
Write-Host ("up-to-date        toolchainctl health  " + $Entrypoint)
