"""META-001: Silver tables carry the metadata columns c_created_ts, c_updated_ts and c_source_system (conventions A3.1)."""

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "META-001"


@rule(RULE_ID)
def check_metadata_columns(ctx: RuleContext, cfg: dict) -> list[Finding]:
    layers = {Layer(x) for x in cfg.get("layers", [])}
    required = [c.lower() for c in cfg.get("required_columns", [])]
    findings = []
    for t in ctx.targets:
        if t.layer not in layers:
            continue
        have = {c.name.lower() for c in t.columns}
        missing = [c for c in required if c not in have]
        if missing:
            findings.append(Finding(
                rule_id=RULE_ID, severity=cfg.get("severity", "warning"), layer=t.layer, table=t.qualified,
                message=f"Missing metadata column(s): {', '.join(missing)}.",
                evidence={"missing": missing, "sheet": t.sheet, "row": t.row},
            ))
    return findings
