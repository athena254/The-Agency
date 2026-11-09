#!/usr/bin/env bash
# Deploy the Agency API from this checkout.
#
# Box path: /workspace/deploy_agency.sh
# This is the reference implementation for the Simular box; the box copy must
# match this contract. It was not verified against the box in this pass.
#
# Contract:
#   * refuses to run on a dirty src/ or tests/ tree (exact-revision discipline)
#   * builds the image from the current commit
#   * starts only the API service; the Telegram bot stays behind its compose
#     profile and is never started as an accidental side effect
#   * waits for the application's real health endpoint (GET /v1/health)
#   * never prints secret values
#
# Required environment:
#   AGENCY_LLM_API_KEY       provider key (unless AGENCY_LLM_PROVIDER=ollama)
#   AGENCY_LLM_PROVIDER      default: openai
#   AGENCY_LLM_MODEL         default: gpt-4o-mini
#
# Optional:
#   AGENCY_LLM_BASE_URL      custom OpenAI-compatible endpoint
#   AGENCY_LOG_LEVEL         default: INFO

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

REVISION="$(git rev-parse HEAD)"
echo "agency_deploy_revision=${REVISION}"

if [[ -n "$(git status --porcelain -- src tests)" ]]; then
  echo "refusing to deploy: local edits under src/ or tests/" >&2
  exit 1
fi

PROVIDER="${AGENCY_LLM_PROVIDER:-openai}"
if [[ "${PROVIDER}" != "ollama" && -z "${AGENCY_LLM_API_KEY:-}" ]]; then
  # Compose reads .env for substitution; treat a non-empty key there as present
  # without ever printing the value.
  if [[ ! -f .env ]] || ! grep -qE '^AGENCY_LLM_API_KEY=.+' .env; then
    echo "refusing to deploy: AGENCY_LLM_API_KEY is not set (or use AGENCY_LLM_PROVIDER=ollama)" >&2
    exit 1
  fi
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is not available" >&2
  exit 1
fi

echo "building image from revision ${REVISION}"
docker compose build agency

echo "starting API service"
docker compose up -d agency

echo "waiting for http://127.0.0.1:8000/v1/health"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/v1/health >/dev/null 2>&1; then
    echo "agency_health=ok revision=${REVISION}"
    exit 0
  fi
  sleep 2
done

echo "health check failed; inspect: docker compose logs agency" >&2
exit 1
