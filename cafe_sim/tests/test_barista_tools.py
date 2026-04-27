"""Tests for execute_barista_tool against a real WorldState."""

import asyncio

import pytest

from agents import barista as barista_module
from agents.barista import execute_barista_tool
from config import BARISTA_POLL_INTERVAL, MENU


async def test_check_queue_empty(world):
    result = await execute_barista_tool("check_queue", {}, world)
    assert "Queue is empty" in result


async def test_check_queue_with_orders(world):
    await world.place_order("cust_a", ["latte"])
    await world.place_order("cust_b", ["tea", "muffin"])
    result = await execute_barista_tool("check_queue", {}, world)
    assert "2 order(s) waiting" in result
    assert "cust_a" in result
    assert "cust_b" in result
    assert "tea, muffin" in result


async def test_claim_order_success(world):
    order_id = await world.place_order("cust_a", ["latte"])
    result = await execute_barista_tool("claim_order", {"order_id": order_id}, world)
    assert "Claimed order" in result
    assert world.get_order(order_id)["status"] == "claimed"


async def test_claim_order_already_claimed(world):
    order_id = await world.place_order("cust_a", ["latte"])
    await world.claim_order("barista_other", order_id)
    result = await execute_barista_tool("claim_order", {"order_id": order_id}, world)
    assert "already claimed" in result
    # World state still owned by the original claimant.
    assert world.get_order(order_id)["barista_id"] == "barista_other"


async def test_prepare_order_not_found(world):
    result = await execute_barista_tool(
        "prepare_order", {"order_id": "ord_missing"}, world
    )
    assert "not found" in result


async def test_prepare_order_uses_max_prep_seconds(world, monkeypatch):
    slept_for: list[float] = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    order_id = await world.place_order("cust_a", ["latte", "muffin"])
    await world.claim_order("barista_alex", order_id)

    result = await execute_barista_tool(
        "prepare_order", {"order_id": order_id}, world
    )
    expected = max(MENU["latte"]["prep_seconds"], MENU["muffin"]["prep_seconds"])
    assert slept_for == [expected]
    assert f"in {expected}s" in result


async def test_mark_ready_updates_status(world):
    order_id = await world.place_order("cust_a", ["latte"])
    await world.claim_order("barista_alex", order_id)
    result = await execute_barista_tool("mark_ready", {"order_id": order_id}, world)
    assert "ready for pickup" in result
    o = world.get_order(order_id)
    assert o["status"] == "ready"
    assert o["ready_at"] is not None


async def test_idle_sleeps_for_poll_interval(world, monkeypatch):
    slept_for: list[float] = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    result = await execute_barista_tool("idle", {}, world)
    assert slept_for == [BARISTA_POLL_INTERVAL]
    assert "Break done" in result


async def test_unknown_tool_returns_unknown_message(world):
    result = await execute_barista_tool("not_a_real_tool", {}, world)
    assert "Unknown tool" in result
