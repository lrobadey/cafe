"""Customer agent loop and tools."""

import asyncio
import json
import time

from config import (
    CUSTOMER_MAX_WAIT,
    CUSTOMER_MODEL,
    MAX_CUSTOMER_HOPS,
    REASONING_EFFORT,
    REASONING_SUMMARY,
    STORE_RESPONSES,
    build_openai_client,
)
from reasoning_summary import extract_reasoning_summary_text

client = build_openai_client()

MIN_CUSTOMER_WAIT_SECONDS = 3
MAX_CUSTOMER_WAIT_SECONDS = 15

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
        "name": "wait",
        "description": "Wait briefly in real cafe time before checking the order again.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "minimum": MIN_CUSTOMER_WAIT_SECONDS,
                    "maximum": MAX_CUSTOMER_WAIT_SECONDS,
                    "description": "How many real seconds to wait before the next action.",
                }
            },
            "required": ["seconds"],
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
6. wait briefly, then check_order again while waiting
7. leave when done or when the cafe is not working for you

Be true to your personality. Keep moving. Always call leave as your final action."""


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
            await world.mark_order_delivered(order_id)
            return f"Your order is ready. You pick it up at the counter. Total wait time: {waited}s."
        if order["status"] == "pending":
            return f"Your order is still in the queue. Waited {waited}s so far."
        if order["status"] == "claimed":
            return f"The barista is preparing your order now. Waited {waited}s so far."
        if order["status"] == "delivered":
            return "You already received your order."
        return f"Unknown order status: {order['status']}."

    if tool_name == "wait":
        requested_seconds = tool_input.get("seconds", MIN_CUSTOMER_WAIT_SECONDS)
        wait_seconds = max(MIN_CUSTOMER_WAIT_SECONDS, min(MAX_CUSTOMER_WAIT_SECONDS, int(requested_seconds)))
        await asyncio.sleep(wait_seconds)
        waited_total = int(time.time() - state["arrived_at"])
        if not state.get("order_id"):
            return (
                f"You pause for {wait_seconds}s, but you have not ordered yet. "
                f"You've been in the cafe for {waited_total}s total."
            )
        return f"You wait for {wait_seconds}s. You've been in the cafe for {waited_total}s total."

    if tool_name == "leave":
        reason = tool_input.get("reason", "satisfied")
        if state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", reason)
        state["done"] = True
        return f"You leave the cafe. Reason: {reason}."

    return f"Unknown tool: {tool_name}"


async def run_customer(persona: dict, world: "WorldState", customer_id: str):
    world.report(
        customer_id,
        "agent_started",
        {
            "agent_type": "customer",
            "customer_id": customer_id,
            "persona_name": persona["name"],
            "persona_mood": persona["mood"],
            "budget": persona["budget"],
        },
    )
    instructions = build_customer_instructions(persona)
    input_items = [
        {
            "role": "user",
            "content": (
                f"You are {persona['name']}. You've just arrived at the cafe door. "
                f"Begin your visit. Remember you have ${persona['budget']:.2f} to spend."
            ),
        }
    ]
    local_state = {
        "order_id": None,
        "table_id": None,
        "done": False,
        "arrived_at": time.time(),
    }

    hops = 0
    while not local_state["done"] and hops < MAX_CUSTOMER_HOPS:
        world.report(
            customer_id,
            "agent_hop_started",
            {
                "agent_type": "customer",
                "hop": hops + 1,
                "order_id": local_state.get("order_id"),
                "table_id": local_state.get("table_id"),
            },
        )
        waited = time.time() - local_state["arrived_at"]
        if waited > CUSTOMER_MAX_WAIT and local_state.get("order_id"):
            input_items.append(
                {
                    "role": "user",
                    "content": f"You've now been waiting {int(waited)} seconds. Consider leaving if your order still isn't ready.",
                }
            )

        response = await client.responses.create(
            model=CUSTOMER_MODEL,
            instructions=instructions,
            input=input_items,
            tools=CUSTOMER_TOOLS,
            max_output_tokens=512,
            parallel_tool_calls=False,
            store=STORE_RESPONSES,
            reasoning={"effort": REASONING_EFFORT, "summary": REASONING_SUMMARY},
        )

        reasoning_summary = extract_reasoning_summary_text(response)
        if reasoning_summary:
            world.record_agent_thinking(
                customer_id,
                "customer",
                persona["name"],
                reasoning_summary,
            )

        input_items.extend(response.output)
        function_calls = [item for item in response.output if item.type == "function_call"]
        world.report(
            customer_id,
            "model_response",
            {
                "agent_type": "customer",
                "hop": hops + 1,
                "response_id": getattr(response, "id", None),
                "function_call_count": len(function_calls),
                "output_item_count": len(response.output),
            },
        )

        if not function_calls:
            if not local_state["done"]:
                input_items.append({"role": "user", "content": "Please call leave to finish your visit."})
                hops += 1
                continue
            break

        for call in function_calls:
            tool_input = json.loads(call.arguments or "{}")
            world.report(
                customer_id,
                "tool_call_requested",
                {
                    "agent_type": "customer",
                    "hop": hops + 1,
                    "tool_name": call.name,
                    "call_id": call.call_id,
                    "arguments": tool_input,
                },
            )
            result = await execute_customer_tool(
                call.name,
                tool_input,
                customer_id,
                world,
                local_state,
            )
            world.report(
                customer_id,
                "tool_call_result",
                {
                    "agent_type": "customer",
                    "hop": hops + 1,
                    "tool_name": call.name,
                    "call_id": call.call_id,
                    "result": result,
                },
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                }
            )

        hops += 1

    if not local_state["done"]:
        if local_state.get("table_id"):
            await world.release_table(customer_id)
        world.log(customer_id, "leave", "hop_limit_exceeded")
    world.report(
        customer_id,
        "agent_finished",
        {
            "agent_type": "customer",
            "customer_id": customer_id,
            "done": local_state["done"],
            "order_id": local_state.get("order_id"),
            "table_id": local_state.get("table_id"),
            "hops": hops,
        },
    )
