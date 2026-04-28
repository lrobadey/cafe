import unittest
from unittest.mock import patch

from control import SimulationController
from runner import next_customer_spawn_delay


class SpawnTimingTests(unittest.TestCase):
    def test_dashboard_spawn_delay_uses_bounded_random_window(self):
        controller = SimulationController()
        controller.set_spawn_interval(30)

        with patch("control.random.uniform", return_value=42) as uniform:
            delay = controller.next_spawn_delay()

        uniform.assert_called_once_with(15.0, 45.0)
        self.assertEqual(delay, 42)

    def test_terminal_spawn_delay_matches_dashboard_window(self):
        with patch("runner.random.uniform", return_value=18) as uniform:
            delay = next_customer_spawn_delay(30)

        uniform.assert_called_once_with(15.0, 45.0)
        self.assertEqual(delay, 18)

    def test_spawn_delay_never_drops_below_one_second(self):
        controller = SimulationController()
        controller.set_spawn_interval(1)

        with patch("control.random.uniform", return_value=0.2):
            delay = controller.next_spawn_delay()

        self.assertEqual(delay, 1)


if __name__ == "__main__":
    unittest.main()
