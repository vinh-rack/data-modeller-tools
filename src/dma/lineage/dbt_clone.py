"""One clone of the dbt repo when the hosted container starts (HTTP mode only), so /data/dbt exists for the reader.

After that the existing daily `git pull --ff-only` (dbt_pull.py) keeps it current, unchanged. Nothing here runs dbt or
renders Jinja. A failed clone never stops the server: dbt_repo_status reports it and the dbt tools say why.

Credentials: a read-only token from DMA_DBT_GIT_TOKEN, never in the remote URL, .git/config, argv, os.environ, logs or
the image. Each git command gets it in its own environment as an http.extraHeader (git's GIT_CONFIG_COUNT,
GIT_CONFIG_KEY_n and GIT_CONFIG_VALUE_n), which git applies to that one command and never writes anywhere.
"""

import base64
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel

from dma.lineage.dbt_pull import Runner, _clean

CloneStatus = Literal["cloned", "present", "skipped", "failed", "no_git"]


class CloneResult(BaseModel):
    ts: str
    status: CloneStatus
    detail: str


def git_auth_env(token: str, user: str = "x-access-token") -> dict[str, str]:
    """Per-command env that sends HTTPS Basic auth with the token. GitHub, Azure DevOps and GitLab accept a token as
    the password with any user name (DMA_DBT_GIT_USER overrides it)."""
    if not token:
        return {}
    basic = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}


def auth_runner(extra_env: Mapping[str, str]) -> Runner:
    """The dbt_pull runner, plus the auth env for that one command. Same shape: argument list, no shell, timeout, no prompt."""
    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(args, shell=False, capture_output=True, text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **extra_env})

    return run


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return _clean(text)


def clone_if_missing(url: str, dest: Path, token: str = "", user: str = "x-access-token", timeout: float = 300, runner: Runner | None = None) -> CloneResult:
    url = (url or "").strip()
    if not url:
        return CloneResult(ts=_now(), status="skipped", detail="DMA_DBT_REPO_URL is not set")
    dest = Path(dest)
    if (dest / ".git").exists():
        return CloneResult(ts=_now(), status="present", detail=f"{dest} is already a clone; the daily pull keeps it current")
    if dest.exists() and any(dest.iterdir()):
        return CloneResult(ts=_now(), status="failed", detail=f"{dest} is not empty and not a git clone, so nothing was cloned into it")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "file"):
        return CloneResult(ts=_now(), status="failed", detail="DMA_DBT_REPO_URL must be an https URL (or file for local tests)")
    if parts.username or parts.password:
        return CloneResult(ts=_now(), status="failed", detail="DMA_DBT_REPO_URL must not contain credentials; put the token in DMA_DBT_GIT_TOKEN")
    env = git_auth_env(token, user)
    run = runner or auth_runner(env)
    secrets = [token, *env.values()]
    try:
        r = run(["git", "clone", "--", url, str(dest)], timeout)
    except FileNotFoundError:
        return CloneResult(ts=_now(), status="no_git", detail="git is not on PATH, so the dbt repo was not cloned")
    except subprocess.TimeoutExpired:
        return CloneResult(ts=_now(), status="failed", detail=f"git clone timed out after {timeout} s")
    except (OSError, subprocess.SubprocessError) as e:
        return CloneResult(ts=_now(), status="failed", detail=f"git could not run: {e.__class__.__name__}")
    if r.returncode != 0:
        return CloneResult(ts=_now(), status="failed", detail=f"git clone failed (no access, wrong URL or offline): {_scrub(r.stderr or r.stdout, secrets)}")
    return CloneResult(ts=_now(), status="cloned", detail=f"cloned into {dest}")
