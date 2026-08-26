param([switch]$TestBmad)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

foreach ($script in @("bootstrap_windows.ps1", "setup_windows.ps1", "install_bmad_windows.ps1", "validate_setup.ps1")) {
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
    $pythonPrefix = @("-3", "-B")
} else {
    $python = Get-Command python -ErrorAction Stop
    $pythonExe = $python.Source
    $pythonPrefix = @("-B")
}
& $pythonExe @pythonPrefix -m py_compile (Join-Path $root "setup_core.py") (Join-Path $root "setup_lib.py") (Join-Path $root "setup_migration.py") (Join-Path $root "setup_runtime.py")
if ($LASTEXITCODE -ne 0) { throw "setup Python modules compile failed" }

$data = Get-Content -LiteralPath (Join-Path $root "config_data.json") -Raw | ConvertFrom-Json
if (@($data.models.PSObject.Properties).Count -ne 13) { throw "config_data.json must contain 13 RouterAI models." }
if ($data.bmad.skills.Count -eq 0 -or @($data.bmad.skills | Sort-Object -Unique).Count -ne $data.bmad.skills.Count) { throw "config_data.json BMAD skills must be non-empty and unique." }
Write-Host "PASS config_data.json"

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-toolchain-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

function Invoke-GitChecked {
    param([string[]]$GitArgs)
    & git @GitArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git failed: git $($GitArgs -join ' ')" }
}

function New-FixtureRemote {
    param([string]$Kind, [string]$Branch)
    $seed = Join-Path $testRoot "$Kind-seed"
    $bare = Join-Path $testRoot "$Kind.git"
    Invoke-GitChecked @("init", "-q", "-b", $Branch, $seed)
    Invoke-GitChecked @("-C", $seed, "config", "user.email", "test@example.invalid")
    Invoke-GitChecked @("-C", $seed, "config", "user.name", "agent-toolchain-test")
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
"@ | Set-Content -LiteralPath (Join-Path $skillDir "SKILL.md") -Encoding ASCII
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
"@ | Set-Content -LiteralPath (Join-Path $skillDir "SKILL.md") -Encoding ASCII
        }
    }
    Invoke-GitChecked @("-C", $seed, "add", ".")
    Invoke-GitChecked @("-C", $seed, "commit", "-qm", "init")
    Invoke-GitChecked @("clone", "-q", "--bare", $seed, $bare)
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

    $testHome = Join-Path $testRoot "home"
    $configDir = Join-Path $testHome ".config\opencode"
    $stashDir = Join-Path $testHome "projects\stash\opencode.ai"
    $credentialDir = Join-Path $configDir "credentials"
    $skillsDir = Join-Path $testHome ".agents\skills"
    $stateDir = Join-Path $testHome "state"
    $projectsDir = Join-Path $testHome "projects"
    New-Item -ItemType Directory -Path $stashDir, (Join-Path $skillsDir "custom-user"), (Join-Path $skillsDir "bmad-user-skill") -Force | Out-Null

    $keyFile = Join-Path $stashDir "api-key.txt"
    [System.IO.File]::WriteAllBytes($keyFile, [System.Text.Encoding]::UTF8.GetBytes("rk-test-preserve`r`nsecond-line"))
    $keyBefore = (Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash
    "user skill must survive" | Set-Content -LiteralPath (Join-Path $skillsDir "custom-user\SKILL.md") -Encoding ASCII
    "BMAD-like user skill must survive" | Set-Content -LiteralPath (Join-Path $skillsDir "bmad-user-skill\SKILL.md") -Encoding ASCII

    function Invoke-CoreFixture {
        param([switch]$Check)
        $coreArgs = @(
            (Join-Path $root "setup_core.py"),
            "--repo-root", $root,
            "--config-dir", $configDir,
            "--stash-dir", $stashDir,
            "--credential-dir", $credentialDir,
            "--skills-dir", $skillsDir,
            "--state-dir", $stateDir,
            "--projects-dir", $projectsDir,
            "--skip-package-install",
            "--skip-dependency-install",
            "--ssh-relay-url", $sshRemote,
            "--agent-safe-url", $safeRemote
        )
        if ($Check) { $coreArgs += "--check" }

        $oldPythonUtf8 = $env:PYTHONUTF8
        $oldPythonIoEncoding = $env:PYTHONIOENCODING
        try {
            $env:PYTHONUTF8 = "1"
            $env:PYTHONIOENCODING = "utf-8"
            $output = (& $pythonExe @pythonPrefix @coreArgs 2>&1 | Out-String)
            $exitCode = $LASTEXITCODE
        } finally {
            if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
            else { $env:PYTHONUTF8 = $oldPythonUtf8 }
            if ($null -eq $oldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
            else { $env:PYTHONIOENCODING = $oldPythonIoEncoding }
        }
        return [pscustomobject]@{ Output = $output; ExitCode = $exitCode }
    }

    $first = Invoke-CoreFixture
    $firstOutput = $first.Output
    if ($first.ExitCode -ne 0) { throw "isolated Windows core reconciliation failed" }
    if ($firstOutput -notmatch '(?m)^configured\s+OpenCode config') { throw "Initial Windows apply must report configured OpenCode config." }
    if ($firstOutput -notmatch '(?m)^configured\s+ownership manifest') { throw "Initial Windows apply must report configured ownership manifest." }
    if ((Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash -ne $keyBefore) { throw "Existing API key bytes changed." }
    if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "custom-user\SKILL.md"))) { throw "Unknown user skill was removed." }
    if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "bmad-user-skill\SKILL.md"))) { throw "BMAD-like user skill was removed." }
    foreach ($skill in @("ssh-relay", "remote-long-running", "recovery-mode", "risk-gate", "safe-cli", "unknown-system-safety")) {
        if (-not (Test-Path -LiteralPath (Join-Path $skillsDir "$skill\SKILL.md"))) { throw "Missing managed skill: $skill" }
    }
    $generated = Get-Content -LiteralPath (Join-Path $configDir "opencode.jsonc") -Raw | ConvertFrom-Json
    if (@($generated.provider.routerai.models.PSObject.Properties).Count -ne 13) { throw "Generated config must contain 13 models." }
    if ((Get-Content -LiteralPath (Join-Path $configDir "AGENTS.md")).Count -gt 12) { throw "AGENTS.md is not compact." }
    Write-Host "PASS isolated Windows core reconciliation + ownership boundaries"

    $before = Get-TreeSnapshot -Path $testHome
    $repeat = Invoke-CoreFixture
    $repeatOutput = $repeat.Output
    if ($repeat.ExitCode -ne 0) { throw "repeated Windows core reconciliation failed" }
    $after = Get-TreeSnapshot -Path $testHome
    if ($before -ne $after) { throw "Repeated Windows core reconciliation changed file bytes." }
    if ($repeatOutput -match '(?m)^configured\s+') { throw "Repeated no-op Windows core reconciliation must not report configured rows." }
    Write-Host "PASS repeated Windows core reconciliation is idempotent and reports no new configuration"

    $beforeCheck = Get-TreeSnapshot -Path $testHome
    $checked = Invoke-CoreFixture -Check
    $checkOutput = $checked.Output
    if ($checked.ExitCode -ne 0) { throw "Windows core --check failed in clean state" }
    $afterCheck = Get-TreeSnapshot -Path $testHome
    if ($beforeCheck -ne $afterCheck) { throw "Windows core --check changed file bytes." }
    if ($checkOutput -match '(?m)^(configured|failed)\s+') { throw "Windows core --check must not report apply action states." }
    Write-Host "PASS Windows core check mode is read-only"

    if ($TestBmad) {
        $bmadTarget = Join-Path $testRoot "bmad-project"
        New-Item -ItemType Directory -Path $bmadTarget | Out-Null
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        if ($LASTEXITCODE -ne 0) { throw "BMAD install failed" }
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        if ($LASTEXITCODE -ne 0) { throw "BMAD repeated install failed" }
        Write-Host "PASS isolated BMAD install and repeated install"
    }
} catch {
    Write-Host "::error title=Windows setup validation::$($_.Exception.Message)"
    throw
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
