param([switch]$TestBmad)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

foreach ($script in @("setup_windows.ps1", "install_bmad_windows.ps1", "validate_setup.ps1")) {
    $path = Join-Path $root $script
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed for $script`: $($errors -join '; ')" }
}
Write-Host "PASS PowerShell syntax"

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    $pythonExe = $python.Source
    $pythonPrefix = @("-3")
} else {
    $python = Get-Command python -ErrorAction Stop
    $pythonExe = $python.Source
    $pythonPrefix = @()
}
& $pythonExe @pythonPrefix -m py_compile (Join-Path $root "setup_core.py") (Join-Path $root "setup_lib.py") (Join-Path $root "setup_runtime.py")
if ($LASTEXITCODE -ne 0) { throw "setup Python modules compile failed" }

$data = Get-Content -LiteralPath (Join-Path $root "config_data.json") -Raw | ConvertFrom-Json
if (@($data.models.PSObject.Properties).Count -ne 13) { throw "config_data.json must contain 13 RouterAI models." }
if ($data.bmad.skills.Count -ne 44) { throw "config_data.json must contain 44 BMAD skills." }
Write-Host "PASS config_data.json"

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("opencode-setup-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

function Invoke-GitChecked {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & git @Args | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git failed: git $($Args -join ' ')" }
}

function New-FixtureRemote {
    param([string]$Kind, [string]$Branch)
    $seed = Join-Path $testRoot "$Kind-seed"
    $bare = Join-Path $testRoot "$Kind.git"
    Invoke-GitChecked init -q -b $Branch $seed
    Invoke-GitChecked -C $seed config user.email "test@example.invalid"
    Invoke-GitChecked -C $seed config user.name "opencode-setup-test"
    if ($Kind -eq "ssh") {
        $skillDir = Join-Path $seed "opencode\skills\ssh-relay"
        New-Item -ItemType Directory -Path $skillDir -Force | Out-Null
        @"
---
name: ssh-relay
description: Test authoritative ssh-relay skill.
compatibility: opencode
---
# ssh relay fixture
"@ | Set-Content -LiteralPath (Join-Path $skillDir "SKILL.md") -Encoding UTF8
    } else {
        foreach ($skill in @("recovery-mode", "risk-gate", "safe-cli", "unknown-system-safety")) {
            $skillDir = Join-Path $seed "opencode\skills\$skill"
            New-Item -ItemType Directory -Path $skillDir -Force | Out-Null
            @"
---
name: $skill
description: Test authoritative $skill skill.
compatibility: opencode
---
# $skill fixture
"@ | Set-Content -LiteralPath (Join-Path $skillDir "SKILL.md") -Encoding UTF8
        }
    }
    Invoke-GitChecked -C $seed add .
    Invoke-GitChecked -C $seed commit -qm init
    Invoke-GitChecked clone -q --bare $seed $bare
    return $bare
}

function Get-TreeSnapshot {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    $items = Get-ChildItem -LiteralPath $Path -File -Recurse | Sort-Object FullName
    return (($items | ForEach-Object {
        $rel = $_.FullName.Substring($Path.Length).TrimStart('\')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        "$rel`t$hash"
    }) -join "`n")
}

try {
    $sshRemote = New-FixtureRemote -Kind "ssh" -Branch "main"
    $safeRemote = New-FixtureRemote -Kind "safe" -Branch "master"

    $home = Join-Path $testRoot "home"
    $configDir = Join-Path $home ".config\opencode"
    $stashDir = Join-Path $home "projects\stash\opencode.ai"
    $skillsDir = Join-Path $home ".agents\skills"
    $stateDir = Join-Path $home "state"
    $projectsDir = Join-Path $home "projects"
    New-Item -ItemType Directory -Path $stashDir, (Join-Path $skillsDir "custom-user"), (Join-Path $skillsDir "bmad-user-skill") -Force | Out-Null

    $keyFile = Join-Path $stashDir "api-key.txt"
    [System.IO.File]::WriteAllBytes($keyFile, [System.Text.Encoding]::UTF8.GetBytes("rk-test-preserve`r`nsecond-line"))
    $keyBefore = (Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash
    "user skill must survive" | Set-Content -LiteralPath (Join-Path $skillsDir "custom-user\SKILL.md") -Encoding UTF8
    "BMAD-like user skill must survive" | Set-Content -LiteralPath (Join-Path $skillsDir "bmad-user-skill\SKILL.md") -Encoding UTF8

    $setupParams = @{
        ConfigDir = $configDir
        StashDir = $stashDir
        SkillsDir = $skillsDir
        StateDir = $stateDir
        ProjectsDir = $projectsDir
        SkipPackageInstall = $true
        SkipDependencyInstall = $true
        SshRelayUrl = $sshRemote
        AgentSafeUrl = $safeRemote
    }

    & (Join-Path $root "setup_windows.ps1") @setupParams
    if ($LASTEXITCODE -ne 0) { throw "isolated Windows setup failed" }
    if ((Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash -ne $keyBefore) { throw "Existing API key bytes changed." }
    if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "custom-user\SKILL.md"))) { throw "Unknown user skill was removed." }
    if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "bmad-user-skill\SKILL.md"))) { throw "BMAD-like user skill was removed." }
    foreach ($skill in @("ssh-relay", "remote-long-running", "recovery-mode", "risk-gate", "safe-cli", "unknown-system-safety")) {
        if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "$skill\SKILL.md"))) { throw "Missing managed skill: $skill" }
    }
    $generated = Get-Content -LiteralPath (Join-Path $configDir "opencode.jsonc") -Raw | ConvertFrom-Json
    if (@($generated.provider.routerai.models.PSObject.Properties).Count -ne 13) { throw "Generated config must contain 13 models." }
    if ((Get-Content -LiteralPath (Join-Path $configDir "AGENTS.md")).Count -gt 12) { throw "AGENTS.md is not compact." }
    Write-Host "PASS isolated Windows install + ownership boundaries"

    $before = Get-TreeSnapshot -Path $home
    & (Join-Path $root "setup_windows.ps1") @setupParams
    if ($LASTEXITCODE -ne 0) { throw "repeated Windows setup failed" }
    $after = Get-TreeSnapshot -Path $home
    if ($before -ne $after) { throw "Repeated Windows setup changed file bytes." }
    Write-Host "PASS repeated Windows setup is idempotent"

    $beforeCheck = Get-TreeSnapshot -Path $home
    & (Join-Path $root "setup_windows.ps1") @setupParams -Check
    if ($LASTEXITCODE -ne 0) { throw "Windows --check failed in clean state" }
    $afterCheck = Get-TreeSnapshot -Path $home
    if ($beforeCheck -ne $afterCheck) { throw "Windows --check changed file bytes." }
    Write-Host "PASS Windows check mode is read-only"

    if ($TestBmad) {
        $bmadTarget = Join-Path $testRoot "bmad-project"
        New-Item -ItemType Directory -Path $bmadTarget | Out-Null
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        if ($LASTEXITCODE -ne 0) { throw "BMAD install failed" }
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        if ($LASTEXITCODE -ne 0) { throw "BMAD repeated install failed" }
        Write-Host "PASS isolated BMAD install and repeated install"
    }
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
