#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

tmp_dir="$(mktemp -d)"
server_pid=""
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

document="$tmp_dir/openapi.json"
types="$tmp_dir/api-types.ts"
client="$tmp_dir/api-client.ts"
usage="$tmp_dir/typescript_client_usage.ts"
compiled="$tmp_dir/compiled"

uv run python -m hayate_openapi examples.conformance_app:app \
  --title "Interoperability fixture" \
  --version "1.0.0" \
  --description "Representative hayate-openapi document" \
  --output "$document" \
  --typescript-client "$client" \
  --typescript-types-import "./api-types.js"

uv run python - "$document" <<'PY'
import json
import sys
from pathlib import Path

from openapi_spec_validator import validate

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
validate(document)
if document.get("openapi") != "3.1.1":
    raise SystemExit(f"unexpected OpenAPI version: {document.get('openapi')!r}")

expected_paths = {
    "/books",
    "/books/{book_id}",
    "/search",
    "/covers",
    "/typed-covers",
    "/typed-books/{book_id}",
    "/sessions",
}
missing_paths = expected_paths.difference(document.get("paths", {}))
if missing_paths:
    raise SystemExit(f"missing representative paths: {sorted(missing_paths)}")

typed = document["paths"]["/typed-books/{book_id}"]["post"]
if typed["requestBody"]["content"]["application/json"]["schema"] == {}:
    raise SystemExit("typed request body schema is empty")
if typed["responses"]["201"]["content"]["application/json"]["schema"] == {}:
    raise SystemExit("typed response schema is empty")
parameter_locations = {(item["name"], item["in"]) for item in typed["parameters"]}
if parameter_locations != {("book_id", "path"), ("notify", "query")}:
    raise SystemExit(f"unexpected typed parameters: {sorted(parameter_locations)}")

print("OpenAPI 3.1.1 schema and semantic validation: PASS")
PY

if [[ ! -x node_modules/.bin/openapi-typescript ]]; then
  echo "openapi-typescript is not installed; run npm ci --ignore-scripts" >&2
  exit 1
fi

node_modules/.bin/openapi-typescript "$document" --output "$types"
grep -q '"/books"' "$types"
grep -q 'post_books' "$types"
grep -q '"/typed-books/{book_id}"' "$types"
grep -q 'post_typed_books_book_id' "$types"
test -s "$types"

echo "openapi-typescript 7.13.0 generation: PASS"

if [[ ! -x node_modules/.bin/tsc ]]; then
  echo "TypeScript is not installed; run npm ci --ignore-scripts" >&2
  exit 1
fi

cp scripts/typescript_client_usage.ts "$usage"
node_modules/.bin/tsc \
  --strict \
  --exactOptionalPropertyTypes \
  --noUncheckedIndexedAccess \
  --module NodeNext \
  --moduleResolution NodeNext \
  --target ES2022 \
  --lib ES2022,DOM,DOM.Iterable \
  --outDir "$compiled" \
  "$types" "$client" "$usage"

echo "TypeScript 5.9.3 strict client compilation: PASS"

port="$(
  uv run python -c \
    'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()'
)"
uv run uvicorn examples.conformance_app:app \
  --host 127.0.0.1 \
  --port "$port" \
  >"$tmp_dir/uvicorn.log" 2>&1 &
server_pid="$!"
for _ in {1..50}; do
  if curl --silent --output /dev/null "http://127.0.0.1:$port/sessions"; then
    break
  fi
  if ! kill -0 "$server_pid" >/dev/null 2>&1; then
    cat "$tmp_dir/uvicorn.log" >&2
    exit 1
  fi
  sleep 0.1
done
node scripts/check_typescript_client.cjs \
  "$compiled/api-client.js" \
  "http://127.0.0.1:$port"
