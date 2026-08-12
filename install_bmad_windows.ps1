param(
    [Parameter(Position = 0)]
    [string]$ProjectPath = (Get-Location).Path
)

$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $PSScriptRoot "config_data.json"
$BmadConfig = (Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json).bmad
$BmadVersion = [string]$BmadConfig.version
$ExpectedIntegrity = [string]$BmadConfig.npm_integrity
$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
$ManifestPath = Join-Path $ProjectPath "_bmad\_config\manifest.yaml"
$SkillsPath = Join-Path $ProjectPath ".agents\skills"

if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Project directory does not exist: $ProjectPath"
}

$nodeVersion = (& node -p "process.versions.node").Trim()
if ([version]$nodeVersion -lt [version]"20.12.0") {
    throw "BMAD $BmadVersion requires Node.js 20.12.0 or newer (found $nodeVersion)."
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    $pythonVersion = (& $python.Source -3 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "BMAD $BmadVersion requires Python 3.11 or newer." }
    $pythonVersion = (& $python.Source -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
}
if ([version]$pythonVersion -lt [version]"3.11.0") {
    throw "BMAD $BmadVersion requires Python 3.11 or newer (found $pythonVersion)."
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "BMAD $BmadVersion requires uv for rendered executable skills. Install uv and retry."
}
& $uv.Source --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "uv is present but could not be executed." }

if (Test-Path -LiteralPath $ManifestPath) {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw
    $escapedVersion = [regex]::Escape($BmadVersion)
    if ($manifest -notmatch "(?m)^\s*version:\s*$escapedVersion\s*$") {
        throw "Existing BMAD is not version $BmadVersion. It was preserved; update it manually before retrying."
    }
} elseif (Test-Path -LiteralPath $SkillsPath) {
    $existingBmadSkill = Get-ChildItem -LiteralPath $SkillsPath -Directory -Filter "bmad-*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingBmadSkill) {
        throw "Unmanaged BMAD skills already exist in $SkillsPath. They were preserved; move or reconcile them before retrying."
    }
}

Write-Host "Checking official bmad-method@$BmadVersion package..."
$actualIntegrity = (& npm view "bmad-method@$BmadVersion" dist.integrity).Trim()
if ($actualIntegrity -ne $ExpectedIntegrity) {
    throw "npm integrity mismatch for bmad-method@$BmadVersion."
}

Write-Host "Installing project-local BMAD $BmadVersion into $ProjectPath..."
& npx --yes "bmad-method@$BmadVersion" install --directory $ProjectPath --modules bmm --tools opencode --yes
if ($LASTEXITCODE -ne 0) { throw "BMAD installer failed with exit code $LASTEXITCODE." }

& node (Join-Path $PSScriptRoot "validate_bmad.js") $ProjectPath
if ($LASTEXITCODE -ne 0) { throw "BMAD post-install validation failed." }
