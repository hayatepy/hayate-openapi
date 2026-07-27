"""Candidate-wheel probe for typed endpoints and raw schemas on Workerd."""

from typing import Annotated, TypedDict
from uuid import UUID

from hayate import Context, File, FormDataLimits, Hayate, Response

from hayate_openapi import (
    Body,
    Constraints,
    Form,
    OpenApi,
    Path,
    Query,
    StdlibProvider,
    endpoint,
    validated,
)


class EchoIn(TypedDict):
    value: str


class EchoOut(TypedDict):
    item_id: UUID
    value: str
    repeat: int


class UploadOut(TypedDict):
    name: str
    media_type: str
    size: int
    spooled: bool
    content: str


UPLOAD_LIMITS = FormDataLimits(
    max_body_bytes=48 * 1024,
    max_file_bytes=32 * 1024,
    max_field_bytes=4 * 1024,
    max_parts=4,
    max_header_bytes=4 * 1024,
    file_memory_bytes=1024,
)

app = Hayate()


@app.get("/health")
async def health(c: Context) -> Response:
    return c.json({"status": "ok"})


@app.post("/typed/:item_id")
@endpoint(status=201, providers=[StdlibProvider()])
async def typed_echo(
    item_id: Annotated[UUID, Path()],
    payload: Annotated[EchoIn, Body()],
    repeat: Annotated[int, Constraints(ge=1, le=3), Query()] = 1,
) -> EchoOut:
    return {
        "item_id": item_id,
        "value": payload["value"] * repeat,
        "repeat": repeat,
    }


@app.post("/upload")
@endpoint(providers=[StdlibProvider()])
async def upload(
    file: Annotated[
        File,
        Form(media_type="multipart/form-data", limits=UPLOAD_LIMITS),
    ],
) -> UploadOut:
    chunks = [chunk async for chunk in file.stream(4)]
    return {
        "name": file.name,
        "media_type": file.type,
        "size": file.size,
        "spooled": file.spooled,
        "content": b"".join(chunks).decode(),
    }


@app.get(
    "/raw/:item_id",
    validated(
        "param",
        {
            "type": "object",
            "properties": {"item_id": {"type": "string", "format": "uuid"}},
            "required": ["item_id"],
            "additionalProperties": False,
        },
    ),
)
async def raw_schema(c: Context) -> Response:
    return c.json({"item_id": c.req.valid("param")["item_id"]})


OpenApi(app, title="typed-workerd", version="1").register(app)
