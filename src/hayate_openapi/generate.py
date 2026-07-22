"""The generator: walk ``app.routes``, merge the tags, emit OpenAPI 3.1.

Everything here is a pure function of the app object — no I/O, no globals —
so ``generate()`` is trivially testable and the mounted endpoint is just
``c.json(generate())``.
"""

from __future__ import annotations

import re
from typing import Any

from hayate import Context, Response

from .providers import SchemaProvider, default_providers, resolve
from .tags import OPENAPI_ATTR

OPENAPI_VERSION = "3.1.1"

_PARAM_RE = re.compile(r":(\w+)(\([^)]*\))?")
# Only real HTTP verbs become operations; hayate's websocket routes use an
# internal marker method and are not documentable in OpenAPI.
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"})


def _convert_path(pattern: str) -> tuple[str, list[dict[str, Any]]] | None:
    """URLPattern pathname -> (OpenAPI path, path parameters).

    Wildcard patterns are not documentable operations; returns None for them.
    """
    if "*" in pattern:
        return None
    parameters: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        name, regex = match.group(1), match.group(2)
        schema: dict[str, Any] = {"type": "string"}
        if regex:
            schema["pattern"] = regex[1:-1]
        parameters.append({"name": name, "in": "path", "required": True, "schema": schema})
        return "{" + name + "}"

    return _PARAM_RE.sub(replace, pattern), parameters


def _operation_id(method: str, path: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_") or "root"
    return f"{method.lower()}_{slug}"


class OpenApi:
    def __init__(
        self,
        app: Any,
        *,
        title: str,
        version: str,
        description: str | None = None,
        path: str = "/openapi.json",
        providers: list[SchemaProvider] | None = None,
    ) -> None:
        self.app = app
        self.title = title
        self.version = version
        self.description = description
        self.path = path
        self.providers = providers if providers is not None else default_providers()

    # -- generation --------------------------------------------------------------------

    def generate(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        components: dict[str, Any] = {}

        for route in self.app.routes:
            if route.method not in _HTTP_METHODS:
                continue
            converted = _convert_path(route.pattern)
            if converted is None:
                continue
            path, path_params = converted
            operation = self._operation(route, path, path_params, components)
            paths.setdefault(path, {})[route.method.lower()] = operation

        document: dict[str, Any] = {
            "openapi": OPENAPI_VERSION,
            "info": {"title": self.title, "version": self.version},
            "paths": paths,
        }
        if self.description is not None:
            document["info"]["description"] = self.description
        if components:
            document["components"] = {"schemas": components}
        return document

    def _operation(
        self,
        route: Any,
        path: str,
        path_params: list[dict[str, Any]],
        components: dict[str, Any],
    ) -> dict[str, Any]:
        meta = getattr(route.handler, OPENAPI_ATTR, {})
        operation: dict[str, Any] = {
            "operationId": meta.get("operation_id") or _operation_id(route.method, path),
            "responses": {},
        }
        for key in ("summary", "description", "tags"):
            if meta.get(key) is not None:
                operation[key] = meta[key]
        if meta.get("deprecated"):
            operation["deprecated"] = True

        parameters = list(path_params)
        has_validator = False
        for middleware in route.middleware:
            tag = getattr(middleware, OPENAPI_ATTR, None)
            if tag is None:
                continue
            has_validator = True
            schema = self._register_schema(tag["type"], components)
            target = tag["target"]
            if target == "json":
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                }
            elif target == "form":
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/x-www-form-urlencoded": {"schema": schema}},
                }
            else:  # query: expand an object schema into individual parameters
                parameters.extend(self._query_parameters(schema, components))

        if parameters:
            operation["parameters"] = parameters

        responses: dict[str, Any] = {}
        for status, type_ in (meta.get("responses") or {}).items():
            entry: dict[str, Any] = {"description": _status_text(status)}
            if type_ is not None:
                entry["content"] = {
                    "application/json": {"schema": self._register_schema(type_, components)}
                }
            responses[str(status)] = entry
        if not responses:
            responses["200"] = {"description": "Successful response"}
        if has_validator and "400" not in responses:
            responses["400"] = {
                "description": "Validation failed",
                "content": {"application/problem+json": {"schema": {"type": "object"}}},
            }
        operation["responses"] = responses
        return operation

    def _register_schema(self, type_: Any, components: dict[str, Any]) -> dict[str, Any]:
        provider = resolve(self.providers, type_)
        schema, defs = provider.schema(type_)
        components.update(defs)
        return schema

    def _query_parameters(
        self, schema: dict[str, Any], components: dict[str, Any]
    ) -> list[dict[str, Any]]:
        resolved = self._deref(schema, components)
        required = set(resolved.get("required", ()))
        out = []
        for name, prop in (resolved.get("properties") or {}).items():
            out.append({"name": name, "in": "query", "required": name in required, "schema": prop})
        return out

    @staticmethod
    def _deref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
        ref = schema.get("$ref", "")
        if ref.startswith("#/components/schemas/"):
            return components.get(ref.rsplit("/", 1)[1], {})
        return schema

    # -- mounting ----------------------------------------------------------------------

    def register(self, app: Any) -> None:
        async def openapi_handler(c: Context) -> Response:
            return c.json(self.generate())

        app.get(self.path)(openapi_handler)


def _status_text(status: int) -> str:
    import http

    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Response"
