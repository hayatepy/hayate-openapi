"""Small constructors for OpenAPI 3.1 security schemes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

type SecurityScheme = dict[str, Any]
type SecurityRequirement = dict[str, list[str]]


def cookie_security(name: str, *, description: str | None = None) -> SecurityScheme:
    scheme: SecurityScheme = {"type": "apiKey", "in": "cookie", "name": name}
    if description is not None:
        scheme["description"] = description
    return scheme


def bearer_security(
    *, bearer_format: str | None = None, description: str | None = None
) -> SecurityScheme:
    scheme: SecurityScheme = {"type": "http", "scheme": "bearer"}
    if bearer_format is not None:
        scheme["bearerFormat"] = bearer_format
    if description is not None:
        scheme["description"] = description
    return scheme


def oauth2_authorization_code_security(
    *,
    authorization_url: str,
    token_url: str,
    scopes: Mapping[str, str] | None = None,
    refresh_url: str | None = None,
) -> SecurityScheme:
    flow: dict[str, Any] = {
        "authorizationUrl": authorization_url,
        "tokenUrl": token_url,
        "scopes": dict(scopes or {}),
    }
    if refresh_url is not None:
        flow["refreshUrl"] = refresh_url
    return {"type": "oauth2", "flows": {"authorizationCode": flow}}
