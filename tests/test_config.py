import pytest

from dma.config import load_settings


def test_dbt_settings_default_and_values(tmp_path):
    assert load_settings({}).dbt_repo is None and load_settings({}).dbt_pull == "auto"
    s = load_settings({"DMA_DBT_REPO": str(tmp_path), "DMA_DBT_PULL": " OFF "})
    assert s.dbt_repo == tmp_path.resolve() and s.dbt_pull == "off"


def test_dbt_pull_rejects_other_values():
    with pytest.raises(ValueError, match="DMA_DBT_PULL"):
        load_settings({"DMA_DBT_PULL": "daily"})


def test_transport_defaults_to_stdio_and_rejects_unknown():
    assert load_settings({}).transport == "stdio"
    assert load_settings({"DMA_TRANSPORT": " HTTP "}).transport == "http"
    with pytest.raises(ValueError, match="DMA_TRANSPORT"):
        load_settings({"DMA_TRANSPORT": "streamable-http"})


def test_v1_paths_unchanged_without_data_root():
    s = load_settings({})
    assert s.data_dir is None and s.dbt_repo is None
    assert s.output_dir.name == "output" and s.kb_dir.name == "kb" and s.smx_dir.parts[-2:] == ("fixtures", "smx")


def test_data_root_gives_defaults_and_explicit_values_win(tmp_path):
    s = load_settings({"DMA_DATA_DIR": str(tmp_path)})
    root = tmp_path.resolve()
    assert (s.output_dir, s.smx_dir, s.kb_dir, s.dbt_repo) == (root / "output", root / "smx", root / "kb", root / "dbt")
    other = tmp_path / "elsewhere"
    s = load_settings({"DMA_DATA_DIR": str(tmp_path), "DMA_OUTPUT_DIR": str(other), "DMA_DBT_REPO": str(other)})
    assert s.output_dir == other.resolve() and s.dbt_repo == other.resolve() and s.kb_dir == root / "kb"


def test_http_settings_defaults():
    h = load_settings({"DMA_TRANSPORT": "http"}).http
    assert (h.host, h.port, h.path, h.proxy_headers) == ("127.0.0.1", 8000, "/mcp", True)
    assert h.resource_server_url == "https://127.0.0.1/mcp"
    assert "127.0.0.1" in h.allowed_hosts and "localhost:*" in h.allowed_hosts
    assert "https://127.0.0.1" in h.allowed_origins


def test_http_settings_from_env():
    h = load_settings({
        "DMA_TRANSPORT": "http", "DMA_HTTP_HOST": "0.0.0.0", "DMA_HTTP_PORT": "9000", "DMA_HTTP_PATH": "/dma",
        "DMA_RESOURCE_SERVER_URL": "https://dma.example.test/dma/", "DMA_ALLOWED_HOSTS": "dma.example.test, 127.0.0.1:9000",
        "DMA_ALLOWED_ORIGINS": "https://dma.example.test", "DMA_PROXY_HEADERS": "0",
    }).http
    assert (h.host, h.port, h.path, h.proxy_headers) == ("0.0.0.0", 9000, "/dma", False)
    assert h.resource_server_url == "https://dma.example.test/dma"
    assert h.allowed_hosts == ["dma.example.test", "127.0.0.1:9000"] and h.allowed_origins == ["https://dma.example.test"]
    # The default resource URL follows the host and path.
    assert load_settings({"DMA_TRANSPORT": "http", "DMA_HTTP_HOST": "dma.local"}).http.resource_server_url == "https://dma.local/mcp"


@pytest.mark.parametrize("key,value", [
    ("DMA_HTTP_PORT", "eighty"), ("DMA_HTTP_PORT", "70000"), ("DMA_HTTP_PATH", "mcp"), ("DMA_HTTP_PATH", "/"),
    ("DMA_RESOURCE_SERVER_URL", "dma.example.test/mcp"), ("DMA_RESOURCE_SERVER_URL", "https://dma.example.test/mcp#x"), ("DMA_PROXY_HEADERS", "maybe"),
])
def test_http_settings_reject_bad_values(key, value):
    with pytest.raises(ValueError, match=key):
        load_settings({"DMA_TRANSPORT": "http", key: value})


def test_http_settings_not_read_in_stdio():
    assert load_settings({"DMA_HTTP_PORT": "eighty"}).http.port == 8000
