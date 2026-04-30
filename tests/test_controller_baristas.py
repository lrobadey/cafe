import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from control import SimulationController


async def idle_until_cancelled(*_args):
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        raise


class DummyReporter:
    report_dir = "/tmp/cafe-test-report"

    def event(self, *_args, **_kwargs):
        return {}

    def close(self, *_args, **_kwargs):
        return None


class ControllerBaristaTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await asyncio.sleep(0)

    async def test_start_creates_two_barista_tasks(self):
        controller = SimulationController()

        with (
            patch("control.RunReporter", return_value=DummyReporter()),
            patch("control.run_barista", side_effect=idle_until_cancelled),
            patch("control.SimulationController._run_loop", side_effect=idle_until_cancelled),
        ):
            await controller.start()

            self.assertEqual(set(controller._barista_tasks), {"barista_alex", "barista_jamie"})
            self.assertEqual(len(controller._barista_tasks), 2)

            await controller.stop()

    async def test_stop_cancels_and_clears_barista_tasks(self):
        controller = SimulationController()

        with (
            patch("control.RunReporter", return_value=DummyReporter()),
            patch("control.run_barista", side_effect=idle_until_cancelled),
            patch("control.SimulationController._run_loop", side_effect=idle_until_cancelled),
        ):
            await controller.start()
            tasks = list(controller._barista_tasks.values())

            await controller.stop()
            await asyncio.sleep(0)

            self.assertEqual(controller._barista_tasks, {})
            self.assertTrue(all(task.cancelled() for task in tasks))


if __name__ == "__main__":
    unittest.main()
