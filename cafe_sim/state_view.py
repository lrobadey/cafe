"""Read-model builders for dashboard payloads."""

from control import SimulationController


def build_live_snapshot(controller: SimulationController) -> dict:
    return controller.world.get_live_snapshot(
        active_customers=controller.get_active_customers(),
        sim_state=controller.get_simulation_state(),
    )


def build_recent_events(controller: SimulationController, after_index: int, limit: int = 100) -> dict:
    events = controller.world.get_recent_events(after_index=after_index, limit=limit)
    return {
        "events": events,
        "next_cursor": after_index + len(events),
    }
