import msgspec
import pydantic
from hayate import Context, Hayate

from hayate_openapi import OpenApi, describe, validated


class BookIn(msgspec.Struct):
    title: str
    pages: int = 0


class BookOut(msgspec.Struct):
    id: str
    title: str


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10


class UserIn(pydantic.BaseModel):
    email: str
    name: str | None = None


def build_app() -> Hayate:
    app = Hayate()

    @app.post("/books", validated("json", BookIn))
    @describe(status=201, response=BookOut, summary="Create a book", tags=["books"])
    async def create_book(c: Context):
        return c.json({}, status=201)

    @app.get("/books/:id")
    @describe(response=BookOut, responses={404: None})
    async def show_book(c: Context):
        return c.json({})

    @app.get("/search", validated("query", SearchQuery))
    async def search(c: Context):
        return c.json([])

    @app.post("/users", validated("json", UserIn))
    async def create_user(c: Context):
        return c.json({})

    @app.post("/raw", validated("json", {"type": "object", "required": ["x"]}))
    async def raw(c: Context):
        return c.json({})

    @app.get("/plain/:kind([a-z]+)")
    async def plain(c: Context):
        return c.json({})

    @app.on("GET", "/mounted/*")
    async def mounted(c: Context):
        return c.json({})

    @app.ws("/ws")
    async def ws(c: Context, socket):  # pragma: no cover - never driven
        pass

    return app


def generate(app: Hayate | None = None) -> dict:
    return OpenApi(
        app or build_app(), title="Test API", version="0.0.1", description="fixture"
    ).generate()
