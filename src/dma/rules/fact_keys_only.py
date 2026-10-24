"""FACT-001: Gold fact tables hold keys and measures only, no descriptive dimension attributes."""

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "FACT-001"


@rule(RULE_ID)
def check_fact_keys_only(ctx: RuleContext, cfg: dict) -> list[Finding]:
    prefixes = tuple(cfg.get("fact_prefixes", ["f_"]))
    suffixes = tuple(cfg.get("descriptive_suffixes", []))
    findings = []
    for t in ctx.targets:
        if t.layer is not Layer.GOLD or not t.name.lower().startswith(prefixes):
            continue
        for c in t.columns:
            if c.is_pk or c.is_fk:
                continue
            matched = next((s for s in suffixes if c.name.lower().endswith(s)), None)
            if matched:
                findings.append(Finding(
                    rule_id=RULE_ID, severity=cfg.get("severity", "error"), layer=t.layer, table=t.qualified, column=c.name,
                    message=f"Fact table carries descriptive attribute {c.name}. Keep it on the dimension and join in the semantic model.",
                    evidence={"matched_suffix": matched, "source_table": c.source_table, "sheet": t.sheet, "row": c.row},
                ))
    return findings
