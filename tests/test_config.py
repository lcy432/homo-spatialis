from pathlib import Path

import pytest

from common.config import ConfigError, ExperimentConfig, ModelsConfig, ModelSpec

REPO = Path(__file__).resolve().parent.parent


def test_shipped_models_yaml_loads():
    cfg = ModelsConfig.load(REPO / "configs" / "models.yaml")
    assert cfg.base_b.provider == "dashscope"
    assert cfg.base_b.api_key_env == "DASHSCOPE_API_KEY"


def test_shipped_experiment_config_loads():
    cfg = ExperimentConfig.load(REPO / "configs" / "experiment.example.yaml")
    assert cfg.reference_id == "US_MSA_2024"
    assert cfg.numeraire == "USD_2024_US100"


@pytest.mark.parametrize(
    "alias",
    ["qwen-plus", "qwen3-max", "gpt-4o", "claude-opus-4-0", "some-model-latest"],
)
def test_rolling_aliases_are_rejected(alias):
    """CLAUDE.md hard rule 6. An alias that moves makes a past run unreproducible
    and there is no way to detect it after the fact, so it must fail at load."""
    with pytest.raises(ValueError, match="dated snapshot"):
        ModelSpec(provider="p", base_url="u", api_key_env="E", model=alias)


@pytest.mark.parametrize(
    "snapshot",
    ["qwen3-max-2025-09-23", "claude-opus-4-20250514", "gpt-4o-2024-08-06"],
)
def test_dated_snapshots_are_accepted(snapshot):
    assert ModelSpec(provider="p", base_url="u", api_key_env="E", model=snapshot).model == snapshot


def test_unpinned_model_raises_on_resolve():
    spec = ModelSpec(provider="p", base_url="u", api_key_env="E", model=None)
    with pytest.raises(ConfigError, match="no snapshot pinned"):
        spec.resolve()


def test_missing_key_env_raises_and_never_invents_a_default(monkeypatch):
    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    spec = ModelSpec(provider="p", base_url="u", api_key_env="SOME_TEST_KEY", model="m-2025-01-01")
    with pytest.raises(ConfigError, match="SOME_TEST_KEY"):
        spec.resolve()


def test_resolved_model_redacts_the_key(monkeypatch):
    monkeypatch.setenv("SOME_TEST_KEY", "super-secret-value")
    spec = ModelSpec(provider="p", base_url="u", api_key_env="SOME_TEST_KEY", model="m-2025-01-01")
    resolved = spec.resolve()
    assert resolved.api_key == "super-secret-value"
    assert "super-secret-value" not in repr(resolved)
    assert "super-secret-value" not in str(resolved)


def test_unknown_config_keys_are_rejected():
    """extra='forbid': a typo in a YAML key must not silently become a default."""
    with pytest.raises(ValueError):
        ModelSpec(provider="p", base_url="u", api_key_env="E", modle="typo-2025-01-01")


def test_dotenv_supplies_the_key_when_the_process_environment_lacks_it(tmp_path, monkeypatch):
    """Windows 上"变量设好了但进程读不到"是必然会重复的故障。

    已经在运行的进程继承的是父进程那份旧环境副本，而且刷新不了。表现是
    KeyError 而不是鉴权失败，所以很容易被误诊成凭证问题——这正是
    2026-09-02 那次排查走错方向的原因。.env 是这条路的后备。
    """
    import common.config as cfg

    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=from-dotenv\n", encoding="utf-8")

    spec = cfg.ModelSpec(
        provider="p", base_url="u", api_key_env="SOME_TEST_KEY", model="m-2025-01-01"
    )
    assert spec.resolve().api_key == "from-dotenv"


def test_a_real_environment_variable_wins_over_a_stale_dotenv(tmp_path, monkeypatch):
    """override=False 是刻意的。一份过期的 .env 不该盖掉刚设好的系统变量，
    否则这个后备机制自己会变成下一个坑。"""
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "from-process")
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=stale-value\n", encoding="utf-8")

    spec = cfg.ModelSpec(
        provider="p", base_url="u", api_key_env="SOME_TEST_KEY", model="m-2025-01-01"
    )
    assert spec.resolve().api_key == "from-process"


def test_key_source_reports_where_it_came_from_never_the_value(tmp_path, monkeypatch):
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "secret-value")
    assert cfg.key_source("SOME_TEST_KEY") == "process environment"
    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", True)  # 不去碰真实的 .env
    assert cfg.key_source("SOME_TEST_KEY") == "not found"
