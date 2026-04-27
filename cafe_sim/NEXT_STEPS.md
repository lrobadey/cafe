# What is left to be done

The MVP scaffold from `cafe_sim_mvp_spec.md` is in place. Remaining work:

1. **Install dependencies and set environment**
   - `pip install -r cafe_sim/requirements.txt`
   - export `OPENAI_API_KEY`.

2. **Run a smoke test with shortened timings**
   - Temporarily adjust in `config.py`:
     - `SIM_DURATION = 60`
     - `CUSTOMER_SPAWN_INTERVAL = 10`
     - `MAX_CONCURRENT_CUSTOMERS = 2`

3. **Validate runtime behavior**
   - Confirm terminal logs include: customer spawn, queue checks, order transitions (`pending -> claimed -> ready -> delivered`), and final summary.

4. **Automated tests (done — see `tests/`)**
   - Unit tests for `WorldState` transitions (`tests/test_world.py`).
   - Tool-execution tests for `execute_customer_tool` and `execute_barista_tool`
     (`tests/test_customer_tools.py`, `tests/test_barista_tools.py`).
   - Fake-response tests that simulate model function-call outputs end-to-end
     for both agent loops and the runner (`tests/test_customer_loop.py`,
     `tests/test_barista_loop.py`, `tests/test_runner.py`).
   - Static spec-§14 schema checks (`tests/test_tool_schemas.py`) and persona
     sanity checks (`tests/test_personas.py`).
   - Run with: `pip install -r requirements-dev.txt && pytest`.

5. **Optional hardening**
   - Graceful shutdown handling for barista task cancellation.
   - Structured log export (JSON lines) in addition to terminal colors.
