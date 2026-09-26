# v0.1.0

The first offline showcase release of MCP Server Sorter.

- Browse 30 synthetic integrations with deterministic BM25 search, metadata filters, inspectable evidence, and comparisons of up to four servers.
- Save collections, export/import pinned selections, compare catalog snapshots, and activate catalog/configuration versions atomically.
- Run 90 versioned golden mock scenarios across seven baseline/provider profiles, inspect per-case failures, and compare immutable reports.
- Expose the shared application through a bundled React interface, REST `/api/v1`, Typer commands, and read-only MCP stdio tools.
- Inspect durable local jobs, structured logs, local OpenTelemetry traces/metrics, Prometheus metrics, fallback events, and alert conditions.
- Create and verify SQLite backups, restore to a new directory while preserving newer collections, and stage/activate/roll back pinned application wheels.
- Install the bundled wheel or load the versioned Linux amd64 container archive. Windows/Linux wheel rollback and container/Compose rollback were exercised with the released artifacts.

The default runtime makes no external calls and needs no credentials or Docker. External adapters are opt-in and contract-tested with controlled responses. Catalog metadata and provider behavior are illustrative. Live model quality is unmeasured.

Verification includes 120 Python tests on both Windows and Linux, five component tests, two Chromium journeys with axe checks, 94.74% Python branch coverage, and detection of 13 targeted mutations. The 10,000-record Linux benchmark recorded 74.9 ms p95 at 10 RPS, with zero errors across load, stress, and a three-minute soak. See the [verification record](verification.md) for raw reports, scope, and platform limits.

Release assets include the wheel, compressed container archive, SHA-256 checksums, an artifact manifest, and recorded verification results. The image archive loads as `mcp-server-sorter:0.1.0`; no container registry account is required.

GitHub Actions remains disabled at the owner's request. This release uses the recorded local verification. macOS checks cover the preceding packaging revision, not the final source. This application is intended for a trusted local machine and does not include multi-user authentication.
