"""GOLD-001: every Gold column is used by the target report's semantic model."""

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "GOLD-001"


@rule(RULE_ID)
def check_gold_columns_used(ctx: RuleContext, cfg: dict) -> list[Finding]:
    gold = [t for t in ctx.targets if t.layer is Layer.GOLD]
    if not gold:
        return []
    if ctx.column_usage is None:
        return [Finding(
            rule_id=RULE_ID, severity="info", layer=Layer.GOLD, table="*",
            message="Skipped: no semantic model column usage list was provided.",
            evidence={"gold_tables": [t.qualified for t in gold], "status": "skipped"},
        )]
    usage = {u.strip().lower() for u in ctx.column_usage}
    findings = []
    for t in gold:
        for c in t.columns:
            keys = {f"{t.qualified}.{c.name}".lower(), f"{t.name}.{c.name}".lower()}
            if usage.isdisjoint(keys):
                findings.append(Finding(
                    rule_id=RULE_ID, severity=cfg.get("severity", "warning"), layer=t.layer, table=t.qualified, column=c.name,
                    message=f"Gold column {c.name} is not used by the report semantic model. Remove it unless a report needs it.",
                    evidence={"sheet": t.sheet, "row": c.row, "usage_entries": len(usage)},
                ))
    return findings
