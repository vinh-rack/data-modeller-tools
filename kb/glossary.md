# Glossary

Template. Replace it with the team's glossary, or point `DMA_KB_DIR` at a folder that holds it. These entries restate terms already used in `docs/REQUIREMENTS.md`.

- **Bronze** - raw tables landed from source systems, schema prefix `brnz_`.
- **Silver Staging** - typed, renamed copies of Bronze with natural keys (`_num`), schema `slvr_stg`.
- **Silver IDM** - integrated data model with hash keys (`_id`), schema `slvr_cdm` or `slvr_<bu>`.
- **Gold** - report-facing semantic data model, schema `gold_<bu>`. Facts `f_`, dimensions `d_`, aggregates `agg_`, lookups `lkp_`, views end `_vw`.
- **BU** - business unit, taken from the schema name.
- **Grain** - the set of primary key columns of a table.
- **CDM promotion** - the same grain in two BUs, a candidate for one shared table.
- **SMX** - the Excel source-to-target mapping workbook, one sheet per layer.
