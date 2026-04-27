"""Unit tests for WorldState transitions."""

import asyncio

import pytest

from config import MENU, TABLE_IDS
from world import WorldState


def test_initial_state(world):
    assert world.count_empty_tables() == len(TABLE_IDS) == 4
    assert world.queue_length() == 0
    assert world.get_pending_unclaimed_orders() == []
    menu = world.get_menu()
    assert set(menu.keys()) == set(MENU.keys())
    for tid in TABLE_IDS:
        assert world.get_table_availability()[tid] == "empty"


def test_get_menu_filters_unavailable(world):
    world._state["menu"]["latte"]["available"] = False
    menu = world.get_menu()
    assert "latte" not in menu
    assert "espresso" in menu


async def test_place_order_appends_and_logs(world):
    order_id = await world.place_order("cust_a", ["latte"])
    assert order_id.startswith("ord_")
    assert world.queue_length() == 1
    pending = world.get_pending_unclaimed_orders()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["customer_id"] == "cust_a"
    assert pending[0]["items"] == ["latte"]
    log = world._state["event_log"]
    assert any(entry["action"] == "place_order" for entry in log)


async def test_claim_table_assigns_unique_tables_then_returns_none(world):
    claimed = []
    for i in range(len(TABLE_IDS)):
        tid = await world.claim_table(f"cust_{i}")
        assert tid is not None
        claimed.append(tid)
    assert len(set(claimed)) == len(TABLE_IDS)
    assert await world.claim_table("cust_overflow") is None
    assert world.count_empty_tables() == 0


async def test_release_table_only_clears_owners_table(world):
    a = await world.claim_table("cust_a")
    b = await world.claim_table("cust_b")
    assert a != b

    await world.release_table("cust_a")
    avail = world.get_table_availability()
    assert avail[a] == "empty"
    assert avail[b] == "occupied"

    # Releasing a non-owner is a no-op (no exception, no other table cleared).
    await world.release_table("cust_unknown")
    assert world.get_table_availability()[b] == "occupied"


async def test_claim_order_succeeds_once_then_fails(world):
    order_id = await world.place_order("cust_a", ["latte"])
    assert await world.claim_order("barista_alex", order_id) is True
    # Already claimed: second attempt returns False.
    assert await world.claim_order("barista_alex", order_id) is False
    order = world.get_order(order_id)
    assert order["status"] == "claimed"
    assert order["barista_id"] == "barista_alex"


async def test_full_order_lifecycle(world):
    order_id = await world.place_order("cust_a", ["espresso"])
    assert world.get_order(order_id)["status"] == "pending"

    assert await world.claim_order("barista_alex", order_id) is True
    assert world.get_order(order_id)["status"] == "claimed"

    await world.mark_order_ready(order_id)
    o = world.get_order(order_id)
    assert o["status"] == "ready"
    assert o["ready_at"] is not None

    await world.mark_order_delivered(order_id)
    assert world.get_order(order_id)["status"] == "delivered"
    # Delivered orders no longer count toward queue length.
    assert world.queue_length() == 0


async def test_pending_unclaimed_excludes_other_statuses(world):
    o1 = await world.place_order("cust_a", ["latte"])
    o2 = await world.place_order("cust_b", ["tea"])
    o3 = await world.place_order("cust_c", ["muffin"])

    await world.claim_order("barista_alex", o1)
    await world.mark_order_ready(o2)

    pending_ids = {o["order_id"] for o in world.get_pending_unclaimed_orders()}
    assert pending_ids == {o3}


async def test_queue_length_ignores_delivered_only(world):
    o1 = await world.place_order("cust_a", ["latte"])
    o2 = await world.place_order("cust_b", ["tea"])
    await world.claim_order("barista_alex", o1)
    await world.mark_order_ready(o1)

    # Both orders still count: ready hasn't been delivered yet.
    assert world.queue_length() == 2

    await world.mark_order_delivered(o1)
    assert world.queue_length() == 1
    assert world.get_order(o2)["status"] == "pending"


async def test_concurrent_claim_order_only_one_wins(world):
    order_id = await world.place_order("cust_a", ["latte"])
    results = await asyncio.gather(
        *[world.claim_order(f"barista_{i}", order_id) for i in range(10)]
    )
    assert results.count(True) == 1
    assert results.count(False) == 9
    assert world.get_order(order_id)["status"] == "claimed"


def test_get_order_returns_none_for_missing(world):
    assert world.get_order("ord_missing") is None
