"""Schema providers: type -> (JSON Schema 2020-12, referenced $defs).

The chain is guarded-import autodetection (DESIGN §3.2): msgspec Structs,
pydantic BaseModels, then raw JSON Schemas. Each provider
also supplies the validation converter, so ``validated()`` needs no separate
wiring.
"""

from __future__ import annotations

import contextlib
import math
import re
import sys
from collections.abc import Callable
from datetime import date, datetime, time
from enum import Enum
from types import UnionType
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Protocol,
    Required,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)
from uuid import UUID

from .parameters import Constraints


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
        if get_origin(type_) is Annotated:
            metadata = get_args(type_)[1:]
            if not all(isinstance(item, self._msgspec.Meta) for item in metadata):
                return False
        try:
            self._msgspec.json.schema_components((type_,), ref_template=_REF_PREFIX + "{name}")
        except (TypeError, ValueError):
            return False
        return True

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        schemas, components = self._msgspec.json.schema_components(
            (type_,), ref_template=_REF_PREFIX + "{name}"
        )
        return schemas[0], dict(components)

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: self._msgspec.convert(data, type_)

    def string_converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: self._msgspec.convert(data, type_, strict=False)

    def dump(self, _type: Any, value: Any) -> Any:
        return self._msgspec.to_builtins(value)


class PydanticProvider:
    def __init__(self) -> None:
        from pydantic import TypeAdapter

        self._adapter = TypeAdapter

    def supports(self, type_: Any) -> bool:
        if get_origin(type_) is Annotated and any(
            isinstance(item, Constraints) for item in get_args(type_)[1:]
        ):
            return False
        try:
            self._adapter(type_)
        except Exception:
            return False
        return True

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = self._adapter(type_).json_schema(
            ref_template=_REF_PREFIX + "{model}", mode="validation"
        )
        defs = raw.pop("$defs", {})
        try:
            from pydantic import BaseModel
        except ImportError:  # pragma: no cover - guarded by __init__
            BaseModel = None  # type: ignore[assignment,misc]
        if isinstance(type_, type) and BaseModel is not None and issubclass(type_, BaseModel):
            # The root model itself lands inline; hoist it so every model ref
            # points into components uniformly.
            name = type_.__name__
            defs[name] = raw
            return {"$ref": _REF_PREFIX + name}, defs
        return raw, defs

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        adapter = self._adapter(type_)
        return adapter.validate_python

    def string_converter(self, type_: Any) -> Callable[[Any], Any]:
        return self.converter(type_)

    def dump(self, type_: Any, value: Any) -> Any:
        return self._adapter(type_).dump_python(value, mode="json")


_NONE_TYPE = type(None)
_UNION_ORIGINS = (UnionType, Union)
_SEQUENCE_ORIGINS = (list, set, frozenset, tuple)
_MAPPING_ORIGINS = (dict,)


def _unwrap_required(type_: Any) -> Any:
    return get_args(type_)[0] if get_origin(type_) in (Required, NotRequired) else type_


def _constraint_metadata(type_: Any) -> tuple[Any, Constraints] | None:
    if get_origin(type_) is not Annotated:
        return None
    base, *metadata = get_args(type_)
    constraints = [item for item in metadata if isinstance(item, Constraints)]
    if not constraints:
        return None
    if len(constraints) != 1 or len(metadata) != 1:
        raise TypeError("Constraints cannot be combined with other Annotated metadata")
    return base, constraints[0]


def _constraint_kinds(type_: Any) -> set[str]:
    type_ = _unwrap_required(type_)
    metadata = _constraint_metadata(type_)
    if metadata is not None:
        type_, _ = metadata
    origin = get_origin(type_)
    args = get_args(type_)
    if type_ in (int, float):
        return {"number"}
    if type_ is str:
        return {"string"}
    if type_ is _NONE_TYPE:
        return set()
    if origin in _UNION_ORIGINS:
        return set().union(*(_constraint_kinds(item) for item in args))
    if origin is Literal:
        return {
            "string" if isinstance(item, str) else "number"
            for item in args
            if isinstance(item, (str, int, float)) and not isinstance(item, bool)
        }
    if isinstance(type_, type) and issubclass(type_, Enum):
        return {
            "string" if isinstance(member.value, str) else "number"
            for member in type_
            if isinstance(member.value, (str, int, float)) and not isinstance(member.value, bool)
        }
    return set()


def _validate_constraint_definition(type_: Any, constraints: Constraints) -> None:
    numeric = {
        "gt": constraints.gt,
        "ge": constraints.ge,
        "lt": constraints.lt,
        "le": constraints.le,
    }
    active_numeric = {name: value for name, value in numeric.items() if value is not None}
    active_string = {
        "min_length": constraints.min_length,
        "max_length": constraints.max_length,
        "pattern": constraints.pattern,
    }
    active_string = {name: value for name, value in active_string.items() if value is not None}
    kinds = _constraint_kinds(type_)
    if active_numeric and kinds != {"number"}:
        raise TypeError("numeric Constraints require an int, float, numeric Literal, or union")
    if active_string and kinds != {"string"}:
        raise TypeError("string Constraints require a str, string Literal, or union")
    if active_numeric and active_string:
        raise TypeError("numeric and string Constraints cannot be combined")
    for name, value in active_numeric.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            raise TypeError(f"Constraints.{name} must be a finite number")
    for name in ("min_length", "max_length"):
        value = getattr(constraints, name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise TypeError(f"Constraints.{name} must be a non-negative integer")
    if (
        constraints.min_length is not None
        and constraints.max_length is not None
        and constraints.min_length > constraints.max_length
    ):
        raise TypeError("Constraints.min_length cannot exceed max_length")
    if constraints.pattern is not None:
        try:
            re.compile(constraints.pattern)
        except re.error as exc:
            raise TypeError(f"Constraints.pattern is invalid: {exc}") from exc

    lower_bounds = [
        (value, exclusive)
        for value, exclusive in ((constraints.gt, True), (constraints.ge, False))
        if value is not None
    ]
    upper_bounds = [
        (value, exclusive)
        for value, exclusive in ((constraints.lt, True), (constraints.le, False))
        if value is not None
    ]
    if lower_bounds and upper_bounds:
        lower, lower_exclusive = max(lower_bounds, key=lambda item: (item[0], item[1]))
        upper, upper_exclusive = min(upper_bounds, key=lambda item: (item[0], not item[1]))
        if lower > upper or (lower == upper and (lower_exclusive or upper_exclusive)):
            raise TypeError("Constraints numeric lower bound must be below the upper bound")


def _apply_constraint_schema(schema: dict[str, Any], constraints: Constraints) -> dict[str, Any]:
    keywords = {
        "exclusiveMinimum": constraints.gt,
        "minimum": constraints.ge,
        "exclusiveMaximum": constraints.lt,
        "maximum": constraints.le,
        "minLength": constraints.min_length,
        "maxLength": constraints.max_length,
        "pattern": constraints.pattern,
    }
    return {**schema, **{name: value for name, value in keywords.items() if value is not None}}


def _enforce_constraints(value: Any, constraints: Constraints) -> Any:
    if value is None:
        return None
    if constraints.gt is not None and not value > constraints.gt:
        raise ValueError(f"expected a value greater than {constraints.gt}")
    if constraints.ge is not None and not value >= constraints.ge:
        raise ValueError(f"expected a value greater than or equal to {constraints.ge}")
    if constraints.lt is not None and not value < constraints.lt:
        raise ValueError(f"expected a value less than {constraints.lt}")
    if constraints.le is not None and not value <= constraints.le:
        raise ValueError(f"expected a value less than or equal to {constraints.le}")
    if constraints.min_length is not None and len(value) < constraints.min_length:
        raise ValueError(f"expected at least {constraints.min_length} characters")
    if constraints.max_length is not None and len(value) > constraints.max_length:
        raise ValueError(f"expected at most {constraints.max_length} characters")
    if constraints.pattern is not None and re.search(constraints.pattern, value) is None:
        raise ValueError(f"expected a value matching {constraints.pattern!r}")
    return value


def _json_type(values: list[Any]) -> str | None:
    kinds = {type(value) for value in values if value is not None}
    if not kinds:
        return "null"
    if len(kinds) != len({type(value) for value in values}):
        return None
    if kinds == {str}:
        return "string"
    if kinds <= {int}:
        return "integer"
    if kinds <= {int, float}:
        return "number"
    if kinds == {bool}:
        return "boolean"
    return None


def _stdlib_schema(type_: Any, seen: set[Any] | None = None) -> dict[str, Any]:
    type_ = _unwrap_required(type_)
    origin = get_origin(type_)
    args = get_args(type_)
    if origin is Annotated:
        metadata = _constraint_metadata(type_)
        if metadata is None:
            raise TypeError("stdlib schemas only interpret hayate_openapi.Constraints metadata")
        base, constraints = metadata
        _validate_constraint_definition(base, constraints)
        return _apply_constraint_schema(_stdlib_schema(base, seen), constraints)
    if type_ is Any:
        return {}
    if type_ is _NONE_TYPE:
        return {"type": "null"}
    if type_ is str:
        return {"type": "string"}
    if type_ is bool:
        return {"type": "boolean"}
    if type_ is int:
        return {"type": "integer"}
    if type_ is float:
        return {"type": "number"}
    if type_ is UUID:
        return {"type": "string", "format": "uuid"}
    if type_ is datetime:
        return {"type": "string", "format": "date-time"}
    if type_ is date:
        return {"type": "string", "format": "date"}
    if type_ is time:
        return {"type": "string", "format": "time"}
    if origin is Literal:
        values = list(args)
        schema: dict[str, Any] = {"enum": values}
        json_type = _json_type(values)
        if json_type is not None:
            schema["type"] = json_type
        return schema
    if origin in _UNION_ORIGINS:
        return {"anyOf": [_stdlib_schema(arg, seen) for arg in args]}
    if origin in _SEQUENCE_ORIGINS:
        if origin is tuple and args and args[-1] is not Ellipsis:
            return {
                "type": "array",
                "prefixItems": [_stdlib_schema(arg, seen) for arg in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        item_type = args[0] if args else Any
        schema = {"type": "array", "items": _stdlib_schema(item_type, seen)}
        if origin in (set, frozenset):
            schema["uniqueItems"] = True
        return schema
    if origin in _MAPPING_ORIGINS:
        key_type, value_type = args or (str, Any)
        if key_type not in (str, Any):
            raise TypeError("JSON object keys must be strings")
        return {"type": "object", "additionalProperties": _stdlib_schema(value_type, seen)}
    if isinstance(type_, type) and issubclass(type_, Enum):
        values = [member.value for member in type_]
        schema = {"enum": values}
        json_type = _json_type(values)
        if json_type is not None:
            schema["type"] = json_type
        return schema
    if is_typeddict(type_):
        active = set() if seen is None else seen
        if type_ in active:
            raise TypeError("recursive TypedDict schemas require msgspec or pydantic")
        active.add(type_)
        try:
            hints = get_type_hints(type_, include_extras=True)
            properties = {
                name: _stdlib_schema(_unwrap_required(annotation), active)
                for name, annotation in hints.items()
            }
        finally:
            active.remove(type_)
        schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        required = sorted(type_.__required_keys__)
        if required:
            schema["required"] = required
        return schema
    raise TypeError(f"stdlib provider does not support {type_!r}")


def _convert_bool(value: Any, *, strings: bool) -> bool:
    if isinstance(value, bool):
        return value
    if strings and isinstance(value, str):
        normalized = value.casefold()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    raise ValueError("expected a boolean")


def _stdlib_convert(type_: Any, value: Any, *, strings: bool = False) -> Any:
    type_ = _unwrap_required(type_)
    origin = get_origin(type_)
    args = get_args(type_)
    if origin is Annotated:
        metadata = _constraint_metadata(type_)
        if metadata is None:
            raise TypeError("stdlib converters only interpret hayate_openapi.Constraints metadata")
        base, constraints = metadata
        return _enforce_constraints(
            _stdlib_convert(base, value, strings=strings),
            constraints,
        )
    if type_ is Any:
        return value
    if type_ is _NONE_TYPE:
        if value is None:
            return None
        raise ValueError("expected null")
    if type_ is str:
        if isinstance(value, str):
            return value
        raise ValueError("expected a string")
    if type_ is bool:
        return _convert_bool(value, strings=strings)
    if type_ is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if strings and isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                pass
        raise ValueError("expected an integer")
    if type_ is float:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        if strings and isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        raise ValueError("expected a number")
    if type_ is UUID:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("expected a UUID") from None
    if type_ in (datetime, date, time):
        if isinstance(value, type_):
            return value
        if not isinstance(value, str):
            raise ValueError(f"expected an ISO {type_.__name__} string")
        try:
            return type_.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"expected an ISO {type_.__name__} string") from None
    if origin is Literal:
        for candidate in args:
            if value == candidate or (
                strings and isinstance(value, str) and value == str(candidate)
            ):
                return candidate
        raise ValueError(f"expected one of {args!r}")
    if origin in _UNION_ORIGINS:
        failures = []
        for candidate in args:
            try:
                return _stdlib_convert(candidate, value, strings=strings)
            except (TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise ValueError("value does not match any union member: " + "; ".join(failures))
    if origin in _SEQUENCE_ORIGINS:
        if not isinstance(value, list | tuple | set | frozenset):
            raise ValueError("expected an array")
        if origin is tuple and args and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ValueError(f"expected an array with {len(args)} items")
            converted = [
                _stdlib_convert(annotation, item, strings=strings)
                for annotation, item in zip(args, value, strict=True)
            ]
            return tuple(converted)
        item_type = args[0] if args else Any
        converted = [_stdlib_convert(item_type, item, strings=strings) for item in value]
        if origin is tuple:
            return tuple(converted)
        if origin is set:
            return set(converted)
        if origin is frozenset:
            return frozenset(converted)
        return converted
    if origin in _MAPPING_ORIGINS:
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        _, value_type = args or (str, Any)
        return {
            str(key): _stdlib_convert(value_type, item, strings=strings)
            for key, item in value.items()
        }
    if isinstance(type_, type) and issubclass(type_, Enum):
        for member in type_:
            if value == member.value or (
                strings and isinstance(value, str) and value == str(member.value)
            ):
                return member
        raise ValueError(f"expected a {type_.__name__} value")
    if is_typeddict(type_):
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        hints = get_type_hints(type_, include_extras=True)
        missing = sorted(type_.__required_keys__ - value.keys())
        if missing:
            raise ValueError("missing required fields: " + ", ".join(missing))
        extra = sorted(value.keys() - hints.keys())
        if extra:
            raise ValueError("unexpected fields: " + ", ".join(extra))
        return {
            key: _stdlib_convert(_unwrap_required(hints[key]), item, strings=strings)
            for key, item in value.items()
        }
    raise TypeError(f"stdlib provider does not support {type_!r}")


def _stdlib_dump(type_: Any, value: Any) -> Any:
    type_ = _unwrap_required(type_)
    origin = get_origin(type_)
    args = get_args(type_)
    if origin is Annotated:
        metadata = _constraint_metadata(type_)
        if metadata is None:
            raise TypeError("stdlib dump only interprets hayate_openapi.Constraints metadata")
        type_, _ = metadata
        origin = get_origin(type_)
        args = get_args(type_)
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if origin in _UNION_ORIGINS:
        for candidate in args:
            try:
                converted = _stdlib_convert(candidate, value)
            except (TypeError, ValueError):
                continue
            return _stdlib_dump(candidate, converted)
        return value
    if origin in _SEQUENCE_ORIGINS:
        if origin is tuple and args and args[-1] is not Ellipsis:
            return [
                _stdlib_dump(annotation, item) for annotation, item in zip(args, value, strict=True)
            ]
        item_type = args[0] if args else Any
        return [_stdlib_dump(item_type, item) for item in value]
    if origin in _MAPPING_ORIGINS:
        _, value_type = args or (str, Any)
        return {str(key): _stdlib_dump(value_type, item) for key, item in value.items()}
    if is_typeddict(type_):
        hints = get_type_hints(type_, include_extras=True)
        return {
            key: _stdlib_dump(_unwrap_required(hints[key]), item) for key, item in value.items()
        }
    return value


class StdlibProvider:
    """Dependency-free schemas and converters for ordinary Python API types."""

    def supports(self, type_: Any) -> bool:
        try:
            _stdlib_schema(type_)
        except TypeError:
            return False
        return True

    def schema(self, type_: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return _stdlib_schema(type_), {}

    def converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: _stdlib_convert(type_, data)

    def string_converter(self, type_: Any) -> Callable[[Any], Any]:
        return lambda data: _stdlib_convert(type_, data, strings=True)

    def dump(self, type_: Any, value: Any) -> Any:
        return _stdlib_dump(type_, value)


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
    chain.append(StdlibProvider())
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


def converter_for(provider: SchemaProvider, type_: Any, *, strings: bool) -> Callable[[Any], Any]:
    if strings:
        factory = getattr(provider, "string_converter", None)
        if factory is not None:
            return cast(Callable[[Any], Any], factory(type_))
    return provider.converter(type_)


def dump_with(provider: SchemaProvider, type_: Any, value: Any) -> Any:
    dump = getattr(provider, "dump", None)
    return value if dump is None else dump(type_, value)
