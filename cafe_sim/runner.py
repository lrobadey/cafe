"""Simulation runner and clock loop."""

import asyncio
import random
import time
import uuid

from agents.barista import BARISTA_ROSTER, run_barista
from agents.customer import run_customer
from config import CUSTOMER_SPAWN_INTERVAL, CUSTOMER_SPAWN_JITTER, MAX_CONCURRENT_CUSTOMERS, SIM_DURATION
from logger import log_event
from personas import PERSONAS
from run_report import RunReporter
from world import WorldState


def next_customer_spawn_delay(base_interval: int) -> float:
    spread = base_interval * CUSTOMER_SPAWN_JITTER
    return max(1, random.uniform(base_interval - spread, base_interval + spread))


async def run_simulation():
    reporter = RunReporter()
    world = WorldState(reporter=reporter)
    active_customers = set()
    barista_tasks = [
        asyncio.create_task(run_barista(world, barista["barista_id"], barista["display_name"]))
        for barista in BARISTA_ROSTER
    ]

    start_time = time.time()
    spawn_count = 0

    world.report(
        "RUNNER",
        "run_started",
        {
            "mode": "terminal",
            "customer_spawn_interval": CUSTOMER_SPAWN_INTERVAL,
            "customer_spawn_jitter": CUSTOMER_SPAWN_JITTER,
            "max_concurrent_customers": MAX_CONCURRENT_CUSTOMERS,
            "sim_duration": SIM_DURATION,
            "report_dir": str(reporter.report_dir),
        },
    )
    log_event("RUNNER", "Simulation started. Baristas on shift.")

    try:
        while time.time() - start_time < SIM_DURATION:
            await asyncio.sleep(next_customer_spawn_delay(CUSTOMER_SPAWN_INTERVAL))
            active_customers = {task for task in active_customers if not task.done()}

            if len(active_customers) >= MAX_CONCURRENT_CUSTOMERS:
                world.report(
                    "RUNNER",
                    "capacity_skip",
                    {
                        "active_customers": len(active_customers),
                        "max_concurrent_customers": MAX_CONCURRENT_CUSTOMERS,
                    },
                )
                log_event("RUNNER", f"At capacity ({MAX_CONCURRENT_CUSTOMERS} customers). Skipping spawn.")
                continue

            persona = random.choice(PERSONAS)
            customer_id = f"cust_{uuid.uuid4().hex[:4]}"
            spawn_count += 1

            world.report(
                "RUNNER",
                "customer_spawned",
                {
                    "spawn_number": spawn_count,
                    "customer_id": customer_id,
                    "persona_name": persona["name"],
                    "persona_mood": persona["mood"],
                },
            )
            log_event("RUNNER", f"Spawning customer #{spawn_count}: {persona['name']} ({persona['mood']})")
            active_customers.add(asyncio.create_task(run_customer(persona, world, customer_id)))

        for task in barista_tasks:
            task.cancel()
        if active_customers:
            await asyncio.gather(*active_customers, return_exceptions=True)

        summary = world.get_shift_summary()
        run_summary = {
            **summary,
            "customers_spawned": spawn_count,
            "duration_seconds": round(time.time() - start_time, 3),
        }
        world.report("RUNNER", "run_completed", run_summary)
        reporter.close("completed", run_summary)
        log_event("RUNNER", f"Simulation complete. {spawn_count} customers visited.")
        log_event("RUNNER", f"Revenue: ${summary['revenue']:.2f}")
        log_event("RUNNER", f"Orders created: {summary['orders_created']}")
        log_event("RUNNER", f"Orders delivered: {summary['orders_delivered']}")
        log_event("RUNNER", f"Orders not delivered: {summary['orders_not_delivered']}")
        average_wait = summary["average_wait_seconds"]
        average_wait_display = f"{average_wait}s" if average_wait is not None else "n/a"
        log_event("RUNNER", f"Average wait: {average_wait_display}")
        log_event("RUNNER", f"Total events logged: {summary['events_logged']}")
        log_event("RUNNER", f"Run report: {reporter.report_dir}")
    except asyncio.CancelledError:
        for task in barista_tasks:
            task.cancel()
        for task in active_customers:
            task.cancel()
        summary = {
            **world.get_shift_summary(),
            "customers_spawned": spawn_count,
            "duration_seconds": round(time.time() - start_time, 3),
            "stop_reason": "cancelled",
        }
        world.report("RUNNER", "run_stopped", summary)
        reporter.close("cancelled", summary)
        raise
    except Exception as exc:
        for task in barista_tasks:
            task.cancel()
        for task in active_customers:
            task.cancel()
        world.report("RUNNER", "run_failed", {"error": str(exc)})
        reporter.close("failed", {"customers_spawned": spawn_count, "error": str(exc)})
        raise
