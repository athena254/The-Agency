#!/usr/bin/env bash
# Start the isolated single-owner pilot launcher.
#
# Box path: /workspace/start_pilot.sh
# This is the reference implementation for the Simular box; the box copy must
# match this contract. It was not verified against the box in this pass.
#
# Usage:
#   scripts/start_pilot.sh --data-dir <absolute-dir> --config <absolute-dir>/owner.json
#   scripts/start_pilot.sh --data-dir <absolute-dir> --config <absolute-dir>/owner.json --check
#
# The launcher verifies the data-directory ACLs/permissions itself and refuses
# unsafe ones; it never reads .env and never falls back to echo. Keep the data
# directory private. The first run must be without --check so the launcher can
# create owner.token; --check is preflight-only afterwards.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DATA_DIR=""
CONFIG=""
CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir)
      DATA_DIR="$2"
      shift 2
      ;;
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --check)
      CHECK=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${DATA_DIR}" || -z "${CONFIG}" ]]; then
  echo "usage: $0 --data-dir <dir> --config <owner.json> [--check]" >&2
  exit 2
fi

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "data directory does not exist: ${DATA_DIR}" >&2
  exit 1
fi
if [[ ! -f "${CONFIG}" ]]; then
  echo "owner config does not exist: ${CONFIG}" >&2
  exit 1
fi

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -x "${ROOT}/.venv/Scripts/python.exe" ]]; then
  PYTHON="${ROOT}/.venv/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "no Python interpreter found" >&2
  exit 1
fi

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

args=(--config "${CONFIG}" --data-dir "${DATA_DIR}")
if [[ "${CHECK}" -eq 1 ]]; then
  args+=(--check)
fi

echo "starting pilot launcher (data-dir=${DATA_DIR})"
exec "${PYTHON}" -m agency.prototype.pilot_launcher "${args[@]}"
