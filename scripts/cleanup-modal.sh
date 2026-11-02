#!/usr/bin/env bash
# Delete the Modal resources created while testing rnix. DRY_RUN=1 only prints.
set -euo pipefail
cd "$(dirname "$0")/.."
exec nix develop . --command python3 scripts/cleanup-modal.py "$@"
