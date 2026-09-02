"""CLAUDE.md hard rule 2, checked mechanically.

A committed key cannot be taken back: rewriting history does not remove it from
clones, forks or caches. PLAN.md names this as M0's failure mode, so it gets a
test rather than a good intention.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SELF = Path(__file__).name

# Shapes of well-known credentials. None of these patterns matches its own
# source text, but the scanner skips this file anyway.
KEY_SHAPES = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # A long opaque literal assigned to something named like a credential.
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]"),
]

TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".cfg",
    ".ini",
    ".json",
    ".sh",
    ".example",
    ".gitignore",
    "",
}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30, check=False
    )


def _in_git_repo() -> bool:
    return _git("rev-parse", "--git-dir").returncode == 0


requires_git = pytest.mark.skipif(not _in_git_repo(), reason="not a git checkout")


@requires_git
def test_dotenv_is_ignored():
    assert _git("check-ignore", "-q", ".env").returncode == 0, ".env must be gitignored"


@requires_git
def test_env_example_is_not_ignored():
    """The template must ship, or a fresh clone has no idea which keys it needs."""
    assert _git("check-ignore", "-q", ".env.example").returncode != 0


@requires_git
@pytest.mark.parametrize("path", ["data/raw/anything.xlsx", "runs/some-run/calls.jsonl"])
def test_bulky_and_transient_paths_are_ignored(path):
    assert _git("check-ignore", "-q", path).returncode == 0


@requires_git
@pytest.mark.parametrize(
    "path", ["data/processed/wages.parquet", "data/frozen/US_MSA_2024.parquet"]
)
def test_committed_data_paths_are_not_ignored(path):
    """CLAUDE.md: processed and frozen parquet are committed, no git-lfs, no DVC."""
    assert _git("check-ignore", "-q", path).returncode != 0


@requires_git
def test_no_tracked_file_looks_like_it_contains_a_key():
    out = _git("ls-files")
    assert out.returncode == 0
    offenders: list[str] = []
    for name in out.stdout.splitlines():
        p = REPO / name
        if p.name == SELF or not p.is_file():
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in KEY_SHAPES:
            m = pattern.search(text)
            if m:
                offenders.append(f"{name}: {pattern.pattern} matched {m.group()[:12]}...")
    assert not offenders, "possible credentials in tracked files:\n" + "\n".join(offenders)


def test_env_example_carries_placeholders_only():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "BEA_API_KEY" in text
    for pattern in KEY_SHAPES:
        assert not pattern.search(text)
