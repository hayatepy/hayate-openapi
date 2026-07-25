"""hayate-openapi: OpenAPI 3.1 from what your app already knows."""

from .generate import OpenApi
from .providers import (
    MsgspecProvider,
    PydanticProvider,
    RawSchemaProvider,
    SchemaProvider,
    default_providers,
)
from .security import (
    SecurityRequirement,
    SecurityScheme,
    bearer_security,
    cookie_security,
    oauth2_authorization_code_security,
)
from .tags import binary_file, describe, validated

__version__ = "0.3.0"

__all__ = [
    "MsgspecProvider",
    "OpenApi",
    "PydanticProvider",
    "RawSchemaProvider",
    "SchemaProvider",
    "SecurityRequirement",
    "SecurityScheme",
    "__version__",
    "bearer_security",
    "binary_file",
    "cookie_security",
    "default_providers",
    "describe",
    "oauth2_authorization_code_security",
    "validated",
]
