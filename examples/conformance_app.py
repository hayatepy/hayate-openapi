"""Representative application used by the interoperability gate."""

from typing import Annotated, TypedDict
from uuid import UUID

import msgspec
import pydantic
from hayate import Context, File, Hayate, Response

from hayate_openapi import (
    Body,
    Cookie,
    Form,
    Header,
    Path,
    Query,
    binary_file,
    describe,
    endpoint,
    validated,
)


class BookIn(msgspec.Struct):
    title: str
    pages: int = 0


class BookOut(msgspec.Struct):
    id: str
    title: str
    pages: int


class SearchQuery(pydantic.BaseModel):
    query: str
    limit: int = 20


class TypedBookIn(TypedDict):
    title: str


class TypedBookOut(TypedDict):
    id: UUID
    title: str
    notify: bool


class SessionOut(TypedDict):
    request_id: str
    scopes: list[str]
    theme: str
    username: str


app = Hayate()


@app.post("/books", validated("json", BookIn))
@describe(
    status=201,
    response=BookOut,
    responses={409: None},
    summary="Create a book",
    tags=["books"],
)
async def create_book(c: Context) -> Response:
    return c.json({}, status=201)


@app.get("/books/:book_id")
@describe(response=BookOut, responses={404: None})
async def get_book(c: Context) -> Response:
    return c.json({})


@app.get("/search", validated("query", SearchQuery))
async def search(c: Context) -> Response:
    return c.json([])


@app.post(
    "/covers",
    validated(
        "form",
        {
            "type": "object",
            "properties": {"cover": binary_file(), "alt": {"type": "string"}},
            "required": ["cover"],
        },
        media_type="multipart/form-data",
    ),
)
@describe(status=201)
async def upload_cover(c: Context) -> Response:
    return c.json({}, status=201)


@app.post("/typed-covers")
@endpoint(status=201)
async def upload_typed_cover(
    cover: Annotated[File, Form(media_type="multipart/form-data")],
    alt: Annotated[str, Form(media_type="multipart/form-data")],
) -> dict[str, str | int]:
    return {"alt": alt, "name": cover.name, "size": cover.size}


@app.post("/typed-books/:book_id")
@endpoint(status=201, summary="Create a typed book", tags=["books"])
async def create_typed_book(
    book_id: Annotated[UUID, Path(description="Stable book identifier")],
    book: Annotated[TypedBookIn, Body(description="Book creation input")],
    notify: Annotated[bool, Query(description="Send a notification")] = False,
) -> TypedBookOut:
    return {"id": book_id, "title": book["title"], "notify": notify}


@app.post("/sessions")
@endpoint(status=201, summary="Create a session")
async def create_session(
    username: Annotated[str, Form()],
    scopes: Annotated[list[str], Form()],
    request_id: Annotated[str, Header(alias="x-request-id")],
    theme: Annotated[str, Cookie()] = "system",
) -> SessionOut:
    return {
        "request_id": request_id,
        "scopes": scopes,
        "theme": theme,
        "username": username,
    }
