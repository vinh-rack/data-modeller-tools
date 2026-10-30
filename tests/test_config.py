import pytest

from dma.config import load_settings


def test_dbt_settings_default_and_values(tmp_path):
    assert load_settings({}).dbt_repo is None and load_settings({}).dbt_pull == "auto"
    s = load_settings({"DMA_DBT_REPO": str(tmp_path), "DMA_DBT_PULL": " OFF "})
    assert s.dbt_repo == tmp_path.resolve() and s.dbt_pull == "off"


def test_dbt_pull_rejects_other_values():
    with pytest.raises(ValueError, match="DMA_DBT_PULL"):
        load_settings({"DMA_DBT_PULL": "daily"})
