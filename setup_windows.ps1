param(
    [string]$ConfigDir = "$env:USERPROFILE\.config\opencode",
    [string]$StashDir = "$env:USERPROFILE\projects\stash\opencode.ai",
    [string]$CredentialDir = "",
    [string]$SkillsDir = "$env:USERPROFILE\.agents\skills",
    [string]$StateDir = "",
    [string]$ProjectsDir = "$env:USERPROFILE\projects",
    [string]$RuntimeDir = "",
    [switch]$Check,
    [switch]$Force,
    [switch]$SkipPackageInstall,
    [switch]$SkipDependencyInstall,
    [string]$SshRelayUrl = "",
    [string]$AgentSafeUrl = ""
)

$ErrorActionPreference = "Stop"

if (-not $CredentialDir) {
    $CredentialDir = Join-Path $ConfigDir "credentials"
}
if (-not $StateDir) {
    if ($env:LOCALAPPDATA) {
        $StateDir = Join-Path $env:LOCALAPPDATA "opencode_setup\state"
    } else {
        $StateDir = Join-Path $env:USERPROFILE ".local\state\opencode_setup"
    }
}
if (-not $RuntimeDir) {
    if ($env:OPENCODE_SETUP_RUNTIME_DIR) {
        $RuntimeDir = $env:OPENCODE_SETUP_RUNTIME_DIR
    } elseif ($env:LOCALAPPDATA) {
        $RuntimeDir = Join-Path $env:LOCALAPPDATA "opencode_setup\runtime\python"
    } else {
        $RuntimeDir = Join-Path $env:USERPROFILE ".local\share\opencode_setup\runtime\python"
    }
}

$core = Join-Path $PSScriptRoot "setup_core.py"
if (-not (Test-Path -LiteralPath $core)) { throw "Не найден setup_core.py: $core" }

$python = Get-Command py -ErrorAction SilentlyContinue
$basePythonPrefix = @()
if ($python) {
    $basePythonExe = $python.Source
    $basePythonPrefix = @("-3", "-B")
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Для запуска opencode_setup требуется Python 3." }
    $basePythonExe = $python.Source
    $basePythonPrefix = @("-B")
}

$runtimePython = Join-Path $RuntimeDir "Scripts\python.exe"
$corePythonExe = $basePythonExe
$corePythonPrefix = $basePythonPrefix

if ($Check) {
    if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
        $corePythonExe = $runtimePython
        $corePythonPrefix = @("-B")
    }
} else {
    if ((Test-Path -LiteralPath $RuntimeDir) -and -not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        throw "Managed Python runtime path exists but is incomplete: $RuntimeDir. Refusing to replace it automatically."
    }

    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        $runtimeParent = Split-Path -Parent $RuntimeDir
        New-Item -ItemType Directory -Path $runtimeParent -Force | Out-Null
        $runtimeTmp = Join-Path $runtimeParent (".python-runtime.tmp-" + $PID + "-" + [guid]::NewGuid().ToString("N"))
        try {
            & $basePythonExe @basePythonPrefix -m venv $runtimeTmp
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create isolated Python runtime. Ensure the selected Python includes the venv module."
            }
            $tmpPython = Join-Path $runtimeTmp "Scripts\python.exe"
            if (-not (Test-Path -LiteralPath $tmpPython -PathType Leaf)) {
                throw "Python venv creation completed without Scripts\python.exe."
            }
            & $tmpPython -m pip --version | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Created Python runtime has no working pip: $runtimeTmp"
            }
            if (Test-Path -LiteralPath $RuntimeDir) {
                throw "Runtime path appeared concurrently; refusing to replace it: $RuntimeDir"
            }
            Move-Item -LiteralPath $runtimeTmp -Destination $RuntimeDir
            $runtimeTmp = $null
        } finally {
            if ($runtimeTmp -and (Test-Path -LiteralPath $runtimeTmp)) {
                Remove-Item -LiteralPath $runtimeTmp -Recurse -Force
            }
        }
    }

    $corePythonExe = $runtimePython
    $corePythonPrefix = @("-B")
}

$argsCore = @(
    $core,
    "--config-dir", $ConfigDir,
    "--stash-dir", $StashDir,
    "--credential-dir", $CredentialDir,
    "--skills-dir", $SkillsDir,
    "--state-dir", $StateDir,
    "--projects-dir", $ProjectsDir
)
if ($Check) { $argsCore += "--check" }
if ($Force) { $argsCore += "--force" }
if ($SkipPackageInstall) { $argsCore += "--skip-package-install" }
if ($SkipDependencyInstall) { $argsCore += "--skip-dependency-install" }
if ($SshRelayUrl) { $argsCore += @("--ssh-relay-url", $SshRelayUrl) }
if ($AgentSafeUrl) { $argsCore += @("--agent-safe-url", $AgentSafeUrl) }

Write-Host "=== Настройка управляемого окружения OpenCode (Windows) ===" -ForegroundColor Cyan
if ($Check) { Write-Host "Режим проверки: файлы и репозитории изменяться не будут." -ForegroundColor Yellow }

$oldPythonUtf8 = $env:PYTHONUTF8
$oldPythonIoEncoding = $env:PYTHONIOENCODING
$exitCode = 0
try {
    # Русские диагностические сообщения должны одинаково работать в консоли и при capture через pipe.
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $corePythonExe @corePythonPrefix @argsCore
    $exitCode = $LASTEXITCODE
} finally {
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $oldPythonUtf8 }
    if ($null -eq $oldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $oldPythonIoEncoding }
}

if ($exitCode -ne 0) { exit $exitCode }
