$ErrorActionPreference = "Stop"
$message = @"
setup_windows.ps1 has been removed as a supported interface.
Run .\bootstrap_windows.ps1 once to install toolchainctl, then use:
  toolchainctl check
  toolchainctl apply
"@
[Console]::Error.WriteLine($message)
exit 2
