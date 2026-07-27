"""The generator: paths, operations, schemas, and the exclusion rules."""

from types import SimpleNamespace
from typing import Any

import msgspec
import pytest
from hayate import Context, Hayate

from conftest import build_app, generate
from hayate_openapi import OpenApi, describe, validated


def test_document_skeleton():
    doc = generate()
    assert doc["openapi"] == "3.1.1"
    assert doc["info"] == {"title": "Test API", "version": "0.0.1", "description": "fixture"}


def test_paths_convert_and_exclude():
    paths = generate()["paths"]
    assert "/books" in paths and "/books/{id}" in paths
    assert "/plain/{kind}" in paths
    assert not any("*" in p for p in paths)
    assert "/ws" not in paths


def test_msgspec_request_body_and_described_response():
    op = generate()["paths"]["/books"]["post"]
    assert op["summary"] == "Create a book"
    assert op["tags"] == ["books"]
    body = op["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"] == "#/components/schemas/BookIn"
    assert op["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BookOut"
    }
    assert op["responses"]["400"]["content"]["application/problem+json"]


def test_components_collect_all_defs():
    schemas = generate()["components"]["schemas"]
    assert {"BookIn", "BookOut", "UserIn"} <= set(schemas)
    assert schemas["BookIn"]["required"] == ["title"]


def test_path_parameters_with_pattern():
    op = generate()["paths"]["/plain/{kind}"]["get"]
    (param,) = op["parameters"]
    assert param == {
        "name": "kind",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "pattern": "[a-z]+"},
    }


def test_query_object_expands_to_parameters():
    op = generate()["paths"]["/search"]["get"]
    params = {p["name"]: p for p in op["parameters"]}
    assert params["q"]["required"] is True
    assert params["limit"]["required"] is False
    assert params["limit"]["schema"]["type"] == "integer"


def test_validated_path_header_and_cookie_expand_to_parameters():
    op = generate()["paths"]["/validated/{id}"]["get"]
    params = {(parameter["in"], parameter["name"]): parameter for parameter in op["parameters"]}
    assert params[("path", "id")] == {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "format": "uuid"},
    }
    assert params[("header", "x-request-id")]["required"] is True
    assert params[("cookie", "theme")]["required"] is False
    assert params[("cookie", "theme")]["schema"]["enum"] == ["light", "dark"]


def test_pydantic_request_body():
    op = generate()["paths"]["/users"]["post"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/UserIn"}


def test_raw_dict_schema_passes_through():
    op = generate()["paths"]["/raw"]["post"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert schema == {"type": "object", "required": ["x"]}


def test_multipart_binary_file_request_body():
    op = generate()["paths"]["/uploads"]["post"]
    content = op["requestBody"]["content"]
    assert "application/x-www-form-urlencoded" not in content
    schema = content["multipart/form-data"]["schema"]
    assert schema["properties"]["file"] == {
        "type": "string",
        "format": "binary",
        "description": "PDF or image",
    }


def test_undescribed_route_gets_default_response_only():
    op = generate()["paths"]["/books/{id}"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]
    assert op["responses"]["404"] == {"description": "Not Found"}


def test_operation_ids_are_deterministic():
    doc = generate()
    ids = [op["operationId"] for methods in doc["paths"].values() for op in methods.values()]
    assert len(ids) == len(set(ids))
    assert "post_books" in ids


async def test_validated_still_validates():
    """The tag must not change validator behavior (DESIGN §3.1)."""
    app = build_app()
    ok = await app.request("/books", method="POST", json={"title": "t"})
    assert ok.status == 201
    bad = await app.request("/books", method="POST", json={"pages": "x"})
    assert bad.status == 400


async def test_mounted_endpoint_serves_the_document():
    app = build_app()
    OpenApi(app, title="Mounted", version="1").register(app)
    res = await app.request("/openapi.json")
    assert res.status == 200
    doc = await res.json()
    assert doc["info"]["title"] == "Mounted"
    assert "/openapi.json" not in doc["paths"]
    assert "/docs" not in doc["paths"]


async def test_mounted_endpoint_serves_hardened_interactive_docs():
    app = build_app()
    OpenApi(app, title='Mounted <script>alert("xss")</script>', version="1").register(app)

    res = await app.request("/docs")
    body = await res.text()

    assert res.status == 200
    assert res.headers.get("content-type") == "text/html;charset=utf-8"
    assert res.headers.get("cache-control") == "no-store"
    assert res.headers.get("referrer-policy") == "no-referrer"
    assert res.headers.get("x-content-type-options") == "nosniff"
    csp = res.headers.get("content-security-policy")
    assert csp is not None
    assert "default-src 'none'" in csp
    assert "script-src https://cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp

    assert body.count("<script") == 1
    assert 'id="api-reference"' in body
    assert "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.63.0" in body
    assert 'integrity="sha384-' in body
    assert 'crossorigin="anonymous"' in body
    assert "&quot;url&quot;:&quot;/openapi.json&quot;" in body
    assert "&quot;withDefaultFonts&quot;:false" in body
    assert "&quot;showDeveloperTools&quot;:&quot;never&quot;" in body
    assert "&quot;hideClientButton&quot;:true" in body
    assert "&quot;agent&quot;:{&quot;disabled&quot;:true}" in body
    assert "&quot;mcp&quot;:{&quot;disabled&quot;:true}" in body
    assert "&quot;telemetry&quot;:false" in body
    assert '<script>alert("xss")</script>' not in body
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in body
    assert "<noscript>" in body


async def test_interactive_docs_can_be_disabled():
    app = build_app()
    OpenApi(app, title="Mounted", version="1", docs_path=None).register(app)

    assert (await app.request("/openapi.json")).status == 200
    assert (await app.request("/docs")).status == 404


async def test_interactive_docs_support_same_origin_self_hosting():
    app = build_app()
    OpenApi(
        app,
        title="Mounted",
        version="1",
        scalar_script_url="/assets/scalar.js",
    ).register(app)

    res = await app.request("/docs")
    body = await res.text()
    assert 'src="/assets/scalar.js"' in body
    assert "integrity=" not in body
    assert "script-src 'self'" in (res.headers.get("content-security-policy") or "")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"docs_path": "docs"},
        {"docs_path": "/openapi.json"},
        {"scalar_script_url": "//cdn.example.com/scalar.js"},
        {"scalar_script_url": "http://cdn.example.com/scalar.js"},
        {"scalar_script_url": "https://user@cdn.example.com/scalar.js"},
        {"scalar_script_url": "javascript:alert(1)"},
        {"scalar_script_url": "https://cdn.example.com/\nscalar.js"},
    ],
)
def test_interactive_docs_reject_unsafe_configuration(kwargs: dict[str, Any]) -> None:
    app = build_app()
    with pytest.raises(ValueError):
        OpenApi(app, title="Mounted", version="1", **kwargs)


def test_duplicate_method_and_path_is_rejected_instead_of_silently_overwritten():
    async def handler(c: Context):
        return c.text("response")

    route = SimpleNamespace(method="GET", pattern="/duplicate", handler=handler, middleware=())
    app = SimpleNamespace(routes=[route, route])
    with pytest.raises(ValueError, match="duplicate OpenAPI operation"):
        OpenApi(app, title="Collisions", version="1").generate()


def test_equivalent_templated_paths_are_rejected_as_openapi_requires():
    app = Hayate()

    @app.get("/items/:id")
    async def by_id(c: Context):
        return c.text("id")

    @app.post("/items/:name")
    async def by_name(c: Context):
        return c.text("name")

    with pytest.raises(ValueError, match="cannot distinguish templated paths"):
        OpenApi(app, title="Collisions", version="1").generate()


def test_duplicate_operation_ids_are_rejected():
    app = Hayate()

    @app.get("/first")
    @describe(operation_id="same")
    async def first(c: Context):
        return c.text("first")

    @app.get("/second")
    @describe(operation_id="same")
    async def second(c: Context):
        return c.text("second")

    with pytest.raises(ValueError, match="duplicate OpenAPI operationId"):
        OpenApi(app, title="Collisions", version="1").generate()


def test_conflicting_component_names_are_rejected():
    first_payload = msgspec.defstruct("Payload", [("first", str)])
    second_payload = msgspec.defstruct("Payload", [("second", int)])
    app = Hayate()

    @app.post("/first", validated("json", first_payload))
    async def first(c: Context):
        return c.text("first")

    @app.post("/second", validated("json", second_payload))
    async def second(c: Context):
        return c.text("second")

    with pytest.raises(ValueError, match="conflicting OpenAPI component schema"):
        OpenApi(app, title="Collisions", version="1").generate()


def test_path_validator_rejects_properties_not_present_in_route():
    app = Hayate()

    @app.get(
        "/books/:id",
        validated(
            "param",
            {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
            },
        ),
    )
    async def book(c: Context):
        return c.json({})

    with pytest.raises(ValueError, match="not route parameters: slug"):
        OpenApi(app, title="Invalid path contract", version="1").generate()


@pytest.mark.parametrize("name", ["Accept", "authorization", "content-type"])
def test_header_validator_rejects_openapi_ignored_parameters(name: str):
    app = Hayate()

    @app.get(
        "/books",
        validated(
            "header",
            {
                "type": "object",
                "properties": {name: {"type": "string"}},
            },
        ),
    )
    async def books(c: Context):
        return c.json([])

    with pytest.raises(ValueError, match="OpenAPI ignores"):
        OpenApi(app, title="Invalid header contract", version="1").generate()
