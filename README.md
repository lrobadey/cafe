# Cafe Simulation

A real-time, multi-agent cafe simulation powered by the OpenAI Responses API. A barista agent (Alex) and up to four concurrent customer agents interact through shared world state, each driven by a live model loop.

## How it works

Four nested systems compose the simulation:

1. **World** (`cafe_sim/world.py`) — single source of truth: menu, supplies, tables, order queue, event log. All mutations go through an `asyncio.Lock`.
2. **Agents** (`cafe_sim/agents/`) — one barista loop and N customer loops, each calling the OpenAI Responses API in a tool-use cycle.
3. **Tools** — local Python functions that validate and apply model decisions to `WorldState`, then return a result string back to the model.
4. **Runner** (`cafe_sim/runner.py`) — real-time clock that starts the barista and spawns customers on a fixed interval.

Agents never mutate world state directly. They call tools; local Python executes those tool calls against `WorldState` and returns `function_call_output` items. `WorldState` is the only authority.

## Repository layout

```
cafe/
├── cafe_sim/
│   ├── main.py              # entry point (terminal or dashboard mode)
│   ├── world.py             # WorldState class + asyncio lock
│   ├── runner.py            # SimulationRunner: clock, spawning
│   ├── agents/
│   │   ├── customer.py      # CustomerAgent loop + customer tools
│   │   └── barista.py       # BaristaAgent loop + barista tools
│   ├── api.py               # FastAPI server for dashboard mode
│   ├── control.py           # SimulationController (start/stop/reset/spawn)
│   ├── state_view.py        # Snapshot and event helpers for the API
│   ├── reasoning_summary.py # Extracts reasoning summaries from responses
│   ├── run_report.py        # End-of-run statistics
│   ├── personas.py          # 12 customer personas injected at spawn time
│   ├── logger.py            # Color terminal logger
│   ├── config.py            # All tunable constants + OpenAI client factory
│   └── requirements.txt
├── dashboard/               # Static frontend for dashboard mode
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── tests/                   # Pytest test suite
```

## Setup

```bash
pip install -r cafe_sim/requirements.txt
export OPENAI_API_KEY=sk-...
```

Alternatively, place `OPENAI_API_KEY=...` in a `.env` file at the repo root — `config.py` will load it automatically.

## Running

**Terminal mode** (colored log output, no browser needed):

```bash
cd cafe_sim
python main.py
```

**Dashboard mode** (live web UI at `http://127.0.0.1:8000`):

```bash
cd cafe_sim
python main.py --dashboard
# optional flags: --host 0.0.0.0 --port 9000
```

The dashboard streams world snapshots via SSE and exposes REST controls to start, stop, reset, or manually spawn a customer.

## Quick smoke test

Edit `cafe_sim/config.py` to shorten the run:

```python
SIM_DURATION = 60
CUSTOMER_SPAWN_INTERVAL = 10
MAX_CONCURRENT_CUSTOMERS = 2
```

Then run terminal mode. You should see customer spawn, order transitions (`pending → claimed → ready → delivered`), and a final summary within one minute.

## Configuration (`cafe_sim/config.py`)

| Constant | Default | Description |
|---|---|---|
| `BARISTA_MODEL` / `CUSTOMER_MODEL` | `gpt-5.4-mini` | Model for each agent |
| `REASONING_EFFORT` | `high` | OpenAI reasoning effort level |
| `SIM_DURATION` | `600` | Total simulation wall-clock seconds |
| `CUSTOMER_SPAWN_INTERVAL` | `30` | Seconds between customer spawns |
| `MAX_CONCURRENT_CUSTOMERS` | `4` | Cap on simultaneous customer agents |
| `MAX_CUSTOMER_HOPS` | `16` | Max tool-call cycles per customer |

## Supplies

Supplies are owned by `WorldState` and linked to menu recipes. The live customer menu hides items that cannot currently be made from stock. Baristas still use `prepare_order` as the final physical checkpoint: if another order consumed the last required supply first, `prepare_order` marks the order `failed`, records the missing supplies, clears the barista's active order, and logs a stockout event without decrementing partial stock.

Tracked supplies: coffee beans, milk, cups, cold brew servings, tea bags, and muffins. The dashboard shows current supply counts, low/out statuses, and whether each menu item is orderable, sold out by supplies, or off menu by toggle. Run summaries include stockout failures and final supply status.
| `CUSTOMER_MAX_WAIT` | `90` | Seconds before the model is nudged to consider leaving |
| `BARISTA_POLL_INTERVAL` | `5` | Idle sleep between barista work cycles |

## Agent tool loops

### Customer

Tools available to the customer model: `enter_cafe`, `read_menu`, `place_order`, `find_seat`, `check_order`, `wait`, `sip_drink`, `eat_item`, `linger`, `leave`.

Each customer runs a `while not done` loop, appending `response.output` items to local `input_items` and returning `function_call_output` for every tool call. The loop exits when the model calls `leave` or `MAX_CUSTOMER_HOPS` is reached.

After an order is ready, `check_order` marks it delivered and moves the purchased item IDs into the customer's visit state. The world validates post-order tools: drinks can be consumed with `sip_drink`, food can be consumed with `eat_item`, and `linger` keeps the customer occupying their table briefly before they leave.

### Barista

Tools: `check_queue`, `claim_order`, `prepare_order`, `mark_ready`, `idle`.

The barista runs a stateless outer loop. Each iteration starts a fresh short conversation (`input_items`) and drives a single work cycle (check → claim → prepare → mark ready, or idle). Context is intentionally kept small.

## Personas

12 customer personas are defined in `cafe_sim/personas.py` (Marcus the hurried commuter, Yuki the budget-conscious student, Felix the researcher, etc.). The runner picks one at random for each spawn.

## Tests

```bash
pytest tests/
```

Tests cover spawn timing, reasoning summary extraction, run report generation, barista shift memory, and customer wait behavior.

## Dashboard API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/snapshot` | Full live state snapshot |
| `GET` | `/api/events?after=N&limit=100` | Paginated event log |
| `GET` | `/api/stream` | SSE stream of snapshots (1 Hz) |
| `POST` | `/api/control/start` | Start simulation |
| `POST` | `/api/control/stop` | Stop simulation |
| `POST` | `/api/control/reset` | Reset to clean state |
| `POST` | `/api/control/spawn` | Manually spawn one customer |
| `POST` | `/api/control/settings` | Update spawn interval or duration |
| `POST` | `/api/control/menu/{item_id}` | Toggle a menu item on/off |
