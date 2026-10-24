"""LAYER-001: a table sources only from its own layer or upstream (Bronze < Silver Staging < Silver IDM < Gold)."""

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, column_for_source, rule

RULE_ID = "LAYER-001"


@rule(RULE_ID)
def check_layer_hierarchy(ctx: RuleContext, cfg: dict) -> list[Finding]:
    findings = []
    for t in ctx.targets:
        if t.layer is Layer.BRONZE:
            continue
        for src in t.sources:
            src_layer = ctx.index.classifier.layer_of_source(src)
            evidence = {"source": src.qualified, "source_system": src.source_system, "table_layer": t.layer.value, "sheet": t.sheet, "row": t.row}
            if src_layer is None:
                findings.append(Finding(
                    rule_id=RULE_ID, severity="info", layer=t.layer, table=t.qualified,
                    column=column_for_source(t, src.qualified),
                    message=f"Source {src.qualified} has no known layer, hierarchy not checked.",
                    evidence={**evidence, "status": "unresolved"},
                ))
            elif src_layer.order > t.layer.order:
                findings.append(Finding(
                    rule_id=RULE_ID, severity=cfg.get("severity", "error"), layer=t.layer, table=t.qualified,
                    column=column_for_source(t, src.qualified),
                    message=f"{t.layer.value} table sources from downstream layer {src_layer.value} ({src.qualified}).",
                    evidence={**evidence, "source_layer": src_layer.value},
                ))
    return findings
