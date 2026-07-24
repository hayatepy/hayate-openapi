"""Security scheme components and operation requirements."""

from hayate import Context, Hayate, Next

from hayate_openapi import (
    OpenApi,
    bearer_security,
    cookie_security,
    describe,
    oauth2_authorization_code_security,
)


async def oauth_required(c: Context, next_: Next) -> None:
    await next_()


oauth_required.__openapi_security__ = [{"OAuth2": ["documents:read"]}]


def security_document() -> dict:
    app = Hayate()

    @app.get("/documents", oauth_required)
    async def documents(c: Context):
        return c.json([])

    @app.get("/health")
    @describe(security=[])
    async def health(c: Context):
        return c.json({"ok": True})

    return OpenApi(
        app,
        title="Secured",
        version="1",
        security_schemes={
            "SessionCookie": cookie_security("__Host-hayate_auth.session"),
            "ApiKeyBearer": bearer_security(description="Hayate API key"),
            "OAuth2": oauth2_authorization_code_security(
                authorization_url="https://auth.example.com/api/auth/oauth2/authorize",
                token_url="https://auth.example.com/api/auth/oauth2/token",
                scopes={"documents:read": "Read documents"},
            ),
        },
        security=[{"SessionCookie": []}],
    ).generate()


def test_security_schemes_and_global_requirement():
    doc = security_document()
    schemes = doc["components"]["securitySchemes"]
    assert schemes["SessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-hayate_auth.session",
    }
    assert schemes["ApiKeyBearer"]["scheme"] == "bearer"
    assert schemes["OAuth2"]["flows"]["authorizationCode"]["scopes"] == {
        "documents:read": "Read documents"
    }
    assert doc["security"] == [{"SessionCookie": []}]


def test_route_middleware_security_is_inferred_and_public_override_is_kept():
    doc = security_document()
    assert doc["paths"]["/documents"]["get"]["security"] == [{"OAuth2": ["documents:read"]}]
    assert doc["paths"]["/health"]["get"]["security"] == []
