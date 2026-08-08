# Security posture

What is actually enforced, what is deliberately not, and where each control
lives. Written to be defended: every "yes" below points at code or a test,
and every "no" is a conscious scope decision rather than an oversight.

**One-line summary:** this is a **local/internal-network tool** with real
structural controls (allowlisted read-only tools, server-side execution,
path jails, input bounds, dependency scanning) and deliberately unbuilt
perimeter controls (SSO, rate limiting, content sanitisation) that a
production deployment would add at the gateway.

## Threat model

| Actor | Trusted? | Notes |
|---|---|---|
| The operator running the app | Yes | Sets `.env`, chooses MCP servers |
| The LLM provider | Partly | Sees prompts and documents sent to it; picked per deployment |
| **The model's output** | **No** | Treated as untrusted input: tool names are allowlisted, arguments are schema-validated, results are strings, nothing is `eval`'d |
| **Uploaded documents** | **No longer** | Since documents are added at runtime, corpus content is user-supplied — see prompt injection below |
| Fetched web pages | No | `fetch_url` output is text pasted into the prompt; treat as hostile |
| MCP servers | Yes, as dependencies | Same trust model as a pip package: run trusted ones |
| Other users of the same instance | Shared | Single-tenant by design — see "not built" |

## What is enforced

### Authentication
Optional bearer token (`ASSISTANT_AUTH_TOKEN`). When set, mutating and
read-sensitive routes require `Authorization: Bearer <token>`, and the chat
WebSocket requires `?token=` (browsers cannot set WS headers). Deliberately
left open: `/api/info`, `/api/health`, `/healthz`, `/metrics` — the UI needs
the first two before authenticating, and none carries conversation content.
*Note:* `/metrics` does expose token counts and spend; put it behind your
ingress in a shared environment.
→ [routes.py](../../src/assistant/api/routes.py) `require_token`,
[ws.py](../../src/assistant/api/ws.py); tested for both HTTP and WS.

### The model can only do allowlisted, read-only things
Every tool call goes through one seam
([tools/base.py](../../src/assistant/agent/tools/base.py) `Tool.run`):
unknown tool names are rejected, arguments are JSON-schema-shaped,
execution is server-side, and a crash becomes an `error:` *result* rather
than an exception. No tool writes to the filesystem, executes shell
commands, or mutates state. There is no `eval`, no shell interpolation of
model output.

### Path traversal
`code__read_file` resolves the target and refuses anything outside the
repository root (`error: path escapes the repository root`) — with a
regression test.
→ [code_search.py](../../src/assistant/mcp_servers/code_search.py)

### SSRF
`fetch_url` accepts only `http(s)` and refuses loopback and private ranges
(`localhost`, `127.`, `0.`, `10.`, `192.168.`, `169.254.`, `172.16–31.`),
with a 15 s timeout and an 8 000-character cap on what enters the prompt.
**Honest limit:** this is a string match on the host, so it does not defeat
DNS rebinding or a redirect to an internal address. Production would resolve
DNS and enforce an egress allowlist at the proxy.
→ [tools/fetch.py](../../src/assistant/agent/tools/fetch.py)

### Input bounds
Chat messages are capped at 8 000 characters; uploads accept only
`.md`/`.markdown`/`.txt`/`.rst`, at most 2 MB per file, and must be valid
UTF-8. Anything else is skipped with a reason rather than silently ignored.
→ [schemas.py](../../src/assistant/api/schemas.py),
[routes.py](../../src/assistant/api/routes.py)

### Secrets
API keys are `SecretStr`, so they do not appear in logs or reprs. `.env` is
gitignored and excluded from the Docker build context. Secrets reach MCP
servers through environment variables, never through the model's context.
`ASSISTANT_LOG_PROMPTS` (which dumps full prompts) is documented as
dev-only, because conversations would land in logs.

### Supply chain
`pip-audit` over the resolved runtime tree and `npm audit` run on every push
and weekly, plus Dependabot for uv/npm/docker/actions. This is not
theoretical: the first run found a critical `happy-dom` VM-context escape
(RCE), SSRF and path traversal in `pydantic-ai`, command injection in
`fastmcp`, and pickle deserialisation in `diskcache` — all fixed by
upgrading. Both audits now report zero.
→ [security.yml](../../.github/workflows/security.yml)

### Container
Runs as an unprivileged user (`USER app`, uid 10001), has a `HEALTHCHECK`,
and ships no seed data. Image tags in compose are pinned rather than
`:latest`.

### Prompt injection
Bounded structurally rather than by filtering. A malicious document can
influence what the model *says*, but the tools it can reach are allowlisted
and read-only, so the blast radius is misinformation, not action. The system
prompt also forbids asserting the content of a page it did not fetch.

## What is deliberately not built

These are scope decisions for a local/internal tool, not oversights. Each
would be required before exposing this to untrusted users.

| Gap | Why it is fine here | What production needs |
|---|---|---|
| Single shared bearer token | One team, one instance | OIDC/SSO at the gateway |
| No rate limiting or quotas | Local use; the LLM provider's own limits apply | Per-user quotas, request throttling |
| No content sanitisation on ingest | Documents come from the operator | Strip instruction-like content, or tier tool permissions by document trust |
| No per-user isolation | Single-tenant | Per-user collections and session scoping |
| SSRF guard is string-based | Local network | DNS resolution + egress allowlist |
| Grafana is fully anonymous-admin | Local compose only | Real auth before any deployment |
| CodeQL skipped | Private repo without GitHub Advanced Security | Enable GHAS, or make the repo public |
| No secret scanning in CI | `detect-private-key` runs in pre-commit | gitleaks/trufflehog in CI |

## If you take this further

Roughly in order of value: OIDC at the gateway → per-user rate limits and
quotas → per-user document scoping → egress allowlist for `fetch_url` →
tool-permission tiers driven by document trust. The `ToolRegistry` is the
natural enforcement point for the last one, which is why it exists as a
single seam.
