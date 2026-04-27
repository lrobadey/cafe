"""End-to-end tests of run_barista with a scripted fake OpenAI client."""

import asyncio

import pytest

from agents import barista as barista_module
from agents.barista import run_barista
from config import MENU

from tests.conftest import fc, scripted_responses


async def test_barista_completes_a_single_order_cycle(world, monkeypatch):
    order_id = await world.place_order("cust_a", ["latte"])

    fake_create = scripted_responses(
        [fc("check_queue", call_id="b1")],
        [fc("claim_order", {"order_id": order_id}, call_id="b2")],
        [fc("prepare_order", {"order_id": order_id}, call_id="b3")],
        [fc("mark_ready", {"order_id": order_id}, call_id="b4")],
        cancel_when_exhausted=True,
    )
    monkeypatch.setattr(barista_module.client.responses, "create", fake_create)

    slept_for: list[float] = []

    async def fake_sleep(seconds):
        slept_for.append(seconds)

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_barista(world)

    o = world.get_order(order_id)
    assert o["status"] == "ready"
    assert o["barista_id"] == "barista_alex"
    assert MENU["latte"]["prep_seconds"] in slept_for
    assert fake_create.remaining() == 0


async def test_barista_idle_when_queue_empty(world, monkeypatch):
    fake_create = scripted_responses(
        [fc("check_queue", call_id="b1")],
        [fc("idle", call_id="b2")],
        cancel_when_exhausted=True,
    )
    monkeypatch.setattr(barista_module.client.responses, "create", fake_create)

    async def fake_sleep(_seconds):
        pass

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_barista(world)

    # Idle path should not produce any orders or state changes.
    assert world.get_pending_unclaimed_orders() == []
    assert fake_create.remaining() == 0


async def test_barista_handles_already_claimed_order(world, monkeypatch):
    order_id = await world.place_order("cust_a", ["latte"])
    # Some other barista grabbed it first.
    await world.claim_order("barista_other", order_id)

    fake_create = scripted_responses(
        [fc("check_queue", call_id="b1")],
        [fc("claim_order", {"order_id": order_id}, call_id="b2")],
        [fc("idle", call_id="b3")],
        cancel_when_exhausted=True,
    )
    monkeypatch.setattr(barista_module.client.responses, "create", fake_create)

    async def fake_sleep(_seconds):
        pass

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_barista(world)

    # Original claimant retained.
    o = world.get_order(order_id)
    assert o["barista_id"] == "barista_other"
    assert o["status"] == "claimed"
    assert fake_create.remaining() == 0
