"""Live Phase 0 probe of the Power BI REST calls (ARCHITECTURE section 2). Read-only: it only uses the allowlisted client.

Run: uv run --extra pbi-arrow python scripts/pbi_probe.py
Needs in .env or the environment: PBI_ACCESS_TOKEN, PBI_REPORT_ID, PBI_DATASET_ID, and optionally PBI_WORKSPACE_ID.
Prints PASS or FAIL per call, then answers for Q8, Q9, Q10 and whether group paths work for My workspace.
It prints IDs, status codes, counts and table names only, never the token or data values. Never run it from pytest.
"""

import os
import sys
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from dma.connectors.powerbi import CLASSIC_QUERY, INFO_QUERY, PowerBIClient, PowerBIError, cell, check_id, parse_arrow

ROOT = Path(__file__).resolve().parents[1]
# The classic endpoint docs say INFO functions are not supported. INFO.VIEW.* are documented as usable in calculated tables, so this checks whether they still work there.
CLASSIC_INFO_VIEW_QUERY = 'EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "column", [Name])'


class Probe:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.ok: dict[str, bool] = {}

    def step(self, key: str, call: str, fn: Callable[[], str]) -> bool:
        try:
            detail, passed = fn(), True
        except PowerBIError as e:
            detail, passed = str(e), False
        self.rows.append((key, call, "PASS" if passed else "FAIL", detail))
        self.ok[key] = passed
        return passed

    def skip(self, key: str, call: str, why: str) -> None:
        self.rows.append((key, call, "SKIP", why))

    def print(self) -> None:
        width = max(len(r[1]) for r in self.rows)
        for key, call, result, detail in self.rows:
            print(f"{key:<3} {call:<{width}}  {result:<4}  {detail}")


def main() -> int:
    load_dotenv(ROOT / ".env")
    try:
        report_id = check_id("PBI_REPORT_ID", os.environ.get("PBI_REPORT_ID"))
        dataset_id = check_id("PBI_DATASET_ID", os.environ.get("PBI_DATASET_ID"))
        ws = os.environ.get("PBI_WORKSPACE_ID", "").strip()
        workspace_id = check_id("PBI_WORKSPACE_ID", ws) if ws else None
        client = PowerBIClient.from_env()
    except PowerBIError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1

    p = Probe()
    print(f"report {report_id}, dataset {dataset_id}, workspace {workspace_id or '(none, My workspace paths)'}\n")

    def report(group: str | None) -> Callable[[], str]:
        def run() -> str:
            r = client.get_report(report_id, group)
            if r.get("datasetId", "").lower() != dataset_id:
                raise PowerBIError(f"report datasetId {r.get('datasetId')} does not match PBI_DATASET_ID")
            return f"name={r.get('name')!r}, reportType={r.get('reportType')}, datasetId matches"
        return run

    p.step("1", "GET reports/{id}", report(None))
    if workspace_id:
        p.step("1b", "GET groups/{ws}/reports/{id}", report(workspace_id))
    else:
        p.skip("1b", "GET groups/{ws}/reports/{id}", "set PBI_WORKSPACE_ID to test group paths for My workspace")

    p.step("2", "GET datasets/{id}", lambda: f"name={client.get_dataset(dataset_id).get('name')!r}")

    def sources(group: str | None) -> Callable[[], str]:
        def run() -> str:
            items = client.get_datasources(dataset_id, group)
            return f"{len(items)} source(s): " + ", ".join(f"{s.get('datasourceType')} {(s.get('connectionDetails') or {}).get('server', '')}/{(s.get('connectionDetails') or {}).get('database', '')}" for s in items)
        return run

    p.step("3", "GET datasets/{id}/datasources", sources(None))
    if workspace_id:
        p.step("3b", "GET groups/{ws}/datasets/{id}/datasources", sources(workspace_id))

    def evaluate_ok() -> str:
        r = client.execute_queries(dataset_id, 'EVALUATE ROW("ok", 1)', workspace_id)
        return f"{len(r.rows)} row returned"

    p.step("4", "POST executeQueries ROW(ok, 1)", evaluate_ok)

    def columns() -> str:
        r = client.execute_queries(dataset_id, CLASSIC_QUERY, workspace_id)
        tables = sorted({str(cell(x, "table")) for x in r.rows})
        return f"{len(r.rows)} columns in {len(tables)} tables: {', '.join(tables)}" + (" (truncated)" if r.truncated else "")

    p.step("5", "POST executeQueries COLUMNSTATISTICS names", columns)
    def info_view_classic() -> str:
        r = client.execute_queries(dataset_id, CLASSIC_INFO_VIEW_QUERY, workspace_id)
        return f"{len(r.rows)} rows, so INFO.VIEW works on the classic endpoint"

    p.step("5b", "POST executeQueries INFO.VIEW.COLUMNS()", info_view_classic)

    def info() -> str:
        resp = client.request("POST", f"groups/{workspace_id}/datasets/{dataset_id}/executeDaxQueries", {"query": INFO_QUERY, "resultSetRowCountLimit": client.max_rows})
        head = f"HTTP {resp.status}, {resp.content_type or 'no content-type'}, {len(resp.body)} bytes"
        try:
            results = parse_arrow(resp.body, client.max_rows)
        except PowerBIError as e:
            raise PowerBIError(f"{head}; {e}") from None
        if not results or not results[0].rows:
            raise PowerBIError(f"{head}; no tables came back, so the INFO route gives nothing usable")
        counts = [len(r.rows) for r in results] + [0] * 4
        with_m = sum(1 for r in results[3].rows if cell(r, "query_definition")) if len(results) > 3 else 0
        measures_with_dax = sum(1 for r in results[2].rows if cell(r, "expression")) if len(results) > 2 else 0
        return f"{head}; {counts[0]} tables, {counts[1]} columns, {counts[2]} measures ({measures_with_dax} with DAX), {counts[3]} partitions ({with_m} with M)"

    if workspace_id:
        p.step("6", "POST groups/{ws}/datasets/{id}/executeDaxQueries INFO", info)
    else:
        p.skip("6", "POST groups/{ws}/datasets/{id}/executeDaxQueries INFO", "needs PBI_WORKSPACE_ID: the endpoint has no My workspace path")

    p.print()
    print()
    print(f"Q8  tenant setting 'Dataset Execute Queries REST API' on: {'yes' if p.ok.get('4') else 'no or no build permission, see call 4'}")
    q9 = p.ok.get("6")
    print(f"Q9  INFO route returns metadata: {'yes, see call 6 for M coverage' if q9 else 'not tested' if q9 is None else 'no, see call 6'}")
    in_group = "" if p.ok.get("3b") is None else f", group path {'yes' if p.ok['3b'] else 'no, see call 3b'}"
    print(f"Q10 datasources call works: {'yes' if p.ok.get('3') else 'no, see call 3'}{in_group} (in My workspace you own the dataset, so a pass does not prove read access alone is enough)")
    g = p.ok.get("1b")
    print(f"Group paths work for My workspace: {'yes, groups/{ws}/... can always be used' if g else 'not tested' if g is None else 'no, keep the My workspace paths'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
