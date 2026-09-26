# Optional integrations

The browser is an offline showcase. Optional external adapters are available through the shared Python services and CLI. They are tested against controlled HTTP/MCP responses; **no live registry, repository, model-provider, or remote-probe validation is claimed**.

All network adapters require `SORTER_MODE=live` and an explicit JSON-array hostname allowlist in `SORTER_ALLOWED_HOSTS`. Credentials alone do not enable calls. URLs must use HTTPS on port 443 with no embedded credentials or fragments. Requests do not follow redirects or inherit proxy environment variables. DNS destinations must resolve entirely to public addresses; real connections pin a validated address while preserving the original TLS hostname.

## Catalog sources

For the registry adapter, explicitly allow `registry.modelcontextprotocol.io`, then run `mcp-sorter catalog sync`. The current adapter consumes `/v0.1/servers`, bounds responses and pagination, and rejects malformed records. A refresh containing rejected records retains the current catalog. Fields absent from the response remain unknown or uncategorized; descriptions are source declarations, not independently verified evidence.

The GitHub adapter is the Python function `mcp_sorter.sources.fetch_repository(owner, repository, settings)`. It requires `api.github.com` in the allowlist and returns bounded repository metadata. It does not automatically enrich or replace catalog records. There is no OAuth sign-in or background GitHub crawler.

## OpenRouter and LiteLLM

The following shell example uses placeholders. Replace the model and key only if live calls are intended:

```sh
export SORTER_MODE=live
export SORTER_ALLOWED_HOSTS='["openrouter.ai"]'
export SORTER_OPENROUTER_MODEL='provider/model-id'
export OPENROUTER_API_KEY='replace-me'
export SORTER_SPENDING_LIMIT_USD=1
export SORTER_REQUEST_RESERVATION_USD=0.10
mcp-sorter rank "github pull requests" --profile openrouter
```

On PowerShell, set each variable with `$env:VARIABLE = 'value'`. Seed a local catalog with `mcp-sorter demo` before switching to live mode, or explicitly refresh an approved source.

LiteLLM uses `SORTER_LITELLM_MODEL`, `SORTER_LITELLM_URL`, `LITELLM_API_KEY`, and `--profile litellm`. Its hostname must also be explicitly allowed and resolve publicly over HTTPS. A loopback or private-network proxy is rejected by this adapter's network policy.

Both adapters request structured output. Returned candidates must preserve the supplied candidate set, cite only supplied evidence IDs, and use the allowed reason code. The UI's explanation text is generated from validated catalog facts. Invalid JSON/schema, unknown candidates, bad evidence, rate limits, timeouts, and provider errors use deterministic fallback. Provider fallback routing is disabled in the request; the returned route is still marked unverified.

The local budget is a persistent **reservation ledger**, not a verified provider billing cap. Every attempted request reserves the configured amount; reservations are retained on failure and survive restarts. Set the reservation conservatively for the selected model and token limit. Actual provider charges may differ. Available usage/route metadata is recorded with results; live quality and actual spend have not been measured for this release.

## Approved MCP probes

Set both the hostname allowlist and `SORTER_APPROVED_PROBE_ENDPOINTS` to the exact operator-controlled HTTPS endpoint, then run:

```sh
mcp-sorter probe https://mcp.example.com/mcp
```

The example endpoint is a placeholder. Probes initialize the connection and list declared tools, resources, and prompts only. They never execute tools, install packages, or run server-supplied commands. Responses, capability counts, names, sessions, and total elapsed time are bounded. Additional pages are reported as truncated.

The bounded adapter supports JSON responses for MCP protocol versions `2025-03-26`, `2025-06-18`, and `2025-11-25`. Persistent SSE responses and other negotiated protocol versions are rejected. Remote declarations remain unverified. These limits are intentional and visible in the returned result.
