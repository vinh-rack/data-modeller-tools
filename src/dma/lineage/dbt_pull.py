"""Daily read-only refresh of the local dbt clone (REQUIREMENTS D3, FR-K4): git pull --ff-only, at most once per interval.

It never clones, never runs dbt and never repairs the clone. Missing git, a dirty or diverged clone and network errors are
reported as a result, and the reader keeps using the files already on disk. Every attempt is logged through the writer.
"""

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

from dma.writer import ArtifactWriter, WriterError

Runner = Callable[[list[str], float], subprocess.CompletedProcess]
PullStatus = Literal["pulled", "up_to_date", "skipped_off", "skipped_recent", "dirty", "failed", "no_git", "not_a_repo"]
# A remote URL with a user or token in it, e.g. https://user:token@host/..., is masked before any text is reported.
_URL_CREDENTIALS = re.compile(r"(\w+://)[^/\s@]+@")


class PullResult(BaseModel):
    ts: str
    status: PullStatus
    detail: str
    commit: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    # An argument list with no shell, a timeout, and no prompt for credentials.
    return subprocess.run(args, shell=False, capture_output=True, text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _clean(text: str) -> str:
    return _URL_CREDENTIALS.sub(r"\1***@", " ".join((text or "").split()))[-300:]


class DbtPuller:
    def __init__(self, repo: Path, writer: ArtifactWriter, mode: str = "auto", interval_hours: float = 24, timeout: float = 60, runner: Runner = _run, clock: Callable[[], datetime] = _utc_now):
        self.repo = Path(repo)
        self.writer = writer
        self.mode = mode
        self.interval = timedelta(hours=interval_hours)
        self.timeout = timeout
        self._runner = runner
        self._clock = clock

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return self._runner(["git", "-C", str(self.repo), *args], self.timeout)

    def last(self) -> dict | None:
        try:
            pulls = self.writer.read_dbt_pulls()
        except (WriterError, ValueError):
            return None
        return pulls[-1] if pulls else None

    def head(self) -> str | None:
        try:
            r = self._git("rev-parse", "HEAD")
        except (OSError, subprocess.SubprocessError):
            return None
        return (r.stdout.strip() or None) if r.returncode == 0 else None

    def maybe_pull(self) -> PullResult:
        now = self._clock()
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.mode == "off":
            return PullResult(ts=ts, status="skipped_off", detail="DMA_DBT_PULL=off, reading the files on disk")
        last = self.last()
        if last:
            try:
                age = now - datetime.strptime(last["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                age = self.interval
            if age < self.interval:
                return PullResult(ts=ts, status="skipped_recent", detail=f"last attempt {last['ts']} ({last.get('status')}), next after {self.interval}", commit=last.get("commit"))
        result = self._pull(ts)
        try:
            self.writer.append_dbt_pull(result.model_dump())
        except WriterError as e:
            result.detail += f" (not logged: {e})"
        return result

    def _pull(self, ts: str) -> PullResult:
        try:
            inside = self._git("rev-parse", "--is-inside-work-tree")
            if inside.returncode != 0 or inside.stdout.strip() != "true":
                return PullResult(ts=ts, status="not_a_repo", detail=f"{self.repo} is not a git clone: {_clean(inside.stderr)}")
            status = self._git("status", "--porcelain")
            if status.returncode != 0:
                return PullResult(ts=ts, status="failed", detail=f"git status failed: {_clean(status.stderr)}", commit=self.head())
            if status.stdout.strip():
                # Local changes are never stashed, reset or merged: the modeller fixes the clone.
                return PullResult(ts=ts, status="dirty", detail=f"clone has local changes, not pulled: {_clean(status.stdout)}", commit=self.head())
            before = self.head()
            pull = self._git("pull", "--ff-only")
            if pull.returncode != 0:
                return PullResult(ts=ts, status="failed", detail=f"git pull --ff-only failed (diverged, offline or no access): {_clean(pull.stderr or pull.stdout)}", commit=before)
            after = self.head()
            return PullResult(ts=ts, status="pulled" if after != before else "up_to_date", detail=_clean(pull.stdout) or "ok", commit=after)
        except FileNotFoundError:
            return PullResult(ts=ts, status="no_git", detail="git is not on PATH, reading the files on disk")
        except subprocess.TimeoutExpired:
            return PullResult(ts=ts, status="failed", detail=f"git timed out after {self.timeout} s")
        except (OSError, subprocess.SubprocessError) as e:
            return PullResult(ts=ts, status="failed", detail=f"git could not run: {e.__class__.__name__}")
