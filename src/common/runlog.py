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
from typing import Any, Literal

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


class ProviderCallRecord(CallRecord):
    """一次真实模型**服务请求**的明细（E1-7B，DECISIONS/0031），写在运行归档的 ``provider_calls.jsonl``——与 E1-7A 的年度规划
    记录 ``calls.jsonl`` 分开，两种结构不混写在一个文件里。

    沿用 ``CallRecord`` 的字段与含义（ts、run_id、role、model、request、response、latency_ms、prompt_tokens、
    completion_tokens、attempt、error），最小扩展：

    * ``attempt`` 是这份候选的**服务尝试序号**（1 或 2），不是计划修订的序号（那是 ``candidate``）；
    * ``seed`` 可以为空：本轮不向供应商发送 seed；
    * 每次请求两条记录：发送之前一条 ``intent``（完整的已检查请求、输入估计与费用预留），返回或失败之后一条 ``outcome``
      （原始回复或安全的错误字段、实际用量、费用）。只有 intent、没有 outcome 的请求是"可能已经发出、结果没有可靠落盘"。
    * 成功的结果另记 ``usable_for_planning``（E1-7B 收尾）：返回的模型与设定相同才可以用来规划。

    不保存密钥、鉴权头、环境变量或包含凭证的配置对象；错误只记类型、状态码与供应商错误码，不倾倒原始异常。"""

    seed: int | None = None
    event: Literal["intent", "outcome"]
    request_id: str
    clone_id: str
    year_index: int
    candidate: int
    provider: str
    settings_fingerprint: str
    status: Literal["sent", "ok", "error"]
    input_tokens_estimate: int | None = None
    reserved_cny: str | None = None
    price_version: str | None = None
    returned_model: str | None = None
    finish_reason: str | None = None
    reasoning_chars: int | None = None
    error_kind: str | None = None
    http_status: int | None = None
    provider_code: str | None = None
    retryable: bool | None = None
    cost_cny: str | None = None
    cost_status: (
        Literal["reserved", "estimated_from_usage", "uncertain_no_usage", "unmetered"] | None
    ) = None
    #: 美元报价的账本（2026-10-18，DECISIONS/0035）记在这两栏；人民币账本照旧用 ``reserved_cny`` / ``cost_cny``。不记账的运行
    #: 两种都不写、``cost_status`` 是 ``unmetered``。新栏为空时不序列化，所以千问记录的格式逐字节不变。
    reserved_usd: str | None = Field(default=None, exclude_if=lambda v: v is None)
    cost_usd: str | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 服务商报出的原始用量明细（Claude Agent SDK：各类输入 token、输出与思考 token、轮数、SDK 自己估的费用），只作核对，
    #: 不作扣额度依据；千问记录不写。
    provider_usage: dict[str, Any] | None = Field(default=None, exclude_if=lambda v: v is None)
    #: E1-7B 收尾 F1：服务成功返回（可能已经产生费用）与"这份回复能不能用来规划"是两件事。只记在成功的结果记录上；返回的模型
    #: 与设定不同时为 False——原文、模型名、用量与费用照样保留，但首次运行、缓存与恢复都不能用它。检查以事实为准，这一栏只是记录。
    usable_for_planning: bool | None = None


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
