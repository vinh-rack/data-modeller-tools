"""Power BI client tests. Mocked HTTP only, never a live call."""

import json
import sys

import pytest

from dma.connectors.powerbi import ArrowUnavailable, PowerBIAuthError, PowerBIClient, PowerBIError, PowerBIForbiddenCall, Response, check_dax, parse_arrow

TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.synthetic.token"
WS = "11111111-1111-1111-1111-111111111111"
REPORT = "22222222-2222-2222-2222-222222222222"
DATASET = "33333333-3333-3333-3333-333333333333"
OTHER = "44444444-4444-4444-4444-444444444444"
BASE = "https://api.powerbi.com/v1.0/myorg"


class FakeSend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json.loads(body) if body else None})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(data, status=200, headers=None):
    return Response(status, headers or {"content-type": "application/json"}, json.dumps(data).encode())


def client(send, **kw):
    return PowerBIClient(TOKEN, send=send, sleep=kw.pop("sleep", lambda s: None), **kw)


REPORT_JSON = {"id": REPORT, "name": "AGI-DMA-TEST-REPORT", "datasetId": DATASET, "webUrl": "https://app.powerbi.com/reports/x", "reportType": "PowerBIReport"}


def test_my_workspace_paths_have_no_group():
    send = FakeSend(ok(REPORT_JSON))
    client(send).get_report(REPORT)
    assert send.calls[0]["url"] == f"{BASE}/reports/{REPORT}"
    assert send.calls[0]["method"] == "GET"


def test_group_paths():
    send = FakeSend(ok(REPORT_JSON), ok({"id": DATASET, "name": "SM"}), ok({"value": []}))
    c = client(send)
    c.get_report(REPORT, WS)
    c.get_dataset(DATASET, WS)
    c.get_datasources(DATASET, WS)
    assert [x["url"] for x in send.calls] == [f"{BASE}/groups/{WS}/reports/{REPORT}", f"{BASE}/groups/{WS}/datasets/{DATASET}", f"{BASE}/groups/{WS}/datasets/{DATASET}/datasources"]


@pytest.mark.parametrize("bad", ["", "abc", "../reports", f"{REPORT}/../x", f"{REPORT}?x=1", None])
def test_non_guid_ids_rejected_before_sending(bad):
    send = FakeSend(ok(REPORT_JSON))
    with pytest.raises(PowerBIError, match="GUID"):
        client(send).get_report(bad)
    assert send.calls == []


@pytest.mark.parametrize("method,path", [
    ("DELETE", f"reports/{REPORT}"),
    ("PATCH", f"datasets/{DATASET}"),
    ("PUT", f"datasets/{DATASET}"),
    ("POST", f"datasets/{DATASET}/refreshes"),
    ("POST", f"groups/{WS}/reports/{REPORT}/Clone"),
    ("POST", f"reports/{REPORT}"),
    ("GET", f"datasets/{DATASET}/refreshes"),
    ("GET", "groups"),
    ("GET", f"admin/groups/{WS}"),
    ("POST", f"datasets/{DATASET}/executeDaxQueries"),
    ("GET", f"reports/{REPORT}/../../admin"),
])
def test_allowlist_refuses_before_sending(method, path):
    send = FakeSend(ok({}))
    with pytest.raises(PowerBIForbiddenCall):
        client(send).request(method, path)
    assert send.calls == []


def test_resolve_report_dataset_mismatch():
    send = FakeSend(ok(REPORT_JSON))
    with pytest.raises(PowerBIError, match="dataset mismatch"):
        client(send).resolve_report(REPORT, dataset_id=OTHER)
    assert client(FakeSend(ok(REPORT_JSON))).resolve_report(REPORT, dataset_id=DATASET.upper())["datasetId"] == DATASET


@pytest.mark.parametrize("report", [{**REPORT_JSON, "reportType": "PaginatedReport"}, {**REPORT_JSON, "datasetId": None}])
def test_paginated_report_rejected(report):
    with pytest.raises(PowerBIError, match="paginated"):
        client(FakeSend(ok(report))).resolve_report(REPORT)


@pytest.mark.parametrize("raw", [TOKEN, f"Bearer {TOKEN}", f"bearer   {TOKEN}  "])
def test_token_prefix_and_401_without_leak(raw):
    send = FakeSend(Response(401, {}, json.dumps({"error": {"code": "TokenExpired", "message": f"bad token {TOKEN}"}}).encode()))
    c = PowerBIClient(raw, send=send)
    with pytest.raises(PowerBIAuthError) as e:
        c.get_report(REPORT)
    assert send.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert len(send.calls) == 1
    assert TOKEN not in str(e.value) and "Get-PowerBIAccessToken" in str(e.value)
    assert TOKEN not in repr(c)


def test_other_errors_redact_token():
    send = FakeSend(Response(404, {}, json.dumps({"error": {"code": "NotFound", "message": f"echo {TOKEN}"}}).encode()))
    with pytest.raises(PowerBIError) as e:
        client(send).get_report(REPORT)
    assert e.value.status == 404 and TOKEN not in str(e.value) and "[redacted]" in str(e.value)


def test_missing_token_and_bad_base():
    with pytest.raises(PowerBIError, match="PBI_ACCESS_TOKEN"):
        PowerBIClient("  ")
    with pytest.raises(PowerBIError, match="PBI_ACCESS_TOKEN"):
        PowerBIClient("Bearer ")
    with pytest.raises(PowerBIError, match="https"):
        PowerBIClient(TOKEN, base="http://api.powerbi.com/v1.0/myorg")


def test_429_retries_once_after_retry_after():
    waits = []
    send = FakeSend(Response(429, {"retry-after": "7"}, b""), ok(REPORT_JSON))
    assert client(send, sleep=waits.append).get_report(REPORT)["name"] == "AGI-DMA-TEST-REPORT"
    assert waits == [7.0] and len(send.calls) == 2


def test_429_twice_fails():
    waits = []
    send = FakeSend(Response(429, {"retry-after": "500"}, b""), Response(429, {"retry-after": "1"}, b""))
    with pytest.raises(PowerBIError, match="429"):
        client(send, sleep=waits.append).get_report(REPORT)
    assert waits == [60.0] and len(send.calls) == 2


def test_429_without_retry_after_fails_at_once():
    send = FakeSend(Response(429, {}, b""))
    with pytest.raises(PowerBIError, match="429"):
        client(send).get_report(REPORT)
    assert len(send.calls) == 1


@pytest.mark.parametrize("query", ["", "DEFINE MEASURE x = 1 EVALUATE ROW(\"a\", 1)", "SELECT * FROM t", "EVALUATE ROW(\"a\", 1) -- hide", "EVALUATE /* x */ ROW(\"a\", 1)", "EVALUATEROW(1)"])
def test_non_evaluate_dax_refused(query):
    send = FakeSend(ok({}))
    with pytest.raises(PowerBIError):
        client(send).execute_queries(DATASET, query)
    assert send.calls == []
    with pytest.raises(PowerBIError):
        check_dax(query)


def test_execute_queries_body_and_row_cap():
    rows = [{"[table]": "t", "[column]": f"c{i}"} for i in range(5)]
    send = FakeSend(ok({"results": [{"tables": [{"rows": rows}]}]}))
    r = client(send, max_rows=3).execute_queries(DATASET, 'EVALUATE ROW("ok", 1)')
    assert send.calls[0]["url"] == f"{BASE}/datasets/{DATASET}/executeQueries"
    assert send.calls[0]["body"] == {"queries": [{"query": 'EVALUATE ROW("ok", 1)'}], "serializerSettings": {"includeNulls": True}}
    assert len(r.rows) == 3 and r.truncated


def test_execute_queries_tenant_hint_and_query_error():
    send = FakeSend(Response(403, {}, b'{"error":{"code":"PowerBINotAuthorizedException"}}'))
    with pytest.raises(PowerBIError, match="Dataset Execute Queries REST API"):
        client(send).execute_queries(DATASET, 'EVALUATE ROW("ok", 1)')
    send = FakeSend(ok({"results": [{"tables": [{"error": {"code": "DAXQueryFailure", "message": "INFO not supported"}}]}]}))
    with pytest.raises(PowerBIError, match="INFO not supported"):
        client(send).execute_queries(DATASET, "EVALUATE INFO.VIEW.COLUMNS()")


def test_cache_hit_makes_one_call():
    send = FakeSend(ok(REPORT_JSON))
    c = client(send)
    c.get_report(REPORT)
    c.get_report(REPORT)
    assert len(send.calls) == 1


def test_dax_queries_need_workspace_and_use_group_path(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    send = FakeSend(Response(200, {"content-type": "application/vnd.apache.arrow.stream"}, b"\x00"))
    with pytest.raises(PowerBIError, match="workspace_id"):
        client(send).execute_dax_queries(DATASET, "EVALUATE INFO.VIEW.TABLES()", None)
    with pytest.raises(ArrowUnavailable, match="pbi-arrow"):
        client(send, max_rows=10).execute_dax_queries(DATASET, "EVALUATE INFO.VIEW.TABLES()", WS)
    assert send.calls[0]["url"] == f"{BASE}/groups/{WS}/datasets/{DATASET}/executeDaxQueries"
    assert send.calls[0]["body"] == {"query": "EVALUATE INFO.VIEW.TABLES()", "resultSetRowCountLimit": 10}


def test_missing_pyarrow_gives_not_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(ArrowUnavailable, match="not available"):
        parse_arrow(b"", 10)


def test_parse_arrow_rows_and_error_rowset():
    pa = pytest.importorskip("pyarrow")

    def stream(table):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as w:
            w.write_table(table)
        return sink.getvalue().to_pybytes()

    data = pa.table({"[Name]": ["a", "b", "c"]})
    second = pa.table({"[Name]": ["x"]})
    results = parse_arrow(stream(data) + stream(second), max_rows=2)
    assert [r.rows for r in results] == [[{"[Name]": "a"}, {"[Name]": "b"}], [{"[Name]": "x"}]]
    assert results[0].truncated and not results[1].truncated
    err = pa.table({"ErrorCode": ["1"]}).replace_schema_metadata({"IsError": "true", "FaultString": "bad INFO call"})
    with pytest.raises(PowerBIError, match="bad INFO call"):
        parse_arrow(stream(err), 10)
