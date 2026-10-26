"""Read-only Power BI REST client. Phase 0 auth: a delegated bearer token in PBI_ACCESS_TOKEN.

Every request is checked against an allowlist before anything is sent: GET on report, dataset and datasource
metadata, and POST only to the two DAX query endpoints. DAX must be a metadata-sized EVALUATE query.
The token is never logged and is redacted from every error message.
"""

import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

DEFAULT_BASE = "https://api.powerbi.com/v1.0/myorg"
DEFAULT_MAX_ROWS = 5000
MAX_RETRY_AFTER = 60
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(f"^{UUID}$")
_GROUP = f"(?:groups/{UUID}/)?"
ALLOWED: dict[str, list[re.Pattern[str]]] = {
    "GET": [
        re.compile(f"^{_GROUP}reports/{UUID}$"),
        re.compile(f"^{_GROUP}datasets/{UUID}$"),
        re.compile(f"^{_GROUP}datasets/{UUID}/datasources$"),
    ],
    "POST": [
        re.compile(f"^{_GROUP}datasets/{UUID}/executeQueries$"),
        re.compile(f"^groups/{UUID}/datasets/{UUID}/executeDaxQueries$"),
    ],
}
AUTH_MESSAGE = (
    "Power BI returned 401 Unauthorized. The token is missing, expired or lacks the scope. "
    "Refresh it with Get-PowerBIAccessToken -AsString (MicrosoftPowerBIMgmt), put it in PBI_ACCESS_TOKEN in .env, and reconnect the dma server."
)
TENANT_HINT = "This most likely means the tenant setting 'Dataset Execute Queries REST API' is off, or you lack build permission on the dataset (REQUIREMENTS Q8)."


class PowerBIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PowerBIAuthError(PowerBIError):
    pass


class PowerBIForbiddenCall(PowerBIError):
    """Raised before sending when a method or path is not on the read-only allowlist."""


class ArrowUnavailable(PowerBIError):
    pass


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    truncated: bool


Send = Callable[[str, str, dict[str, str], bytes | None, float], Response]


def _urllib_send(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")
    except urllib.error.URLError as e:
        raise PowerBIError(f"could not reach Power BI: {e.reason}") from None


def check_id(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value.strip()):
        raise PowerBIError(f"{name} must be a GUID like 00000000-0000-0000-0000-000000000000, got {value!r}")
    return value.strip().lower()


def check_dax(query: str) -> str:
    """Only metadata-sized read queries: a plain EVALUATE statement, no comments that could hide anything."""
    q = (query or "").strip()
    if any(marker in q for marker in ("--", "//", "/*")):
        raise PowerBIError("DAX comments are not allowed in metadata queries")
    if not re.match(r"(?i)^EVALUATE\b", q):
        raise PowerBIError("only DAX queries that start with EVALUATE are allowed")
    return q


def _retry_after(headers: Mapping[str, str]) -> float | None:
    try:
        return min(max(float(headers.get("retry-after", "")), 0.0), MAX_RETRY_AFTER)
    except ValueError:
        return None


def _error_text(body: bytes) -> str:
    try:
        err = json.loads(body).get("error") or {}
        text = " ".join(str(err.get(k)) for k in ("code", "message") if err.get(k))
        if text:
            return text
    except (ValueError, AttributeError):
        pass
    return body[:300].decode("utf-8", errors="replace")


def cell(row: Mapping[str, Any], name: str) -> Any:
    """Read a DAX result column whether it comes back as name, [name] or Table[name]."""
    for k, v in row.items():
        if k == name or k.endswith(f"[{name}]"):
            return v
    return None


class PowerBIClient:
    def __init__(self, token: str | None, base: str = DEFAULT_BASE, max_rows: int = DEFAULT_MAX_ROWS, send: Send | None = None, sleep: Callable[[float], None] = time.sleep, timeout: float = 60):
        # Get-PowerBIAccessToken -AsString already includes the "Bearer " prefix, so accept it with or without.
        token = re.sub(r"(?i)^bearer(\s+|$)", "", (token or "").strip()).strip()
        if not token:
            raise PowerBIError("PBI_ACCESS_TOKEN is not set. Get one with Get-PowerBIAccessToken -AsString and put it in .env.")
        if urlsplit(base).scheme != "https":
            raise PowerBIError(f"PBI_API_BASE must be an https URL, got {base!r}")
        self._token = token
        self.base = base.rstrip("/")
        self.max_rows = max_rows
        self._send = send or _urllib_send
        self._sleep = sleep
        self.timeout = timeout
        self._cache: dict[tuple[str, str, bytes | None], Response] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kwargs: Any) -> "PowerBIClient":
        env = os.environ if env is None else env
        rows = env.get("PBI_MAX_ROWS", "").strip()
        return cls(env.get("PBI_ACCESS_TOKEN"), base=env.get("PBI_API_BASE", "").strip() or DEFAULT_BASE, max_rows=int(rows) if rows else DEFAULT_MAX_ROWS, **kwargs)

    def __repr__(self) -> str:
        return f"PowerBIClient(base={self.base!r})"

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "[redacted]")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response:
        method = method.upper()
        if not any(p.match(path) for p in ALLOWED.get(method, [])):
            raise PowerBIForbiddenCall(f"{method} {path} is not on the read-only allowlist")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        key = (method, path, data)
        if key in self._cache:
            return self._cache[key]
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json, application/vnd.apache.arrow.stream"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.base}/{path}"
        resp = self._send(method, url, headers, data, self.timeout)
        if resp.status == 429:
            wait = _retry_after(resp.headers)
            if wait is not None:
                self._sleep(wait)
                resp = self._send(method, url, headers, data, self.timeout)
        if resp.status == 401:
            raise PowerBIAuthError(AUTH_MESSAGE, status=401)
        if resp.status == 429:
            raise PowerBIError("Power BI returned 429 Too Many Requests and Retry-After was missing or did not help. Wait a minute and try again.", status=429)
        if resp.status >= 400:
            raise PowerBIError(self._redact(f"{method} {path} returned HTTP {resp.status}: {_error_text(resp.body)}"), status=resp.status)
        self._cache[key] = resp
        return resp

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.request(method, path, body)
        try:
            return json.loads(resp.body)
        except ValueError as e:
            raise PowerBIError(f"{method} {path} did not return JSON") from e

    @staticmethod
    def _prefix(workspace_id: str | None) -> str:
        return f"groups/{check_id('workspace_id', workspace_id)}/" if workspace_id else ""

    def get_report(self, report_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        return self._json("GET", f"{self._prefix(workspace_id)}reports/{check_id('report_id', report_id)}")

    def get_dataset(self, dataset_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        return self._json("GET", f"{self._prefix(workspace_id)}datasets/{check_id('dataset_id', dataset_id)}")

    def get_datasources(self, dataset_id: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return self._json("GET", f"{self._prefix(workspace_id)}datasets/{check_id('dataset_id', dataset_id)}/datasources").get("value", [])

    def resolve_report(self, report_id: str, workspace_id: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
        """Get the report and check its dataset (FR-P2). Paginated reports have no dataset and are rejected."""
        wanted = check_id("dataset_id", dataset_id) if dataset_id else None
        report = self.get_report(report_id, workspace_id)
        if report.get("reportType") == "PaginatedReport" or not report.get("datasetId"):
            raise PowerBIError(f"report {report_id} is a paginated report or has no dataset. Only Power BI reports with a semantic model are supported.")
        if wanted and wanted != str(report["datasetId"]).lower():
            raise PowerBIError(f"dataset mismatch: report {report_id} uses dataset {report['datasetId']}, but dataset_id {dataset_id} was given. Stop and confirm the IDs with the modeller.")
        return report

    def execute_queries(self, dataset_id: str, query: str, workspace_id: str | None = None) -> QueryResult:
        """Classic endpoint: JSON rows, DAX only (no INFO functions). One table per query."""
        path = f"{self._prefix(workspace_id)}datasets/{check_id('dataset_id', dataset_id)}/executeQueries"
        body = {"queries": [{"query": check_dax(query)}], "serializerSettings": {"includeNulls": True}}
        try:
            data = self._json("POST", path, body)
        except PowerBIError as e:
            if e.status in (400, 403):
                raise PowerBIError(f"{e} {TENANT_HINT}", status=e.status) from None
            raise
        result = (data.get("results") or [{}])[0]
        err = data.get("error") or result.get("error") or ((result.get("tables") or [{}])[0].get("error"))
        if err:
            raise PowerBIError(self._redact(f"DAX query failed: {err.get('code', '')} {err.get('message', '')}".strip()))
        rows = ((result.get("tables") or [{}])[0]).get("rows") or []
        return QueryResult(rows=rows[: self.max_rows], truncated=len(rows) > self.max_rows)

    def execute_dax_queries(self, dataset_id: str, query: str, workspace_id: str) -> list[QueryResult]:
        """Arrow endpoint (group path only): supports INFO functions and several EVALUATE statements, one result per statement."""
        if not workspace_id:
            raise PowerBIError("Execute DAX Queries In Group needs a workspace_id; the endpoint has no My workspace path")
        path = f"{self._prefix(workspace_id)}datasets/{check_id('dataset_id', dataset_id)}/executeDaxQueries"
        resp = self.request("POST", path, {"query": check_dax(query), "resultSetRowCountLimit": self.max_rows})
        return parse_arrow(resp.body, self.max_rows)


def parse_arrow(body: bytes, max_rows: int) -> list[QueryResult]:
    """Read concatenated Arrow IPC streams. Query errors arrive as HTTP 200 with IsError=true in the schema metadata."""
    try:
        import pyarrow as pa
    except ImportError:
        raise ArrowUnavailable("not available: the Arrow route needs pyarrow. Install it with uv sync --extra pbi-arrow.") from None
    stream = io.BytesIO(body)
    results = []
    while stream.tell() < len(body):
        try:
            reader = pa.ipc.open_stream(stream)
            table = reader.read_all()
        except pa.ArrowInvalid:
            break
        meta = {k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()}
        if meta.get("IsError") == "true":
            raise PowerBIError(f"DAX query failed [{meta.get('FaultCode', '')}]: {meta.get('FaultString', '')}")
        results.append(QueryResult(rows=table.slice(0, max_rows).to_pylist(), truncated=table.num_rows > max_rows))
    return results
