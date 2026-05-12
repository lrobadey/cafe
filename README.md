# CafeLab v1

CafeLab is a real-time cafe simulation powered by the OpenAI Responses API.

The app now has two layers. The live shift is still the heart of the experience: two AI baristas work a shared queue while deterministic customers enter, order, sit, consume items, reorder, and leave. Above that shift is a campaign/day layer that turns each run into one day inside a longer cafe save.

The important ownership rule is unchanged: the Python runtime owns the cafe. Agents can only act by calling local tools, and those tools validate every change against shared world state.

## What Is Live Now

CafeLab is built from six nested systems:

1. **Campaign** (`cafe_sim/campaign.py`) - the long-lived save object for cafe name, cash, reputation, persistent supplies, menu state, day history, and current day.
2. **Day** (`cafe_sim/campaign.py`) - one durable calendar day with planning, open, closing, settled, summary, reports, and historical files.
3. **Shift** (`cafe_sim/control.py`, `cafe_sim/runner.py`) - the real-time service window for the current day.
4. **World** (`cafe_sim/world.py`) - the single source of truth for menu items, supplies, tables, staff, orders, customer visits, metrics, and events during a shift.
5. **Agents and customers** - two barista agents, Alex and Jamie, plus deterministic customer simulations.
6. **Dashboard, APIs, and reports** (`dashboard/`, `cafe_sim/api.py`, `cafe_sim/run_report.py`) - live operator visibility, controls, saved run reports, and campaign/day artifacts.

The hierarchy is:

```text
Campaign -> Day -> Shift -> visits, orders, staff actions, events
```

Agents and deterministic customers do not edit the cafe directly. The world accepts, rejects, or records each action. That keeps queue ownership, stock, tables, customer state, day settlement, and reporting in one authoritative chain.

## Campaign And Day Loop

Dashboard mode auto-creates one active campaign when the server starts. A campaign tracks:

- Cafe name
- Current day
- Cash and cumulative revenue/costs
- Reputation
- Persistent supplies
- Menu availability defaults
- Recent day history

Each day moves through:

```text
planning -> open -> closing -> settled
```

After a day settles, the campaign moves into a between-days state. The player can review the recap, then advance to a clean next day. Active customers, active orders, tables, queue state, and temporary agent thinking do not carry forward. Cash, reputation, menu state, persistent supplies, summaries, and report paths do.

This first campaign slice intentionally supports a single auto-created active campaign per server run. Campaign creation/loading endpoints exist as API placeholders, but full multi-campaign selection is not enabled yet. Staff scheduling and menu pricing are also planned-later API stubs.

## Live Shift

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

### Supplies And Menu

The cafe tracks physical supplies:

- Coffee beans
- Milk
- Cups
- Cold brew servings
- Tea bags
- Muffins

Menu items have recipes. Customers only see items that are both manually enabled and possible to make from current stock. If supplies run out, the menu automatically marks affected items as sold out for new customers.

The barista preparation step is the final physical checkpoint. If stock changed after an order was placed, `prepare_order` fails the order cleanly, records the missing supplies, and does not partially decrement inventory.

Outside live service, the dashboard can restock supplies. Restock spending is recorded in the current day's opening plan and counted as a day cost at settlement.

### Customers

Customers are deterministic demand patterns, not LLM agents. Each customer is generated from one of three archetypes: Hurried Commuter, Remote Worker, or Leisure Customer.

A visit can include entering, evaluating friction, ordering, finding a table, waiting, picking up an order, consuming items, dwelling, possibly reordering, and leaving. Customers without a table can still take their order away unless their archetype requires seating.

## Dashboard

Dashboard mode serves a live control room at:

```text
http://127.0.0.1:8000
```

The dashboard shows:

- Campaign status, day, simulated clock, cash, reputation, and recent history
- Live run state, elapsed time, revenue, open orders, table use, and customer count
- Queue pipeline counts for waiting, claimed, in prep, ready, picked up, abandoned, stale, and failed orders
- Alex and Jamie's status, current order, completed count, last action, and reasoning summary when available
- Active customers, table placement, visit phase, order state, held items, and consumed items
- Current supplies with normal, low, and out status
- Menu items marked as orderable, sold out by supplies, or off menu by operator toggle
- Planning/restock controls, day open/close/settle/advance controls, live activity feed, and run settings

The dashboard reads from the runtime snapshot API. It is a visibility and control layer, not a separate simulation.

## Saved Artifacts

### Run Reports

Each shift writes a report directory under:

```text
runs/reports/
```

A run report contains:

- `events.jsonl` - append-only event stream with ordered runtime events
- `summary.json` - final status, timing, metrics, final snapshot, and alerts

Reports include customer spawns, deterministic customer lifecycle events, queue movement, stockout failures, final supplies, revenue, wait times, consumption counts, archetype metrics, barista model activity, barista completion counts, and coordination metrics.

### Campaign Saves

Campaign state is written under:

```text
runs/campaigns/
```

Each campaign contains:

- `campaign.json` - full current campaign save
- `campaign_summary.json` - compact campaign/history summary
- `days/<day_id>/plan.json` - opening plan and restocks for a day
- `days/<day_id>/summary.json` - settled day result
- `days/<day_id>/final_snapshot.json` - final day state
- `days/<day_id>/events.jsonl` - day-scoped event stream

## Repository Layout

```text
cafe/
├── cafe_sim/
│   ├── main.py              # terminal and dashboard entry point
│   ├── campaign.py          # campaign/day save state and settlement
│   ├── world.py             # authoritative WorldState and mutations
│   ├── runner.py            # terminal-mode simulation clock
│   ├── control.py           # dashboard-mode lifecycle and campaign control
│   ├── api.py               # FastAPI server and dashboard API
│   ├── state_view.py        # live snapshot and event read models
│   ├── run_report.py        # durable per-run report writer
│   ├── config.py            # models, timing, menu, recipes, supplies, tables
│   ├── reasoning_summary.py # reasoning summary extraction
│   ├── logger.py            # terminal logging
│   ├── customers/           # deterministic customer archetypes, profiles, decisions, and visit loop
│   ├── agents/
│   │   └── barista.py       # Alex/Jamie barista loop and tools
│   └── requirements.txt
├── dashboard/
│   ├── index.html           # dashboard shell
│   ├── app.js               # live UI and control calls
│   └── styles.css
├── docs/
│   └── multi_day_simulation_spec.md
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

Terminal mode runs one live simulation with colored logs and writes a run report when the run closes.

```bash
cd cafe_sim
python main.py
```

By default, the run lasts 600 seconds, spawns customers roughly every 30 seconds with jitter, allows up to 8 concurrent customers, and gives the cafe 20 seconds of closing grace before unresolved state is closed out.

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

In dashboard mode, use the on-screen controls to open service, close and settle the day, advance to tomorrow, spawn customers, change spawn interval or duration, restock supplies, and toggle menu items.

## Configuration

The main knobs live in `cafe_sim/config.py`.

| Setting | Default | Meaning |
|---|---:|---|
| `BARISTA_MODEL` | `gpt-5.5` | Model used by Alex and Jamie |
| `REASONING_EFFORT` | `low` | Reasoning effort for model calls |
| `REASONING_SUMMARY` | `auto` | Reasoning summary mode |
| `STORE_RESPONSES` | `True` | Whether Responses API calls are stored |
| `SIM_DURATION` | `600` | Run duration in real seconds |
| `CLOSING_GRACE_SECONDS` | `20` | Extra time before closeout |
| `CUSTOMER_SPAWN_INTERVAL` | `30` | Base seconds between customer spawns |
| `CUSTOMER_SPAWN_JITTER` | `0.5` | Random spread around spawn interval |
| `MAX_CONCURRENT_CUSTOMERS` | `8` | Active customer cap |
| `TABLE_SEAT_CAPACITY` | `2` | Seats available at each table |
| `CUSTOMER_RANDOM_SEED` | `None` | Optional seed for reproducible customer archetypes, profiles, orders, and reorders |
| `BARISTA_POLL_INTERVAL` | `5` | Idle wait between barista work cycles |

The same file owns the live menu, recipes, starting supplies, and table IDs.

## Dashboard API

### Live Shift

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/snapshot` | Full live runtime snapshot, including campaign, calendar, day summary, and history |
| `GET` | `/api/events?after=N&limit=100` | Paginated event log |
| `GET` | `/api/events?after=N&day_id=day_001` | Event log filtered by day |
| `GET` | `/api/stream` | 1 Hz server-sent snapshot stream |
| `POST` | `/api/control/start` | Start the dashboard simulation |
| `POST` | `/api/control/stop` | Stop and close out the simulation |
| `POST` | `/api/control/reset` | Reset the current unsettled day to planning |
| `POST` | `/api/control/spawn` | Manually spawn one customer |
| `POST` | `/api/control/settings` | Update spawn interval or duration |
| `POST` | `/api/control/menu/{item_id}` | Turn a menu item on or off |

### Campaign And Day

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/campaign` | Current campaign, calendar, and history snapshot |
| `GET` | `/api/campaigns` | Current single active campaign summary |
| `POST` | `/api/campaign/create` | Placeholder; full campaign creation is not enabled in this slice |
| `POST` | `/api/campaign/load` | Placeholder; campaign loading is not enabled in this slice |
| `POST` | `/api/day/start` | Open the current day |
| `POST` | `/api/day/close` | Close service and settle if possible |
| `POST` | `/api/day/settle` | Settle a stopped day |
| `POST` | `/api/day/advance` | Move from a settled day to the next planning day |
| `GET` | `/api/day/plan` | Current day opening plan |
| `POST` | `/api/day/plan` | Update planning data outside live service |
| `POST` | `/api/restock` | Buy more of one supply outside live service |
| `GET` | `/api/day/{day_id}/summary` | Settled day summary |
| `GET` | `/api/day/{day_id}/snapshot` | Settled day final snapshot |
| `GET` | `/api/day/{day_id}/events` | Settled or current day events |
| `POST` | `/api/staff/schedule` | Placeholder; staff scheduling is planned later |
| `POST` | `/api/menu/prices` | Placeholder; menu pricing is planned later |

## Tests

Run the test suite from the repo root:

```bash
pytest tests/
```

Current tests cover reasoning summary extraction, run reports, spawn timing, two-barista queue coordination, barista shift memory, customer wait behavior, world closeout, campaign snapshot fields, day metadata on events, day close/settle/advance behavior, and restock cost accounting.

## Quick Smoke Run

For a short local run, temporarily lower these values in `cafe_sim/config.py`:

```python
SIM_DURATION = 60
CUSTOMER_SPAWN_INTERVAL = 10
MAX_CONCURRENT_CUSTOMERS = 2
```

Then run dashboard mode:

```bash
cd cafe_sim
python main.py --dashboard
```

Open `http://127.0.0.1:8000`, start the day, spawn or wait for customers, then close and settle the day. You should see Alex and Jamie come on shift, customers spawn, orders move through the queue, supplies decrease as items are prepared, customers pick up and consume items when the visit allows it, and a final day summary become available before advancing to the next day.
