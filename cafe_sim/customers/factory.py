"""Shared customer spawning helpers for terminal and dashboard runs."""

import asyncio
import random
import uuid
from typing import Optional

from config import CUSTOMER_RANDOM_SEED
from customers.deterministic import run_deterministic_customer
from customers.profile import CustomerProfile, generate_customer_profile


def build_customer_rng() -> random.Random:
    return random.Random(CUSTOMER_RANDOM_SEED)


def new_customer_id() -> str:
    return f"cust_{uuid.uuid4().hex[:4]}"


def spawn_deterministic_customer(
    world: "WorldState",
    rng: random.Random,
    customer_id: Optional[str] = None,
) -> tuple[CustomerProfile, asyncio.Task]:
    profile = generate_customer_profile(rng, customer_id or new_customer_id())
    visit_rng = random.Random(rng.randint(0, 2**32 - 1))
    task = asyncio.create_task(run_deterministic_customer(profile, world, visit_rng))
    return profile, task


def active_customer_row(profile: CustomerProfile, arrived_at: float) -> dict:
    return {
        "customer_id": profile.customer_id,
        "name": profile.display_name,
        "display_name": profile.display_name,
        "mood": profile.archetype_id,
        "archetype_id": profile.archetype_id,
        "budget": profile.budget,
        "patience": profile.patience,
        "seat_need": profile.seat_need,
        "dwell_seconds_target": profile.dwell_seconds,
        "arrived_at": arrived_at,
    }
