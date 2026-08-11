param(
    [Parameter(Position = 0)]
    [string]$ProjectPath = (Get-Location).Path
)

$ErrorActionPreference = "Stop"
$BmadVersion = "6.8.0"
$ExpectedIntegrity = "sha512-RRkXdhrFJdnD7lIeR6OuacUDDPZA+0/k+kHmD+9Us7XQ5W6ptSAzxsS/SoNkNe37X0YHwQIyLyKGV9b3iXzWpw=="
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

if (Test-Path -LiteralPath $ManifestPath) {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw
    if ($manifest -notmatch '(?m)^\s*version:\s*6\.8\.0\s*$') {
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
