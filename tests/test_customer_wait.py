import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

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
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        await world.mark_order_ready(order_id, barista_id="barista_alex")
        state = {
            "order_id": order_id,
            "table_id": None,
            "done": False,
            "arrived_at": time.time(),
        }

        with patch("agents.customer.asyncio.sleep", new=AsyncMock()):
            await execute_customer_tool("wait", {"seconds": 5}, "cust_test", world, state)

        self.assertEqual(world.get_order(order_id)["status"], "ready")

    async def test_check_order_delivers_and_populates_held_items(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["espresso", "muffin"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        await world.mark_order_ready(order_id, barista_id="barista_alex")
        state = {
            "order_id": order_id,
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": [],
            "consumed_items": [],
        }

        result = await execute_customer_tool("check_order", {"order_id": order_id}, "cust_test", world, state)

        self.assertEqual(world.get_order(order_id)["status"], "delivered")
        self.assertEqual(state["visit_phase"], "received_order")
        self.assertEqual(state["held_items"], ["espresso", "muffin"])
        self.assertIn("sip_drink", result)
        self.assertEqual(world.get_customer_visit("cust_test")["held_items"], ["espresso", "muffin"])

    async def test_check_order_reports_stockout_failure(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        state = {
            "order_id": order_id,
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": [],
            "consumed_items": [],
        }

        result = await execute_customer_tool("check_order", {"order_id": order_id}, "cust_test", world, state)

        self.assertIn("cannot be completed", result)
        self.assertIn("Milk", result)
        self.assertEqual(state["visit_phase"], "order_failed")
        self.assertEqual(world.get_customer_visit("cust_test")["visit_phase"], "order_failed")

    async def test_customer_can_reorder_after_stockout_failure(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0
        failed_order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", failed_order_id)
        await world.prepare_order("barista_alex", failed_order_id)
        state = {
            "order_id": failed_order_id,
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": [],
            "consumed_items": [],
        }
        await execute_customer_tool("check_order", {"order_id": failed_order_id}, "cust_test", world, state)

        result = await execute_customer_tool("place_order", {"items": ["tea"]}, "cust_test", world, state)

        self.assertIn("Order placed", result)
        self.assertIn("Tea", result)
        self.assertEqual(state["previous_order_id"], failed_order_id)
        self.assertNotEqual(state["order_id"], failed_order_id)
        self.assertEqual(world.get_order(state["order_id"])["items"], ["tea"])

    async def test_sip_drink_succeeds_for_held_drink(self):
        world = WorldState()
        await world.update_customer_visit("cust_test", held_items=["latte"], consumed_items=[])
        state = {
            "order_id": "ord_test",
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["latte"],
            "consumed_items": [],
        }

        result = await execute_customer_tool("sip_drink", {"item_id": "latte"}, "cust_test", world, state)

        self.assertIn("You sip your Latte", result)
        self.assertEqual(state["visit_phase"], "consuming")
        self.assertEqual(state["consumed_items"], ["latte"])
        self.assertEqual(world.get_customer_visit("cust_test")["consumed_items"], ["latte"])

    async def test_sip_drink_rejects_food_and_items_not_held(self):
        world = WorldState()
        await world.update_customer_visit("cust_test", held_items=["muffin"], consumed_items=[])
        state = {
            "order_id": "ord_test",
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["muffin"],
            "consumed_items": [],
        }

        food_result = await execute_customer_tool("sip_drink", {"item_id": "muffin"}, "cust_test", world, state)
        missing_result = await execute_customer_tool("sip_drink", {"item_id": "latte"}, "cust_test", world, state)

        self.assertIn("not a drink", food_result)
        self.assertIn("do not have Latte", missing_result)
        self.assertEqual(state["consumed_items"], [])

    async def test_eat_item_succeeds_for_held_food_and_rejects_drinks(self):
        world = WorldState()
        await world.update_customer_visit("cust_test", held_items=["muffin", "tea"], consumed_items=[])
        state = {
            "order_id": "ord_test",
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["muffin", "tea"],
            "consumed_items": [],
        }

        food_result = await execute_customer_tool("eat_item", {"item_id": "muffin"}, "cust_test", world, state)
        drink_result = await execute_customer_tool("eat_item", {"item_id": "tea"}, "cust_test", world, state)

        self.assertIn("You eat your Blueberry Muffin", food_result)
        self.assertIn("not a food", drink_result)
        self.assertEqual(state["consumed_items"], ["muffin"])

    async def test_consuming_same_item_twice_is_rejected(self):
        world = WorldState()
        await world.update_customer_visit("cust_test", held_items=["espresso"], consumed_items=["espresso"])
        state = {
            "order_id": "ord_test",
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["espresso"],
            "consumed_items": ["espresso"],
        }

        result = await execute_customer_tool("sip_drink", {"item_id": "espresso"}, "cust_test", world, state)

        self.assertIn("already consumed Espresso", result)
        self.assertEqual(state["consumed_items"], ["espresso"])

    async def test_linger_clamps_duration_and_does_not_change_order_status(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["tea"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        await world.mark_order_ready(order_id, barista_id="barista_alex")
        await world.mark_order_delivered(order_id)
        state = {
            "order_id": order_id,
            "table_id": "t1",
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["tea"],
            "consumed_items": [],
            "consumption_started_at": None,
        }

        with patch("agents.customer.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await execute_customer_tool("linger", {"seconds": 100}, "cust_test", world, state)

        sleep.assert_awaited_once_with(15)
        self.assertIn("linger at table t1 for 15s", result)
        self.assertEqual(world.get_order(order_id)["status"], "delivered")

    async def test_leave_releases_table_after_consuming_and_records_unconsumed_items(self):
        world = WorldState()
        table_id = await world.claim_table("cust_test")
        await world.update_customer_visit("cust_test", held_items=["latte", "muffin"], consumed_items=["latte"])
        state = {
            "order_id": "ord_test",
            "table_id": table_id,
            "done": False,
            "arrived_at": time.time(),
            "held_items": ["latte", "muffin"],
            "consumed_items": ["latte"],
        }

        result = await execute_customer_tool("leave", {"reason": "satisfied"}, "cust_test", world, state)

        self.assertIn("You leave the cafe", result)
        self.assertTrue(state["done"])
        self.assertEqual(world.get_table_availability()[table_id], "empty")
        visit = world.get_customer_visit("cust_test")
        self.assertEqual(visit["visit_phase"], "done")
        self.assertTrue(visit["left_with_unconsumed_items"])


if __name__ == "__main__":
    unittest.main()
