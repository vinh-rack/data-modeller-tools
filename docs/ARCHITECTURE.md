# Data Modeller Assistant: Architecture (v1)

v1 is local-first. The MCP server runs as a local stdio process on each modeller's machine, and reasoning uses the IDE's own model. The original Azure diagram is treated as a v2 option (Section 4), not the v1 design.

---

## 1. System overview

```mermaid
flowchart TB
    subgraph USER["Modeller machine"]
        direction TB

        subgraph IDE["IDE client: Cursor / Kiro / Claude Desktop / Claude Code"]
            LLM["IDE model (reasoning)"]
            SKILLS["Instruction pack (skills)<br/>build / review / lineage playbooks<br/>checkpoint at every stage"]
        end

        subgraph MCP["Data Modeller MCP server (Python, stdio)"]
            direction TB
            TOOLS["MCP tools (read-only by default)"]

            subgraph CORE["Core"]
                RULES["Rules engine<br/>deterministic checks"]
                INDEX["Model index<br/>all existing SMX tables"]
                KB["Knowledge base<br/>glossary, conventions, report map"]
                LIN["Lineage builder"]
                WRITER["Artifact writer<br/>new files only"]
            end

            subgraph CONN["Connectors"]
                XL["SMX Excel reader"]
                DBT["dbt manifest reader"]
                SQLP["SQL parser (SQLGlot)"]
                RS["Redshift metadata (read-only)"]
                DBX["Databricks metadata (read-only)"]
                PBI["Semantic model sheet reader"]
            end
        end

        OUT[("output/ folder<br/>findings, proposed SMX rows,<br/>lineage, session logs")]
    end

    subgraph EXT["External systems"]
        SMX[("Baseline SMX workbooks")]
        REPO[("dbt repo (git)")]
        REDSHIFT[("Redshift source warehouse")]
        DATABRICKS[("Databricks target platform")]
        SHEET[("Source table sheet<br/>semantic model extract")]
    end

    LLM <--> SKILLS
    LLM <-->|"MCP stdio"| TOOLS
    TOOLS --> RULES
    TOOLS --> INDEX
    TOOLS --> KB
    TOOLS --> LIN
    TOOLS --> WRITER
    RULES --> INDEX
    LIN --> INDEX
    LIN --> DBT
    LIN --> SQLP
    INDEX --> XL
    XL -->|"read"| SMX
    DBT -->|"read"| REPO
    RS -->|"read"| REDSHIFT
    DBX -->|"read"| DATABRICKS
    PBI -->|"read"| SHEET
    WRITER -->|"write"| OUT
```

---

## 2. Build flow (per Power BI report)

Follows the process the architect described on Sept 17. Every stage ends with a human checkpoint.

```mermaid
flowchart TD
    A["1. Scope<br/>report ID, BU, in-scope tables"] --> CP1{"Checkpoint"}
    CP1 --> B["2. Semantic model intake<br/>tables + Power Query transforms from source table sheet"]
    B --> C["3. Source tables<br/>Redshift tables the report points to"]
    C --> D["4. Entity logic<br/>dbt models for those tables"]
    D --> E["5. Bronze identification<br/>from dbt dependencies"]
    E --> R1{"Reuse check<br/>bronze already modelled?"}
    R1 -->|"yes"| REUSE["Reuse existing tables<br/>check missing columns only"]
    R1 -->|"no"| F["6. Silver Staging<br/>close to bronze, minimal joins"]
    F --> CP2{"Checkpoint"}
    CP2 --> G["7. Silver IDM<br/>1:1 with staging + key mapping (party)"]
    G --> CP3{"Checkpoint"}
    CP3 --> R2{"Grain check<br/>same grain exists anywhere?"}
    R2 -->|"same grain, other BU"| CDM["Flag: promote to CDM"]
    R2 -->|"aggregation of existing fact"| VIEW["Propose view, not table"]
    R2 -->|"new grain"| H["8. Gold<br/>keys-only facts, report columns only,<br/>Power Query aggregations moved here"]
    REUSE --> H
    CDM --> H
    VIEW --> H
    H --> CP4{"Checkpoint"}
    CP4 --> I["9. Rules engine pass"]
    I --> J["10. Write proposed SMX rows,<br/>decision log, lineage"]
```

---

## 3. Review session (sequence)

```mermaid
sequenceDiagram
    actor M as Modeller
    participant IDE as IDE model + skills
    participant S as MCP server
    participant R as Rules engine
    participant O as output/

    M->>IDE: Review model X, tables A, B, C
    IDE->>S: get_scope(model X)
    S-->>IDE: tables, layers, approved status
    IDE-->>M: Checkpoint 1: confirm scope
    M->>IDE: Confirm
    IDE->>S: read_model(X) [read-only]
    S-->>IDE: structured SMX
    IDE->>S: run_rules(X, tables)
    S->>R: evaluate deterministic rules
    R-->>S: findings (rule_id, table, column, severity)
    S-->>IDE: findings
    IDE-->>M: Checkpoint 2: structural findings
    M->>IDE: Accept / reject / redirect
    IDE-->>M: Checkpoint 3: semantic findings (grain, reuse, 3NF/dim fit)
    M->>IDE: Accept / reject / redirect
    IDE->>S: write_review(findings, decisions)
    S->>O: findings.md, decisions.json, proposed_changes.xlsx
    S-->>IDE: file paths
    IDE-->>M: Summary + paths
```

---

## 4. v2 option: hosted

Only if the team decides on hosting. The same tool and rules code is reused, exposed over Streamable HTTP. If a Foundry agent becomes a client, note that Foundry Agent Service only accepts remote MCP endpoints, and non-streaming MCP tool calls time out at 50 seconds.

```mermaid
flowchart LR
    subgraph CLIENTS["Clients"]
        C1["Cursor"]
        C2["Kiro"]
        C3["Claude Desktop"]
        C4["Foundry agent (optional)"]
    end

    subgraph AZ["Azure"]
        GW["Auth gateway (Entra ID)"]
        SRV["Same MCP server code<br/>Streamable HTTP transport"]
        SEC["Secrets store"]
    end

    C1 & C2 & C3 & C4 -->|"HTTPS"| GW
    GW --> SRV
    SRV --> SEC
    SRV -->|"read-only"| SYS[("Redshift / Databricks / dbt repo / SMX store")]
```

---

## 5. Design rules

| Rule | Why |
|---|---|
| Deterministic checks live in code, not prompts | Repeatable, testable, auditable |
| The LLM handles judgement and explanation only | Grain, reuse and business meaning stay human + agent |
| Read-only credentials for every source system | Read-only is enforced, not just promised |
| Writer can only write inside `output/` | Baseline SMX is never overwritten |
| Every finding carries rule_id, table, column, severity, evidence | Fully traceable |
| Model index rebuilt from source at session start | Avoids stale reuse detection |
| One rules/KB repo shared by all IDEs | One source of truth |

---

## 6. Sources

- MCP transports and Python SDK: [MCP Python SDK](https://github.com/john0312/mcp-python-sdk)
- Kiro MCP (stdio + remote): [kiro.dev](https://kiro.dev/docs/mcp/index)
- Cursor MCP transports: [cursor.com](https://cursor.com/docs/mcp)
- Claude Desktop local servers: [guide](https://www.host-tracker.com/pt/learn/ai-assistants/connect-mcp-servers-to-claude-desktop)
- dbt lineage via manifest: [OpenMetadata](https://docs.open-metadata.org/connectors/ingestion/workflows/dbt/ingest-dbt-lineage)
- SQLGlot dialects and lineage: [SQLGlot](https://openapps.pro/packages/sqlglot)
- Foundry MCP limits: [Microsoft Learn](https://learn.microsoft.com/en-my/Azure/foundry-classic/agents/how-to/tools-classic/model-context-protocol)