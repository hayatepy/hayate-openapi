"""hayate-openapi: OpenAPI 3.1 from what your app already knows."""

from .endpoint import endpoint
from .generate import OpenApi
from .parameters import Body, Constraints, Cookie, Depends, Form, Header, Path, Query
from .providers import (
    MsgspecProvider,
    PydanticProvider,
    RawSchemaProvider,
    SchemaProvider,
    StdlibProvider,
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
from .typescript import generate_typescript_client

__version__ = "0.8.2"

__all__ = [
    "Body",
    "Constraints",
    "Cookie",
    "Depends",
    "Form",
    "Header",
    "MsgspecProvider",
    "OpenApi",
    "Path",
    "PydanticProvider",
    "Query",
    "RawSchemaProvider",
    "SchemaProvider",
    "SecurityRequirement",
    "SecurityScheme",
    "StdlibProvider",
    "__version__",
    "bearer_security",
    "binary_file",
    "cookie_security",
    "default_providers",
    "describe",
    "endpoint",
    "generate_typescript_client",
    "oauth2_authorization_code_security",
    "validated",
]
