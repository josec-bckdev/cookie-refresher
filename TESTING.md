# Testing Guide

Testing in cookie-refresher follows **Test-Driven Development (TDD)** with strict adherence to code coverage targets. This guide documents how to run tests, what to test, and why.

---

## Local Setup

Tests run against a local `.venv` — the service itself runs in Docker but tests do not.

**One-time setup:**

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

**`.env.test`** contains dummy credentials so `pydantic-settings` can boot without real secrets. It is committed to the repo and safe (no real values):

```bash
# Run all tests
env $(cat .env.test | grep -v '^#' | xargs) .venv/bin/pytest

# Run with coverage report
env $(cat .env.test | grep -v '^#' | xargs) .venv/bin/pytest --cov=src/cookie_refresher --cov-report=term-missing
```

---

## Quick Start

Run the full test suite:

```bash
env $(cat .env.test | grep -v '^#' | xargs) .venv/bin/pytest
```

Run with coverage:

```bash
pytest tests/ --cov=src/cookie_refresher --cov-report=term-missing
```

Run a specific test file:

```bash
pytest tests/unit/domain/test_entities.py
```

Run a specific test class or function:

```bash
pytest tests/unit/domain/test_entities.py::TestSessionCookies::test_valid_creation
```

Watch mode (re-run on file changes):

```bash
pytest-watch tests/
```

---

## Coverage Objective

**Minimum coverage: 80%** across all source files (`src/cookie_refresher/`). This is enforced in CI and locally.

**Why 80%?**
- High enough to catch real bugs and regressions
- Low enough to avoid testing implementation details (private methods, trivial getters)
- Pragmatic threshold that balances confidence with maintainability

**How coverage is measured:**
- Line coverage only (not branch coverage)
- Reviewed in every test run and CI
- Gaps shown with `--cov-report=term-missing`

---

## Test Organization

Tests live in `tests/` mirroring source structure:

```
tests/
├── unit/
│   ├── domain/              # domain/entities.py, domain/ports.py
│   ├── application/         # application/use_cases/
│   ├── adapters/            # adapters/gateways/, adapters/job_store.py
│   └── infrastructure/      # infrastructure/*.py
├── integration/             # end-to-end flows
└── conftest.py             # shared fixtures
```

Each test file begins with a docstring stating its purpose (RED, unit test, integration test).

### Test Phases

- **Unit tests** — Test one layer/component in isolation. Mock all external dependencies (ports).
- **Integration tests** — Test multiple layers working together.
- **RED tests** — Fail before implementation exists. Committed separately from GREEN.
- **GREEN tests** — Pass. Committed with implementation.

---

## TDD Workflow

Follow this strictly (documented in CLAUDE.md):

1. **RED** — Write a failing test. Commit: `test(scope): add failing test for X`
2. **GREEN** — Write minimum code to pass. Commit: `feat(scope): implement X` or `fix(scope): ...`
3. **REFACTOR** — Clean up without changing behavior. Commit: `refactor(scope): simplify X` (if needed)

Each commit must:
- Pass all tests
- Be independently understandable
- Have a narrow scope (one logical change)

Example for a multi-layer feature:

```
test(domain): add failing tests for messages field
feat(domain): add messages to AgentResult and Job
test(adapters): add failing tests for messages storage
feat(adapters): store messages in job store
```

---

## What to Test

### Always test:

- **Public methods** on domain entities and use cases
- **All error paths** (exceptions, validation failures)
- **State transitions** (e.g., Job status changes: PROCESSING → SUCCESS/FAILED)
- **Integration points** where layers meet (adapters calling use cases)
- **Port contracts** — if you add a method to `IBrowserGateway`, test it's implemented correctly

### Don't test:

- **Private `_methods`** — they're implementation details
- **Third-party libraries** — assume `httpx`, `FastAPI`, `pytest` work correctly
- **Trivial getters/setters** — unless they have logic
- **Mock call counts only** — always assert the actual outcome, not just that a mock was called

---

## Mocking Strategy

Mock only at **port boundaries** — the interfaces defined in `domain/ports.py`:

- `IBrowserGateway` — always mock in unit tests
- `IVtrackGateway` — always mock in unit tests
- `IAgentClient` — always mock in unit tests
- `IJobStore` — always mock when testing the use case

Use `unittest.mock.AsyncMock` for async ports. Mocks should be **valid substitutes** for the real implementation (follow the interface contract).

Example:

```python
def _make_browser() -> IBrowserGateway:
    browser = AsyncMock(spec=IBrowserGateway)
    browser.take_screenshot.return_value = FAKE_PNG
    return browser
```

---

## Writing Assertions

### Test behavior, not implementation:

❌ **Bad** — tests the how:
```python
mock_browser.take_screenshot.assert_called_once()
```

✅ **Good** — tests the what:
```python
assert result.success is True
assert result.cookies == expected_cookies
```

### When to use mock assertions:

Only when the interaction itself is the behavior (e.g., "the use case must call the gateway"):

```python
browser.navigate.assert_awaited_once()
```

But always pair it with an outcome assertion:

```python
result = await use_case.execute()
assert result.success is True  # the outcome matters
browser.navigate.assert_awaited_once()  # and the interaction
```

---

## Test Naming

Follow this pattern: `test_<scenario>_<expected_result>`

Examples:

```python
def test_success_factory_sets_correct_fields():
def test_failure_when_vtrack_post_fails():
def test_empty_cf_clearance_raises():
def test_agent_never_done_exceeds_max_steps():
```

---

## Common Patterns

### Testing a domain entity:

```python
from cookie_refresher.domain.entities import SessionCookies

def test_empty_cf_clearance_raises():
    with pytest.raises(ValueError, match="cf_clearance"):
        SessionCookies(cf_clearance="", ci_session="xyz")
```

### Testing a use case with mocked ports:

```python
@pytest.mark.asyncio
async def test_success_posts_cookies_to_vtrack():
    browser = AsyncMock(spec=IBrowserGateway)
    vtrack = AsyncMock(spec=IVtrackGateway)
    agent = AsyncMock(spec=IAgentClient)
    
    use_case = RefreshSessionUseCase(browser=browser, vtrack=vtrack, agent=agent, ...)
    result = await use_case.execute()
    
    assert result.success is True
    vtrack.post_cookies.assert_awaited_once()
```

### Testing error handling:

```python
def test_failure_when_max_steps_exceeded():
    agent = _make_agent_never_done(n_steps=3)
    use_case = RefreshSessionUseCase(..., max_steps=2)
    
    result = await use_case.execute()
    
    assert result.success is False
    assert "Max steps" in result.error
    assert result.steps_taken == 2
```

---

## Pytest Configuration

Settings in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"           # automatically handle async tests
testpaths = ["tests"]           # where to find tests
addopts = "--tb=short -q"       # short tracebacks, quiet output
```

Coverage settings:

```toml
[tool.coverage.run]
branch = false
omit = [...]

[tool.coverage.report]
fail_under = 80                 # fail CI if below 80%
```

---

## Debugging Tests

Run with verbose output:

```bash
pytest tests/ -vv
```

Run with full tracebacks:

```bash
pytest tests/ --tb=long
```

Drop into debugger on failure:

```bash
pytest tests/ --pdb
```

Run only a failing test with output:

```bash
pytest tests/unit/domain/test_entities.py::TestAgentResult::test_success_factory_sets_correct_fields -vv -s
```

---

## CI/CD Integration

Tests run on every push via GitHub Actions. They must:

1. **Pass** — no failing tests
2. **Meet coverage** — 80% minimum
3. **Type check** — type annotations validated (via Pylance in IDE)

If CI fails, diagnose locally:

```bash
pytest tests/ --cov=src/cookie_refresher
```

---

## Best Practices

1. **Write tests before code** — RED → GREEN → REFACTOR
2. **One assertion per test** (or one logical outcome) — makes failures specific
3. **Use descriptive names** — test names are documentation
4. **Avoid test interdependence** — each test is independent
5. **Mock at boundaries** — keep tests fast and focused
6. **Review coverage gaps** — `--cov-report=term-missing` shows untested lines
7. **Commit tests separately** — `test(scope): ...` commits prove RED state

---

## When Coverage Is Hard

Sometimes 80% feels strict. Common situations:

- **Error handling for impossible states** — skip if you've proven it can't happen
- **Defensive checks in library code** — test once, don't repeat in every caller
- **Third-party integration** — mock it, don't test the library itself
- **Platform-specific code** — tag with `@pytest.mark.skipif` if needed

If coverage is blocking, ask: "Is this testing behavior or implementation?"  If the latter, consider skipping or refactoring.

---

## Resources

- **CLAUDE.md** — TDD workflow and architecture principles
- **pytest docs** — https://docs.pytest.org
- **unittest.mock** — https://docs.python.org/3/library/unittest.mock.html
