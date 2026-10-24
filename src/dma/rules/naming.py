"""NAME-001: snake_case table and column names, Gold prefixes, view suffix, forbidden suffixes (conventions B2, B3)."""

import re

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "NAME-001"


@rule(RULE_ID)
def check_naming(ctx: RuleContext, cfg: dict) -> list[Finding]:
    snake = re.compile(cfg.get("snake_case_regex", r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"))
    gold_prefixes = tuple(cfg.get("gold_table_prefixes", []))
    view_suffix = cfg.get("view_suffix", "_vw")
    bad_view_prefix = cfg.get("forbidden_view_prefix", "vw_")
    bad_suffixes = tuple(cfg.get("forbidden_table_suffixes", []))
    severity = cfg.get("severity", "warning")
    findings = []

    def add(t, message, column=None, **evidence):
        findings.append(Finding(
            rule_id=RULE_ID, severity=severity, layer=t.layer, table=t.qualified, column=column,
            message=message, evidence={"sheet": t.sheet, "row": t.row, **evidence},
        ))

    for t in ctx.targets:
        name = t.name
        if not snake.match(name):
            add(t, f"Table name {name} is not snake_case.", check="table_snake_case")
        if bad_view_prefix and name.lower().startswith(bad_view_prefix):
            add(t, f"View prefix {bad_view_prefix} used. Put the view marker at the end ({view_suffix}).", check="view_suffix")
        if bad_suffixes and name.lower().endswith(bad_suffixes):
            add(t, f"Table name {name} ends with a forbidden suffix {list(bad_suffixes)}. Name it after the business concept.", check="forbidden_suffix")
        if t.layer is Layer.GOLD and gold_prefixes and not name.lower().startswith(gold_prefixes):
            add(t, f"Gold table {name} has none of the prefixes {list(gold_prefixes)}.", check="gold_prefix")
        for c in t.columns:
            if not snake.match(c.name):
                add(t, f"Column name {c.name} is not snake_case.", column=c.name, check="column_snake_case", column_row=c.row)
    return findings
