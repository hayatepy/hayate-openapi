# hayate-openapi

> **Hayate ecosystem:** [Start here](https://github.com/hayatepy/.github/blob/main/docs/START.md)
> · [Production golden app](https://github.com/hayatepy/golden-app)
> · [Tested compatibility](https://github.com/hayatepy/.github/blob/main/docs/COMPATIBILITY.md)

OpenAPI 3.1 generation for [hayate](https://github.com/hayatepy/hayate) —
built from what your app already knows: routes from `app.routes`, request
schemas from validators or explicit typed parameters, and response schemas
from one decorator. No implicit body/query guessing and no schema-library
lock-in.

> **Status: alpha (0.8.x).** The emitted OpenAPI 3.1.1 document passes
> `openapi-spec-validator` and feeds `openapi-typescript` 7.13 for end-to-end
> TypeScript types in a locked CI interoperability gate. The internal design
> memo (Japanese, per project convention) lives in [DESIGN.md](DESIGN.md).
> Interactive Scalar docs, security schemes, multipart uploads, and strict
> inline typing are included. Release history is in
> [CHANGELOG.md](CHANGELOG.md).

The typed endpoint surface removes repeated validation and response glue while
keeping every request source explicit:

```python
from typing import Annotated, TypedDict
from uuid import UUID

from hayate import Hayate
from hayate_openapi import Body, Constraints, OpenApi, Path, Query, endpoint


class BookIn(TypedDict):
    title: str


class BookOut(TypedDict):
    id: UUID
    title: str


app = Hayate()


@app.post("/books/:book_id")
@endpoint(status=201, summary="Create a book")
async def create(
    book_id: Annotated[UUID, Path()],
    book: Annotated[BookIn, Body()],
    notify: Annotated[bool, Query()] = False,
    limit: Annotated[int, Constraints(ge=1, le=100), Query()] = 25,
) -> BookOut:
    if notify:
        ...
    return {"id": book_id, "title": book["title"]}


OpenApi(app, title="Bookstore", version="1.0.0").register(app)
```

`@endpoint` validates and converts every marked input, validates the declared
return type, emits JSON, and attaches the exact same schemas to OpenAPI.
Invalid inputs use Hayate's RFC 9457 `400` response; response contract failures
remain non-leaking `500` errors. Put the decorator closest to the function,
below the Hayate route decorator.

`Constraints` supplies portable numeric bounds (`gt`, `ge`, `lt`, `le`) and
string rules (`min_length`, `max_length`, `pattern`) without Pydantic or
msgspec. The same metadata validates converted HTTP values and emits JSON
Schema keywords, including on the Cloudflare Workers/Pyodide path.

Dependencies can have request parameters and sub-dependencies. A dependency
runs once per request even when several consumers use it:

```python
from hayate import Context
from hayate_openapi import Depends, Header


def request_id(
    c: Context,
    value: Annotated[UUID, Header(alias="x-request-id")],
) -> UUID:
    return value


@app.get("/jobs/:job_id")
@endpoint
async def job(
    job_id: Annotated[UUID, Path()],
    trace: Annotated[UUID, Depends(request_id)],
) -> dict[str, str]:
    return {"job_id": str(job_id), "trace": str(trace)}
```

Use `Depends(callable, use_cache=False)` only when a fresh result is required.
Synchronous dependencies run in a worker thread on native Python and inline
on threadless Pyodide; async dependencies work on both.

The original Context-first surface remains fully supported:

```python
from hayate import Hayate
from hayate_openapi import OpenApi, describe, validated
import msgspec


class BookIn(msgspec.Struct):
    title: str


app = Hayate()


@app.post("/books", validated("json", BookIn))  # validator + schema tag in one
@describe(status=201, summary="Create a book")
async def create(c):
    book = c.req.valid("json")  # BookIn instance — validation still runs
    return c.json({"title": book.title}, status=201)


OpenApi(app, title="Bookstore", version="1.0.0").register(app)
# GET /openapi.json and interactive GET /docs are live; or emit statically:
#   python -m hayate_openapi main:app --title Bookstore --version 1.0.0
```

## How it works

| Source | What it provides |
|---|---|
| `app.routes` (hayate ≥ 0.8) | every method + path, converted to OpenAPI templating (`:id` → `{id}`) |
| `@endpoint` + `Annotated` markers | typed binding, dependencies, response validation, JSON serialization, and matching schemas |
| `validated(target, T)` | request body plus query / path / header / cookie schemas — a tagging wrapper around the core `validator`, behavior-identical |
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
use Hayate's own `File` type in a typed multipart field:

```python
from hashlib import sha256
from typing import Annotated

from hayate import File, FormDataLimits
from hayate_openapi import Form, endpoint

UPLOAD_LIMITS = FormDataLimits(
    max_body_bytes=10 * 1024 * 1024,
    max_file_bytes=10 * 1024 * 1024,
    file_memory_bytes=1024 * 1024,
)


@app.post("/uploads")
@endpoint(status=201)
async def upload(
    file: Annotated[
        File,
        Form(media_type="multipart/form-data", limits=UPLOAD_LIMITS),
    ],
) -> dict[str, str | int]:
    digest = sha256()
    async for chunk in file.stream():
        digest.update(chunk)
    return {"name": file.name, "size": file.size, "sha256": digest.hexdigest()}
```

The same declaration binds the runtime `File`, emits an OpenAPI
`string`/`binary` part, applies parser limits, and documents both `400` and
`413` failures. On native streamed requests, files above
`file_memory_bytes` spill to an unnamed temporary file; Workers remain
disk-free and bounded. `@endpoint` closes parsed files after the handler
returns or raises, so consume or persist them during the handler call.
All typed fields in one form, including fields declared by dependencies, must
share one media type and one `FormDataLimits` value.

The Context-first surface remains available: combine
`validated("form", schema, media_type="multipart/form-data")` with
`binary_file()` in a raw schema.

Schema conversion goes through a `SchemaProvider` protocol. The built-in
`StdlibProvider` covers primitives, UUID/date/time, enums, literals, unions,
collections, mappings, and `TypedDict` without another dependency. msgspec
and pydantic are auto-detected through guarded imports; install them with
`hayate-openapi[msgspec]` or `hayate-openapi[pydantic]` when their models and
constraints are desired. A plain dict is taken as literal JSON Schema Draft
2020-12 and compiled once for runtime validation, including supported
`format` checks such as UUID. CPython checks raw schemas when routes register.
Pyodide compiles them on the first matching request and caches the validator
because Workers forbids extension entropy during module-global evaluation.
Raw schemas validate the literal request representation, so query, path,
header, and cookie values are strings. Use `@endpoint` with typed parameters
when the wire value should be converted to an integer, number, or boolean.

`validated()` supports the core's six targets: `json`, `form`, `query`,
`param`, `header`, and `cookie`. The last four expand object properties into
OpenAPI parameters. A `param` property must match a route parameter and
overrides its generated schema while remaining required as OpenAPI mandates.
Header schemas use the lowercase names exposed by Fetch; model aliases such as
`x-request-id` keep a Python field name separate when needed. OpenAPI-reserved
`Accept`, `Content-Type`, and `Authorization` header parameters are rejected
instead of emitting fields the specification ignores.

Generate TypeScript types and the first-party callable client:

```sh
python -m hayate_openapi main:app \
  --title API \
  --version 1.0.0 \
  --output openapi.json \
  --typescript-client src/api-client.ts \
  --typescript-types-import ./api-types.js
npx openapi-typescript openapi.json -o src/api-types.ts
```

The generated client imports only `openapi-typescript`'s `paths` type. Its
runtime has no package dependency: it uses the platform Fetch, URL, Headers,
URLSearchParams, FormData, and Blob APIs. Every documented operation becomes a
method keyed by its `operationId`:

```ts
import { createHayateClient } from "./api-client.js";

const api = createHayateClient({ baseUrl: "https://api.example.com/v1" });
const response = await api.post_typed_books_book_id({
  path: { book_id: "018f47a6-42d2-7f5a-a724-35d7d230ad42" },
  query: { notify: true },
  json: { title: "Typed contracts" },
});

if (response.status === 201) {
  const book = await response.json(); // status-discriminated response type
}
```

Path, query, header, cookie, JSON, URL-encoded, and multipart values are typed
from the emitted OpenAPI document. Generation fails when a document asks for a
serialization the runtime does not implement, including ambiguous request
media types, object-valued parameters, custom form encodings, nested form
values, or non-JSON response bodies.

Cookie parameters are for server-side Fetch runtimes. Browsers control the
`Cookie` header themselves; use `credentials` for browser-managed cookies.
Cookie arrays are rejected because
[OpenAPI 3.1 Appendix D](https://spec.openapis.org/oas/v3.1.1.html#appendix-d-serializing-headers-and-cookies)
notes that form-style multi-value serialization does not match the
semicolon-delimited Cookie header.

The repository continuously validates, generates, strictly compiles, and runs
the same flow against a representative application:

```sh
npm ci --ignore-scripts
scripts/check_interoperability.sh
```

That gate starts a real Hayate ASGI process and executes path/query/JSON,
multipart, and form/header/cookie round trips with the compiled generated
client.

The generator deliberately emits OpenAPI 3.1.1. Although
[OpenAPI 3.2.0](https://spec.openapis.org/oas/v3.2.0.html) is published,
[`openapi-typescript` documents support](https://openapi-ts.dev/introduction)
for 3.0 and 3.1. Staying on 3.1.1 keeps validation, documentation, and typed
client generation on one tested interoperability profile instead of claiming
an unverified version bump. The private Node lock overrides vulnerable
transitive parser/glob versions with patched releases; it is CI tooling and is
not part of the Python distribution.

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
  `application/problem+json` failure the framework actually returns; form
  operations also document parser-limit failures as 413.
- Cookie, Bearer, and OAuth 2.0 security requirements, including combined
  route middleware requirements.
- JSON, URL-encoded, and multipart request bodies, including typed binary
  `File` parts with shared resource limits.
- Explicit typed endpoint parameters, including request fields declared by
  cached sub-dependencies, and validated return annotations.

## License

MIT
