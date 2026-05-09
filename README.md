# CafeLab v0.4

CafeLab v0.4 is a real-time cafe simulation powered by the OpenAI Responses API.

The live app now models a small cafe with two AI baristas, a shared order queue, live supplies, a stock-aware menu, customer dining behavior, dashboard visibility, and durable run reports. The important rule is simple: the Python runtime owns the cafe. Agents can only act by calling tools, and those tools validate every change against the shared world state.

## What Is Live Now

CafeLab v0.4 is built from five nested systems:

1. **World** (`cafe_sim/world.py`) - the single source of truth for menu items, supplies, tables, staff, orders, customer visits, metrics, and events.
2. **Agents** (`cafe_sim/agents/`) - two barista agents, Alex and Jamie, plus customer agents spawned during the run.
3. **Tools** - local Python actions that agents call to enter, order, claim work, prepare items, consume food and drinks, leave, or idle.
4. **Runner and controller** (`cafe_sim/runner.py`, `cafe_sim/control.py`) - the real-time clock, customer spawning, start/stop/reset behavior, and dashboard controls.
5. **Dashboard and reports** (`dashboard/`, `cafe_sim/run_report.py`) - live operator visibility plus saved run artifacts under `runs/reports/`.

Agents do not edit the cafe directly. Alex, Jamie, and each customer ask to do something through a tool call. The world accepts, rejects, or records the action. That keeps queue ownership, stock, tables, customer state, and reporting in one authoritative place.

## Feature Summary

### Two Baristas, One Shared Queue

Alex and Jamie run as separate barista agents. Both look at the same pending order queue and claim one order at a time. The world records which barista owns each claimed order, blocks duplicate ownership, and tracks coordination metrics such as claim conflicts, idle checks, and completed orders by barista.

Barista order flow:

```text
pending -> claimed -> preparing -> ready
```

Customer pickup then closes the order:

```text
ready -> delivered
```

Unfinished orders are closed at shutdown as abandoned, stale, failed, or otherwise unresolved depending on their state.

### Live Supplies and Stock-Aware Menu

The cafe tracks physical supplies:

- Coffee beans
- Milk
- Cups
- Cold brew servings
- Tea bags
- Muffins

Menu items have recipes. Customers only see items that are both manually enabled and possible to make from current stock. If supplies run out, the menu automatically marks affected items as sold out for new customers.

The barista preparation step is the final physical checkpoint. If stock changed after an order was placed, `prepare_order` fails the order cleanly, records the missing supplies, and does not partially decrement inventory.

### Customer Visits, Dining, and Consumption

Customers are persona-driven agents with a budget and mood. A visit can include entering, reading the live menu, ordering, finding a table, waiting, picking up an order, sipping drinks, eating food, lingering, and leaving.

The world tracks each customer's visit phase, held items, consumed items, table, received-order time, and whether they left with unconsumed items. Customers without a table can still take their order away.

### Dashboard Visibility

Dashboard mode serves a live control room at `http://127.0.0.1:8000`.

The dashboard shows:

- Run state, elapsed time, revenue, open orders, table use, and customer count
- Queue pipeline counts for waiting, claimed, in prep, ready, picked up, abandoned, stale, and failed orders
- Alex and Jamie's status, current order, completed count, last action, and reasoning summary when available
- Active customers, table placement, visit phase, order state, held items, and consumed items
- Current supplies with normal, low, and out status
- Menu items marked as orderable, sold out by supplies, or off menu by operator toggle
- Live activity feed and controls for start, stop, reset, manual spawn, run settings, and menu toggles

The dashboard reads from the runtime snapshot API. It is a visibility and control layer, not a separate simulation.

### Run Reports

Each run creates a report directory under:

```text
runs/reports/
```

A report contains:

- `events.jsonl` - append-only event stream with ordered runtime events
- `summary.json` - final status, timing, metrics, final snapshot, and alerts

Reports include customer spawns, model responses, tool calls, tool results, queue movement, stockout failures, final supplies, revenue, wait times, consumption counts, barista completion counts, and coordination metrics.

## Repository Layout

```text
cafe/
├── cafe_sim/
│   ├── main.py              # terminal and dashboard entry point
│   ├── world.py             # authoritative WorldState and mutations
│   ├── runner.py            # terminal-mode simulation clock
│   ├── control.py           # dashboard-mode start/stop/reset/spawn control
│   ├── api.py               # FastAPI server and dashboard API
│   ├── state_view.py        # live snapshot and event read models
│   ├── run_report.py        # durable per-run report writer
│   ├── config.py            # models, timing, menu, recipes, supplies, tables
│   ├── personas.py          # customer personas
│   ├── reasoning_summary.py # reasoning summary extraction
│   ├── logger.py            # terminal logging
│   ├── agents/
│   │   ├── barista.py       # Alex/Jamie barista loop and tools
│   │   └── customer.py      # customer loop and tools
│   └── requirements.txt
├── dashboard/
│   ├── index.html           # dashboard shell
│   ├── app.js               # live UI and control calls
│   └── styles.css
├── tests/                   # pytest coverage for runtime behavior
└── cafe_sim_mvp_spec.md
```

## Setup

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r cafe_sim/requirements.txt
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-...
```

You can also put the key in a repo-local `.env` file:

```text
OPENAI_API_KEY=sk-...
```

`cafe_sim/config.py` loads that `.env` file automatically when the app starts.

## Running

### Terminal Mode

Terminal mode runs the simulation with colored logs and writes a run report when the run closes.

```bash
cd cafe_sim
python main.py
```

By default, the run lasts 600 seconds, spawns customers roughly every 30 seconds with jitter, allows up to 4 concurrent customers, and gives the cafe 20 seconds of closing grace before unresolved state is closed out.

### Dashboard Mode

Dashboard mode starts the FastAPI server and static control room.

```bash
cd cafe_sim
python main.py --dashboard
```

Then open:

```text
http://127.0.0.1:8000
```

Optional host and port:

```bash
python main.py --dashboard --host 0.0.0.0 --port 9000
```

In dashboard mode, use the on-screen controls to start, stop, reset, spawn a customer, change spawn interval or duration, and toggle menu items.

## Configuration

The main knobs live in `cafe_sim/config.py`.

| Setting | Default | Meaning |
|---|---:|---|
| `BARISTA_MODEL` | `gpt-5.4-mini` | Model used by Alex and Jamie |
| `CUSTOMER_MODEL` | `gpt-5.4-mini` | Model used by customer agents |
| `REASONING_EFFORT` | `high` | Reasoning effort for model calls |
| `REASONING_SUMMARY` | `auto` | Reasoning summary mode |
| `STORE_RESPONSES` | `True` | Whether Responses API calls are stored |
| `SIM_DURATION` | `600` | Run duration in real seconds |
| `CLOSING_GRACE_SECONDS` | `20` | Extra time before closeout |
| `CUSTOMER_SPAWN_INTERVAL` | `30` | Base seconds between customer spawns |
| `CUSTOMER_SPAWN_JITTER` | `0.5` | Random spread around spawn interval |
| `MAX_CONCURRENT_CUSTOMERS` | `4` | Active customer cap |
| `MAX_CUSTOMER_HOPS` | `16` | Max customer tool-call cycles |
| `CUSTOMER_MAX_WAIT` | `90` | Wait time before customers are nudged to consider leaving |
| `BARISTA_POLL_INTERVAL` | `5` | Idle wait between barista work cycles |

The same file owns the live menu, recipes, starting supplies, and table IDs.

## Dashboard API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/snapshot` | Full live runtime snapshot |
| `GET` | `/api/events?after=N&limit=100` | Paginated event log |
| `GET` | `/api/stream` | 1 Hz server-sent snapshot stream |
| `POST` | `/api/control/start` | Start the dashboard simulation |
| `POST` | `/api/control/stop` | Stop and close out the simulation |
| `POST` | `/api/control/reset` | Reset to a clean world |
| `POST` | `/api/control/spawn` | Manually spawn one customer |
| `POST` | `/api/control/settings` | Update spawn interval or duration |
| `POST` | `/api/control/menu/{item_id}` | Turn a menu item on or off |

## Tests

Run the test suite from the repo root:

```bash
pytest tests/
```

Current tests cover reasoning summary extraction, run reports, spawn timing, two-barista queue coordination, barista shift memory, customer wait behavior, and world closeout.

## Quick Smoke Run

For a short local run, temporarily lower these values in `cafe_sim/config.py`:

```python
SIM_DURATION = 60
CUSTOMER_SPAWN_INTERVAL = 10
MAX_CONCURRENT_CUSTOMERS = 2
```

Then run terminal mode:

```bash
cd cafe_sim
python main.py
```

You should see Alex and Jamie come on shift, customers spawn, orders move through the queue, supplies decrease as items are prepared, customers pick up and consume items when the visit allows it, and a final report path printed at the end.
