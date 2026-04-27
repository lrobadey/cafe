"""Simulation runner and clock loop."""

import asyncio
import random
import time
import uuid

from agents.barista import run_barista
from agents.customer import run_customer
from config import CUSTOMER_SPAWN_INTERVAL, MAX_CONCURRENT_CUSTOMERS, SIM_DURATION
from logger import log_event
from personas import PERSONAS
from world import WorldState


async def run_simulation():
    world = WorldState()
    active_customers = set()
    barista_task = asyncio.create_task(run_barista(world))

    start_time = time.time()
    spawn_count = 0

    log_event("RUNNER", "Simulation started. Barista on shift.")

    while time.time() - start_time < SIM_DURATION:
        await asyncio.sleep(CUSTOMER_SPAWN_INTERVAL)
        active_customers = {task for task in active_customers if not task.done()}

        if len(active_customers) >= MAX_CONCURRENT_CUSTOMERS:
            log_event("RUNNER", f"At capacity ({MAX_CONCURRENT_CUSTOMERS} customers). Skipping spawn.")
            continue

        persona = random.choice(PERSONAS)
        customer_id = f"cust_{uuid.uuid4().hex[:4]}"
        spawn_count += 1

        log_event("RUNNER", f"Spawning customer #{spawn_count}: {persona['name']} ({persona['mood']})")
        active_customers.add(asyncio.create_task(run_customer(persona, world, customer_id)))

    barista_task.cancel()
    try:
        await barista_task
    except asyncio.CancelledError:
        pass
    if active_customers:
        await asyncio.gather(*active_customers, return_exceptions=True)

    log_event("RUNNER", f"Simulation complete. {spawn_count} customers visited.")
    log_event("RUNNER", f"Total events logged: {len(world._state['event_log'])}")
