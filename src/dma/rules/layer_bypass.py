"""LAYER-002: no layer bypass. Gold sources only Silver IDM or Gold, Silver IDM only Silver Staging or Silver IDM (conventions A1.2).

Off by default in rules.yaml until REQUIREMENTS Q2 confirms it is a hard rule. Downstream sources are LAYER-001's job, and unresolved sources are reported there too.
"""

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, column_for_source, rule

RULE_ID = "LAYER-002"


@rule(RULE_ID)
def check_layer_bypass(ctx: RuleContext, cfg: dict) -> list[Finding]:
    allowed = {Layer(k): {Layer(v) for v in vs} for k, vs in cfg.get("allowed_sources", {}).items()}
    findings = []
    for t in ctx.targets:
        if t.layer not in allowed:
            continue
        for src in t.sources:
            src_layer = ctx.index.classifier.layer_of_source(src)
            if src_layer is None or src_layer.order > t.layer.order or src_layer in allowed[t.layer]:
                continue
            findings.append(Finding(
                rule_id=RULE_ID, severity=cfg.get("severity", "error"), layer=t.layer, table=t.qualified,
                column=column_for_source(t, src.qualified),
                message=f"{t.layer.value} table sources {src.qualified} from {src_layer.value}, skipping a layer. Allowed: {', '.join(sorted(l.value for l in allowed[t.layer]))}.",
                evidence={"source": src.qualified, "source_layer": src_layer.value, "table_layer": t.layer.value, "sheet": t.sheet, "row": t.row},
            ))
    return findings
