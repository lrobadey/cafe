import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from campaign import CampaignState
from control import SimulationController


async def idle_until_cancelled(*_args):
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        raise


class DummyReporter:
    report_dir = "/tmp/cafe-test-report"
    events_path = "/tmp/cafe-test-report/events.jsonl"
    summary_path = "/tmp/cafe-test-report/summary.json"

    def event(self, *_args, **_kwargs):
        return {}

    def close(self, *_args, **_kwargs):
        return None


class CampaignLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_controller(self, campaign_root: Path) -> SimulationController:
        original_new_campaign = CampaignState.new_campaign
        with patch(
            "control.CampaignState.new_campaign",
            side_effect=lambda: original_new_campaign(campaign_root=campaign_root),
        ):
            return SimulationController()

    async def test_snapshot_keeps_old_fields_and_adds_campaign_calendar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))

            snapshot = controller.get_snapshot()

            self.assertIn("simulation", snapshot)
            self.assertIn("metrics", snapshot)
            self.assertIn("campaign", snapshot)
            self.assertIn("calendar", snapshot)
            self.assertEqual(snapshot["calendar"]["day_index"], 1)
            self.assertEqual(snapshot["calendar"]["phase"], "planning")
            self.assertEqual(snapshot["campaign"]["days_completed"], 0)

    async def test_world_events_include_day_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))

            controller.world.log("RUNNER", "test_event", "detail")
            event = controller.get_events(after_index=0)["events"][0]

            self.assertEqual(event["campaign_id"], controller.campaign.campaign_id)
            self.assertEqual(event["day_id"], "day_001")
            self.assertEqual(event["day_index"], 1)
            self.assertIn("sim_time", event)

    async def test_close_day_settles_and_advance_starts_clean_next_day(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))

            with (
                patch("control.RunReporter", return_value=DummyReporter()),
                patch("control.run_barista", side_effect=idle_until_cancelled),
                patch("control.SimulationController._run_loop", side_effect=idle_until_cancelled),
            ):
                await controller.start()
                await controller.world.place_order("cust_test", ["espresso"])

                closed = await controller.close_day()

            self.assertTrue(closed)
            self.assertEqual(controller.campaign.current_day.phase, "settled")
            self.assertEqual(controller.campaign.campaign_snapshot()["days_completed"], 1)
            self.assertIsNotNone(controller.campaign.current_day.summary)
            self.assertTrue((Path(tmp_dir) / controller.campaign.campaign_id / "days" / "day_001" / "summary.json").exists())

            advanced = controller.advance_day()

            self.assertTrue(advanced)
            self.assertEqual(controller.campaign.current_day.day_id, "day_002")
            self.assertEqual(controller.campaign.current_day.phase, "planning")
            self.assertEqual(controller.get_snapshot()["queue"], [])
            self.assertEqual(controller.get_snapshot()["calendar"]["day_index"], 2)

    async def test_restock_cost_is_not_charged_again_at_settlement(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            controller = self.make_controller(Path(tmp_dir))

            restock = controller.restock_supply("coffee_beans", 5)

            self.assertTrue(restock["ok"])
            self.assertEqual(controller.campaign.money, 195.0)

            with (
                patch("control.RunReporter", return_value=DummyReporter()),
                patch("control.run_barista", side_effect=idle_until_cancelled),
                patch("control.SimulationController._run_loop", side_effect=idle_until_cancelled),
            ):
                await controller.start()
                await controller.close_day()

            self.assertEqual(controller.campaign.current_day.summary["supply_costs"], 5.0)
            self.assertEqual(controller.campaign.current_day.summary["profit"], -5.0)
            self.assertEqual(controller.campaign.money, 195.0)


if __name__ == "__main__":
    unittest.main()
