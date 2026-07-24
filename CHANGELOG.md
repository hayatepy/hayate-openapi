# Changelog

All notable changes to hayate-openapi are documented here.

## [0.2.0] - 2026-07-24

### Added

- Add OpenAPI 3.1 cookie, Bearer, and OAuth 2.0 security schemes, global
  security, per-operation security, and inference from hayate-auth middleware.
- Add explicit public-operation overrides with `@describe(security=[])`.
- Add multipart form request bodies and `binary_file()` schemas for uploads.
- Mark the distribution as typed and run strict mypy validation in CI.

### Changed

- Preserve decorated handler types through `@describe`.
- Validate generated security and multipart documents with the official
  OpenAPI validator.
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
