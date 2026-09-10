"""SQLite persistence.

Replaces what used to be module-global dicts and a JSON file. The change is not
tidiness: the old arrangement lost the queue on restart, could not be written to
by a background worker while the dashboard read from it, and had no transaction
around "record the approval and mark the decision approved", which is exactly the
pair that must not half-happen.

Design notes:

* **One connection, one lock.** The workload is a single-process server plus one
  worker thread. A connection pool would add failure modes to solve a contention
  problem this does not have. WAL is on so a reader is never blocked by the
  writer.
* **`INSERT OR IGNORE` on natural keys** rather than SELECT-then-INSERT. The
  latter is a race even under a lock the moment there is a second writer, and the
  idempotency guarantee is the point of the ingestion path.
* **Times are stored as ISO-8601 UTC strings.** SQLite has no datetime type;
  storing text that sorts correctly and round-trips through
  ``datetime.fromisoformat`` beats storing epoch floats nobody can read in a
  ``sqlite3`` shell at 2am.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import REPO_ROOT, settings
from .domain import (
    Approval,
    AuditEvent,
    Campaign,
    CampaignEvidence,
    Case,
    CaseState,
    Decision,
    DecisionState,
    Indicator,
    Job,
    JobState,
    OutboxMessage,
    ReportState,
    utcnow,
)

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS reports (
    report_id           TEXT PRIMARY KEY,
    community_id        TEXT NOT NULL,
    received_at         TEXT NOT NULL,
    channel             TEXT NOT NULL,
    raw_content         TEXT NOT NULL,
    volunteer_note      TEXT NOT NULL DEFAULT '',
    reporter_pseudonym  TEXT NOT NULL DEFAULT 'resident',
    reporter_area       TEXT NOT NULL DEFAULT '',
    content_hash        TEXT NOT NULL DEFAULT '',
    state               TEXT NOT NULL DEFAULT 'queued',
    duplicate_of        TEXT,
    case_id             TEXT,
    script_fingerprint  TEXT NOT NULL DEFAULT '',
    urgency             TEXT NOT NULL DEFAULT 'green',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_community ON reports(community_id, received_at);
CREATE INDEX IF NOT EXISTS ix_reports_dedup ON reports(community_id, reporter_pseudonym, content_hash);
CREATE INDEX IF NOT EXISTS ix_reports_case ON reports(case_id);

CREATE TABLE IF NOT EXISTS indicators (
    report_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    normalised  TEXT NOT NULL,
    PRIMARY KEY (report_id, kind, normalised)
);
CREATE INDEX IF NOT EXISTS ix_indicators_key ON indicators(kind, normalised);

CREATE TABLE IF NOT EXISTS cases (
    case_id                TEXT PRIMARY KEY,
    community_id           TEXT NOT NULL,
    opened_at              TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    state                  TEXT NOT NULL,
    urgency                TEXT NOT NULL DEFAULT 'green',
    hours_to_irreversible  REAL,
    script_fingerprint     TEXT NOT NULL DEFAULT '',
    impersonated_entity    TEXT NOT NULL DEFAULT '',
    money_rail             TEXT NOT NULL DEFAULT 'none',
    reporter_pseudonym     TEXT NOT NULL DEFAULT '',
    reporter_area          TEXT NOT NULL DEFAULT '',
    campaign_id            TEXT NOT NULL DEFAULT '',
    summary                TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_cases_community ON cases(community_id, updated_at);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id             TEXT PRIMARY KEY,
    community_id            TEXT NOT NULL,
    label                   TEXT NOT NULL DEFAULT '',
    first_seen              TEXT NOT NULL,
    last_seen               TEXT NOT NULL,
    confidence              REAL NOT NULL DEFAULT 0,
    evidence_json           TEXT NOT NULL DEFAULT '{}',
    report_ids_json         TEXT NOT NULL DEFAULT '[]',
    escalated_at            TEXT,
    escalated_by_report_id  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id   TEXT PRIMARY KEY,
    community_id  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    action        TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    audience      TEXT NOT NULL,
    rationale     TEXT NOT NULL DEFAULT '',
    case_id       TEXT NOT NULL DEFAULT '',
    campaign_id   TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolved_by   TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_decisions_state ON decisions(community_id, state, created_at);
-- One pending decision per (campaign, kind). This is the notification-dedup
-- guarantee expressed as a constraint rather than as a check the caller might
-- forget: re-running correlation cannot produce a second identical ask.
CREATE UNIQUE INDEX IF NOT EXISTS ux_decisions_pending
    ON decisions(community_id, campaign_id, kind)
    WHERE state = 'pending' AND campaign_id != '';

CREATE TABLE IF NOT EXISTS approvals (
    token         TEXT PRIMARY KEY,
    approver      TEXT NOT NULL,
    role          TEXT NOT NULL,
    action        TEXT NOT NULL,
    audience      TEXT NOT NULL,
    message_hash  TEXT NOT NULL,
    case_id       TEXT NOT NULL DEFAULT '',
    decision_id   TEXT NOT NULL DEFAULT '',
    issued_at     TEXT NOT NULL,
    expires_at    TEXT,
    spent_at      TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT '',
    origin       TEXT NOT NULL DEFAULT 'agent',
    action       TEXT NOT NULL DEFAULT '',
    effect       TEXT NOT NULL DEFAULT '',
    policy_id    TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    resource     TEXT NOT NULL DEFAULT '',
    report_id    TEXT NOT NULL DEFAULT '',
    case_id      TEXT NOT NULL DEFAULT '',
    campaign_id  TEXT NOT NULL DEFAULT '',
    decision_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_audit_effect ON audit_events(effect, event_id);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT NOT NULL,
    channel       TEXT NOT NULL,
    audience      TEXT NOT NULL,
    body          TEXT NOT NULL,
    delivered_at  TEXT NOT NULL,
    approver      TEXT NOT NULL DEFAULT '',
    sandbox       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    idempotency_key  TEXT NOT NULL UNIQUE,
    state            TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state, job_id);

CREATE TABLE IF NOT EXISTS broadcast_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT ''
);
"""

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_PATH: Optional[Path] = None


def db_path() -> Path:
    return Path(settings().db_path) if settings().db_path else REPO_ROOT / "porchlight.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (once) and return the process connection.

    A bare ``connect()`` means "give me the current connection", and will never
    switch databases. Only an explicit path does that.

    This distinction is not pedantry — it was a live bug. Every internal caller
    goes through ``tx()``, which calls ``connect()`` with no argument. When that
    re-resolved the default path, the first write after a test opened a temporary
    database silently reconnected to the repo-root file and wrote there instead:
    tests polluted each other and would have scribbled on real demo state.
    """
    global _CONN, _PATH
    with _LOCK:
        if path is None:
            if _CONN is not None:
                return _CONN
            target = db_path()
        else:
            target = Path(path)
            if _CONN is not None and _PATH == target:
                return _CONN
        if _CONN is not None:
            _CONN.close()
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        _CONN, _PATH = conn, target
        return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """A transaction. Commits on clean exit, rolls back on any exception."""
    conn = connect()
    with _LOCK:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def reset(path: Optional[Path] = None) -> None:
    """Drop every row. Used by the demo reset button and by test fixtures."""
    conn = connect(path)
    with _LOCK:
        for table in ("reports", "indicators", "cases", "campaigns", "decisions",
                      "approvals", "audit_events", "outbox", "jobs", "broadcast_log"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


def close() -> None:
    global _CONN, _PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN, _PATH = None, None


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------
def _dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _dt(value)
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
def insert_report(report: Any, content_hash: str, state: ReportState = ReportState.QUEUED,
                  duplicate_of: str = "") -> bool:
    """Insert a report. Returns False if the id was already present."""
    with tx() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO reports
               (report_id, community_id, received_at, channel, raw_content, volunteer_note,
                reporter_pseudonym, reporter_area, content_hash, state, duplicate_of, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report.report_id, report.community_id, _iso(report.received_at),
             report.channel.value if hasattr(report.channel, "value") else str(report.channel),
             report.raw_content, report.volunteer_note, report.reporter_pseudonym,
             report.reporter_area, content_hash, state.value, duplicate_of or None,
             utcnow().isoformat()),
        )
        return cur.rowcount > 0


def find_duplicate(community_id: str, reporter: str, content_hash: str,
                   within_days: int = 14) -> Optional[str]:
    """An earlier report of the same substance by the same resident, if any.

    Scoped to one reporter on purpose. Two residents describing the same call is
    the signal this whole system exists to find — collapsing that as a duplicate
    would delete the product.
    """
    cutoff = (utcnow() - timedelta(days=within_days)).isoformat()
    conn = connect()
    row = conn.execute(
        """SELECT report_id FROM reports
           WHERE community_id=? AND reporter_pseudonym=? AND content_hash=?
             AND received_at >= ? AND state != 'duplicate'
           ORDER BY received_at LIMIT 1""",
        (community_id, reporter, content_hash, cutoff),
    ).fetchone()
    return row["report_id"] if row else None


def update_report(report_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"state", "duplicate_of", "case_id", "script_fingerprint", "urgency"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"update_report: unknown column {key!r}")
        sets.append(f"{key}=?")
        values.append(value.value if hasattr(value, "value") else value)
    values.append(report_id)
    with tx() as conn:
        conn.execute(f"UPDATE reports SET {', '.join(sets)} WHERE report_id=?", values)


def get_report(report_id: str) -> Optional[dict[str, Any]]:
    row = connect().execute("SELECT * FROM reports WHERE report_id=?", (report_id,)).fetchone()
    return dict(row) if row else None


def count_reports(community_id: str, state: Optional[ReportState] = None) -> int:
    conn = connect()
    if state:
        row = conn.execute("SELECT COUNT(*) c FROM reports WHERE community_id=? AND state=?",
                           (community_id, state.value)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) c FROM reports WHERE community_id=?",
                           (community_id,)).fetchone()
    return int(row["c"])


# --------------------------------------------------------------------------
# Indicators — the correlation substrate
# --------------------------------------------------------------------------
def put_indicators(indicators: list[Indicator]) -> None:
    if not indicators:
        return
    with tx() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO indicators(report_id, kind, value, normalised) VALUES (?,?,?,?)",
            [(i.report_id, i.kind.value, i.value, i.normalised) for i in indicators],
        )


def correlation_records(community_id: str, days: int) -> list[dict[str, Any]]:
    """The rows correlation consumes: one per non-duplicate report in the window.

    Duplicates are excluded here rather than filtered downstream. A resident's
    third call about the same incident must not add a third member to a cluster,
    or "6 residents affected" becomes a number the coordinator cannot trust.
    """
    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    conn = connect()
    rows = conn.execute(
        """SELECT r.report_id, r.reporter_pseudonym, r.reporter_area, r.received_at,
                  r.script_fingerprint, r.urgency
           FROM reports r
           WHERE r.community_id=? AND r.received_at >= ? AND r.state != 'duplicate'
           ORDER BY r.received_at""",
        (community_id, cutoff),
    ).fetchall()
    out = []
    for row in rows:
        keys = conn.execute(
            "SELECT kind, normalised FROM indicators WHERE report_id=?", (row["report_id"],)
        ).fetchall()
        rec = dict(row)
        rec["indicator_keys"] = sorted(f"{k['kind']}:{k['normalised']}" for k in keys)
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------
def upsert_case(case: Case) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO cases (case_id, community_id, opened_at, updated_at, state, urgency,
                                  hours_to_irreversible, script_fingerprint, impersonated_entity,
                                  money_rail, reporter_pseudonym, reporter_area, campaign_id, summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(case_id) DO UPDATE SET
                 updated_at=excluded.updated_at, state=excluded.state, urgency=excluded.urgency,
                 hours_to_irreversible=excluded.hours_to_irreversible,
                 script_fingerprint=excluded.script_fingerprint,
                 impersonated_entity=excluded.impersonated_entity, money_rail=excluded.money_rail,
                 campaign_id=excluded.campaign_id, summary=excluded.summary""",
            (case.case_id, case.community_id, _iso(case.opened_at), _iso(case.updated_at),
             case.state.value, case.urgency, case.hours_to_irreversible, case.script_fingerprint,
             case.impersonated_entity, case.money_rail, case.reporter_pseudonym,
             case.reporter_area, case.campaign_id, case.summary),
        )
        for rid in case.report_ids:
            conn.execute("UPDATE reports SET case_id=? WHERE report_id=?", (case.case_id, rid))


def get_case(case_id: str) -> Optional[Case]:
    conn = connect()
    row = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not row:
        return None
    rids = [r["report_id"] for r in conn.execute(
        "SELECT report_id FROM reports WHERE case_id=? ORDER BY received_at", (case_id,))]
    return _case_from_row(row, rids)


def _case_from_row(row: sqlite3.Row, report_ids: list[str]) -> Case:
    return Case(
        case_id=row["case_id"], community_id=row["community_id"],
        opened_at=_dt(row["opened_at"]) or utcnow(), updated_at=_dt(row["updated_at"]) or utcnow(),
        state=CaseState(row["state"]), urgency=row["urgency"],
        hours_to_irreversible=row["hours_to_irreversible"],
        script_fingerprint=row["script_fingerprint"],
        impersonated_entity=row["impersonated_entity"], money_rail=row["money_rail"],
        reporter_pseudonym=row["reporter_pseudonym"], reporter_area=row["reporter_area"],
        campaign_id=row["campaign_id"], summary=row["summary"], report_ids=report_ids,
    )


def list_cases(community_id: str, limit: int = 200) -> list[Case]:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM cases WHERE community_id=? ORDER BY updated_at DESC LIMIT ?",
        (community_id, limit)).fetchall()
    out = []
    for row in rows:
        rids = [r["report_id"] for r in conn.execute(
            "SELECT report_id FROM reports WHERE case_id=? ORDER BY received_at",
            (row["case_id"],))]
        out.append(_case_from_row(row, rids))
    return out


def find_case_for_merge(community_id: str, reporter: str, fingerprint: str,
                        within_days: int = 14) -> Optional[str]:
    """An open case from the same resident about the same script.

    This is the "merged two into an existing case" behaviour: a resident calling
    back with more detail extends their case rather than opening a second one.
    """
    if not fingerprint or fingerprint == "unclassified script":
        return None
    cutoff = (utcnow() - timedelta(days=within_days)).isoformat()
    row = connect().execute(
        """SELECT case_id FROM cases
           WHERE community_id=? AND reporter_pseudonym=? AND script_fingerprint=?
             AND state NOT IN ('closed') AND updated_at >= ?
           ORDER BY updated_at DESC LIMIT 1""",
        (community_id, reporter, fingerprint, cutoff)).fetchone()
    return row["case_id"] if row else None


# --------------------------------------------------------------------------
# Campaigns
# --------------------------------------------------------------------------
def upsert_campaign(campaign: Campaign) -> bool:
    """Store a campaign. Returns True if this call is what first escalated it."""
    conn = connect()
    existing = conn.execute("SELECT escalated_at FROM campaigns WHERE campaign_id=?",
                            (campaign.campaign_id,)).fetchone()
    newly = existing is None
    with tx() as c:
        c.execute(
            """INSERT INTO campaigns (campaign_id, community_id, label, first_seen, last_seen,
                                      confidence, evidence_json, report_ids_json,
                                      escalated_at, escalated_by_report_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id) DO UPDATE SET
                 label=excluded.label, last_seen=excluded.last_seen,
                 confidence=excluded.confidence, evidence_json=excluded.evidence_json,
                 report_ids_json=excluded.report_ids_json""",
            (campaign.campaign_id, campaign.community_id, campaign.label,
             _iso(campaign.first_seen), _iso(campaign.last_seen), campaign.confidence,
             json.dumps(campaign.evidence.model_dump(mode="json")),
             json.dumps(campaign.report_ids),
             _iso(campaign.escalated_at) or utcnow().isoformat(),
             campaign.escalated_by_report_id),
        )
    return newly


def get_campaign(campaign_id: str) -> Optional[Campaign]:
    row = connect().execute("SELECT * FROM campaigns WHERE campaign_id=?",
                            (campaign_id,)).fetchone()
    return _campaign_from_row(row) if row else None


def _campaign_from_row(row: sqlite3.Row) -> Campaign:
    return Campaign(
        campaign_id=row["campaign_id"], community_id=row["community_id"], label=row["label"],
        first_seen=_dt(row["first_seen"]) or utcnow(), last_seen=_dt(row["last_seen"]) or utcnow(),
        confidence=row["confidence"],
        evidence=CampaignEvidence(**json.loads(row["evidence_json"] or "{}")),
        report_ids=json.loads(row["report_ids_json"] or "[]"),
        escalated_at=_dt(row["escalated_at"]),
        escalated_by_report_id=row["escalated_by_report_id"],
    )


def list_campaigns(community_id: str) -> list[Campaign]:
    rows = connect().execute(
        "SELECT * FROM campaigns WHERE community_id=? ORDER BY last_seen DESC",
        (community_id,)).fetchall()
    return [_campaign_from_row(r) for r in rows]


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------
def create_decision(decision: Decision) -> bool:
    """Create a pending decision. False if an identical one is already waiting.

    The uniqueness is enforced by a partial index, so two workers racing to
    escalate the same campaign produce one item in the coordinator's inbox.
    """
    try:
        with tx() as conn:
            conn.execute(
                """INSERT INTO decisions (decision_id, community_id, kind, action, title, body,
                                          audience, rationale, case_id, campaign_id, state,
                                          created_at, evidence_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (decision.decision_id, decision.community_id, decision.kind, decision.action,
                 decision.title, decision.body, decision.audience, decision.rationale,
                 decision.case_id, decision.campaign_id, decision.state.value,
                 _iso(decision.created_at), json.dumps(decision.evidence, default=str)),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_decision(decision_id: str) -> Optional[Decision]:
    row = connect().execute("SELECT * FROM decisions WHERE decision_id=?",
                            (decision_id,)).fetchone()
    return _decision_from_row(row) if row else None


def _decision_from_row(row: sqlite3.Row) -> Decision:
    return Decision(
        decision_id=row["decision_id"], community_id=row["community_id"], kind=row["kind"],
        action=row["action"], title=row["title"], body=row["body"], audience=row["audience"],
        rationale=row["rationale"], case_id=row["case_id"], campaign_id=row["campaign_id"],
        state=DecisionState(row["state"]), created_at=_dt(row["created_at"]) or utcnow(),
        resolved_at=_dt(row["resolved_at"]), resolved_by=row["resolved_by"],
        evidence=json.loads(row["evidence_json"] or "{}"),
    )


def list_decisions(community_id: str, state: Optional[DecisionState] = None) -> list[Decision]:
    conn = connect()
    if state:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE community_id=? AND state=? ORDER BY created_at",
            (community_id, state.value)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE community_id=? ORDER BY created_at DESC",
            (community_id,)).fetchall()
    return [_decision_from_row(r) for r in rows]


def update_decision_body(decision_id: str, body: str, title: Optional[str] = None) -> None:
    """Edit a draft. Any outstanding approval stops matching, by construction."""
    with tx() as conn:
        if title is not None:
            conn.execute("UPDATE decisions SET body=?, title=? WHERE decision_id=?",
                         (body, title, decision_id))
        else:
            conn.execute("UPDATE decisions SET body=? WHERE decision_id=?", (body, decision_id))


def resolve_decision(decision_id: str, state: DecisionState, by: str) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE decisions SET state=?, resolved_at=?, resolved_by=? WHERE decision_id=?",
            (state.value, utcnow().isoformat(), by, decision_id))


# --------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------
def put_approval(approval: Approval) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO approvals (token, approver, role, action, audience, message_hash,
                                      case_id, decision_id, issued_at, expires_at, spent_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (approval.token, approval.approver, approval.role, approval.action, approval.audience,
             approval.message_hash, approval.case_id, approval.decision_id,
             _iso(approval.issued_at), _iso(approval.expires_at), _iso(approval.spent_at)),
        )


def get_approval(token: str) -> Optional[Approval]:
    row = connect().execute("SELECT * FROM approvals WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    return Approval(
        token=row["token"], approver=row["approver"], role=row["role"], action=row["action"],
        audience=row["audience"], message_hash=row["message_hash"], case_id=row["case_id"],
        decision_id=row["decision_id"], issued_at=_dt(row["issued_at"]) or utcnow(),
        expires_at=_dt(row["expires_at"]), spent_at=_dt(row["spent_at"]),
    )


def spend_approval_atomic(token: str) -> bool:
    """Mark an approval spent. Returns False if it already was.

    The UPDATE ... WHERE spent_at IS NULL is the whole point: two concurrent
    redemptions of one capability cannot both see NULL and both proceed, because
    SQLite serialises the write and the second sees zero rows affected.
    """
    with tx() as conn:
        cur = conn.execute(
            "UPDATE approvals SET spent_at=? WHERE token=? AND spent_at IS NULL",
            (utcnow().isoformat(), token))
        return cur.rowcount > 0


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
def append_audit(event: AuditEvent) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO audit_events (at, actor, origin, action, effect, policy_id, reason,
                                         resource, report_id, case_id, campaign_id, decision_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_iso(event.at), event.actor, event.origin, event.action, event.effect,
             event.policy_id, event.reason, event.resource[:200], event.report_id,
             event.case_id, event.campaign_id, event.decision_id),
        )


def list_audit(limit: int = 50, effect: Optional[str] = None) -> list[dict[str, Any]]:
    """Newest first. Filtering happens in SQL, over the whole trail.

    Filtering a page that has already been taken is why a "denied only" view can
    come back empty after a replay writes hundreds of permits.
    """
    conn = connect()
    if effect in {"permit", "forbid"}:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE effect=? ORDER BY event_id DESC LIMIT ?",
            (effect, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY event_id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_audit(effect: Optional[str] = None) -> int:
    conn = connect()
    if effect:
        row = conn.execute("SELECT COUNT(*) c FROM audit_events WHERE effect=?",
                           (effect,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) c FROM audit_events").fetchone()
    return int(row["c"])


# --------------------------------------------------------------------------
# Sandbox outbox
# --------------------------------------------------------------------------
def append_outbox(message: OutboxMessage) -> int:
    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO outbox (decision_id, channel, audience, body, delivered_at,
                                   approver, sandbox)
               VALUES (?,?,?,?,?,?,?)""",
            (message.decision_id, message.channel, message.audience, message.body,
             _iso(message.delivered_at), message.approver, 1 if message.sandbox else 0))
        return int(cur.lastrowid or 0)


def list_outbox(limit: int = 50) -> list[dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM outbox ORDER BY outbox_id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Broadcast rate-limit window (P004)
# --------------------------------------------------------------------------
def record_broadcast(audience: str = "") -> None:
    with tx() as conn:
        conn.execute("INSERT INTO broadcast_log (at, audience) VALUES (?,?)",
                     (utcnow().isoformat(), audience))


def broadcasts_since(hours: int = 24) -> int:
    cutoff = (utcnow() - timedelta(hours=hours)).isoformat()
    row = connect().execute("SELECT COUNT(*) c FROM broadcast_log WHERE at >= ?",
                            (cutoff,)).fetchone()
    return int(row["c"])


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
def enqueue(kind: str, payload: dict[str, Any], idempotency_key: str) -> bool:
    """Add a job. False if this idempotency key was already enqueued."""
    try:
        with tx() as conn:
            now = utcnow().isoformat()
            conn.execute(
                """INSERT INTO jobs (kind, payload_json, idempotency_key, state, created_at, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (kind, json.dumps(payload, default=str), idempotency_key,
                 JobState.PENDING.value, now, now))
        return True
    except sqlite3.IntegrityError:
        return False


def claim_job() -> Optional[Job]:
    """Atomically take the oldest pending job.

    Claim and read are one statement's worth of serialised work under the write
    lock, so two workers cannot claim the same job.
    """
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE state='pending' ORDER BY job_id LIMIT 1").fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? "
            "WHERE job_id=? AND state='pending'",
            (utcnow().isoformat(), row["job_id"]))
        if cur.rowcount == 0:
            return None
        return Job(job_id=row["job_id"], kind=row["kind"],
                   payload=json.loads(row["payload_json"] or "{}"),
                   idempotency_key=row["idempotency_key"], state=JobState.RUNNING,
                   attempts=row["attempts"] + 1, last_error=row["last_error"],
                   created_at=_dt(row["created_at"]) or utcnow(),
                   updated_at=utcnow())


def finish_job(job_id: int, state: JobState, error: str = "") -> None:
    with tx() as conn:
        conn.execute("UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?",
                     (state.value, error[:500], utcnow().isoformat(), job_id))


def retry_or_fail(job_id: int, attempts: int, error: str, max_attempts: int = 3) -> JobState:
    """Bounded retry. A job that keeps failing becomes visible, not invisible."""
    state = JobState.PENDING if attempts < max_attempts else JobState.FAILED
    finish_job(job_id, state, error)
    return state


def job_counts() -> dict[str, int]:
    rows = connect().execute("SELECT state, COUNT(*) c FROM jobs GROUP BY state").fetchall()
    return {r["state"]: int(r["c"]) for r in rows}


def list_failed_jobs(limit: int = 20) -> list[dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM jobs WHERE state='failed' ORDER BY job_id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]
