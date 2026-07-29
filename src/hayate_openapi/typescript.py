"""Generate a typed, dependency-free TypeScript fetch client.

Schema-to-TypeScript conversion deliberately remains the responsibility of
``openapi-typescript``.  This module consumes the same OpenAPI document and
generates the smaller piece that tool does not provide: one callable method
per operation, correct HTTP serialization, and status-discriminated Fetch
responses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
_PARAMETER_LOCATIONS = frozenset({"cookie", "header", "path", "query"})
_PARAMETER_DEFAULTS = {
    "cookie": ("form", True),
    "header": ("simple", False),
    "path": ("simple", False),
    "query": ("form", True),
}
_FORM_MEDIA_TYPES = frozenset(
    {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }
)


@dataclass(frozen=True, slots=True)
class _BinaryField:
    name: str
    required: bool
    multiple: bool


@dataclass(frozen=True, slots=True)
class _Operation:
    index: int
    path: str
    method: str
    operation_id: str
    required_input: bool
    body_media_type: str | None
    body_required: bool
    binary_fields: tuple[_BinaryField, ...]

    @property
    def body_property(self) -> str | None:
        if self.body_media_type is None:
            return None
        return "form" if self.body_media_type in _FORM_MEDIA_TYPES else "json"


def generate_typescript_client(
    document: Mapping[str, Any],
    *,
    types_import: str = "./api-types.js",
) -> str:
    """Return a deterministic TypeScript client for a Hayate OpenAPI document.

    The generated source imports only the ``paths`` type produced by
    ``openapi-typescript``.  At runtime it uses the platform Fetch, URL,
    Headers, URLSearchParams, and FormData APIs and therefore adds no
    JavaScript dependency.

    Unsupported serialization contracts fail here instead of becoming a
    client that sends a different wire representation than the server accepts.
    """

    if not isinstance(types_import, str) or not types_import:
        raise ValueError("TypeScript types import must be a non-empty string")
    operations = _collect_operations(document)

    sections = [
        _HEADER.replace("__TYPES_IMPORT__", json.dumps(types_import)),
        _operation_types(operations),
        _client_interface(operations),
        _RUNTIME_HELPERS,
        (
            _MULTIPART_HELPERS
            if any(operation.body_media_type == "multipart/form-data" for operation in operations)
            else ""
        ),
        _client_factory(operations),
    ]
    return "\n\n".join(section.rstrip() for section in sections if section) + "\n"


def _collect_operations(document: Mapping[str, Any]) -> tuple[_Operation, ...]:
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("OpenAPI document paths must be an object")

    components = document.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, Mapping) else {}
    if not isinstance(schemas, Mapping):
        raise ValueError("OpenAPI component schemas must be an object")

    seen_operation_ids: set[str] = set()
    operations: list[_Operation] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("OpenAPI client paths must be absolute strings")
        if not isinstance(path_item, Mapping):
            raise ValueError(f"OpenAPI path item {path!r} must be an object")
        path_parameters = _parameter_list(
            path_item.get("parameters", ()),
            f"OpenAPI path parameters for {path!r}",
        )
        for method, raw_operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            if not isinstance(raw_operation, Mapping):
                raise ValueError(f"OpenAPI operation {method.upper()} {path} must be an object")

            operation_id = raw_operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError(f"OpenAPI operation {method.upper()} {path} needs an operationId")
            if operation_id in seen_operation_ids:
                raise ValueError(f"duplicate OpenAPI operationId {operation_id!r}")
            seen_operation_ids.add(operation_id)

            operation_parameters = _parameter_list(
                raw_operation.get("parameters", ()),
                f"OpenAPI parameters for {operation_id!r}",
            )
            parameters = _merge_parameters(
                path_parameters,
                operation_parameters,
                operation_id,
            )
            required_input = False
            for parameter in parameters:
                location = parameter.get("in")
                name = parameter.get("name")
                if location not in _PARAMETER_LOCATIONS or not isinstance(name, str) or not name:
                    raise ValueError(f"invalid OpenAPI parameter for {operation_id!r}")
                if location == "path" and parameter.get("required") is not True:
                    raise ValueError(f"OpenAPI path parameter {name!r} must be required")
                required_input = required_input or parameter.get("required") is True
                schema = parameter.get("schema", {})
                if not isinstance(schema, Mapping):
                    raise ValueError(f"OpenAPI parameter schema for {name!r} must be an object")
                _validate_parameter_serialization(
                    parameter,
                    schema,
                    schemas,
                    operation_id,
                    name,
                    location,
                )

            body_media_type, body_required, binary_fields = _request_body(
                raw_operation,
                schemas,
                operation_id,
            )
            required_input = required_input or body_required
            _validate_responses(raw_operation, operation_id)

            operations.append(
                _Operation(
                    index=len(operations),
                    path=path,
                    method=method,
                    operation_id=operation_id,
                    required_input=required_input,
                    body_media_type=body_media_type,
                    body_required=body_required,
                    binary_fields=binary_fields,
                )
            )
    return tuple(operations)


def _parameter_list(value: object, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{label} must be an array")
    result: list[Mapping[str, Any]] = []
    seen: set[tuple[object, object]] = set()
    for parameter in value:
        if not isinstance(parameter, Mapping):
            raise ValueError(f"{label} must contain objects")
        key = (parameter.get("name"), parameter.get("in"))
        if key in seen:
            raise ValueError(f"{label} contains duplicate parameter {key!r}")
        seen.add(key)
        result.append(parameter)
    return tuple(result)


def _merge_parameters(
    inherited: tuple[Mapping[str, Any], ...],
    operation: tuple[Mapping[str, Any], ...],
    operation_id: str,
) -> tuple[Mapping[str, Any], ...]:
    merged = {(parameter.get("name"), parameter.get("in")): parameter for parameter in inherited}
    for parameter in operation:
        key = (parameter.get("name"), parameter.get("in"))
        merged[key] = parameter
    if any(name is None or location is None for name, location in merged):
        raise ValueError(f"invalid OpenAPI parameter for {operation_id!r}")
    return tuple(merged.values())


def _validate_parameter_serialization(
    parameter: Mapping[str, Any],
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    operation_id: str,
    name: str,
    location: str,
) -> None:
    if "content" in parameter:
        raise ValueError(
            f"OpenAPI client parameter {name!r} in {operation_id!r} "
            "must use schema-based serialization"
        )
    default_style, default_explode = _PARAMETER_DEFAULTS[location]
    style = parameter.get("style", default_style)
    explode = parameter.get("explode", default_explode)
    if style != default_style or explode is not default_explode:
        raise ValueError(
            f"OpenAPI client parameter {name!r} in {operation_id!r} uses unsupported "
            f"{location} serialization"
        )
    if parameter.get("allowReserved") is True:
        raise ValueError(
            f"OpenAPI client parameter {name!r} in {operation_id!r} does not support allowReserved"
        )

    resolved = _deref(schema, schemas)
    schema_type = resolved.get("type")
    if schema_type == "object":
        raise ValueError(
            f"OpenAPI client parameter {name!r} in {operation_id!r} "
            "uses unsupported object encoding"
        )
    if schema_type == "array":
        if location == "cookie":
            raise ValueError(
                f"OpenAPI client parameter {name!r} in {operation_id!r} "
                "cannot serialize cookie arrays unambiguously"
            )
        items = resolved.get("items", {})
        if not isinstance(items, Mapping) or _deref(items, schemas).get("type") in {
            "array",
            "object",
        }:
            raise ValueError(
                f"OpenAPI client parameter {name!r} in {operation_id!r} "
                "must be an array of scalar values"
            )
    if _contains_binary(resolved, schemas):
        raise ValueError(
            f"OpenAPI client parameter {name!r} in {operation_id!r} cannot use binary values"
        )


def _request_body(
    operation: Mapping[str, Any],
    schemas: Mapping[str, Any],
    operation_id: str,
) -> tuple[str | None, bool, tuple[_BinaryField, ...]]:
    body = operation.get("requestBody")
    if body is None:
        return None, False, ()
    if not isinstance(body, Mapping):
        raise ValueError(f"OpenAPI request body for {operation_id!r} must be an object")
    content = body.get("content")
    if not isinstance(content, Mapping) or len(content) != 1:
        raise ValueError(
            f"OpenAPI client operation {operation_id!r} must declare exactly one request media type"
        )
    media_type, media = next(iter(content.items()))
    if not isinstance(media_type, str) or not isinstance(media, Mapping):
        raise ValueError(f"invalid OpenAPI request content for {operation_id!r}")
    if not (_is_json_media_type(media_type) or media_type in _FORM_MEDIA_TYPES):
        raise ValueError(
            f"OpenAPI client operation {operation_id!r} uses unsupported request media type "
            f"{media_type!r}"
        )
    schema = media.get("schema", {})
    if not isinstance(schema, Mapping):
        raise ValueError(f"OpenAPI request schema for {operation_id!r} must be an object")
    if media.get("encoding"):
        raise ValueError(
            f"OpenAPI client operation {operation_id!r} uses unsupported form encoding overrides"
        )

    binary_fields: tuple[_BinaryField, ...] = ()
    if media_type in _FORM_MEDIA_TYPES:
        resolved = _deref(schema, schemas)
        if resolved.get("type") != "object":
            raise ValueError(f"form request body for {operation_id!r} must be an object schema")
        binary_fields = _binary_fields(resolved, schemas, operation_id)
        if media_type != "multipart/form-data" and binary_fields:
            raise ValueError(f"binary form fields in {operation_id!r} require multipart/form-data")
    return media_type, body.get("required") is True, binary_fields


def _binary_fields(
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    operation_id: str,
) -> tuple[_BinaryField, ...]:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError(f"form request body for {operation_id!r} needs object properties")
    required = set(schema.get("required", ()))
    result: list[_BinaryField] = []
    for name, raw_property in properties.items():
        if not isinstance(name, str) or not isinstance(raw_property, Mapping):
            raise ValueError(f"invalid form property in {operation_id!r}")
        prop = _deref(raw_property, schemas)
        multiple = prop.get("type") == "array"
        candidate = prop.get("items", {}) if multiple else prop
        if not isinstance(candidate, Mapping):
            raise ValueError(f"invalid array form property {name!r} in {operation_id!r}")
        candidate = _deref(candidate, schemas)
        if candidate.get("format") == "binary":
            if candidate.get("type") not in (None, "string"):
                raise ValueError(f"binary form property {name!r} must use a string schema")
            result.append(_BinaryField(name, name in required, multiple))
        elif candidate.get("type") in {"array", "object"} or "properties" in candidate:
            raise ValueError(
                f"form property {name!r} in {operation_id!r} must be a scalar or scalar array"
            )
        elif _contains_binary(candidate, schemas):
            raise ValueError(
                f"nested binary form property {name!r} in {operation_id!r} is not supported"
            )
    return tuple(result)


def _contains_binary(schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> bool:
    resolved = _deref(schema, schemas)
    if resolved.get("format") == "binary":
        return True
    for key in ("items", "additionalProperties"):
        value = resolved.get(key)
        if isinstance(value, Mapping) and _contains_binary(value, schemas):
            return True
    properties = resolved.get("properties", {})
    return isinstance(properties, Mapping) and any(
        isinstance(value, Mapping) and _contains_binary(value, schemas)
        for value in properties.values()
    )


def _validate_responses(operation: Mapping[str, Any], operation_id: str) -> None:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping) or not responses:
        raise ValueError(f"OpenAPI operation {operation_id!r} needs responses")
    for status, response in responses.items():
        if not isinstance(status, str) or not status.isdecimal():
            raise ValueError(
                f"OpenAPI client operation {operation_id!r} needs exact numeric response statuses"
            )
        if not isinstance(response, Mapping):
            raise ValueError(f"invalid OpenAPI response {status!r} for {operation_id!r}")
        content = response.get("content", {})
        if not isinstance(content, Mapping):
            raise ValueError(f"invalid OpenAPI response content for {operation_id!r}")
        unsupported = [media for media in content if not _is_json_media_type(media)]
        if unsupported:
            raise ValueError(
                f"OpenAPI client operation {operation_id!r} uses unsupported response media type "
                f"{unsupported[0]!r}"
            )


def _deref(schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.removeprefix("#/components/schemas/")
    resolved = schemas.get(name)
    if not isinstance(resolved, Mapping):
        raise ValueError(f"OpenAPI schema reference {ref!r} does not resolve")
    return resolved


def _is_json_media_type(media_type: object) -> bool:
    return isinstance(media_type, str) and (
        media_type == "application/json" or media_type.endswith("+json")
    )


def _operation_types(operations: tuple[_Operation, ...]) -> str:
    blocks = []
    for operation in operations:
        body = ""
        if operation.body_media_type is not None:
            base = (
                f"RequestBody<Operation{operation.index}, {json.dumps(operation.body_media_type)}>"
            )
            if operation.binary_fields:
                omitted = " | ".join(json.dumps(field.name) for field in operation.binary_fields)
                replacements = "; ".join(
                    f"{json.dumps(field.name)}{'' if field.required else '?'}: "
                    f"{'Blob[]' if field.multiple else 'Blob'}"
                    for field in operation.binary_fields
                )
                base = f"Omit<{base}, {omitted}> & {{ {replacements} }}"
            optional = "" if operation.body_required else "?"
            body = f" & {{ {operation.body_property}{optional}: {base} }}"
        blocks.append(
            "\n".join(
                (
                    f"type Operation{operation.index} = "
                    f"paths[{json.dumps(operation.path)}][{json.dumps(operation.method)}];",
                    f"type Operation{operation.index}Input = "
                    f"OperationParameters<Operation{operation.index}>{body};",
                    f"type Operation{operation.index}Response = "
                    f"TypedResponse<Operation{operation.index}>;",
                )
            )
        )
    return "\n\n".join(blocks)


def _client_interface(operations: tuple[_Operation, ...]) -> str:
    methods = []
    for operation in operations:
        optional = "" if operation.required_input else "?"
        methods.append(
            f"  {json.dumps(operation.operation_id)}"
            f"(input{optional}: Operation{operation.index}Input, "
            f"init?: ClientRequestInit): Promise<Operation{operation.index}Response>;"
        )
    return "\n".join(
        (
            "export interface HayateClient {",
            *methods,
            "}",
        )
    )


def _client_factory(operations: tuple[_Operation, ...]) -> str:
    methods = []
    for operation in operations:
        input_default = (
            "" if operation.required_input else f" = {{}} as Operation{operation.index}Input"
        )
        body_lines: list[str] = []
        if operation.body_media_type is not None:
            body_property = operation.body_property
            assert body_property is not None
            media_type = operation.body_media_type
            body_lines.extend(
                (
                    f"      const bodyValue = input.{body_property};",
                    "      let body: BodyInit | undefined;",
                )
            )
            if _is_json_media_type(media_type):
                body_lines.extend(
                    (
                        "      if (bodyValue !== undefined) {",
                        "        body = JSON.stringify(bodyValue);",
                        f"        setContentType(headers, {json.dumps(media_type)});",
                        "      }",
                    )
                )
            elif media_type == "application/x-www-form-urlencoded":
                body_lines.extend(
                    (
                        "      if (bodyValue !== undefined) {",
                        "        body = encodeSearchValues(bodyValue);",
                        f"        setContentType(headers, {json.dumps(media_type)});",
                        "      }",
                    )
                )
            else:
                body_lines.extend(
                    (
                        "      if (bodyValue !== undefined) {",
                        "        body = encodeMultipart(bodyValue);",
                        "      }",
                    )
                )
        else:
            body_lines.append("      const body: BodyInit | undefined = undefined;")

        methods.append(
            "\n".join(
                (
                    f"    [{json.dumps(operation.operation_id)}]: async (",
                    f"      input: Operation{operation.index}Input{input_default},",
                    "      init: ClientRequestInit = {},",
                    f"    ): Promise<Operation{operation.index}Response> => {{",
                    "      const runtimeInput = input as unknown as InputRecord;",
                    f"      let path = {json.dumps(operation.path)};",
                    "      path = encodePath(path, runtimeInput.path);",
                    "      const url = buildUrl(options.baseUrl, path, runtimeInput.query);",
                    "      const headers = await buildHeaders(options, runtimeInput, init);",
                    *body_lines,
                    "      const request: RequestInit = {",
                    "        ...init,",
                    f"        method: {json.dumps(operation.method.upper())},",
                    "        headers,",
                    "      };",
                    "      if (body !== undefined) request.body = body;",
                    "      if (",
                    "        request.credentials === undefined &&",
                    "        options.credentials !== undefined",
                    "      ) {",
                    "        request.credentials = options.credentials;",
                    "      }",
                    "      const response = await fetchImpl(url, request);",
                    f"      return response as Operation{operation.index}Response;",
                    "    },",
                )
            )
        )
    return "\n".join(
        (
            "export function createHayateClient(options: HayateClientOptions): HayateClient {",
            "  const fetchImpl = options.fetch ?? globalThis.fetch;",
            '  if (typeof fetchImpl !== "function") {',
            '    throw new TypeError("Hayate client requires a Fetch implementation");',
            "  }",
            "  return {",
            *methods,
            "  };",
            "}",
        )
    )


_HEADER = """\
/* This file is generated by hayate-openapi. Do not edit it directly. */
import type { paths } from __TYPES_IMPORT__;

export interface HayateClientOptions {
  baseUrl: string | URL;
  fetch?: typeof globalThis.fetch;
  headers?: HeadersInit | (() => HeadersInit | Promise<HeadersInit>);
  credentials?: RequestCredentials;
}

export type ClientRequestInit = Omit<RequestInit, "body" | "headers" | "method"> & {
  headers?: HeadersInit;
};

type Defined<T> = Exclude<T, undefined>;
type OperationParameters<Operation> = Operation extends { parameters: infer Parameters }
  ? {
      [Location in keyof Parameters as [Defined<Parameters[Location]>] extends [never]
        ? never
        : Location]: Defined<Parameters[Location]>;
    }
  : Record<never, never>;
type RequestBody<Operation, MediaType extends string> =
  Operation extends { requestBody?: infer Body }
    ? [Defined<Body>] extends [never]
      ? never
      : Defined<Body> extends { content: infer Content }
        ? MediaType extends keyof Content
          ? Content[MediaType]
          : never
        : never
    : never;
type Responses<Operation> = Operation extends { responses: infer Value } ? Value : never;
type JsonBody<ResponseValue> =
  ResponseValue extends { content: infer Content }
    ? Content extends Record<PropertyKey, unknown>
      ? Content[keyof Content]
      : never
    : never;
export type TypedResponse<Operation> = {
  [Status in Extract<keyof Responses<Operation>, number>]:
    Omit<globalThis.Response, "json" | "status"> & {
      readonly status: Status;
      json(): Promise<JsonBody<Responses<Operation>[Status]>>;
    };
}[Extract<keyof Responses<Operation>, number>];"""


_RUNTIME_HELPERS = """\
type InputRecord = {
  path?: Record<string, unknown>;
  query?: Record<string, unknown>;
  header?: Record<string, unknown>;
  cookie?: Record<string, unknown>;
};

function buildUrl(
  baseUrl: string | URL,
  path: string,
  query: Record<string, unknown> | undefined,
): URL {
  const base = baseUrl.toString();
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const relativePath = path.startsWith("/") ? path.slice(1) : path;
  const url = new URL(relativePath, normalizedBase);
  if (query !== undefined) {
    url.search = encodeSearchValues(query).toString();
  }
  return url;
}

function encodePath(path: string, parameters: Record<string, unknown> | undefined): string {
  if (parameters !== undefined) {
    for (const [name, value] of Object.entries(parameters)) {
      if (value === undefined) {
        continue;
      }
      const encoded = Array.isArray(value)
        ? value.map((item) => encodeURIComponent(String(item))).join(",")
        : encodeURIComponent(String(value));
      path = path.replaceAll(`{${name}}`, encoded);
    }
  }
  if (/\\{[^}]+\\}/u.test(path)) {
    throw new TypeError(`Hayate client is missing a path parameter for ${path}`);
  }
  return path;
}

function appendSearchValue(search: URLSearchParams, name: string, value: unknown): void {
  if (value === undefined) {
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      appendSearchValue(search, name, item);
    }
    return;
  }
  search.append(name, String(value));
}

function encodeSearchValues(values: unknown): URLSearchParams {
  const search = new URLSearchParams();
  if (typeof values !== "object" || values === null || Array.isArray(values)) {
    throw new TypeError("Hayate form and query inputs must be objects");
  }
  for (const [name, value] of Object.entries(values)) {
    appendSearchValue(search, name, value);
  }
  return search;
}

async function buildHeaders(
  options: HayateClientOptions,
  input: InputRecord,
  init: ClientRequestInit,
): Promise<Headers> {
  const configured =
    typeof options.headers === "function" ? await options.headers() : options.headers;
  const headers = new Headers(configured);
  for (const [name, value] of Object.entries(input.header ?? {})) {
    if (value !== undefined) {
      headers.set(name, Array.isArray(value) ? value.join(",") : String(value));
    }
  }
  const cookies: string[] = [];
  for (const [name, value] of Object.entries(input.cookie ?? {})) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item !== undefined) {
        cookies.push(`${encodeURIComponent(name)}=${encodeURIComponent(String(item))}`);
      }
    }
  }
  if (cookies.length > 0) {
    const existing = headers.get("cookie");
    headers.set("cookie", [existing, ...cookies].filter(Boolean).join("; "));
  }
  new Headers(init.headers).forEach((value, name) => headers.set(name, value));
  return headers;
}

function setContentType(headers: Headers, mediaType: string): void {
  if (!headers.has("content-type")) {
    headers.set("content-type", mediaType);
  }
}"""


_MULTIPART_HELPERS = """\
function appendFormValue(form: FormData, name: string, value: unknown): void {
  if (value === undefined) {
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      appendFormValue(form, name, item);
    }
    return;
  }
  if (typeof Blob !== "undefined" && value instanceof Blob) {
    form.append(name, value);
    return;
  }
  form.append(name, String(value));
}

function encodeMultipart(values: unknown): FormData {
  if (typeof values !== "object" || values === null || Array.isArray(values)) {
    throw new TypeError("Hayate multipart input must be an object");
  }
  const form = new FormData();
  for (const [name, value] of Object.entries(values)) {
    appendFormValue(form, name, value);
  }
  return form;
}"""
