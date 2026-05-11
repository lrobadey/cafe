"""Read-model builders for dashboard and report payloads."""

from world import OPEN_ORDER_STATUSES


def build_world_snapshot(world, active_customers: list[dict], sim_state: dict) -> dict:
    customer_by_id = _build_customer_rows(world, active_customers)
    table_by_customer = _table_by_customer(world)
    order_by_customer = _open_order_by_customer(world)

    return {
        "simulation": dict(sim_state),
        "metrics": world.get_shift_summary(),
        "pipeline": world.get_order_pipeline(),
        "tables": _build_table_rows(world, customer_by_id),
        "queue": _build_queue_rows(world, customer_by_id),
        "menu": world.get_menu_availability(),
        "supplies": world.get_supplies(),
        "staff": world.get_staff(),
        "agent_thinking": _build_agent_thinking(world, active_customers, sim_state),
        "active_customers": [
            {
                **customer_by_id.get(customer["customer_id"], dict(customer)),
                "table_id": table_by_customer.get(customer["customer_id"]),
                "order_id": order_by_customer.get(customer["customer_id"], {}).get("order_id"),
                "order_status": order_by_customer.get(customer["customer_id"], {}).get("status"),
            }
            for customer in active_customers
        ],
        "event_cursor": world.get_event_cursor(),
    }


def build_live_snapshot(controller) -> dict:
    sim_state = controller.get_simulation_state()
    snapshot = build_world_snapshot(
        controller.world,
        active_customers=controller.get_active_customers(),
        sim_state=sim_state,
    )
    snapshot.update(
        {
            "campaign": controller.campaign.campaign_snapshot(),
            "calendar": controller.campaign.calendar_snapshot(
                elapsed_seconds=sim_state["elapsed_seconds"],
                sim_duration=sim_state["sim_duration"],
            ),
            "day_summary": controller.campaign.current_day.summary,
            "history": controller.campaign.history_snapshot(),
        }
    )
    return snapshot


def build_recent_events(controller, after_index: int, limit: int = 100) -> dict:
    return controller.get_events(after_index=after_index, limit=limit)


def _build_customer_rows(world, active_customers: list[dict]) -> dict:
    customer_by_id = {}
    for customer in active_customers:
        customer_id = customer["customer_id"]
        visit = world.get_customer_visit(customer_id)
        held_items = visit.get("held_items", [])
        consumed_items = visit.get("consumed_items", [])
        customer_by_id[customer_id] = {
            **dict(customer),
            "visit_phase": visit.get("visit_phase", "arrived"),
            "archetype_id": visit.get("archetype_id") or customer.get("archetype_id"),
            "display_name": visit.get("display_name") or customer.get("display_name") or customer.get("name"),
            "budget": visit.get("budget", customer.get("budget")),
            "budget_spent": visit.get("budget_spent"),
            "budget_remaining": visit.get("budget_remaining"),
            "patience": visit.get("patience", customer.get("patience")),
            "seat_need": visit.get("seat_need", customer.get("seat_need")),
            "orders_placed": visit.get("orders_placed"),
            "order_ids": visit.get("order_ids", []),
            "active_order_id": visit.get("active_order_id"),
            "dwell_seconds_target": visit.get("dwell_seconds_target", customer.get("dwell_seconds_target")),
            "dwell_seconds_actual": visit.get("dwell_seconds_actual"),
            "next_reorder_check_at": visit.get("next_reorder_check_at"),
            "leave_reason": visit.get("leave_reason"),
            "friction": visit.get("friction"),
            "held_items": held_items,
            "held_item_names": world.get_menu_item_names(held_items),
            "consumed_items": consumed_items,
            "consumed_item_names": world.get_menu_item_names(consumed_items),
            "received_order_at": visit.get("received_order_at"),
            "consumption_started_at": visit.get("consumption_started_at"),
        }
    return customer_by_id


def _table_by_customer(world) -> dict:
    return {
        table["customer_id"]: table_id
        for table_id, table in world.get_tables().items()
        if table["customer_id"]
    }


def _open_order_by_customer(world) -> dict:
    order_by_customer = {}
    for order in world.get_orders():
        if order["status"] in OPEN_ORDER_STATUSES:
            order_by_customer[order["customer_id"]] = order
    return order_by_customer


def _build_table_rows(world, customer_by_id: dict) -> list[dict]:
    return [
        {
            "table_id": table_id,
            "status": table["status"],
            "customer_id": table["customer_id"],
            "customer": customer_by_id.get(table["customer_id"]),
        }
        for table_id, table in world.get_tables().items()
    ]


def _build_queue_rows(world, customer_by_id: dict) -> list[dict]:
    return [
        {
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "customer": customer_by_id.get(order["customer_id"]),
            "items": list(order["items"]),
            "item_names": world.get_menu_item_names(order["items"]),
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
            "missing_supplies": dict(order.get("missing_supplies", {})),
        }
        for order in world.get_orders()
    ]


def _build_agent_thinking(world, active_customers: list[dict], sim_state: dict) -> list[dict]:
    active_by_id = {customer["customer_id"]: customer for customer in active_customers}
    agent_rows = []
    if sim_state.get("running"):
        for staff_id, staff in world.get_staff().items():
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

    thinking = world.get_agent_thinking_entries()
    return [
        {
            **row,
            "summary": thinking.get(row["agent_id"], {}).get("summary"),
            "updated_at": thinking.get(row["agent_id"], {}).get("updated_at"),
            "persona_mood": active_by_id.get(row["agent_id"], {}).get("mood"),
        }
        for row in agent_rows
    ]
