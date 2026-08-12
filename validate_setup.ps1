param([switch]$TestBmad)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

foreach ($script in @("setup_windows.ps1", "install_bmad_windows.ps1", "validate_setup.ps1")) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root $script), [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "PowerShell parse failed for $script`: $($errors -join '; ')" }
}
Write-Host "PASS PowerShell syntax"

$data = Get-Content -LiteralPath (Join-Path $root "config_data.json") -Raw | ConvertFrom-Json
if ($data.bmad.skills.Count -ne 44) { throw "config_data.json must contain 44 BMAD skills." }
Write-Host "PASS config_data.json (44 BMAD IDs)"

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("opencode-setup-" + [guid]::NewGuid().ToString("N"))
$configDir = Join-Path $testRoot "config"
$stashDir = Join-Path $testRoot "stash"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

try {
    & (Join-Path $root "setup_windows.ps1") -ConfigDir $configDir -StashDir $stashDir -SkipPackageInstall
    $keyFile = Join-Path $stashDir "api-key.txt"
    "test-key-must-survive" | Set-Content -LiteralPath $keyFile -NoNewline -Encoding ASCII
    & (Join-Path $root "setup_windows.ps1") -ConfigDir $configDir -StashDir $stashDir -SkipPackageInstall
    if ((Get-Content -LiteralPath $keyFile -Raw) -ne "test-key-must-survive") { throw "Existing API key was overwritten." }

    $generated = Get-Content -LiteralPath (Join-Path $configDir "opencode.jsonc") -Raw | ConvertFrom-Json
    if ($generated.'$schema' -ne "https://opencode.ai/config.json") { throw "Generated config has an invalid schema field." }
    if (@($generated.provider.routerai.models.PSObject.Properties).Count -ne 13) { throw "Generated config must contain 13 models." }
    Write-Host "PASS isolated Windows setup, config structure, and API key preservation"

    if ($TestBmad) {
        $bmadTarget = Join-Path $testRoot "bmad-project"
        New-Item -ItemType Directory -Path $bmadTarget | Out-Null
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        & (Join-Path $root "install_bmad_windows.ps1") $bmadTarget
        Write-Host "PASS isolated BMAD install and repeated install"
    }
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
