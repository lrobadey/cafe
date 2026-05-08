"""Durable per-run reports for cafe simulations."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional


REPORT_ROOT = Path(__file__).resolve().parent.parent / "runs" / "reports"


class RunReporter:
    """Append-only writer for one simulation run."""

    def __init__(
        self,
        report_root: Optional[Path] = None,
        *,
        campaign_id: Optional[str] = None,
        day_id: Optional[str] = None,
        day_index: Optional[int] = None,
    ):
        self.run_id = uuid.uuid4().hex[:8]
        self.started_at = time.time()
        self._seq = 0
        self._lock = Lock()
        self._closed = False
        self.campaign_id = campaign_id
        self.day_id = day_id
        self.day_index = day_index

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = Path(report_root) if report_root else REPORT_ROOT
        self.report_dir = root / f"{timestamp}-{self.run_id}"
        self.report_dir.mkdir(parents=True, exist_ok=False)
        self.events_path = self.report_dir / "events.jsonl"
        self.summary_path = self.report_dir / "summary.json"

    def event(self, source: str, event_type: str, payload: Optional[dict] = None) -> dict:
        """Append one ordered event and return the written entry."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot write to a closed run report.")
            self._seq += 1
            now = time.time()
            entry = {
                "seq": self._seq,
                "time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "elapsed_seconds": round(now - self.started_at, 3),
                "source": source,
                "event_type": event_type,
                "payload": payload or {},
            }
            if self.campaign_id:
                entry["campaign_id"] = self.campaign_id
            if self.day_id:
                entry["day_id"] = self.day_id
            if self.day_index is not None:
                entry["day_index"] = self.day_index
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            return entry

    def close(
        self,
        final_status: str,
        summary: Optional[dict] = None,
        *,
        final_snapshot: Optional[dict] = None,
        alerts: Optional[list[dict]] = None,
    ) -> Path:
        """Write the final summary for this run."""
        with self._lock:
            if self._closed:
                return self.summary_path
            finished_at = time.time()
            payload = {
                "run_id": self.run_id,
                "status": final_status,
                "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(),
                "finished_at": datetime.fromtimestamp(finished_at, timezone.utc).isoformat(),
                "duration_seconds": round(finished_at - self.started_at, 3),
                "event_count": self._seq,
                "report_dir": str(self.report_dir),
                "events_path": str(self.events_path),
                "summary": summary or {},
                "final_snapshot": final_snapshot,
                "alerts": alerts or [],
            }
            if self.campaign_id:
                payload["campaign_id"] = self.campaign_id
            if self.day_id:
                payload["day_id"] = self.day_id
            if self.day_index is not None:
                payload["day_index"] = self.day_index
            self.summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self._closed = True
            return self.summary_path
