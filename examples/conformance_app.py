"""Representative application used by the interoperability gate."""

import msgspec
import pydantic
from hayate import Context, Hayate, Response

from hayate_openapi import binary_file, describe, validated


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
async def upload_cover(c: Context) -> Response:
    return c.json({}, status=201)
