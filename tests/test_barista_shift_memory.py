import unittest

from agents.barista import build_barista_cycle_prompt, create_shift_memory, render_shift_memory, update_shift_memory


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


if __name__ == "__main__":
    unittest.main()
