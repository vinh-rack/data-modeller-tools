"""Rule protocol, registry and runner. Rules are plain functions registered with @rule."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from dma.index.model_index import ModelIndex
from dma.models import Finding, Table

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class RuleContext:
    index: ModelIndex
    targets: list[Table]
    config: dict[str, Any]
    column_usage: set[str] | None = None


class Rule(Protocol):
    rule_id: str

    def __call__(self, ctx: RuleContext, cfg: dict[str, Any]) -> list[Finding]: ...


REGISTRY: dict[str, Rule] = {}


def rule(rule_id: str) -> Callable[[Callable], Rule]:
    def deco(fn):
        if rule_id in REGISTRY:
            raise ValueError(f"duplicate rule_id {rule_id}")
        fn.rule_id = rule_id
        REGISTRY[rule_id] = fn
        return fn
    return deco


def rule_config(config: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return config.get("rules", {}).get(rule_id, {})


def run_rules(ctx: RuleContext, only: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule_id, fn in sorted(REGISTRY.items()):
        cfg = rule_config(ctx.config, rule_id)
        if only and rule_id not in only:
            continue
        if not cfg.get("enabled", True):
            continue
        findings.extend(fn(ctx, cfg))
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule_id, f.table, f.column or ""))
    return findings


def column_for_source(table: Table, source_qualified: str) -> str | None:
    """First column whose source table cell mentions the source, for evidence."""
    needle = source_qualified.lower().split(".")[-1]
    for c in table.columns:
        if c.source_table and needle in c.source_table.lower():
            return c.name
    return None
