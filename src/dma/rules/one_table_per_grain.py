"""GRAIN-001: one table per grain and BU. GRAIN-002: a table that only aggregates an existing fact should be a view."""

import re

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule


@rule("GRAIN-001")
def check_one_table_per_grain(ctx: RuleContext, cfg: dict) -> list[Finding]:
    require_bronze = cfg.get("require_shared_bronze", False)
    findings = []
    reported: set[frozenset[str]] = set()
    for t in ctx.targets:
        if t.layer is Layer.BRONZE:
            continue
        if not t.grain:
            findings.append(Finding(
                rule_id="GRAIN-001", severity="info", layer=t.layer, table=t.qualified,
                message="No PK columns flagged, so the grain is undeclared and duplicates cannot be checked.",
                evidence={"sheet": t.sheet, "row": t.row, "status": "no_grain"},
            ))
            continue
        t_bronze = set(ctx.index.bronze_sources(t)) if require_bronze else set()
        for other in ctx.index.tables:
            if other is t or other.layer is not t.layer or other.grain != t.grain:
                continue
            pair = frozenset({f"{t.model}:{t.qualified}", f"{other.model}:{other.qualified}"})
            if pair in reported:
                continue
            o_bronze = set(ctx.index.bronze_sources(other))
            if require_bronze and t_bronze.isdisjoint(o_bronze):
                continue
            reported.add(pair)
            same_bu = t.bu == other.bu
            evidence = {
                "grain": t.grain, "other_table": other.qualified, "other_model": other.model,
                "bu": t.bu, "other_bu": other.bu,
                "bronze": ctx.index.bronze_sources(t), "other_bronze": sorted(o_bronze),
            }
            if same_bu:
                message = f"Same grain {t.grain} and BU ({t.bu or 'unknown'}) as {other.qualified}. Keep one table per grain."
            else:
                message = f"Same grain {t.grain} as {other.qualified} in BU {other.bu or 'unknown'}. Possible CDM promotion."
            findings.append(Finding(
                rule_id="GRAIN-001",
                severity=cfg.get("severity_same_bu", "error") if same_bu else cfg.get("severity_cross_bu", "warning"),
                layer=t.layer, table=t.qualified, message=message, evidence=evidence,
            ))
    return findings


@rule("GRAIN-002")
def check_aggregate_should_be_view(ctx: RuleContext, cfg: dict) -> list[Finding]:
    fact_prefixes = tuple(cfg.get("fact_prefixes", ["f_"]))
    view_suffix = cfg.get("view_suffix", "_vw")
    patterns = [re.compile(p, re.I) for p in cfg.get("aggregation_patterns", [])]
    findings = []
    for t in ctx.targets:
        if t.layer is not Layer.GOLD or not t.sources or t.name.lower().endswith(view_suffix):
            continue
        parents = set()
        for src in t.sources:
            facts = [p for p in ctx.index.resolve(src.qualified) if p.layer is Layer.GOLD and p.name.lower().startswith(fact_prefixes) and p is not t]
            if not facts:
                parents = set()
                break
            parents.update(p.qualified for p in facts)
        if len(parents) != 1:
            continue
        text = "\n".join([t.rules_text] + [c.rules or "" for c in t.columns])
        hit = next((p.pattern for p in patterns if p.search(text)), None)
        if hit:
            findings.append(Finding(
                rule_id="GRAIN-002", severity=cfg.get("severity", "warning"), layer=t.layer, table=t.qualified,
                message=f"Table only aggregates existing fact {next(iter(parents))}. It should be a view ({view_suffix}), not a table.",
                evidence={"parent_fact": next(iter(parents)), "matched_pattern": hit, "sheet": t.sheet, "row": t.row},
            ))
    return findings
