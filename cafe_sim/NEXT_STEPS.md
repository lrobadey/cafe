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

4. **Add automated tests (not included in this scaffold)**
   - Unit tests for `WorldState` transitions.
   - Tool-execution tests for `execute_customer_tool` and `execute_barista_tool` with mocked world.
   - Optional fake-response tests that simulate model function-call outputs.

5. **Optional hardening**
   - Graceful shutdown handling for barista task cancellation.
   - Structured log export (JSON lines) in addition to terminal colors.
