"""Configuration loading and validation.

Two things here are enforcement rather than plumbing:

  * A model string must be a dated snapshot. CLAUDE.md hard rule 6 forbids
    rolling aliases, and an alias is undetectable after the fact -- a run logged
    against "qwen-plus" cannot be reproduced once the alias moves. The validator
    turns that into a load-time failure.

  * A key is never a config value. Configuration names the *environment
    variable* to read; the value is resolved at use time and redacted in repr.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# A dated snapshot ends in -YYYY-MM-DD, -YYYYMMDD, or -MMDD.
#
# The -MMDD form (qwen-max-0107, qwen3.8-max-0902) is what DashScope uses for
# some of its models. It is a genuine pin -- the rolling alias would be
# `qwen-max` with no suffix -- so rejecting it would block legitimate choices,
# which is not what hard rule 6 is for.
#
# It is still the weaker form: the year is implicit, so the string alone does
# not identify the version across years. Prefer a full-date snapshot when the
# provider offers one. The run log records the model string together with the
# run timestamp, so the year stays recoverable either way.
_DATED_SNAPSHOT = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4})$")


class ConfigError(ValueError):
    """Raised for a configuration that loads as YAML but is not usable."""


_DOTENV_LOADED = False


def _ensure_dotenv() -> None:
    """惰性加载一次 .env，作为进程环境变量的**后备**，不覆盖它。

    为什么需要这条后备路径。Windows 只把改过的环境变量交给**之后**启动的
    进程；已经在运行的进程继承的是父进程那份旧副本，而且没法刷新。于是
    "变量明明设好了，程序却读不到"是一个必然会重复发生的故障，而它的表现
    是 KeyError 而不是鉴权失败——2026-09-02 那次排查里，第一次探针报的
    正是"not set"，我却按"key 无效"去查了。

    ``override=False`` 是刻意的：真实环境变量优先。一份过期的 .env 不该
    盖掉一个刚设好的系统变量，否则这个后备机制自己会变成下一个坑。

    .env 在 .gitignore 里（硬规则 2），仓库里只有 .env.example。
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    from dotenv import find_dotenv, load_dotenv

    # usecwd=True 是必需的：默认的 find_dotenv 从**调用方源文件**所在目录
    # 往上找，也就是从 src/common/ 往上，而不是从你运行命令的地方往上。
    # 默认行为在这个仓库里碰巧能用，但在测试里和从别处调用时都会找错，
    # 而找错的表现是"没读到"，又一次伪装成凭证问题。
    load_dotenv(find_dotenv(usecwd=True), override=False)
    _DOTENV_LOADED = True


def key_source(env_var: str) -> str:
    """这个密钥是从哪儿来的。只报来源，永不报值。

    诊断用。"读到了"和"从哪读到的"是两件事，后者在排查环境变量问题时
    才是有用的那一件。
    """
    in_process = env_var in os.environ
    _ensure_dotenv()
    if in_process:
        return "process environment"
    if env_var in os.environ:
        return ".env file"
    return "not found"


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at the top level")
    return data


class ResolvedModel(BaseModel):
    """A model spec with its key resolved. Never log this object's api_key."""

    model_config = ConfigDict(frozen=True)

    provider: str
    base_url: str
    model: str
    api_key: str = Field(repr=False)

    def __repr__(self) -> str:
        return (
            f"ResolvedModel(provider={self.provider!r}, model={self.model!r}, api_key=<redacted>)"
        )

    __str__ = __repr__


class ModelSpec(BaseModel):
    """One base model. ``model`` may be null while the choice is still open;
    resolving an unset spec is an error, so a half-configured base fails loudly
    at the point of use rather than silently defaulting to the other one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    base_url: str
    api_key_env: str
    model: str | None = None
    note: str | None = None

    @field_validator("model")
    @classmethod
    def _must_be_dated_snapshot(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _DATED_SNAPSHOT.search(v):
            raise ValueError(
                f"model {v!r} is not a dated snapshot. CLAUDE.md hard rule 6 forbids rolling "
                "aliases; use a name ending in -YYYY-MM-DD, -YYYYMMDD or -MMDD so the run "
                "stays reproducible after the alias moves."
            )
        return v

    def resolve(self) -> ResolvedModel:
        _ensure_dotenv()
        if self.model is None:
            raise ConfigError(
                f"base model for provider {self.provider!r} has no snapshot pinned yet; "
                "set `model` in configs/models.yaml before using this role"
            )
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ConfigError(
                f"environment variable {self.api_key_env} is not set, and no .env supplied it. "
                "Keys are read from the environment or .env, never from config or the repo. "
                "On Windows, a variable set after this process started is invisible to it: "
                "open a new terminal, or put the key in .env."
            )
        return ResolvedModel(
            provider=self.provider, base_url=self.base_url, model=self.model, api_key=key
        )


ModelRole = Literal["base_a", "base_b"]


class ModelsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_a: ModelSpec
    base_b: ModelSpec

    def spec(self, role: ModelRole) -> ModelSpec:
        return getattr(self, role)

    def resolve(self, role: ModelRole) -> ResolvedModel:
        return self.spec(role).resolve()

    @classmethod
    def load(cls, path: str | Path = "configs/models.yaml") -> ModelsConfig:
        return cls.model_validate(load_yaml(path))


class ExperimentConfig(BaseModel):
    """The minimum every run records about itself.

    Module-specific settings belong to their module's own config model; this is
    only what the run log and the result metadata need in order for a result to
    mean something six months later.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    seed: int
    reference_id: str  # frozen amenity reference population, README §5.3
    numeraire: str  # scale of every monetary quantity, README §5.8
    model_role: ModelRole = "base_b"
    notes: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> ExperimentConfig:
        return cls.model_validate(load_yaml(path))
