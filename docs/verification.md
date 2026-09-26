# Verification record

Measurements were collected on 26 September 2026. Reports below describe executed checks, not targets. Each evaluation and benchmark report carries its source and data hashes; artifacts from different checks need not have identical packaging metadata.

## Functional and security checks

The Linux suite passed **120 tests**, including generated OpenAPI cases, real SQLite/FTS queries and migrations, property/metamorphic checks, API/CLI consistency, mocked external contracts, MCP stdio subprocess exchanges, concurrent job claims, process-crash recovery, and rollback drills. The Windows full suite and subsequent changed-module checks also passed.

Security cases cover untrusted model output and metadata, unknown evidence, SSRF destinations and redirects, malformed and oversized responses, cross-origin writes, secret/query redaction, unsafe backup paths, and changed artifacts during restoration. Network-denying tests intentionally produce a socket-blocking warning. The installed FastAPI/Starlette combination also emits a TestClient deprecation warning; neither warning is suppressed as a correctness failure.

The frontend passed **five component tests** and **two Chromium journeys**. Browser checks exercise search, comparison, collections, export, 90-case evaluations, malformed mock responses, catalog refreshes, atomic version activation, backup manifests, keyboard focus, mobile overflow, and axe accessibility rules. Automated checks do not constitute a full manual accessibility audit.

Ruff, formatting, strict mypy, TypeScript, ESLint, OpenAPI schema drift, workflow syntax, and dependency audits passed locally. Python and npm audits reported no known vulnerabilities at the time of the run; the unpublished local package has no vulnerability-database entry.

## Coverage and mutation testing

| Scope | Branch coverage | Gate |
| --- | ---: | ---: |
| Overall Python | 94.74% | 85% |
| Ranking | 100.00% | 90% |
| Evaluation | 95.00% | 90% |
| Data recovery | 95.24% | 90% |
| Application releases | 100.00% | 90% |
| Configuration versioning | 100.00% | 90% |

Combined line/branch coverage was 98.20%; this is distinct from branch coverage. [Raw coverage summary](verification/coverage-summary.json).

All **13 targeted mutations** were detected, with no exclusions. The campaign changes authentication/deprecation filters, evidence/freshness ordering, metric math, the NDCG gate, backup integrity/path checks, the stopped-service guard, wheel pinning, and schema compatibility. An unmodified baseline must pass first; parse/import failures and timeouts are not credited as detected faults. A surviving backup-whitelist mutation prompted an additional test for an unexpected but checksummed file inside the bundle. [Raw mutation report](verification/mutation-report.json).

This is an explicit, bounded campaign. It does not claim exhaustive mutation coverage of every expression or branch.

## Golden mock evaluation

[Recorded profile results](verification/fixture-evaluations.json) contain all seven profiles and paired comparisons. Reproduce them with:

```sh
uv run python scripts/record_fixture_results.py
```

The script writes full per-run JSON/HTML files beneath `dist/fixture-evaluations/reports` and a summary to `dist/fixture-evaluations.json`. Thirty abstention cases are excluded from NDCG/recall/MRR averages because they contain no relevant items; all 90 cases contribute to abstention, constraint, and fallback metrics. Bootstrap resampling uses intent groups and a fixed seed. The held-out split is by intent, not a claim of independent human assessment.

The deliberately degraded mock fast profile fails the paired regression gate. Failure presets pass the ranking-quality gate through deterministic fallback while separately reporting their operational failures. Live model quality, vendor capabilities, billing, and provider routing remain unmeasured.

## HTTP load, stress, and bounded soak

Reference environment: Ubuntu 24.04 under WSL2 on Windows 11; Linux kernel 6.6.87.2; AMD Ryzen 7 6800HS, eight cores/16 logical CPUs; Python 3.13.1; SQLite 3.47.1. Application data was on the Linux filesystem. This was a shared workstation, not a dedicated hosted runner.

The dataset contains 10,000 records made by repeating the synthetic catalog with unique IDs. Six query shapes include broad, narrow, and absent matches; one-third of requests apply an authentication constraint. Requests go through real loopback HTTP. Open-loop latency includes scheduling delay to avoid concealing overload.

| Phase | Scheduled rate | Duration | Requests | p95 | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Load | 10 RPS | 30 s | 300 | 74.9 ms | 0 |
| Stress | 50 RPS | 10 s | 500 | 97.9 ms | 0 |
| Soak | 10 RPS | 180 s | 1,800 | 70.0 ms | 0 |

The 500 ms p95 target at 10 RPS passed. Resident memory declined from 176.0 MB after stress to 127.3 MB after the soak. This short run does not prove an absence of memory leaks or establish a saturation limit. [Raw benchmark](verification/benchmark-linux.json).

## Distribution and platform limits

Real Windows and Linux wheel drills install two versioned artifacts from offline wheelhouses, serve the bundled interface over HTTP, switch versions, roll back, preserve the catalog, and verify query redaction. Unit/integration recovery tests additionally exercise saved-collection preservation, pre-restore backups, previous-schema upgrades, and incompatible targets.

macOS installation and test checks passed on the preceding packaging revision (`ff26ec7`). The final source has not been rerun on macOS. GitHub Actions was disabled at the owner's request after account billing prevented hosted jobs from starting. Historical red checks are retained; they are not counted as successful runs. The checked-in workflows remain available as executable definitions.

Registry/GitHub/model/probe adapters and monitoring deployment have no live verification. The local telemetry paths, metrics, event retention, fallback events, and alert conditions are covered by tests.
