"""Per-user isolation in HTTP mode: a caller can only see and act on the sessions it started.

Each case sets up a session owned by user A in a state where the call is valid, checks that user B is refused with
the same "unknown session" text as a missing session and that nothing was logged, then that A's own call succeeds.
"""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

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
}


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
    with pytest.raises(ToolError) as err:
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
