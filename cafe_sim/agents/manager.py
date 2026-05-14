"""Manager agent loop and tools for planning restocks."""

import json
from typing import Optional

from config import (
    MANAGER_MODEL,
    MANAGER_REASONING_EFFORT,
    MANAGER_REASONING_SUMMARY,
    MENU,
    MENU_RECIPES,
    STORE_RESPONSES,
    build_openai_client,
)
from reasoning_summary import extract_reasoning_summary_text

client = build_openai_client()

MANAGER_AGENT_ID = "manager"
MANAGER_DISPLAY_NAME = "Manager"

MANAGER_TOOLS = [
    {
        "type": "function",
        "name": "inspect_cafe_state",
        "description": "Inspect yesterday's summary, current cash, supplies, menu recipes, and planning state.",
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
        "name": "restock_supply",
        "description": "Restock a supply through the cafe's normal planning restock path.",
        "parameters": {
            "type": "object",
            "properties": {
                "supply_id": {"type": "string", "description": "The supply id to restock."},
                "quantity": {"type": "integer", "description": "The small quantity to add."},
            },
            "required": ["supply_id", "quantity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "finalize_plan",
        "description": "Finish the manager restock plan with a concise summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A short summary of what was restocked or why no restock was needed.",
                }
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def build_manager_instructions() -> str:
    return """You are the manager for a coffee shop. At the end of each day, it is your job to check the summary for that day and restock supplies if needed.

Use inspect_cafe_state first. Prefer small restocks that bring low or out supplies back above their thresholds. Do not stockpile. If nothing needs restocking, finalize the plan without restocking."""


def build_manager_prompt() -> str:
    return "Check yesterday's summary and restock only what is needed for the next planning day."


def _latest_completed_day_summary(controller) -> Optional[dict]:
    if not controller.campaign.day_summaries:
        return None
    latest = controller.campaign.day_summaries[-1]
    return latest.get("summary") or latest


def _build_menu_recipe_snapshot() -> dict:
    return {
        item_id: {
            "name": MENU.get(item_id, {}).get("name", item_id),
            "recipe": dict(recipe),
        }
        for item_id, recipe in MENU_RECIPES.items()
    }


def inspect_cafe_state(controller) -> dict:
    campaign = controller.campaign
    calendar = campaign.calendar_snapshot(
        elapsed_seconds=controller.get_simulation_state()["elapsed_seconds"],
        sim_duration=controller.sim_duration,
    )
    return {
        "cash": campaign.money,
        "campaign": campaign.campaign_snapshot(),
        "planning_day": {
            "day_id": campaign.current_day.day_id,
            "day_index": campaign.current_day.day_index,
            "date_label": campaign.current_day.date_label,
            "phase": campaign.current_day.phase,
            "calendar": calendar,
            "opening_plan": dict(campaign.current_day.opening_plan),
        },
        "supplies": controller.world.get_supplies(),
        "menu_recipes": _build_menu_recipe_snapshot(),
        "latest_completed_day_summary": _latest_completed_day_summary(controller),
    }


def _extract_message_text(response) -> str:
    pieces = []
    for item in getattr(response, "output", []) or []:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type != "message":
            continue
        content = item.get("content", []) if isinstance(item, dict) else getattr(item, "content", [])
        for part in content or []:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                pieces.append(str(text).strip())
    return "\n\n".join(piece for piece in pieces if piece)


def _function_calls(response) -> list:
    return [
        item
        for item in getattr(response, "output", []) or []
        if (item.get("type") if isinstance(item, dict) else getattr(item, "type", None)) == "function_call"
    ]


def _call_field(call, name, default=None):
    if isinstance(call, dict):
        return call.get(name, default)
    return getattr(call, name, default)


async def execute_manager_tool(controller, tool_name: str, tool_input: dict) -> dict:
    if tool_name == "inspect_cafe_state":
        return {"ok": True, "state": inspect_cafe_state(controller)}

    if tool_name == "restock_supply":
        result = controller.restock_supply(tool_input["supply_id"], int(tool_input["quantity"]))
        return result if isinstance(result, dict) else {"ok": bool(result)}

    if tool_name == "finalize_plan":
        return {"ok": True, "summary": str(tool_input["summary"]).strip()}

    return {"ok": False, "error": f"Unknown tool: {tool_name}"}


def record_current_manager_thinking(controller, reasoning_summaries: list[str]) -> str:
    current_reasoning_summary = "\n\n".join(summary for summary in reasoning_summaries if summary).strip()
    if current_reasoning_summary:
        controller.world.record_agent_thinking(
            MANAGER_AGENT_ID,
            "manager",
            MANAGER_DISPLAY_NAME,
            current_reasoning_summary,
        )
    return current_reasoning_summary


async def run_manager_restock_plan(controller) -> dict:
    input_items = [{"role": "user", "content": build_manager_prompt()}]
    tool_results = []
    reasoning_summaries = []
    manager_summary = ""

    for cycle in range(1, 8):
        response = await client.responses.create(
            model=MANAGER_MODEL,
            instructions=build_manager_instructions(),
            input=input_items,
            tools=MANAGER_TOOLS,
            max_output_tokens=700,
            parallel_tool_calls=False,
            store=STORE_RESPONSES,
            reasoning={"effort": MANAGER_REASONING_EFFORT, "summary": MANAGER_REASONING_SUMMARY},
        )

        reasoning_summary = extract_reasoning_summary_text(response)
        if reasoning_summary:
            reasoning_summaries.append(reasoning_summary)
            record_current_manager_thinking(controller, reasoning_summaries)

        input_items.extend(response.output)
        function_calls = _function_calls(response)
        controller.world.report(
            MANAGER_AGENT_ID,
            "model_response",
            {
                "agent_type": "manager",
                "cycle": cycle,
                "response_id": getattr(response, "id", None),
                "function_call_count": len(function_calls),
                "output_item_count": len(response.output),
            },
        )

        if not function_calls:
            manager_summary = manager_summary or _extract_message_text(response)
            break

        for call in function_calls:
            tool_name = _call_field(call, "name")
            call_id = _call_field(call, "call_id")
            tool_input = json.loads(_call_field(call, "arguments", "{}") or "{}")
            controller.world.report(
                MANAGER_AGENT_ID,
                "tool_call_requested",
                {
                    "agent_type": "manager",
                    "cycle": cycle,
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "arguments": tool_input,
                },
            )
            result = await execute_manager_tool(controller, tool_name, tool_input)
            tool_results.append(
                {
                    "tool_name": tool_name,
                    "arguments": tool_input,
                    "result": result,
                }
            )
            controller.world.report(
                MANAGER_AGENT_ID,
                "tool_call_result",
                {
                    "agent_type": "manager",
                    "cycle": cycle,
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "result": result,
                },
            )
            if tool_name == "finalize_plan" and result.get("ok"):
                manager_summary = result.get("summary", "")
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, sort_keys=True),
                }
            )
        if manager_summary:
            break

    full_reasoning_summary = record_current_manager_thinking(controller, reasoning_summaries)

    return {
        "manager_summary": manager_summary or "Manager finished without a final plan.",
        "reasoning_summary": full_reasoning_summary,
        "tool_results": tool_results,
        "plan": dict(controller.campaign.current_day.opening_plan),
    }
