"""KEY-001: `_id` for hash or surrogate keys, `_num` for natural keys (conventions B3.3).

The SMX has no key-type column, so the key type is inferred: PKs in natural-key layers (Silver Staging) are natural keys, PKs in hash-key layers (Silver IDM, Gold) are hash keys, and any column whose rule text is a hash is a hash key.
"""

import re

from dma.models import Finding, Layer
from dma.rules.base import RuleContext, rule

RULE_ID = "KEY-001"


@rule(RULE_ID)
def check_key_suffix(ctx: RuleContext, cfg: dict) -> list[Finding]:
    natural_suffix = cfg.get("natural_key_suffix", "_num")
    hash_suffix = cfg.get("hash_key_suffix", "_id")
    natural_layers = {Layer(x) for x in cfg.get("natural_key_layers", [])}
    hash_layers = {Layer(x) for x in cfg.get("hash_key_layers", [])}
    hash_rule = re.compile(cfg.get("hash_rule_pattern", r"\b(hash|md5|sha\d*)\s*\("), re.I)
    findings = []
    for t in ctx.targets:
        for c in t.columns:
            name = c.name.lower()
            if hash_rule.search(c.rules or ""):
                want, why = hash_suffix, "its rule is a hash"
            elif c.is_pk and t.layer in natural_layers:
                want, why = natural_suffix, f"it is a primary key in {t.layer.value}, so a natural key"
            elif c.is_pk and t.layer in hash_layers:
                want, why = hash_suffix, f"it is a primary key in {t.layer.value}, so a hash key"
            else:
                continue
            other = natural_suffix if want == hash_suffix else hash_suffix
            # Only a key that carries the other suffix is flagged. PK parts like booking_month are not keys by name.
            if name.endswith(other):
                findings.append(Finding(
                    rule_id=RULE_ID, severity=cfg.get("severity", "warning"), layer=t.layer, table=t.qualified, column=c.name,
                    message=f"Key {c.name} should end {want}: {why}.",
                    evidence={"expected_suffix": want, "basis": why, "rules": c.rules, "sheet": t.sheet, "row": c.row},
                ))
    return findings
