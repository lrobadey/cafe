import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from campaign import CampaignState
from control import SimulationController
from state_view import build_live_snapshot


def fake_response(response_id: str, calls: list[dict], reasoning_text: str):
    output = [
        SimpleNamespace(
            type="reasoning",
            summary=[SimpleNamespace(type="summary_text", text=reasoning_text)],
        )
    ]
    for call in calls:
        output.append(
            SimpleNamespace(
                type="function_call",
                name=call["name"],
                call_id=call["call_id"],
                arguments=json.dumps(call.get("arguments", {})),
            )
        )
    return SimpleNamespace(id=response_id, output=output)


async def fake_manager_model_call(*_args, **_kwargs):
    return fake_response(
        "resp_manager",
        [
            {"name": "inspect_cafe_state", "call_id": "call_inspect"},
            {
                "name": "restock_supply",
                "call_id": "call_restock",
                "arguments": {"supply_id": "cups", "quantity": 3},
            },
            {
                "name": "finalize_plan",
                "call_id": "call_finalize",
                "arguments": {"summary": "Restocked cups by 3 for tomorrow."},
            },
        ],
        "Full manager reasoning summary.\n\nIt checked the day summary and chose a small cups restock.",
    )


class ManagerAgentTests(unittest.IsolatedAsyncioTestCase):
    def make_controller(self, campaign_root: Path) -> SimulationController:
        original_new_campaign = CampaignState.new_campaign
        with patch(
            "control.CampaignState.new_campaign",
            side_effect=lambda: original_new_campaign(campaign_root=campaign_root),
        ):
            return SimulationController()

    def add_completed_day_summary(self, controller: SimulationController):
        controller.campaign.day_summaries.append(
            {
                "day_id": "day_001",
                "day_index": 1,
                "date_label": "Monday, Spring 1",
                "summary": {
                    "day_id": "day_001",
                    "revenue": 24.0,
                    "customers_served": 4,
                    "tomorrow_warnings": ["Cups ended the day low."],
                    "final_supplies": {
                        "cups": {"name": "Cups", "quantity": 2, "low_threshold": 4, "status": "low"}
                    },
                },
            }
        )

    async def test_manager_rejects_while_running(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))
            self.add_completed_day_summary(controller)
            controller.phase = "running"

            result = await controller.run_manager_restock_plan()

            self.assertFalse(result["ok"])
            self.assertIn("planning", result["error"])

    async def test_manager_rejects_without_completed_day_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))

            result = await controller.run_manager_restock_plan()

            self.assertFalse(result["ok"])
            self.assertIn("completed day summary", result["error"])

    async def test_start_does_not_open_day_while_manager_is_running(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))
            self.add_completed_day_summary(controller)
            controller._manager_running = True

            await controller.start()

            self.assertFalse(controller.running)
            self.assertEqual(controller.phase, "idle")
            self.assertEqual(controller.campaign.current_day.phase, "planning")

    async def test_manager_restock_uses_existing_campaign_path_and_preserves_full_reasoning_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))
            self.add_completed_day_summary(controller)

            with patch("agents.manager.client.responses.create", side_effect=fake_manager_model_call):
                result = await controller.run_manager_restock_plan()

            self.assertTrue(result["ok"])
            self.assertEqual(result["manager_summary"], "Restocked cups by 3 for tomorrow.")
            self.assertIn("Full manager reasoning summary.", result["reasoning_summary"])
            self.assertIn("small cups restock", result["reasoning_summary"])
            self.assertEqual(controller.campaign.money, 197.0)
            self.assertEqual(controller.campaign.current_day.opening_plan["restocks"]["cups"], 3)
            self.assertEqual(controller.campaign.current_day.opening_plan["restock_costs"], 3.0)
            self.assertEqual(controller.campaign.persistent_supplies["cups"]["quantity"], 17)
            self.assertEqual(controller.world.get_supplies()["cups"]["quantity"], 17)

            snapshot = build_live_snapshot(controller)
            thinking_by_id = {entry["agent_id"]: entry for entry in snapshot["agent_thinking"]}
            self.assertEqual(thinking_by_id["manager"]["agent_type"], "manager")
            self.assertIn("Full manager reasoning summary.", thinking_by_id["manager"]["summary"])

    async def test_manager_records_reasoning_before_tool_execution_finishes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))
            self.add_completed_day_summary(controller)
            seen_reasoning_during_tool = []

            async def fake_execute_tool(_controller, tool_name, tool_input):
                thinking = controller.world.get_agent_thinking_entries().get("manager", {})
                seen_reasoning_during_tool.append(thinking.get("summary", ""))
                if tool_name == "finalize_plan":
                    return {"ok": True, "summary": str(tool_input["summary"]).strip()}
                return {"ok": True}

            response = fake_response(
                "resp_manager",
                [
                    {
                        "name": "finalize_plan",
                        "call_id": "call_finalize",
                        "arguments": {"summary": "No restock needed."},
                    }
                ],
                "Current manager reasoning while the plan is still running.",
            )

            with (
                patch("agents.manager.client.responses.create", return_value=response),
                patch("agents.manager.execute_manager_tool", side_effect=fake_execute_tool),
            ):
                result = await controller.run_manager_restock_plan()

            self.assertTrue(result["ok"])
            self.assertEqual(result["manager_summary"], "No restock needed.")
            self.assertEqual(
                seen_reasoning_during_tool,
                ["Current manager reasoning while the plan is still running."],
            )

    async def test_manager_restock_api_returns_plan_and_tool_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))
            self.add_completed_day_summary(controller)

            import api

            with (
                patch.object(api, "controller", controller),
                patch("agents.manager.client.responses.create", side_effect=fake_manager_model_call),
            ):
                result = await api.manager_restock()

            self.assertTrue(result["ok"])
            self.assertEqual(result["manager_summary"], "Restocked cups by 3 for tomorrow.")
            self.assertIn("reasoning_summary", result)
            self.assertEqual([entry["tool_name"] for entry in result["tool_results"]], [
                "inspect_cafe_state",
                "restock_supply",
                "finalize_plan",
            ])
            self.assertEqual(result["plan"]["restocks"]["cups"], 3)


if __name__ == "__main__":
    unittest.main()
