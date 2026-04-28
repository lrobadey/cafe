import json
import tempfile
import unittest

from run_report import RunReporter
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


if __name__ == "__main__":
    unittest.main()
