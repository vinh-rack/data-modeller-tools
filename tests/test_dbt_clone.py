"""The start-up dbt clone: token per command only, never in the URL, .git/config, argv, os.environ or errors."""

import base64
import os
import shutil
import subprocess

import pytest

from conftest import CONFIG_DIR, SMX_DIR
from dma.config import load_settings
from dma.lineage import dbt_clone
from dma.lineage.dbt_clone import auth_runner, clone_if_missing, git_auth_env
from dma.server import build_http
from httpkit import mcp_client

TOKEN = "ghp_syntheticTokenForTests0123456789abcdef"
URL = "https://git.example.test/org/dbt.git"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


class FakeRunner:
    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, args, timeout):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)


def test_token_is_never_in_argv(tmp_path):
    run = FakeRunner()
    r = clone_if_missing(URL, tmp_path / "dbt", token=TOKEN, runner=run)
    assert r.status == "cloned"
    assert run.calls == [["git", "clone", "--", URL, str(tmp_path / "dbt")]]
    assert not any(TOKEN in a for a in run.calls[0])


def test_auth_env_is_for_that_command_only(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(dbt_clone.subprocess, "run", fake_run)
    before = dict(os.environ)
    auth_runner(git_auth_env(TOKEN))(["git", "status"], 10)
    header = seen["GIT_CONFIG_VALUE_0"]
    assert seen["GIT_CONFIG_COUNT"] == "1" and seen["GIT_CONFIG_KEY_0"] == "http.extraHeader" and seen["GIT_TERMINAL_PROMPT"] == "0"
    assert base64.b64decode(header.split("Basic ", 1)[1]).decode() == f"x-access-token:{TOKEN}"
    assert dict(os.environ) == before and not any(TOKEN in v for v in os.environ.values())


def test_custom_user_name():
    header = git_auth_env(TOKEN, user="dma-reader")["GIT_CONFIG_VALUE_0"]
    assert base64.b64decode(header.split("Basic ", 1)[1]).decode() == f"dma-reader:{TOKEN}"
    assert git_auth_env("") == {}


def test_failure_text_never_contains_the_token(tmp_path):
    basic = git_auth_env(TOKEN)["GIT_CONFIG_VALUE_0"]
    run = FakeRunner(128, f"fatal: Authentication failed for {URL} with {TOKEN} and {basic} at https://user:{TOKEN}@git.example.test")
    r = clone_if_missing(URL, tmp_path / "dbt", token=TOKEN, runner=run)
    assert r.status == "failed" and "Authentication failed" in r.detail
    assert TOKEN not in r.detail and basic not in r.detail


def test_existing_clone_is_left_alone(tmp_path):
    (tmp_path / "dbt" / ".git").mkdir(parents=True)
    run = FakeRunner()
    assert clone_if_missing(URL, tmp_path / "dbt", token=TOKEN, runner=run).status == "present"
    assert run.calls == []


@pytest.mark.parametrize("url,words", [
    (f"https://user:{TOKEN}@git.example.test/org/dbt.git", "must not contain credentials"),
    (f"https://{TOKEN}@git.example.test/org/dbt.git", "must not contain credentials"),
    ("http://git.example.test/org/dbt.git", "https"),
    ("git@git.example.test:org/dbt.git", "https"),
])
def test_bad_urls_are_refused_before_git_runs(tmp_path, url, words):
    run = FakeRunner()
    r = clone_if_missing(url, tmp_path / "dbt", token=TOKEN, runner=run)
    assert r.status == "failed" and words in r.detail and TOKEN not in r.detail
    assert run.calls == []


def test_non_empty_folder_is_not_cloned_into(tmp_path):
    (tmp_path / "dbt").mkdir()
    (tmp_path / "dbt" / "notes.txt").write_text("x", encoding="utf-8")
    run = FakeRunner()
    assert clone_if_missing(URL, tmp_path / "dbt", runner=run).status == "failed"
    assert run.calls == []


def test_missing_git_is_reported(tmp_path):
    def no_git(args, timeout):
        raise FileNotFoundError("git")

    assert clone_if_missing(URL, tmp_path / "dbt", runner=no_git).status == "no_git"


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def make_origin(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", str(work))
    (work / "dbt_project.yml").write_text("name: synthetic\n", encoding="utf-8")
    git("-c", "user.name=t", "-c", "user.email=t@example.test", "add", ".", cwd=work)
    git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", "init", cwd=work)
    bare = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(work), str(bare))
    return bare.resolve().as_uri()


@needs_git
def test_real_clone_leaves_no_token_in_the_clone(tmp_path):
    url = make_origin(tmp_path)
    dest = tmp_path / "dbt"
    r = clone_if_missing(url, dest, token=TOKEN)
    assert r.status == "cloned", r.detail
    config = (dest / ".git" / "config").read_text(encoding="utf-8")
    basic = git_auth_env(TOKEN)["GIT_CONFIG_VALUE_0"].split("Basic ", 1)[1]
    assert TOKEN not in config and basic not in config and "extraHeader" not in config
    assert git("remote", "get-url", "origin", cwd=dest).stdout.strip() == url
    # The daily pull through the same per-command runner leaves the config clean too.
    pulled = auth_runner(git_auth_env(TOKEN))(["git", "-C", str(dest), "pull", "--ff-only"], 60)
    assert pulled.returncode == 0
    assert (dest / ".git" / "config").read_text(encoding="utf-8") == config
    assert not any(TOKEN in v for v in os.environ.values())


@needs_git
@pytest.mark.anyio
async def test_failed_clone_still_starts_the_server_and_status_says_why(tmp_path):
    env = {
        "DMA_SMX_DIR": str(SMX_DIR), "DMA_DATA_DIR": str(tmp_path / "data"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_TRANSPORT": "http",
        "DMA_AUTH": "dev", "DMA_DEV_TOKEN": "dev-token-for-tests-only-0123456789abcdef", "DMA_RESOURCE_SERVER_URL": "http://127.0.0.1:8000/mcp",
        "DMA_DBT_REPO_URL": (tmp_path / "no-such-repo.git").resolve().as_uri(), "DMA_DBT_GIT_TOKEN": TOKEN, "DMA_DBT_PULL": "off",
    }
    app = build_http(load_settings(env), env)
    async with mcp_client(app, headers={"authorization": "Bearer dev-token-for-tests-only-0123456789abcdef"}) as c:
        status = (await c.call_tool("dbt_repo_status", {})).structured_content
    assert status["clone"]["status"] == "failed"
    assert any("clone at start: failed" in p for p in status["problems"])
    assert TOKEN not in str(status)


def test_daily_pull_uses_the_per_command_runner(tmp_path):
    from dma.tools import DmaTools

    tools = DmaTools(load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "out"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_DBT_REPO": str(tmp_path / "dbt")}))
    default = tools._dbt_puller._runner
    tools = DmaTools(load_settings({"DMA_SMX_DIR": str(SMX_DIR), "DMA_OUTPUT_DIR": str(tmp_path / "out2"), "DMA_CONFIG_DIR": str(CONFIG_DIR), "DMA_DBT_REPO": str(tmp_path / "dbt")}))
    tools.git_runner = auth_runner(git_auth_env(TOKEN))
    assert tools._dbt_puller._runner is tools.git_runner and default is not tools.git_runner
