# Grafana MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents on the
MSPbots Agent Platform live, read-first access to a Grafana instance: dashboards, folders,
datasources (including running a query), alerting rules and contact points, and annotations.

It runs as a stateless HTTP service behind the MSPbots MCP gateway. The gateway sends each
tenant's Grafana base URL and Service Account token as request headers on every call; the server
keeps nothing between requests.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Meaning |
|---|---|---|
| `X-Grafana-Base-Url` | Yes | Base URL of the target Grafana instance, e.g. `https://acme.grafana.net`. `https://` or `http://` (self-hosted instances on a private network are common). |
| `X-Grafana-Token` | Yes | Grafana Service Account token, sent upstream as `Authorization: Bearer <token>`. Create it in Grafana under **Administration → Service accounts**. Role **Viewer** covers every read tool; **Editor** (or higher) is required for `grafana_create_annotation`. |

Header names are case-insensitive. `initialize` and `tools/list` work even without these headers
(the platform's admin console refreshes a connector's tool catalog this way); a tool call made
without them returns a structured `not_configured` error instead of a generic failure, and makes
no outbound call to Grafana. A `X-Grafana-Base-Url` that isn't a valid absolute `http(s)` URL
produces `invalid_argument` on the next tool call (`initialize` / `tools/list` still work).

## Tools

| Area | Tools |
|---|---|
| Dashboards | `grafana_search_dashboards`, `grafana_get_dashboard`, `grafana_list_folders` |
| Datasources | `grafana_list_datasources`, `grafana_get_datasource`, `grafana_query_datasource` |
| Alerting | `grafana_list_alert_rules`, `grafana_get_alert_rule`, `grafana_list_contact_points` |
| Annotations | `grafana_search_annotations`, `grafana_create_annotation` |
| Diagnostics | `grafana_get_health` |

`grafana_create_annotation` is the only write tool; everything else is read-only
(`readOnlyHint=true`). It is additive only (never updates or deletes) and not idempotent --
Grafana does not dedupe repeated calls, so do not retry it on an ambiguous outcome.

Behaviour notes:

- Every tool returns a JSON string. Errors use one envelope:
  `{"error": {"code": "...", "message": "...", "retryable": false}}` with `code` one of
  `not_configured`, `unauthorized`, `not_found`, `invalid_argument`, `rate_limited`, `upstream_error`.
- List tools return `{"items": [...], "has_more": bool}`. `has_more` is detected by asking Grafana
  for one row more than `limit` (default 50, hard cap 200) where Grafana supports a `limit` param;
  `grafana_list_alert_rules` and `grafana_list_contact_points` use Grafana's provisioning API,
  which has no `limit` or folder filter, so both the folder scope and the page size are applied
  after fetching the full list.
- `grafana_get_dashboard` returns a compact summary by default (title/uid/tags/folder/panels);
  pass `include_raw=true` for the full upstream dashboard JSON. Either shape is capped at 20,000
  characters -- an oversized raw dashboard comes back with `truncated: true` rather than cut-off
  JSON.
- `grafana_get_datasource` / `grafana_list_datasources` never return a datasource's secret fields
  (`secureJsonFields`, `basicAuthPassword`, ...); `grafana_list_contact_points` never returns a
  contact point's `settings` (which can carry webhook URLs or SMTP credentials).
- `grafana_query_datasource`'s `query` argument holds the datasource-specific fields (e.g.
  `{"expr": "up"}` for Prometheus); the tool always sets `refId` and `datasource` itself.
- `grafana_get_health` works without a token -- Grafana's own `/api/health` needs none -- but
  still requires `X-Grafana-Base-Url` so the tool knows which instance to ask.
- Transient failures (429, 5xx, connection errors, timeouts) are retried with bounded backoff,
  honouring `Retry-After`; total time per tool call stays under 120s.
- This connector targets Grafana's documented legacy `/api/*` HTTP API, not the newer namespaced
  `/apis/*.grafana.app/v1` surface, which is confirmed only on recent Grafana versions. The legacy
  surface is documented as not being disabled.

## Run locally

```bash
uv sync
uv run grafana-mcp            # listens on 0.0.0.0:8080
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "X-Grafana-Token: $GRAFANA_TOKEN" -H "X-Grafana-Base-Url: $GRAFANA_BASE_URL" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Environment variables (all optional): `MCP_HTTP_HOST` (default `0.0.0.0`), `MCP_HTTP_PORT`
(default `8080`), `MCP_TRANSPORT` (`http` or `stdio`, default `http`).

## License

Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
