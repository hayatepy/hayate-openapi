# Changelog

All notable changes to hayate-openapi are documented here.

## Unreleased

## [0.4.2] - 2026-07-27

### Fixed

- Defer JSON Schema compiler import and initialization until the first
  matching request on Emscripten/Pyodide, then cache it. Ordinary Python
  runtimes retain registration-time schema checks, while Cloudflare Workers
  no longer request forbidden entropy during module-global evaluation.

## [0.4.1] - 2026-07-27

### Fixed

- Compile raw JSON Schema contracts with the Draft 2020-12 validator and
  format checking instead of projecting them into OpenAPI without runtime
  enforcement. Invalid bodies, parameters, headers, and cookies now become
  RFC 9457 validation responses through the same contract the generator emits.
- Require Hayate 0.12.1 so route validators also run on the optimized native
  Workers path when an application has no global middleware.

## [0.4.0] - 2026-07-27

### Added

- Project `param`, `header`, and `cookie` validators into OpenAPI parameters,
  reusing the same schema contract enforced at runtime.
- Verify the installed distribution metadata and public `__version__` in the
  ordinary test suite so release version drift is caught before tagging.

### Changed

- Reject path properties that do not exist in the route, duplicate header or
  cookie parameters, and reserved header parameters that OpenAPI would ignore
  instead of emitting misleading contracts.

## [0.3.1] - 2026-07-26

### Added

- Add a locked CI interoperability gate that emits a representative OpenAPI
  3.1.1 document, validates it, and generates TypeScript types with
  `openapi-typescript` 7.13.0.

### Changed

- Document why the generator remains on the fully tested OpenAPI 3.1.1
  interoperability profile while downstream type generation does not yet
  advertise OpenAPI 3.2 support.
- Link the canonical ecosystem start page, production golden app, and tested
  compatibility evidence from the published package description.

## [0.3.0] - 2026-07-25

### Added

- Serve an interactive Scalar API reference at `/docs` by default when
  `OpenApi.register()` mounts the OpenAPI document.
- Allow the docs route to be disabled or use a same-origin, self-hosted Scalar
  script without adding a Python runtime dependency.

### Security

- Pin the default Scalar browser bundle to an immutable version with
  Subresource Integrity, avoid inline JavaScript, escape all generated HTML
  configuration, and send a restrictive Content Security Policy.
- Disable Scalar's telemetry, external client, sharing, deployment,
  MCP-generation, developer-tools, and AI-agent integrations in the generated
  page.
- Exclude the OpenAPI JSON and docs implementation routes from the generated
  application schema.

## [0.2.0] - 2026-07-24

### Added

- Add OpenAPI 3.1 cookie, Bearer, and OAuth 2.0 security schemes, global
  security, per-operation security, and inference from hayate-auth middleware.
- Add explicit public-operation overrides with `@describe(security=[])`.
- Add multipart form request bodies and `binary_file()` schemas for uploads.
- Mark the distribution as typed and run strict mypy validation in CI.

### Changed

- Preserve decorated handler types through `@describe`.
- Validate generated security and multipart documents with
  `openapi-spec-validator`.
- Reject duplicate operations, ambiguous path templates, duplicate operation
  IDs, and conflicting component names instead of emitting misleading specs.
- Audit locked dependencies on every change and publish an SPDX SBOM plus
  GitHub build and SBOM attestations with each release.

## [0.1.1] - 2026-07-24

### Changed

- Align package metadata and the protected release path.

## [0.1.0] - 2026-07-23

### Added

- Generate OpenAPI 3.1 documents from hayate routes, validators, and response
  annotations with msgspec, Pydantic, and raw JSON Schema providers.
