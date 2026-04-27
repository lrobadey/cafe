"""Barista agent loop and tools."""

import asyncio
import json

from config import BARISTA_MODEL, BARISTA_POLL_INTERVAL, MENU, REASONING_EFFORT, STORE_RESPONSES, build_openai_client

client = build_openai_client()

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
            "properties": {"order_id": {"type": "string", "description": "The order_id to claim."}},
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
            "properties": {"order_id": {"type": "string", "description": "The order_id to prepare."}},
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
            "properties": {"order_id": {"type": "string", "description": "The order_id to mark ready."}},
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

BARISTA_INSTRUCTIONS = """You are Alex, the barista at a small coffee shop.

Your job is simple: check the order queue, claim one order at a time, prepare it, and mark it ready.

Work cycle:
1. check_queue
2. If orders exist: claim_order, prepare_order, mark_ready
3. If the queue is empty: idle

Stay focused. Keep the queue moving."""


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


async def run_barista(world: "WorldState"):
    while True:
        input_items = [
            {
                "role": "user",
                "content": "Check the queue and handle the next order. Or idle if empty.",
            }
        ]

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
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": result,
                    }
                )
                if call.name in ("mark_ready", "idle"):
                    done_cycle = True

            if done_cycle:
                break
