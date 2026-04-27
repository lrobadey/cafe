"""World state and world mutation methods."""

import asyncio
import time
import uuid

from config import MENU, TABLE_IDS


class WorldState:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._state = {
            "menu": {k: dict(v) for k, v in MENU.items()},
            "tables": {tid: {"status": "empty", "customer_id": None} for tid in TABLE_IDS},
            "order_queue": [],
            "event_log": [],
        }

    def get_menu(self) -> dict:
        return {k: v for k, v in self._state["menu"].items() if v["available"]}

    def get_table_availability(self) -> dict:
        return {tid: t["status"] for tid, t in self._state["tables"].items()}

    def count_empty_tables(self) -> int:
        return sum(1 for t in self._state["tables"].values() if t["status"] == "empty")

    def get_order(self, order_id: str) -> dict | None:
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
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": items,
            "status": "pending",
            "barista_id": None,
            "placed_at": time.time(),
            "ready_at": None,
        }
        async with self._lock:
            self._state["order_queue"].append(order)
        self.log(customer_id, "place_order", f"items={items} -> {order_id}")
        return order_id

    async def claim_table(self, customer_id: str) -> str | None:
        async with self._lock:
            for table_id, table in self._state["tables"].items():
                if table["status"] == "empty":
                    table["status"] = "occupied"
                    table["customer_id"] = customer_id
                    self.log(customer_id, "claim_table", table_id)
                    return table_id
        return None

    async def release_table(self, customer_id: str):
        released_table_id: str | None = None
        async with self._lock:
            for table_id, table in self._state["tables"].items():
                if table["customer_id"] == customer_id:
                    table["status"] = "empty"
                    table["customer_id"] = None
                    released_table_id = table_id
                    break
        if released_table_id is not None:
            self.log(customer_id, "release_table", released_table_id)

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
        from logger import log_event

        log_event(agent_id, f"{action}: {detail}")
