# cookie-refresher

AI agent microservice that refreshes `vtrack-app` session cookies by controlling a real
browser with the **Claude Computer Use API**.

---

## Why this exists

`vtrack-app` scrapes a site protected by **Cloudflare Bot Management**.
The cookies (`cf_clearance` + `ci_session`) expire every ~1.5 hours and cannot be
refreshed programmatically — Cloudflare requires a real browser that passes its JS
challenges.  
`cookie-refresher` solves this by launching a sandboxed Chromium instance and letting
Claude navigate, solve challenges, and extract cookies visually — with no WebDriver
fingerprint.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Clean Architecture Layers                     │
│                                                                  │
│  infrastructure   ──────→   adapters   ──────→   application    │
│  (FastAPI, SDK,             (gateways,           (use case,     │
│   APScheduler,              controller)           ReAct loop)   │
│   settings)                                            │         │
│                                                        ▼         │
│                                                    domain        │
│                                                  (entities,      │
│                                                   ports / ABCs)  │
└─────────────────────────────────────────────────────────────────┘
```

Dependency arrows point inward only. The use case has zero knowledge of
Anthropic SDK, httpx, or VNC.

---

## Agentic pattern: ReAct (Reason + Act)

**Why ReAct over Plan-and-Execute or Reflection?**

Cloudflare challenges are dynamic — the exact UI, CAPTCHA variant, and timing change
every run. A pre-planned sequence of clicks (Plan-and-Execute) will break the first
time the login page looks different.

ReAct handles this naturally:

```
┌─────────────────────────────────────────────────────────────────┐
│                        ReAct Loop                               │
│                                                                  │
│   ┌──────────┐   screenshot   ┌──────────────────────────────┐  │
│   │ Browser  │ ─────────────→ │  Claude (claude-opus-4-7)   │  │
│   │ (VNC)    │                │  adaptive thinking enabled   │  │
│   │          │ ←───────────── │  Reason: "I see a login form"│  │
│   │          │  click/type/   │  Act:    left_click [x, y]  │  │
│   └──────────┘  key action    └──────────────────────────────┘  │
│        │                                    │                    │
│        │        ◄── loop until ─────────────┘                   │
│        │        "COOKIES_JSON: {...}" detected                   │
│        │                                                         │
│        ▼                                                         │
│   POST /session/set-cookies → vtrack-app                        │
└─────────────────────────────────────────────────────────────────┘
```

Each iteration: the agent sees the current screen, reasons about the next single action,
executes it, then sees the updated screen. The logged `Thought:` traces are the audit trail.

---

## Sequence diagram

```
Scheduler / POST /refresh
        │
        ▼
RefreshSessionUseCase.execute()
        │
        ├─► browser.navigate(LOGIN_URL)
        │
        └─[ReAct loop]──────────────────────────────────────┐
              │                                              │
              ├─► browser.take_screenshot()                  │
              │        └─ PNG bytes                          │
              │                                              │
              ├─► agent.complete(messages)                   │
              │    │  POST /v1/messages (Anthropic API)      │
              │    │  ← ToolUseBlock  OR  TextBlock          │
              │    └─ AgentStep(actions, is_done, cookies)   │
              │                                              │
              ├─[if not done]──────────────────────────────── loop
              │   └─► ActionDispatcher.dispatch(action)
              │         ├─ left_click → browser.click(x, y)
              │         ├─ type       → browser.type_text(t)
              │         ├─ key        → browser.press_key(k)
              │         └─ screenshot → browser.take_screenshot()
              │
              └─[if done]
                  └─► vtrack.post_cookies(SessionCookies)
                       └─ POST /session/set-cookies → vtrack-app
                           └─ AgentResult(success=True, steps=N)
```

---

## Running tests

```bash
# Install dev deps
pip install -e ".[dev]"

# Full suite with coverage
pytest

# Unit only (fast, no integration)
pytest tests/unit/

# Integration test (mocked Anthropic + VNC)
pytest tests/integration/ -v

# Watch mode during development
pytest-watch tests/
```

Expected output (green):
```
tests/unit/domain/test_entities.py        ........ [ 8 passed]
tests/unit/application/test_refresh_session.py  ...... [ 6 passed]
tests/unit/application/test_react_decisions.py  ....... [ 7 passed]
tests/integration/test_agent_login_flow.py      ... [ 3 passed]
Coverage: 84%
```

---

## Running locally

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, LOGIN_EMAIL, LOGIN_PASSWORD, VNC_BROWSER_URL

pip install -e ".[dev]"
uvicorn cookie_refresher.infrastructure.main:app --port 8001 --reload
```

**Manual trigger:**
```bash
curl -X POST http://localhost:8001/refresh
```

**Health check:**
```bash
curl http://localhost:8001/health
```

---

## Docker Compose integration

Add to `vtrack/docker-compose.yml`:

```yaml
cookie-refresher:
  build: ./microservices/cookie-refresher
  container_name: cookie_refresher
  restart: always
  env_file: ./microservices/cookie-refresher/.env
  environment:
    VTRACK_API_URL: http://api:8000
    VNC_BROWSER_URL: http://vnc-browser:8080
  ports:
    - "8001:8001"
  depends_on:
    api:
      condition: service_healthy
  networks:
    - vtrack-network
```

---

## Portfolio highlights

| Concept | Where to look |
|---------|---------------|
| ReAct agentic loop | [application/use_cases/refresh_session.py](src/cookie_refresher/application/use_cases/refresh_session.py) |
| Claude Computer Use integration | [infrastructure/anthropic_client.py](src/cookie_refresher/infrastructure/anthropic_client.py) |
| Clean Architecture layers | entire `src/` tree |
| TDD: failing tests first | [tests/](tests/) — committed before implementation |
| Port/adapter pattern | [domain/ports.py](src/cookie_refresher/domain/ports.py) + adapters/ |
| Integration test with scripted mocks | [tests/integration/test_agent_login_flow.py](tests/integration/test_agent_login_flow.py) |
| Cron scheduler | [infrastructure/scheduler.py](src/cookie_refresher/infrastructure/scheduler.py) |
| Secrets management | [infrastructure/settings.py](src/cookie_refresher/infrastructure/settings.py) — env-only |
