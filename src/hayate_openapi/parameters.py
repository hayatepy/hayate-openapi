"""Explicit ``typing.Annotated`` markers for typed Hayate endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Constraints:
    """Dependency-free JSON Schema constraints for ``typing.Annotated``."""

    gt: int | float | None = None
    ge: int | float | None = None
    lt: int | float | None = None
    le: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class Body:
    """Bind the JSON request body to the annotated parameter."""

    media_type: str = "application/json"
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Form:
    """Bind one URL-encoded or multipart form field."""

    alias: str | None = None
    media_type: str = "application/x-www-form-urlencoded"
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Query:
    """Bind one query-string field."""

    alias: str | None = None
    description: str | None = None
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class Path:
    """Bind one decoded route parameter."""

    alias: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Header:
    """Bind one Fetch-combined request header."""

    alias: str | None = None
    description: str | None = None
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class Cookie:
    """Bind one request cookie."""

    alias: str | None = None
    description: str | None = None
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class Depends:
    """Inject a callable result, cached once per request by default."""

    dependency: Callable[..., Any]
    use_cache: bool = True


type RequestMarker = Body | Form | Query | Path | Header | Cookie


__all__ = ["Body", "Constraints", "Cookie", "Depends", "Form", "Header", "Path", "Query"]
