"""The generator: walk ``app.routes``, merge the tags, emit OpenAPI 3.1.

Everything here is a pure function of the app object — no I/O, no globals —
so ``generate()`` is trivially testable and the mounted endpoint is just
``c.json(generate())``.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from hayate import Context, Response

from .providers import SchemaProvider, default_providers, resolve
from .security import SecurityRequirement, SecurityScheme
from .tags import OPENAPI_ATTR

OPENAPI_VERSION = "3.1.1"

_SCALAR_SCRIPT_URL = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.63.0"
_SCALAR_SCRIPT_INTEGRITY = "sha384-bnRzGcRYqM9jbXxeIbNDWWD8mNMY0p8qvmfAyfcT5S7/I6E7bsyLprA0uIP2gUu7"
_OPENAPI_EXCLUDE_ATTR = "__hayate_openapi_exclude__"
_PARAM_RE = re.compile(r":(\w+)(\([^)]*\))?")
_PATH_TEMPLATE_PARAM_RE = re.compile(r"\{[^{}]+\}")
_HTTPS_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
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
        docs_path: str | None = "/docs",
        scalar_script_url: str = _SCALAR_SCRIPT_URL,
        providers: list[SchemaProvider] | None = None,
        security_schemes: Mapping[str, SecurityScheme] | None = None,
        security: list[SecurityRequirement] | None = None,
    ) -> None:
        if docs_path is not None:
            if not docs_path.startswith("/") or docs_path.startswith("//"):
                raise ValueError("docs_path must be an absolute application path")
            if docs_path == path:
                raise ValueError("docs_path and path must be different")
        self.app = app
        self.title = title
        self.version = version
        self.description = description
        self.path = path
        self.docs_path = docs_path
        self.scalar_script_url = scalar_script_url
        self._scalar_script_source = _script_source(scalar_script_url)
        self.providers = providers if providers is not None else default_providers()
        self.security_schemes = dict(security_schemes or {})
        self.security = security

    # -- generation --------------------------------------------------------------------

    def generate(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        schemas: dict[str, Any] = {}
        template_paths: dict[str, str] = {}
        operation_ids: set[str] = set()

        for route in self.app.routes:
            if getattr(route.handler, _OPENAPI_EXCLUDE_ATTR, False):
                continue
            if route.method not in _HTTP_METHODS:
                continue
            converted = _convert_path(route.pattern)
            if converted is None:
                continue
            path, path_params = converted
            template_path = _PATH_TEMPLATE_PARAM_RE.sub("{}", path)
            existing_path = template_paths.setdefault(template_path, path)
            if existing_path != path:
                raise ValueError(
                    f"OpenAPI cannot distinguish templated paths {existing_path!r} and {path!r}"
                )
            method = route.method.lower()
            if method in paths.get(path, {}):
                raise ValueError(f"duplicate OpenAPI operation for {route.method} {path}")
            operation = self._operation(route, path, path_params, schemas)
            operation_id = operation["operationId"]
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError("OpenAPI operationId must be a non-empty string")
            if operation_id in operation_ids:
                raise ValueError(f"duplicate OpenAPI operationId {operation_id!r}")
            operation_ids.add(operation_id)
            paths.setdefault(path, {})[method] = operation

        document: dict[str, Any] = {
            "openapi": OPENAPI_VERSION,
            "info": {"title": self.title, "version": self.version},
            "paths": paths,
        }
        if self.description is not None:
            document["info"]["description"] = self.description
        components: dict[str, Any] = {}
        if schemas:
            components["schemas"] = schemas
        if self.security_schemes:
            components["securitySchemes"] = self.security_schemes
        if components:
            document["components"] = components
        if self.security is not None:
            document["security"] = self.security
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
        explicit_security = meta.get("security")
        if explicit_security is not None:
            operation["security"] = explicit_security

        parameters = list(path_params)
        has_validator = False
        inferred_security: dict[str, list[str]] = {}
        for middleware in route.middleware:
            for requirement in getattr(middleware, "__openapi_security__", ()):
                inferred_security.update(requirement)
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
                media_type = tag.get("media_type") or "application/x-www-form-urlencoded"
                operation["requestBody"] = {
                    "required": True,
                    "content": {media_type: {"schema": schema}},
                }
            else:  # query: expand an object schema into individual parameters
                parameters.extend(self._query_parameters(schema, components))

        if explicit_security is None and inferred_security:
            operation["security"] = [inferred_security]
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
        for name, definition in defs.items():
            existing = components.get(name, _MISSING)
            if existing is not _MISSING and existing != definition:
                raise ValueError(f"conflicting OpenAPI component schema {name!r}")
            components[name] = definition
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
            resolved = components.get(ref.rsplit("/", 1)[1], {})
            return resolved if isinstance(resolved, dict) else {}
        return schema

    # -- mounting ----------------------------------------------------------------------

    def register(self, app: Any) -> None:
        async def openapi_handler(c: Context) -> Response:
            return c.json(self.generate())

        setattr(openapi_handler, _OPENAPI_EXCLUDE_ATTR, True)
        app.get(self.path)(openapi_handler)

        if self.docs_path is not None:

            async def docs_handler(c: Context) -> Response:
                return c.html(
                    self._docs_html(),
                    headers={
                        "cache-control": "no-store",
                        "content-security-policy": self._docs_csp(),
                        "referrer-policy": "no-referrer",
                        "x-content-type-options": "nosniff",
                    },
                )

            setattr(docs_handler, _OPENAPI_EXCLUDE_ATTR, True)
            app.get(self.docs_path)(docs_handler)

    def _docs_html(self) -> str:
        configuration = html.escape(
            json.dumps(
                {
                    "url": self.path,
                    "withDefaultFonts": False,
                    "showDeveloperTools": "never",
                    "hideClientButton": True,
                    "agent": {"disabled": True},
                    "mcp": {"disabled": True},
                    "telemetry": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            quote=True,
        )
        script_url = html.escape(self.scalar_script_url, quote=True)
        title = html.escape(self.title, quote=True)
        integrity = ""
        if self.scalar_script_url == _SCALAR_SCRIPT_URL:
            integrity = (
                f'\n      integrity="{_SCALAR_SCRIPT_INTEGRITY}"\n      crossorigin="anonymous"'
            )
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} API reference</title>
  </head>
  <body>
    <script
      id="api-reference"
      data-configuration="{configuration}"
      src="{script_url}"{integrity}
    ></script>
    <noscript>Enable JavaScript to use the interactive API reference.</noscript>
  </body>
</html>
"""

    def _docs_csp(self) -> str:
        return "; ".join(
            (
                "default-src 'none'",
                f"script-src {self._scalar_script_source}",
                "style-src 'unsafe-inline'",
                "img-src data: https:",
                "connect-src 'self' https:",
                "font-src 'none'",
                "object-src 'none'",
                "base-uri 'none'",
                "form-action 'none'",
                "frame-ancestors 'none'",
            )
        )


def _script_source(url: str) -> str:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        raise ValueError("scalar_script_url must not contain control characters")
    if url.startswith("/") and not url.startswith("//"):
        return "'self'"

    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or _HTTPS_HOST_RE.fullmatch(parsed.hostname) is None
    ):
        raise ValueError("scalar_script_url must be root-relative or use an HTTPS origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("scalar_script_url has an invalid port") from exc
    suffix = f":{port}" if port is not None else ""
    return f"https://{parsed.hostname}{suffix}"


def _status_text(status: int) -> str:
    import http

    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "Response"


_MISSING = object()
