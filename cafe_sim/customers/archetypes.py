"""Structured customer archetypes for deterministic demand simulation."""

from dataclasses import dataclass
from typing import Optional


QUEUE_WEIGHTS = {
    "low": 3,
    "medium": 6,
    "high": 10,
}

WAIT_WEIGHTS = {
    "low": 0.10,
    "medium": 0.20,
    "high": 0.35,
}

NO_SEAT_PENALTIES = {
    "none": 0,
    "low": 8,
    "medium": 18,
    "high": 35,
}

STOCKOUT_DISAPPOINTMENT = 12


@dataclass(frozen=True)
class Archetype:
    id: str
    display_name: str
    weight: int
    preferred_items: dict[str, float]
    disliked_items: dict[str, float]
    preferred_categories: dict[str, float]
    budget_range: tuple[float, float]
    max_items_per_order_range: tuple[int, int]
    max_orders_per_visit: int
    patience_range: tuple[int, int]
    seat_need: str
    queue_sensitivity: str
    no_seat_sensitivity: str
    dwell_range_seconds: tuple[int, int]
    reorder_chance: float
    reorder_check_interval_seconds: Optional[tuple[int, int]]
    requires_seat_to_dwell: bool
    leave_after_pickup: bool


ARCHETYPES = [
    Archetype(
        id="hurried_commuter",
        display_name="Hurried Commuter",
        weight=35,
        preferred_items={"espresso": 0.40, "cold_brew": 0.35, "latte": 0.20, "tea": 0.05},
        disliked_items={"muffin": 0.35, "tea": 0.15},
        preferred_categories={"drink": 0.95, "food": 0.05},
        budget_range=(4.00, 8.50),
        max_items_per_order_range=(1, 1),
        max_orders_per_visit=1,
        patience_range=(10, 35),
        seat_need="low",
        queue_sensitivity="high",
        no_seat_sensitivity="none",
        dwell_range_seconds=(0, 5),
        reorder_chance=0.00,
        reorder_check_interval_seconds=None,
        requires_seat_to_dwell=False,
        leave_after_pickup=True,
    ),
    Archetype(
        id="remote_worker",
        display_name="Remote Worker",
        weight=25,
        preferred_items={"cold_brew": 0.35, "latte": 0.30, "tea": 0.20, "muffin": 0.15},
        disliked_items={"espresso": 0.15},
        preferred_categories={"drink": 0.70, "food": 0.30},
        budget_range=(10.00, 20.00),
        max_items_per_order_range=(1, 2),
        max_orders_per_visit=2,
        patience_range=(60, 95),
        seat_need="high",
        queue_sensitivity="medium",
        no_seat_sensitivity="high",
        dwell_range_seconds=(180, 600),
        reorder_chance=0.45,
        reorder_check_interval_seconds=(120, 240),
        requires_seat_to_dwell=True,
        leave_after_pickup=False,
    ),
    Archetype(
        id="leisure_customer",
        display_name="Leisure Customer",
        weight=40,
        preferred_items={"latte": 0.35, "tea": 0.25, "muffin": 0.25, "cold_brew": 0.10, "espresso": 0.05},
        disliked_items={},
        preferred_categories={"drink": 0.60, "food": 0.40},
        budget_range=(7.00, 15.00),
        max_items_per_order_range=(1, 2),
        max_orders_per_visit=2,
        patience_range=(40, 75),
        seat_need="medium",
        queue_sensitivity="medium",
        no_seat_sensitivity="medium",
        dwell_range_seconds=(45, 180),
        reorder_chance=0.15,
        reorder_check_interval_seconds=(90, 180),
        requires_seat_to_dwell=False,
        leave_after_pickup=False,
    ),
]


ARCHETYPE_BY_ID = {archetype.id: archetype for archetype in ARCHETYPES}

