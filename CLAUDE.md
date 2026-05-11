# cookie-refresher — AI Development Guidelines

This file configures how Claude Code collaborates on this project.
It encodes the engineering practices that must be maintained across all sessions.

---

## Architecture contract

This service follows **Clean Architecture**. The dependency rule is non-negotiable:

```
infrastructure → adapters → application → domain
```

- `domain/` — pure Python only. No framework imports. No I/O.
- `application/` — imports only from `domain/`. No SDK imports.
- `adapters/` — implements domain ports. May import `httpx`, `fastapi`.
- `infrastructure/` — wires everything. May import any external library.

If a proposed change breaks this direction (e.g. importing `httpx` into a use case), push back and find the correct layer.

---

## TDD workflow (non-negotiable)

Follow **red → green → refactor** strictly:

1. **RED** — Write a failing test first. Commit it: `test(scope): add failing test for X`
2. **GREEN** — Write the minimum code to make it pass. Commit: `feat(scope): implement X`
3. **REFACTOR** — Clean up without changing behaviour. Commit: `refactor(scope): simplify X`

Never write implementation code before a test exists for it.
Test the behaviour, not the implementation. Mock only at port boundaries (`IBrowserGateway`, `IVtrackGateway`, `IAgentClient`).

---

## Commit format (Commitizen)

Format: `type(scope): subject` — lowercase, imperative, no period.

| type | when |
|------|------|
| `feat` | new capability |
| `fix` | bug fix |
| `test` | test-only change |
| `refactor` | restructure without behaviour change |
| `chore` | tooling, deps, CI |
| `docs` | documentation only |

Valid scopes: `agent`, `domain`, `adapters`, `infra`, `scheduler`, `login`, `cookies`, `config`.

Examples:
```
feat(agent): add cookie extraction ReAct loop
test(login): add mock for Cloudflare challenge sequence
fix(adapters): handle vtrack 422 response gracefully
```

---

## Agentic pattern

This service uses **ReAct (Reason + Act)**. Each loop iteration:
```
Observe (screenshot) → Think (Claude reasons) → Act (dispatch tool) → Observe → …
```

- Keep the ReAct loop logic inside `application/use_cases/refresh_session.py`.
- Keep action dispatch in `ActionDispatcher` (single responsibility).
- The `AnthropicAgentClient` translates SDK types → domain entities. Nothing else should touch the Anthropic SDK.
- Log Claude's reasoning at INFO level so the trace is visible in production logs.

---

## SOLID reminders

- **S** — `ActionDispatcher` dispatches only. `RefreshSessionUseCase` orchestrates only.
- **O** — New action types should extend `ActionDispatcher.dispatch` via the dispatch table, not nested if-chains.
- **L** — Any `IBrowserGateway` mock must be a valid substitute for `VncBrowserGateway`.
- **I** — Keep port interfaces narrow; don't add methods that not all implementations need.
- **D** — Use cases receive ports via constructor injection. Never instantiate gateways inside a use case.

---

## What NOT to do

- Do not hardcode credentials anywhere in source files.
- Do not import `anthropic` outside `infrastructure/`.
- Do not write tests that only verify mock call counts without asserting outcome.
- Do not suppress exceptions silently — log and propagate or convert to domain errors.
- Do not add a method to `domain/ports.py` without a corresponding test for it.
