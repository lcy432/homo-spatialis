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


def test_dotenv_does_not_override_the_process_environment(tmp_path, monkeypatch):
    """`override=False` 仍然成立：真实环境变量优先于 .env。

    这是 12-factor 的约定，也是部署时唯一说得通的方向。改变的不是优先级，
    而是**两处不一致时不再静默地按优先级挑一个**——见下面那条冲突测试。
    """
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "from-process")
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=stale-value\n", encoding="utf-8")

    cfg._ensure_dotenv()
    import os

    assert os.environ["SOME_TEST_KEY"] == "from-process"


def test_key_source_reports_where_it_came_from_never_the_value(tmp_path, monkeypatch):
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "secret-value")
    assert cfg.key_source("SOME_TEST_KEY") == "process environment"
    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", True)  # 不去碰真实的 .env
    assert cfg.key_source("SOME_TEST_KEY") == "not found"


def test_a_conflicting_dotenv_and_process_env_is_reported(tmp_path, monkeypatch):
    """两处取值不同时进程环境赢，而"赢了的是旧的"表现为 401——看起来像
    凭证坏了，不像配置冲突。

    2026-09-06 实测到的正是这一形态：新 key 在 .env 里，旧的已吊销 key 还
    留在系统环境变量里，探针连报两次 401，而 .env 里那个 key 单独测完全
    正常。不改 precedence（12-factor 说真实环境变量优先，那是对的），改成
    把冲突报出来。
    """
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "stale-from-process")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=fresh-from-dotenv\n", encoding="utf-8")

    assert cfg.dotenv_conflicts() == {"SOME_TEST_KEY": "fresh-from-dotenv"}


def test_agreeing_values_are_not_a_conflict(tmp_path, monkeypatch):
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "same")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=same\n", encoding="utf-8")
    assert cfg.dotenv_conflicts() == {}


def test_resolve_refuses_a_conflicted_key(tmp_path, monkeypatch):
    """静默地选一个，无论选哪个都会在某天骗到人。"""
    import common.config as cfg

    monkeypatch.setenv("SOME_TEST_KEY", "stale")
    monkeypatch.setattr(cfg, "_DOTENV_LOADED", True)  # 不去动真实的 .env
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOME_TEST_KEY=fresh\n", encoding="utf-8")

    spec = cfg.ModelSpec(
        provider="p", base_url="u", api_key_env="SOME_TEST_KEY", model="m-2025-01-01"
    )
    with pytest.raises(cfg.ConfigError, match="取值不同"):
        spec.resolve()
