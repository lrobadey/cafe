"""Tests for execute_customer_tool against a real WorldState."""

import time

import pytest

from agents.customer import execute_customer_tool


def _local_state():
    return {
        "order_id": None,
        "table_id": None,
        "done": False,
        "arrived_at": time.time(),
    }


async def test_enter_cafe_reports_world_status(world):
    from config import TABLE_IDS

    state = _local_state()
    result = await execute_customer_tool("enter_cafe", {}, "cust_a", world, state)
    expected = f"Empty tables: {len(TABLE_IDS)}/{len(TABLE_IDS)}"
    assert expected in result
    assert "Orders currently in queue: 0" in result


async def test_read_menu_lists_available_items(world):
    state = _local_state()
    result = await execute_customer_tool("read_menu", {}, "cust_a", world, state)
    assert "Latte" in result and "latte" in result
    assert "Espresso" in result
    assert "$5.50" in result  # latte price formatted

    # Hide an item; it should disappear from the menu output.
    world._state["menu"]["latte"]["available"] = False
    result = await execute_customer_tool("read_menu", {}, "cust_a", world, state)
    assert "Latte" not in result
    assert "Espresso" in result


async def test_place_order_happy_path(world):
    state = _local_state()
    result = await execute_customer_tool(
        "place_order", {"items": ["latte"]}, "cust_a", world, state
    )
    assert "Order placed" in result
    assert state["order_id"] is not None
    assert world.queue_length() == 1
    pending = world.get_pending_unclaimed_orders()
    assert pending[0]["customer_id"] == "cust_a"
    assert pending[0]["items"] == ["latte"]


async def test_place_order_invalid_item_does_not_corrupt_world(world):
    state = _local_state()
    result = await execute_customer_tool(
        "place_order", {"items": ["nonexistent"]}, "cust_a", world, state
    )
    assert "not on the menu" in result
    assert state["order_id"] is None
    assert world.queue_length() == 0


async def test_place_order_twice_blocks_second(world):
    state = _local_state()
    await execute_customer_tool(
        "place_order", {"items": ["latte"]}, "cust_a", world, state
    )
    result = await execute_customer_tool(
        "place_order", {"items": ["tea"]}, "cust_a", world, state
    )
    assert "already placed an order" in result
    assert world.queue_length() == 1


async def test_find_seat_first_call_then_already_seated(world):
    state = _local_state()
    result = await execute_customer_tool("find_seat", {}, "cust_a", world, state)
    assert "table" in result
    assert state["table_id"] is not None

    result2 = await execute_customer_tool("find_seat", {}, "cust_a", world, state)
    assert "already seated" in result2


async def test_find_seat_no_seats(world):
    # Fill all tables with other customers.
    for i in range(4):
        await world.claim_table(f"other_{i}")
    state = _local_state()
    result = await execute_customer_tool("find_seat", {}, "cust_a", world, state)
    assert "No seats" in result
    assert state["table_id"] is None


async def test_check_order_no_id_returns_default_message(world):
    state = _local_state()
    result = await execute_customer_tool(
        "check_order", {"order_id": ""}, "cust_a", world, state
    )
    assert "don't have an order" in result


async def test_check_order_pending(world):
    state = _local_state()
    order_id = await world.place_order("cust_a", ["latte"])
    state["order_id"] = order_id
    result = await execute_customer_tool(
        "check_order", {"order_id": order_id}, "cust_a", world, state
    )
    assert "still in the queue" in result


async def test_check_order_claimed(world):
    state = _local_state()
    order_id = await world.place_order("cust_a", ["latte"])
    state["order_id"] = order_id
    await world.claim_order("barista_alex", order_id)
    result = await execute_customer_tool(
        "check_order", {"order_id": order_id}, "cust_a", world, state
    )
    assert "preparing" in result


async def test_check_order_ready_marks_delivered(world):
    state = _local_state()
    order_id = await world.place_order("cust_a", ["latte"])
    state["order_id"] = order_id
    await world.claim_order("barista_alex", order_id)
    await world.mark_order_ready(order_id)

    result = await execute_customer_tool(
        "check_order", {"order_id": order_id}, "cust_a", world, state
    )
    assert "ready" in result
    # Delivery is awaited inline, so the world reflects it as soon as the
    # tool returns — no background task race.
    assert world.get_order(order_id)["status"] == "delivered"


async def test_check_order_delivered_branch(world):
    state = _local_state()
    order_id = await world.place_order("cust_a", ["latte"])
    state["order_id"] = order_id
    await world.claim_order("barista_alex", order_id)
    await world.mark_order_ready(order_id)
    await world.mark_order_delivered(order_id)
    result = await execute_customer_tool(
        "check_order", {"order_id": order_id}, "cust_a", world, state
    )
    assert "already received" in result


async def test_check_order_missing_id_in_world(world):
    state = _local_state()
    state["order_id"] = "ord_missing"
    result = await execute_customer_tool(
        "check_order", {"order_id": "ord_missing"}, "cust_a", world, state
    )
    assert "Order not found" in result


async def test_leave_releases_table_and_marks_done(world):
    state = _local_state()
    await execute_customer_tool("find_seat", {}, "cust_a", world, state)
    held_table = state["table_id"]
    result = await execute_customer_tool(
        "leave", {"reason": "satisfied"}, "cust_a", world, state
    )
    assert "leave the cafe" in result
    assert state["done"] is True
    assert world.get_table_availability()[held_table] == "empty"
    log_actions = [e["action"] for e in world._state["event_log"]]
    assert "leave" in log_actions


async def test_leave_without_table_still_marks_done(world):
    state = _local_state()
    await execute_customer_tool(
        "leave", {"reason": "no_seats"}, "cust_a", world, state
    )
    assert state["done"] is True


async def test_unknown_tool_returns_unknown_message(world):
    state = _local_state()
    result = await execute_customer_tool(
        "not_a_real_tool", {}, "cust_a", world, state
    )
    assert "Unknown tool" in result
