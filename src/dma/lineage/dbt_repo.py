"""Static reader for a local dbt repository (REQUIREMENTS D3, FR-K4). Reads files as code only.

No dbt command runs and no Jinja is rendered: dbt_project.yml and the model-path YAML are read with yaml.safe_load,
and model SQL is read as text.
"""

import re
from pathlib import Path
from collections import deque
from typing import Any, Literal

import sqlglot
import yaml
from pydantic import BaseModel, Field
from sqlglot.errors import SqlglotError

from dma.lineage.dbt_models import BronzeSource, DbtUpstream, Unresolved

PROJECT_FILE = "dbt_project.yml"
# alias='x' or alias="x" inside a {{ config(...) }} call. Only literal strings are read.
_CONFIG_CALL = re.compile(r"\{\{-?\s*config\s*\((?P<args>.*?)\)\s*-?\}\}", re.S)
_ALIAS_ARG = re.compile(r"""\balias\s*=\s*(['"])(?P<alias>[^'"]+)\1""")
# ref('m'), ref("m"), ref('pkg', 'm'), ref('m', v=2), ref('m', version=2). Only literal arguments; anything else stays unresolved.
_REF = re.compile(r"""\bref\s*\(\s*(['"])(?P<a>[^'"]+)\1\s*(?:,\s*(['"])(?P<b>[^'"]+)\3\s*)?(?:,\s*(?:v|version)\s*=\s*['"]?(?P<v>\d+)['"]?\s*)?\)""")
_SOURCE = re.compile(r"""\bsource\s*\(\s*(['"])(?P<s>[^'"]+)\1\s*,\s*(['"])(?P<t>[^'"]+)\3\s*\)""")
_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_INCREMENTAL = re.compile(r"\{%-?\s*if\s+is_incremental\s*\(\s*\)\s*-?%\}.*?\{%-?\s*endif\s*-?%\}", re.S)
_EXPR = re.compile(r"\{\{-?(?P<body>.*?)-?\}\}", re.S)
_TAG = re.compile(r"\{%-?(?P<body>.*?)-?%\}", re.S)


class DbtRepoError(ValueError):
    pass


class DbtColumn(BaseModel):
    name: str
    description: str | None = None


class DbtModel(BaseModel):
    name: str
    path: str = Field(description="File path relative to the repo root")
    alias: str | None = None
    description: str | None = None
    columns: list[DbtColumn] = Field(default_factory=list)
    versioned_as: str | None = Field(default=None, description="Model name this file is a version of, from YAML versions")
    version: int | None = None

    @property
    def relation(self) -> str:
        """Relation identifier: the alias, else the file name. The schema depends on the target, so it is not included."""
        return self.alias or self.name


class DbtSourceTable(BaseModel):
    source: str
    table: str
    schema_name: str
    identifier: str
    description: str | None = None
    path: str

    @property
    def relation(self) -> str:
        return f"{self.schema_name}.{self.identifier}"


class DbtSeed(BaseModel):
    name: str
    path: str


class DbtRef(BaseModel):
    name: str
    package: str | None = None
    version: int | None = None
    line: int


class DbtSourceRef(BaseModel):
    source: str
    table: str
    line: int


class RenderedModel(BaseModel):
    name: str
    path: str
    raw_sql: str
    rendered_sql: str = Field(description="SQL with ref() and source() replaced by relation names and other Jinja removed or replaced by placeholders. Never executed.")
    refs: list[DbtRef]
    sources: list[DbtSourceRef]
    unresolved: list[Unresolved]
    parses: bool
    parse_error: str | None = None


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _blank(text: str) -> str:
    """Keep only the newlines of removed text, so line numbers in the rest of the file do not move."""
    return "\n" * text.count("\n")


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


class DbtRepo:
    def __init__(self, root: Path, project: dict[str, Any], models: dict[str, DbtModel], sources: dict[tuple[str, str], DbtSourceTable], seeds: dict[str, DbtSeed], versions: dict[str, dict[int, str]], latest: dict[str, int], problems: list[str]):
        self.root = root
        self.project = project
        self.models = models
        self.sources = sources
        self.seeds = seeds
        self.versions = versions
        self.latest = latest
        self.problems = problems

    @property
    def name(self) -> str | None:
        return self.project.get("name")

    @classmethod
    def load(cls, root: Path) -> "DbtRepo":
        root = Path(root).resolve()
        project_file = root / PROJECT_FILE
        if not project_file.is_file():
            raise DbtRepoError(f"no {PROJECT_FILE} in {root}. Set DMA_DBT_REPO to the root of a local dbt clone.")
        project = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
        problems: list[str] = []
        model_dirs = cls._dirs(root, project.get("model-paths") or ["models"], problems)
        seed_dirs = cls._dirs(root, project.get("seed-paths") or ["seeds"], problems)

        models: dict[str, DbtModel] = {}
        for f in cls._files(root, model_dirs, ".sql"):
            sql = f.read_text(encoding="utf-8")
            config_alias = next((m.group("alias") for c in _CONFIG_CALL.finditer(sql) if (m := _ALIAS_ARG.search(c.group("args")))), None)
            models[f.stem.lower()] = DbtModel(name=f.stem, path=_rel(root, f), alias=config_alias)

        sources: dict[tuple[str, str], DbtSourceTable] = {}
        versions: dict[str, dict[int, str]] = {}
        latest: dict[str, int] = {}
        for f in cls._files(root, model_dirs, ".yml", ".yaml"):
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                problems.append(f"{_rel(root, f)}: YAML not readable ({e.__class__.__name__})")
                continue
            if not isinstance(doc, dict):
                continue
            for src in doc.get("sources") or []:
                for t in src.get("tables") or []:
                    # dbt defaults: schema is the source name, identifier is the table name.
                    st = DbtSourceTable(source=src["name"], table=t["name"], schema_name=src.get("schema") or src["name"], identifier=t.get("identifier") or t["name"], description=t.get("description"), path=_rel(root, f))
                    sources[(st.source.lower(), st.table.lower())] = st
            for m in doc.get("models") or []:
                cls._apply_model_yaml(m, models, versions, latest)
        seeds = {f.stem.lower(): DbtSeed(name=f.stem, path=_rel(root, f)) for f in cls._files(root, seed_dirs, ".csv")}
        return cls(root, project, models, sources, seeds, versions, latest, problems)

    def model_node(self, name: str, version: int | None = None) -> DbtModel | None:
        """The model file a ref points at: a versioned name resolves to the given or latest version."""
        key = name.lower()
        if key in self.versions:
            stem = self.versions[key].get(version if version is not None else self.latest.get(key, -1))
            return self.models.get(stem) if stem else None
        return self.models.get(key) if version is None else None

    def find_models(self, name: str) -> list[tuple[DbtModel, Literal["name", "alias", "source_table"]]]:
        """Exact, case-insensitive matches: file name, then alias, then every model whose source() names that table. Never fuzzy."""
        key = name.strip().lower()
        hits: list[tuple[DbtModel, Literal["name", "alias", "source_table"]]] = []
        by_name = self.model_node(key)
        if by_name:
            hits.append((by_name, "name"))
        hits += [(m, "alias") for m in self.models.values() if m.alias and m.alias.lower() == key]
        tables = {k for k, st in self.sources.items() if key in (st.table.lower(), st.identifier.lower())}
        for m in self.models.values():
            text = _COMMENT.sub("", self.raw_sql(m))
            if any((s.group("s").lower(), s.group("t").lower()) in tables for s in _SOURCE.finditer(text)):
                hits.append((m, "source_table"))
        seen: set[str] = set()
        return [(m, how) for m, how in hits if not (m.name in seen or seen.add(m.name))]

    def unique_id(self, kind: str, *parts: str) -> str:
        return ".".join([kind, self.name or "project", *parts])

    def upstream(self, name: str, bronze_types: list[str], dialect: str = "databricks") -> DbtUpstream:
        """Follow ref() to upstream models until source() (and seeds, if configured as bronze). Cycles are visited once."""
        start = next((m for m, how in self.find_models(name) if how != "source_table"), None)
        if start is None:
            return DbtUpstream(model=name, unique_id="", exists=False, route="data_analysis", reader="repo", bronze_tables=[], via_models=[], missing_nodes=[])
        bronze: dict[str, BronzeSource] = {}
        via: list[str] = []
        missing: list[str] = []
        seeds: list[str] = []
        unresolved: dict[tuple[str, str, int, str], Unresolved] = {}
        seen = {start.name.lower()}
        queue = deque([start])
        evidence: list[str] = []
        while queue:
            model = queue.popleft()
            evidence.append(model.path)
            r = self.render(model, dialect)
            for u in r.unresolved:
                unresolved[(u.kind, u.path, u.line, u.text)] = u
            for s in r.sources:
                st = self.sources.get((s.source.lower(), s.table.lower()))
                where = f"{model.path}:{s.line}"
                if st:
                    bronze.setdefault(st.relation, BronzeSource(unique_id=self.unique_id("source", st.source, st.table), table=st.relation, source_name=st.source, evidence=where))
                else:
                    u = Unresolved(kind="missing_source", text=f"source('{s.source}', '{s.table}')", path=model.path, line=s.line)
                    unresolved[(u.kind, u.path, u.line, u.text)] = u
            for ref in r.refs:
                node = self.model_node(ref.name, ref.version)
                if node:
                    if node.name.lower() not in seen:
                        seen.add(node.name.lower())
                        via.append(node.name)
                        queue.append(node)
                elif ref.name.lower() in self.seeds:
                    seed = self.seeds[ref.name.lower()]
                    if "seed" in bronze_types:
                        bronze.setdefault(seed.name, BronzeSource(unique_id=self.unique_id("seed", seed.name), table=seed.name, evidence=f"{model.path}:{ref.line}"))
                    elif seed.name not in seeds:
                        seeds.append(seed.name)
                else:
                    missing.append(ref.name)
                    u = Unresolved(kind="missing_ref", text=f"ref('{ref.name}')", path=model.path, line=ref.line)
                    unresolved[(u.kind, u.path, u.line, u.text)] = u
        return DbtUpstream(
            model=start.name, unique_id=self.unique_id("model", start.name), exists=True, route="dbt_model", reader="repo",
            bronze_tables=sorted(bronze.values(), key=lambda b: b.table), via_models=via, missing_nodes=sorted(set(missing)),
            seeds=seeds, unresolved=sorted(unresolved.values(), key=lambda u: (u.path, u.line)), evidence=evidence,
        )

    def raw_sql(self, model: DbtModel) -> str:
        return (self.root / model.path).read_text(encoding="utf-8")

    def render(self, model: DbtModel, dialect: str = "databricks") -> RenderedModel:
        """Extract ref() and source() and turn the model into plain SQL for SQLGlot, without a Jinja engine."""
        raw = self.raw_sql(model)
        unresolved: list[Unresolved] = []

        def note(kind: str, text: str, line: int) -> None:
            unresolved.append(Unresolved(kind=kind, text=" ".join(text.split())[:200], path=model.path, line=line))

        # Jinja comments go first, so a commented-out ref() is not counted. Removed text keeps its newlines.
        text = _COMMENT.sub(lambda m: _blank(m.group(0)), raw)
        refs = [DbtRef(name=m.group("b") or m.group("a"), package=m.group("a") if m.group("b") else None, version=int(m.group("v")) if m.group("v") else None, line=_line(text, m.start())) for m in _REF.finditer(text)]
        sources = [DbtSourceRef(source=m.group("s"), table=m.group("t"), line=_line(text, m.start())) for m in _SOURCE.finditer(text)]

        def incremental(m: re.Match) -> str:
            # Dropped: the full-refresh logic is the reference for Silver Staging.
            note("incremental_block", m.group(0), _line(text, m.start()))
            return _blank(m.group(0))

        text = _INCREMENTAL.sub(incremental, text)
        placeholders = iter(range(1, 1_000_000))

        def expression(m: re.Match) -> str:
            body, line = m.group("body").strip(), _line(text, m.start())
            if re.fullmatch(r"config\s*\(.*\)", body, re.S):
                return _blank(m.group(0))
            if r := _REF.fullmatch(body):
                name = r.group("b") or r.group("a")
                node = self.model_node(name, int(r.group("v")) if r.group("v") else None)
                if node:
                    return node.relation
                if name.lower() in self.seeds:
                    return self.seeds[name.lower()].name
                note("missing_ref", body, line)
                return name
            if s := _SOURCE.fullmatch(body):
                st = self.sources.get((s.group("s").lower(), s.group("t").lower()))
                if st:
                    return st.relation
                note("missing_source", body, line)
                return f"{s.group('s')}.{s.group('t')}"
            note("jinja_expression", m.group(0), line)
            return f"__dma_unresolved_{next(placeholders)}__"

        text = _EXPR.sub(expression, text)

        def tag(m: re.Match) -> str:
            note("jinja_tag", m.group(0), _line(text, m.start()))
            return _blank(m.group(0))

        text = _TAG.sub(tag, text)
        rendered = "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"
        try:
            sqlglot.parse_one(rendered, read=dialect)
            parses, error = True, None
        except SqlglotError as e:
            parses, error = False, str(e).splitlines()[0][:300]
        return RenderedModel(name=model.name, path=model.path, raw_sql=raw, rendered_sql=rendered, refs=refs, sources=sources, unresolved=sorted(unresolved, key=lambda u: u.line), parses=parses, parse_error=error)

    @staticmethod
    def _apply_model_yaml(m: dict[str, Any], models: dict[str, DbtModel], versions: dict[str, dict[int, str]], latest: dict[str, int]) -> None:
        name = str(m.get("name", ""))
        columns = [DbtColumn(name=c["name"], description=c.get("description")) for c in m.get("columns") or [] if isinstance(c, dict) and c.get("name")]
        yaml_alias = (m.get("config") or {}).get("alias")
        targets = []
        if m.get("versions"):
            for v in m["versions"]:
                num = int(v["v"])
                # dbt looks for <name>_v<v> unless defined_in names another file; the default alias is <name>_v<v> too.
                file_stem = str(v.get("defined_in") or f"{name}_v{num}").lower()
                versions.setdefault(name.lower(), {})[num] = file_stem
                if file_stem in models:
                    node = models[file_stem]
                    node.versioned_as, node.version = name, num
                    node.alias = node.alias or (v.get("config") or {}).get("alias") or yaml_alias
                    targets.append(node)
            latest[name.lower()] = int(m.get("latest_version") or max(versions[name.lower()]))
        elif name.lower() in models:
            node = models[name.lower()]
            # A config() call in the SQL file wins over YAML, as in dbt.
            node.alias = node.alias or yaml_alias
            targets.append(node)
        for node in targets:
            node.description = node.description or m.get("description")
            node.columns = node.columns or columns

    @staticmethod
    def _dirs(root: Path, paths: list[str], problems: list[str]) -> list[Path]:
        out = []
        for p in paths:
            d = (root / p).resolve()
            if not d.is_relative_to(root):
                problems.append(f"path {p!r} in {PROJECT_FILE} is outside the repo and was skipped")
            elif d.is_dir():
                out.append(d)
        return out

    @staticmethod
    def _files(root: Path, dirs: list[Path], *suffixes: str) -> list[Path]:
        found = []
        for d in dirs:
            for f in sorted(d.rglob("*")):
                if f.suffix.lower() in suffixes and f.is_file() and not f.is_symlink() and f.resolve().is_relative_to(root):
                    found.append(f)
        return found
