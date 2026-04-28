"""World state and world mutation methods."""

import asyncio
import time
import uuid
from typing import Optional

from config import MENU, TABLE_IDS
from logger import log_event
from run_report import RunReporter


class WorldState:
    def __init__(self, reporter: Optional[RunReporter] = None):
        self._lock = asyncio.Lock()
        self.reporter = reporter
        self._state = {
            "menu": {k: dict(v) for k, v in MENU.items()},
            "tables": {tid: {"status": "empty", "customer_id": None} for tid in TABLE_IDS},
            "order_queue": [],
            "event_log": [],
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
        return [dict(order) for order in self._state["order_queue"] if order["status"] == "pending"]

    def queue_length(self) -> int:
        return len([order for order in self._state["order_queue"] if order["status"] != "delivered"])

    async def place_order(self, customer_id: str, items: list[str]) -> str:
        order_id = f"ord_{uuid.uuid4().hex[:6]}"
        total_price = sum(self._state["menu"][item]["price"] for item in items)
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": items,
            "total_price": total_price,
            "status": "pending",
            "barista_id": None,
            "placed_at": time.time(),
            "ready_at": None,
        }
        async with self._lock:
            self._state["order_queue"].append(order)
            self._state["revenue"] += total_price
        self.log(customer_id, "place_order", f"items={items} -> {order_id}")
        return order_id

    def get_shift_summary(self) -> dict:
        orders = self._state["order_queue"]
        delivered = [order for order in orders if order["status"] == "delivered"]
        waits = [
            order["ready_at"] - order["placed_at"]
            for order in orders
            if order.get("ready_at") is not None
        ]

        return {
            "revenue": round(self._state["revenue"], 2),
            "orders_created": len(orders),
            "orders_delivered": len(delivered),
            "orders_not_delivered": len(orders) - len(delivered),
            "average_wait_seconds": round(sum(waits) / len(waits), 1) if waits else None,
            "events_logged": len(self._state["event_log"]),
        }

    def get_order_pipeline(self) -> dict:
        pipeline = {"pending": 0, "claimed": 0, "ready": 0, "delivered": 0}
        for order in self._state["order_queue"]:
            status = order["status"]
            if status in pipeline:
                pipeline[status] += 1
        return pipeline

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
            if order["status"] != "delivered":
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
                "ready_at": order["ready_at"],
                "barista_id": order["barista_id"],
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

    def attach_reporter(self, reporter: Optional[RunReporter]):
        self.reporter = reporter

    def report(self, source: str, event_type: str, payload: Optional[dict] = None):
        if self.reporter:
            self.reporter.event(source, event_type, payload or {})

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
                if order["order_id"] == order_id and order["status"] == "pending":
                    order["status"] = "claimed"
                    order["barista_id"] = barista_id
                    self.log(barista_id, "claim_order", order_id)
                    return True
        return False

    async def mark_order_ready(self, order_id: str):
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id:
                    order["status"] = "ready"
                    order["ready_at"] = time.time()
        self.log("barista", "mark_ready", order_id)

    async def mark_order_delivered(self, order_id: str):
        async with self._lock:
            for order in self._state["order_queue"]:
                if order["order_id"] == order_id:
                    order["status"] = "delivered"
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
