"""Read-model builders for dashboard payloads."""

from control import SimulationController


def build_live_snapshot(controller: SimulationController) -> dict:
    return controller.get_snapshot()


def build_recent_events(controller: SimulationController, after_index: int, limit: int = 100) -> dict:
    return controller.get_events(after_index=after_index, limit=limit)
