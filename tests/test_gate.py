import json

import pytest

from conftest import CONFIG_DIR
from dma.config import load_yaml
from dma.gate import GateError, GateStore
from dma.writer import ArtifactWriter

STAGES = ["scope", "intake", "findings"]


@pytest.fixture
def out(tmp_path):
    return tmp_path / "output"


def make_store(out, user="tester"):
    return GateStore(ArtifactWriter(out), {"flows": {"review": STAGES}, "max_content_bytes": 100}, user=user)


@pytest.fixture
def gate(out):
    return make_store(out)


@pytest.fixture
def sid(gate):
    return gate.start("review", "model X, tables A and B").session_id


def approve(gate, sid, stage, content="ok output"):
    v = gate.submit(sid, stage, content).version
    gate.decide(sid, stage, v, "approve")
    return v


def stage_state(gate, sid, stage):
    return next(s for s in gate.view(sid).stages if s.stage == stage)


def test_start_returns_stages_and_first_step(gate, sid):
    v = gate.view(sid)
    assert [s.stage for s in v.stages] == STAGES
    assert v.status == "active" and v.current_stage == "scope"
    assert v.allowed_next == ["submit_stage stage=scope"]
    assert v.created_by == "tester"


def test_real_config_flows_load(out):
    store = GateStore(ArtifactWriter(out), load_yaml(CONFIG_DIR, "gates.yaml"), user="t")
    assert set(store.flows) == {"review", "build", "lineage"}
    assert store.flows["review"][-1] == "findings"
    assert store.flows["build"] == ["scope", "powerbi_intake", "requirements", "legacy_entities", "bronze_identification", "data_extraction", "reuse_check", "silver_staging", "silver_idm", "gold", "rules_pass"]


def test_start_refuses_unknown_flow_and_empty_scope(gate):
    with pytest.raises(GateError, match="flow"):
        gate.start("deploy", "x")
    with pytest.raises(GateError, match="scope"):
        gate.start("review", "  ")


def test_approve_locks_stage_and_moves_on(gate, sid):
    approve(gate, sid, "scope")
    v = gate.view(sid)
    assert v.current_stage == "intake"
    assert stage_state(gate, sid, "scope").status == "approved"
    assert v.decision_log[0].decision == "approve" and v.decision_log[0].user == "tester"
    with pytest.raises(GateError, match="only stage 'intake'"):
        gate.submit(sid, "scope", "change after approval")


def test_suggest_changes_needs_comment_and_creates_next_version(gate, sid):
    v1 = gate.submit(sid, "scope", "draft").version
    with pytest.raises(GateError, match="comment"):
        gate.decide(sid, "scope", v1, "suggest_changes")
    with pytest.raises(GateError, match="comment"):
        gate.decide(sid, "scope", v1, "suggest_changes", note="   ")
    gate.decide(sid, "scope", v1, "suggest_changes", note="add table C")
    assert stage_state(gate, sid, "scope").status == "open"
    v2 = gate.submit(sid, "scope", "draft with C").version
    assert (v1, v2) == (1, 2)
    gate.decide(sid, "scope", v2, "approve")
    st = stage_state(gate, sid, "scope")
    assert st.approved_version == 2
    assert [x.status for x in st.versions] == ["changes_requested", "approved"]


def test_disapprove_needs_reason_and_then(gate, sid):
    v = gate.submit(sid, "scope", "draft").version
    with pytest.raises(GateError, match="reason"):
        gate.decide(sid, "scope", v, "disapprove", then="redo")
    with pytest.raises(GateError, match="then"):
        gate.decide(sid, "scope", v, "disapprove", note="wrong model")
    with pytest.raises(GateError, match="then"):
        gate.decide(sid, "scope", v, "disapprove", note="wrong model", then="later")


def test_disapprove_redo(gate, sid):
    v = gate.submit(sid, "scope", "draft").version
    gate.decide(sid, "scope", v, "disapprove", note="wrong model", then="redo")
    st = stage_state(gate, sid, "scope")
    assert st.status == "open" and st.versions[0].status == "disapproved"
    assert gate.submit(sid, "scope", "redo").version == 2


def test_disapprove_reopen_invalidates_later_stages(gate, sid):
    approve(gate, sid, "scope")
    approve(gate, sid, "intake")
    v = gate.submit(sid, "findings", "findings draft").version
    with pytest.raises(GateError, match="earlier stage"):
        gate.decide(sid, "findings", v, "disapprove", note="scope was wrong", then="reopen", reopen_stage="findings")
    with pytest.raises(GateError, match="earlier stage"):
        gate.decide(sid, "findings", v, "disapprove", note="scope was wrong", then="reopen")
    view = gate.decide(sid, "findings", v, "disapprove", note="scope was wrong", then="reopen", reopen_stage="scope")
    assert view.current_stage == "scope"
    assert view.decision_log[-1].invalidated == ["scope", "intake"]
    assert [stage_state(gate, sid, s).status for s in STAGES] == ["open", "open", "open"]
    assert stage_state(gate, sid, "intake").versions[0].status == "invalidated"
    with pytest.raises(GateError, match="only stage 'scope'"):
        gate.submit(sid, "intake", "skip ahead")
    assert gate.submit(sid, "scope", "new scope").version == 2


def test_disapprove_end_closes_session(gate, sid):
    v = gate.submit(sid, "scope", "draft").version
    view = gate.decide(sid, "scope", v, "disapprove", note="not needed", then="end")
    assert view.status == "ended" and view.allowed_next == []
    with pytest.raises(GateError, match="ended"):
        gate.submit(sid, "scope", "again")
    with pytest.raises(GateError, match="ended"):
        gate.require_complete(sid)


def test_submit_out_of_order_refused(gate, sid):
    with pytest.raises(GateError, match="only stage 'scope'"):
        gate.submit(sid, "intake", "skip")
    with pytest.raises(GateError, match="only stage 'scope'"):
        gate.submit(sid, "nope", "x")


def test_submit_while_pending_refused(gate, sid):
    gate.submit(sid, "scope", "v1")
    with pytest.raises(GateError, match="waiting"):
        gate.submit(sid, "scope", "v2")


def test_submit_content_checks(gate, sid):
    with pytest.raises(GateError, match="content"):
        gate.submit(sid, "scope", " ")
    with pytest.raises(GateError, match="limit"):
        gate.submit(sid, "scope", "x" * 101)


def test_superseded_and_decided_versions_refused(gate, sid):
    gate.submit(sid, "scope", "v1")
    gate.decide(sid, "scope", 1, "suggest_changes", note="more")
    gate.submit(sid, "scope", "v2")
    with pytest.raises(GateError, match="already changes_requested"):
        gate.decide(sid, "scope", 1, "approve")
    with pytest.raises(GateError, match="no version 3"):
        gate.decide(sid, "scope", 3, "approve")
    gate.decide(sid, "scope", 2, "approve")
    with pytest.raises(GateError, match="already approved"):
        gate.decide(sid, "scope", 2, "approve")


@pytest.mark.parametrize("word", ["ok", "looks fine", "Approve", "approved", "", "yes"])
def test_only_exact_decision_words(gate, sid, word):
    gate.submit(sid, "scope", "draft")
    with pytest.raises(GateError, match="exactly one of"):
        gate.decide(sid, "scope", 1, word)
    assert stage_state(gate, sid, "scope").status == "awaiting_decision"


def test_then_only_with_disapprove(gate, sid):
    gate.submit(sid, "scope", "draft")
    with pytest.raises(GateError, match="only apply to disapprove"):
        gate.decide(sid, "scope", 1, "approve", then="end")
    with pytest.raises(GateError, match="only applies when then is reopen"):
        gate.decide(sid, "scope", 1, "disapprove", note="x", then="redo", reopen_stage="scope")


def test_complete_and_approved_outputs_only(gate, sid):
    with pytest.raises(GateError, match="blocked"):
        gate.require_complete(sid)
    gate.submit(sid, "scope", "SUPERSEDED scope")
    gate.decide(sid, "scope", 1, "suggest_changes", note="narrow it")
    approve(gate, sid, "scope", "APPROVED scope")
    gate.submit(sid, "intake", "DISCARDED intake")
    gate.decide(sid, "intake", 1, "disapprove", note="wrong", then="redo")
    approve(gate, sid, "intake", "APPROVED intake")
    approve(gate, sid, "findings", "APPROVED findings")
    assert gate.view(sid).status == "complete"
    outputs = gate.approved_outputs(sid)
    assert [(o["stage"], o["version"], o["content"]) for o in outputs] == [("scope", 2, "APPROVED scope"), ("intake", 2, "APPROVED intake"), ("findings", 1, "APPROVED findings")]
    with pytest.raises(GateError, match="every stage"):
        gate.submit(sid, "findings", "more")


def test_decision_log_is_append_only_jsonl(gate, sid, out):
    approve(gate, sid, "scope")
    lines = (out / ".sessions" / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["type"] for e in events] == ["session_started", "stage_submitted", "decision"]
    for e in events:
        assert {"session_id", "user", "ts"} <= set(e) and e["ts"].endswith("Z")


def test_restart_reloads_from_disk_and_continues(out):
    first = make_store(out, user="alice")
    sid = first.start("review", "model X").session_id
    approve(first, sid, "scope")
    first.submit(sid, "intake", "pending intake")

    second = make_store(out, user="bob")
    view = second.view(sid)
    assert view.current_stage == "intake" and stage_state(second, sid, "intake").status == "awaiting_decision"
    second.decide(sid, "intake", 1, "approve")
    approve(second, sid, "findings")
    assert second.view(sid).status == "complete"
    assert [e.user for e in second.view(sid).decision_log] == ["alice", "bob", "bob"]
    assert [s.session_id for s in second.list_sessions()] == [sid]


def test_unknown_session(gate):
    with pytest.raises(GateError, match="unknown session"):
        gate.view("0" * 32)
    with pytest.raises(GateError, match="session_id"):
        gate.view("../x")


def test_bad_config_refused(out):
    with pytest.raises(GateError, match="flows"):
        GateStore(ArtifactWriter(out), {"flows": {"review": ["a", "a"]}}, user="t")
    with pytest.raises(GateError, match="flows"):
        GateStore(ArtifactWriter(out), {}, user="t")


# Stage metadata: label, OG step and major gates, kept in the session.

META_FLOW = [
    {"id": "scope", "label": "Scope"},
    {"id": "bronze", "label": "Bronze identification", "og_step": 4, "major_gate": "Bronze table list"},
    "plain",
    {"id": "gold", "major_gate": "Gold models"},
]


def test_stage_metadata_in_view_and_major_gate_numbers(out):
    store = GateStore(ArtifactWriter(out), {"flows": {"build": META_FLOW}}, user="t")
    assert store.flows["build"] == ["scope", "bronze", "plain", "gold"]
    v = store.start("build", "report R")
    by = {s.stage: s for s in v.stages}
    assert (by["bronze"].label, by["bronze"].og_step, by["bronze"].major_gate, by["bronze"].major_gate_number) == ("Bronze identification", "4", "Bronze table list", 1)
    assert (by["gold"].major_gate_number, by["plain"].label, by["scope"].major_gate) == (2, None, None)


def test_session_keeps_the_metadata_it_started_with(out):
    sid = GateStore(ArtifactWriter(out), {"flows": {"build": META_FLOW}}, user="t").start("build", "r").session_id
    renamed = [{"id": "scope", "label": "Renamed"}, "bronze", "plain", "gold"]
    later = GateStore(ArtifactWriter(out), {"flows": {"build": renamed}}, user="t")
    assert later.view(sid).stages[0].label == "Scope"


def test_old_session_without_metadata_replays(gate, out):
    sid = "a" * 32
    gate.writer.append_session_event(sid, {"type": "session_started", "flow": "review", "scope": "old", "stages": STAGES, "session_id": sid, "user": "t", "ts": "2026-10-28T00:00:00Z"}, create=True)
    v = gate.view(sid)
    assert [s.stage for s in v.stages] == STAGES and all(s.label is None for s in v.stages)
    approve(gate, sid, "scope")
    assert gate.view(sid).current_stage == "intake"


@pytest.mark.parametrize("flow", [[{"label": "no id"}], [{"id": "a", "colour": "red"}], [42]])
def test_bad_stage_items_refused(out, flow):
    with pytest.raises(GateError, match="stage"):
        GateStore(ArtifactWriter(out), {"flows": {"build": flow}}, user="t")


OLD_BUILD = ["scope", "powerbi_intake", "source_tables", "bronze_identification", "silver_staging", "silver_idm", "gold", "rules_pass"]


def test_build_flow_major_gates_from_real_config(out):
    store = GateStore(ArtifactWriter(out), load_yaml(CONFIG_DIR, "gates.yaml"), user="t")
    v = store.start("build", "report R")
    majors = [(s.major_gate_number, s.stage, s.major_gate) for s in v.stages if s.major_gate]
    assert majors == [(1, "bronze_identification", "Bronze table list"), (2, "silver_idm", "Silver schemas"), (3, "gold", "Gold models")]
    assert [s.og_step for s in v.stages][:6] == [None, "1", "2", "3", "4", "5.1"]


def test_old_eight_stage_build_session_replays_and_completes(out):
    store = GateStore(ArtifactWriter(out), load_yaml(CONFIG_DIR, "gates.yaml"), user="t")
    sid = "b" * 32
    store.writer.append_session_event(sid, {"type": "session_started", "flow": "build", "scope": "old build", "stages": OLD_BUILD, "session_id": sid, "user": "t", "ts": "2026-10-28T00:00:00Z"}, create=True)
    assert [s.stage for s in store.view(sid).stages] == OLD_BUILD
    for stage in OLD_BUILD:
        approve(store, sid, stage)
    assert store.view(sid).status == "complete" and [o["stage"] for o in store.approved_outputs(sid)] == OLD_BUILD


# The approved Bronze table list on bronze_identification.


def bronze_store(out):
    flow = ["scope", "bronze_identification", "data_extraction", "rest"]
    return GateStore(ArtifactWriter(out), {"flows": {"build": flow}}, user="t")


def test_bronze_tables_recorded_and_locked_by_approval(out):
    store = bronze_store(out)
    sid = store.start("build", "r").session_id
    approve(store, sid, "scope")
    v1 = store.submit(sid, "bronze_identification", "list v1", ["raw.raw_orders"])
    assert v1.bronze_tables == ["raw.raw_orders"] and store._load(sid).approved_bronze is None
    store.decide(sid, "bronze_identification", 1, "suggest_changes", "add customers")
    store.submit(sid, "bronze_identification", "list v2", [" raw.raw_orders ", "raw.raw_customers"])
    store.decide(sid, "bronze_identification", 2, "approve")
    assert store._load(sid).approved_bronze == ["raw.raw_orders", "raw.raw_customers"]
    assert store.view(sid).stages[1].versions[0].bronze_tables == ["raw.raw_orders"]


@pytest.mark.parametrize("stage,tables,match", [
    ("scope", ["raw.a"], "only accepted"),
    ("bronze_identification", [], "non-empty"),
    ("bronze_identification", ["raw.a", " "], "non-empty"),
    ("bronze_identification", ["raw.a", "RAW.A"], "duplicates"),
])
def test_bronze_tables_refused(out, stage, tables, match):
    store = bronze_store(out)
    sid = store.start("build", "r").session_id
    if stage == "bronze_identification":
        approve(store, sid, "scope")
    with pytest.raises(GateError, match=match):
        store.submit(sid, stage, "x", tables)


# data_extraction permission: grant, revoke and the sampling check.


def at_extraction(out, tables=("raw.raw_orders", "raw.raw_customers")):
    store = bronze_store(out)
    sid = store.start("build", "r").session_id
    approve(store, sid, "scope")
    v = store.submit(sid, "bronze_identification", "bronze list", list(tables)).version
    store.decide(sid, "bronze_identification", v, "approve")
    return store, sid


def test_grant_refused_before_major_gate_1(out):
    store = bronze_store(out)
    sid = store.start("build", "r").session_id
    with pytest.raises(GateError, match="major gate 1"):
        store.grant(sid, "data_extraction", ["raw.raw_orders"])


@pytest.mark.parametrize("permission,tables,match", [
    ("profiling", ["raw.raw_orders"], "permission must be"),
    ("data_extraction", ["raw.raw_payments"], "not in the approved Bronze list"),
    ("data_extraction", [], "non-empty"),
])
def test_grant_refused_for_bad_input(out, permission, tables, match):
    store, sid = at_extraction(out)
    with pytest.raises(GateError, match=match):
        store.grant(sid, permission, tables)


def test_grant_sample_check_and_revoke_are_logged(out):
    store, sid = at_extraction(out)
    with pytest.raises(GateError, match="no logged"):
        store.require_sampling(sid, "raw.raw_orders")
    v = store.grant(sid, "data_extraction", ["RAW.RAW_ORDERS"])
    assert v.granted_tables == ["raw.raw_orders"]
    assert store.require_sampling(sid, "raw.raw_orders") == "raw.raw_orders"
    with pytest.raises(GateError, match="not in the granted"):
        store.require_sampling(sid, "raw.raw_customers")
    v = store.revoke(sid, "data_extraction")
    assert v.granted_tables is None and [(p.event, p.tables) for p in v.permissions] == [("granted", ["raw.raw_orders"]), ("revoked", ["raw.raw_orders"])]
    with pytest.raises(GateError, match="no logged"):
        store.require_sampling(sid, "raw.raw_orders")
    with pytest.raises(GateError, match="no active"):
        store.revoke(sid, "data_extraction")
    types = [json.loads(line)["type"] for line in (out / ".sessions" / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert types[-2:] == ["permission_granted", "permission_revoked"]


def test_sampling_refused_after_reopen_of_bronze(out):
    store, sid = at_extraction(out)
    store.grant(sid, "data_extraction", ["raw.raw_orders"])
    v = store.submit(sid, "data_extraction", "sampled").version
    store.decide(sid, "data_extraction", v, "disapprove", "wrong tables", then="reopen", reopen_stage="bronze_identification")
    with pytest.raises(GateError, match="current stage is 'bronze_identification'"):
        store.require_sampling(sid, "raw.raw_orders")


def test_sampling_refused_after_data_extraction_is_approved(out):
    store, sid = at_extraction(out)
    store.grant(sid, "data_extraction", ["raw.raw_orders"])
    approve(store, sid, "data_extraction")
    with pytest.raises(GateError, match="only allowed while"):
        store.require_sampling(sid, "raw.raw_orders")
