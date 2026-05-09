import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from run_report import RunReporter
from state_view import build_world_snapshot
from world import WorldState


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class RunReporterTests(unittest.TestCase):
    def test_reporter_writes_ordered_events_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = RunReporter(report_root=tmp_dir)

            first = reporter.event("RUNNER", "run_started", {"mode": "test"})
            second = reporter.event("RUNNER", "run_completed", {"ok": True})
            summary_path = reporter.close("completed", {"orders_created": 0})

            events = read_jsonl(reporter.events_path)
            self.assertEqual(first["seq"], 1)
            self.assertEqual(second["seq"], 2)
            self.assertEqual([event["seq"] for event in events], [1, 2])
            self.assertEqual(events[0]["payload"], {"mode": "test"})

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["event_count"], 2)
            self.assertEqual(summary["summary"], {"orders_created": 0})

    def test_world_events_are_reported_in_mutation_order(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = RunReporter(report_root=tmp_dir)
            world = WorldState(reporter=reporter)

            world.log("RUNNER", "first", "one")
            world.log("RUNNER", "second", "two")

            events = read_jsonl(reporter.events_path)
            self.assertEqual([event["seq"] for event in events], [1, 2])
            self.assertEqual([event["event_type"] for event in events], ["world_event", "world_event"])
            self.assertEqual([event["payload"]["action"] for event in events], ["first", "second"])

    def test_agent_thinking_is_reported_without_world_event_log_mutation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = RunReporter(report_root=tmp_dir)
            world = WorldState(reporter=reporter)

            world.record_agent_thinking("cust_test", "customer", "Test Customer", "Considering the menu.")

            self.assertEqual(world.get_recent_events(), [])
            events = read_jsonl(reporter.events_path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["source"], "cust_test")
            self.assertEqual(events[0]["event_type"], "agent_thinking_summary")
            self.assertEqual(events[0]["payload"]["summary"], "Considering the menu.")

    def test_live_snapshot_includes_latest_agent_thinking_for_active_agents(self):
        world = WorldState()
        world.record_agent_thinking("barista_alex", "barista", "Alex", "Checking whether the queue is empty.")
        world.record_agent_thinking("cust_test", "customer", "Test Customer", "Comparing budget to prices.")

        snapshot = build_world_snapshot(
            world,
            active_customers=[
                {
                    "customer_id": "cust_test",
                    "name": "Test Customer",
                    "mood": "curious",
                    "waiting_seconds": 4,
                }
            ],
            sim_state={"running": True},
        )

        thinking_by_id = {entry["agent_id"]: entry for entry in snapshot["agent_thinking"]}
        self.assertEqual(thinking_by_id["barista_alex"]["summary"], "Checking whether the queue is empty.")
        self.assertEqual(thinking_by_id["cust_test"]["summary"], "Comparing budget to prices.")
        self.assertEqual(thinking_by_id["cust_test"]["display_name"], "Test Customer")

    def test_live_snapshot_includes_customer_visit_phase_and_items(self):
        world = WorldState()
        world._state["customer_visits"]["cust_test"] = {
            "customer_id": "cust_test",
            "name": "Test Customer",
            "mood": "settled",
            "visit_phase": "consuming",
            "held_items": ["latte", "muffin"],
            "consumed_items": ["latte"],
            "received_order_at": 123.0,
            "consumption_started_at": 124.0,
            "left_with_unconsumed_items": False,
        }

        snapshot = build_world_snapshot(
            world,
            active_customers=[
                {
                    "customer_id": "cust_test",
                    "name": "Test Customer",
                    "mood": "settled",
                    "waiting_seconds": 8,
                }
            ],
            sim_state={"running": True},
        )

        customer = snapshot["active_customers"][0]
        self.assertEqual(customer["visit_phase"], "consuming")
        self.assertEqual(customer["held_item_names"], ["Latte", "Blueberry Muffin"])
        self.assertEqual(customer["consumed_item_names"], ["Latte"])

    def test_shift_summary_includes_stockout_metrics(self):
        world = WorldState()
        world._state["order_queue"].append(
            {
                "order_id": "ord_stockout",
                "customer_id": "cust_test",
                "items": ["latte"],
                "total_price": 5.5,
                "status": "failed",
                "barista_id": "barista_alex",
                "placed_at": 1.0,
                "claimed_at": 2.0,
                "preparing_at": None,
                "ready_at": None,
                "delivered_at": None,
                "completed_by": None,
                "closed_at": 3.0,
                "close_reason": "stockout",
                "missing_supplies": {"milk": {"name": "Milk", "required": 1, "available": 0, "short_by": 1}},
            }
        )

        summary = world.get_shift_summary()

        self.assertEqual(summary["stockout_failures"], 1)
        self.assertEqual(summary["stockout_failures_by_supply"], {"milk": 1})
        self.assertIn("final_supplies", summary)
        self.assertIn("sold_out_supplies", summary)

    def test_shift_summary_includes_final_supply_status(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0

        summary = world.get_shift_summary()

        self.assertEqual(summary["final_supplies"]["milk"]["status"], "out")
        self.assertEqual(summary["sold_out_supplies"]["milk"]["name"], "Milk")
        self.assertNotIn("coffee_beans", summary["sold_out_supplies"])

    def test_reporter_accepts_final_snapshot_and_alerts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = RunReporter(report_root=tmp_dir)
            final_snapshot = {"simulation": {"phase": "stopped"}, "queue": []}
            alerts = [{"type": "unresolved_orders", "count": 1}]

            summary_path = reporter.close(
                "completed",
                {"orders_created": 1},
                final_snapshot=final_snapshot,
                alerts=alerts,
            )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["summary"], {"orders_created": 1})
            self.assertEqual(summary["final_snapshot"], final_snapshot)
            self.assertEqual(summary["alerts"], alerts)


if __name__ == "__main__":
    unittest.main()
