from pathlib import Path

import pytest

from dma.config import load_yaml
from dma.index.model_index import ModelIndex

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
FIXTURES = ROOT / "tests" / "fixtures"
SMX_DIR = FIXTURES / "smx"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def mapping():
    return load_yaml(CONFIG_DIR, "smx_mapping.yaml")


@pytest.fixture(scope="session")
def rules_cfg():
    return load_yaml(CONFIG_DIR, "rules.yaml")


@pytest.fixture(scope="session")
def index(mapping, rules_cfg):
    return ModelIndex.build(SMX_DIR, mapping, rules_cfg, strict=True)
