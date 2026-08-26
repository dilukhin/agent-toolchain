#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
setup_linux.sh has been removed as a supported interface.
Run ./bootstrap_linux.sh once to install toolchainctl, then use:
  toolchainctl check
  toolchainctl apply
EOF
exit 2
