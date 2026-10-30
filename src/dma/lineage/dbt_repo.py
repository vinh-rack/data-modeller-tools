"""Static reader for a local dbt repository (REQUIREMENTS D3, FR-K4). Reads files as code only.

No dbt command runs and no Jinja is rendered: dbt_project.yml and the model-path YAML are read with yaml.safe_load,
and model SQL is read as text.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROJECT_FILE = "dbt_project.yml"
# alias='x' or alias="x" inside a {{ config(...) }} call. Only literal strings are read.
_CONFIG_CALL = re.compile(r"\{\{-?\s*config\s*\((?P<args>.*?)\)\s*-?\}\}", re.S)
_ALIAS_ARG = re.compile(r"""\balias\s*=\s*(['"])(?P<alias>[^'"]+)\1""")


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
