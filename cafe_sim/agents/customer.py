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
MIN_CUSTOMER_LINGER_SECONDS = 3
MAX_CUSTOMER_LINGER_SECONDS = 15

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
        "name": "sip_drink",
        "description": "Sip one drink item that you have already received from your order.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The drink item ID you received, such as 'latte' or 'tea'.",
                }
            },
            "required": ["item_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "eat_item",
        "description": "Eat one food item that you have already received from your order.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The food item ID you received, such as 'muffin'.",
                }
            },
            "required": ["item_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "linger",
        "description": "Stay briefly at your table after receiving your order.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "minimum": MIN_CUSTOMER_LINGER_SECONDS,
                    "maximum": MAX_CUSTOMER_LINGER_SECONDS,
                    "description": "How many real seconds to linger before the next action.",
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
7. once you receive your order, stay and consume it if you have a seat
8. leave when done or when the cafe is not working for you

After pickup, use sip_drink for drinks and eat_item for food that you actually received.
You may linger briefly at a table after receiving your order. Hurried customers may leave sooner.
Customers without seats may take items away and leave. Always call leave as your final action."""


async def execute_customer_tool(
    tool_name: str,
    tool_input: dict,
    customer_id: str,
    world: "WorldState",
    state: dict,
) -> str:
    if tool_name == "enter_cafe":
        state["visit_phase"] = "arrived"
        await world.update_customer_visit(customer_id, visit_phase="arrived")
        empty = world.count_empty_tables()
        queue_len = world.queue_length()
        return (
            f"You've entered the cafe. Empty tables: {empty}/4. "
            f"Orders currently in queue: {queue_len}. The cafe smells of coffee."
        )

    if tool_name == "read_menu":
        state["visit_phase"] = "ordering"
        await world.update_customer_visit(customer_id, visit_phase="ordering")
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
            current_order = world.get_order(state["order_id"])
            if current_order and current_order.get("status") == "failed":
                state["previous_order_id"] = state["order_id"]
                state["order_id"] = None
            else:
                return "You've already placed an order. Check its status with check_order."
        order_id = await world.place_order(customer_id, items)
        state["order_id"] = order_id
        state["visit_phase"] = "waiting"
        await world.update_customer_visit(customer_id, visit_phase="waiting")
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
            received_at = time.time()
            held_items = list(order["items"])
            state["held_items"] = held_items
            state["visit_phase"] = "received_order"
            state["received_order_at"] = received_at
            item_names = [
                world.get_menu_item(item_id)["name"]
                for item_id in held_items
                if world.get_menu_item(item_id)
            ]
            await world.update_customer_visit(
                customer_id,
                visit_phase="received_order",
                held_items=held_items,
                consumed_items=state.get("consumed_items", []),
                received_order_at=received_at,
            )
            if state.get("table_id"):
                return (
                    f"Your order is ready. You pick it up at the counter: {', '.join(item_names)}. "
                    f"Total wait time: {waited}s. You have a seat, so you can sip_drink, eat_item, linger, or leave."
                )
            return (
                f"Your order is ready. You pick it up at the counter: {', '.join(item_names)}. "
                f"Total wait time: {waited}s. You do not have a seat, so you can take it away and leave."
            )
        if order["status"] == "pending":
            return f"Your order is still in the queue. Waited {waited}s so far."
        if order["status"] in ("claimed", "preparing"):
            return f"The barista is preparing your order now. Waited {waited}s so far."
        if order["status"] == "delivered":
            if state.get("held_items"):
                return "You already received your order. Consume your items or leave when ready."
            return "You already received your order."
        if order["status"] == "failed":
            state["visit_phase"] = "order_failed"
            await world.update_customer_visit(customer_id, visit_phase="order_failed")
            missing = order.get("missing_supplies", {})
            if order.get("close_reason") == "stockout" and missing:
                missing_text = ", ".join(
                    f"{entry.get('name', supply_id)}"
                    for supply_id, entry in missing.items()
                )
                return (
                    f"Your order cannot be completed because the cafe is out of {missing_text}. "
                    "You can leave or place a different order if you still want something else."
                )
            return "Your order cannot be completed. You can leave or ask for something else."
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

    if tool_name == "sip_drink":
        item_id = tool_input.get("item_id")
        result = await world.consume_customer_item(customer_id, item_id, "drink", "sip_drink")
        if not result["ok"]:
            return result["message"]
        state["visit_phase"] = "consuming"
        state["held_items"] = result["held_items"]
        state["consumed_items"] = result["consumed_items"]
        if state.get("consumption_started_at") is None:
            state["consumption_started_at"] = time.time()
        return f"You sip your {result['item_name']}. Consumed items: {', '.join(state['consumed_items'])}."

    if tool_name == "eat_item":
        item_id = tool_input.get("item_id")
        result = await world.consume_customer_item(customer_id, item_id, "food", "eat_item")
        if not result["ok"]:
            return result["message"]
        state["visit_phase"] = "consuming"
        state["held_items"] = result["held_items"]
        state["consumed_items"] = result["consumed_items"]
        if state.get("consumption_started_at") is None:
            state["consumption_started_at"] = time.time()
        return f"You eat your {result['item_name']}. Consumed items: {', '.join(state['consumed_items'])}."

    if tool_name == "linger":
        if not state.get("held_items") and not state.get("consumed_items"):
            return "You have not received an order yet, so linger after pickup instead."
        requested_seconds = tool_input.get("seconds", MIN_CUSTOMER_LINGER_SECONDS)
        linger_seconds = max(
            MIN_CUSTOMER_LINGER_SECONDS,
            min(MAX_CUSTOMER_LINGER_SECONDS, int(requested_seconds)),
        )
        await asyncio.sleep(linger_seconds)
        state["visit_phase"] = "consuming"
        if state.get("consumption_started_at") is None:
            state["consumption_started_at"] = time.time()
        await world.update_customer_visit(
            customer_id,
            visit_phase="consuming",
            consumption_started_at=state.get("consumption_started_at"),
        )
        world.log(customer_id, "linger", f"{linger_seconds}s")
        if state.get("table_id"):
            return f"You linger at table {state['table_id']} for {linger_seconds}s."
        return f"You linger near the counter for {linger_seconds}s with your order."

    if tool_name == "leave":
        reason = tool_input.get("reason", "satisfied")
        held_items = state.get("held_items", [])
        consumed_items = state.get("consumed_items", [])
        left_with_unconsumed = bool(set(held_items) - set(consumed_items))
        state["visit_phase"] = "done"
        await world.update_customer_visit(
            customer_id,
            visit_phase="done",
            held_items=held_items,
            consumed_items=consumed_items,
            left_with_unconsumed_items=left_with_unconsumed,
        )
        if state.get("table_id"):
            await world.release_table(customer_id)
        detail = f"{reason}; left_with_unconsumed={left_with_unconsumed}"
        world.log(customer_id, "leave", detail)
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
        "visit_phase": "arrived",
        "held_items": [],
        "consumed_items": [],
        "received_order_at": None,
        "consumption_started_at": None,
    }
    await world.register_customer_visit(customer_id, persona, local_state["arrived_at"])

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
                "visit_phase": local_state.get("visit_phase"),
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
        held_items = local_state.get("held_items", [])
        consumed_items = local_state.get("consumed_items", [])
        await world.update_customer_visit(
            customer_id,
            visit_phase="done",
            held_items=held_items,
            consumed_items=consumed_items,
            left_with_unconsumed_items=bool(set(held_items) - set(consumed_items)),
        )
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
            "visit_phase": local_state.get("visit_phase"),
            "held_items": local_state.get("held_items", []),
            "consumed_items": local_state.get("consumed_items", []),
            "hops": hops,
        },
    )
