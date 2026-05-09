# Multi-Day Simulation Spec

## Purpose

Turn the cafe app from a single live shift into a real simulation game that unfolds over many cafe days.

The current app is a live operations room: start a run, watch customers arrive, watch baristas prepare orders, see supplies drop, then stop or reset. Multi-day play should keep that satisfying live-shift loop, but place it inside a larger campaign system where decisions, resources, staff memory, reputation, and customer patterns carry forward.

The important design move is nesting:

1. Campaign: the long-lived save file for the cafe.
2. Day: one calendar day inside that campaign.
3. Shift: the live service period for that day.
4. Visits, orders, staff actions, events: the moment-to-moment simulation objects that already exist today.

## Current System Shape

### Backend

The backend currently has one in-memory `WorldState` for one run. That world owns menu, supplies, tables, orders, customer visits, staff, metrics, revenue, and event logs.

`SimulationController` owns a shift-like lifecycle:

- `idle`
- `running`
- `closing`
- `stopped`

Time is real elapsed wall-clock time. A shift ends when `SIM_DURATION` is reached, then closing starts, then the controller stops after a grace period.

Reports are durable, but they are run-level reports, not campaign/day reports. The live world itself is memory-only and `reset` replaces it.

### Frontend

The dashboard is one control room. It has one global snapshot, one event cursor, one rolling event list, and live controls for:

- start
- stop
- reset
- spawn customer
- change spawn interval
- change simulation duration
- toggle menu item availability

The UI does not yet have:

- a current day identity
- previous day history
- end-of-day summaries
- next-day planning
- campaign money/reputation/progression
- a saved-run browser

## Product Target

The player should feel like they are running a cafe over time, not just watching one stress test.

Each day should have a clear rhythm:

1. Morning planning: inspect yesterday, restock, adjust menu, staff, and prices.
2. Open shift: customers arrive and the live sim runs.
3. Closing: new customers stop arriving, active work resolves or gets abandoned.
4. End-of-day settlement: money, waste, satisfaction, reputation, staff effects, and summary are calculated.
5. Advance overnight: persistent cafe state updates and the next day becomes available.

The player should see both levels at once:

- the live operational truth of the current shift
- the larger campaign truth of how the cafe is doing over days

## State Ownership

### Campaign State

Long-lived state that survives across days.

Owns:

- `campaign_id`
- `created_at`
- `current_day_index`
- `cafe_name`
- cumulative money
- cumulative revenue
- cumulative costs
- reputation
- unlocked menu items
- known customers
- staff roster and staff memory
- persistent supply inventory
- historical day summaries
- active day id
- campaign status: `planning`, `open`, `closing`, `settling`, `between_days`, `ended`

This is the top-level save-game object.

### Day State

One calendar day inside the campaign.

Owns:

- `day_id`
- `day_index`
- `date_label`
- `phase`: `planning`, `open`, `closing`, `settled`
- opening plan
- starting supplies
- starting cash
- day-scoped metrics
- day-scoped revenue and costs
- day-scoped event log cursor
- day summary after settlement
- report paths

The day is the durable historical unit the frontend can navigate.

### Shift State

The live simulation window inside one day.

Owns:

- simulated open time
- simulated close time
- elapsed real seconds
- current simulated minute
- customer spawn rules
- active customer tasks
- active barista tasks
- active orders
- occupied tables
- live queue/pipeline
- unresolved closeout state

This layer can reuse much of the current `WorldState` behavior at first.

### Service Objects

The existing moment-to-moment objects should stay day-scoped:

- customer visits
- orders
- tables
- staff assignments
- agent thinking
- active event stream

These should not leak directly into the next day. Only summarized or explicitly persistent effects should carry forward.

## Carry-Forward Rules

### Carries Forward

- money/cash
- reputation
- remaining shelf-stable supplies
- menu unlocks and availability defaults
- staff roster
- staff memory, fatigue, skill, or morale
- customer history if the app later models repeat customers
- cumulative campaign stats
- historical summaries and reports

### Does Not Carry Forward

- active customers
- active orders
- occupied tables
- queue state
- temporary thought bubbles
- shift event cursor
- pending barista tasks
- unresolved order state except as day-end consequences

### Conditional Carry Forward

Perishable supplies should have explicit rules:

- coffee beans, cups, lids: carry forward normally
- milk, pastries, prepared drinks: carry forward only if the model supports freshness
- spoiled/wasted inventory becomes a day-end cost

Staff effects should be simple at first:

- every worked day increases experience slightly
- high pressure or many failures can reduce morale
- rest or good results can recover morale

## Time Model

Add a simulation clock separate from wall-clock time.

Required fields:

- `day_index`
- `day_phase`
- `sim_open_minute`
- `sim_close_minute`
- `sim_current_minute`
- `real_elapsed_seconds`
- `time_scale`

Example:

```json
{
  "calendar": {
    "campaign_id": "campaign_2026_05_07_001",
    "day_id": "day_001",
    "day_index": 1,
    "date_label": "Monday, Spring 1",
    "phase": "open",
    "sim_current_time": "10:35",
    "sim_open_time": "08:00",
    "sim_close_time": "16:00",
    "time_scale": 24
  }
}
```

Wall-clock time still matters for async tasks and the browser stream. Cafe time matters for gameplay.

For the first implementation, the current `SIM_DURATION` can remain the real-time shift duration, but the snapshot should expose it as a simulated day window. Later, customer patterns can depend on simulated time of day.

## Backend Architecture

### New Modules

Add a campaign layer instead of making `WorldState` bigger.

Suggested files:

- `cafe_sim/campaign.py`
- `cafe_sim/day.py`
- `cafe_sim/clock.py`
- `cafe_sim/persistence.py`
- `cafe_sim/settlement.py`

### `CampaignState`

Responsible for the long-lived save.

Key methods:

- `new_campaign(settings) -> CampaignState`
- `current_day() -> DayState`
- `begin_day(plan) -> DayState`
- `settle_day(day_result) -> CampaignState`
- `advance_to_next_day(plan) -> DayState`
- `to_snapshot() -> dict`

### `DayState`

Responsible for one cafe day.

Key methods:

- `create_from_campaign(campaign, plan) -> DayState`
- `attach_world(world)`
- `close_for_service()`
- `settle(world_snapshot) -> DaySummary`
- `to_summary() -> dict`

### `SimulationClock`

Responsible for mapping real elapsed time to cafe time.

Key methods:

- `start(real_time)`
- `now(real_time) -> ClockSnapshot`
- `is_past_close(real_time) -> bool`
- `format_time() -> str`

### `SettlementEngine`

Responsible for day-end consequences.

Inputs:

- starting campaign state
- starting day state
- final world snapshot
- unresolved closeout result

Outputs:

- day revenue
- day costs
- profit
- supply deltas
- waste
- customer satisfaction
- reputation delta
- staff effects
- alerts
- next-day recommendations

The existing `closeout_unresolved` behavior should become a primitive inside day settlement. It already knows how to mark unresolved work and clear active service state.

## Controller Changes

The current controller is shift-shaped. Keep that, but place it inside a campaign controller.

### Existing Shift Controls

Keep:

- start shift
- stop shift
- reset live state
- spawn customer
- change live settings
- toggle menu availability

### New Day/Campaign Controls

Add:

- create campaign
- load campaign
- start current day
- begin closing
- settle current day
- advance to next day
- apply day plan
- list day summaries
- load historical day snapshot/report

### Proposed Lifecycle

```text
No campaign
  -> campaign created

Campaign planning
  -> day planning
  -> day open
  -> day closing
  -> day settling
  -> between days
  -> next day planning
```

The current `idle/running/closing/stopped` can remain as the shift phase, but it should no longer be the whole app phase.

### Stop vs Close Day

These must be different.

`stop` means pause or halt the current live process.

`close day` means the cafe day is over and settlement should happen.

`reset` should be treated as a development/admin action, not the primary gameplay loop.

## Persistence And Reports

The game needs durable campaign state, not only final run reports.

### Suggested Directory Layout

```text
runs/
  campaigns/
    campaign_2026_05_07_001/
      campaign.json
      days/
        day_001/
          plan.json
          events.jsonl
          summary.json
          final_snapshot.json
        day_002/
          plan.json
          events.jsonl
          summary.json
          final_snapshot.json
      campaign_summary.json
```

### Campaign Save

`campaign.json` should be the authoritative reloadable save.

It should include only durable state:

- money
- reputation
- supplies
- staff
- menu state
- customer memory
- current day pointer
- day summaries

It should not include active async task handles, browser cursors, or temporary live UI state.

### Events

Events should include campaign/day metadata:

```json
{
  "id": 42,
  "campaign_id": "campaign_2026_05_07_001",
  "day_id": "day_001",
  "day_index": 1,
  "sim_time": "10:35",
  "type": "order_ready",
  "message": "Order 12 is ready"
}
```

Keep cursor-based event reading, but make the cursor day-scoped.

## API Contract

### Snapshot

Keep `GET /api/snapshot`, but extend it.

Required top-level shape:

```json
{
  "campaign": {
    "campaign_id": "campaign_2026_05_07_001",
    "status": "open",
    "current_day_index": 1,
    "money": 240.5,
    "reputation": 52,
    "days_completed": 0
  },
  "calendar": {
    "day_id": "day_001",
    "day_index": 1,
    "phase": "open",
    "sim_current_time": "10:35",
    "sim_open_time": "08:00",
    "sim_close_time": "16:00"
  },
  "simulation": {},
  "metrics": {},
  "pipeline": {},
  "tables": [],
  "queue": [],
  "menu": [],
  "supplies": [],
  "staff": [],
  "active_customers": [],
  "agent_thinking": [],
  "event_cursor": 42,
  "day_summary": null,
  "history": {
    "recent_days": []
  }
}
```

The existing frontend can keep using the old fields while new panels consume `campaign`, `calendar`, `day_summary`, and `history`.

### Events

Keep:

- `GET /api/events?after=&limit=`

Add optional filters:

- `day_id`
- `campaign_id`

Examples:

- `GET /api/events?after=40&limit=50`
- `GET /api/events?day_id=day_001&after=0&limit=100`

### Campaign Endpoints

Add:

- `POST /api/campaign/create`
- `GET /api/campaign`
- `GET /api/campaigns`
- `POST /api/campaign/load`

### Day Endpoints

Add:

- `POST /api/day/start`
- `POST /api/day/close`
- `POST /api/day/settle`
- `POST /api/day/advance`
- `GET /api/day/{day_id}/summary`
- `GET /api/day/{day_id}/snapshot`
- `GET /api/day/{day_id}/events`

### Planning Endpoints

Add:

- `GET /api/day/plan`
- `POST /api/day/plan`
- `POST /api/restock`
- `POST /api/staff/schedule`
- `POST /api/menu/prices`

For a first version, planning can be narrow:

- restock supplies
- toggle menu items
- choose spawn intensity
- start the next day

Pricing, staffing, upgrades, and customer segments can come later.

## Frontend Spec

### App Shell

The dashboard should become a campaign command center with two modes:

1. Live Day: operate the current cafe day.
2. History/Planning: review prior days and prepare the next one.

The current live control room remains the heart of the app. It should not be hidden behind a landing page.

### Header

Show:

- cafe name
- current day
- simulated time
- day phase
- money
- reputation
- open/close/settle action

Example:

```text
Day 4 | Tuesday, Spring 4 | 10:35 | Open
Cash $240.50 | Reputation 52
```

### Day Timeline

Add a compact day strip:

- Day 1 summary
- Day 2 summary
- Day 3 summary
- Day 4 live

Each day chip should show:

- profit/loss
- customers served
- satisfaction
- warning indicator if the day had unresolved failures

Clicking an old day switches the dashboard into historical mode.

### Live Day View

Keep current panels:

- KPIs
- floor/tables
- staff
- supplies
- activity
- menu/settings

Extend them:

- KPIs become day-scoped and campaign-aware.
- Supplies show starting amount, current amount, and projected shortage.
- Activity groups by simulated cafe time, not only wall-clock time.
- Menu changes during an open day should be visibly marked as live operational changes.

### Planning View

Appears before a day starts and after a day settles.

Initial controls:

- restock common supplies
- set menu availability
- set customer spawn intensity
- review yesterday's alerts
- start next day

Later controls:

- staff scheduling
- prices
- upgrades
- marketing
- opening hours

### End-Of-Day Summary

After settlement, show a summary before advancing:

- revenue
- supply costs
- waste
- profit
- customers served
- customers lost
- average wait
- abandoned/stale orders
- reputation change
- staff notes
- tomorrow warnings

This summary is the player's feedback loop. It turns the live simulation into a game.

### Historical Mode

When viewing a completed day:

- disable live controls
- show final snapshot
- show day summary
- show event log for that day
- show what carried forward into the next day

The player should be able to understand why Day 5 started differently from Day 4.

## Backend Implementation Phases

### Phase 1: Add Day Identity Without Changing Gameplay

Goal: keep the current app working while adding campaign/day fields.

Backend:

- Add `CampaignState` and `DayState`.
- Create a default campaign on app startup.
- Attach `campaign_id`, `day_id`, and `day_index` to snapshots and events.
- Keep current start/stop/reset behavior.
- Keep existing reports, but include day metadata.

Frontend:

- Display day number and phase.
- Display campaign money/reputation placeholders.
- Keep the existing dashboard layout.

Tests:

- snapshot includes campaign/calendar fields
- events include day metadata
- old snapshot fields still exist

### Phase 2: Real Day Close And Settlement

Goal: make the end of a shift produce a day result.

Backend:

- Add `close_day`.
- Use existing unresolved closeout behavior during settlement.
- Calculate daily revenue, waste, customers served/lost, and satisfaction.
- Write `day_001/summary.json`.
- Update campaign money and day history.

Frontend:

- Add end-of-day summary panel.
- Add `Close Day` and `Settle Day` controls.
- Show previous day in a day strip.

Tests:

- active orders do not carry into next day
- revenue and supplies carry forward correctly
- day summary persists
- campaign summary updates

### Phase 3: Advance To Next Day

Goal: the player can play Day 1, settle it, start Day 2, and see carry-forward effects.

Backend:

- Add `advance_day`.
- Create a new day from campaign state.
- Start next day with persistent supplies, money, menu, and staff.
- Clear day-scoped objects.
- Keep day history queryable.

Frontend:

- Add planning state between days.
- Add basic restock controls.
- Show Day 1 and Day 2 as separate timeline entries.

Tests:

- new day starts with clean tables/orders/queue
- supplies persist after settlement/restock
- historical events remain readable
- live event cursor resets or scopes correctly by day

### Phase 4: Make Days Feel Different

Goal: make the simulation game-like over time.

Backend:

- Customer arrival patterns vary by simulated time and reputation.
- Staff skill/morale affects service.
- Menu availability and pricing affect demand.
- Repeat customers can remember prior outcomes.
- Warnings and recommendations are generated from prior day summaries.

Frontend:

- Add reputation trend.
- Add staff notes.
- Add next-day warnings.
- Add comparison between recent days.

Tests:

- reputation affects spawn rules
- staff memory persists
- recommendations derive from real summary data

## Testing Strategy

Add tests at the boundaries where bugs would damage the campaign.

Important test files to add or extend:

- controller day lifecycle tests
- world closeout tests
- run/day report tests
- snapshot compatibility tests
- API endpoint tests
- frontend smoke test if a browser test harness is added later

Specific cases:

- closing a day blocks new customer spawns
- unresolved orders are marked stale or abandoned
- occupied tables are released before the next day
- active customer tasks do not survive day advance
- supplies and money carry forward
- perishable waste is counted once
- day event logs are separate
- historical day reads do not mutate the live day
- reset does not accidentally delete campaign history unless explicitly requested

## Compatibility Rules

The first backend changes should be additive.

Do not remove existing snapshot fields until the frontend has migrated.

Keep current endpoints working:

- `/api/snapshot`
- `/api/events`
- `/api/stream`
- `/api/control/start`
- `/api/control/stop`
- `/api/control/reset`
- `/api/control/spawn`
- `/api/control/settings`
- `/api/menu/{item_id}`

Add new campaign/day fields alongside them.

## Open Design Decisions

These should be decided before implementation begins.

1. Is a campaign auto-created on server startup, or does the player explicitly create/load one?
2. Is there only one active campaign at a time in v1?
3. Should supplies be bought manually between days, or auto-restocked with a cost?
4. Does reputation directly affect customer spawn rate in v1?
5. Are staff members persistent named characters in v1, or still operational worker slots?
6. Should the app support saving/loading active mid-shift state, or only settled day boundaries?

Recommended v1 answers:

1. Auto-create one default campaign on startup.
2. Support one active campaign at a time.
3. Start with manual restock between days.
4. Show reputation in v1, but let it affect demand in Phase 4.
5. Preserve current staff model first; add deeper staff progression after day carry-forward works.
6. Persist settled day boundaries first; mid-shift save/load can come later.

## Recommended First Implementation Slice

The smallest useful slice is:

1. Add campaign/day identity to backend state and snapshots.
2. Add day metadata to events and reports.
3. Add a visible Day 1 header in the dashboard.
4. Add a day-end summary after the existing shift stops.
5. Add `advance_day` that starts Day 2 with clean live state and carried supplies/money.

That gives the app the real skeleton of a multi-day game while preserving the current live cafe simulation.
