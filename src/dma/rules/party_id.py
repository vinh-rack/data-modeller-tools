"""PARTY-001: data_area_party_id is derived in Silver only, and Gold carries it forward 1:1."""

import re

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "PARTY-001"


@rule(RULE_ID)
def check_party_id(ctx: RuleContext, cfg: dict) -> list[Finding]:
    column = cfg.get("column", "data_area_party_id").lower()
    passthrough = re.compile(cfg.get("passthrough_pattern", r"^\s*(1:1( mapping)?|direct|as is|pk)?\s*$"), re.I)
    findings = []
    for t in ctx.targets:
        if t.layer is not Layer.GOLD:
            continue
        for c in t.columns:
            if c.name.lower() != column:
                continue
            problems = []
            if (c.source_column or "").lower() != column:
                problems.append(f"its source column is {c.source_column or 'empty'}, not {column}")
            if not passthrough.match(c.rules or ""):
                problems.append(f"its rule '{c.rules}' derives it instead of carrying it forward")
            if problems:
                findings.append(Finding(
                    rule_id=RULE_ID, severity=cfg.get("severity", "info"), layer=t.layer, table=t.qualified, column=c.name,
                    message=f"Gold must carry {column} forward 1:1 from Silver, but {' and '.join(problems)}.",
                    evidence={"source_column": c.source_column, "rules": c.rules, "sheet": t.sheet, "row": c.row},
                ))
    return findings
