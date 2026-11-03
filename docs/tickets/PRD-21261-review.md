# PRD-21261 · Code review

## Round 1 · 2026-11-03 · reviewed dev/PRD-21261 @ 1b6b831 (base main @ 2eec5ea)

Verdict: changes requested

### Findings

1. [must-fix] `src/grafana_mcp/credentials.py:66-93` — `CredentialMiddleware` never rejects a request that is missing the required `X-Grafana-Token` header: `initialize`/`tools/list`/`tools/call` all return HTTP 200 regardless of headers, and a missing token is instead reported in-band by `tools/_common.py:82-95` (`run()`) as `{"error": {"code": "not_configured", ...}}`. This violates `vendor-mcp-development-sop.md` §3.2 ("凭据缺失即 401", marked 必须/mandatory: *"`/mcp` 请求缺任一必需 Header 时，中间件直接返回 401，并在 body 里列出所需的 Header 名"*) and the explicit `mcp-track.md` §4.3 must-fix bullet that folds in the SOP §14 Checklist, whose "对接契约（§1、§3，必须全部满足）" section lists *"缺 Header 时 `/mcp` 返回 401 并列出所需 Header"* as a mandatory, not optional, item.

   The design doc's own justification for this deviation (`docs/tickets/PRD-21261.md:297-301`, "A documented deviation from the SOP's generic pattern") cites "the same rationale as the itglue-mcp PRD-20835 fix: the platform's admin console refreshes a connector's tool catalog with placeholder/absent headers" — but that precedent does not actually support skipping 401 for a missing required credential. Checked `MSPbotsAIFramework/itglue-mcp@main (abf9dd7)`:
   - `src/itglue_mcp/credentials.py:91-93` — `CredentialMiddleware.__call__` returns a 401 `JSONResponse` (via `_reject()`) the moment the required `X-ITGlue-API-Key` header is empty, before even reaching `tools/list`.
   - `tests/test_middleware.py:25-27` — `test_missing_api_key_is_401` calls `tools/list` (no `initialize`) with the API key header absent and asserts `r.status_code == 401` and `"X-ITGlue-API-Key" in r.json()["required_headers"]`.
   - `tests/test_middleware.py:39-49` — the actual PRD-20835 fix only exempts an **optional** header (`X-ITGlue-Region` / `X-ITGlue-Base-URL`, both of which have defaults) carrying a *placeholder* value from blocking `initialize`/`tools/list`. It never touches the required `X-ITGlue-API-Key` check above.

   So the one existing connector that was actually patched for the admin-console tool-catalog-refresh scenario still 401s on a missing required credential; it only relaxed validation of an optional header. Grafana's two required headers (`X-Grafana-Token` and `X-Grafana-Base-Url`) are both treated as "may be entirely absent, still 200" here, which is a materially different (and non-compliant) behavior from the precedent cited to justify it.

   Fix: have `CredentialMiddleware` return 401 (with `required_headers` listing `X-Grafana-Token` and `X-Grafana-Base-Url`, mirroring itglue-mcp's `_reject()` shape) when either required header is absent. The in-band `not_configured`/`invalid_argument` tool-call envelope can stay for a header that is present but malformed (that part isn't covered by §3.2, which only speaks to an absent header), but an outright missing required header must not produce a 200. `tools/list`/`initialize` without any headers (the actual admin-console scenario) would then need its own narrower accommodation if still required — but it cannot be achieved by exempting the required-credential check entirely, only an optional one, per the real precedent.

2. [must-fix] `docs/tickets/PRD-21261.md:56,57,63,68,84,86-102` — the frozen ticket document (which per `mcp-track.md` §2.4 must be English-only — no `.zh.md` companion is written for MCP repos) still carries untranslated Chinese: `主线 A` (line 63), `先复用，再自研` (line 68), and `[新增]` / `[改]` / `[不阻塞/not blocking]` / `[已假设/assumed]` bracket tags throughout "Open questions" (lines 56-57) and "Change list" (lines 84, 86-102). This fails `vendor-mcp-development-sop.md` §0.4 (必须/mandatory: *"仓库内容全部用英文...README.md 及仓库里的其它文档"* — explicitly repo-wide, not limited to code) and the explicit `mcp-track.md` §4.3 must-fix bullet "仓库里没有中文".

   The Self-test's "English-only scan" (`docs/tickets/PRD-21261.md` Gates table) only ran `grep -rnP '[\x{4e00}-\x{9fff}]' ... src tests README.md NOTICE pyproject.toml Dockerfile docker-compose.yml .env.example .gitignore .dockerignore LICENSE` — it excludes `docs/tickets/`, so its "no output (clean; SOP §0.4)" result does not actually cover the repo-wide requirement. Both `mcp-track.md` §3.2 step 7 and the SOP itself specify scanning the whole repo (`grep ... .`). Running that full-repo scan now:

   ```
   $ grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .
   ```

   returns 19 hits, all in `docs/tickets/PRD-21261.md` (verified 2026-11-03 on `1b6b831`).

   Fix: translate the bracket tags to their English equivalents (`[new]` / `[changed]` / `[not blocking]` / `[assumed]` — the English phrasing is already used correctly elsewhere in the same document, e.g. "Not a blocker" / "not blocking" in the Self-test section) and replace `主线 A` / `先复用，再自研` with English ("main-line A" / "reuse first, build second" — matching the terminology already used in English in the Self-test and Reuse & cleanup sections for the same concepts). These are markup/wording substitutions only; none of them touch AC1-AC11, the tool list, or any user-visible behavior, so they're in scope for the dev leg to fix directly. Re-run the full-repo scan after the edit and confirm no output in the next Self-test update.

Both findings are confined to the credential-rejection transport behavior and to markup/wording in the frozen document — neither requires any change to the Product design's goals, non-goals, or AC1-AC11, and neither requires touching the 12 tool implementations, the error-envelope vocabulary, the multi-tenant isolation mechanism, or the test suite's coverage (independently re-run and confirmed: `uv run pytest -q` → 33 passed; `uv run ruff check .` → all checks passed, both reproduced on `1b6b831` during this review).
