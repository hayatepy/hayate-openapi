#!/usr/bin/env bash
set -euo pipefail

wheel="${1:-}"
test_dir="$(mktemp -d)"
log_file="${test_dir}.workerd.log"
dry_run_log="${test_dir}.dry-run.log"
bundle_dir="${test_dir}/bundle"
port="${WORKERD_PORT:-8797}"
server_pid=""

if [[ ! -f "${wheel}" || "${wheel}" != *.whl ]]; then
  echo "usage: $0 PATH_TO_HAYATE_OPENAPI_WHEEL" >&2
  exit 2
fi
wheel="$(cd "$(dirname "${wheel}")" && pwd)/$(basename "${wheel}")"

terminate_tree() {
  local parent_pid="$1"
  local child_pid
  while read -r child_pid; do
    if [[ -n "${child_pid}" ]]; then
      terminate_tree "${child_pid}"
    fi
  done < <(pgrep -P "${parent_pid}" 2>/dev/null || true)
  kill "${parent_pid}" 2>/dev/null || true
}

cleanup() {
  local status=$?
  if [[ "${status}" -ne 0 ]]; then
    cat "${dry_run_log}" 2>/dev/null || true
    cat "${log_file}" 2>/dev/null || true
  fi
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    terminate_tree "${server_pid}"
    wait "${server_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ "$(node --version)" != v24.* ]]; then
  echo "the workerd contract requires Node.js 24" >&2
  exit 2
fi

(
  cd "${test_dir}"
  uvx --from create-hayate==0.5.0 create-hayate \
    demo-app --template workers --with openapi --no-input
  cd demo-app
  uv sync
  uv pip install \
    --python .venv/bin/python \
    --reinstall-package hayate-openapi \
    --no-deps \
    "${wheel}"
  uv run --no-sync pytest -q

  # Pywrangler resolves the public dependency graph first. Replace only this
  # candidate package in the portable bundle so PRs test their unpublished
  # wheel against the exact platform environment users receive.
  uv run --no-sync python manage_workers.py sync
  uv pip install \
    --target python_modules \
    --reinstall \
    --no-deps \
    "${wheel}"

  uv run --no-sync python manage_workers.py deploy \
    --dry-run \
    --outdir "${bundle_dir}" >"${dry_run_log}" 2>&1
  uv run --no-sync python manage_workers.py dev --port "${port}"
) >"${log_file}" 2>&1 &
server_pid=$!

ready=false
for _ in {1..60}; do
  if curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    exit 1
  fi
  sleep 1
done
if [[ "${ready}" != true ]]; then
  exit 1
fi

created="$(
  curl --fail --silent --show-error --max-time 10 \
    -X POST "http://127.0.0.1:${port}/todos" \
    -H "content-type: application/json" \
    --data '{"title":"workerd schema contract"}'
)"
todo_id="$(
  uv run python -c \
    'import json,sys; value=json.loads(sys.argv[1]); assert value["title"] == "workerd schema contract"; print(value["id"])' \
    "${created}"
)"

invalid_status="$(
  curl --silent --show-error --output /dev/null --write-out "%{http_code}" --max-time 10 \
    "http://127.0.0.1:${port}/todos/not-a-uuid"
)"
if [[ "${invalid_status}" != "400" ]]; then
  echo "expected malformed UUID to return 400; got ${invalid_status}" >&2
  exit 1
fi

curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:${port}/todos/${todo_id}" >/dev/null
openapi="$(
  curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${port}/openapi.json"
)"
uv run python -c \
  'import json,sys; document=json.loads(sys.argv[1]); parameter=document["paths"]["/todos/{id}"]["get"]["parameters"][0]; assert parameter["schema"] == {"type":"string","format":"uuid"}' \
  "${openapi}"

upload="$(grep -F "Total Upload:" "${dry_run_log}" | tail -1)"
if [[ -z "${upload}" ]]; then
  echo "Wrangler dry-run did not report upload size" >&2
  exit 1
fi
echo "workerd raw-schema contract passed: ${upload}"
