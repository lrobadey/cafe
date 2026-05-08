"""Campaign and day state for the multi-day cafe layer."""

import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import MENU, SUPPLIES


CAMPAIGN_ROOT = Path(__file__).resolve().parent.parent / "runs" / "campaigns"
OPEN_MINUTE = 8 * 60
CLOSE_MINUTE = 16 * 60
STARTING_MONEY = 200.0
STARTING_REPUTATION = 50
RESTOCK_UNIT_COST = 1.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_sim_time(minute: int) -> str:
    hour = max(0, minute) // 60
    mins = max(0, minute) % 60
    return f"{hour:02d}:{mins:02d}"


def date_label_for_day(day_index: int) -> str:
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return f"{weekdays[(day_index - 1) % len(weekdays)]}, Spring {day_index}"


def copy_supplies(supplies: dict) -> dict:
    return {supply_id: dict(supply) for supply_id, supply in supplies.items()}


def copy_menu(menu: dict) -> dict:
    return {item_id: dict(item) for item_id, item in menu.items()}


@dataclass
class DayState:
    """One durable cafe day inside a campaign."""

    day_index: int
    starting_supplies: dict
    starting_cash: float
    day_id: str = ""
    date_label: str = ""
    phase: str = "planning"
    opening_plan: dict = field(default_factory=dict)
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    settled_at: Optional[str] = None
    summary: Optional[dict] = None
    final_snapshot: Optional[dict] = None
    events: list[dict] = field(default_factory=list)
    report_paths: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.day_id:
            self.day_id = f"day_{self.day_index:03d}"
        if not self.date_label:
            self.date_label = date_label_for_day(self.day_index)
        self.starting_supplies = copy_supplies(self.starting_supplies)
        self.starting_cash = round(float(self.starting_cash), 2)

    def to_snapshot(self) -> dict:
        return {
            "day_id": self.day_id,
            "day_index": self.day_index,
            "date_label": self.date_label,
            "phase": self.phase,
            "starting_cash": self.starting_cash,
            "starting_supplies": copy_supplies(self.starting_supplies),
            "opening_plan": deepcopy(self.opening_plan),
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "settled_at": self.settled_at,
            "summary": self.summary,
            "report_paths": dict(self.report_paths),
        }

    def to_timeline_entry(self, active: bool = False) -> dict:
        summary = self.summary or {}
        return {
            "day_id": self.day_id,
            "day_index": self.day_index,
            "date_label": self.date_label,
            "phase": self.phase,
            "active": active,
            "profit": summary.get("profit"),
            "revenue": summary.get("revenue"),
            "customers_served": summary.get("customers_served"),
            "satisfaction": summary.get("satisfaction"),
            "warnings": len(summary.get("alerts", [])) if summary else 0,
        }


class CampaignState:
    """Long-lived save-game state above the existing live shift."""

    def __init__(
        self,
        *,
        campaign_id: str,
        cafe_name: str,
        created_at: str,
        campaign_root: Optional[Path] = None,
        current_day_index: int = 1,
        money: float = STARTING_MONEY,
        cumulative_revenue: float = 0.0,
        cumulative_costs: float = 0.0,
        reputation: int = STARTING_REPUTATION,
        persistent_supplies: Optional[dict] = None,
        menu_state: Optional[dict] = None,
        day_summaries: Optional[list[dict]] = None,
        current_day: Optional[DayState] = None,
        status: str = "planning",
    ):
        self.campaign_id = campaign_id
        self.cafe_name = cafe_name
        self.created_at = created_at
        self.current_day_index = current_day_index
        self.money = round(float(money), 2)
        self.cumulative_revenue = round(float(cumulative_revenue), 2)
        self.cumulative_costs = round(float(cumulative_costs), 2)
        self.reputation = int(reputation)
        self.persistent_supplies = copy_supplies(persistent_supplies or SUPPLIES)
        self.menu_state = copy_menu(menu_state or MENU)
        self.day_summaries = list(day_summaries or [])
        self.status = status
        self.campaign_root = Path(campaign_root) if campaign_root else CAMPAIGN_ROOT
        self.current_day = current_day or DayState(
            day_index=current_day_index,
            starting_supplies=self.persistent_supplies,
            starting_cash=self.money,
        )

    @classmethod
    def new_campaign(cls, settings: Optional[dict] = None, campaign_root: Optional[Path] = None) -> "CampaignState":
        settings = settings or {}
        timestamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
        campaign = cls(
            campaign_id=settings.get("campaign_id") or f"campaign_{timestamp}_{uuid.uuid4().hex[:4]}",
            cafe_name=settings.get("cafe_name") or "CafeLab",
            created_at=utc_now(),
            campaign_root=campaign_root,
            money=settings.get("money", STARTING_MONEY),
            reputation=settings.get("reputation", STARTING_REPUTATION),
        )
        campaign.save()
        return campaign

    @property
    def campaign_dir(self) -> Path:
        return self.campaign_root / self.campaign_id

    def current_day_dir(self) -> Path:
        return self.campaign_dir / "days" / self.current_day.day_id

    def begin_day(self, plan: Optional[dict] = None):
        if self.current_day.phase == "settled":
            raise ValueError("Advance to the next day before starting service again.")
        self.current_day.phase = "open"
        self.current_day.opened_at = utc_now()
        if plan:
            self.current_day.opening_plan.update(plan)
        self.status = "open"
        self.save()

    def begin_closing(self):
        if self.current_day.phase == "settled":
            return
        self.current_day.phase = "closing"
        self.current_day.closed_at = utc_now()
        self.status = "closing"
        self.save()

    def settle_current_day(
        self,
        *,
        metrics: dict,
        closeout: dict,
        final_snapshot: dict,
        events: list[dict],
        alerts: list[dict],
        report_paths: Optional[dict] = None,
    ) -> dict:
        if self.current_day.phase == "settled" and self.current_day.summary:
            return self.current_day.summary

        revenue = round(float(metrics.get("revenue") or 0), 2)
        supply_costs = round(float(self.current_day.opening_plan.get("restock_costs") or 0), 2)
        profit = round(revenue - supply_costs, 2)
        not_delivered = int(metrics.get("orders_not_delivered") or 0)
        delivered = int(metrics.get("orders_delivered") or 0)
        satisfaction = max(0, min(100, 70 + (delivered * 4) - (not_delivered * 8)))
        reputation_delta = max(-5, min(5, round((satisfaction - 70) / 10)))
        final_supplies = copy_supplies(metrics.get("final_supplies") or self.persistent_supplies)

        summary = {
            "day_id": self.current_day.day_id,
            "day_index": self.current_day.day_index,
            "date_label": self.current_day.date_label,
            "revenue": revenue,
            "supply_costs": supply_costs,
            "waste": 0.0,
            "profit": profit,
            "customers_served": delivered,
            "customers_lost": not_delivered,
            "orders_created": int(metrics.get("orders_created") or 0),
            "average_wait_seconds": metrics.get("average_wait_seconds"),
            "abandoned_orders": int(metrics.get("orders_abandoned") or 0),
            "stale_orders": int(metrics.get("orders_stale") or 0),
            "failed_orders": int(metrics.get("orders_failed") or 0),
            "satisfaction": satisfaction,
            "reputation_delta": reputation_delta,
            "staff_notes": self._build_staff_notes(metrics),
            "tomorrow_warnings": self._build_tomorrow_warnings(metrics, alerts),
            "alerts": list(alerts or []),
            "closeout": deepcopy(closeout or {}),
            "final_supplies": final_supplies,
        }

        self.money = round(self.money + revenue, 2)
        self.cumulative_revenue = round(self.cumulative_revenue + revenue, 2)
        self.reputation = max(0, min(100, self.reputation + reputation_delta))
        self.persistent_supplies = final_supplies
        self.current_day.phase = "settled"
        self.current_day.settled_at = utc_now()
        self.current_day.summary = summary
        self.current_day.final_snapshot = deepcopy(final_snapshot)
        self.current_day.events = [dict(event) for event in events]
        self.current_day.report_paths.update(report_paths or {})
        self.status = "between_days"

        self._replace_day_summary(summary)
        self._write_current_day_files()
        self.save()
        return summary

    def advance_to_next_day(self, plan: Optional[dict] = None) -> DayState:
        if self.current_day.phase != "settled":
            raise ValueError("Settle the current day before advancing.")
        self.current_day_index += 1
        self.current_day = DayState(
            day_index=self.current_day_index,
            starting_supplies=self.persistent_supplies,
            starting_cash=self.money,
            opening_plan=plan or {},
        )
        self.status = "planning"
        self.save()
        return self.current_day

    def update_menu_availability(self, item_id: str, available: bool) -> bool:
        item = self.menu_state.get(item_id)
        if not item:
            return False
        item["available"] = bool(available)
        self.save()
        return True

    def restock(self, supply_id: str, quantity: int) -> dict:
        quantity = max(0, int(quantity))
        supply = self.persistent_supplies.get(supply_id)
        if not supply or quantity == 0:
            return {"ok": False, "cost": 0.0}
        cost = round(quantity * RESTOCK_UNIT_COST, 2)
        supply["quantity"] = int(supply.get("quantity", 0)) + quantity
        self.money = round(self.money - cost, 2)
        self.cumulative_costs = round(self.cumulative_costs + cost, 2)
        self.current_day.opening_plan["restock_costs"] = round(
            float(self.current_day.opening_plan.get("restock_costs") or 0) + cost,
            2,
        )
        restocks = self.current_day.opening_plan.setdefault("restocks", {})
        restocks[supply_id] = int(restocks.get(supply_id, 0)) + quantity
        self.current_day.starting_supplies = copy_supplies(self.persistent_supplies)
        self.current_day.starting_cash = self.money
        self.save()
        return {"ok": True, "cost": cost, "supply": dict(supply)}

    def campaign_snapshot(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "cafe_name": self.cafe_name,
            "status": self.status,
            "current_day_index": self.current_day_index,
            "money": self.money,
            "reputation": self.reputation,
            "days_completed": len(self.day_summaries),
            "cumulative_revenue": self.cumulative_revenue,
            "cumulative_costs": self.cumulative_costs,
            "active_day_id": self.current_day.day_id,
        }

    def calendar_snapshot(self, *, elapsed_seconds: int = 0, sim_duration: int = 1) -> dict:
        phase = self.current_day.phase
        if phase == "open":
            ratio = min(1.0, max(0.0, elapsed_seconds / max(1, sim_duration)))
            current_minute = OPEN_MINUTE + round((CLOSE_MINUTE - OPEN_MINUTE) * ratio)
        elif phase == "settled":
            current_minute = CLOSE_MINUTE
        else:
            current_minute = OPEN_MINUTE
        return {
            "campaign_id": self.campaign_id,
            "day_id": self.current_day.day_id,
            "day_index": self.current_day.day_index,
            "date_label": self.current_day.date_label,
            "phase": phase,
            "sim_current_time": format_sim_time(current_minute),
            "sim_open_time": format_sim_time(OPEN_MINUTE),
            "sim_close_time": format_sim_time(CLOSE_MINUTE),
            "time_scale": round((CLOSE_MINUTE - OPEN_MINUTE) / max(1, sim_duration), 2),
        }

    def history_snapshot(self) -> dict:
        timeline = [
            {
                **summary,
                "active": False,
            }
            for summary in self.day_summaries[-6:]
        ]
        if not any(entry["day_id"] == self.current_day.day_id for entry in timeline):
            timeline.append(self.current_day.to_timeline_entry(active=True))
        else:
            for entry in timeline:
                entry["active"] = entry["day_id"] == self.current_day.day_id
        return {
            "recent_days": list(self.day_summaries[-5:]),
            "timeline": timeline,
        }

    def to_save_payload(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "cafe_name": self.cafe_name,
            "created_at": self.created_at,
            "current_day_index": self.current_day_index,
            "money": self.money,
            "cumulative_revenue": self.cumulative_revenue,
            "cumulative_costs": self.cumulative_costs,
            "reputation": self.reputation,
            "persistent_supplies": self.persistent_supplies,
            "menu_state": self.menu_state,
            "day_summaries": self.day_summaries,
            "current_day": self.current_day.to_snapshot(),
            "status": self.status,
        }

    def save(self):
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        (self.campaign_dir / "campaign.json").write_text(
            json.dumps(self.to_save_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.campaign_dir / "campaign_summary.json").write_text(
            json.dumps(
                {
                    "campaign": self.campaign_snapshot(),
                    "history": self.history_snapshot(),
                    "updated_at": utc_now(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _replace_day_summary(self, summary: dict):
        self.day_summaries = [
            existing for existing in self.day_summaries if existing["day_id"] != summary["day_id"]
        ]
        self.day_summaries.append(
            {
                "day_id": summary["day_id"],
                "day_index": summary["day_index"],
                "date_label": summary["date_label"],
                "phase": "settled",
                "profit": summary["profit"],
                "revenue": summary["revenue"],
                "customers_served": summary["customers_served"],
                "customers_lost": summary["customers_lost"],
                "satisfaction": summary["satisfaction"],
                "warnings": len(summary.get("alerts", [])),
                "summary": summary,
            }
        )

    def _write_current_day_files(self):
        day_dir = self.current_day_dir()
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "plan.json").write_text(
            json.dumps(self.current_day.opening_plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (day_dir / "summary.json").write_text(
            json.dumps(self.current_day.summary or {}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (day_dir / "final_snapshot.json").write_text(
            json.dumps(self.current_day.final_snapshot or {}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with (day_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
            for event in self.current_day.events:
                fh.write(json.dumps(event, sort_keys=True) + "\n")

    def _build_staff_notes(self, metrics: dict) -> list[str]:
        completed = metrics.get("orders_completed_by_barista") or {}
        if not completed:
            return ["No staff completions were recorded today."]
        return [f"{staff_id} completed {count} order(s)." for staff_id, count in completed.items()]

    def _build_tomorrow_warnings(self, metrics: dict, alerts: list[dict]) -> list[str]:
        warnings = [alert.get("message") for alert in alerts or [] if alert.get("message")]
        sold_out = metrics.get("sold_out_supplies") or {}
        for supply in sold_out.values():
            warnings.append(f"{supply.get('name', 'Supply')} ended the day out of stock.")
        return warnings[:6]
