from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hayate_openapi import generate_typescript_client


def _document() -> dict:
    return {
        "openapi": "3.1.1",
        "info": {"title": "Client", "version": "1"},
        "paths": {
            "/items/{item_id}": {
                "post": {
                    "operationId": "create-item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "tags",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "array", "items": {"type": "string"}},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ItemIn"}}
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ItemOut"}
                                }
                            },
                        },
                        "409": {"description": "Conflict"},
                    },
                }
            },
            "/uploads": {
                "post": {
                    "operationId": "upload",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"},
                                        "note": {"type": "string"},
                                    },
                                    "required": ["file"],
                                }
                            }
                        },
                    },
                    "responses": {"204": {"description": "No Content"}},
                }
            },
        },
        "components": {
            "schemas": {
                "ItemIn": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                "ItemOut": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        },
    }


def test_generates_deterministic_dependency_free_client() -> None:
    source = generate_typescript_client(_document(), types_import="./schema.js")

    assert source == generate_typescript_client(_document(), types_import="./schema.js")
    assert 'import type { paths } from "./schema.js";' in source
    assert '["create-item"]' in source
    assert 'method: "POST"' in source
    assert 'setContentType(headers, "application/json")' in source
    assert 'Omit<RequestBody<Operation1, "multipart/form-data">, "file">' in source
    assert '"file": Blob' in source
    assert "openapi-fetch" not in source
    assert "fetchImpl(url" in source
    assert "const runtimeInput = input as unknown as InputRecord;" in source
    assert "const request: RequestInit" in source


def test_omits_unused_multipart_helpers() -> None:
    document = copy.deepcopy(_document())
    del document["paths"]["/uploads"]

    source = generate_typescript_client(document)

    assert "function encodeMultipart" not in source
    assert "function appendFormValue" not in source


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda document: document["paths"]["/items/{item_id}"]["post"]["requestBody"][
                "content"
            ].update({"text/plain": {"schema": {"type": "string"}}}),
            "exactly one request media type",
        ),
        (
            lambda document: document["paths"]["/items/{item_id}"]["post"]["parameters"][1].update(
                {"schema": {"type": "object"}}
            ),
            "unsupported object encoding",
        ),
        (
            lambda document: document["paths"]["/items/{item_id}"]["post"]["responses"].update(
                {"default": {"description": "Fallback"}}
            ),
            "exact numeric response statuses",
        ),
        (
            lambda document: document["paths"]["/items/{item_id}"]["post"]["parameters"][1].update(
                {"style": "deepObject"}
            ),
            "unsupported query serialization",
        ),
        (
            lambda document: document["paths"]["/uploads"]["post"]["requestBody"]["content"][
                "multipart/form-data"
            ]["schema"]["properties"].update(
                {"metadata": {"type": "object", "properties": {"key": {"type": "string"}}}}
            ),
            "must be a scalar or scalar array",
        ),
    ],
)
def test_rejects_contracts_it_cannot_serialize(mutate, message: str) -> None:
    document = copy.deepcopy(_document())
    mutate(document)

    with pytest.raises(ValueError, match=message):
        generate_typescript_client(document)


def test_merges_path_level_parameters_with_operation_overrides() -> None:
    document = copy.deepcopy(_document())
    path_item = document["paths"]["/items/{item_id}"]
    operation = path_item["post"]
    path_parameter = operation["parameters"].pop(0)
    path_item["parameters"] = [path_parameter]

    source = generate_typescript_client(document)

    assert 'type Operation0 = paths["/items/{item_id}"]["post"];' in source
    assert 'let path = "/items/{item_id}";' in source


def test_rejects_ambiguous_cookie_arrays() -> None:
    document = copy.deepcopy(_document())
    operation = document["paths"]["/items/{item_id}"]["post"]
    operation["parameters"].append(
        {
            "name": "roles",
            "in": "cookie",
            "schema": {"type": "array", "items": {"type": "string"}},
        }
    )

    with pytest.raises(ValueError, match="cannot serialize cookie arrays"):
        generate_typescript_client(document)


def test_cli_writes_client_and_preserves_json_output(tmp_path: Path) -> None:
    from hayate_openapi.__main__ import main

    document = tmp_path / "openapi.json"
    client = tmp_path / "api-client.ts"
    assert (
        main(
            [
                "examples.conformance_app:app",
                "--title",
                "Client",
                "--version",
                "1",
                "--output",
                str(document),
                "--typescript-client",
                str(client),
                "--typescript-types-import",
                "./schema.js",
            ]
        )
        == 0
    )

    parsed = json.loads(document.read_text(encoding="utf-8"))
    assert parsed["openapi"] == "3.1.1"
    assert "/sessions" in parsed["paths"]
    source = client.read_text(encoding="utf-8")
    assert 'import type { paths } from "./schema.js";' in source
    assert '"post_sessions"' in source
