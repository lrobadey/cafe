# OpenAI Cafe Simulation — MVP Spec

> Status note: this is the original single-barista MVP spec. The live implementation has moved to CafeLab v0.2: two active baristas, one shared queue, staff state, dashboard staff visibility, and coordination metrics. Use `cafe_sim/NEXT_STEPS.md` for the current implementation status.

**Version:** 0.2  
**Scope:** Tightest buildable OpenAI-specific version. No manager. No event bus. No Redis. No frontend. One barista. One world state dict. Customers spawn on a fixed timer.  
**Goal:** A terminal-observable, genuinely emergent multi-agent simulation that runs in real time using the OpenAI Responses API.

---

## 1. System Shape

The MVP has four nested systems:

1. **World** — the single source of truth: menu, tables, order queue, event log.
2. **Agents** — OpenAI model loops for customers and the barista.
3. **Tools** — local Python functions that let model decisions affect the world.
4. **Runner** — the real-time clock that starts the barista and spawns customers.

Agents never mutate world state directly. They can only request OpenAI function tools. Local Python code executes those tool requests against `WorldState`, then sends `function_call_output` items back to the model.

OpenAI docs grounding:

- Use the Responses API: `https://api.openai.com/v1/responses`
- Use OpenAI function tools: `https://developers.openai.com/api/docs/guides/function-calling`
- Handle tool loops by reading `response.output` items of type `function_call`, executing local code, and returning `function_call_output` items tied to the original `call_id`.

---

## 2. Repository Structure

```text
cafe_sim/
├── main.py              # entry point, starts runner
├── world.py             # WorldState class + asyncio lock
├── runner.py            # SimulationRunner: clock, spawning
├── agents/
│   ├── customer.py      # CustomerAgent loop + customer tools
│   └── barista.py       # BaristaAgent loop + barista tools
├── personas.py          # PERSONAS list
├── logger.py            # pretty terminal logger
├── config.py            # all constants in one place
└── requirements.txt
```

**requirements.txt**

```text
openai>=1.0.0
```

No LangChain. No Agents SDK. No Assistants API. No framework. `asyncio` is standard library and does not belong in requirements.

---

## 3. Config (`config.py`)

Every tunable constant lives here. Nothing is hardcoded elsewhere.

```python
# Model routing
BARISTA_MODEL = "gpt-5.4-mini"
CUSTOMER_MODEL = "gpt-5.4-mini"

# OpenAI Responses API controls
REASONING_EFFORT = "low"
STORE_RESPONSES = False

# Timing (real seconds)
CUSTOMER_SPAWN_INTERVAL = 30
BARISTA_POLL_INTERVAL = 2
CUSTOMER_MAX_WAIT = 90
SIM_DURATION = 600

# Concurrency
MAX_CONCURRENT_CUSTOMERS = 4
MAX_CUSTOMER_HOPS = 12

# Menu (name, price, prep_seconds)
MENU = {
    "espresso": {"name": "Espresso", "price": 3.00, "prep_seconds": 4, "available": True},
    "latte": {"name": "Latte", "price": 5.50, "prep_seconds": 8, "available": True},
    "cold_brew": {"name": "Cold Brew", "price": 5.00, "prep_seconds": 3, "available": True},
    "tea": {"name": "Tea", "price": 3.50, "prep_seconds": 5, "available": True},
    "muffin": {"name": "Blueberry Muffin", "price": 4.00, "prep_seconds": 2, "available": True},
}

# Tables
TABLE_IDS = ["t1", "t2", "t3", "t4"]
```

The process reads `OPENAI_API_KEY` from the environment. If the key is missing, fail fast before the simulation starts.

---

## 4. World State (`world.py`)

Single shared dict behind an `asyncio.Lock`. All agents access world state exclusively through methods on this class.

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
            "order_queue": [],
            "event_log": [],
        }

    def get_menu(self) -> dict:
        return {k: v for k, v in self._state["menu"].items() if v["available"]}

    def get_table_availability(self) -> dict:
        return {tid: t["status"] for tid, t in self._state["tables"].items()}

    def count_empty_tables(self) -> int:
        return sum(1 for t in self._state["tables"].values() if t["status"] == "empty")

    def get_order(self, order_id: str) -> dict | None:
        for order in self._state["order_queue"]:
            if order["order_id"] == order_id:
                return dict(order)
        return None

    def get_pending_unclaimed_orders(self) -> list[dict]:
        return [
            dict(order)
            for order in self._state["order_queue"]
            if order["status"] == "pending"
        ]

    def queue_length(self) -> int:
        return len([
            order
            for order in self._state["order_queue"]
            if order["status"] != "delivered"
        ])

    async def place_order(self, customer_id: str, items: list[str]) -> str:
        order_id = f"ord_{uuid.uuid4().hex[:6]}"
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": items,
            "status": "pending",
            "barista_id": None,
            "placed_at": time.time(),
            "ready_at": None,
        }
        async with self._lock:
            self._state["order_queue"].append(order)
        self.log(customer_id, "place_order", f"items={items} -> {order_id}")
        return order_id

    async def claim_table(self, customer_id: str) -> str | None:
        async with self._lock:
            for table_id, table in self._state["tables"].items():
                if table["status"] == "empty":
                    table["status"] = "occupied"
                    table["customer_id"] = customer_id
                    self.log(customer_id, "claim_table", table_id)
                    return table_id
        return None

    async def release_table(self, customer_id: str):
        async with self._lock:
            for table in self._state["tables"].values():
                if table["customer_id"] == customer_id:
                    table["status"] = "empty"
                    table["customer_id"] = None
        self.log(customer_id, "release_table", "done")

    async def claim_order(self, barista_id: str, order_id: str) -> bool:
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id and order["status"] == "pending":
                    order["status"] = "claimed"
                    order["barista_id"] = barista_id
                    self.log(barista_id, "claim_order", order_id)
                    return True
        return False

    async def mark_order_ready(self, order_id: str):
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id:
                    order["status"] = "ready"
                    order["ready_at"] = time.time()
        self.log("barista", "mark_ready", order_id)

    async def mark_order_delivered(self, order_id: str):
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id:
                    order["status"] = "delivered"
        self.log("barista", "delivered", order_id)

    def log(self, agent_id: str, action: str, detail: str):
        entry = {"t": time.time(), "agent": agent_id, "action": action, "detail": detail}
        self._state["event_log"].append(entry)
        from logger import log_event
        log_event(agent_id, f"{action}: {detail}")
```

---

## 5. Personas (`personas.py`)

12 personas. Runner picks randomly at spawn time. Each persona is injected into the customer instructions.

```python
PERSONAS = [
    {
        "name": "Marcus",
        "mood": "hurried",
        "budget": 8.00,
        "blurb": "You're running late for a meeting. You want something fast: espresso or cold brew only. If the wait looks long, you'll skip it and leave.",
    },
    {
        "name": "Priya",
        "mood": "leisurely",
        "budget": 15.00,
        "blurb": "You have nowhere to be. You'll browse the menu carefully, maybe get a drink and a snack, and take your time enjoying it.",
    },
    {
        "name": "Devon",
        "mood": "picky",
        "budget": 10.00,
        "blurb": "You care a lot about what you order. You dislike espresso. You prefer tea or cold brew. You'll only order if something genuinely appeals to you.",
    },
    {
        "name": "Sam",
        "mood": "regular",
        "budget": 7.00,
        "blurb": "You come here all the time. You almost always get a latte. You're friendly and patient.",
    },
    {
        "name": "Yuki",
        "mood": "budget-conscious",
        "budget": 5.00,
        "blurb": "You only have $5. You'll order the cheapest thing that sounds good. If nothing fits your budget, you'll leave.",
    },
    {
        "name": "Jordan",
        "mood": "indecisive",
        "budget": 12.00,
        "blurb": "You can never quite decide what you want. You'll read the menu a couple of times before ordering. You might end up getting something random.",
    },
    {
        "name": "Elena",
        "mood": "chatty",
        "budget": 9.00,
        "blurb": "You love talking to people. You'll probably strike up a conversation while waiting. You're not in a rush.",
    },
    {
        "name": "Theo",
        "mood": "first-timer",
        "budget": 12.00,
        "blurb": "You've never been to this cafe before. You'll read the menu carefully and maybe ask for a recommendation.",
    },
    {
        "name": "Amara",
        "mood": "hungry",
        "budget": 10.00,
        "blurb": "You're more hungry than thirsty. You definitely want a muffin, and maybe a tea to go with it.",
    },
    {
        "name": "Chris",
        "mood": "skeptical",
        "budget": 8.00,
        "blurb": "You think coffee shops are overpriced. You'll look at the menu and probably grimace at the prices. You might order the cheapest thing or leave.",
    },
    {
        "name": "Nadia",
        "mood": "tired",
        "budget": 8.00,
        "blurb": "You're exhausted. You need caffeine badly. Espresso or latte, whatever gets you moving. You're patient because you don't have the energy not to be.",
    },
    {
        "name": "Felix",
        "mood": "researcher",
        "budget": 20.00,
        "blurb": "You're doing work at the cafe today. You'll order something, find a seat, and stay a while. You like cold brew for long sessions.",
    },
]
```

---

## 6. OpenAI Runtime Contract

OpenAI tools are requests, not authority. The model can ask to call `place_order`, but only local Python can validate the menu, write to `WorldState`, and return the outcome.

Rules:

- `WorldState` is the only source of truth.
- Function calls are the only model outputs that change the simulation.
- Assistant text is flavor and should be logged only if useful for observability.
- Each OpenAI function tool uses strict JSON Schema.
- `parallel_tool_calls=False` keeps each agent to one world action at a time.
- Local `input_items` history is used instead of persistent OpenAI Conversations.
- `store=STORE_RESPONSES` defaults to `False` for private local simulation runs.

Shared OpenAI client helper:

```python
import os
from openai import AsyncOpenAI


def build_openai_client() -> AsyncOpenAI:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY must be set before running the cafe simulation.")
    return AsyncOpenAI()
```

---

## 7. Customer Agent (`agents/customer.py`)

### 7.1 Customer Tools

```python
CUSTOMER_TOOLS = [
    {
        "type": "function",
        "name": "enter_cafe",
        "description": "Enter the cafe and assess whether it is worth staying. Call this first.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_menu",
        "description": "Read all currently available menu items with names and prices. Call this before ordering.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "place_order",
        "description": "Place one order for one or more available menu item IDs.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Menu item IDs to order, such as ['latte', 'muffin'].",
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "find_seat",
        "description": "Claim an available table while waiting for an order.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "check_order",
        "description": "Check the status of the customer's order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order_id returned by place_order.",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "leave",
        "description": "Leave the cafe. Always call this as the final action.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["satisfied", "impatient", "no_seats", "nothing_appealing", "too_expensive"],
                    "description": "Why the customer is leaving.",
                }
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]
```

### 7.2 Customer Instructions

```python
def build_customer_instructions(persona: dict) -> str:
    return f"""You are {persona['name']}, a customer at a small coffee shop.

{persona['blurb']}

Your budget is ${persona['budget']:.2f}. Do not order items that exceed your total budget.

Use cafe tools to move through your visit:
1. enter_cafe
2. read_menu
3. decide whether to order based on your personality and budget
4. place_order if you want something
5. find_seat if available
6. check_order while waiting
7. leave when done or when the cafe is not working for you

Be true to your personality. Keep moving. Always call leave as your final action."""
```

### 7.3 Customer Tool Execution

```python
import asyncio
import time


async def execute_customer_tool(
    tool_name: str,
    tool_input: dict,
    customer_id: str,
    world: "WorldState",
    state: dict,
) -> str:
    if tool_name == "enter_cafe":
        empty = world.count_empty_tables()
        queue_len = world.queue_length()
        return (
            f"You've entered the cafe. Empty tables: {empty}/4. "
            f"Orders currently in queue: {queue_len}. The cafe smells of coffee."
        )

    if tool_name == "read_menu":
        menu = world.get_menu()
        lines = [f"- {v['name']} (ID: {k}): ${v['price']:.2f}" for k, v in menu.items()]
        return "Menu:\n" + "\n".join(lines)

    if tool_name == "place_order":
        items = tool_input.get("items", [])
        available = world.get_menu()
        invalid = [item for item in items if item not in available]
        if invalid:
            return f"Could not place order. These item IDs are not on the menu: {invalid}."
        if state.get("order_id"):
            return "You've already placed an order. Check its status with check_order."
        order_id = await world.place_order(customer_id, items)
        state["order_id"] = order_id
        item_names = [available[item]["name"] for item in items]
        return (
            f"Order placed. Order ID: {order_id}. "
            f"You ordered: {', '.join(item_names)}. "
            f"You are number {world.queue_length()} in the queue."
        )

    if tool_name == "find_seat":
        if state.get("table_id"):
            return f"You're already seated at table {state['table_id']}."
        table_id = await world.claim_table(customer_id)
        if table_id:
            state["table_id"] = table_id
            return f"You found a seat at table {table_id}."
        return "No seats are available right now. You're standing while you wait."

    if tool_name == "check_order":
        order_id = tool_input.get("order_id") or state.get("order_id")
        if not order_id:
            return "You don't have an order to check."
        order = world.get_order(order_id)
        if not order:
            return "Order not found."
        waited = int(time.time() - state["arrived_at"])
        if order["status"] == "ready":
            asyncio.create_task(world.mark_order_delivered(order_id))
            return f"Your order is ready. You pick it up at the counter. Total wait time: {waited}s."
        if order["status"] == "pending":
            return f"Your order is still in the queue. Waited {waited}s so far."
        if order["status"] == "claimed":
            return f"The barista is preparing your order now. Waited {waited}s so far."
        if order["status"] == "delivered":
            return "You already received your order."
        return f"Unknown order status: {order['status']}."

    if tool_name == "leave":
        reason = tool_input.get("reason", "satisfied")
        if state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", reason)
        state["done"] = True
        return f"You leave the cafe. Reason: {reason}."

    return f"Unknown tool: {tool_name}"
```

### 7.4 Customer Loop

```python
import json
import time
from openai import AsyncOpenAI
from config import CUSTOMER_MODEL, MAX_CUSTOMER_HOPS, CUSTOMER_MAX_WAIT, REASONING_EFFORT, STORE_RESPONSES

client = AsyncOpenAI()


async def run_customer(persona: dict, world: "WorldState", customer_id: str):
    instructions = build_customer_instructions(persona)
    input_items = [{
        "role": "user",
        "content": (
            f"You are {persona['name']}. You've just arrived at the cafe door. "
            f"Begin your visit. Remember you have ${persona['budget']:.2f} to spend."
        ),
    }]
    local_state = {
        "order_id": None,
        "table_id": None,
        "done": False,
        "arrived_at": time.time(),
    }

    hops = 0
    while not local_state["done"] and hops < MAX_CUSTOMER_HOPS:
        waited = time.time() - local_state["arrived_at"]
        if waited > CUSTOMER_MAX_WAIT and local_state.get("order_id"):
            input_items.append({
                "role": "user",
                "content": f"You've now been waiting {int(waited)} seconds. Consider leaving if your order still isn't ready.",
            })

        response = await client.responses.create(
            model=CUSTOMER_MODEL,
            instructions=instructions,
            input=input_items,
            tools=CUSTOMER_TOOLS,
            max_output_tokens=512,
            parallel_tool_calls=False,
            store=STORE_RESPONSES,
            reasoning={"effort": REASONING_EFFORT},
        )

        input_items.extend(response.output)
        function_calls = [item for item in response.output if item.type == "function_call"]

        if not function_calls:
            if not local_state["done"]:
                input_items.append({"role": "user", "content": "Please call leave to finish your visit."})
                hops += 1
                continue
            break

        for call in function_calls:
            tool_input = json.loads(call.arguments or "{}")
            result = await execute_customer_tool(
                call.name,
                tool_input,
                customer_id,
                world,
                local_state,
            )
            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,
            })

        hops += 1

    if not local_state["done"]:
        if local_state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", "hop_limit_exceeded")
```

---

## 8. Barista Agent (`agents/barista.py`)

### 8.1 Barista Tools

```python
BARISTA_TOOLS = [
    {
        "type": "function",
        "name": "check_queue",
        "description": "Check all pending, unclaimed orders. Call at the start of each work cycle.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "claim_order",
        "description": "Claim one pending order by order_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order_id to claim."}
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "prepare_order",
        "description": "Prepare a claimed order. This takes real time based on the items.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order_id to prepare."}
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "mark_ready",
        "description": "Mark a prepared order as ready for pickup.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order_id to mark ready."}
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "idle",
        "description": "Take a short break when the queue is empty.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
]
```

### 8.2 Barista Instructions

```python
BARISTA_INSTRUCTIONS = """You are Alex, the barista at a small coffee shop.

Your job is simple: check the order queue, claim one order at a time, prepare it, and mark it ready.

Work cycle:
1. check_queue
2. If orders exist: claim_order, prepare_order, mark_ready
3. If the queue is empty: idle

Stay focused. Keep the queue moving."""
```

### 8.3 Barista Tool Execution

```python
import asyncio
from config import MENU, BARISTA_POLL_INTERVAL


async def execute_barista_tool(tool_name: str, tool_input: dict, world: "WorldState") -> str:
    if tool_name == "check_queue":
        pending = world.get_pending_unclaimed_orders()
        if not pending:
            return "Queue is empty. Nothing to do right now."
        lines = []
        for order in pending:
            items_str = ", ".join(order["items"])
            lines.append(f"- Order {order['order_id']}: {items_str} for customer {order['customer_id']}")
        return f"{len(pending)} order(s) waiting:\n" + "\n".join(lines)

    if tool_name == "claim_order":
        order_id = tool_input["order_id"]
        success = await world.claim_order("barista_alex", order_id)
        if success:
            order = world.get_order(order_id)
            return f"Claimed order {order_id}: {', '.join(order['items'])}. Start preparing it."
        return f"Order {order_id} was already claimed. Check the queue again."

    if tool_name == "prepare_order":
        order_id = tool_input["order_id"]
        order = world.get_order(order_id)
        if not order:
            return f"Order {order_id} not found."
        prep_time = max(MENU.get(item, {}).get("prep_seconds", 5) for item in order["items"])
        await asyncio.sleep(prep_time)
        return f"Prepared order {order_id} in {prep_time}s. Mark it ready."

    if tool_name == "mark_ready":
        order_id = tool_input["order_id"]
        await world.mark_order_ready(order_id)
        return f"Order {order_id} is ready for pickup. Check the queue for more orders."

    if tool_name == "idle":
        await asyncio.sleep(BARISTA_POLL_INTERVAL)
        return "Break done. Check the queue again."

    return f"Unknown tool: {tool_name}"
```

### 8.4 Barista Loop

The barista runs a stateless loop. Each work cycle is a fresh short conversation to keep context small.

```python
import json
from openai import AsyncOpenAI
from config import BARISTA_MODEL, REASONING_EFFORT, STORE_RESPONSES

client = AsyncOpenAI()


async def run_barista(world: "WorldState"):
    while True:
        input_items = [{
            "role": "user",
            "content": "Check the queue and handle the next order. Or idle if empty.",
        }]

        for _ in range(6):
            response = await client.responses.create(
                model=BARISTA_MODEL,
                instructions=BARISTA_INSTRUCTIONS,
                input=input_items,
                tools=BARISTA_TOOLS,
                max_output_tokens=256,
                parallel_tool_calls=False,
                store=STORE_RESPONSES,
                reasoning={"effort": REASONING_EFFORT},
            )

            input_items.extend(response.output)
            function_calls = [item for item in response.output if item.type == "function_call"]

            if not function_calls:
                break

            done_cycle = False
            for call in function_calls:
                tool_input = json.loads(call.arguments or "{}")
                result = await execute_barista_tool(call.name, tool_input, world)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                })
                if call.name in ("mark_ready", "idle"):
                    done_cycle = True

            if done_cycle:
                break
```

---

## 9. Simulation Runner (`runner.py`)

```python
import asyncio
import random
import time
import uuid
from config import CUSTOMER_SPAWN_INTERVAL, MAX_CONCURRENT_CUSTOMERS, SIM_DURATION
from personas import PERSONAS
from agents.customer import run_customer
from agents.barista import run_barista
from world import WorldState
from logger import log_event


async def run_simulation():
    world = WorldState()
    active_customers = set()
    barista_task = asyncio.create_task(run_barista(world))

    start_time = time.time()
    spawn_count = 0

    log_event("RUNNER", "Simulation started. Barista on shift.")

    while time.time() - start_time < SIM_DURATION:
        await asyncio.sleep(CUSTOMER_SPAWN_INTERVAL)
        active_customers = {task for task in active_customers if not task.done()}

        if len(active_customers) >= MAX_CONCURRENT_CUSTOMERS:
            log_event("RUNNER", f"At capacity ({MAX_CONCURRENT_CUSTOMERS} customers). Skipping spawn.")
            continue

        persona = random.choice(PERSONAS)
        customer_id = f"cust_{uuid.uuid4().hex[:4]}"
        spawn_count += 1

        log_event("RUNNER", f"Spawning customer #{spawn_count}: {persona['name']} ({persona['mood']})")
        active_customers.add(asyncio.create_task(run_customer(persona, world, customer_id)))

    barista_task.cancel()
    if active_customers:
        await asyncio.gather(*active_customers, return_exceptions=True)

    log_event("RUNNER", f"Simulation complete. {spawn_count} customers visited.")
    log_event("RUNNER", f"Total events logged: {len(world._state['event_log'])}")
```

---

## 10. Logger (`logger.py`)

```python
import time

COLORS = {
    "RUNNER": "\033[90m",
    "barista": "\033[36m",
    "cust": "\033[33m",
}
RESET = "\033[0m"


def log_event(agent_id: str, message: str):
    color = COLORS.get(agent_id, "")
    for prefix, prefix_color in COLORS.items():
        if agent_id.startswith(prefix):
            color = prefix_color
            break
    timestamp = time.strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] [{agent_id}] {message}{RESET}")
```

---

## 11. Entry Point (`main.py`)

```python
import asyncio
from runner import run_simulation


if __name__ == "__main__":
    asyncio.run(run_simulation())
```

---

## 12. Exact OpenAI Responses API Call Shape

Every model turn uses this shape:

```python
response = await client.responses.create(
    model=MODEL,
    instructions=SYSTEM_PROMPT,
    input=input_items,
    tools=TOOLS,
    max_output_tokens=512,
    parallel_tool_calls=False,
    store=STORE_RESPONSES,
    reasoning={"effort": REASONING_EFFORT},
)
```

Function calls are read from:

```python
function_calls = [
    item
    for item in response.output
    if item.type == "function_call"
]
```

Tool outputs are appended back into local history as:

```python
{
    "type": "function_call_output",
    "call_id": call.call_id,
    "output": result_string,
}
```

The implementation should preserve the model output items in `input_items` before adding function outputs:

```python
input_items.extend(response.output)
input_items.append({
    "type": "function_call_output",
    "call_id": call.call_id,
    "output": result_string,
})
```

This keeps the local conversation complete without using persistent OpenAI Conversations.

---

## 13. What You Can Observe Running This

With default config: 10 minute sim, customer every 30 seconds, max 4 concurrent customers.

- About 20 customer visits.
- Each customer usually performs 6-10 tool calls.
- Barista continuously checks the queue and prepares about one order every 10-15 seconds.
- Terminal output shows every world-changing action.
- Natural pressure emerges around minutes 3-5 as customer concurrency and queue length rise.

Expected emergent failure cases:

- Customer enters, finds no seats, leaves immediately.
- Customer places order, hits patience limit, and leaves before pickup.
- Barista prepares an abandoned order anyway because it remains in the queue.
- Customer checks status while the barista has claimed but not finished the order.

These are features. They reveal the system working.

---

## 14. Verification Plan

Static verification:

- No legacy provider imports, legacy model names, legacy schema fields, legacy tool-result fields, or legacy message-call shapes remain.
- Every OpenAI tool has `type: "function"`, `parameters`, `strict: True`, and `additionalProperties: False`.
- Every tool parameter object lists all properties in `required`.

Dry-run behavior with fake OpenAI responses:

- Customer can enter, read menu, place order, find seat, check order, and leave.
- Barista can check queue, claim order, prepare order, and mark ready.
- Invalid item IDs return a local tool error without corrupting world state.

Live smoke test:

- Temporarily set `SIM_DURATION = 60`, `CUSTOMER_SPAWN_INTERVAL = 10`, and `MAX_CONCURRENT_CUSTOMERS = 2`.
- Run the terminal simulation with `OPENAI_API_KEY` set.
- Confirm logs show customer actions, barista actions, order state transitions, and final summary.

---

## 15. Line Count Estimate

| File | Est. Lines |
|---|---:|
| config.py | 35 |
| world.py | 110 |
| personas.py | 80 |
| agents/customer.py | 210 |
| agents/barista.py | 150 |
| runner.py | 60 |
| logger.py | 20 |
| main.py | 5 |
| **Total** | **~670** |

Buildable in an afternoon. The provider-specific surface is now OpenAI Responses API only.
