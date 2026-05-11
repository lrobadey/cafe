"""Decision helpers for deterministic customers."""

from itertools import combinations
from random import Random
from typing import Optional

from customers.archetypes import NO_SEAT_PENALTIES, QUEUE_WEIGHTS, STOCKOUT_DISAPPOINTMENT, WAIT_WEIGHTS
from customers.profile import CustomerProfile, CustomerRuntimeState


def friction_breakdown(
    profile: CustomerProfile,
    *,
    queue_length: int,
    elapsed_wait_seconds: float,
    empty_tables: int,
    stockout_disappointment: int = 0,
) -> dict[str, float]:
    no_seat_penalty = 0
    if empty_tables <= 0 and profile.seat_need in {"medium", "high"}:
        no_seat_penalty = NO_SEAT_PENALTIES[profile.no_seat_sensitivity]

    queue = queue_length * QUEUE_WEIGHTS[profile.queue_sensitivity]
    wait = elapsed_wait_seconds * WAIT_WEIGHTS[profile.queue_sensitivity]
    stockout = stockout_disappointment * STOCKOUT_DISAPPOINTMENT
    total = queue + wait + no_seat_penalty + stockout
    return {
        "queue": round(queue, 2),
        "wait": round(wait, 2),
        "no_seat": round(no_seat_penalty, 2),
        "stockout": round(stockout, 2),
        "total": round(total, 2),
    }


def friction_exceeds_patience(profile: CustomerProfile, breakdown: dict[str, float]) -> bool:
    return breakdown["total"] > profile.patience


def leave_reason_from_friction(breakdown: dict[str, float]) -> str:
    if breakdown.get("no_seat", 0) > 0 and breakdown["no_seat"] >= breakdown.get("queue", 0):
        return "no_seats"
    if breakdown.get("stockout", 0) > 0:
        return "nothing_appealing"
    return "impatient"


def affordable_order_candidates(profile: CustomerProfile, menu: dict, budget_remaining: float) -> list[list[str]]:
    orderable = [
        item_id
        for item_id, item in menu.items()
        if item.get("orderable", item.get("available", False)) and item.get("price", 0) <= budget_remaining
    ]
    candidates: list[list[str]] = []
    max_items = min(profile.max_items_per_order, len(orderable))
    for size in range(1, max_items + 1):
        for combo in combinations(orderable, size):
            total = sum(menu[item_id]["price"] for item_id in combo)
            if total <= budget_remaining:
                candidates.append(list(combo))
    return candidates


def score_order(profile: CustomerProfile, menu: dict, items: list[str], budget_remaining: float) -> float:
    total = sum(menu[item_id]["price"] for item_id in items)
    score = 0.0
    for item_id in items:
        item = menu[item_id]
        if item_id in profile.preferred_items:
            preference_rank = profile.preferred_items.index(item_id)
            score += 30 - (preference_rank * 4)
        if item_id in profile.disliked_items:
            score -= 35
        category = item.get("category")
        if category in profile.preferred_categories:
            category_rank = profile.preferred_categories.index(category)
            score += 18 - (category_rank * 4)
        if not item.get("orderable", item.get("available", False)):
            score -= 100
    if budget_remaining > 0:
        score -= (total / budget_remaining) * 8
    if len(items) > 1:
        score += 4
    return round(score, 3)


def choose_order(profile: CustomerProfile, menu: dict, budget_remaining: float, rng: Random) -> Optional[list[str]]:
    candidates = affordable_order_candidates(profile, menu, budget_remaining)
    if not candidates:
        return None
    scored = sorted(
        ((score_order(profile, menu, candidate, budget_remaining), candidate) for candidate in candidates),
        key=lambda entry: entry[0],
        reverse=True,
    )
    top = [candidate for score, candidate in scored[:3] if score > -50]
    if not top:
        return None
    return list(rng.choice(top))


def should_try_reorder(
    profile: CustomerProfile,
    runtime: CustomerRuntimeState,
    *,
    now: float,
    friction: dict[str, float],
    menu: dict,
    rng: Random,
) -> bool:
    if runtime.done or runtime.active_order_id:
        return False
    if runtime.orders_placed >= profile.max_orders_per_visit:
        return False
    if runtime.budget_spent >= profile.budget:
        return False
    if runtime.next_reorder_check_at is None or now < runtime.next_reorder_check_at:
        return False
    if friction_exceeds_patience(profile, friction):
        return False
    if not affordable_order_candidates(profile, menu, profile.budget - runtime.budget_spent):
        return False
    return rng.random() < profile.reorder_chance

