"""The generator: paths, operations, schemas, and the exclusion rules."""

from types import SimpleNamespace

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
    # The document documents itself last (registered after the fixture routes).
    assert "/openapi.json" in doc["paths"]


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
