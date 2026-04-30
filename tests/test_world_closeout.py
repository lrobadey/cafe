import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from run_report import RunReporter
from world import WorldState


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class WorldCloseoutTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop(asyncio.new_event_loop())

    async def test_closeout_marks_unfinished_orders_and_releases_tables(self):
        world = WorldState()
        pending_id = await world.place_order("cust_pending", ["espresso"])
        claimed_id = await world.place_order("cust_claimed", ["tea"])
        preparing_id = await world.place_order("cust_preparing", ["latte"])
        ready_id = await world.place_order("cust_ready", ["muffin"])
        delivered_id = await world.place_order("cust_done", ["cold_brew"])

        await world.claim_order("barista_alex", claimed_id)
        await world.claim_order("barista_jamie", preparing_id)
        await world.prepare_order("barista_jamie", preparing_id)
        await world.claim_order("barista_alex", ready_id)
        await world.prepare_order("barista_alex", ready_id)
        await world.mark_order_ready(ready_id, barista_id="barista_alex")
        await world.claim_order("barista_alex", delivered_id)
        await world.prepare_order("barista_alex", delivered_id)
        await world.mark_order_ready(delivered_id, barista_id="barista_alex")
        await world.mark_order_delivered(delivered_id)
        await world.claim_table("cust_pending")

        closeout = await world.closeout_unresolved("duration_complete")

        self.assertEqual(world.get_order(pending_id)["status"], "stale")
        self.assertEqual(world.get_order(claimed_id)["status"], "stale")
        self.assertEqual(world.get_order(preparing_id)["status"], "stale")
        self.assertEqual(world.get_order(ready_id)["status"], "abandoned")
        self.assertEqual(world.get_order(delivered_id)["status"], "delivered")
        self.assertEqual(world.get_order(pending_id)["close_reason"], "duration_complete")
        self.assertIsNotNone(world.get_order(ready_id)["closed_at"])
        self.assertEqual(world.get_table_availability()["t1"], "empty")
        self.assertEqual(len(closeout["closed_orders"]), 4)
        self.assertEqual(len(closeout["released_tables"]), 1)

        staff = world.get_staff()
        self.assertEqual(staff["barista_alex"]["status"], "idle")
        self.assertIsNone(staff["barista_alex"]["current_order_id"])
        self.assertEqual(staff["barista_jamie"]["status"], "idle")
        self.assertIsNone(staff["barista_jamie"]["current_order_id"])

    async def test_closeout_emits_reported_world_events_and_alerts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = RunReporter(report_root=tmp_dir)
            world = WorldState(reporter=reporter)
            order_id = await world.place_order("cust_test", ["espresso"])
            await world.claim_table("cust_test")

            closeout = await world.closeout_unresolved("manual_stop")
            alerts = world.get_run_alerts(closeout)
            events = read_jsonl(reporter.events_path)

            self.assertEqual(world.get_order(order_id)["status"], "stale")
            self.assertTrue(any(event["payload"].get("action") == "close_order" for event in events))
            self.assertTrue(any(event["payload"].get("action") == "close_table" for event in events))
            self.assertTrue(any(event["event_type"] == "closeout_completed" for event in events))
            self.assertTrue(any(alert["type"] == "unresolved_orders" for alert in alerts))
            self.assertTrue(any(alert["type"] == "stale_table_cleanup" for alert in alerts))

    async def test_pipeline_and_snapshot_include_closed_statuses_and_phase(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["espresso"])

        await world.closeout_unresolved("duration_complete")
        pipeline = world.get_order_pipeline()
        snapshot = world.get_live_snapshot(
            active_customers=[],
            sim_state={"running": False, "phase": "stopped"},
        )

        self.assertEqual(world.get_order(order_id)["status"], "stale")
        self.assertEqual(pipeline["stale"], 1)
        self.assertIn("abandoned", pipeline)
        self.assertEqual(snapshot["simulation"]["phase"], "stopped")
        self.assertEqual(snapshot["queue"][0]["close_reason"], "duration_complete")


if __name__ == "__main__":
    unittest.main()
