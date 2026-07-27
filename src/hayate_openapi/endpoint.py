"""Typed endpoint binding built on explicit ``typing.Annotated`` markers."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import (
    Annotated,
    Any,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from hayate import Context, HTTPException, Response

from .parameters import Body, Cookie, Depends, Form, Header, Path, Query, RequestMarker
from .providers import (
    SchemaProvider,
    converter_for,
    default_providers,
    dump_with,
    resolve,
)
from .tags import describe

TYPED_BINDINGS_ATTR = "__hayate_openapi_typed_bindings__"
TYPED_RESPONSES_ATTR = "__hayate_openapi_typed_responses__"
_EMPTY = inspect.Parameter.empty
_MISSING = object()
_REQUEST_MARKERS = (Body, Form, Query, Path, Header, Cookie)
_FORM_MEDIA_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})


@dataclass(frozen=True, slots=True)
class TypedBinding:
    """One compiled request value, also consumed by the OpenAPI generator."""

    parameter: str
    target: str
    external_name: str | None
    type_: Any
    marker: RequestMarker
    required: bool
    default: Any
    provider: SchemaProvider
    converter: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class _ContextArgument:
    parameter: str


@dataclass(frozen=True, slots=True)
class _RequestArgument:
    binding: TypedBinding


@dataclass(frozen=True, slots=True)
class _DependencyArgument:
    parameter: str
    dependency: _CompiledCallable
    use_cache: bool


type _Argument = _ContextArgument | _RequestArgument | _DependencyArgument


@dataclass(frozen=True, slots=True)
class _CompiledCallable:
    fn: Callable[..., Any]
    arguments: tuple[_Argument, ...]
    request_bindings: tuple[TypedBinding, ...]
    is_async: bool


class _RequestState:
    __slots__ = ("body", "form")

    def __init__(self) -> None:
        self.body: Any = _MISSING
        self.form: Any = _MISSING


def _split_marker(annotation: Any) -> tuple[Any, RequestMarker | Depends | None]:
    if get_origin(annotation) is not Annotated:
        return annotation, None
    base, *metadata = get_args(annotation)
    markers = [item for item in metadata if isinstance(item, (*_REQUEST_MARKERS, Depends))]
    if len(markers) > 1:
        raise TypeError("a typed endpoint parameter must have exactly one binding marker")
    remaining = [item for item in metadata if item not in markers]
    type_ = Annotated[base, *remaining] if remaining else base
    return type_, markers[0] if markers else None


def _is_multi_value(type_: Any) -> bool:
    origin = get_origin(type_)
    if origin in (list, set, frozenset, tuple):
        return True
    return any(_is_multi_value(item) for item in get_args(type_)) if get_args(type_) else False


def _external_name(parameter: str, marker: RequestMarker) -> str | None:
    if isinstance(marker, Body):
        return None
    alias = marker.alias
    if alias is not None:
        if not alias:
            raise TypeError(f"typed endpoint parameter {parameter!r} has an empty alias")
        return alias
    return parameter.replace("_", "-") if isinstance(marker, Header) else parameter


def _target(marker: RequestMarker) -> str:
    if isinstance(marker, Body):
        return "json"
    if isinstance(marker, Form):
        return "form"
    if isinstance(marker, Query):
        return "query"
    if isinstance(marker, Path):
        return "param"
    if isinstance(marker, Header):
        return "header"
    return "cookie"


def _base_media_type(value: str) -> str:
    return value.partition(";")[0].strip().lower()


def _unique_bindings(bindings: list[TypedBinding]) -> tuple[TypedBinding, ...]:
    seen: set[int] = set()
    unique = []
    for binding in bindings:
        identity = id(binding)
        if identity not in seen:
            seen.add(identity)
            unique.append(binding)
    return tuple(unique)


def _compile_callable(
    fn: Callable[..., Any],
    providers: list[SchemaProvider],
    *,
    localns: dict[str, Any],
    stack: tuple[Callable[..., Any], ...],
    cache: dict[Callable[..., Any], _CompiledCallable],
) -> _CompiledCallable:
    existing = cache.get(fn)
    if existing is not None:
        return existing
    if fn in stack:
        chain = " -> ".join(getattr(item, "__name__", repr(item)) for item in (*stack, fn))
        raise TypeError(f"typed endpoint dependency cycle: {chain}")

    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, localns=localns, include_extras=True)
    except Exception as exc:
        raise TypeError(f"could not resolve annotations for {fn!r}: {exc}") from exc
    arguments: list[_Argument] = []
    flattened: list[TypedBinding] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                f"typed endpoint parameter {parameter.name!r} must be positional-or-keyword "
                "or keyword-only"
            )
        annotation = hints.get(parameter.name, parameter.annotation)
        if annotation is _EMPTY:
            raise TypeError(
                f"typed endpoint parameter {parameter.name!r} needs Context or Annotated[...]"
            )
        type_, marker = _split_marker(annotation)
        if marker is None:
            if type_ is not Context:
                raise TypeError(
                    f"typed endpoint parameter {parameter.name!r} needs an explicit "
                    "Body, Form, Query, Path, Header, Cookie, or Depends marker"
                )
            arguments.append(_ContextArgument(parameter.name))
            continue
        if isinstance(marker, Depends):
            compiled_dependency = _compile_callable(
                marker.dependency,
                providers,
                localns=localns,
                stack=(*stack, fn),
                cache=cache,
            )
            arguments.append(
                _DependencyArgument(parameter.name, compiled_dependency, marker.use_cache)
            )
            flattened.extend(compiled_dependency.request_bindings)
            continue
        if isinstance(marker, Path) and parameter.default is not _EMPTY:
            raise TypeError(f"path parameter {parameter.name!r} cannot have a default")
        if isinstance(marker, Body) and parameter.default is not _EMPTY:
            raise TypeError(f"JSON body parameter {parameter.name!r} cannot have a default")
        if isinstance(marker, Body):
            media_type = _base_media_type(marker.media_type)
            if media_type != "application/json" and not media_type.endswith("+json"):
                raise TypeError(
                    f"typed JSON body {parameter.name!r} has unsupported media type "
                    f"{marker.media_type!r}"
                )
        if (
            isinstance(marker, Form)
            and _base_media_type(marker.media_type) not in _FORM_MEDIA_TYPES
        ):
            raise TypeError(
                f"typed form field {parameter.name!r} has unsupported media type "
                f"{marker.media_type!r}"
            )
        provider = resolve(providers, type_)
        target = _target(marker)
        if target in {"param", "header", "cookie"} and _is_multi_value(type_):
            raise TypeError(f"typed {target} parameter {parameter.name!r} must be scalar")
        binding = TypedBinding(
            parameter=parameter.name,
            target=target,
            external_name=_external_name(parameter.name, marker),
            type_=type_,
            marker=marker,
            required=parameter.default is _EMPTY,
            default=parameter.default,
            provider=provider,
            converter=converter_for(
                provider,
                type_,
                strings=target in {"query", "param", "header", "cookie", "form"},
            ),
        )
        arguments.append(_RequestArgument(binding))
        flattened.append(binding)

    request_bindings = _unique_bindings(flattened)
    bodies = [binding for binding in request_bindings if binding.target == "json"]
    forms = [binding for binding in request_bindings if binding.target == "form"]
    if len(bodies) > 1:
        raise TypeError("a typed endpoint may declare only one JSON body")
    if bodies and forms:
        raise TypeError("a typed endpoint cannot mix JSON body and form fields")
    form_media_types = {cast(Form, binding.marker).media_type for binding in forms}
    if len(form_media_types) > 1:
        raise TypeError("all typed form fields must use the same media type")

    compiled = _CompiledCallable(
        fn=fn,
        arguments=tuple(arguments),
        request_bindings=request_bindings,
        is_async=inspect.iscoroutinefunction(fn),
    )
    cache[fn] = compiled
    return compiled


async def _raw_value(c: Context, binding: TypedBinding, state: _RequestState) -> Any:
    name = binding.external_name
    if binding.target == "json":
        if state.body is _MISSING:
            try:
                state.body = await c.req.json()
            except Exception:
                raise HTTPException(
                    400,
                    title="Validation failed",
                    detail="request body is not valid JSON",
                ) from None
        return state.body
    if binding.target == "form":
        if state.form is _MISSING:
            try:
                state.form = await c.req.form_data()
            except Exception as exc:
                raise HTTPException(
                    400,
                    title="Validation failed",
                    detail=f"request form is invalid: {exc}",
                ) from exc
        assert name is not None
        if not state.form.has(name):
            return _MISSING
        return state.form.get_all(name) if _is_multi_value(binding.type_) else state.form.get(name)
    assert name is not None
    if binding.target == "query":
        params = c.req.url.search_params
        if not params.has(name):
            return _MISSING
        return params.get_all(name) if _is_multi_value(binding.type_) else params.get(name)
    if binding.target == "param":
        value = c.req.param(name)
        return _MISSING if value is None else value
    if binding.target == "header":
        value = c.req.header(name)
        return _MISSING if value is None else value
    value = c.req.cookies.get(name)
    return _MISSING if value is None else value


async def _resolve_request_value(c: Context, binding: TypedBinding, state: _RequestState) -> Any:
    raw = await _raw_value(c, binding, state)
    if raw is _MISSING:
        if not binding.required:
            return binding.default
        location = "body" if binding.external_name is None else binding.external_name
        raise HTTPException(
            400,
            title="Validation failed",
            detail=f"{binding.target}.{location}: field required",
        )
    try:
        return binding.converter(raw)
    except HTTPException:
        raise
    except Exception as exc:
        location = "body" if binding.external_name is None else binding.external_name
        raise HTTPException(
            400,
            title="Validation failed",
            detail=f"{binding.target}.{location}: {exc}",
        ) from exc


async def _call(
    compiled: _CompiledCallable,
    c: Context,
    state: _RequestState,
    dependency_cache: dict[Callable[..., Any], Any],
) -> Any:
    values: dict[str, Any] = {}
    for argument in compiled.arguments:
        if isinstance(argument, _ContextArgument):
            values[argument.parameter] = c
        elif isinstance(argument, _RequestArgument):
            values[argument.binding.parameter] = await _resolve_request_value(
                c, argument.binding, state
            )
        else:
            dependency = argument.dependency
            if argument.use_cache and dependency.fn in dependency_cache:
                value = dependency_cache[dependency.fn]
            else:
                value = await _call(dependency, c, state, dependency_cache)
                if argument.use_cache:
                    dependency_cache[dependency.fn] = value
            values[argument.parameter] = value

    if compiled.is_async:
        return await cast(Callable[..., Awaitable[Any]], compiled.fn)(**values)
    if sys.platform == "emscripten":
        return compiled.fn(**values)
    return await asyncio.to_thread(compiled.fn, **values)


@overload
def endpoint[**P, R](
    fn: Callable[P, R],
    *,
    status: int = 200,
    summary: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    responses: dict[int, Any] | None = None,
    operation_id: str | None = None,
    deprecated: bool = False,
    security: list[dict[str, list[str]]] | None = None,
    providers: list[SchemaProvider] | None = None,
) -> Callable[[Context], Awaitable[Response]]: ...


@overload
def endpoint[**P, R](
    fn: None = None,
    *,
    status: int = 200,
    summary: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    responses: dict[int, Any] | None = None,
    operation_id: str | None = None,
    deprecated: bool = False,
    security: list[dict[str, list[str]]] | None = None,
    providers: list[SchemaProvider] | None = None,
) -> Callable[
    [Callable[P, R]],
    Callable[[Context], Awaitable[Response]],
]: ...


def endpoint(
    fn: Callable[..., Any] | None = None,
    *,
    status: int = 200,
    summary: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    responses: dict[int, Any] | None = None,
    operation_id: str | None = None,
    deprecated: bool = False,
    security: list[dict[str, list[str]]] | None = None,
    providers: list[SchemaProvider] | None = None,
) -> Any:
    """Turn one explicit typed signature into binding, OpenAPI, and JSON.

    Put this decorator closest to the function, below ``@app.get(...)`` or
    another Hayate route decorator.
    """

    frame = inspect.currentframe()
    caller = None if frame is None else frame.f_back
    definition_locals = {} if caller is None else dict(caller.f_locals)
    del caller
    del frame

    def decorate(handler: Callable[..., Any]) -> Callable[[Context], Awaitable[Response]]:
        chain = providers if providers is not None else default_providers()
        cache: dict[Callable[..., Any], _CompiledCallable] = {}
        compiled = _compile_callable(
            handler,
            chain,
            localns=definition_locals,
            stack=(),
            cache=cache,
        )
        hints = get_type_hints(handler, localns=definition_locals, include_extras=True)
        return_type = hints.get("return", inspect.signature(handler).return_annotation)
        response_provider: SchemaProvider | None = None
        response_schema_type: Any | None = None
        if return_type is not _EMPTY and return_type is not Response:
            if isinstance(return_type, type) and issubclass(return_type, Response):
                response_schema_type = None
            else:
                response_provider = resolve(chain, return_type)
                response_schema_type = return_type
        if status == 204 and response_schema_type not in (None, type(None)):
            raise TypeError("a 204 typed endpoint cannot declare a response body")

        documented_responses = dict(responses or {})
        if status == 204:
            documented_responses.setdefault(204, None)
        elif response_schema_type is not None:
            documented_responses.setdefault(status, response_schema_type)

        @wraps(handler)
        async def wrapped(c: Context) -> Response:
            result = await _call(compiled, c, _RequestState(), {})
            if isinstance(result, Response):
                return result
            if status == 204:
                if result is not None:
                    raise TypeError("a 204 typed endpoint must return None or Response")
                return c.body(None, status=204)
            if response_provider is not None and response_schema_type is not None:
                validated = response_provider.converter(response_schema_type)(result)
                result = dump_with(response_provider, response_schema_type, validated)
            return c.json(result, status=status)

        setattr(wrapped, TYPED_BINDINGS_ATTR, compiled.request_bindings)
        if response_provider is not None and response_schema_type is not None:
            setattr(
                wrapped,
                TYPED_RESPONSES_ATTR,
                {status: (response_schema_type, response_provider)},
            )
        return describe(
            summary=summary,
            description=description,
            tags=tags,
            responses=documented_responses,
            operation_id=operation_id,
            deprecated=deprecated,
            security=security,
        )(wrapped)

    return decorate(fn) if fn is not None else decorate


__all__ = [
    "TYPED_BINDINGS_ATTR",
    "TYPED_RESPONSES_ATTR",
    "TypedBinding",
    "endpoint",
]
