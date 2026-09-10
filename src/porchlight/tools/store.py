"""The community report store — Porchlight's institutional memory.

This is the coalition's own prior reports, scoped to one community. It is the
most valuable enrichment source in the system and the only one that no
commercial product can provide, because it is made of what this neighbourhood
reported.

Backed by a JSON file. In every mode, including a deployed container — see
``porchlight.memory`` for why ``AGENTCORE_MEMORY_ID`` does not currently change
that, and the README's limitations section for what it would take to.

The consequence worth knowing: in a container with no volume, this file dies
with the container, and the coalition's institutional memory dies with it. That
is survivable for a demo and not for a deployment.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from strands import tool

from ..config import REPO_ROOT, settings

_STORE_PATH = REPO_ROOT / ".porchlight_store.json"
_LOCK = threading.Lock()


class ReportStore:
    """Append-only record of processed reports, keyed by community."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _STORE_PATH
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    def add(self, community_id: str, record: dict[str, Any]) -> None:
        with _LOCK:
            self._data.setdefault(community_id, []).append(record)
            self._flush()

    def all(self, community_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(community_id, []))

    def recent(self, community_id: str, days: int) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        for r in self.all(community_id):
            try:
                ts = datetime.fromisoformat(str(r["received_at"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if ts >= cutoff:
                out.append(r)
        return out

    def clear(self, community_id: str | None = None) -> None:
        with _LOCK:
            if community_id:
                self._data.pop(community_id, None)
            else:
                self._data = {}
            self._flush()


_STORE: ReportStore | None = None


def get_store() -> ReportStore:
    global _STORE
    if _STORE is None:
        _STORE = ReportStore()
    return _STORE


@tool
def lookup_prior_reports(script_fingerprint: str, indicators: list[str]) -> dict:
    """Search this coalition's own previous reports for overlap with the current one.

    This is usually the strongest corroboration available: another resident,
    unconnected to this one, describing the same crew.

    Args:
        script_fingerprint: The canonical script label produced by intake.
        indicators: Hard indicator strings (phone numbers, UPI ids, wallets, URLs).

    Returns:
        A dict with keys: matched_report_ids, matched_on, distinct_reporters, detail.
    """
    s = settings()
    store = get_store()
    wanted = {i.strip().lower() for i in indicators if i and i.strip()}
    matched: list[str] = []
    matched_on: set[str] = set()
    reporters: set[str] = set()

    for rec in store.recent(s.community_id, s.campaign_window_days):
        rec_inds = {str(i).lower() for i in rec.get("indicator_keys", [])}
        overlap = wanted & rec_inds
        same_script = bool(script_fingerprint) and rec.get("script_fingerprint") == script_fingerprint
        if overlap or same_script:
            matched.append(rec["report_id"])
            matched_on |= overlap
            if same_script:
                matched_on.add(f"script:{script_fingerprint}")
            reporters.add(rec.get("reporter_pseudonym", rec["report_id"]))

    return {
        "matched_report_ids": matched,
        "matched_on": sorted(matched_on),
        "distinct_reporters": len(reporters),
        "detail": (
            f"{len(matched)} prior report(s) from {len(reporters)} distinct reporter(s) "
            f"in the last {s.campaign_window_days} days"
            if matched else "no prior reports in this community share these indicators"
        ),
    }


def record_processed(community_id: str, record: dict[str, Any]) -> None:
    get_store().add(community_id, record)


def iter_records(community_id: str, days: int | None = None) -> Iterable[dict[str, Any]]:
    store = get_store()
    return store.recent(community_id, days) if days else store.all(community_id)
