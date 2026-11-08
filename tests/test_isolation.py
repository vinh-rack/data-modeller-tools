"""Per-user isolation in HTTP mode: a caller can only see and act on the sessions it started.

Each case sets up a session owned by user A in a state where the call is valid, checks that user B is refused with
the same "unknown session" text as a missing session and that nothing was logged, then that A's own call succeeds.
"""

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from conftest import CONFIG_DIR, SMX_DIR
from dma.config import load_settings
from dma.identity import Caller
from dma.tools import DmaTools

A = Caller(id="aaaaaaaa-0000-4000-8000-000000000001", label="User A (aaaaaaaa-0000-4000-8000-000000000001)")
B = Caller(id="bbbbbbbb-0000-4000-8000-000000000002", label="User B (bbbbbbbb-0000-4000-8000-000000000002)")
BRONZE = ["brnz_fleetops.booking", "brnz_fleetops.driver"]
TABLE_STATUS = {"booking": "dbt_model_found", "store_targets": "missing"}
SQL = "select s.store_id from raw.store_targets_extract s"


class Switch:
    def __init__(self):
        self.who = A

    def __call__(self):
        return self.who


@pytest.fixture
def env(tmp_path):
    settings = load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(CONFIG_DIR)})
    switch = Switch()
    return DmaTools(settings, caller=switch, enforce_owner=True), switch


def lineage(t, approve_all=False, submit_first=False):
    sid = t.start_session("lineage", "isolation test").session_id
    stages = [s.stage for s in t.get_session(sid).stages]
    if submit_first:
        t.submit_stage(sid, stages[0], "stage output")
    if approve_all:
        for st in stages:
            v = t.submit_stage(sid, st, f"output {st}").version
            t.record_decision(sid, st, v, "approve")
    return sid


def build_at_extraction(t, grant=False, route=False):
    sid = t.start_session("build", "isolation test").session_id
    for st in ["scope", "powerbi_intake", "requirements", "legacy_entities", "bronze_identification"]:
        extra = {"bronze_tables": BRONZE, "table_status": TABLE_STATUS} if st == "bronze_identification" else {}
        v = t.submit_stage(sid, st, f"output {st}", **extra).version
        t.record_decision(sid, st, v, "approve")
    if grant:
        t.grant_permission(sid, "data_extraction", [BRONZE[0]])
    if route:
        t.choose_missing_route(sid, "store_targets", "provided_code")
    return sid


CASES = {
    "get_session": (lambda t: lineage(t), lambda t, sid: t.get_session(sid)),
    "submit_stage": (lambda t: lineage(t), lambda t, sid: t.submit_stage(sid, "scope", "stage output")),
    "record_decision": (lambda t: lineage(t, submit_first=True), lambda t, sid: t.record_decision(sid, "scope", 1, "approve")),
    "grant_permission": (lambda t: build_at_extraction(t), lambda t, sid: t.grant_permission(sid, "data_extraction", [BRONZE[0]])),
    "revoke_permission": (lambda t: build_at_extraction(t, grant=True), lambda t, sid: t.revoke_permission(sid, "data_extraction")),
    "sample_rows": (lambda t: build_at_extraction(t, grant=True), lambda t, sid: t.sample_rows(sid, BRONZE[0])),
    "choose_missing_route": (lambda t: build_at_extraction(t), lambda t, sid: t.choose_missing_route(sid, "store_targets", "data_analysis")),
    "provide_table_code": (lambda t: build_at_extraction(t, route=True), lambda t, sid: t.provide_table_code(sid, "store_targets", SQL)),
    "compile_report": (lambda t: lineage(t, approve_all=True), lambda t, sid: t.compile_report(sid)),
    "write_artifact": (lambda t: lineage(t, approve_all=True), lambda t, sid: t.write_artifact("lineage_mermaid", "isolation", "flowchart LR", sid)),
    "read_artifact (resource)": (lambda t: with_artifact(t), lambda t, sid: t.read_artifact(sid, last_artifact_name(t, sid), binary=False)),
}


def with_artifact(t):
    sid = lineage(t, approve_all=True)
    t.write_artifact("lineage_mermaid", "isolation", "flowchart LR", sid)
    return sid


def last_artifact_name(t, sid):
    # Read straight from the log, so user B asks for the real file name and only the owner check stands in the way.
    events = [json.loads(line) for line in t.writer.session_log(sid).read_text(encoding="utf-8").splitlines()]
    return Path([e for e in events if e["type"] == "artifact_written"][-1]["path"]).name


def log_text(t, sid):
    return t.writer.session_log(sid).read_text(encoding="utf-8")


@pytest.mark.parametrize("tool", sorted(CASES))
def test_other_user_is_refused_and_owner_is_not(env, tool):
    t, switch = env
    setup, call = CASES[tool]
    switch.who = A
    sid = setup(t)
    before = log_text(t, sid)
    switch.who = B
    with pytest.raises((ToolError, ResourceError)) as err:
        call(t, sid)
    assert str(err.value) == f"unknown session: {sid}"
    assert log_text(t, sid) == before
    switch.who = A
    call(t, sid)


def test_list_sessions_shows_only_the_callers_own(env):
    t, switch = env
    switch.who = A
    mine = lineage(t)
    switch.who = B
    theirs = lineage(t)
    assert [s.session_id for s in t.list_sessions().sessions] == [theirs]
    switch.who = A
    assert [s.session_id for s in t.list_sessions().sessions] == [mine]


def test_ownerless_session_is_invisible_in_http_mode(env, tmp_path):
    t, switch = env
    # A stdio server sharing the same output folder starts a session with no owner.
    stdio = DmaTools(t.settings)
    sid = stdio.start_session("lineage", "local session").session_id
    assert "owner" not in json.loads(log_text(stdio, sid).splitlines()[0])
    switch.who = A
    assert t.list_sessions().sessions == []
    with pytest.raises(ToolError, match="unknown session"):
        t.get_session(sid)


def test_unknown_and_foreign_sessions_read_the_same(env):
    t, switch = env
    switch.who = A
    sid = lineage(t)
    switch.who = B
    with pytest.raises(ToolError) as foreign:
        t.get_session(sid)
    missing = "0" * 32
    with pytest.raises(ToolError) as unknown:
        t.get_session(missing)
    assert str(foreign.value).replace(sid, "<id>") == str(unknown.value).replace(missing, "<id>")


def test_artifacts_land_in_the_callers_folder(env):
    t, switch = env
    switch.who = A
    sid = lineage(t, approve_all=True)
    report = t.compile_report(sid)
    mermaid = t.write_artifact("lineage_mermaid", "isolation", "flowchart LR", sid)
    root = t.settings.output_dir
    for path in [a.path for a in report.artifacts] + [mermaid.path]:
        assert Path(path).parent == root / A.id
    assert not any(p.is_file() for p in root.iterdir())


# The same isolation over HTTP, with two users holding signed Entra test tokens.

import entrakit as ek
from dma.server import build_http
from httpkit import mcp_client


@pytest.mark.anyio
async def test_http_users_see_only_their_own_sessions(tmp_path):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR)
    app = build_http(load_settings(env), env, jwks_client=ek.LocalJWKS())
    tok_a = ek.token(oid=ek.USER_A, name="User A")
    tok_b = ek.token(oid=ek.USER_B, name="User B")
    async with mcp_client(app, headers={"authorization": f"Bearer {tok_a}"}) as a:
        sid = (await a.call_tool("start_session", {"flow": "lineage", "scope": "http isolation"})).structured_content["session_id"]
        await a.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "stage output"})
    log = (tmp_path / "output" / ".sessions" / sid / "events.jsonl").read_text(encoding="utf-8")
    first = json.loads(log.splitlines()[0])
    assert first["owner"] == ek.USER_A and first["user"] == f"User A ({ek.USER_A})" and first["user_id"] == ek.USER_A


@pytest.mark.anyio
async def test_http_other_user_cannot_read_or_decide(tmp_path):
    env = ek.entra_env(tmp_path, SMX_DIR, CONFIG_DIR)
    settings = load_settings(env)
    # Two apps over one output folder, as two requests to one server: each request's token names its caller.
    app_a = build_http(settings, env, jwks_client=ek.LocalJWKS())
    async with mcp_client(app_a, headers={"authorization": f"Bearer {ek.token(oid=ek.USER_A, name='User A')}"}) as a:
        sid = (await a.call_tool("start_session", {"flow": "lineage", "scope": "http isolation"})).structured_content["session_id"]
        await a.call_tool("submit_stage", {"session_id": sid, "stage": "scope", "content": "stage output"})
    app_b = build_http(settings, env, jwks_client=ek.LocalJWKS())
    async with mcp_client(app_b, headers={"authorization": f"Bearer {ek.token(oid=ek.USER_B, name='User B')}"}) as b:
        got = await b.call_tool("get_session", {"session_id": sid})
        decided = await b.call_tool("record_decision", {"session_id": sid, "stage": "scope", "version": 1, "decision": "approve"})
        listed = (await b.call_tool("list_sessions", {})).structured_content["sessions"]
    assert got.is_error and got.content[0].text.endswith(f"unknown session: {sid}")
    assert decided.is_error and decided.content[0].text.endswith(f"unknown session: {sid}")
    assert listed == []
    events = (tmp_path / "output" / ".sessions" / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(e)["type"] for e in events] == ["session_started", "stage_submitted"]
