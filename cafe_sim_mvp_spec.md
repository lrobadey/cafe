# LLM Cafe Simulation — MVP Spec

**Version:** 0.1  
**Scope:** Tightest buildable version. No manager. No event bus. No Redis. No frontend. One barista. One world state dict. Customers spawn on a fixed timer.  
**Goal:** A terminal-observable, genuinely emergent multi-agent simulation that runs in real time.

---

## 1. Repository Structure

```
cafe_sim/
├── main.py              # entry point — starts runner
├── world.py             # WorldState class + asyncio lock
├── runner.py            # SimulationRunner — clock, spawning
├── agents/
│   ├── customer.py      # CustomerAgent class + tools
│   └── barista.py       # BaristaAgent class + tools
├── personas.py          # PERSONAS list
├── logger.py            # pretty terminal logger
├── config.py            # all constants in one place
└── requirements.txt
```

**requirements.txt**
```
anthropic>=0.25.0
asyncio
```

That's the entire dependency surface. No LangChain. No framework.

---

## 2. Config (`config.py`)

Every tunable constant lives here. Nothing is hardcoded elsewhere.

```python
# Model routing
BARISTA_MODEL   = "claude-haiku-4-5"
CUSTOMER_MODEL  = "claude-haiku-4-5"

# Timing (real seconds)
CUSTOMER_SPAWN_INTERVAL = 30      # new customer every N seconds
BARISTA_POLL_INTERVAL   = 2       # barista checks queue every N seconds if idle
CUSTOMER_MAX_WAIT       = 90      # seconds before impatient customer leaves
SIM_DURATION            = 600     # total run time in seconds (10 min)

# Concurrency
MAX_CONCURRENT_CUSTOMERS = 4      # hard cap on simultaneous customer agents
MAX_CUSTOMER_HOPS        = 12     # hard ReAct hop limit per customer

# Menu (name, price, prep_seconds)
MENU = {
    "espresso":  {"name": "Espresso",      "price": 3.00, "prep_seconds": 4,  "available": True},
    "latte":     {"name": "Latte",         "price": 5.50, "prep_seconds": 8,  "available": True},
    "cold_brew": {"name": "Cold Brew",     "price": 5.00, "prep_seconds": 3,  "available": True},
    "tea":       {"name": "Tea",           "price": 3.50, "prep_seconds": 5,  "available": True},
    "muffin":    {"name": "Blueberry Muffin", "price": 4.00, "prep_seconds": 2, "available": True},
}

# Tables
TABLE_IDS = ["t1", "t2", "t3", "t4"]
```

---

## 3. World State (`world.py`)

Single shared dict behind an `asyncio.Lock`. All agents access world state exclusively through the methods on this class — never by reaching into the dict directly.

```python
import asyncio
import time
import uuid
from config import MENU, TABLE_IDS

class WorldState:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._state = {
            "menu": {k: dict(v) for k, v in MENU.items()},
            "tables": {
                tid: {"status": "empty", "customer_id": None}
                for tid in TABLE_IDS
            },
            "order_queue": [],   # list of order dicts
            "event_log": [],     # append-only
        }

    # ── Read helpers (no lock needed — reads are safe for our scale) ──────────

    def get_menu(self) -> dict:
        return {k: v for k, v in self._state["menu"].items() if v["available"]}

    def get_table_availability(self) -> dict:
        """Returns {table_id: status} for all tables."""
        return {tid: t["status"] for tid, t in self._state["tables"].items()}

    def count_empty_tables(self) -> int:
        return sum(1 for t in self._state["tables"].values() if t["status"] == "empty")

    def get_order(self, order_id: str) -> dict | None:
        for o in self._state["order_queue"]:
            if o["order_id"] == order_id:
                return dict(o)
        return None

    def get_pending_unclaimed_orders(self) -> list:
        return [
            dict(o) for o in self._state["order_queue"]
            if o["status"] == "pending"
        ]

    def queue_length(self) -> int:
        return len([o for o in self._state["order_queue"] if o["status"] != "delivered"])

    # ── Write methods (all use lock) ──────────────────────────────────────────

    async def place_order(self, customer_id: str, items: list[str]) -> str:
        """Append a new order. Returns order_id."""
        order_id = f"ord_{uuid.uuid4().hex[:6]}"
        order = {
            "order_id":   order_id,
            "customer_id": customer_id,
            "items":       items,
            "status":     "pending",
            "barista_id": None,
            "placed_at":  time.time(),
            "ready_at":   None,
        }
        async with self._lock:
            self._state["order_queue"].append(order)
        self.log(customer_id, "place_order", f"items={items} → {order_id}")
        return order_id

    async def claim_table(self, customer_id: str) -> str | None:
        """Claim the first empty table. Returns table_id or None."""
        async with self._lock:
            for tid, t in self._state["tables"].items():
                if t["status"] == "empty":
                    t["status"] = "occupied"
                    t["customer_id"] = customer_id
                    self.log(customer_id, "claim_table", tid)
                    return tid
        return None

    async def release_table(self, customer_id: str):
        async with self._lock:
            for t in self._state["tables"].values():
                if t["customer_id"] == customer_id:
                    t["status"] = "empty"
                    t["customer_id"] = None
        self.log(customer_id, "release_table", "done")

    async def claim_order(self, barista_id: str, order_id: str) -> bool:
        """Returns True if successfully claimed, False if already taken."""
        async with self._lock:
            for o in self._state["order_queue"]:
                if o["order_id"] == order_id and o["status"] == "pending":
                    o["status"]    = "claimed"
                    o["barista_id"] = barista_id
                    self.log(barista_id, "claim_order", order_id)
                    return True
        return False

    async def mark_order_ready(self, order_id: str):
        async with self._lock:
            for o in self._state["order_queue"]:
                if o["order_id"] == order_id:
                    o["status"]   = "ready"
                    o["ready_at"] = time.time()
        self.log("barista", "mark_ready", order_id)

    async def mark_order_delivered(self, order_id: str):
        async with self._lock:
            for o in self._state["order_queue"]:
                if o["order_id"] == order_id:
                    o["status"] = "delivered"
        self.log("barista", "delivered", order_id)

    def log(self, agent_id: str, action: str, detail: str):
        self._state["event_log"].append({
            "t":       time.time(),
            "agent":   agent_id,
            "action":  action,
            "detail":  detail,
        })
```

---

## 4. Personas (`personas.py`)

12 personas. Runner picks randomly at spawn time. Each is a 3-line string injected directly into the customer system prompt.

```python
PERSONAS = [
    {
        "name":   "Marcus",
        "mood":   "hurried",
        "budget": 8.00,
        "blurb":  "You're running late for a meeting. You want something fast — espresso or cold brew only. If the wait looks long, you'll skip it and leave.",
    },
    {
        "name":   "Priya",
        "mood":   "leisurely",
        "budget": 15.00,
        "blurb":  "You have nowhere to be. You'll browse the menu carefully, maybe get a drink and a snack, and take your time enjoying it.",
    },
    {
        "name":   "Devon",
        "mood":   "picky",
        "budget": 10.00,
        "blurb":  "You care a lot about what you order. You dislike espresso. You prefer tea or cold brew. You'll only order if something genuinely appeals to you.",
    },
    {
        "name":   "Sam",
        "mood":   "regular",
        "budget": 7.00,
        "blurb":  "You come here all the time. You almost always get a latte. You're friendly and patient.",
    },
    {
        "name":   "Yuki",
        "mood":   "budget-conscious",
        "budget": 5.00,
        "blurb":  "You only have $5. You'll order the cheapest thing that sounds good. If nothing fits your budget, you'll leave.",
    },
    {
        "name":   "Jordan",
        "mood":   "indecisive",
        "budget": 12.00,
        "blurb":  "You can never quite decide what you want. You'll read the menu a couple of times before ordering. You might end up getting something random.",
    },
    {
        "name":   "Elena",
        "mood":   "chatty",
        "budget": 9.00,
        "blurb":  "You love talking to people. You'll probably strike up a conversation while waiting. You're not in a rush.",
    },
    {
        "name":   "Theo",
        "mood":   "first-timer",
        "budget": 12.00,
        "blurb":  "You've never been to this cafe before. You'll read the menu carefully and maybe ask for a recommendation.",
    },
    {
        "name":   "Amara",
        "mood":   "hungry",
        "budget": 10.00,
        "blurb":  "You're more hungry than thirsty. You definitely want a muffin, and maybe a tea to go with it.",
    },
    {
        "name":   "Chris",
        "mood":   "skeptical",
        "budget": 8.00,
        "blurb":  "You think coffee shops are overpriced. You'll look at the menu and probably grimace at the prices. You might order the cheapest thing or leave.",
    },
    {
        "name":   "Nadia",
        "mood":   "tired",
        "budget": 8.00,
        "blurb":  "You're exhausted. You need caffeine badly. Espresso or latte, whatever gets you moving. You're patient because you don't have the energy not to be.",
    },
    {
        "name":   "Felix",
        "mood":   "researcher",
        "budget": 20.00,
        "blurb":  "You're doing work at the cafe today. You'll order something, find a seat, and stay a while. You like cold brew for long sessions.",
    },
]
```

---

## 5. Customer Agent (`agents/customer.py`)

### 5.1 Tool Definitions (passed to Anthropic API)

```python
CUSTOMER_TOOLS = [
    {
        "name": "enter_cafe",
        "description": (
            "Enter the cafe and assess whether it's worth staying. "
            "Returns how many tables are available and how long the order queue is. "
            "Call this first, before anything else."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_menu",
        "description": (
            "Read the full menu. Returns all currently available items "
            "with their names and prices. Call this before deciding what to order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "place_order",
        "description": (
            "Place an order for one or more items from the menu. "
            "Returns your order_id and your position in the queue. "
            "Only call this once. Only order items that are on the menu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of item IDs to order (e.g. ['latte', 'muffin']).",
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "find_seat",
        "description": (
            "Claim an available table to sit at while waiting for your order. "
            "Returns 'seated' with a table_id, or 'no_seats' if the cafe is full."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_order",
        "description": (
            "Check the status of your order. Returns one of: "
            "'pending' (in queue), 'claimed' (barista is making it), "
            "'ready' (waiting for you at the counter), 'delivered'. "
            "Call this while you're waiting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id returned when you placed your order.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "leave",
        "description": (
            "Leave the cafe. Call this when you're done (satisfied), "
            "if you've been waiting too long, if there are no seats, "
            "or if nothing on the menu appeals to you. "
            "Always call this as your final action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["satisfied", "impatient", "no_seats", "nothing_appealing", "too_expensive"],
                    "description": "Why you're leaving.",
                }
            },
            "required": ["reason"],
        },
    },
]
```

### 5.2 System Prompt

```python
def build_customer_system_prompt(persona: dict) -> str:
    return f"""You are {persona['name']}, a customer at a small coffee shop.

{persona['blurb']}

Your budget is ${persona['budget']:.2f}. Do not order items that exceed your total budget.

You will use tools to navigate the cafe visit. Work through your visit step by step:
1. Enter the cafe and assess the situation
2. Read the menu
3. Decide whether to order based on your personality and budget
4. Place an order if you want something
5. Find a seat if available
6. Wait for your order, checking its status
7. Leave when done (or if something isn't working for you)

Be true to your personality throughout. Think briefly before each tool call.
Always call leave() as your final action — never end without it.
You have a limited number of steps, so don't repeat yourself unnecessarily."""
```

### 5.3 Tool Execution

```python
import asyncio
import time

async def execute_customer_tool(
    tool_name: str,
    tool_input: dict,
    customer_id: str,
    world: "WorldState",
    state: dict,  # local mutable state: {order_id, table_id, seated, arrived_at}
) -> str:
    """Execute a customer tool call. Returns result string for the LLM."""

    if tool_name == "enter_cafe":
        empty = world.count_empty_tables()
        q_len = world.queue_length()
        return (
            f"You've entered the cafe. "
            f"Empty tables: {empty}/4. "
            f"Orders currently in queue: {q_len}. "
            f"The cafe smells of coffee."
        )

    elif tool_name == "read_menu":
        menu = world.get_menu()
        lines = [f"- {v['name']} (ID: {k}): ${v['price']:.2f}" for k, v in menu.items()]
        return "Menu:\n" + "\n".join(lines)

    elif tool_name == "place_order":
        items = tool_input.get("items", [])
        # Validate items exist on menu
        available = world.get_menu()
        invalid = [i for i in items if i not in available]
        if invalid:
            return f"Could not place order. These items aren't on the menu: {invalid}. Try again with valid item IDs."
        if state.get("order_id"):
            return "You've already placed an order. Check its status with check_order."
        order_id = await world.place_order(customer_id, items)
        state["order_id"] = order_id
        q_pos = world.queue_length()
        item_names = [available[i]["name"] for i in items if i in available]
        return (
            f"Order placed! Order ID: {order_id}. "
            f"You ordered: {', '.join(item_names)}. "
            f"You are number {q_pos} in the queue."
        )

    elif tool_name == "find_seat":
        if state.get("table_id"):
            return f"You're already seated at table {state['table_id']}."
        table_id = await world.claim_table(customer_id)
        if table_id:
            state["table_id"] = table_id
            return f"You found a seat at table {table_id}. Make yourself comfortable."
        else:
            return "No seats available right now. You're standing while you wait."

    elif tool_name == "check_order":
        order_id = tool_input.get("order_id") or state.get("order_id")
        if not order_id:
            return "You don't have an order to check."
        order = world.get_order(order_id)
        if not order:
            return "Order not found."
        waited = int(time.time() - state["arrived_at"])
        if order["status"] == "ready":
            # Auto-deliver: mark delivered, return success
            asyncio.create_task(world.mark_order_delivered(order_id))
            return (
                f"Your order is ready! You pick it up at the counter. "
                f"Total wait time: {waited}s."
            )
        status_msgs = {
            "pending":  "Your order is still in the queue. The barista hasn't started it yet.",
            "claimed":  "The barista has your order and is preparing it now.",
            "delivered": "You already received your order.",
        }
        return status_msgs.get(order["status"], "Unknown status.") + f" (waited {waited}s so far)"

    elif tool_name == "leave":
        reason = tool_input.get("reason", "satisfied")
        if state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", reason)
        state["done"] = True
        return f"You leave the cafe. Reason: {reason}."

    return f"Unknown tool: {tool_name}"
```

### 5.4 Agent Loop

```python
import anthropic
from config import CUSTOMER_MODEL, MAX_CUSTOMER_HOPS, CUSTOMER_MAX_WAIT

client = anthropic.AsyncAnthropic()

async def run_customer(persona: dict, world: "WorldState", customer_id: str):
    system = build_customer_system_prompt(persona)
    messages = []
    local_state = {
        "order_id":   None,
        "table_id":   None,
        "done":       False,
        "arrived_at": time.time(),
    }

    # Seed the conversation
    messages.append({
        "role": "user",
        "content": (
            f"You are {persona['name']}. You've just arrived at the cafe door. "
            f"Begin your visit. Remember you have ${persona['budget']:.2f} to spend."
        ),
    })

    hops = 0
    while not local_state["done"] and hops < MAX_CUSTOMER_HOPS:

        # Patience check — insert a nudge if they've waited too long
        waited = time.time() - local_state["arrived_at"]
        if waited > CUSTOMER_MAX_WAIT and local_state.get("order_id") and not local_state["done"]:
            messages.append({
                "role": "user",
                "content": (
                    f"You've now been waiting {int(waited)} seconds. "
                    f"You're getting impatient. Consider leaving if your order still isn't ready."
                ),
            })

        response = await client.messages.create(
            model=CUSTOMER_MODEL,
            max_tokens=512,
            system=system,
            tools=CUSTOMER_TOOLS,
            messages=messages,
        )

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # If no tool use, model is done reasoning — prompt it to act
        if response.stop_reason == "end_turn":
            # Check if it forgot to call leave()
            if not local_state["done"]:
                messages.append({
                    "role": "user",
                    "content": "Please call leave() to finish your visit.",
                })
                hops += 1
                continue
            break

        # Process all tool calls in this response
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await execute_customer_tool(
                    block.name,
                    block.input,
                    customer_id,
                    world,
                    local_state,
                )
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        hops += 1

    # Hard exit — clean up if hop limit hit
    if not local_state["done"]:
        if local_state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", "hop_limit_exceeded")
```

---

## 6. Barista Agent (`agents/barista.py`)

### 6.1 Tool Definitions

```python
BARISTA_TOOLS = [
    {
        "name": "check_queue",
        "description": (
            "Check the current order queue. Returns all pending, unclaimed orders "
            "waiting to be made. Call this at the start of each work cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "claim_order",
        "description": (
            "Claim an order from the queue to start making it. "
            "Returns 'claimed' if successful, 'already_claimed' if another barista got it first. "
            "Claim one order at a time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id to claim.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "prepare_order",
        "description": (
            "Prepare the claimed order. This takes real time based on the items. "
            "Returns when the order is ready. Call this after claiming an order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id to prepare.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "mark_ready",
        "description": (
            "Mark the prepared order as ready for the customer to pick up. "
            "Call this immediately after prepare_order completes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id to mark ready.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "idle",
        "description": (
            "Nothing in the queue right now. Take a short break. "
            "Call this when check_queue returns no orders. "
            "You'll automatically be prompted again soon."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
```

### 6.2 System Prompt

```python
BARISTA_SYSTEM = """You are Alex, the barista at a small coffee shop.

Your job is simple: check the order queue, claim orders one at a time, prepare them, and mark them ready. Work through orders efficiently. 

Work cycle each loop:
1. check_queue — see what's waiting
2. If orders exist: claim_order → prepare_order → mark_ready → loop back
3. If queue is empty: call idle

Stay focused. Don't overthink. Keep moving through orders."""
```

### 6.3 Tool Execution

```python
import asyncio
from config import MENU, BARISTA_POLL_INTERVAL

async def execute_barista_tool(
    tool_name: str,
    tool_input: dict,
    world: "WorldState",
) -> str:

    if tool_name == "check_queue":
        pending = world.get_pending_unclaimed_orders()
        if not pending:
            return "Queue is empty. Nothing to do right now."
        lines = []
        for o in pending:
            items_str = ", ".join(o["items"])
            lines.append(f"- Order {o['order_id']}: {items_str} (for customer {o['customer_id']})")
        return f"{len(pending)} order(s) waiting:\n" + "\n".join(lines)

    elif tool_name == "claim_order":
        order_id = tool_input["order_id"]
        success = await world.claim_order("barista_alex", order_id)
        if success:
            order = world.get_order(order_id)
            items_str = ", ".join(order["items"])
            return f"Claimed order {order_id}: {items_str}. Start preparing it."
        return f"Order {order_id} was already claimed. Check the queue again."

    elif tool_name == "prepare_order":
        order_id = tool_input["order_id"]
        order = world.get_order(order_id)
        if not order:
            return f"Order {order_id} not found."
        # Calculate prep time: max of all items' prep_seconds
        prep_time = max(
            MENU.get(item, {}).get("prep_seconds", 5)
            for item in order["items"]
        )
        await asyncio.sleep(prep_time)
        return f"Prepared order {order_id} in {prep_time}s. Mark it ready."

    elif tool_name == "mark_ready":
        order_id = tool_input["order_id"]
        await world.mark_order_ready(order_id)
        return f"Order {order_id} is ready for pickup. Check the queue for more orders."

    elif tool_name == "idle":
        await asyncio.sleep(BARISTA_POLL_INTERVAL)
        return "Break done. Check the queue again."

    return f"Unknown tool: {tool_name}"
```

### 6.4 Barista Loop

The barista runs a **stateless loop**: each iteration is a fresh short conversation (3–5 turns max). This keeps context small and prevents drift over a long shift.

```python
async def run_barista(world: "WorldState"):
    """Runs forever until the simulation ends."""
    while True:
        messages = [{
            "role": "user",
            "content": "Check the queue and handle the next order. Or idle if empty.",
        }]

        # Short bounded loop: claim + prepare + mark_ready = 4 tool calls max
        for _ in range(6):
            response = await client.messages.create(
                model=BARISTA_MODEL,
                max_tokens=256,
                system=BARISTA_SYSTEM,
                tools=BARISTA_TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            tool_results = []
            done_cycle = False
            for block in response.content:
                if block.type == "tool_use":
                    result = await execute_barista_tool(block.name, block.input, world)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })
                    # After mark_ready or idle, end this conversation cycle
                    if block.name in ("mark_ready", "idle"):
                        done_cycle = True

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            if done_cycle:
                break
        # Loop immediately — barista never stops working
```

---

## 7. Simulation Runner (`runner.py`)

```python
import asyncio
import random
import time
import uuid
from config import (
    CUSTOMER_SPAWN_INTERVAL,
    MAX_CONCURRENT_CUSTOMERS,
    SIM_DURATION,
)
from personas import PERSONAS
from agents.customer import run_customer
from agents.barista import run_barista
from world import WorldState
from logger import log_event

async def run_simulation():
    world = WorldState()
    active_customers = set()  # track running customer tasks

    # Start the barista as a persistent background task
    barista_task = asyncio.create_task(run_barista(world))

    start_time = time.time()
    spawn_count = 0

    log_event("RUNNER", "Simulation started. Barista on shift.")

    while time.time() - start_time < SIM_DURATION:
        await asyncio.sleep(CUSTOMER_SPAWN_INTERVAL)

        # Prune finished customer tasks
        active_customers = {t for t in active_customers if not t.done()}

        if len(active_customers) >= MAX_CONCURRENT_CUSTOMERS:
            log_event("RUNNER", f"At capacity ({MAX_CONCURRENT_CUSTOMERS} customers). Skipping spawn.")
            continue

        # Spawn a customer
        persona = random.choice(PERSONAS)
        customer_id = f"cust_{uuid.uuid4().hex[:4]}"
        spawn_count += 1

        log_event("RUNNER", f"Spawning customer #{spawn_count}: {persona['name']} ({persona['mood']})")

        task = asyncio.create_task(
            run_customer(persona, world, customer_id)
        )
        active_customers.add(task)

    # Sim over — cancel barista, wait for customers to finish
    barista_task.cancel()
    if active_customers:
        await asyncio.gather(*active_customers, return_exceptions=True)

    log_event("RUNNER", f"Simulation complete. {spawn_count} customers visited.")
    log_event("RUNNER", f"Total events logged: {len(world._state['event_log'])}")
```

---

## 8. Logger (`logger.py`)

Simple, readable terminal output. Each agent gets a color.

```python
import time

COLORS = {
    "RUNNER":  "\033[90m",   # gray
    "barista": "\033[36m",   # cyan
    "cust":    "\033[33m",   # yellow (prefix match)
}
RESET = "\033[0m"

def log_event(agent_id: str, message: str):
    color = COLORS.get(agent_id, "")
    for prefix, c in COLORS.items():
        if agent_id.startswith(prefix):
            color = c
            break
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{agent_id}] {message}{RESET}")
```

Hook this into `world.log()` so every world state write prints to terminal automatically.

```python
# In WorldState.log():
def log(self, agent_id: str, action: str, detail: str):
    entry = {"t": time.time(), "agent": agent_id, "action": action, "detail": detail}
    self._state["event_log"].append(entry)
    from logger import log_event
    log_event(agent_id, f"{action}: {detail}")
```

---

## 9. Entry Point (`main.py`)

```python
import asyncio
from runner import run_simulation

if __name__ == "__main__":
    asyncio.run(run_simulation())
```

---

## 10. Exact API Call Shape

Every LLM call in this system looks like this. No framework abstractions.

```python
response = await client.messages.create(
    model=MODEL,
    max_tokens=512,          # customers / 256 for barista
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,       # full history for this agent's current loop
)
```

Tool results are fed back as:
```python
{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": block.id, "content": result_string}
]}
```

No streaming. No batching. Simple request-response.

---

## 11. What You Can Observe Running This

With default config (10 min sim, customer every 30s, max 4 concurrent):

- ~20 customer visits
- Each customer: 6–10 tool calls, ~15–25 real seconds of lifecycle
- Barista: continuous loop, ~1 order every 10–15 seconds
- Terminal output: color-coded stream of every action by every agent
- Natural emergent pressure: at minutes 3–5, with 3–4 concurrent customers, the queue backs up, patience checks start firing, hurried personas leave early

**Failure cases you'll see naturally:**
- Customer spawns, finds no seats, leaves immediately
- Customer places order, hits patience limit before barista catches up, leaves (order stays in queue, barista prepares it anyway, marks ready — no one picks it up)
- Barista claims order while customer is mid-loop checking status

All of these are features. They reveal the system working.

---

## 12. Line Count Estimate

| File | Est. Lines |
|---|---|
| config.py | 30 |
| world.py | 110 |
| personas.py | 80 |
| agents/customer.py | 180 |
| agents/barista.py | 130 |
| runner.py | 60 |
| logger.py | 20 |
| main.py | 5 |
| **Total** | **~615** |

Buildable in an afternoon. Every line is load-bearing.
