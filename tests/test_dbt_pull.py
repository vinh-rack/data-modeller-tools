"""Daily dbt clone pull with subprocess mocked. No git, no network."""

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from dma.lineage.dbt_pull import DbtPuller
from dma.writer import ArtifactWriter

T0 = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)


class FakeGit:
    """Answers git subcommands from a table and records every call."""

    def __init__(self, answers=None, heads=("aaa", "bbb")):
        self.calls = []
        self.answers = {"rev-parse --is-inside-work-tree": (0, "true\n", ""), "status --porcelain": (0, "", ""), "pull --ff-only": (0, "Updating aaa..bbb\n", ""), **(answers or {})}
        self.heads = list(heads)

    def __call__(self, args, timeout):
        assert args[:3] == ["git", "-C", "REPO"] and timeout == 60
        sub = " ".join(args[3:])
        self.calls.append(sub)
        if sub == "rev-parse HEAD":
            return subprocess.CompletedProcess(args, 0, (self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]) + "\n", "")
        answer = self.answers[sub]
        if isinstance(answer, Exception):
            raise answer
        return subprocess.CompletedProcess(args, *answer)


@pytest.fixture
def writer(tmp_path):
    return ArtifactWriter(tmp_path / "output")


def puller(writer, git, mode="auto", now=T0):
    return DbtPuller("REPO", writer, mode=mode, runner=git, clock=lambda: now)


def test_pulls_once_then_skips_for_24_hours(writer):
    git = FakeGit()
    first = puller(writer, git).maybe_pull()
    assert (first.status, first.commit) == ("pulled", "bbb")
    assert git.calls == ["rev-parse --is-inside-work-tree", "status --porcelain", "rev-parse HEAD", "pull --ff-only", "rev-parse HEAD"]
    git.calls.clear()
    again = puller(writer, git, now=T0 + timedelta(hours=23)).maybe_pull()
    assert again.status == "skipped_recent" and git.calls == []
    later = puller(writer, FakeGit(heads=("bbb",)), now=T0 + timedelta(hours=25)).maybe_pull()
    assert later.status == "up_to_date"
    assert [p["status"] for p in writer.read_dbt_pulls()] == ["pulled", "up_to_date"]


def test_off_never_runs_git(writer):
    git = FakeGit()
    assert puller(writer, git, mode="off").maybe_pull().status == "skipped_off"
    assert git.calls == [] and writer.read_dbt_pulls() == []


@pytest.mark.parametrize("answers,status", [
    ({"status --porcelain": (0, " M models/x.sql\n", "")}, "dirty"),
    ({"pull --ff-only": (128, "", "fatal: Not possible to fast-forward, aborting.")}, "failed"),
    ({"rev-parse --is-inside-work-tree": (128, "", "fatal: not a git repository")}, "not_a_repo"),
    ({"rev-parse --is-inside-work-tree": FileNotFoundError("git")}, "no_git"),
    ({"pull --ff-only": subprocess.TimeoutExpired("git", 60)}, "failed"),
])
def test_failures_are_reported_not_raised(writer, answers, status):
    git = FakeGit(answers)
    r = puller(writer, git).maybe_pull()
    assert r.status == status and r.detail
    assert "pull --ff-only" not in git.calls or status == "failed"
    assert writer.read_dbt_pulls()[-1]["status"] == status


def test_dirty_clone_is_not_touched(writer):
    git = FakeGit({"status --porcelain": (0, "?? notes.txt\n", "")})
    puller(writer, git).maybe_pull()
    assert "pull --ff-only" not in git.calls and not any(c.startswith(("stash", "reset", "checkout", "merge", "clone")) for c in git.calls)


def test_credentials_in_git_output_are_masked(writer):
    git = FakeGit({"pull --ff-only": (1, "", "fatal: unable to access 'https://user:s3cret@example.com/repo.git/'")})
    r = puller(writer, git).maybe_pull()
    assert "s3cret" not in r.detail and "https://***@example.com" in r.detail
    assert "s3cret" not in str(writer.read_dbt_pulls())
