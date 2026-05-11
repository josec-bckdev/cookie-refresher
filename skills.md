# Technical Skills Demonstrated — cookie-refresher

This document maps engineering concepts to specific files in the codebase.
Intended for portfolio review and technical interviews.

---

## AI Engineering

### Claude Computer Use API
**Where:** [infrastructure/anthropic_client.py](src/cookie_refresher/infrastructure/anthropic_client.py)

- Integrates `computer_20251124` tool type with beta header `computer-use-2025-11-24`
- Uses `claude-opus-4-7` with adaptive thinking (`thinking: {type: "adaptive"}`) to handle dynamic Cloudflare challenges
- Parses structured output (`COOKIES_JSON: {...}`) from free-form agent text using regex
- Translates SDK-specific `ToolUseBlock` / `TextBlock` types into domain entities at the adapter boundary

### ReAct Agentic Pattern
**Where:** [application/use_cases/refresh_session.py](src/cookie_refresher/application/use_cases/refresh_session.py)

- Implements the full Observe → Think → Act loop without any framework scaffolding
- Maintains conversation history (`messages: list[dict]`) across iterations
- Logs agent reasoning at each step — visible in production and demo environments
- Terminates on `COOKIES_JSON` signal or max-steps guard
- `ActionDispatcher` is a separate class (single responsibility) with a clean dispatch table

### Prompt Engineering
**Where:** [infrastructure/anthropic_client.py](src/cookie_refresher/infrastructure/anthropic_client.py) (`_SYSTEM_PROMPT`)

- System prompt constrains agent behaviour: one action at a time, structured output format, Cloudflare handling instruction
- Cookie extraction instruction defers to visual verification ("read the actual values from the cookies panel")

---

## Software Architecture

### Clean Architecture
**Where:** entire `src/cookie_refresher/` tree

- Four layers with strict inward-only dependencies: `infrastructure → adapters → application → domain`
- Domain layer is framework-free: pure Python dataclasses and ABCs
- Dependency Inversion: use case declares `IBrowserGateway`, `IVtrackGateway`, `IAgentClient` — never the concrete class
- Infrastructure wires concrete implementations via constructor injection in `main.py`

### SOLID Principles
- **S** — `ActionDispatcher` dispatches actions only; `RefreshSessionUseCase` orchestrates only
- **O** — New Computer Use actions extend the dispatch table without modifying existing branches
- **L** — `AsyncMock(spec=IBrowserGateway)` is a valid substitute in every test
- **I** — Ports declare only the methods their consumers actually call
- **D** — All gateway dependencies injected at construction; no `new` inside business logic

---

## Testing

### Test-Driven Development
**Where:** [tests/](tests/)

- Tests written **before** implementation (red → green → refactor commits)
- Unit tests: domain invariants, use-case behaviour, action dispatch table
- Integration test: full agent flow with scripted Anthropic mock — no real HTTP
- Coverage target: 80% on business logic, enforced in CI via `pytest --cov`

### Port-Boundary Mocking
**Where:** [tests/unit/application/test_refresh_session.py](tests/unit/application/test_refresh_session.py)

- All mocks target the `IBrowserGateway`, `IVtrackGateway`, `IAgentClient` ports
- Implementation details of adapters never leak into use-case tests
- `AsyncMock(spec=IBrowserGateway)` guarantees the mock interface matches the real one

---

## Backend Engineering

### FastAPI + async Python
**Where:** [adapters/controllers/api.py](src/cookie_refresher/adapters/controllers/api.py), [infrastructure/main.py](src/cookie_refresher/infrastructure/main.py)

- Lifespan context manager for scheduler startup/shutdown
- `BackgroundTasks` for non-blocking manual refresh trigger
- Health endpoint for load balancer and Docker healthcheck

### APScheduler + Cron
**Where:** [infrastructure/scheduler.py](src/cookie_refresher/infrastructure/scheduler.py)

- `AsyncIOScheduler` with cron triggers for Mon–Fri 6 AM and 3 PM
- Timezone-aware (`America/Bogota`)
- Scheduler accepts a coroutine factory, keeping it decoupled from the use case

### httpx async HTTP client
**Where:** [adapters/gateways/vnc_browser.py](src/cookie_refresher/adapters/gateways/vnc_browser.py), [adapters/gateways/vtrack_http.py](src/cookie_refresher/adapters/gateways/vtrack_http.py)

- Async `httpx.AsyncClient` for both VNC browser control and vtrack API calls
- Proper error handling: distinguishes `HTTPStatusError` from `RequestError`
- Logs redacted credential indicators (never logs actual secrets)

### Configuration management
**Where:** [infrastructure/settings.py](src/cookie_refresher/infrastructure/settings.py)

- `pydantic-settings` with `.env` file support and type validation
- Credentials loaded from environment variables only — never hardcoded
- Single `Settings` instance imported by infrastructure layer only

---

## DevOps

### Docker
**Where:** [Dockerfile](Dockerfile)

- Minimal `python:3.12-slim` base
- No browser installed — browser runs in the separate VNC sandbox container
- Health check via `/health` endpoint
- Non-root default (inherits slim base behaviour)

### Project structure
```
cookie-refresher/
├── src/cookie_refresher/
│   ├── domain/          # entities.py · ports.py
│   ├── application/     # use_cases/refresh_session.py
│   ├── adapters/        # gateways/ · controllers/
│   └── infrastructure/  # anthropic_client.py · scheduler.py · settings.py · main.py
└── tests/
    ├── unit/            # domain/ · application/
    └── integration/     # test_agent_login_flow.py
```
