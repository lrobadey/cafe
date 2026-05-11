"""Simulation runner and clock loop."""

import asyncio
import random
import time

from agents.barista import BARISTA_ROSTER, run_barista
from config import (
    CLOSING_GRACE_SECONDS,
    CUSTOMER_SPAWN_INTERVAL,
    CUSTOMER_SPAWN_JITTER,
    MAX_CONCURRENT_CUSTOMERS,
    SIM_DURATION,
)
from customers.factory import build_customer_rng, spawn_deterministic_customer
from logger import log_event
from run_report import RunReporter
from state_view import build_world_snapshot
from world import WorldState


def next_customer_spawn_delay(base_interval: int) -> float:
    spread = base_interval * CUSTOMER_SPAWN_JITTER
    return max(1, random.uniform(base_interval - spread, base_interval + spread))


async def run_simulation():
    reporter = RunReporter()
    world = WorldState(reporter=reporter)
    customer_rng = build_customer_rng()
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

            profile, task = spawn_deterministic_customer(world, customer_rng)
            spawn_count += 1

            world.report(
                "RUNNER",
                "customer_spawned",
                {
                    "spawn_number": spawn_count,
                    "customer_id": profile.customer_id,
                    "archetype_id": profile.archetype_id,
                    "display_name": profile.display_name,
                    "budget": profile.budget,
                    "patience": profile.patience,
                },
            )
            log_event("RUNNER", f"Spawning customer #{spawn_count}: {profile.display_name} ({profile.archetype_id})")
            active_customers.add(task)

        world.report(
            "RUNNER",
            "run_closing",
            {
                "reason": "duration_complete",
                "closing_grace_seconds": CLOSING_GRACE_SECONDS,
            },
        )
        log_event("RUNNER", f"Simulation closing for {CLOSING_GRACE_SECONDS}s.")
        await asyncio.sleep(CLOSING_GRACE_SECONDS)

        for task in barista_tasks:
            task.cancel()
        active_customers = {task for task in active_customers if not task.done()}
        for task in active_customers:
            task.cancel()
        tasks_to_await = [*barista_tasks, *active_customers]
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)
        closeout = await world.closeout_unresolved("duration_complete")

        summary = world.get_shift_summary()
        run_summary = {
            **summary,
            "customers_spawned": spawn_count,
            "duration_seconds": round(time.time() - start_time, 3),
            "stop_reason": "duration_complete",
        }
        final_snapshot = build_world_snapshot(
            world,
            active_customers=[],
            sim_state={
                "running": False,
                "phase": "stopped",
                "elapsed_seconds": int(time.time() - start_time),
                "spawn_count": spawn_count,
                "spawn_interval": CUSTOMER_SPAWN_INTERVAL,
                "spawn_jitter": CUSTOMER_SPAWN_JITTER,
                "sim_duration": SIM_DURATION,
                "max_concurrent_customers": MAX_CONCURRENT_CUSTOMERS,
            },
        )
        alerts = world.get_run_alerts(closeout)
        world.report("RUNNER", "run_completed", run_summary)
        reporter.close("completed", run_summary, final_snapshot=final_snapshot, alerts=alerts)
        log_event("RUNNER", f"Simulation complete. {spawn_count} customers visited.")
        log_event("RUNNER", f"Revenue: ${summary['revenue']:.2f}")
        log_event("RUNNER", f"Orders created: {summary['orders_created']}")
        log_event("RUNNER", f"Orders delivered: {summary['orders_delivered']}")
        log_event("RUNNER", f"Orders not delivered: {summary['orders_not_delivered']}")
        log_event("RUNNER", f"Stockout failures: {summary['stockout_failures']}")
        sold_out = summary.get("sold_out_supplies", {})
        if sold_out:
            sold_out_names = ", ".join(supply["name"] for supply in sold_out.values())
            log_event("RUNNER", f"Sold out supplies: {sold_out_names}")
        else:
            log_event("RUNNER", "Sold out supplies: none")
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
        await asyncio.gather(*barista_tasks, *active_customers, return_exceptions=True)
        closeout = await world.closeout_unresolved("cancelled")
        summary = {
            **world.get_shift_summary(),
            "customers_spawned": spawn_count,
            "duration_seconds": round(time.time() - start_time, 3),
            "stop_reason": "cancelled",
        }
        final_snapshot = build_world_snapshot(
            world,
            active_customers=[],
            sim_state={
                "running": False,
                "phase": "stopped",
                "elapsed_seconds": int(time.time() - start_time),
                "spawn_count": spawn_count,
                "spawn_interval": CUSTOMER_SPAWN_INTERVAL,
                "spawn_jitter": CUSTOMER_SPAWN_JITTER,
                "sim_duration": SIM_DURATION,
                "max_concurrent_customers": MAX_CONCURRENT_CUSTOMERS,
            },
        )
        world.report("RUNNER", "run_stopped", summary)
        reporter.close("cancelled", summary, final_snapshot=final_snapshot, alerts=world.get_run_alerts(closeout))
        raise
    except Exception as exc:
        for task in barista_tasks:
            task.cancel()
        for task in active_customers:
            task.cancel()
        await asyncio.gather(*barista_tasks, *active_customers, return_exceptions=True)
        closeout = await world.closeout_unresolved("failed")
        final_snapshot = build_world_snapshot(
            world,
            active_customers=[],
            sim_state={
                "running": False,
                "phase": "stopped",
                "elapsed_seconds": int(time.time() - start_time),
                "spawn_count": spawn_count,
                "spawn_interval": CUSTOMER_SPAWN_INTERVAL,
                "spawn_jitter": CUSTOMER_SPAWN_JITTER,
                "sim_duration": SIM_DURATION,
                "max_concurrent_customers": MAX_CONCURRENT_CUSTOMERS,
            },
        )
        world.report("RUNNER", "run_failed", {"error": str(exc)})
        reporter.close(
            "failed",
            {"customers_spawned": spawn_count, "error": str(exc)},
            final_snapshot=final_snapshot,
            alerts=world.get_run_alerts(closeout),
        )
        raise
