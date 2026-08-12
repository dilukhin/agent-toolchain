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
if (-not (Test-Path -LiteralPath $core)) { throw "setup_core.py not found: $core" }

$python = Get-Command py -ErrorAction SilentlyContinue
$pythonPrefix = @()
if ($python) {
    $pythonExe = $python.Source
    $pythonPrefix = @("-3", "-B")
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python 3 is required to run opencode_setup." }
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

Write-Host "=== OpenCode managed environment setup (Windows) ===" -ForegroundColor Cyan
if ($Check) { Write-Host "Check mode: no files or repositories will be changed." -ForegroundColor Yellow }

& $pythonExe @pythonPrefix @argsCore
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
