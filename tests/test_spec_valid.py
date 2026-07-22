"""Acceptance bar: the emitted document passes the official spec validator."""

from openapi_spec_validator import validate

from conftest import generate


def test_document_is_valid_openapi_31():
    validate(generate())
