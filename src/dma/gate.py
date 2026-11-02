"""Step gate: every stage ends at Approve, Disapprove or Suggest changes (ARCHITECTURE section 3, FR-G1 to FR-G7).

State is never stored directly. It is rebuilt by replaying the session's append-only event log, so a restarted server
continues where the last one stopped. All file IO goes through ArtifactWriter.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel

from dma.writer import ArtifactWriter, WriterError

Decision = Literal["approve", "suggest_changes", "disapprove"]
After = Literal["redo", "reopen", "end"]
DECISIONS = ("approve", "suggest_changes", "disapprove")
AFTER = ("redo", "reopen", "end")
VERSION_STATUS = {"approve": "approved", "suggest_changes": "changes_requested", "disapprove": "disapproved"}
STAGE_KEYS = {"id", "label", "og_step", "major_gate", "template"}
# The stage whose approved version becomes the requirement document (FR-B10).
REQUIREMENTS_STAGE = "requirements"
# The stage whose approved version fixes the Bronze tables that data extraction may sample (FR-B11).
BRONZE_STAGE = "bronze_identification"
# Sampling needs a permission separate from any gate decision, given while the session is at this stage.
EXTRACTION_STAGE = "data_extraction"
PERMISSIONS = ("data_extraction",)


class GateError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class StageVersion(BaseModel):
    version: int
    status: Literal["pending", "approved", "changes_requested", "disapproved", "invalidated"]
    sha256: str
    submitted_by: str
    submitted_at: str
    bronze_tables: list[str] | None = None


class StageState(BaseModel):
    stage: str
    label: str | None = None
    og_step: str | None = None
    major_gate: str | None = None
    major_gate_number: int | None = None
    template: str | None = None
    headings: list[str] | None = None
    status: Literal["not_started", "awaiting_decision", "open", "approved"]
    approved_version: int | None
    versions: list[StageVersion]


class LogEntry(BaseModel):
    stage: str
    version: int
    decision: Decision
    note: str | None
    then: After | None
    reopen_stage: str | None
    invalidated: list[str]
    user: str
    ts: str


class PermissionEntry(BaseModel):
    event: Literal["granted", "revoked"]
    permission: str
    tables: list[str]
    user: str
    ts: str


class SessionView(BaseModel):
    session_id: str
    flow: str
    scope: str
    status: Literal["active", "complete", "ended"]
    created_by: str
    created_at: str
    current_stage: str | None
    stages: list[StageState]
    allowed_next: list[str]
    decision_log: list[LogEntry]
    permissions: list[PermissionEntry] = []
    granted_tables: list[str] | None = None
    artifacts: list[str]


class SessionSummary(BaseModel):
    session_id: str
    flow: str
    scope: str
    status: str
    current_stage: str | None
    created_at: str


@dataclass
class _Session:
    session_id: str
    flow: str
    scope: str
    stages: list[str]
    created_by: str
    created_at: str
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    approved: dict[str, int] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)
    permission_log: list[dict[str, Any]] = field(default_factory=list)
    grant: list[str] | None = None
    artifacts: list[str] = field(default_factory=list)
    ended: bool = False

    @property
    def next_stage(self) -> str | None:
        """First stage without an approved version. Approved stages always form a prefix of the flow."""
        return next((s for s in self.stages if s not in self.approved), None)

    @property
    def complete(self) -> bool:
        return not self.ended and self.next_stage is None

    def pending(self, stage: str) -> dict[str, Any] | None:
        return next((v for v in self.versions[stage] if v["status"] == "pending"), None)

    @property
    def approved_bronze(self) -> list[str] | None:
        """Bronze tables of the approved bronze_identification version, or None when it is not approved or listed none."""
        if BRONZE_STAGE not in self.approved:
            return None
        return self.versions[BRONZE_STAGE][self.approved[BRONZE_STAGE] - 1].get("bronze_tables")


def _replay(events: list[dict[str, Any]]) -> _Session:
    if not events or events[0].get("type") != "session_started":
        raise GateError("session log is corrupt: it does not start with session_started")
    first = events[0]
    s = _Session(first["session_id"], first["flow"], first["scope"], list(first["stages"]), first["user"], first["ts"])
    # Sessions started before stage metadata existed have no stage_meta; their stages show the id only.
    s.meta = {m["id"]: m for m in first.get("stage_meta") or []}
    s.versions = {stage: [] for stage in s.stages}
    for e in events[1:]:
        kind = e.get("type")
        if kind == "stage_submitted":
            s.versions[e["stage"]].append({"version": e["version"], "status": "pending", "content": e["content"], "sha256": e["sha256"], "user": e["user"], "ts": e["ts"], "bronze_tables": e.get("bronze_tables")})
        elif kind == "decision":
            rec = s.versions[e["stage"]][e["version"] - 1]
            rec["status"] = VERSION_STATUS[e["decision"]]
            if e["decision"] == "approve":
                s.approved[e["stage"]] = e["version"]
            for stage in e.get("invalidated", []):
                s.versions[stage][s.approved.pop(stage) - 1]["status"] = "invalidated"
            if e.get("then") == "end":
                s.ended = True
            s.log.append(e)
        elif kind == "permission_granted":
            s.grant = list(e["tables"])
            s.permission_log.append(e)
        elif kind == "permission_revoked":
            s.grant = None
            s.permission_log.append(e)
        elif kind == "artifact_written":
            s.artifacts.append(e["path"])
        else:
            raise GateError(f"session log is corrupt: unknown event type {kind!r}")
    return s


def _version(v: dict[str, Any]) -> StageVersion:
    return StageVersion(version=v["version"], status=v["status"], sha256=v["sha256"], submitted_by=v["user"], submitted_at=v["ts"], bronze_tables=v.get("bronze_tables"))


def _check_bronze(stage: str, tables: list[str]) -> list[str]:
    if stage != BRONZE_STAGE:
        raise GateError(f"bronze_tables is only accepted for stage {BRONZE_STAGE!r}, got stage {stage!r}")
    cleaned = [t.strip() for t in tables if isinstance(t, str) and t.strip()]
    if not cleaned or len(cleaned) != len(tables):
        raise GateError("bronze_tables must be a non-empty list of table names, e.g. raw.raw_orders")
    if len({t.lower() for t in cleaned}) != len(cleaned):
        raise GateError(f"bronze_tables has duplicates: {cleaned}")
    return cleaned


def _parse_stages(flow: str, items: list[Any]) -> list[dict[str, Any]]:
    """A stage is a plain id, or a mapping with id and optional label, og_step and major_gate. Major gates are numbered in flow order."""
    out: list[dict[str, Any]] = []
    for item in items:
        m = {"id": item} if isinstance(item, str) else dict(item) if isinstance(item, dict) else None
        if not m or not isinstance(m.get("id"), str) or not m["id"]:
            raise GateError(f"flow {flow!r}: each stage must be an id or a mapping with an id, got {item!r}")
        unknown = set(m) - STAGE_KEYS
        if unknown:
            raise GateError(f"flow {flow!r}, stage {m['id']!r}: unknown keys {sorted(unknown)}, allowed {sorted(STAGE_KEYS)}")
        m = {k: str(v) for k, v in m.items() if v is not None}
        if m.get("major_gate"):
            m["major_gate_number"] = sum(1 for x in out if x.get("major_gate")) + 1
        out.append(m)
    ids = [m["id"] for m in out]
    if len(set(ids)) != len(ids):
        raise GateError(f"gates.yaml flows: flow {flow!r} has duplicate stage ids: {ids}")
    return out


class GateStore:
    def __init__(self, writer: ArtifactWriter, config: dict[str, Any], user: str, clock: Callable[[], str] = _utc_now, templates: dict[str, dict[str, Any]] | None = None):
        flows = config.get("flows") or {}
        if not flows or not all(isinstance(v, list) and v for v in flows.values()):
            raise GateError("gates.yaml must define flows, each a non-empty list of stages")
        self.stage_meta: dict[str, list[dict[str, Any]]] = {k: _parse_stages(k, v) for k, v in flows.items()}
        self.flows: dict[str, list[str]] = {k: [m["id"] for m in v] for k, v in self.stage_meta.items()}
        # Template headings are copied into the stage metadata, so a session keeps the headings it started with.
        for metas in self.stage_meta.values():
            for m in metas:
                if "template" in m:
                    if m["template"] not in (templates or {}):
                        raise GateError(f"stage {m['id']!r} names template {m['template']!r}, which was not loaded")
                    m["headings"] = [str(h) for h in templates[m["template"]].get("headings") or []]
        self.max_content_bytes = int(config.get("max_content_bytes", 1_000_000))
        self.writer = writer
        self.user = user
        self.clock = clock

    def _append(self, session_id: str, record: dict[str, Any], create: bool = False) -> None:
        try:
            self.writer.append_session_event(session_id, {**record, "session_id": session_id, "user": self.user, "ts": self.clock()}, create=create)
        except WriterError as e:
            raise GateError(str(e)) from e

    def _load(self, session_id: str) -> _Session:
        try:
            return _replay(self.writer.read_session_events(session_id))
        except WriterError as e:
            raise GateError(str(e)) from e

    def _load_active(self, session_id: str) -> _Session:
        s = self._load(session_id)
        if s.ended:
            raise GateError(f"session {session_id} was ended by a disapprove decision. Start a new session.")
        return s

    def start(self, flow: str, scope: str) -> SessionView:
        if flow not in self.flows:
            raise GateError(f"flow must be one of {sorted(self.flows)}, got {flow!r}")
        if not scope or not scope.strip():
            raise GateError("scope is required, for example the model and tables under review")
        session_id = uuid.uuid4().hex
        self._append(session_id, {"type": "session_started", "flow": flow, "scope": scope, "stages": self.flows[flow], "stage_meta": self.stage_meta[flow]}, create=True)
        return self.view(session_id)

    def submit(self, session_id: str, stage: str, content: str, bronze_tables: list[str] | None = None) -> StageVersion:
        s = self._load_active(session_id)
        if s.complete:
            raise GateError(f"every stage of session {session_id} is approved. Nothing more can be submitted.")
        expected = s.next_stage
        if stage != expected:
            raise GateError(f"only stage {expected!r} can be submitted now (stages are approved in order), got {stage!r}")
        pending = s.pending(stage)
        if pending:
            raise GateError(f"version {pending['version']} of stage {stage!r} is waiting for the modeller's decision. Record it before submitting again.")
        if not content or not content.strip():
            raise GateError("content is required: the stage output shown to the modeller")
        size = len(content.encode("utf-8"))
        if size > self.max_content_bytes:
            raise GateError(f"content is {size} bytes, the limit is {self.max_content_bytes}")
        record: dict[str, Any] = {"type": "stage_submitted", "stage": stage, "version": len(s.versions[stage]) + 1, "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        if bronze_tables is not None:
            record["bronze_tables"] = _check_bronze(stage, bronze_tables)
        self._append(session_id, record)
        return _version(self._load(session_id).versions[stage][-1])

    def decide(self, session_id: str, stage: str, version: int, decision: str, note: str | None = None, then: str | None = None, reopen_stage: str | None = None) -> SessionView:
        s = self._load_active(session_id)
        if decision not in DECISIONS:
            raise GateError(f"decision must be exactly one of {list(DECISIONS)}, got {decision!r}. Ask the modeller again; never infer a decision.")
        if stage not in s.versions:
            raise GateError(f"stage {stage!r} is not in flow {s.flow!r}: {s.stages}")
        if not 1 <= version <= len(s.versions[stage]):
            raise GateError(f"stage {stage!r} has no version {version}")
        rec = s.versions[stage][version - 1]
        if rec["status"] != "pending":
            raise GateError(f"version {version} of stage {stage!r} is already {rec['status']}. Only the latest version waiting for a decision can be decided.")
        note = note.strip() if note and note.strip() else None
        invalidated: list[str] = []
        if decision == "approve":
            if then or reopen_stage:
                raise GateError("then and reopen_stage only apply to disapprove")
        elif decision == "suggest_changes":
            if not note:
                raise GateError("suggest_changes needs a comment saying what to change")
            if then or reopen_stage:
                raise GateError("then and reopen_stage only apply to disapprove")
        else:
            if not note:
                raise GateError("disapprove needs a reason")
            if then not in AFTER:
                raise GateError(f"disapprove needs then = one of {list(AFTER)}: redo this stage, reopen an earlier stage, or end the session")
            if then == "reopen":
                idx = s.stages.index(stage)
                if reopen_stage not in s.stages[:idx]:
                    raise GateError(f"reopen_stage must be an earlier stage than {stage!r}: {s.stages[:idx]}")
                invalidated = s.stages[s.stages.index(reopen_stage):idx]
            elif reopen_stage:
                raise GateError("reopen_stage only applies when then is reopen")
        self._append(session_id, {"type": "decision", "stage": stage, "version": version, "decision": decision, "note": note, "then": then, "reopen_stage": reopen_stage, "invalidated": invalidated})
        return self.view(session_id)

    def grant(self, session_id: str, permission: str, tables: list[str]) -> SessionView:
        """Log a permission to sample the given approved Bronze tables. Only while the session is at data_extraction (FR-B11)."""
        s = self._load_active(session_id)
        if permission not in PERMISSIONS:
            raise GateError(f"permission must be one of {list(PERMISSIONS)}, got {permission!r}")
        if EXTRACTION_STAGE not in s.stages or s.next_stage != EXTRACTION_STAGE:
            raise GateError(f"{permission} permission can only be granted while the session is at stage {EXTRACTION_STAGE!r}, after major gate 1 ({BRONZE_STAGE}) is approved; the current stage is {s.next_stage!r}")
        approved = s.approved_bronze
        if not approved:
            raise GateError(f"the approved {BRONZE_STAGE} version lists no bronze_tables, so nothing can be granted")
        by_key = {t.lower(): t for t in approved}
        wanted = [t.strip() for t in tables or [] if isinstance(t, str) and t.strip()]
        if not wanted or len({t.lower() for t in wanted}) != len(wanted):
            raise GateError("tables must be a non-empty list of unique Bronze table names")
        outside = [t for t in wanted if t.lower() not in by_key]
        if outside:
            raise GateError(f"tables not in the approved Bronze list {approved}: {outside}")
        self._append(session_id, {"type": "permission_granted", "permission": permission, "tables": [by_key[t.lower()] for t in wanted]})
        return self.view(session_id)

    def revoke(self, session_id: str, permission: str) -> SessionView:
        s = self._load(session_id)
        if permission not in PERMISSIONS:
            raise GateError(f"permission must be one of {list(PERMISSIONS)}, got {permission!r}")
        if s.grant is None:
            raise GateError(f"no active {permission} permission to revoke")
        self._append(session_id, {"type": "permission_revoked", "permission": permission, "tables": s.grant})
        return self.view(session_id)

    def require_sampling(self, session_id: str, table: str) -> str:
        """The table's approved name if sampling it is allowed now, else GateError. Never samples anything itself."""
        s = self._load_active(session_id)
        if s.next_stage != EXTRACTION_STAGE:
            raise GateError(f"sampling is only allowed while the session is at stage {EXTRACTION_STAGE!r}; the current stage is {s.next_stage!r}")
        if s.grant is None:
            raise GateError("no logged data_extraction permission. Ask the modeller and call grant_permission only on an explicit yes.")
        key = (table or "").strip().lower()
        granted = {t.lower(): t for t in s.grant}
        if key not in granted:
            raise GateError(f"table {table!r} is not in the granted tables {s.grant}")
        if key not in {t.lower() for t in s.approved_bronze or []}:
            raise GateError(f"table {table!r} is not in the currently approved Bronze list")
        return granted[key]

    def require_complete(self, session_id: str) -> _Session:
        s = self._load(session_id)
        if not s.complete:
            last = s.stages[-1]
            where = "the session was ended" if s.ended else f"the current stage is {s.next_stage!r}"
            raise GateError(f"blocked until the final stage {last!r} of session {session_id} is approved; {where}")
        return s

    def approved_outputs(self, session_id: str) -> list[dict[str, Any]]:
        s = self.require_complete(session_id)
        return [{"stage": stage, **s.versions[stage][s.approved[stage] - 1]} for stage in s.stages]

    def record_artifact(self, session_id: str, path: str) -> None:
        self.require_complete(session_id)
        self._append(session_id, {"type": "artifact_written", "path": path})

    def view(self, session_id: str) -> SessionView:
        s = self._load(session_id)
        current = None if s.ended else s.next_stage
        stages = []
        for stage in s.stages:
            versions = [_version(v) for v in s.versions[stage]]
            if stage in s.approved:
                status = "approved"
            elif s.pending(stage):
                status = "awaiting_decision"
            elif versions:
                status = "open"
            else:
                status = "not_started"
            meta = {k: v for k, v in s.meta.get(stage, {}).items() if k != "id"}
            stages.append(StageState(stage=stage, status=status, approved_version=s.approved.get(stage), versions=versions, **meta))
        if s.ended:
            status, allowed = "ended", []
        elif current is None:
            status, allowed = "complete", ["compile_report", "write_artifact"]
        else:
            status = "active"
            pending = s.pending(current)
            if pending:
                allowed = [f"record_decision stage={current} version={pending['version']} decision=approve, suggest_changes (comment required) or disapprove (reason and then required)"]
            else:
                allowed = [f"submit_stage stage={current}"]
        log = [LogEntry(**{k: e.get(k) for k in LogEntry.model_fields if k != "invalidated"}, invalidated=e.get("invalidated", [])) for e in s.log]
        return SessionView(
            session_id=s.session_id, flow=s.flow, scope=s.scope, status=status, created_by=s.created_by, created_at=s.created_at,
            current_stage=current, stages=stages, allowed_next=allowed, decision_log=log, artifacts=s.artifacts,
            permissions=[PermissionEntry(event="granted" if e["type"] == "permission_granted" else "revoked", permission=e["permission"], tables=e.get("tables", []), user=e["user"], ts=e["ts"]) for e in s.permission_log],
            granted_tables=s.grant,
        )

    def list_sessions(self) -> list[SessionSummary]:
        out = []
        for sid in self.writer.list_session_ids():
            try:
                v = self.view(sid)
            except GateError:
                continue
            out.append(SessionSummary(session_id=sid, flow=v.flow, scope=v.scope, status=v.status, current_stage=v.current_stage, created_at=v.created_at))
        return out
