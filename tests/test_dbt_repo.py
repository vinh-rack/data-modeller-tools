"""Static dbt repo reader on the synthetic fixture. No dbt, no Jinja, no network."""

import pytest

from conftest import FIXTURES
from dma.lineage.dbt_repo import DbtRepo, DbtRepoError

REPO_DIR = FIXTURES / "dbt_repo"


@pytest.fixture(scope="module")
def repo():
    return DbtRepo.load(REPO_DIR)


def test_project_and_counts(repo):
    assert repo.name == "shop_pkg" and repo.problems == []
    assert sorted(repo.models) == ["broken", "cycle_one", "cycle_two", "from_seed", "mart_ab", "stg_a", "stg_b", "stg_c", "stg_versioned_v2"]
    assert list(repo.seeds) == ["seed_x"]


def test_sources_apply_dbt_defaults(repo):
    assert repo.sources[("shop", "raw_c")].relation == "raw.raw_c_v2"
    assert repo.sources[("legacy", "old_x")].relation == "legacy.old_x"
    assert repo.sources[("shop", "raw_a")].description == "Synthetic table A"


def test_alias_from_sql_config_and_yaml(repo):
    assert repo.models["stg_c"].alias == "stg_c_clean" and repo.models["stg_c"].relation == "stg_c_clean"
    assert repo.models["stg_b"].alias == "stg_b_alias"
    assert repo.models["stg_a"].alias is None and repo.models["stg_a"].relation == "stg_a"


def test_yaml_description_columns_and_versions(repo):
    a = repo.models["stg_a"]
    assert a.description == "Synthetic staging of raw_a." and [(c.name, c.description) for c in a.columns] == [("a_id", "Key of A."), ("a_nm", None)]
    v = repo.models["stg_versioned_v2"]
    assert (v.versioned_as, v.version) == ("stg_versioned", 2)
    assert repo.versions == {"stg_versioned": {2: "stg_versioned_v2"}} and repo.latest == {"stg_versioned": 2}


def test_missing_project_and_paths_outside_repo(tmp_path):
    with pytest.raises(DbtRepoError, match="dbt_project.yml"):
        DbtRepo.load(tmp_path)
    (tmp_path / "dbt_project.yml").write_text('name: x\nmodel-paths: ["../elsewhere"]\n', encoding="utf-8")
    r = DbtRepo.load(tmp_path)
    assert r.models == {} and "outside the repo" in r.problems[0]


def test_unreadable_yaml_is_a_problem_not_a_crash(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: x\n", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "bad.yml").write_text("models: [\n", encoding="utf-8")
    (tmp_path / "models" / "m.sql").write_text("select 1", encoding="utf-8")
    r = DbtRepo.load(tmp_path)
    assert list(r.models) == ["m"] and "bad.yml" in r.problems[0]
