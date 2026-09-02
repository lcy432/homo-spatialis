"""Run identity and the JSONL call log.

Every language-model call becomes one line in runs/<run_id>/calls.jsonl. Three
downstream things depend on that file existing in exactly this shape:

  * Reproducibility. The snapshot model string is on every line (CLAUDE.md hard
    rule 6), alongside the seed and the git sha recorded in meta.json.
  * The leak test (README §10.2). It asserts that no ground-truth value appears
    in a serialised prompt, which requires the prompt to have been serialised
    and kept.
  * Cost accounting. README §11.9 budgets roughly 25,000 calls; token counts per
    call are how that stays a number rather than a hope.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_RUNS_ROOT = Path("runs")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_run_id() -> str:
    """Sortable and collision-resistant: 20260901T104512Z-3f9a2c."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def git_sha(short: bool = True) -> str | None:
    """The commit the run was launched from, or None outside a git checkout."""
    cmd = ["git", "rev-parse", "--short", "HEAD"] if short else ["git", "rev-parse", "HEAD"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def git_is_dirty() -> bool | None:
    """True if the working tree has uncommitted changes at launch time.

    Recorded rather than forbidden. A dirty tree does not invalidate an
    exploratory run, but a result whose provenance is a commit that never
    existed should say so on its face.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip())


class CallRecord(BaseModel):
    """One language-model call. Written as a single JSONL line."""

    model_config = ConfigDict(extra="forbid")

    ts: str = Field(default_factory=utc_now_iso)
    run_id: str
    seed: int
    role: str  # "base_a" | "base_b"
    model: str  # dated snapshot string, never an alias
    request: dict[str, Any]  # the full serialised request, prompt included
    response: dict[str, Any] | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempt: int = 1
    error: str | None = None

    @field_validator("model")
    @classmethod
    def _model_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model version must be recorded on every call (CLAUDE.md hard rule 6)")
        return v


class RunDir:
    """runs/<run_id>/ , created on construction."""

    def __init__(self, run_id: str | None = None, root: str | Path = DEFAULT_RUNS_ROOT) -> None:
        self.run_id = run_id or new_run_id()
        self.path = Path(root) / self.run_id
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def calls_path(self) -> Path:
        return self.path / "calls.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    def write_meta(self, config: dict[str, Any], **extra: Any) -> Path:
        meta = {
            "run_id": self.run_id,
            "started_at": utc_now_iso(),
            "git_sha": git_sha(),
            "git_dirty": git_is_dirty(),
            "python": sys.version.split()[0],
            "config": config,
            **extra,
        }
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        return self.meta_path

    def log_call(self, record: CallRecord) -> None:
        line = record.model_dump_json()
        with self.calls_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_calls(self) -> list[dict[str, Any]]:
        if not self.calls_path.exists():
            return []
        with self.calls_path.open(encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]

    def __repr__(self) -> str:
        return f"RunDir({self.run_id!r} -> {self.path})"
