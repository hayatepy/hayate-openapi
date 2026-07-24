"""hayate-openapi: OpenAPI 3.1 from what your app already knows."""

from .generate import OpenApi
from .providers import (
    MsgspecProvider,
    PydanticProvider,
    RawSchemaProvider,
    SchemaProvider,
    default_providers,
)
from .tags import describe, validated

__version__ = "0.1.1"

__all__ = [
    "MsgspecProvider",
    "OpenApi",
    "PydanticProvider",
    "RawSchemaProvider",
    "SchemaProvider",
    "__version__",
    "default_providers",
    "describe",
    "validated",
]
