"""The two annotation surfaces (DESIGN §3.1): ``validated`` wraps the core
validator and tags the middleware with its type; ``describe`` tags the
handler. Both are additive — untagged routes still document (thinly).
"""

from __future__ import annotations

from typing import Any

from hayate import Middleware, validator

from .providers import SchemaProvider, default_providers, resolve

OPENAPI_ATTR = "__openapi__"


def validated(
    target: str,
    type_: Any,
    *,
    providers: list[SchemaProvider] | None = None,
) -> Middleware:
    """``hayate.validator`` plus an OpenAPI tag.

    Validation behavior is identical to wiring the converter yourself; the
    only addition is the ``__openapi__`` attribute the generator reads.
    """
    chain = providers if providers is not None else default_providers()
    provider = resolve(chain, type_)
    middleware = validator(target, provider.converter(type_))
    middleware.__openapi__ = {"target": target, "type": type_}  # type: ignore[attr-defined]
    return middleware


def describe(
    *,
    summary: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    status: int = 200,
    response: Any | None = None,
    responses: dict[int, Any] | None = None,
    operation_id: str | None = None,
    deprecated: bool = False,
):
    """Attach OpenAPI operation metadata to a handler (all fields optional).

    ``response=T, status=201`` is sugar for ``responses={201: T}``; use
    ``responses={404: None}`` for a schema-less documented status.
    """
    merged: dict[int, Any] = dict(responses or {})
    if response is not None or (responses is None and status != 200):
        merged.setdefault(status, response)

    def wrap(handler):
        handler.__openapi__ = {
            "summary": summary,
            "description": description,
            "tags": tags,
            "responses": merged,
            "operation_id": operation_id,
            "deprecated": deprecated,
        }
        return handler

    return wrap
