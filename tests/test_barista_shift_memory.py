import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cafe_sim"))

from agents.barista import (
    build_barista_cycle_prompt,
    build_barista_instructions,
    create_shift_memory,
    execute_barista_tool,
    render_shift_memory,
    update_shift_memory,
)
from world import WorldState


class BaristaShiftMemoryTests(unittest.TestCase):
    def test_initial_memory_renders_minimal_empty_state(self):
        memory = create_shift_memory()

        self.assertEqual(render_shift_memory(memory), "Shift memory: no completed orders yet.")

    def test_empty_queue_check_increments_consecutive_empty_checks(self):
        memory = create_shift_memory()

        update_shift_memory(memory, "check_queue", "Queue is empty. Nothing to do right now.")
        update_shift_memory(memory, "check_queue", "Queue is empty. Nothing to do right now.")

        self.assertEqual(memory["empty_queue_checks"], 2)
        self.assertEqual(memory["recent_queue_pressure"], "empty")
        self.assertEqual(memory["last_action"], "checked queue; it was empty")

    def test_non_empty_queue_resets_empty_checks_and_marks_pressure(self):
        memory = create_shift_memory()
        update_shift_memory(memory, "check_queue", "Queue is empty. Nothing to do right now.")

        update_shift_memory(
            memory,
            "check_queue",
            "1 order(s) waiting:\n- Order ord_123abc: latte for customer cust_test",
        )

        self.assertEqual(memory["empty_queue_checks"], 0)
        self.assertEqual(memory["recent_queue_pressure"], "normal")
        self.assertEqual(memory["last_action"], "checked queue; orders were waiting")

    def test_multiple_waiting_orders_mark_queue_busy(self):
        memory = create_shift_memory()

        update_shift_memory(
            memory,
            "check_queue",
            "3 order(s) waiting:\n- Order ord_123abc: latte for customer cust_test",
        )

        self.assertEqual(memory["recent_queue_pressure"], "busy")

    def test_mark_ready_increments_completed_count_and_records_order(self):
        memory = create_shift_memory()

        update_shift_memory(
            memory,
            "mark_ready",
            "Order ord_123abc is ready for pickup. Check the queue for more orders.",
        )

        self.assertEqual(memory["orders_completed"], 1)
        self.assertEqual(memory["last_completed_order"], "ord_123abc")
        self.assertEqual(memory["last_action"], "marked ord_123abc ready")

    def test_cycle_prompt_includes_compact_private_memory_block(self):
        memory = create_shift_memory()
        update_shift_memory(
            memory,
            "mark_ready",
            "Order ord_123abc is ready for pickup. Check the queue for more orders.",
        )

        prompt = build_barista_cycle_prompt(memory)

        self.assertIn("Shift memory:", prompt)
        self.assertIn("- Orders completed this shift: 1", prompt)
        self.assertIn("- Last completed order: ord_123abc", prompt)
        self.assertIn("Check the queue and handle the next order.", prompt)

    def test_instructions_use_display_name(self):
        instructions = build_barista_instructions("Jamie")

        self.assertIn("You are Jamie, the barista", instructions)

    def test_stockout_prepare_result_clears_current_order_memory(self):
        memory = create_shift_memory()
        memory["current_order_id"] = "ord_test"

        update_shift_memory(
            memory,
            "prepare_order",
            "Cannot prepare order ord_test: missing supplies (Milk need 1 have 0).",
        )

        self.assertIsNone(memory["current_order_id"])
        self.assertIn("missing supplies", memory["last_action"])


class BaristaToolIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_order_uses_passed_barista_id(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])

        result = await execute_barista_tool(
            "barista_jamie",
            "claim_order",
            {"order_id": order_id},
            world,
        )

        self.assertIn(f"Claimed order {order_id}", result)
        self.assertEqual(world.get_order(order_id)["barista_id"], "barista_jamie")

    async def test_failed_claim_names_current_owner(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await execute_barista_tool("barista_alex", "claim_order", {"order_id": order_id}, world)

        result = await execute_barista_tool("barista_jamie", "claim_order", {"order_id": order_id}, world)

        self.assertIn("already claimed by Alex", result)
        self.assertIn("another pending order", result)


class StaffStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_initializes_barista_staff_roster(self):
        world = WorldState()

        staff = world.get_staff()

        self.assertEqual(set(staff), {"barista_alex", "barista_jamie"})
        self.assertEqual(staff["barista_alex"]["display_name"], "Alex")
        self.assertEqual(staff["barista_jamie"]["display_name"], "Jamie")
        for member in staff.values():
            self.assertEqual(member["role"], "barista")
            self.assertEqual(member["status"], "idle")
            self.assertIsNone(member["current_order_id"])
            self.assertEqual(member["orders_completed"], 0)
            self.assertIsNone(member["last_action"])

    async def test_claim_order_updates_staff_state(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])

        claimed = await world.claim_order("barista_alex", order_id)

        alex = world.get_staff()["barista_alex"]
        self.assertTrue(claimed)
        self.assertEqual(alex["status"], "claimed")
        self.assertEqual(alex["current_order_id"], order_id)
        self.assertEqual(alex["last_action"], f"claimed {order_id}")

    async def test_claim_order_updates_jamie_staff_state(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])

        claimed = await world.claim_order("barista_jamie", order_id)

        jamie = world.get_staff()["barista_jamie"]
        self.assertTrue(claimed)
        self.assertEqual(jamie["status"], "claimed")
        self.assertEqual(jamie["current_order_id"], order_id)
        self.assertEqual(jamie["last_action"], f"claimed {order_id}")

    async def test_mark_ready_completes_staff_order(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)

        result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        alex = world.get_staff()["barista_alex"]
        self.assertTrue(result["ok"])
        self.assertEqual(alex["status"], "idle")
        self.assertIsNone(alex["current_order_id"])
        self.assertEqual(alex["orders_completed"], 1)
        self.assertEqual(alex["last_action"], f"marked {order_id} ready")

    async def test_owner_can_prepare_claimed_order(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)

        result = await world.prepare_order("barista_alex", order_id)

        alex = world.get_staff()["barista_alex"]
        self.assertTrue(result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "preparing")
        self.assertEqual(alex["status"], "preparing")
        self.assertEqual(alex["current_order_id"], order_id)
        self.assertEqual(alex["last_action"], f"preparing {order_id}")

    async def test_prepare_order_decrements_recipe_supplies(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        before = world.get_supplies()

        result = await world.prepare_order("barista_alex", order_id)
        after = world.get_supplies()

        self.assertTrue(result["ok"])
        self.assertEqual(after["coffee_beans"]["quantity"], before["coffee_beans"]["quantity"] - 1)
        self.assertEqual(after["milk"]["quantity"], before["milk"]["quantity"] - 1)
        self.assertEqual(after["cups"]["quantity"], before["cups"]["quantity"] - 1)

    async def test_menu_hides_items_that_cannot_be_made_from_stock(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0

        menu = world.get_menu()

        self.assertNotIn("latte", menu)
        self.assertIn("espresso", menu)

    async def test_menu_hides_manually_disabled_items_even_when_stocked(self):
        world = WorldState()
        world.set_menu_item_availability("espresso", False)

        menu = world.get_menu()

        self.assertNotIn("espresso", menu)

    async def test_shared_supply_consumption_removes_new_orders_from_menu(self):
        world = WorldState()
        world._state["supplies"]["cups"]["quantity"] = 1
        order_id = await world.place_order("cust_test", ["espresso"])
        await world.claim_order("barista_alex", order_id)

        result = await world.prepare_order("barista_alex", order_id)
        menu = world.get_menu()

        self.assertTrue(result["ok"])
        self.assertNotIn("latte", menu)
        self.assertNotIn("tea", menu)
        self.assertNotIn("cold_brew", menu)
        self.assertIn("muffin", menu)

    async def test_place_order_rejects_stocked_out_items(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0

        with self.assertRaisesRegex(ValueError, "not on the menu"):
            await world.place_order("cust_test", ["latte"])

        self.assertEqual(world.queue_length(), 0)

    async def test_place_order_rejects_aggregate_supply_shortage(self):
        world = WorldState()
        world._state["supplies"]["cups"]["quantity"] = 1

        with self.assertRaisesRegex(ValueError, "Missing supplies"):
            await world.place_order("cust_test", ["espresso", "tea"])

        self.assertEqual(world.queue_length(), 0)

    async def test_prepare_order_aggregates_multi_item_recipe_supplies(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte", "muffin"])
        await world.claim_order("barista_alex", order_id)
        before = world.get_supplies()

        result = await world.prepare_order("barista_alex", order_id)
        after = world.get_supplies()

        self.assertTrue(result["ok"])
        self.assertEqual(after["coffee_beans"]["quantity"], before["coffee_beans"]["quantity"] - 1)
        self.assertEqual(after["milk"]["quantity"], before["milk"]["quantity"] - 1)
        self.assertEqual(after["cups"]["quantity"], before["cups"]["quantity"] - 1)
        self.assertEqual(after["muffins"]["quantity"], before["muffins"]["quantity"] - 1)

    async def test_stockout_fails_order_without_partial_supply_decrement(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        world._state["supplies"]["milk"]["quantity"] = 0
        await world.claim_order("barista_alex", order_id)
        before = world.get_supplies()

        result = await world.prepare_order("barista_alex", order_id)
        after = world.get_supplies()
        order = world.get_order(order_id)
        alex = world.get_staff()["barista_alex"]

        self.assertFalse(result["ok"])
        self.assertIn("missing supplies", result["message"])
        self.assertEqual(order["status"], "failed")
        self.assertEqual(order["close_reason"], "stockout")
        self.assertIn("milk", order["missing_supplies"])
        self.assertIsNotNone(order["closed_at"])
        self.assertEqual(before["coffee_beans"]["quantity"], after["coffee_beans"]["quantity"])
        self.assertEqual(before["cups"]["quantity"], after["cups"]["quantity"])
        self.assertEqual(alex["status"], "idle")
        self.assertIsNone(alex["current_order_id"])

    async def test_non_owner_cannot_prepare_or_mark_ready(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)

        prepare_result = await world.prepare_order("barista_jamie", order_id)
        ready_result = await world.mark_order_ready(order_id, barista_id="barista_jamie")

        alex = world.get_staff()["barista_alex"]
        jamie = world.get_staff()["barista_jamie"]
        self.assertFalse(prepare_result["ok"])
        self.assertFalse(ready_result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "claimed")
        self.assertEqual(alex["current_order_id"], order_id)
        self.assertEqual(alex["orders_completed"], 0)
        self.assertIsNone(jamie["current_order_id"])
        self.assertEqual(jamie["orders_completed"], 0)

    async def test_pending_order_cannot_be_prepared_or_marked_ready(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])

        prepare_result = await world.prepare_order("barista_alex", order_id)
        ready_result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        alex = world.get_staff()["barista_alex"]
        self.assertFalse(prepare_result["ok"])
        self.assertFalse(ready_result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "pending")
        self.assertIsNone(alex["current_order_id"])
        self.assertEqual(alex["orders_completed"], 0)

    async def test_ready_rejection_does_not_clear_owner_order_or_increment_completion(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        first_result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        second_result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        alex = world.get_staff()["barista_alex"]
        self.assertTrue(first_result["ok"])
        self.assertFalse(second_result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "ready")
        self.assertIsNone(alex["current_order_id"])
        self.assertEqual(alex["orders_completed"], 1)

    async def test_claimed_order_cannot_be_marked_ready_until_prepared(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)

        result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        alex = world.get_staff()["barista_alex"]
        self.assertFalse(result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "claimed")
        self.assertEqual(alex["current_order_id"], order_id)
        self.assertEqual(alex["orders_completed"], 0)

    async def test_preparing_order_can_be_marked_ready_by_owner(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)

        result = await world.mark_order_ready(order_id, barista_id="barista_alex")

        self.assertTrue(result["ok"])
        self.assertEqual(world.get_order(order_id)["status"], "ready")

    async def test_pipeline_counts_preparing_orders(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)

        pipeline = world.get_order_pipeline()

        self.assertEqual(pipeline["preparing"], 1)

    async def test_barista_operational_snapshot_is_agent_relative(self):
        world = WorldState()
        alex_order_id = await world.place_order("cust_a", ["latte"])
        jamie_order_id = await world.place_order("cust_b", ["espresso"])
        pending_order_id = await world.place_order("cust_c", ["tea"])
        await world.claim_order("barista_alex", alex_order_id)
        await world.claim_order("barista_jamie", jamie_order_id)
        memory = create_shift_memory()
        memory["last_action"] = "claimed a test order"

        alex_prompt = build_barista_cycle_prompt(memory, world, "barista_alex")
        jamie_prompt = build_barista_cycle_prompt(create_shift_memory(), world, "barista_jamie")

        self.assertIn("You are Alex.", alex_prompt)
        self.assertIn("You are Jamie.", jamie_prompt)
        self.assertIn("- Claimed by you: 1", alex_prompt)
        self.assertIn("- Claimed by Jamie: 1", alex_prompt)
        self.assertIn("- Claimed by Alex: 1", jamie_prompt)
        self.assertIn(pending_order_id, alex_prompt)
        self.assertIn("waiting", alex_prompt)

    async def test_barista_operational_snapshot_includes_low_and_out_supplies(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 1
        world._state["supplies"]["muffins"]["quantity"] = 0

        prompt = build_barista_cycle_prompt(create_shift_memory(), world, "barista_alex")

        self.assertIn("Supplies:", prompt)
        self.assertIn("Milk: low (1 left)", prompt)
        self.assertIn("Muffins: out (0 left)", prompt)

    async def test_claim_conflict_metric_counts_existing_non_pending_order(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)

        claimed = await world.claim_order("barista_jamie", order_id)

        summary = world.get_shift_summary()
        self.assertFalse(claimed)
        self.assertEqual(summary["claim_conflicts"], 1)
        self.assertEqual(summary["claim_conflicts_by_barista"]["barista_jamie"], 1)
        self.assertEqual(summary["claim_conflict_pairs"]["barista_jamie->barista_alex"], 1)

    async def test_unknown_order_claim_does_not_count_as_conflict(self):
        world = WorldState()

        claimed = await world.claim_order("barista_jamie", "ord_missing")

        summary = world.get_shift_summary()
        self.assertFalse(claimed)
        self.assertEqual(summary["claim_conflicts"], 0)

    async def test_summary_derives_orders_completed_by_barista_from_staff(self):
        world = WorldState()
        alex_order_id = await world.place_order("cust_a", ["latte"])
        jamie_order_id = await world.place_order("cust_b", ["espresso"])
        await world.claim_order("barista_alex", alex_order_id)
        await world.prepare_order("barista_alex", alex_order_id)
        await world.mark_order_ready(alex_order_id, barista_id="barista_alex")
        await world.claim_order("barista_jamie", jamie_order_id)
        await world.prepare_order("barista_jamie", jamie_order_id)
        await world.mark_order_ready(jamie_order_id, barista_id="barista_jamie")

        summary = world.get_shift_summary()

        self.assertEqual(
            summary["orders_completed_by_barista"],
            {"barista_alex": 1, "barista_jamie": 1},
        )

    async def test_summary_includes_order_lifecycle_durations(self):
        world = WorldState()
        order_id = await world.place_order("cust_test", ["latte"])
        await world.claim_order("barista_alex", order_id)
        await world.prepare_order("barista_alex", order_id)
        await world.mark_order_ready(order_id, barista_id="barista_alex")
        await world.mark_order_delivered(order_id)

        summary = world.get_shift_summary()

        self.assertIsNotNone(summary["average_claim_wait_seconds"])
        self.assertIsNotNone(summary["average_prep_seconds"])
        self.assertIsNotNone(summary["average_total_wait_seconds"])

    async def test_idle_checks_by_barista_increment_through_idle_tool(self):
        world = WorldState()

        with patch("agents.barista.asyncio.sleep", new=AsyncMock()):
            await execute_barista_tool("barista_jamie", "idle", {}, world)

        summary = world.get_shift_summary()
        self.assertEqual(summary["idle_checks_by_barista"]["barista_alex"], 0)
        self.assertEqual(summary["idle_checks_by_barista"]["barista_jamie"], 1)

    async def test_live_snapshot_exposes_staff_state(self):
        world = WorldState()

        snapshot = world.get_live_snapshot(active_customers=[], sim_state={"running": False})

        self.assertIn("staff", snapshot)
        self.assertEqual(snapshot["staff"]["barista_alex"]["display_name"], "Alex")
        self.assertEqual(snapshot["staff"]["barista_jamie"]["display_name"], "Jamie")

    async def test_live_snapshot_exposes_supplies(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 1
        world._state["supplies"]["muffins"]["quantity"] = 0

        snapshot = world.get_live_snapshot(active_customers=[], sim_state={"running": False})

        self.assertEqual(snapshot["supplies"]["milk"]["status"], "low")
        self.assertEqual(snapshot["supplies"]["muffins"]["status"], "out")

    async def test_live_snapshot_exposes_stock_aware_menu_state(self):
        world = WorldState()
        world._state["supplies"]["milk"]["quantity"] = 0
        world.set_menu_item_availability("tea", False)

        snapshot = world.get_live_snapshot(active_customers=[], sim_state={"running": False})

        self.assertFalse(snapshot["menu"]["latte"]["stock_available"])
        self.assertTrue(snapshot["menu"]["latte"]["manually_available"])
        self.assertFalse(snapshot["menu"]["latte"]["orderable"])
        self.assertIn("milk", snapshot["menu"]["latte"]["missing_supplies"])
        self.assertTrue(snapshot["menu"]["tea"]["stock_available"])
        self.assertFalse(snapshot["menu"]["tea"]["manually_available"])
        self.assertFalse(snapshot["menu"]["tea"]["orderable"])
        self.assertEqual(snapshot["menu"]["tea"]["missing_supplies"], {})
        self.assertTrue(snapshot["menu"]["espresso"]["orderable"])

    async def test_running_agent_thinking_rows_include_staff_baristas(self):
        world = WorldState()

        snapshot = world.get_live_snapshot(active_customers=[], sim_state={"running": True})

        thinking_by_id = {entry["agent_id"]: entry for entry in snapshot["agent_thinking"]}
        self.assertEqual(thinking_by_id["barista_alex"]["display_name"], "Alex")
        self.assertEqual(thinking_by_id["barista_jamie"]["display_name"], "Jamie")


if __name__ == "__main__":
    unittest.main()
