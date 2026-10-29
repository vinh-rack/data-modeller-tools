# Conventions

Template. Replace it with the team's formatting and naming conventions, or point `DMA_KB_DIR` at a folder that holds them. The deterministic checks below run in code through `run_rules`; this file is for explaining them, never for re-deriving them.

| Rule | Check | Rule id |
| --- | --- | --- |
| RULE-1 | A table sources only from its own layer or upstream | LAYER-001 |
| RULE-2 | No layer bypass (off until Q2 is answered) | LAYER-002 |
| RULE-3 | Gold facts hold keys and measures only | FACT-001 |
| RULE-4 | snake_case, Gold prefixes, `_vw` view suffix, no `_lookup` or `_enum` | NAME-001 |
| RULE-5 | Every Gold column is used by the target report | GOLD-001 |
| RULE-6 | One table per grain and BU | GRAIN-001 |
| RULE-7 | An aggregate of an existing fact is a view | GRAIN-002 |
| RULE-8 | `_num` for natural keys, `_id` for hash keys | KEY-001 |
| RULE-9 | `c_created_ts`, `c_updated_ts`, `c_source_system` on Silver | META-001 |
| RULE-10 | Load behaviour documented (needs an SMX column, see Q1) | not built |
| RULE-11 | `data_area_party_id` carried forward 1:1 in Gold | PARTY-001 |
