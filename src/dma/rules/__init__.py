"""Importing this package registers every rule module."""

from dma.rules import fact_keys_only, gold_columns_used, key_suffix, layer_bypass, layer_hierarchy, metadata_columns, naming, one_table_per_grain
from dma.rules.base import REGISTRY, RuleContext, run_rules

__all__ = ["REGISTRY", "RuleContext", "run_rules"]
