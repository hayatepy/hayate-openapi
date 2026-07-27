import pytest
from hayate import Context, Hayate
from jsonschema.exceptions import SchemaError

from hayate_openapi import providers, validated


@pytest.mark.asyncio
async def test_raw_schema_validates_json_and_preserves_valid_data() -> None:
    app = Hayate()
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 20},
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    @app.post("/items", validated("json", schema))
    async def create(c: Context):
        return c.json(c.req.valid("json"))

    valid = await app.request("/items", method="POST", json={"title": "ship"})
    assert valid.status == 200
    assert await valid.json() == {"title": "ship"}

    for body in ({}, {"title": ""}, {"title": "ship", "owner": "other"}):
        invalid = await app.request("/items", method="POST", json=body)
        assert invalid.status == 400
        assert invalid.headers.get("content-type") == "application/problem+json"
        assert (await invalid.json())["title"] == "Validation failed"


@pytest.mark.asyncio
async def test_raw_schema_validates_every_parameter_target_and_uuid_format() -> None:
    app = Hayate()

    @app.get(
        "/items/:id",
        validated(
            "param",
            {
                "type": "object",
                "properties": {"id": {"type": "string", "format": "uuid"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        ),
        validated(
            "query",
            {
                "type": "object",
                "properties": {"page": {"type": "string", "pattern": "^[1-9][0-9]*$"}},
                "required": ["page"],
                "additionalProperties": False,
            },
        ),
        validated(
            "header",
            {
                "type": "object",
                "properties": {"x-mode": {"type": "string", "enum": ["safe"]}},
                "required": ["x-mode"],
            },
        ),
        validated(
            "cookie",
            {
                "type": "object",
                "properties": {"theme": {"type": "string", "enum": ["light", "dark"]}},
                "required": ["theme"],
                "additionalProperties": False,
            },
        ),
    )
    async def show(c: Context):
        return c.json(
            {
                "param": c.req.valid("param"),
                "query": c.req.valid("query"),
                "header": c.req.valid("header")["x-mode"],
                "cookie": c.req.valid("cookie"),
            }
        )

    path = "/items/123e4567-e89b-12d3-a456-426614174000?page=2"
    headers = {"x-mode": "safe", "cookie": "theme=dark"}
    valid = await app.request(path, headers=headers)
    assert valid.status == 200

    invalid_requests = (
        ("/items/not-a-uuid?page=2", headers),
        ("/items/123e4567-e89b-12d3-a456-426614174000?page=zero", headers),
        (path, {"x-mode": "unsafe", "cookie": "theme=dark"}),
        (path, {"x-mode": "safe", "cookie": "theme=contrast"}),
    )
    for invalid_path, invalid_headers in invalid_requests:
        response = await app.request(invalid_path, headers=invalid_headers)
        assert response.status == 400


def test_invalid_raw_schema_fails_when_middleware_is_created() -> None:
    with pytest.raises(SchemaError):
        validated("json", {"type": "not-a-json-schema-type"})


@pytest.mark.asyncio
async def test_pyodide_defers_schema_compilation_until_first_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers.sys, "platform", "emscripten")
    compile_raw_schema = providers._compile_raw_schema
    calls = 0

    def counted_compile(schema):
        nonlocal calls
        calls += 1
        return compile_raw_schema(schema)

    monkeypatch.setattr(providers, "_compile_raw_schema", counted_compile)
    app = Hayate()

    @app.post(
        "/items",
        validated(
            "json",
            {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
        ),
    )
    async def create(c: Context):
        return c.json(c.req.valid("json"))

    assert calls == 0
    first = await app.request("/items", method="POST", json={"title": "first"})
    second = await app.request("/items", method="POST", json={"title": "second"})

    assert first.status == 200
    assert second.status == 200
    assert calls == 1
