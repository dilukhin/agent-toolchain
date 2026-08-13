param(
    [string]$ConfigDir = "$env:USERPROFILE\.config\opencode",
    [string]$StashDir = "$env:USERPROFILE\projects\stash\opencode.ai",
    [string]$CredentialDir = "",
    [string]$SkillsDir = "$env:USERPROFILE\.agents\skills",
    [string]$StateDir = "",
    [string]$ProjectsDir = "$env:USERPROFILE\projects",
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

$core = Join-Path $PSScriptRoot "setup_core.py"
if (-not (Test-Path -LiteralPath $core)) { throw "Не найден setup_core.py: $core" }

$python = Get-Command py -ErrorAction SilentlyContinue
$pythonPrefix = @()
if ($python) {
    $pythonExe = $python.Source
    $pythonPrefix = @("-3", "-B")
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Для запуска opencode_setup требуется Python 3." }
    $pythonExe = $python.Source
    $pythonPrefix = @("-B")
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
    & $pythonExe @pythonPrefix @argsCore
    $exitCode = $LASTEXITCODE
} finally {
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $oldPythonUtf8 }
    if ($null -eq $oldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $oldPythonIoEncoding }
}

if ($exitCode -ne 0) { exit $exitCode }
