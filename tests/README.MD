# Tests

Four groups, registered as pytest markers in `pytest.ini`:

| Group | Path | What it covers |
|---|---|---|
| `unit` | `tests/unit/` | The Database and Vector DB services in isolation |
| `contract` | `tests/contract/` | Each service against its real downstream neighbours, wired in-process |
| `golden` | `tests/golden/` | 28 captured responses replayed through the Gateway |
| `e2e` | `tests/e2e/` | `test_endpoints_integration.py` (in-process) and `test_rate_limiter.py` (live) |

`tests/golden` is the behavioural arbiter for the refactor: 27 of its 28 responses are
byte-identical to the pre-refactor monolith, and 1 was deliberately re-baselined when the
`/api/llm-query` keyword-argument bug was fixed.

Every suite except `tests/e2e/test_rate_limiter.py` runs **in-process** over
`httpx.ASGITransport`, with Google, OpenAI, Ollama, Chroma, the Go sync server and the Go
rate limiter all mocked. No ports need to be up. `tests/e2e/test_rate_limiter.py` is the
only suite that requires live processes: all six Python services plus the Go rate limiter
on `:8002`.

## Normal run

```bash
uv run pytest -q --deselect tests/e2e/test_rate_limiter.py
```

Expect **334 passed, 2 failed**. The 2 failures are in
`tests/e2e/test_endpoints_integration.py` (`test_create_calendar_event_missing_email_account_id`,
`test_update_calendar_event_success`): they send request bodies that fail the endpoints'
Pydantic validation and get `422`. They are pre-existing request-validation errors that
predate the refactor entirely and are carried across unchanged — not a broken install.

## The live rate-limiter suite

> [!WARNING]
> `tests/e2e/test_rate_limiter.py` creates **real events in the user's Google Calendar**.
> 290 such "Test Event" entries from historical runs already exist. Unless that pollution
> is acceptable, deselect the calendar class:
>
> ```bash
> uv run pytest tests/e2e/test_rate_limiter.py \
>   --deselect tests/e2e/test_rate_limiter.py::TestCalendarEventsEndpoint
> ```

> [!TIP]
> Clear the rate limiter before running this suite — it makes actual HTTP calls and
> consumes tokens, so leftover state causes assertion mismatches on the expected counts:
>
> ```bash
> curl -X POST http://localhost:8002/reset
> ```
