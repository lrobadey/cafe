"""FastAPI server for dashboard controls and live state."""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from control import SimulationController
from state_view import build_live_snapshot, build_recent_events

controller = SimulationController()
app = FastAPI(title="Cafe Dashboard API")


class SettingsPayload(BaseModel):
    spawn_interval: Optional[int] = None
    sim_duration: Optional[int] = None


class MenuTogglePayload(BaseModel):
    available: bool


@app.get("/api/snapshot")
async def get_snapshot():
    return build_live_snapshot(controller)


@app.get("/api/events")
async def get_events(after: int = 0, limit: int = 100):
    return build_recent_events(controller, after_index=after, limit=limit)


@app.post("/api/control/start")
async def start_simulation():
    await controller.start()
    return {"ok": True}


@app.post("/api/control/stop")
async def stop_simulation():
    await controller.stop()
    return {"ok": True}


@app.post("/api/control/reset")
async def reset_simulation():
    await controller.reset()
    return {"ok": True}


@app.post("/api/control/spawn")
async def spawn_customer():
    ok = await controller.spawn_customer()
    if not ok:
        raise HTTPException(status_code=409, detail="Simulation is not accepting more customers right now.")
    return {"ok": True}


@app.post("/api/control/settings")
async def update_settings(payload: SettingsPayload):
    if payload.spawn_interval is not None:
        controller.set_spawn_interval(payload.spawn_interval)
    if payload.sim_duration is not None:
        controller.set_sim_duration(payload.sim_duration)
    return {"ok": True}


@app.post("/api/control/menu/{item_id}")
async def toggle_menu_item(item_id: str, payload: MenuTogglePayload):
    changed = controller.toggle_menu_item(item_id=item_id, available=payload.available)
    if not changed:
        raise HTTPException(status_code=404, detail="Menu item not found.")
    return {"ok": True}


@app.get("/api/stream")
async def stream_snapshot():
    async def event_stream():
        while True:
            payload = build_live_snapshot(controller)
            yield f"event: snapshot\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
app.mount("/", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")
