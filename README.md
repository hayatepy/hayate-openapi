# hayate-openapi

OpenAPI 3.1 generation for [hayate](https://github.com/hayatepy/hayate) —
built from what your app already knows: routes from `app.routes`, request
schemas from your validators, response schemas from one decorator. No magic
inference, no schema-library lock-in.

> **Status: alpha (0.3.x).** The emitted document passes the official
> `openapi-spec-validator` and feeds `openapi-typescript` for end-to-end
> TypeScript types. The internal design memo (Japanese, per project
> convention) lives in [DESIGN.md](DESIGN.md).
> Interactive Scalar docs, security schemes, multipart uploads, and strict
> inline typing are included. Release history is in
> [CHANGELOG.md](CHANGELOG.md).

```python
from hayate import Hayate
from hayate_openapi import OpenApi, describe, validated
import msgspec

class BookIn(msgspec.Struct):
    title: str

app = Hayate()

@app.post("/books", validated("json", BookIn))   # validator + schema tag in one
@describe(status=201, summary="Create a book")
async def create(c):
    book = c.req.valid("json")     # BookIn instance — validation still runs
    return c.json({"title": book.title}, status=201)

OpenApi(app, title="Bookstore", version="1.0.0").register(app)
# GET /openapi.json and interactive GET /docs are live; or emit statically:
#   python -m hayate_openapi main:app --title Bookstore --version 1.0.0
```

## How it works

| Source | What it provides |
|---|---|
| `app.routes` (hayate ≥ 0.8) | every method + path, converted to OpenAPI templating (`:id` → `{id}`) |
| `validated(target, T)` | request body / query / form schemas — a tagging wrapper around the core `validator`, behavior-identical |
| `@describe(...)` | summary, tags, response schemas, operationId — all optional, all additive |

hayate-auth middleware can supply operation security automatically:

```python
@app.get("/documents", auth.require_oauth_token("documents:read"))
async def documents(c):
    return c.json([])

OpenApi(
    app,
    title="API",
    version="1",
    security_schemes=auth.openapi_security_schemes(),
).register(app)
```

Use `@describe(security=[])` for an explicitly public operation. For uploads,
combine `validated("form", schema, media_type="multipart/form-data")` with
`binary_file()` in a raw schema.

Schema conversion goes through a `SchemaProvider` protocol. msgspec and
pydantic are auto-detected (guarded imports); a plain dict is taken as
literal JSON Schema. **The package itself depends only on hayate.**

TypeScript types, the recommended recipe:

```sh
python -m hayate_openapi main:app --title API --version 1.0.0 -o openapi.json
npx openapi-typescript openapi.json -o src/api-types.ts
```

## Interactive API reference

`register()` serves a [Scalar](https://github.com/scalar/scalar) API reference
at `/docs` by default. It can execute requests, render schemas and security
requirements, and generate client examples directly from the same OpenAPI 3.1
document.

The page has no inline JavaScript. It pins Scalar to an immutable version with
Subresource Integrity and sends a restrictive Content Security Policy. Scalar
is loaded from jsDelivr at browser time, while Scalar's telemetry, external
client, sharing, deployment, MCP-generation, developer-tools, and AI-agent
integrations are disabled by default. The Python package therefore keeps its
single `hayate` dependency without sending the API document to another
service. Disable the page or self-host the script when needed:

```python
# JSON only
OpenApi(app, title="API", version="1", docs_path=None).register(app)

# Same-origin, self-hosted Scalar bundle
OpenApi(
    app,
    title="API",
    version="1",
    scalar_script_url="/assets/scalar.js",
).register(app)
```

The OpenAPI JSON and docs routes are intentionally excluded from the generated
application schema.

## What is documented (and what is not)

- Routes with real HTTP verbs; WebSocket routes and wildcard mounts
  (`/api/auth/*`) are skipped.
- Responses you declare. Undeclared operations get a bare 200 — the
  generator never invents schemas.
- Operations with a validator automatically document the 400
  `application/problem+json` failure the framework actually returns.
- Cookie, Bearer, and OAuth 2.0 security requirements, including combined
  route middleware requirements.
- JSON, URL-encoded, and multipart request bodies, including binary file
  parts.

## License

MIT
