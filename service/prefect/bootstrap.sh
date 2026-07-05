#!/usr/bin/env bash
# Idempotent bootstrap: create the work pool and register deployments.
#
# Run once after the Prefect server is up (from inside the prefect container):
#   docker exec prefect bash bootstrap.sh
#
# Safe to re-run: an existing work pool is left as-is and deployments are updated.
set -euo pipefail

POOL_NAME="${PREFECT_WORK_POOL:-noktah-pool}"

echo "==> Ensuring work pool '${POOL_NAME}' (type: process) exists"
if prefect work-pool inspect "${POOL_NAME}" >/dev/null 2>&1; then
  echo "    work pool already exists; leaving as-is"
else
  prefect work-pool create --type process "${POOL_NAME}"
fi

echo "==> Registering deployments from prefect.yaml"
prefect deploy --all

echo "==> Done. Deployments:"
prefect deployment ls
