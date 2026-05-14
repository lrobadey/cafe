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


class RestockPayload(BaseModel):
    supply_id: str
    quantity: int


@app.get("/api/snapshot")
async def get_snapshot():
    return build_live_snapshot(controller)


@app.get("/api/events")
async def get_events(after: int = 0, limit: int = 100, day_id: Optional[str] = None, campaign_id: Optional[str] = None):
    return controller.get_events(after_index=after, limit=limit, day_id=day_id, campaign_id=campaign_id)


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


@app.post("/api/campaign/create")
async def create_campaign():
    raise HTTPException(status_code=409, detail="CafeLab v1 supports one auto-created active campaign per server run.")


@app.get("/api/campaign")
async def get_campaign():
    snapshot = build_live_snapshot(controller)
    return {
        "campaign": snapshot["campaign"],
        "calendar": snapshot["calendar"],
        "history": snapshot["history"],
    }


@app.get("/api/campaigns")
async def list_campaigns():
    return {"campaigns": [controller.campaign.campaign_snapshot()]}


@app.post("/api/campaign/load")
async def load_campaign():
    raise HTTPException(status_code=409, detail="Campaign loading is not enabled in the single-active-campaign v1.")


@app.post("/api/day/start")
async def start_day():
    await controller.start()
    return {"ok": True}


@app.post("/api/day/close")
async def close_day():
    ok = await controller.close_day()
    if not ok:
        raise HTTPException(status_code=409, detail="Current day cannot be closed from this state.")
    return {"ok": True, "day_summary": controller.campaign.current_day.summary}


@app.post("/api/day/settle")
async def settle_day():
    ok = controller.settle_day()
    if not ok:
        raise HTTPException(status_code=409, detail="Current day is not ready to settle.")
    return {"ok": True, "day_summary": controller.campaign.current_day.summary}


@app.post("/api/day/advance")
async def advance_day():
    ok = controller.advance_day()
    if not ok:
        raise HTTPException(status_code=409, detail="Settle the current day before advancing.")
    return {
        "ok": True,
        "calendar": controller.campaign.calendar_snapshot(),
        "campaign": controller.campaign.campaign_snapshot(),
    }


@app.get("/api/day/plan")
async def get_day_plan():
    return {"plan": controller.campaign.current_day.opening_plan}


@app.post("/api/day/plan")
async def update_day_plan(plan: dict):
    if controller.phase in {"running", "closing"}:
        raise HTTPException(status_code=409, detail="Planning changes are locked during live service.")
    controller.campaign.current_day.opening_plan.update(plan)
    controller.campaign.save()
    return {"ok": True, "plan": controller.campaign.current_day.opening_plan}


@app.post("/api/restock")
async def restock(payload: RestockPayload):
    result = controller.restock_supply(payload.supply_id, payload.quantity)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "Supply could not be restocked.")
    return {"ok": True, **result}


@app.post("/api/manager/restock")
async def manager_restock():
    result = await controller.run_manager_restock_plan()
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "Manager could not plan restocks.")
    return result


@app.post("/api/staff/schedule")
async def schedule_staff():
    raise HTTPException(status_code=409, detail="Staff scheduling is planned for a later campaign slice.")


@app.post("/api/menu/prices")
async def update_menu_prices():
    raise HTTPException(status_code=409, detail="Menu pricing is planned for a later campaign slice.")


@app.get("/api/day/{day_id}/summary")
async def get_day_summary(day_id: str):
    if controller.campaign.current_day.day_id == day_id and controller.campaign.current_day.summary:
        return {"summary": controller.campaign.current_day.summary}
    for entry in controller.campaign.day_summaries:
        if entry["day_id"] == day_id:
            return {"summary": entry.get("summary", entry)}
    raise HTTPException(status_code=404, detail="Day summary not found.")


@app.get("/api/day/{day_id}/snapshot")
async def get_day_snapshot(day_id: str):
    if controller.campaign.current_day.day_id == day_id and controller.campaign.current_day.final_snapshot:
        return {"snapshot": controller.campaign.current_day.final_snapshot}
    day_dir = controller.campaign.campaign_dir / "days" / day_id / "final_snapshot.json"
    if day_dir.exists():
        return {"snapshot": json.loads(day_dir.read_text(encoding="utf-8"))}
    raise HTTPException(status_code=404, detail="Day snapshot not found.")


@app.get("/api/day/{day_id}/events")
async def get_day_events(day_id: str, after: int = 0, limit: int = 100):
    if controller.campaign.current_day.day_id == day_id:
        return controller.get_events(after_index=after, limit=limit, day_id=day_id)
    day_events_path = controller.campaign.campaign_dir / "days" / day_id / "events.jsonl"
    if not day_events_path.exists():
        raise HTTPException(status_code=404, detail="Day events not found.")
    events = [
        json.loads(line)
        for line in day_events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = events[after : after + max(1, limit)]
    return {"events": selected, "next_cursor": after + len(selected)}


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
