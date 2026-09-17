"""门一的世界标定选择键。

这个键是为长通勤研究 profile 加的：`DECISIONS/0012` 第六节写明「若将来把长
通勤臂升为主臂，门一必须重跑」，而 `DECISIONS/0013` 的 P8 参考设计正是那条臂。

**这里钉住的是两件不同的事**：缺省不变（旧结果继续有效），以及换了标定就是
另一次预注册运行（旧结果的适用范围不扩大）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval.gates import config_sha
from worlds.attributes import (
    CALIBRATIONS,
    DEFAULT_CALIBRATION_NAME,
    LONG_COMMUTE_ARM_2024,
    PROVISIONAL_US_2024,
)
from worlds.generator import WorldGenerator
from worlds.personas import reference_persona

DEFAULT_CONFIG = Path("configs/gate_one.yaml")
LONG_COMMUTE_CONFIG = Path("configs/gate_one_long_commute.yaml")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_default_calibration_name_resolves_to_the_legacy_object() -> None:
    assert CALIBRATIONS[DEFAULT_CALIBRATION_NAME] is PROVISIONAL_US_2024
    assert CALIBRATIONS["LONG_COMMUTE_ARM_2024"] is LONG_COMMUTE_ARM_2024


def test_the_shipped_default_config_does_not_name_a_calibration() -> None:
    """默认配置不写这个键——它写了，`sample.get` 的缺省分支就再也测不到了。"""
    assert "world_calibration" not in _load(DEFAULT_CONFIG)["sample"]


def test_an_absent_key_gives_the_same_world_as_before_the_key_existed() -> None:
    """缺省路径逐位一致。这是 2026-09-11 那次门一结果仍然有效的全部依据。"""
    sample = _load(DEFAULT_CONFIG)["sample"]
    calibration = CALIBRATIONS[sample.get("world_calibration", DEFAULT_CALIBRATION_NAME)]

    legacy = WorldGenerator(n_cities=sample["n_cities"])
    switched = WorldGenerator(n_cities=sample["n_cities"], calibration=calibration)

    persona = reference_persona()
    a = legacy.generate(seed=sample["world_seed"], reference_persona=persona)
    b = switched.generate(seed=sample["world_seed"], reference_persona=persona)

    assert a.cities == b.cities
    assert a.condition_number == b.condition_number
    assert a.label_permutation == b.label_permutation
    assert legacy.tax() == switched.tax()


def test_the_equality_check_above_has_teeth() -> None:
    """上一条用 `==` 比城市表。先证明这个比较能失败，否则它什么也没钉住。"""
    sample = _load(DEFAULT_CONFIG)["sample"]
    persona = reference_persona()
    legacy = WorldGenerator(n_cities=sample["n_cities"]).generate(
        seed=sample["world_seed"], reference_persona=persona
    )
    long_commute = WorldGenerator(
        n_cities=sample["n_cities"], calibration=LONG_COMMUTE_ARM_2024
    ).generate(seed=sample["world_seed"], reference_persona=persona)

    assert legacy.cities != long_commute.cities


def test_the_long_commute_config_changes_the_calibration_and_nothing_else() -> None:
    """判据一字未动——否则这次 PASS 说明不了长通勤臂的任何事。"""
    default = _load(DEFAULT_CONFIG)
    long_commute = _load(LONG_COMMUTE_CONFIG)

    assert long_commute["sample"]["world_calibration"] == "LONG_COMMUTE_ARM_2024"

    stripped = {**long_commute, "sample": dict(long_commute["sample"])}
    del stripped["sample"]["world_calibration"]
    assert stripped == default


def test_the_two_configs_hash_differently_so_the_old_verdict_is_not_reused() -> None:
    """两次运行、两个哈希。共用一个哈希就等于让旧判定替新臂背书。"""
    assert config_sha(DEFAULT_CONFIG) != config_sha(LONG_COMMUTE_CONFIG)


def test_an_unknown_calibration_name_fails_loudly() -> None:
    with pytest.raises(KeyError):
        CALIBRATIONS["NO_SUCH_CALIBRATION_2024"]
