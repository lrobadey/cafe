import asyncio
import random
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from customers.decisions import choose_order, friction_breakdown, friction_exceeds_patience, should_try_reorder
from customers.deterministic import run_deterministic_customer
from customers.profile import CustomerProfile, CustomerRuntimeState, generate_customer_profile
from world import WorldState


def make_profile(**overrides):
    values = {
        "customer_id": "cust_test",
        "archetype_id": "hurried_commuter",
        "display_name": "Hurried Commuter",
        "preferred_items": ["espresso", "cold_brew", "latte", "tea"],
        "disliked_items": ["muffin"],
        "preferred_categories": ["drink", "food"],
        "budget": 8.0,
        "max_items_per_order": 1,
        "max_orders_per_visit": 1,
        "patience": 50,
        "seat_need": "low",
        "queue_sensitivity": "high",
        "no_seat_sensitivity": "none",
        "dwell_seconds": 0,
        "reorder_chance": 0.0,
        "reorder_check_after_seconds": None,
        "requires_seat_to_dwell": False,
        "leave_after_pickup": True,
    }
    values.update(overrides)
    return CustomerProfile(**values)


async def prepare_next_order(world, customer_id, seen):
    for _ in range(100):
        for order in world.get_orders():
            if order["customer_id"] == customer_id and order["order_id"] not in seen:
                seen.add(order["order_id"])
                await world.claim_order("barista_alex", order["order_id"])
                await world.prepare_order("barista_alex", order["order_id"])
                await world.mark_order_ready(order["order_id"], barista_id="barista_alex")
                return order["order_id"]
        await asyncio.sleep(0.02)
    raise AssertionError("No order appeared for customer.")


async def fail_next_order_from_stockout(world, customer_id, seen, supply_id):
    for _ in range(100):
        for order in world.get_orders():
            if order["customer_id"] == customer_id and order["order_id"] not in seen:
                seen.add(order["order_id"])
                world._state["supplies"][supply_id]["quantity"] = 0
                await world.claim_order("barista_alex", order["order_id"])
                result = await world.prepare_order("barista_alex", order["order_id"])
                if result["ok"]:
                    raise AssertionError("Expected stockout preparation failure.")
                return order["order_id"]
        await asyncio.sleep(0.02)
    raise AssertionError("No order appeared for customer.")


class CustomerProfileGenerationTests(unittest.TestCase):
    def test_seeded_profiles_repeat(self):
        first_rng = random.Random(123)
        second_rng = random.Random(123)

        first = [generate_customer_profile(first_rng, f"cust_{index}") for index in range(5)]
        second = [generate_customer_profile(second_rng, f"cust_{index}") for index in range(5)]

        self.assertEqual(first, second)

    def test_generated_values_stay_inside_archetype_ranges(self):
        rng = random.Random(4)

        profiles = [generate_customer_profile(rng, f"cust_{index}") for index in range(20)]

        for profile in profiles:
            self.assertGreaterEqual(profile.budget, 4.0)
            self.assertLessEqual(profile.budget, 20.0)
            self.assertGreaterEqual(profile.patience, 10)
            self.assertLessEqual(profile.patience, 95)
            self.assertIn(profile.seat_need, {"low", "medium", "high"})
            self.assertGreaterEqual(profile.max_items_per_order, 1)
            self.assertLessEqual(profile.max_items_per_order, 2)


class CustomerDecisionTests(unittest.TestCase):
    def test_friction_reflects_queue_wait_and_no_seat_pressure(self):
        profile = make_profile(
            patience=30,
            seat_need="high",
            queue_sensitivity="high",
            no_seat_sensitivity="high",
        )

        breakdown = friction_breakdown(
            profile,
            queue_length=3,
            elapsed_wait_seconds=20,
            empty_tables=0,
        )

        self.assertGreater(breakdown["queue"], 0)
        self.assertGreater(breakdown["wait"], 0)
        self.assertGreater(breakdown["no_seat"], 0)
        self.assertTrue(friction_exceeds_patience(profile, breakdown))

    def test_order_choice_prefers_profile_preferences_and_affordability(self):
        profile = make_profile(budget=5.0, preferred_items=["cold_brew", "espresso"], disliked_items=["muffin"])
        menu = WorldState().get_menu_availability()

        order = choose_order(profile, menu, profile.budget, random.Random(1))

        self.assertEqual(order, ["cold_brew"])

    def test_reorder_requires_budget_timing_and_probability(self):
        profile = make_profile(
            max_orders_per_visit=2,
            reorder_chance=1.0,
            reorder_check_after_seconds=0,
            budget=12.0,
        )
        runtime = CustomerRuntimeState(
            customer_id="cust_test",
            arrived_at=time.time() - 10,
            orders_placed=1,
            budget_spent=3.0,
            next_reorder_check_at=time.time() - 1,
        )
        friction = {"total": 0}

        allowed = should_try_reorder(
            profile,
            runtime,
            now=time.time(),
            friction=friction,
            menu=WorldState().get_menu_availability(),
            rng=random.Random(1),
        )

        self.assertTrue(allowed)


class DeterministicCustomerVisitTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop(asyncio.new_event_loop())

    async def test_hurried_commuter_places_one_order_and_leaves(self):
        world = WorldState()
        profile = make_profile(customer_id="cust_fast")
        seen = set()

        with patch("customers.deterministic.POLL_SECONDS", 0.02):
            task = asyncio.create_task(run_deterministic_customer(profile, world, random.Random(3)))
            await prepare_next_order(world, "cust_fast", seen)
            await asyncio.wait_for(task, timeout=2)

        visit = world.get_customer_visit("cust_fast")
        self.assertEqual(visit["archetype_id"], "hurried_commuter")
        self.assertEqual(visit["orders_placed"], 1)
        self.assertEqual(visit["visit_phase"], "done")
        self.assertEqual(world.get_orders()[0]["status"], "delivered")

    async def test_remote_worker_can_claim_table_and_reorder_without_parallel_orders(self):
        world = WorldState()
        profile = make_profile(
            customer_id="cust_remote",
            archetype_id="remote_worker",
            display_name="Remote Worker",
            preferred_items=["cold_brew", "latte", "tea", "muffin"],
            disliked_items=["espresso"],
            preferred_categories=["drink", "food"],
            budget=20.0,
            max_items_per_order=1,
            max_orders_per_visit=2,
            patience=95,
            seat_need="high",
            queue_sensitivity="medium",
            no_seat_sensitivity="high",
            dwell_seconds=1,
            reorder_chance=1.0,
            reorder_check_after_seconds=0,
            requires_seat_to_dwell=True,
            leave_after_pickup=False,
        )
        seen = set()

        with patch("customers.deterministic.POLL_SECONDS", 0.02):
            task = asyncio.create_task(run_deterministic_customer(profile, world, random.Random(5)))
            await prepare_next_order(world, "cust_remote", seen)
            await prepare_next_order(world, "cust_remote", seen)
            await asyncio.wait_for(task, timeout=3)

        visit = world.get_customer_visit("cust_remote")
        self.assertEqual(visit["orders_placed"], 2)
        self.assertEqual(len(visit["order_ids"]), 2)
        self.assertIsNone(visit["active_order_id"])
        self.assertEqual(world.get_table_availability()["t1"], "empty")
        self.assertEqual(world.get_shift_summary()["reorders_by_archetype"]["remote_worker"], 1)

    async def test_customer_can_place_replacement_order_after_stockout_failure(self):
        world = WorldState()
        world.set_menu_item_availability("espresso", False)
        world.set_menu_item_availability("cold_brew", False)
        world.set_menu_item_availability("tea", False)
        world.set_menu_item_availability("muffin", False)
        profile = make_profile(
            customer_id="cust_stockout_retry",
            preferred_items=["latte", "espresso"],
            disliked_items=[],
            budget=6.0,
            max_orders_per_visit=2,
            patience=95,
        )
        seen = set()

        with patch("customers.deterministic.POLL_SECONDS", 0.02):
            task = asyncio.create_task(run_deterministic_customer(profile, world, random.Random(3)))
            failed_order_id = await fail_next_order_from_stockout(world, "cust_stockout_retry", seen, "milk")
            world.set_menu_item_availability("espresso", True)
            replacement_order_id = await prepare_next_order(world, "cust_stockout_retry", seen)
            await asyncio.wait_for(task, timeout=3)

        visit = world.get_customer_visit("cust_stockout_retry")
        orders = {order["order_id"]: order for order in world.get_orders()}
        self.assertEqual(orders[failed_order_id]["status"], "failed")
        self.assertEqual(orders[failed_order_id]["close_reason"], "stockout")
        self.assertEqual(orders[replacement_order_id]["status"], "delivered")
        self.assertEqual(orders[replacement_order_id]["items"], ["espresso"])
        self.assertEqual(visit["orders_placed"], 2)
        self.assertEqual(visit["order_ids"], [failed_order_id, replacement_order_id])
        self.assertEqual(visit["consumed_items"], ["espresso"])
        self.assertEqual(visit["budget_spent"], 3.0)
        self.assertEqual(visit["visit_phase"], "done")

    async def test_customer_leaves_after_stockout_when_patience_is_exceeded(self):
        world = WorldState()
        world.set_menu_item_availability("espresso", False)
        world.set_menu_item_availability("cold_brew", False)
        world.set_menu_item_availability("tea", False)
        world.set_menu_item_availability("muffin", False)
        profile = make_profile(
            customer_id="cust_stockout_leave",
            preferred_items=["latte", "espresso"],
            disliked_items=[],
            budget=12.0,
            max_orders_per_visit=2,
            patience=11,
            queue_sensitivity="low",
        )
        seen = set()

        with patch("customers.deterministic.POLL_SECONDS", 0.02):
            task = asyncio.create_task(run_deterministic_customer(profile, world, random.Random(3)))
            failed_order_id = await fail_next_order_from_stockout(world, "cust_stockout_leave", seen, "milk")
            world.set_menu_item_availability("espresso", True)
            await asyncio.wait_for(task, timeout=3)

        visit = world.get_customer_visit("cust_stockout_leave")
        self.assertEqual(world.get_order(failed_order_id)["status"], "failed")
        self.assertEqual(visit["orders_placed"], 1)
        self.assertEqual(visit["order_ids"], [failed_order_id])
        self.assertEqual(visit["leave_reason"], "nothing_appealing")
        self.assertEqual(visit["visit_phase"], "done")


if __name__ == "__main__":
    unittest.main()
