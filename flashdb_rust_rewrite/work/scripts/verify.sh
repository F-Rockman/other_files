#!/usr/bin/env bash
# Compatibility wrapper. The authoritative verifier is flashdb_pipeline.py.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

exec python3 work/scripts/flashdb_pipeline.py verify --strict
