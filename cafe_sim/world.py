"""World state and world mutation methods."""

import asyncio
import time
import uuid
from typing import Optional

from config import MENU, TABLE_IDS
from logger import log_event
from run_report import RunReporter


ORDER_PENDING = "pending"
ORDER_CLAIMED = "claimed"
ORDER_PREPARING = "preparing"
ORDER_READY = "ready"
ORDER_DELIVERED = "delivered"
ORDER_ABANDONED = "abandoned"
ORDER_FAILED = "failed"
ORDER_STALE = "stale"

ORDER_STATUSES = (
    ORDER_PENDING,
    ORDER_CLAIMED,
    ORDER_PREPARING,
    ORDER_READY,
    ORDER_DELIVERED,
    ORDER_ABANDONED,
    ORDER_FAILED,
    ORDER_STALE,
)

ORDER_PIPELINE_STATUSES = (
    ORDER_PENDING,
    ORDER_CLAIMED,
    ORDER_PREPARING,
    ORDER_READY,
    ORDER_DELIVERED,
    ORDER_ABANDONED,
    ORDER_STALE,
    ORDER_FAILED,
)

OPEN_ORDER_STATUSES = {ORDER_PENDING, ORDER_CLAIMED, ORDER_PREPARING, ORDER_READY}
UNRESOLVED_ORDER_STATUSES = {ORDER_PENDING, ORDER_CLAIMED, ORDER_PREPARING, ORDER_READY}


class WorldState:
    def __init__(self, reporter: Optional[RunReporter] = None):
        self._lock = asyncio.Lock()
        self.reporter = reporter
        self._state = {
            "menu": {k: dict(v) for k, v in MENU.items()},
            "tables": {tid: {"status": "empty", "customer_id": None} for tid in TABLE_IDS},
            "order_queue": [],
            "event_log": [],
            "agent_thinking": {},
            "staff": {
                "barista_alex": {
                    "display_name": "Alex",
                    "role": "barista",
                    "status": "idle",
                    "current_order_id": None,
                    "orders_completed": 0,
                    "last_action": None,
                },
                "barista_jamie": {
                    "display_name": "Jamie",
                    "role": "barista",
                    "status": "idle",
                    "current_order_id": None,
                    "orders_completed": 0,
                    "last_action": None,
                },
            },
            "coordination_metrics": {
                "claim_conflicts": 0,
                "claim_conflicts_by_barista": {
                    "barista_alex": 0,
                    "barista_jamie": 0,
                },
                "claim_conflict_pairs": {},
                "idle_checks_by_barista": {
                    "barista_alex": 0,
                    "barista_jamie": 0,
                },
            },
            "revenue": 0.0,
        }

    def get_menu(self) -> dict:
        return {k: v for k, v in self._state["menu"].items() if v["available"]}

    def get_table_availability(self) -> dict:
        return {tid: t["status"] for tid, t in self._state["tables"].items()}

    def count_empty_tables(self) -> int:
        return sum(1 for t in self._state["tables"].values() if t["status"] == "empty")

    def get_order(self, order_id: str) -> Optional[dict]:
        for order in self._state["order_queue"]:
            if order["order_id"] == order_id:
                return dict(order)
        return None

    def get_pending_unclaimed_orders(self) -> list[dict]:
        return [dict(order) for order in self._state["order_queue"] if order["status"] == ORDER_PENDING]

    def get_staff(self) -> dict:
        return {staff_id: dict(staff) for staff_id, staff in self._state["staff"].items()}

    def _update_staff(self, staff_id: str, **updates):
        staff = self._state["staff"].get(staff_id)
        if not staff:
            return
        staff.update(updates)

    def queue_length(self) -> int:
        return len([order for order in self._state["order_queue"] if order["status"] in OPEN_ORDER_STATUSES])

    async def place_order(self, customer_id: str, items: list[str]) -> str:
        order_id = f"ord_{uuid.uuid4().hex[:6]}"
        total_price = sum(self._state["menu"][item]["price"] for item in items)
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": items,
            "total_price": total_price,
            "status": ORDER_PENDING,
            "barista_id": None,
            "placed_at": time.time(),
            "claimed_at": None,
            "preparing_at": None,
            "ready_at": None,
            "delivered_at": None,
            "completed_by": None,
            "closed_at": None,
            "close_reason": None,
        }
        async with self._lock:
            self._state["order_queue"].append(order)
            self._state["revenue"] += total_price
        self.log(customer_id, "place_order", f"items={items} -> {order_id}")
        return order_id

    def get_shift_summary(self) -> dict:
        orders = self._state["order_queue"]
        delivered = [order for order in orders if order["status"] == ORDER_DELIVERED]
        abandoned = [order for order in orders if order["status"] == ORDER_ABANDONED]
        stale = [order for order in orders if order["status"] == ORDER_STALE]
        failed = [order for order in orders if order["status"] == ORDER_FAILED]
        ready_waits = [order["ready_at"] - order["placed_at"] for order in orders if order.get("ready_at")]
        delivery_waits = [order["delivered_at"] - order["placed_at"] for order in delivered if order.get("delivered_at")]
        claim_waits = [order["claimed_at"] - order["placed_at"] for order in orders if order.get("claimed_at")]
        prep_durations = [
            order["ready_at"] - order["preparing_at"]
            for order in orders
            if order.get("ready_at") and order.get("preparing_at")
        ]

        return {
            "revenue": round(self._state["revenue"], 2),
            "orders_created": len(orders),
            "orders_delivered": len(delivered),
            "orders_abandoned": len(abandoned),
            "orders_stale": len(stale),
            "orders_failed": len(failed),
            "orders_not_delivered": len(orders) - len(delivered),
            "average_wait_seconds": round(sum(ready_waits) / len(ready_waits), 1) if ready_waits else None,
            "average_total_wait_seconds": round(sum(delivery_waits) / len(delivery_waits), 1) if delivery_waits else None,
            "average_claim_wait_seconds": round(sum(claim_waits) / len(claim_waits), 1) if claim_waits else None,
            "average_prep_seconds": round(sum(prep_durations) / len(prep_durations), 1) if prep_durations else None,
            "events_logged": len(self._state["event_log"]),
            "claim_conflicts": self._state["coordination_metrics"]["claim_conflicts"],
            "claim_conflicts_by_barista": dict(self._state["coordination_metrics"]["claim_conflicts_by_barista"]),
            "claim_conflict_pairs": dict(self._state["coordination_metrics"]["claim_conflict_pairs"]),
            "orders_completed_by_barista": {
                staff_id: staff["orders_completed"]
                for staff_id, staff in self._state["staff"].items()
                if staff["role"] == "barista"
            },
            "idle_checks_by_barista": dict(self._state["coordination_metrics"]["idle_checks_by_barista"]),
        }

    def get_order_pipeline(self) -> dict:
        pipeline = {status: 0 for status in ORDER_PIPELINE_STATUSES}
        for order in self._state["order_queue"]:
            status = order["status"]
            if status in pipeline:
                pipeline[status] += 1
        return pipeline

    def get_barista_operational_snapshot(self, barista_id: str, memory: dict) -> str:
        staff = self._state["staff"].get(barista_id, {})
        display_name = staff.get("display_name", barista_id)
        other_staff = [
            (staff_id, member)
            for staff_id, member in self._state["staff"].items()
            if staff_id != barista_id and member.get("role") == "barista"
        ]
        pending = [order for order in self._state["order_queue"] if order["status"] == ORDER_PENDING]
        claimed_by_you = [
            order
            for order in self._state["order_queue"]
            if order["barista_id"] == barista_id and order["status"] in {ORDER_CLAIMED, ORDER_PREPARING}
        ]
        claimed_by_others = [
            order
            for order in self._state["order_queue"]
            if order["barista_id"] not in (None, barista_id)
            and order["status"] in {ORDER_CLAIMED, ORDER_PREPARING}
        ]
        ready = [order for order in self._state["order_queue"] if order["status"] == ORDER_READY]
        now = time.time()

        if len(pending) >= 3:
            queue_pressure = "busy"
        elif pending:
            queue_pressure = "normal"
        else:
            queue_pressure = "empty"

        memory["recent_queue_pressure"] = queue_pressure
        other_lines = []
        for other_id, other in other_staff:
            other_lines.append(
                f"- Claimed by {other.get('display_name', other_id)}: "
                f"{sum(1 for order in claimed_by_others if order['barista_id'] == other_id)}"
            )
        pending_lines = []
        for order in pending[:6]:
            waited = int(now - order["placed_at"])
            items = ", ".join(order["items"])
            pending_lines.append(f"- {order['order_id']}: {items} for {order['customer_id']}, waiting {waited}s")

        if not pending_lines:
            pending_lines.append("- none")

        current_order = staff.get("current_order_id") or memory.get("current_order_id") or "none"
        last_action = memory.get("last_action") or staff.get("last_action") or "none"

        return "\n".join(
            [
                f"You are {display_name}.",
                "",
                "Shift memory:",
                f"- Orders completed this shift: {memory.get('orders_completed', 0)}",
                f"- Current order: {current_order}",
                f"- Recent queue pressure: {queue_pressure}",
                f"- Failed claim attempts: {memory.get('failed_claims', 0)}",
                f"- Empty queue checks in a row: {memory.get('empty_queue_checks', 0)}",
                f"- Last action: {last_action}",
                "",
                "Queue status:",
                f"- Pending orders: {len(pending)}",
                f"- Claimed by you: {len(claimed_by_you)}",
                *other_lines,
                f"- Ready orders: {len(ready)}",
                "",
                "Pending orders:",
                *pending_lines,
                "",
                "Instruction:",
                "Check the queue and take the next appropriate action.",
            ]
        )

    def get_agent_thinking(self, active_customers: list[dict], sim_state: dict) -> list[dict]:
        active_by_id = {customer["customer_id"]: customer for customer in active_customers}
        agent_rows = []
        if sim_state.get("running"):
            for staff_id, staff in self._state["staff"].items():
                if staff["role"] == "barista":
                    agent_rows.append(
                        {
                            "agent_id": staff_id,
                            "agent_type": "barista",
                            "display_name": staff["display_name"],
                        }
                    )
        for customer in active_customers:
            agent_rows.append(
                {
                    "agent_id": customer["customer_id"],
                    "agent_type": "customer",
                    "display_name": customer["name"],
                }
            )

        thinking = self._state["agent_thinking"]
        result = []
        for row in agent_rows:
            current = thinking.get(row["agent_id"], {})
            result.append(
                {
                    **row,
                    "summary": current.get("summary"),
                    "updated_at": current.get("updated_at"),
                    "persona_mood": active_by_id.get(row["agent_id"], {}).get("mood"),
                }
            )
        return result

    def get_recent_events(self, after_index: int = 0, limit: int = 100) -> list[dict]:
        start = max(0, after_index)
        end = start + max(1, limit)
        return [dict(event) for event in self._state["event_log"][start:end]]

    def set_menu_item_availability(self, item_id: str, available: bool) -> bool:
        item = self._state["menu"].get(item_id)
        if not item:
            return False
        item["available"] = available
        self.log("RUNNER", "menu_availability", f"{item_id} -> {available}")
        return True

    def get_live_snapshot(self, active_customers: list[dict], sim_state: dict) -> dict:
        customer_by_id = {customer["customer_id"]: dict(customer) for customer in active_customers}
        table_by_customer = {
            table["customer_id"]: table_id
            for table_id, table in self._state["tables"].items()
            if table["customer_id"]
        }
        order_by_customer = {}
        for order in self._state["order_queue"]:
            if order["status"] in OPEN_ORDER_STATUSES:
                order_by_customer[order["customer_id"]] = order

        tables = [
            {
                "table_id": table_id,
                "status": table["status"],
                "customer_id": table["customer_id"],
                "customer": customer_by_id.get(table["customer_id"]),
            }
            for table_id, table in self._state["tables"].items()
        ]
        queue = [
            {
                "order_id": order["order_id"],
                "customer_id": order["customer_id"],
                "customer": customer_by_id.get(order["customer_id"]),
                "items": list(order["items"]),
                "item_names": [self._state["menu"][item]["name"] for item in order["items"]],
                "status": order["status"],
                "total_price": order["total_price"],
                "placed_at": order["placed_at"],
                "claimed_at": order["claimed_at"],
                "preparing_at": order["preparing_at"],
                "ready_at": order["ready_at"],
                "delivered_at": order["delivered_at"],
                "barista_id": order["barista_id"],
                "completed_by": order["completed_by"],
                "closed_at": order.get("closed_at"),
                "close_reason": order.get("close_reason"),
            }
            for order in self._state["order_queue"]
        ]
        menu = {
            item_id: {
                "name": item["name"],
                "price": item["price"],
                "prep_seconds": item["prep_seconds"],
                "available": item["available"],
            }
            for item_id, item in self._state["menu"].items()
        }
        return {
            "simulation": dict(sim_state),
            "metrics": self.get_shift_summary(),
            "pipeline": self.get_order_pipeline(),
            "tables": tables,
            "queue": queue,
            "menu": menu,
            "staff": self.get_staff(),
            "agent_thinking": self.get_agent_thinking(active_customers, sim_state),
            "active_customers": [
                {
                    **dict(customer),
                    "table_id": table_by_customer.get(customer["customer_id"]),
                    "order_id": order_by_customer.get(customer["customer_id"], {}).get("order_id"),
                    "order_status": order_by_customer.get(customer["customer_id"], {}).get("status"),
                }
                for customer in active_customers
            ],
            "event_cursor": len(self._state["event_log"]),
        }

    async def closeout_unresolved(self, reason: str) -> dict:
        """Resolve any remaining world state at the end of a run."""
        now = time.time()
        closed_orders = []
        released_tables = []
        cleared_staff = []

        async with self._lock:
            for order in self._state["order_queue"]:
                original_status = order["status"]
                if original_status in {ORDER_PENDING, ORDER_CLAIMED, ORDER_PREPARING}:
                    order["status"] = ORDER_STALE
                elif original_status == ORDER_READY:
                    order["status"] = ORDER_ABANDONED
                else:
                    continue

                order["closed_at"] = now
                order["close_reason"] = reason
                closed_orders.append(
                    {
                        "order_id": order["order_id"],
                        "customer_id": order["customer_id"],
                        "from_status": original_status,
                        "to_status": order["status"],
                        "close_reason": reason,
                    }
                )

            for table_id, table in self._state["tables"].items():
                if table["status"] != "occupied":
                    continue
                released_tables.append(
                    {
                        "table_id": table_id,
                        "customer_id": table["customer_id"],
                        "close_reason": reason,
                    }
                )
                table["status"] = "empty"
                table["customer_id"] = None

            for staff_id, staff in self._state["staff"].items():
                if staff["role"] != "barista":
                    continue
                if staff.get("status") != "idle" or staff.get("current_order_id") is not None:
                    cleared_staff.append(
                        {
                            "staff_id": staff_id,
                            "from_status": staff.get("status"),
                            "from_order_id": staff.get("current_order_id"),
                        }
                    )
                self._update_staff(
                    staff_id,
                    status="idle",
                    current_order_id=None,
                    last_action=f"closed shift after {reason}",
                )

        for order in closed_orders:
            self.log(
                "RUNNER",
                "close_order",
                f"{order['order_id']} {order['from_status']} -> {order['to_status']} ({reason})",
            )
        for table in released_tables:
            self.log(
                "RUNNER",
                "close_table",
                f"{table['table_id']} released from {table['customer_id']} ({reason})",
            )
        for staff in cleared_staff:
            self.log(
                "RUNNER",
                "close_staff",
                f"{staff['staff_id']} cleared from {staff['from_status']} ({reason})",
            )

        result = {
            "reason": reason,
            "closed_orders": closed_orders,
            "released_tables": released_tables,
            "cleared_staff": cleared_staff,
        }
        self.report("RUNNER", "closeout_completed", result)
        return result

    def get_run_alerts(self, closeout: Optional[dict] = None) -> list[dict]:
        orders = self._state["order_queue"]
        alerts = []
        unresolved = [order for order in orders if order["status"] != ORDER_DELIVERED]
        stale = [order for order in orders if order["status"] == ORDER_STALE]
        abandoned = [order for order in orders if order["status"] == ORDER_ABANDONED]
        failed = [order for order in orders if order["status"] == ORDER_FAILED]
        hop_limit_exits = [
            event
            for event in self._state["event_log"]
            if event["action"] == "leave" and event["detail"] == "hop_limit_exceeded"
        ]
        claim_conflicts = self._state["coordination_metrics"]["claim_conflicts"]
        released_tables = (closeout or {}).get("released_tables", [])

        if unresolved:
            alerts.append(
                {
                    "type": "unresolved_orders",
                    "severity": "warning",
                    "count": len(unresolved),
                    "message": f"{len(unresolved)} order(s) were not delivered before closeout.",
                }
            )
        if stale:
            alerts.append(
                {
                    "type": "stale_orders",
                    "severity": "warning",
                    "count": len(stale),
                    "message": f"{len(stale)} in-progress order(s) were marked stale at closeout.",
                }
            )
        if abandoned:
            alerts.append(
                {
                    "type": "abandoned_orders",
                    "severity": "warning",
                    "count": len(abandoned),
                    "message": f"{len(abandoned)} ready order(s) were abandoned at closeout.",
                }
            )
        if failed:
            alerts.append(
                {
                    "type": "failed_orders",
                    "severity": "error",
                    "count": len(failed),
                    "message": f"{len(failed)} order(s) failed during the run.",
                }
            )
        if released_tables:
            alerts.append(
                {
                    "type": "stale_table_cleanup",
                    "severity": "info",
                    "count": len(released_tables),
                    "message": f"{len(released_tables)} occupied table(s) were released during closeout.",
                }
            )
        if claim_conflicts:
            alerts.append(
                {
                    "type": "claim_conflicts",
                    "severity": "info",
                    "count": claim_conflicts,
                    "message": f"{claim_conflicts} barista claim conflict(s) occurred.",
                }
            )
        if hop_limit_exits:
            alerts.append(
                {
                    "type": "hop_limit_exits",
                    "severity": "warning",
                    "count": len(hop_limit_exits),
                    "message": f"{len(hop_limit_exits)} customer(s) hit the hop limit before leaving cleanly.",
                }
            )
        return alerts

    def attach_reporter(self, reporter: Optional[RunReporter]):
        self.reporter = reporter

    def report(self, source: str, event_type: str, payload: Optional[dict] = None):
        if self.reporter:
            self.reporter.event(source, event_type, payload or {})

    def record_agent_thinking(self, agent_id: str, agent_type: str, display_name: str, summary: str):
        clean_summary = (summary or "").strip()
        if not clean_summary:
            return
        now = time.time()
        entry = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "display_name": display_name,
            "summary": clean_summary,
            "updated_at": now,
        }
        self._state["agent_thinking"][agent_id] = entry
        self.report(agent_id, "agent_thinking_summary", entry)

    async def claim_table(self, customer_id: str) -> Optional[str]:
        async with self._lock:
            for table_id, table in self._state["tables"].items():
                if table["status"] == "empty":
                    table["status"] = "occupied"
                    table["customer_id"] = customer_id
                    self.log(customer_id, "claim_table", table_id)
                    return table_id
        return None

    async def release_table(self, customer_id: str):
        async with self._lock:
            for table in self._state["tables"].values():
                if table["customer_id"] == customer_id:
                    table["status"] = "empty"
                    table["customer_id"] = None
        self.log(customer_id, "release_table", "done")

    async def claim_order(self, barista_id: str, order_id: str) -> bool:
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] != order_id:
                    continue
                if order["status"] != ORDER_PENDING:
                    self._state["coordination_metrics"]["claim_conflicts"] += 1
                    conflicts_by_barista = self._state["coordination_metrics"]["claim_conflicts_by_barista"]
                    conflicts_by_barista[barista_id] = conflicts_by_barista.get(barista_id, 0) + 1
                    owner_id = order.get("barista_id") or "none"
                    pair_key = f"{barista_id}->{owner_id}"
                    pairs = self._state["coordination_metrics"]["claim_conflict_pairs"]
                    pairs[pair_key] = pairs.get(pair_key, 0) + 1
                    owner_display = self._state["staff"].get(owner_id, {}).get("display_name", owner_id)
                    self._update_staff(
                        barista_id,
                        last_action=f"failed to claim {order_id}; already claimed by {owner_display}",
                    )
                    return False
                order["status"] = ORDER_CLAIMED
                order["barista_id"] = barista_id
                order["claimed_at"] = time.time()
                self._update_staff(
                    barista_id,
                    status="claimed",
                    current_order_id=order_id,
                    last_action=f"claimed {order_id}",
                )
                self.log(barista_id, "claim_order", order_id)
                return True
        return False

    async def record_idle_check(self, barista_id: str):
        async with self._lock:
            idle_checks = self._state["coordination_metrics"]["idle_checks_by_barista"]
            idle_checks[barista_id] = idle_checks.get(barista_id, 0) + 1

    async def prepare_order(self, barista_id: str, order_id: str) -> dict:
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] != order_id:
                    continue
                if order["status"] == ORDER_PENDING:
                    return {"ok": False, "message": f"Order {order_id} must be claimed before preparing."}
                if order["status"] != ORDER_CLAIMED:
                    return {"ok": False, "message": f"Order {order_id} is {order['status']} and cannot be prepared."}
                if order["barista_id"] != barista_id:
                    owner_display = self._state["staff"].get(order["barista_id"], {}).get("display_name", order["barista_id"])
                    return {
                        "ok": False,
                        "message": f"Order {order_id} is claimed by {owner_display}. Check the queue for your order.",
                    }
                order["status"] = ORDER_PREPARING
                order["preparing_at"] = time.time()
                self._update_staff(
                    barista_id,
                    status="preparing",
                    current_order_id=order_id,
                    last_action=f"preparing {order_id}",
                )
                return {"ok": True, "message": f"Preparing order {order_id}.", "order": dict(order)}
        return {"ok": False, "message": f"Order {order_id} not found."}

    async def mark_order_ready(self, order_id: str, barista_id: Optional[str] = None) -> dict:
        ready_by = barista_id or "barista"
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] != order_id:
                    continue
                if order["status"] == ORDER_PENDING:
                    return {"ok": False, "message": f"Order {order_id} must be claimed before it can be marked ready."}
                if order["status"] == ORDER_CLAIMED:
                    return {"ok": False, "message": f"Order {order_id} must be prepared before it can be marked ready."}
                if order["status"] != ORDER_PREPARING:
                    return {"ok": False, "message": f"Order {order_id} is {order['status']} and cannot be marked ready."}
                if order["barista_id"] != barista_id:
                    owner_display = self._state["staff"].get(order["barista_id"], {}).get("display_name", order["barista_id"])
                    return {
                        "ok": False,
                        "message": f"Order {order_id} is claimed by {owner_display}. Only that barista can mark it ready.",
                    }
                order["status"] = ORDER_READY
                order["ready_at"] = time.time()
                order["completed_by"] = barista_id
                staff = self._state["staff"].get(barista_id)
                completed = staff.get("orders_completed", 0) + 1 if staff else 0
                self._update_staff(
                    barista_id,
                    status="idle",
                    current_order_id=None,
                    orders_completed=completed,
                    last_action=f"marked {order_id} ready",
                )
                self.log(ready_by, "mark_ready", order_id)
                return {"ok": True, "message": f"Order {order_id} is ready for pickup."}
        return {"ok": False, "message": f"Order {order_id} not found."}

    async def update_staff_action(
        self,
        barista_id: str,
        *,
        status: Optional[str] = None,
        current_order_id: Optional[str] = None,
        clear_current_order: bool = False,
        last_action: Optional[str] = None,
    ):
        updates = {}
        if status is not None:
            updates["status"] = status
        if current_order_id is not None:
            updates["current_order_id"] = current_order_id
        if clear_current_order:
            updates["current_order_id"] = None
        if last_action is not None:
            updates["last_action"] = last_action
        if not updates:
            return
        async with self._lock:
            self._update_staff(barista_id, **updates)

    async def mark_order_delivered(self, order_id: str):
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id:
                    order["status"] = ORDER_DELIVERED
                    order["delivered_at"] = time.time()
        self.log("barista", "delivered", order_id)

    def log(self, agent_id: str, action: str, detail: str):
        entry = {"t": time.time(), "agent": agent_id, "action": action, "detail": detail}
        self._state["event_log"].append(entry)
        self.report(
            agent_id,
            "world_event",
            {
                "agent": agent_id,
                "action": action,
                "detail": detail,
                "world_event_index": len(self._state["event_log"]),
            },
        )
        log_event(agent_id, f"{action}: {detail}")
