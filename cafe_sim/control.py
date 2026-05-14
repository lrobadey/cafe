"""Simulation control layer for dashboard mode."""

import asyncio
import random
import time
from typing import Optional

from agents.barista import BARISTA_ROSTER, run_barista
from agents.manager import run_manager_restock_plan as run_manager_agent_restock_plan
from campaign import CampaignState
from config import (
    CLOSING_GRACE_SECONDS,
    CUSTOMER_SPAWN_INTERVAL,
    CUSTOMER_SPAWN_JITTER,
    MAX_CONCURRENT_CUSTOMERS,
    SIM_DURATION,
)
from customers.factory import active_customer_row, build_customer_rng, spawn_deterministic_customer
from logger import log_event
from run_report import RunReporter
from state_view import build_live_snapshot
from world import WorldState


class SimulationController:
    def __init__(self):
        self.campaign = CampaignState.new_campaign()
        self.world = self._new_world_for_current_day()
        self.spawn_interval = CUSTOMER_SPAWN_INTERVAL
        self.spawn_jitter = CUSTOMER_SPAWN_JITTER
        self.sim_duration = SIM_DURATION
        self.max_concurrent_customers = MAX_CONCURRENT_CUSTOMERS
        self.running = False
        self.phase = "idle"
        self.started_at = None
        self.spawn_count = 0
        self.customer_rng = build_customer_rng()
        self._active_customers: dict[str, dict] = {}
        self._barista_tasks: dict[str, asyncio.Task] = {}
        self._runner_task: Optional[asyncio.Task] = None
        self._reporter: Optional[RunReporter] = None
        self._last_closeout: Optional[dict] = None
        self._last_final_snapshot: Optional[dict] = None
        self._last_alerts: list[dict] = []
        self._last_report_paths: dict = {}
        self._manager_running = False
        self._last_manager_result: Optional[dict] = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if self._manager_running:
                return
            if self.phase in {"running", "closing"}:
                return
            if self.campaign.current_day.phase == "settled":
                return
            if self.phase in {"idle", "stopped"} and self.campaign.current_day.phase == "planning":
                self.world = self._new_world_for_current_day()
            self.campaign.begin_day(
                {
                    "spawn_interval": self.spawn_interval,
                    "sim_duration": self.sim_duration,
                    "max_concurrent_customers": self.max_concurrent_customers,
                }
            )
            self._reporter = RunReporter(
                campaign_id=self.campaign.campaign_id,
                day_id=self.campaign.current_day.day_id,
                day_index=self.campaign.current_day.day_index,
            )
            self.world.attach_reporter(self._reporter)
            self._attach_world_context()
            self._last_closeout = None
            self._last_final_snapshot = None
            self._last_alerts = []
            self._last_report_paths = {}
            self.customer_rng = build_customer_rng()
            self.running = True
            self.phase = "running"
            self.started_at = time.time()
            self._barista_tasks = {
                barista["barista_id"]: asyncio.create_task(
                    run_barista(self.world, barista["barista_id"], barista["display_name"])
                )
                for barista in BARISTA_ROSTER
            }
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
        await self._complete_stop(reason)

    async def close_day(self):
        if self.phase in {"running", "closing"}:
            self.campaign.begin_closing()
            await self._complete_stop("day_closed")
            return True
        if self.phase == "stopped" and self.campaign.current_day.phase != "settled":
            return self.settle_day()
        return False

    def settle_day(self) -> bool:
        if self.campaign.current_day.phase == "settled":
            return True
        if self.phase == "running":
            return False
        if not self._last_closeout or not self._last_final_snapshot:
            return False
        self._settle_current_day()
        return True

    def advance_day(self) -> bool:
        if self.phase in {"running", "closing"}:
            return False
        if self.campaign.current_day.phase != "settled":
            return False
        self.campaign.advance_to_next_day()
        self.world = self._new_world_for_current_day()
        self.phase = "idle"
        self.running = False
        self.started_at = None
        self.spawn_count = 0
        self._last_closeout = None
        self._last_final_snapshot = None
        self._last_alerts = []
        self._last_report_paths = {}
        self._last_manager_result = None
        return True

    async def _begin_closing(self, reason: str):
        async with self._lock:
            if self.phase != "running":
                return
            self.phase = "closing"
            self.campaign.begin_closing()
            self.world.report(
                "RUNNER",
                "run_closing",
                {
                    "reason": reason,
                    "closing_grace_seconds": CLOSING_GRACE_SECONDS,
                },
            )
            log_event("RUNNER", f"Dashboard simulation closing for {CLOSING_GRACE_SECONDS}s.")

        await asyncio.sleep(CLOSING_GRACE_SECONDS)
        await self._complete_stop(reason)

    async def _complete_stop(self, reason: str):
        current_task = asyncio.current_task()
        tasks_to_await = []
        async with self._lock:
            if self.phase in {"idle", "stopped"} and not self.running:
                return
            self.running = False
            self.phase = "stopped"
            if self._runner_task and self._runner_task is not current_task:
                self._runner_task.cancel()
                tasks_to_await.append(self._runner_task)
            for task in self._barista_tasks.values():
                task.cancel()
                tasks_to_await.append(task)
            self._runner_task = None
            self._barista_tasks = {}
            for customer in self._active_customers.values():
                customer["task"].cancel()
                tasks_to_await.append(customer["task"])
            self._active_customers.clear()

        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)
        closeout = await self.world.closeout_unresolved(reason)
        final_snapshot = self._finish_report(reason, closeout)
        self._last_closeout = closeout
        self._last_final_snapshot = final_snapshot
        if reason in {"duration_complete", "day_closed"}:
            self._settle_current_day()
        log_event("RUNNER", "Dashboard simulation stopped.")

    async def reset(self):
        await self.stop()
        async with self._lock:
            if self.campaign.current_day.phase != "settled":
                self.campaign.current_day.phase = "planning"
                self.campaign.status = "planning"
                self.campaign.save()
            self.world = self._new_world_for_current_day()
            self.phase = "idle"
            self.running = False
            self.started_at = None
            self.spawn_count = 0
            self._reporter = None
            self._last_manager_result = None
            log_event("RUNNER", "Dashboard simulation reset.")

    async def spawn_customer(self):
        async with self._lock:
            if self.phase != "running":
                self.world.report(
                    "RUNNER",
                    "spawn_skip",
                    {
                        "phase": self.phase,
                        "active_customers": len(self._active_customers),
                    },
                )
                return False
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
            profile, task = spawn_deterministic_customer(self.world, self.customer_rng)
            self.spawn_count += 1
            row = active_customer_row(profile, time.time())
            self._active_customers[profile.customer_id] = {
                **row,
                "task": task,
            }
            task.add_done_callback(lambda _: self._active_customers.pop(profile.customer_id, None))
            self.world.report(
                "RUNNER",
                "customer_spawned",
                {
                    "spawn_number": self.spawn_count,
                    "customer_id": profile.customer_id,
                    "archetype_id": profile.archetype_id,
                    "display_name": profile.display_name,
                    "budget": profile.budget,
                    "patience": profile.patience,
                },
            )
            log_event("RUNNER", f"Spawned customer #{self.spawn_count}: {profile.display_name} ({profile.archetype_id})")
            return True

    def set_spawn_interval(self, value: int):
        self.spawn_interval = max(1, int(value))

    def next_spawn_delay(self) -> float:
        spread = self.spawn_interval * self.spawn_jitter
        return max(1, random.uniform(self.spawn_interval - spread, self.spawn_interval + spread))

    def set_sim_duration(self, value: int):
        self.sim_duration = max(10, int(value))

    def toggle_menu_item(self, item_id: str, available: bool) -> bool:
        changed = self.world.set_menu_item_availability(item_id, available)
        if changed:
            self.campaign.update_menu_availability(item_id, available)
        return changed

    def restock_supply(self, supply_id: str, quantity: int) -> dict:
        if self.phase in {"running", "closing"}:
            return {"ok": False, "error": "Restocking is only available outside live service."}
        result = self.campaign.restock(supply_id, quantity)
        if result.get("ok"):
            self.world.restock_supply(supply_id, quantity)
        return result

    async def run_manager_restock_plan(self) -> dict:
        async with self._lock:
            if self._manager_running:
                return {"ok": False, "error": "Manager is already planning a restock."}
            if self.phase in {"running", "closing"} or self.campaign.current_day.phase != "planning":
                return {"ok": False, "error": "Manager restock planning is only available during day planning."}
            if not self.campaign.day_summaries:
                return {"ok": False, "error": "Manager needs a completed day summary before planning restocks."}
            self._manager_running = True

        self.world.report("manager", "agent_started", {"agent_type": "manager"})
        try:
            result = await run_manager_agent_restock_plan(self)
            payload = {"ok": True, **result}
            self._last_manager_result = payload
            self.world.report(
                "manager",
                "manager_plan_finalized",
                {
                    "agent_type": "manager",
                    "manager_summary": payload.get("manager_summary"),
                    "tool_result_count": len(payload.get("tool_results") or []),
                    "plan": payload.get("plan"),
                },
            )
            return payload
        finally:
            async with self._lock:
                self._manager_running = False

    def get_manager_state(self) -> dict:
        return {
            "running": self._manager_running,
            "last_summary": (self._last_manager_result or {}).get("manager_summary"),
            "last_tool_result_count": len((self._last_manager_result or {}).get("tool_results") or []),
        }

    def get_simulation_state(self) -> dict:
        elapsed = int(time.time() - self.started_at) if self.started_at else 0
        return {
            "running": self.running,
            "phase": self.phase,
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
                    "display_name": customer.get("display_name", customer["name"]),
                    "mood": customer["mood"],
                    "archetype_id": customer.get("archetype_id"),
                    "budget": customer.get("budget"),
                    "patience": customer.get("patience"),
                    "seat_need": customer.get("seat_need"),
                    "dwell_seconds_target": customer.get("dwell_seconds_target"),
                    "waiting_seconds": int(time.time() - customer["arrived_at"]),
                }
            )
        return customers

    def get_snapshot(self) -> dict:
        return build_live_snapshot(self)

    def get_events(self, after_index: int, limit: int = 100, day_id: Optional[str] = None, campaign_id: Optional[str] = None) -> dict:
        raw_events = self.world.get_recent_events(after_index=after_index, limit=limit)
        events = raw_events
        if day_id:
            events = [event for event in events if event.get("day_id") == day_id]
        if campaign_id:
            events = [event for event in events if event.get("campaign_id") == campaign_id]
        return {
            "events": events,
            "next_cursor": after_index + len(raw_events),
        }

    async def _run_loop(self):
        try:
            while self.running and self.phase == "running":
                await asyncio.sleep(self.next_spawn_delay())
                if not self.running or self.phase != "running":
                    break
                elapsed = int(time.time() - self.started_at) if self.started_at else 0
                if elapsed >= self.sim_duration:
                    await self._begin_closing(reason="duration_complete")
                    break
                await self.spawn_customer()
        except asyncio.CancelledError:
            pass

    def _finish_report(self, reason: str, closeout: dict) -> dict:
        final_snapshot = self.get_snapshot()
        if not self._reporter:
            self._last_alerts = self.world.get_run_alerts(closeout)
            self._last_report_paths = {}
            return final_snapshot
        alerts = self.world.get_run_alerts(closeout)
        summary = {
            **self.world.get_shift_summary(),
            "customers_spawned": self.spawn_count,
            "duration_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "stop_reason": reason,
        }
        self.world.report("RUNNER", "run_stopped", summary)
        final_status = "completed" if reason == "duration_complete" else "stopped"
        self._reporter.close(final_status, summary, final_snapshot=final_snapshot, alerts=alerts)
        self._last_alerts = alerts
        self._last_report_paths = {
            key: str(value)
            for key, value in {
                "report_dir": getattr(self._reporter, "report_dir", None),
                "events_path": getattr(self._reporter, "events_path", None),
                "summary_path": getattr(self._reporter, "summary_path", None),
            }.items()
            if value is not None
        }
        self.world.attach_reporter(None)
        self._reporter = None
        return final_snapshot

    def _settle_current_day(self):
        if self.campaign.current_day.phase == "settled" or not self._last_final_snapshot:
            return
        self.campaign.settle_current_day(
            metrics=self.world.get_shift_summary(),
            closeout=self._last_closeout or {},
            final_snapshot=self._last_final_snapshot,
            events=self.world.get_recent_events(after_index=0, limit=10000),
            alerts=self._last_alerts,
            report_paths=self._last_report_paths,
        )

    def _new_world_for_current_day(self) -> WorldState:
        world = WorldState(
            initial_supplies=self.campaign.persistent_supplies,
            initial_menu=self.campaign.menu_state,
        )
        self._attach_world_context(world)
        return world

    def _attach_world_context(self, world: Optional[WorldState] = None):
        target = world or self.world
        target.set_event_context(
            campaign_id=self.campaign.campaign_id,
            day_id=self.campaign.current_day.day_id,
            day_index=self.campaign.current_day.day_index,
            sim_time_provider=self._current_sim_time,
        )

    def _current_sim_time(self) -> str:
        sim_state = self.get_simulation_state()
        return self.campaign.calendar_snapshot(
            elapsed_seconds=sim_state["elapsed_seconds"],
            sim_duration=sim_state["sim_duration"],
        )["sim_current_time"]
