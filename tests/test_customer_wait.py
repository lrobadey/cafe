import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from agents.customer import execute_customer_tool
from world import WorldState


class CustomerWaitToolTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop(asyncio.new_event_loop())

    async def test_wait_clamps_duration_and_reports_total_time(self):
        world = WorldState()
        state = {
            "order_id": "ord_test",
            "table_id": None,
            "done": False,
            "arrived_at": time.time() - 20,
        }

        with patch("agents.customer.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await execute_customer_tool("wait", {"seconds": 100}, "cust_test", world, state)

        sleep.assert_awaited_once_with(15)
        self.assertIn("You wait for 15s.", result)
        self.assertIn("total", result)
        self.assertEqual(world.queue_length(), 0)

    async def test_wait_before_order_is_safe(self):
        world = WorldState()
        state = {
            "order_id": None,
            "table_id": None,
            "done": False,
            "arrived_at": time.time(),
        }

        with patch("agents.customer.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await execute_customer_tool("wait", {"seconds": 1}, "cust_test", world, state)

        sleep.assert_awaited_once_with(3)
        self.assertIn("you have not ordered yet", result)
        self.assertEqual(world.queue_length(), 0)

    async def test_wait_does_not_deliver_ready_order(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["espresso"])
        await world.mark_order_ready(order_id)
        state = {
            "order_id": order_id,
            "table_id": None,
            "done": False,
            "arrived_at": time.time(),
        }

        with patch("agents.customer.asyncio.sleep", new=AsyncMock()):
            await execute_customer_tool("wait", {"seconds": 5}, "cust_test", world, state)

        self.assertEqual(world.get_order(order_id)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
