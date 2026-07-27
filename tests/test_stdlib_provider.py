from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

import pytest

from hayate_openapi import StdlibProvider


class State(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class StandardRecord(TypedDict):
    id: UUID
    state: State
    created_at: datetime
    due_on: NotRequired[date | None]
    labels: list[str]
    mode: Literal["safe", "fast"]


def test_stdlib_provider_covers_portable_api_types_without_optional_dependencies():
    provider = StdlibProvider()
    schema, definitions = provider.schema(StandardRecord)

    assert definitions == {}
    assert schema["required"] == ["created_at", "id", "labels", "mode", "state"]
    assert schema["properties"]["id"] == {"type": "string", "format": "uuid"}
    assert schema["properties"]["state"] == {"enum": ["open", "closed"], "type": "string"}
    assert schema["properties"]["created_at"] == {
        "type": "string",
        "format": "date-time",
    }
    assert schema["properties"]["due_on"] == {
        "anyOf": [
            {"type": "string", "format": "date"},
            {"type": "null"},
        ]
    }
    assert schema["properties"]["labels"] == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_stdlib_provider_converts_and_dumps_typed_dicts():
    provider = StdlibProvider()
    identifier = UUID("550e8400-e29b-41d4-a716-446655440000")
    converted = provider.converter(StandardRecord)(
        {
            "id": str(identifier),
            "state": "open",
            "created_at": "2026-07-27T12:00:00+00:00",
            "labels": ["oss"],
            "mode": "safe",
        }
    )

    assert converted["id"] == identifier
    assert converted["state"] is State.OPEN
    assert converted["created_at"] == datetime(2026, 7, 27, 12, tzinfo=UTC)
    assert provider.dump(StandardRecord, converted) == {
        "id": str(identifier),
        "state": "open",
        "created_at": "2026-07-27T12:00:00+00:00",
        "labels": ["oss"],
        "mode": "safe",
    }


@pytest.mark.parametrize(
    ("type_", "raw", "expected"),
    [
        (int, "42", 42),
        (float, "1.5", 1.5),
        (bool, "true", True),
        (bool, "off", False),
        (list[int], ["1", "2"], [1, 2]),
        (time, "12:30:00", time(12, 30)),
    ],
)
def test_stdlib_string_converter_matches_http_text_inputs(type_, raw, expected):
    assert StdlibProvider().string_converter(type_)(raw) == expected


def test_stdlib_provider_rejects_missing_extra_and_invalid_fields():
    converter = StdlibProvider().converter(StandardRecord)
    with pytest.raises(ValueError, match="missing required"):
        converter({})
    with pytest.raises(ValueError, match="unexpected fields"):
        converter(
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "state": "open",
                "created_at": "2026-07-27T12:00:00+00:00",
                "labels": [],
                "mode": "safe",
                "secret": True,
            }
        )


def test_nullable_literal_does_not_emit_a_contradictory_scalar_type():
    schema, _ = StdlibProvider().schema(Literal["ready", None])
    assert schema == {"enum": ["ready", None]}
