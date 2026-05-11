"""Customer profile generation and visit runtime state."""

from dataclasses import dataclass, field
from random import Random
from typing import Optional

from customers.archetypes import ARCHETYPES, Archetype


@dataclass(frozen=True)
class CustomerProfile:
    customer_id: str
    archetype_id: str
    display_name: str
    preferred_items: list[str]
    disliked_items: list[str]
    preferred_categories: list[str]
    budget: float
    max_items_per_order: int
    max_orders_per_visit: int
    patience: int
    seat_need: str
    queue_sensitivity: str
    no_seat_sensitivity: str
    dwell_seconds: int
    reorder_chance: float
    reorder_check_after_seconds: Optional[int]
    requires_seat_to_dwell: bool
    leave_after_pickup: bool


@dataclass
class CustomerRuntimeState:
    customer_id: str
    arrived_at: float
    active_order_id: Optional[str] = None
    order_ids: list[str] = field(default_factory=list)
    table_id: Optional[str] = None
    visit_phase: str = "arrived"
    held_items: list[str] = field(default_factory=list)
    consumed_items: list[str] = field(default_factory=list)
    orders_placed: int = 0
    budget_spent: float = 0.0
    done: bool = False
    dwell_started_at: Optional[float] = None
    next_reorder_check_at: Optional[float] = None


def choose_archetype(rng: Random) -> Archetype:
    total_weight = sum(archetype.weight for archetype in ARCHETYPES)
    roll = rng.uniform(0, total_weight)
    cumulative = 0.0
    for archetype in ARCHETYPES:
        cumulative += archetype.weight
        if roll <= cumulative:
            return archetype
    return ARCHETYPES[-1]


def weighted_item_list(weighted: dict[str, float], rng: Random) -> list[str]:
    items = list(weighted)
    rng.shuffle(items)
    return sorted(items, key=lambda item_id: weighted[item_id], reverse=True)


def generate_customer_profile(rng: Random, customer_id: str) -> CustomerProfile:
    archetype = choose_archetype(rng)
    reorder_check_after = None
    if archetype.reorder_check_interval_seconds:
        start, end = archetype.reorder_check_interval_seconds
        reorder_check_after = rng.randint(start, end)

    return CustomerProfile(
        customer_id=customer_id,
        archetype_id=archetype.id,
        display_name=archetype.display_name,
        preferred_items=weighted_item_list(archetype.preferred_items, rng),
        disliked_items=weighted_item_list(archetype.disliked_items, rng),
        preferred_categories=weighted_item_list(archetype.preferred_categories, rng),
        budget=round(rng.uniform(*archetype.budget_range), 2),
        max_items_per_order=rng.randint(*archetype.max_items_per_order_range),
        max_orders_per_visit=archetype.max_orders_per_visit,
        patience=rng.randint(*archetype.patience_range),
        seat_need=archetype.seat_need,
        queue_sensitivity=archetype.queue_sensitivity,
        no_seat_sensitivity=archetype.no_seat_sensitivity,
        dwell_seconds=rng.randint(*archetype.dwell_range_seconds),
        reorder_chance=archetype.reorder_chance,
        reorder_check_after_seconds=reorder_check_after,
        requires_seat_to_dwell=archetype.requires_seat_to_dwell,
        leave_after_pickup=archetype.leave_after_pickup,
    )

