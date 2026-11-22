$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Windows PowerShell can drop empty arguments when invoking native programs.
if ($args.Count -gt 1 -or ($args.Count -eq 1 -and $args[0] -cnotin @("-h", "--help"))) {
    [Console]::Error.WriteLine("Unexpected bootstrap arguments. Use --help for usage.")
    exit 2
}

$Bootstrap = Join-Path $PSScriptRoot "bootstrap_core.py"
if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
    throw "Missing bootstrap_core.py: $Bootstrap"
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & $py.Source -3 -B -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 2)"
    if ($LASTEXITCODE -ne 0) { throw "agent-toolchain requires Python 3.10+." }
    & $py.Source -3 -B $Bootstrap @args
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python 3.10+ is required to bootstrap agent-toolchain." }
& $python.Source -B -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 2)"
if ($LASTEXITCODE -ne 0) { throw "agent-toolchain requires Python 3.10+." }
& $python.Source -B $Bootstrap @args
exit $LASTEXITCODE
