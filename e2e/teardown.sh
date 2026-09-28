#!/usr/bin/env bash
# Delete the end-to-end VMs, the Incus project, and the local test directory.
set -euo pipefail
PROJECT=${PROJECT:-cf-e2e}
E2E_DIR=${E2E_DIR:-/tmp/cf-e2e}
if incus project show "$PROJECT" >/dev/null 2>&1; then
  for vm in $(incus list --project "$PROJECT" -c n -f csv); do
    incus delete --force "$vm" --project "$PROJECT"
  done
  incus project delete "$PROJECT"
fi
rm -rf "$E2E_DIR"
