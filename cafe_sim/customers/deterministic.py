"""Async visit loop for deterministic customers."""

import asyncio
import time
from random import Random
from typing import Optional

from customers.decisions import (
    choose_order,
    friction_breakdown,
    friction_exceeds_patience,
    leave_reason_from_friction,
    should_try_reorder,
)
from customers.profile import CustomerProfile, CustomerRuntimeState

POLL_SECONDS = 1


async def run_deterministic_customer(profile: CustomerProfile, world: "WorldState", rng: Random):
    runtime = CustomerRuntimeState(customer_id=profile.customer_id, arrived_at=time.time())
    await _register_visit(profile, runtime, world)
    world.report(
        profile.customer_id,
        "deterministic_customer_started",
        {
            "agent_type": "customer",
            "customer_id": profile.customer_id,
            "archetype_id": profile.archetype_id,
            "display_name": profile.display_name,
            "budget": profile.budget,
            "patience": profile.patience,
        },
    )
    world.log(profile.customer_id, "enter_cafe", profile.display_name)

    try:
        await _run_visit(profile, runtime, world, rng)
    finally:
        if not runtime.done:
            await _leave(profile, runtime, world, "cancelled")
        world.report(
            profile.customer_id,
            "deterministic_customer_finished",
            {
                "agent_type": "customer",
                "customer_id": profile.customer_id,
                "archetype_id": profile.archetype_id,
                "done": runtime.done,
                "order_ids": list(runtime.order_ids),
                "table_id": runtime.table_id,
                "visit_phase": runtime.visit_phase,
                "orders_placed": runtime.orders_placed,
                "budget_spent": round(runtime.budget_spent, 2),
            },
        )


async def _run_visit(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState", rng: Random):
    if await _should_leave_from_current_friction(profile, runtime, world, stockout_disappointment=0):
        return

    order_items = choose_order(profile, world.get_menu_availability(), profile.budget, rng)
    if not order_items:
        await _leave(profile, runtime, world, "too_expensive")
        return

    await _place_order(profile, runtime, world, order_items)
    await _maybe_claim_seat(profile, runtime, world)
    if runtime.done:
        return
    await _wait_for_active_order(profile, runtime, world, rng)
    if runtime.done:
        return

    await _consume_held_items(profile, runtime, world)
    if profile.leave_after_pickup:
        await _dwell(profile, runtime, world, min(profile.dwell_seconds, 5))
        await _leave(profile, runtime, world, "satisfied")
        return

    await _dwell_and_maybe_reorder(profile, runtime, world, rng)
    if not runtime.done:
        await _leave(profile, runtime, world, "satisfied")


async def _register_visit(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState"):
    persona = {"name": profile.display_name, "mood": profile.archetype_id}
    await world.register_customer_visit(profile.customer_id, persona, runtime.arrived_at)
    await _sync_visit(profile, runtime, world)


async def _sync_visit(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState", **extra):
    updates = {
        "name": profile.display_name,
        "mood": profile.archetype_id,
        "archetype_id": profile.archetype_id,
        "display_name": profile.display_name,
        "budget": profile.budget,
        "budget_spent": round(runtime.budget_spent, 2),
        "budget_remaining": round(max(0, profile.budget - runtime.budget_spent), 2),
        "patience": profile.patience,
        "seat_need": profile.seat_need,
        "orders_placed": runtime.orders_placed,
        "order_ids": list(runtime.order_ids),
        "active_order_id": runtime.active_order_id,
        "table_id": runtime.table_id,
        "visit_phase": runtime.visit_phase,
        "held_items": list(runtime.held_items),
        "consumed_items": list(runtime.consumed_items),
        "dwell_seconds_target": profile.dwell_seconds,
        "next_reorder_check_at": runtime.next_reorder_check_at,
    }
    updates.update(extra)
    await world.update_customer_visit(profile.customer_id, **updates)


async def _should_leave_from_current_friction(
    profile: CustomerProfile,
    runtime: CustomerRuntimeState,
    world: "WorldState",
    *,
    stockout_disappointment: int,
) -> bool:
    breakdown = friction_breakdown(
        profile,
        queue_length=world.queue_length(),
        elapsed_wait_seconds=time.time() - runtime.arrived_at,
        empty_tables=world.count_empty_tables(),
        stockout_disappointment=stockout_disappointment,
    )
    await _sync_visit(profile, runtime, world, friction=breakdown)
    if not friction_exceeds_patience(profile, breakdown):
        return False
    await _leave(profile, runtime, world, leave_reason_from_friction(breakdown), friction=breakdown)
    return True


async def _place_order(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState", items: list[str]) -> bool:
    try:
        order_id = await world.place_order(profile.customer_id, items)
    except ValueError:
        await _leave(profile, runtime, world, "nothing_appealing")
        return False
    order = world.get_order(order_id) or {}
    runtime.active_order_id = order_id
    runtime.order_ids.append(order_id)
    runtime.orders_placed += 1
    runtime.visit_phase = "waiting"
    await _sync_visit(profile, runtime, world)
    world.report(
        profile.customer_id,
        "deterministic_order_placed",
        {
            "customer_id": profile.customer_id,
            "archetype_id": profile.archetype_id,
            "order_id": order_id,
            "items": list(items),
            "budget_spent": round(runtime.budget_spent, 2),
        },
    )
    return True


async def _maybe_claim_seat(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState"):
    if profile.seat_need == "low":
        return
    table_id = await world.claim_table(profile.customer_id)
    if table_id:
        runtime.table_id = table_id
        await _sync_visit(profile, runtime, world, table_claimed_at=time.time())
        return
    if profile.no_seat_sensitivity == "high":
        breakdown = friction_breakdown(
            profile,
            queue_length=world.queue_length(),
            elapsed_wait_seconds=time.time() - runtime.arrived_at,
            empty_tables=0,
        )
        if friction_exceeds_patience(profile, breakdown):
            await _leave(profile, runtime, world, "no_seats", friction=breakdown)


async def _wait_for_active_order(
    profile: CustomerProfile,
    runtime: CustomerRuntimeState,
    world: "WorldState",
    rng: Random,
):
    while runtime.active_order_id and not runtime.done:
        order = world.get_order(runtime.active_order_id)
        if not order:
            runtime.active_order_id = None
            runtime.visit_phase = "order_failed"
            await _sync_visit(profile, runtime, world)
            return
        if order["status"] == "ready":
            await world.mark_order_delivered(order["order_id"])
            runtime.held_items.extend(order["items"])
            runtime.budget_spent += float(order.get("total_price", 0.0))
            runtime.active_order_id = None
            runtime.visit_phase = "received_order"
            now = time.time()
            if profile.reorder_check_after_seconds is not None:
                runtime.next_reorder_check_at = now + profile.reorder_check_after_seconds
            await _sync_visit(profile, runtime, world, received_order_at=now)
            return
        if order["status"] == "failed":
            runtime.active_order_id = None
            runtime.visit_phase = "order_failed"
            await _sync_visit(profile, runtime, world)
            if await _should_leave_from_current_friction(profile, runtime, world, stockout_disappointment=1):
                return
            await _try_replacement_order_after_failure(profile, runtime, world, rng)
            return

        breakdown = friction_breakdown(
            profile,
            queue_length=world.queue_length(),
            elapsed_wait_seconds=time.time() - (order.get("placed_at") or runtime.arrived_at),
            empty_tables=world.count_empty_tables(),
        )
        await _sync_visit(profile, runtime, world, friction=breakdown)
        if friction_exceeds_patience(profile, breakdown):
            await _leave(profile, runtime, world, leave_reason_from_friction(breakdown), friction=breakdown)
            return
        await asyncio.sleep(POLL_SECONDS)


async def _try_replacement_order_after_failure(
    profile: CustomerProfile,
    runtime: CustomerRuntimeState,
    world: "WorldState",
    rng: Random,
):
    if runtime.done or runtime.active_order_id:
        return
    if runtime.orders_placed >= profile.max_orders_per_visit:
        return
    budget_remaining = profile.budget - runtime.budget_spent
    if budget_remaining <= 0:
        return
    order_items = choose_order(profile, world.get_menu_availability(), budget_remaining, rng)
    if not order_items:
        return
    if await _place_order(profile, runtime, world, order_items):
        await _wait_for_active_order(profile, runtime, world, rng)


async def _consume_held_items(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState"):
    for item_id in list(runtime.held_items):
        if item_id in runtime.consumed_items:
            continue
        item = world.get_menu_item(item_id)
        if not item:
            continue
        action_name = "sip_drink" if item.get("category") == "drink" else "eat_item"
        expected_category = item.get("category") or "food"
        result = await world.consume_customer_item(profile.customer_id, item_id, expected_category, action_name)
        if result["ok"]:
            runtime.consumed_items = result["consumed_items"]
            runtime.visit_phase = "consuming"
            await _sync_visit(profile, runtime, world, consumption_started_at=time.time())


async def _dwell_and_maybe_reorder(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState", rng: Random):
    if profile.requires_seat_to_dwell and not runtime.table_id:
        await _leave(profile, runtime, world, "no_seats")
        return
    await _dwell(profile, runtime, world, profile.dwell_seconds, rng=rng)


async def _dwell(
    profile: CustomerProfile,
    runtime: CustomerRuntimeState,
    world: "WorldState",
    seconds: int,
    *,
    rng: Optional[Random] = None,
):
    runtime.visit_phase = "dwelling"
    runtime.dwell_started_at = runtime.dwell_started_at or time.time()
    await _sync_visit(profile, runtime, world, dwell_started_at=runtime.dwell_started_at)
    end_at = time.time() + max(0, seconds)
    while time.time() < end_at and not runtime.done:
        now = time.time()
        breakdown = friction_breakdown(
            profile,
            queue_length=world.queue_length(),
            elapsed_wait_seconds=now - runtime.arrived_at,
            empty_tables=world.count_empty_tables(),
        )
        await _sync_visit(profile, runtime, world, friction=breakdown)
        if friction_exceeds_patience(profile, breakdown):
            await _leave(profile, runtime, world, leave_reason_from_friction(breakdown), friction=breakdown)
            return
        if rng and should_try_reorder(
            profile,
            runtime,
            now=now,
            friction=breakdown,
            menu=world.get_menu_availability(),
            rng=rng,
        ):
            order_items = choose_order(profile, world.get_menu_availability(), profile.budget - runtime.budget_spent, rng)
            if order_items and await _place_order(profile, runtime, world, order_items):
                await _wait_for_active_order(profile, runtime, world, rng)
                if runtime.done:
                    return
                await _consume_held_items(profile, runtime, world)
                runtime.next_reorder_check_at = None
                await _sync_visit(profile, runtime, world)
        await asyncio.sleep(min(POLL_SECONDS, max(0, end_at - time.time())))


async def _leave(profile: CustomerProfile, runtime: CustomerRuntimeState, world: "WorldState", reason: str, **extra):
    if runtime.done:
        return
    runtime.done = True
    runtime.visit_phase = "done"
    left_at = time.time()
    left_with_unconsumed = bool(set(runtime.held_items) - set(runtime.consumed_items))
    if runtime.table_id:
        await world.release_table(profile.customer_id)
        extra.setdefault("table_released_at", left_at)
    await _sync_visit(
        profile,
        runtime,
        world,
        leave_reason=reason,
        left_at=left_at,
        dwell_seconds_actual=round(left_at - runtime.dwell_started_at, 2) if runtime.dwell_started_at else 0,
        left_with_unconsumed_items=left_with_unconsumed,
        **extra,
    )
    world.log(profile.customer_id, "leave", f"{reason}; archetype={profile.archetype_id}")
