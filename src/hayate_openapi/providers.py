"""Schema providers: type -> (JSON Schema 2020-12, referenced $defs).

The chain is guarded-import autodetection (DESIGN §3.2): msgspec Structs,
pydantic BaseModels, then raw dicts passed through untouched. Each provider
also supplies the validation converter, so ``validated()`` needs no separate
wiring.
"""

from __future__ import annotations

import contextlib
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
    """A plain dict is taken as literal JSON Schema: documented, not enforced."""

    def supports(self, type_: Any) -> bool:
        return isinstance(type_, dict)

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(type_), {}

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: data


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
