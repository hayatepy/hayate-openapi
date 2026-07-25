#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

document="$tmp_dir/openapi.json"
types="$tmp_dir/api-types.ts"

uv run python -m hayate_openapi examples.conformance_app:app \
  --title "Interoperability fixture" \
  --version "1.0.0" \
  --description "Representative hayate-openapi document" \
  --output "$document"

uv run python - "$document" <<'PY'
import json
import sys
from pathlib import Path

from openapi_spec_validator import validate

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
validate(document)
if document.get("openapi") != "3.1.1":
    raise SystemExit(f"unexpected OpenAPI version: {document.get('openapi')!r}")

expected_paths = {"/books", "/books/{book_id}", "/search", "/covers"}
missing_paths = expected_paths.difference(document.get("paths", {}))
if missing_paths:
    raise SystemExit(f"missing representative paths: {sorted(missing_paths)}")

print("OpenAPI 3.1.1 schema and semantic validation: PASS")
PY

if [[ ! -x node_modules/.bin/openapi-typescript ]]; then
  echo "openapi-typescript is not installed; run npm ci --ignore-scripts" >&2
  exit 1
fi

node_modules/.bin/openapi-typescript "$document" --output "$types"
grep -q '"/books"' "$types"
grep -q 'post_books' "$types"
test -s "$types"

echo "openapi-typescript 7.13.0 generation: PASS"
