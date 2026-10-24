"""Column lineage for a single SQL statement, via SQLGlot (sqlglot.lineage.lineage)."""

from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

ALLOWED_DIALECTS = ("redshift", "databricks", "spark")


class LineageError(ValueError):
    pass


class SourceColumn(BaseModel):
    table: str
    column: str
    expression: str


class SqlLineage(BaseModel):
    column: str
    dialect: str
    sources: list[SourceColumn]


def column_lineage(sql: str, column: str, dialect: str) -> SqlLineage:
    dialect = dialect.lower().strip()
    if dialect not in ALLOWED_DIALECTS:
        raise LineageError(f"dialect must be one of {list(ALLOWED_DIALECTS)}, got '{dialect}'")
    try:
        root = lineage(column, sql, {}, dialect=dialect)
    except SqlglotError as e:
        raise LineageError(f"could not trace column '{column}': {e}") from e

    sources: dict[tuple[str, str], SourceColumn] = {}
    for node in root.walk():
        if node.downstream or not isinstance(node.source, exp.Table):
            continue
        t = node.source
        table = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        col = node.name.split(".")[-1]
        sources[(table, col)] = SourceColumn(table=table, column=col, expression=t.sql(dialect=dialect))
    return SqlLineage(column=column, dialect=dialect, sources=sorted(sources.values(), key=lambda s: (s.table, s.column)))
