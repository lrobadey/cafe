"""Barista agent loop and tools."""

import asyncio
import json
from typing import Optional

from config import (
    BARISTA_MODEL,
    BARISTA_POLL_INTERVAL,
    MENU,
    REASONING_EFFORT,
    REASONING_SUMMARY,
    STORE_RESPONSES,
    build_openai_client,
)
from reasoning_summary import extract_reasoning_summary_text

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


def create_shift_memory() -> dict:
    return {
        "orders_completed": 0,
        "last_completed_order": None,
        "last_action": None,
        "empty_queue_checks": 0,
        "recent_queue_pressure": "empty",
    }


def render_shift_memory(memory: dict) -> str:
    if not memory.get("orders_completed") and not memory.get("last_action"):
        return "Shift memory: no completed orders yet."

    last_completed = memory.get("last_completed_order") or "none yet"
    last_action = memory.get("last_action") or "none yet"
    return "\n".join(
        [
            "Shift memory:",
            f"- Orders completed this shift: {memory.get('orders_completed', 0)}",
            f"- Last completed order: {last_completed}",
            f"- Recent queue pressure: {memory.get('recent_queue_pressure', 'empty')}",
            f"- Empty queue checks in a row: {memory.get('empty_queue_checks', 0)}",
            f"- Last action: {last_action}",
        ]
    )


def build_barista_cycle_prompt(memory: dict) -> str:
    return (
        f"{render_shift_memory(memory)}\n\n"
        "Check the queue and handle the next order. Or idle if empty."
    )


def _extract_order_id_from_mark_ready_result(result: str) -> Optional[str]:
    prefix = "Order "
    suffix = " is ready for pickup."
    if not result.startswith(prefix) or suffix not in result:
        return None
    return result[len(prefix) : result.index(suffix)]


def update_shift_memory(memory: dict, tool_name: str, result: str) -> None:
    if tool_name == "check_queue":
        if result.startswith("Queue is empty."):
            memory["empty_queue_checks"] = memory.get("empty_queue_checks", 0) + 1
            memory["recent_queue_pressure"] = "empty"
            memory["last_action"] = "checked queue; it was empty"
            return

        memory["empty_queue_checks"] = 0
        if result.startswith("1 order(s) waiting:"):
            memory["recent_queue_pressure"] = "normal"
        else:
            memory["recent_queue_pressure"] = "busy"
        memory["last_action"] = "checked queue; orders were waiting"
        return

    if tool_name == "mark_ready":
        order_id = _extract_order_id_from_mark_ready_result(result)
        if order_id:
            memory["orders_completed"] = memory.get("orders_completed", 0) + 1
            memory["last_completed_order"] = order_id
            memory["last_action"] = f"marked {order_id} ready"
        return

    if tool_name == "claim_order":
        memory["last_action"] = result
        return

    if tool_name == "prepare_order":
        memory["last_action"] = result
        return

    if tool_name == "idle":
        memory["last_action"] = "idled while queue was empty"


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
    world.report("barista_alex", "agent_started", {"agent_type": "barista"})
    cycle = 0
    shift_memory = create_shift_memory()
    while True:
        cycle += 1
        world.report("barista_alex", "agent_cycle_started", {"agent_type": "barista", "cycle": cycle})
        input_items = [
            {
                "role": "user",
                "content": build_barista_cycle_prompt(shift_memory),
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
                reasoning={"effort": REASONING_EFFORT, "summary": REASONING_SUMMARY},
            )

            reasoning_summary = extract_reasoning_summary_text(response)
            if reasoning_summary:
                world.record_agent_thinking(
                    "barista_alex",
                    "barista",
                    "Alex",
                    reasoning_summary,
                )

            input_items.extend(response.output)
            function_calls = [item for item in response.output if item.type == "function_call"]
            world.report(
                "barista_alex",
                "model_response",
                {
                    "agent_type": "barista",
                    "cycle": cycle,
                    "response_id": getattr(response, "id", None),
                    "function_call_count": len(function_calls),
                    "output_item_count": len(response.output),
                },
            )

            if not function_calls:
                break

            done_cycle = False
            for call in function_calls:
                tool_input = json.loads(call.arguments or "{}")
                world.report(
                    "barista_alex",
                    "tool_call_requested",
                    {
                        "agent_type": "barista",
                        "cycle": cycle,
                        "tool_name": call.name,
                        "call_id": call.call_id,
                        "arguments": tool_input,
                    },
                )
                result = await execute_barista_tool(call.name, tool_input, world)
                update_shift_memory(shift_memory, call.name, result)
                world.report(
                    "barista_alex",
                    "tool_call_result",
                    {
                        "agent_type": "barista",
                        "cycle": cycle,
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
                if call.name in ("mark_ready", "idle"):
                    done_cycle = True

            if done_cycle:
                break
