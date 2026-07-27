"""Schema providers: type -> (JSON Schema 2020-12, referenced $defs).

The chain is guarded-import autodetection (DESIGN §3.2): msgspec Structs,
pydantic BaseModels, then raw JSON Schemas. Each provider
also supplies the validation converter, so ``validated()`` needs no separate
wiring.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import Any, Protocol


class SchemaProvider(Protocol):
    def supports(self, type_: Any) -> bool: ...

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (schema for the type, {name: definition} it references)."""
        ...

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        """A callable that validates/converts raw data (raises on failure)."""
        ...


_REF_PREFIX = "#/components/schemas/"


def _compile_raw_schema(schema: dict[str, Any]) -> Callable[[Any], Any]:
    # jsonschema imports rpds, whose Pyodide extension requests entropy while
    # loading. Cloudflare Workers permits that inside a request, not while the
    # application module is evaluated in global scope.
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import best_match

    Draft202012Validator.check_schema(schema)
    compiled = Draft202012Validator(schema, format_checker=FormatChecker())

    def validate(data: Any) -> Any:
        error = best_match(compiled.iter_errors(data))
        if error is not None:
            raise ValueError(f"{error.json_path}: {error.message}")
        return data

    return validate


class MsgspecProvider:
    def __init__(self) -> None:
        import msgspec

        self._msgspec = msgspec

    def supports(self, type_: Any) -> bool:
        return isinstance(type_, type) and issubclass(type_, self._msgspec.Struct)

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        schemas, components = self._msgspec.json.schema_components(
            (type_,), ref_template=_REF_PREFIX + "{name}"
        )
        return schemas[0], dict(components)

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: self._msgspec.convert(data, type_)


class PydanticProvider:
    def __init__(self) -> None:
        from pydantic import TypeAdapter

        self._adapter = TypeAdapter

    def supports(self, type_: Any) -> bool:
        try:
            from pydantic import BaseModel
        except ImportError:  # pragma: no cover - guarded twice
            return False
        return isinstance(type_, type) and issubclass(type_, BaseModel)

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = self._adapter(type_).json_schema(
            ref_template=_REF_PREFIX + "{model}", mode="validation"
        )
        defs = raw.pop("$defs", {})
        # The root model itself lands inline; hoist it so every model ref
        # points into components uniformly.
        name = type_.__name__
        defs[name] = raw
        return {"$ref": _REF_PREFIX + name}, defs

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        adapter = self._adapter(type_)
        return adapter.validate_python


class RawSchemaProvider:
    """A plain dict is compiled as literal JSON Schema Draft 2020-12."""

    def supports(self, type_: Any) -> bool:
        return isinstance(type_, dict)

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(type_), {}

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        compiled = None if sys.platform == "emscripten" else _compile_raw_schema(type_)

        def validate(data: Any) -> Any:
            nonlocal compiled
            converter = compiled
            if converter is None:
                converter = _compile_raw_schema(type_)
                compiled = converter
            return converter(data)

        return validate


def default_providers() -> list[SchemaProvider]:
    chain: list[SchemaProvider] = []
    with contextlib.suppress(ImportError):
        chain.append(MsgspecProvider())
    with contextlib.suppress(ImportError):
        chain.append(PydanticProvider())
    chain.append(RawSchemaProvider())
    return chain


def resolve(providers: list[SchemaProvider], type_: Any) -> SchemaProvider:
    for provider in providers:
        if provider.supports(type_):
            return provider
    raise TypeError(
        f"no schema provider supports {type_!r}: install msgspec or pydantic, "
        "or pass a raw JSON Schema dict"
    )
