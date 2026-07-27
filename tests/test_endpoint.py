from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, TypedDict
from uuid import UUID, uuid4

import msgspec
import pytest
from hayate import Context, File, FormDataLimits, Hayate
from openapi_spec_validator import validate
from pydantic import BaseModel, Field

from hayate_openapi import (
    Body,
    Constraints,
    Cookie,
    Depends,
    Form,
    Header,
    OpenApi,
    Path,
    Query,
    StdlibProvider,
    endpoint,
)


class ItemIn(TypedDict):
    title: str


class ItemOut(TypedDict):
    id: UUID
    title: str
    limit: int
    request_id: UUID
    theme: str


class PydanticIn(BaseModel):
    title: str = Field(min_length=2)


class PydanticOut(BaseModel):
    title: str
    count: int


class AlternateOut(TypedDict):
    accepted: bool


def _typed_app() -> tuple[Hayate, dict[str, int]]:
    app = Hayate()
    calls = {"request_id": 0}

    def request_id(
        c: Context,
        value: Annotated[UUID, Header(alias="x-request-id")],
    ) -> UUID:
        assert c.req.method == "POST"
        calls["request_id"] += 1
        return value

    @app.post("/items/:item_id")
    @endpoint(
        status=201,
        summary="Create an item",
        providers=[StdlibProvider()],
    )
    async def create_item(
        c: Context,
        item_id: Annotated[UUID, Path(description="Stable item identifier")],
        payload: Annotated[ItemIn, Body(description="New item")],
        first_request_id: Annotated[UUID, Depends(request_id)],
        second_request_id: Annotated[UUID, Depends(request_id)],
        limit: Annotated[int, Query(description="Result limit")] = 10,
        theme: Annotated[str, Cookie()] = "light",
    ) -> ItemOut:
        assert first_request_id == second_request_id
        return {
            "id": item_id,
            "title": payload["title"],
            "limit": limit,
            "request_id": first_request_id,
            "theme": theme,
        }

    return app, calls


async def test_typed_endpoint_binds_validates_caches_and_serializes():
    app, calls = _typed_app()
    item_id = uuid4()
    request_id = uuid4()

    response = await app.request(
        f"/items/{item_id}?limit=3",
        method="POST",
        headers={
            "content-type": "application/json",
            "cookie": "theme=dark",
            "x-request-id": str(request_id),
        },
        body='{"title":"typed"}',
    )

    assert response.status == 201
    assert await response.json() == {
        "id": str(item_id),
        "title": "typed",
        "limit": 3,
        "request_id": str(request_id),
        "theme": "dark",
    }
    assert calls == {"request_id": 1}


@pytest.mark.parametrize(
    ("url", "headers", "body", "detail"),
    [
        (
            "/items/not-a-uuid",
            {"content-type": "application/json", "x-request-id": str(uuid4())},
            '{"title":"typed"}',
            "param.item_id",
        ),
        (
            f"/items/{uuid4()}?limit=nope",
            {"content-type": "application/json", "x-request-id": str(uuid4())},
            '{"title":"typed"}',
            "query.limit",
        ),
        (
            f"/items/{uuid4()}",
            {"content-type": "application/json"},
            '{"title":"typed"}',
            "header.x-request-id",
        ),
        (
            f"/items/{uuid4()}",
            {"content-type": "application/json", "x-request-id": str(uuid4())},
            "not-json",
            "request body is not valid JSON",
        ),
    ],
)
async def test_typed_endpoint_validation_failures_are_problem_details(
    url: str, headers: dict[str, str], body: str, detail: str
):
    app, _ = _typed_app()
    response = await app.request(url, method="POST", headers=headers, body=body)
    problem = await response.json()

    assert response.status == 400
    assert response.headers.get("content-type") == "application/problem+json"
    assert problem["title"] == "Validation failed"
    assert detail in problem["detail"]


def test_typed_endpoint_generates_request_dependency_and_response_contracts():
    app, _ = _typed_app()
    document = OpenApi(app, title="Typed", version="1").generate()
    validate(document)

    operation = document["paths"]["/items/{item_id}"]["post"]
    parameters = {(item["in"], item["name"]): item for item in operation["parameters"]}
    assert parameters[("path", "item_id")]["schema"] == {
        "type": "string",
        "format": "uuid",
    }
    assert parameters[("query", "limit")]["schema"]["type"] == "integer"
    assert parameters[("query", "limit")]["schema"]["default"] == 10
    assert parameters[("query", "limit")]["required"] is False
    assert parameters[("header", "x-request-id")]["schema"]["format"] == "uuid"
    assert parameters[("cookie", "theme")]["required"] is False
    assert parameters[("cookie", "theme")]["schema"]["default"] == "light"
    assert (
        len(
            [
                item
                for item in operation["parameters"]
                if item["in"] == "header" and item["name"] == "x-request-id"
            ]
        )
        == 1
    )
    body = operation["requestBody"]
    assert body["description"] == "New item"
    assert body["content"]["application/json"]["schema"]["required"] == ["title"]
    response_schema = operation["responses"]["201"]["content"]["application/json"]["schema"]
    assert response_schema["properties"]["id"]["format"] == "uuid"
    assert operation["responses"]["400"]["content"]["application/problem+json"]


async def test_typed_form_fields_bind_and_document_one_request_body():
    app = Hayate()

    @app.post("/form")
    @endpoint(providers=[StdlibProvider()])
    async def submit(
        c: Context,
        name: Annotated[str, Form()],
        scores: Annotated[list[int], Form()],
    ) -> dict[str, int | str]:
        assert c.req.method == "POST"
        return {"name": name, "total": sum(scores)}

    response = await app.request(
        "/form",
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        body="name=Hayate&scores=2&scores=3",
    )
    assert await response.json() == {"name": "Hayate", "total": 5}

    document = OpenApi(app, title="Form", version="1").generate()
    schema = document["paths"]["/form"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ]["schema"]
    assert schema["required"] == ["name", "scores"]
    assert schema["properties"]["scores"] == {
        "type": "array",
        "items": {"type": "integer"},
    }


async def test_typed_multipart_file_spools_binds_documents_and_closes():
    app = Hayate()
    captured: list[File] = []
    payload = b"typed upload" * 512
    boundary = "hayate-openapi-test"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            "Quarterly report\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="report.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + payload
        + f"\r\n--{boundary}--\r\n".encode()
    )
    limits = FormDataLimits(
        max_body_bytes=len(body),
        max_file_bytes=len(payload),
        file_memory_bytes=256,
    )

    @app.post("/upload")
    @endpoint
    async def upload(
        title: Annotated[
            str,
            Form(
                media_type="multipart/form-data",
                description="Display title",
                limits=limits,
            ),
        ],
        file: Annotated[File, Form(media_type="multipart/form-data", limits=limits)],
    ) -> dict[str, int | str | bool]:
        captured.append(file)
        chunks = [chunk async for chunk in file.stream(257)]
        return {
            "title": title,
            "name": file.name,
            "type": file.type,
            "size": file.size,
            "spooled": file.spooled,
            "matches": b"".join(chunks) == payload,
        }

    async def chunked() -> AsyncIterator[bytes]:
        for offset in range(0, len(body), 113):
            yield body[offset : offset + 113]

    response = await app.request(
        "/upload",
        method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        body=chunked(),
    )

    assert response.status == 200
    assert await response.json() == {
        "title": "Quarterly report",
        "name": "report.txt",
        "type": "text/plain",
        "size": len(payload),
        "spooled": True,
        "matches": True,
    }
    assert len(captured) == 1
    assert captured[0].closed is True

    document = OpenApi(app, title="Typed upload", version="1").generate()
    operation = document["paths"]["/upload"]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["file"] == {
        "type": "string",
        "format": "binary",
    }
    assert schema["properties"]["title"] == {
        "type": "string",
        "description": "Display title",
    }
    assert operation["responses"]["413"]["description"] == "Payload Too Large"
    validate(document)


async def test_typed_multipart_file_limit_returns_413_without_calling_handler():
    app = Hayate()
    called = False
    boundary = "bounded"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="large.bin"\r\n\r\n'
        "too-large\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    limits = FormDataLimits(
        max_body_bytes=len(body),
        max_file_bytes=4,
        file_memory_bytes=2,
    )

    @app.post("/bounded")
    @endpoint
    async def bounded(
        file: Annotated[File, Form(media_type="multipart/form-data", limits=limits)],
    ) -> dict[str, str]:
        nonlocal called
        called = True
        return {"name": file.name}

    response = await app.request(
        "/bounded",
        method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        body=body,
    )
    problem = await response.json()

    assert response.status == 413
    assert problem["title"] == "Payload Too Large"
    assert "max_file_bytes" in problem["detail"]
    assert called is False


async def test_pydantic_and_msgspec_constraints_work_for_scalar_parameters():
    app = Hayate()

    @app.post("/pydantic")
    @endpoint
    async def pydantic_endpoint(
        body: Annotated[PydanticIn, Body()],
        count: Annotated[int, Field(gt=0), Query()],
    ) -> PydanticOut:
        return PydanticOut(title=body.title, count=count)

    @app.get("/msgspec")
    @endpoint
    async def msgspec_endpoint(
        count: Annotated[int, msgspec.Meta(ge=1), Query()],
    ) -> dict[str, int]:
        return {"count": count}

    invalid_pydantic = await app.request(
        "/pydantic?count=0",
        method="POST",
        headers={"content-type": "application/json"},
        body='{"title":"ok"}',
    )
    invalid_msgspec = await app.request("/msgspec?count=0")
    assert invalid_pydantic.status == 400
    assert invalid_msgspec.status == 400

    document = OpenApi(app, title="Constraints", version="1").generate()
    pydantic_parameter = document["paths"]["/pydantic"]["post"]["parameters"][0]
    msgspec_parameter = document["paths"]["/msgspec"]["get"]["parameters"][0]
    assert pydantic_parameter["schema"]["exclusiveMinimum"] == 0
    assert msgspec_parameter["schema"]["minimum"] == 1


async def test_stdlib_constraints_are_portable_and_documented():
    app = Hayate()

    @app.get("/search")
    @endpoint
    async def search(
        limit: Annotated[int, Constraints(ge=1, le=100), Query()] = 25,
        cursor: Annotated[
            str | None,
            Constraints(min_length=3, max_length=12, pattern=r"^[a-z0-9]+$"),
            Query(),
        ] = None,
    ) -> dict[str, int | str | None]:
        return {"limit": limit, "cursor": cursor}

    valid = await app.request("/search?limit=100&cursor=abc123")
    assert valid.status == 200
    assert await valid.json() == {"limit": 100, "cursor": "abc123"}

    for path in (
        "/search?limit=0",
        "/search?limit=101",
        "/search?limit=many",
        "/search?cursor=ab",
        "/search?cursor=not-valid!",
    ):
        response = await app.request(path)
        assert response.status == 400
        assert response.headers.get("content-type") == "application/problem+json"

    document = OpenApi(app, title="Portable constraints", version="1").generate()
    parameters = {
        parameter["name"]: parameter
        for parameter in document["paths"]["/search"]["get"]["parameters"]
    }
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 25,
    }
    cursor_schema = parameters["cursor"]["schema"]
    assert cursor_schema["minLength"] == 3
    assert cursor_schema["maxLength"] == 12
    assert cursor_schema["pattern"] == "^[a-z0-9]+$"


async def test_declared_response_is_validated_and_failures_do_not_leak():
    app = Hayate()

    @app.get("/broken")
    @endpoint(providers=[StdlibProvider()])
    async def broken() -> ItemOut:
        return {"id": "not-a-uuid"}  # type: ignore[return-value]

    response = await app.request("/broken")
    problem = await response.json()
    assert response.status == 500
    assert "uuid" not in (problem.get("detail") or "").lower()


async def test_subdependencies_share_request_cache_and_can_opt_out():
    app = Hayate()
    calls = {"leaf": 0, "branch": 0}

    def leaf(value: Annotated[int, Query()]) -> int:
        calls["leaf"] += 1
        return value

    async def branch(value: Annotated[int, Depends(leaf)]) -> int:
        calls["branch"] += 1
        return value * 2

    @app.get("/cached")
    @endpoint(providers=[StdlibProvider()])
    async def cached(
        first: Annotated[int, Depends(branch)],
        second: Annotated[int, Depends(branch)],
        fresh: Annotated[int, Depends(branch, use_cache=False)],
    ) -> dict[str, int]:
        return {"first": first, "second": second, "fresh": fresh}

    response = await app.request("/cached?value=3")
    assert await response.json() == {"first": 6, "second": 6, "fresh": 6}
    assert calls == {"leaf": 1, "branch": 2}


def test_ambiguous_typed_signatures_fail_at_registration():
    with pytest.raises(TypeError, match="explicit"):

        @endpoint
        async def unmarked(value: int):
            return value

    with pytest.raises(TypeError, match="only one JSON body"):

        @endpoint
        async def duplicate_body(
            first: Annotated[ItemIn, Body()],
            second: Annotated[ItemIn, Body()],
        ):
            return first, second

    def first(value: Annotated[str, Depends(lambda: "x")]) -> str:
        return value

    marker = Depends(first)
    first.__annotations__["value"] = Annotated[str, marker]
    with pytest.raises(TypeError, match="dependency cycle"):
        endpoint(first)

    first_limits = FormDataLimits(max_body_bytes=10)
    second_limits = FormDataLimits(max_body_bytes=20)
    with pytest.raises(TypeError, match="same FormDataLimits"):

        @endpoint
        async def conflicting_form_limits(
            first: Annotated[str, Form(limits=first_limits)],
            second: Annotated[str, Form(limits=second_limits)],
        ):
            return first, second

    with pytest.raises(TypeError, match="multipart/form-data"):

        @endpoint
        async def urlencoded_file(file: Annotated[File, Form()]):
            return file.name


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[str, Constraints(ge=1), Query()],
        Annotated[int, Constraints(min_length=1), Query()],
        Annotated[int, Constraints(gt=10, le=10), Query()],
        Annotated[str, Constraints(min_length=2, max_length=1), Query()],
        Annotated[str, Constraints(pattern="["), Query()],
    ],
)
def test_invalid_stdlib_constraints_fail_at_registration(annotation):
    async def invalid(value):
        return value

    invalid.__annotations__ = {"value": annotation}
    with pytest.raises(TypeError, match=r"Constraints|constraint|bound|pattern|length"):
        endpoint(invalid, providers=[StdlibProvider()])


@pytest.mark.parametrize(
    "annotation",
    [
        Annotated[ItemIn, Body("text/plain")],
        Annotated[str, Form(media_type="application/json")],
        Annotated[list[str], Header()],
    ],
)
def test_unsupported_typed_transport_contracts_fail_at_registration(annotation):
    async def invalid(value):
        return value

    invalid.__annotations__ = {"value": annotation}
    with pytest.raises(TypeError, match=r"unsupported media type|must be scalar"):
        endpoint(invalid, providers=[StdlibProvider()])


def test_explicit_response_documentation_uses_its_own_provider():
    app = Hayate()

    @app.get("/override")
    @endpoint(
        responses={200: AlternateOut},
        providers=[StdlibProvider()],
    )
    async def override() -> ItemIn:
        return {"title": "runtime contract"}

    document = OpenApi(
        app,
        title="Override",
        version="1",
        providers=[StdlibProvider()],
    ).generate()
    schema = document["paths"]["/override"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert set(schema["properties"]) == {"accepted"}
