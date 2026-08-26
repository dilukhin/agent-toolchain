$ErrorActionPreference = "Stop"
Write-Error @"
setup_windows.ps1 has been removed as a supported interface.
Run .\bootstrap_windows.ps1 once to install toolchainctl, then use:
  toolchainctl check
  toolchainctl apply
"@ -ErrorAction Continue
exit 2
