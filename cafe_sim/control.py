"""Simulation control layer for dashboard mode."""

import asyncio
import random
import time
import uuid
from typing import Optional

from agents.barista import run_barista
from agents.customer import run_customer
from config import CUSTOMER_SPAWN_INTERVAL, CUSTOMER_SPAWN_JITTER, MAX_CONCURRENT_CUSTOMERS, SIM_DURATION
from logger import log_event
from personas import PERSONAS
from run_report import RunReporter
from world import WorldState


class SimulationController:
    def __init__(self):
        self.world = WorldState()
        self.spawn_interval = CUSTOMER_SPAWN_INTERVAL
        self.spawn_jitter = CUSTOMER_SPAWN_JITTER
        self.sim_duration = SIM_DURATION
        self.max_concurrent_customers = MAX_CONCURRENT_CUSTOMERS
        self.running = False
        self.started_at = None
        self.spawn_count = 0
        self._active_customers: dict[str, dict] = {}
        self._barista_task: Optional[asyncio.Task] = None
        self._runner_task: Optional[asyncio.Task] = None
        self._reporter: Optional[RunReporter] = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if self.running:
                return
            self._reporter = RunReporter()
            self.world.attach_reporter(self._reporter)
            self.running = True
            self.started_at = time.time()
            self._barista_task = asyncio.create_task(run_barista(self.world))
            self._runner_task = asyncio.create_task(self._run_loop())
            self.world.report(
                "RUNNER",
                "run_started",
                {
                    "mode": "dashboard",
                    "spawn_interval": self.spawn_interval,
                    "spawn_jitter": self.spawn_jitter,
                    "sim_duration": self.sim_duration,
                    "max_concurrent_customers": self.max_concurrent_customers,
                    "report_dir": str(self._reporter.report_dir),
                },
            )
            log_event("RUNNER", "Dashboard simulation started.")

    async def stop(self, reason: str = "manual_stop"):
        async with self._lock:
            if not self.running:
                return
            self.running = False
            current_task = asyncio.current_task()
            if self._runner_task and self._runner_task is not current_task:
                self._runner_task.cancel()
            if self._barista_task:
                self._barista_task.cancel()
            self._runner_task = None
            self._barista_task = None
            for customer in self._active_customers.values():
                customer["task"].cancel()
            self._active_customers.clear()
            self._finish_report(reason)
            log_event("RUNNER", "Dashboard simulation stopped.")

    async def reset(self):
        await self.stop()
        async with self._lock:
            self.world = WorldState()
            self.started_at = None
            self.spawn_count = 0
            self._reporter = None
            log_event("RUNNER", "Dashboard simulation reset.")

    async def spawn_customer(self):
        async with self._lock:
            if len(self._active_customers) >= self.max_concurrent_customers:
                self.world.report(
                    "RUNNER",
                    "capacity_skip",
                    {
                        "active_customers": len(self._active_customers),
                        "max_concurrent_customers": self.max_concurrent_customers,
                    },
                )
                return False
            persona = random.choice(PERSONAS)
            customer_id = f"cust_{uuid.uuid4().hex[:4]}"
            self.spawn_count += 1
            task = asyncio.create_task(run_customer(persona, self.world, customer_id))
            self._active_customers[customer_id] = {
                "task": task,
                "customer_id": customer_id,
                "name": persona["name"],
                "mood": persona["mood"],
                "arrived_at": time.time(),
            }
            task.add_done_callback(lambda _: self._active_customers.pop(customer_id, None))
            self.world.report(
                "RUNNER",
                "customer_spawned",
                {
                    "spawn_number": self.spawn_count,
                    "customer_id": customer_id,
                    "persona_name": persona["name"],
                    "persona_mood": persona["mood"],
                },
            )
            log_event("RUNNER", f"Spawned customer #{self.spawn_count}: {persona['name']} ({persona['mood']})")
            return True

    def set_spawn_interval(self, value: int):
        self.spawn_interval = max(1, int(value))

    def next_spawn_delay(self) -> float:
        spread = self.spawn_interval * self.spawn_jitter
        return max(1, random.uniform(self.spawn_interval - spread, self.spawn_interval + spread))

    def set_sim_duration(self, value: int):
        self.sim_duration = max(10, int(value))

    def toggle_menu_item(self, item_id: str, available: bool) -> bool:
        return self.world.set_menu_item_availability(item_id, available)

    def get_simulation_state(self) -> dict:
        elapsed = int(time.time() - self.started_at) if self.started_at else 0
        return {
            "running": self.running,
            "elapsed_seconds": elapsed,
            "spawn_count": self.spawn_count,
            "spawn_interval": self.spawn_interval,
            "spawn_jitter": self.spawn_jitter,
            "sim_duration": self.sim_duration,
            "max_concurrent_customers": self.max_concurrent_customers,
        }

    def get_active_customers(self) -> list[dict]:
        customers = []
        for customer in self._active_customers.values():
            customers.append(
                {
                    "customer_id": customer["customer_id"],
                    "name": customer["name"],
                    "mood": customer["mood"],
                    "waiting_seconds": int(time.time() - customer["arrived_at"]),
                }
            )
        return customers

    async def _run_loop(self):
        try:
            while self.running:
                await asyncio.sleep(self.next_spawn_delay())
                if not self.running:
                    break
                elapsed = int(time.time() - self.started_at) if self.started_at else 0
                if elapsed >= self.sim_duration:
                    await self.stop(reason="duration_complete")
                    break
                await self.spawn_customer()
        except asyncio.CancelledError:
            pass

    def _finish_report(self, reason: str):
        if not self._reporter:
            return
        summary = {
            **self.world.get_shift_summary(),
            "customers_spawned": self.spawn_count,
            "duration_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "stop_reason": reason,
        }
        self.world.report("RUNNER", "run_stopped", summary)
        final_status = "completed" if reason == "duration_complete" else "stopped"
        self._reporter.close(final_status, summary)
        self.world.attach_reporter(None)
        self._reporter = None
