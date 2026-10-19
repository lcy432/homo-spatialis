"""小批次：单个分身停止，不等于整场模拟停止（E1-4B r2 修复 prompt 第六节；`DECISIONS/0021` 第十三节）。

被测的是演示层的薄循环 ``scripts/demo_life_e1_4b.run_batch``——不是正式多分身 runner（正式调度、重试与异常
隔离在 E1-7）。这里核对：预期的停止只停这个分身；报告保留全部预定分身与停止原因，分类互斥、能与原计划人数
核对；不给停止后的年份补分、补现金流或补行动；单独运行与在批次里运行结果一致；意外程序错误显式报告并停止
批次，不被吞掉。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from .feedback_support import OFF, variant

SCRIPT = Path("scripts/demo_life_e1_4b.py")
CLONES = ["u31.a61.c0", "u33.a61.c0", "u34.a61.c0"]
A61 = "US:SYNTH2026:A61"


@pytest.fixture(scope="module")
def demo():
    scripts = str(SCRIPT.parent.resolve())
    sys.path.insert(0, scripts)  # 演示脚本先 import _bootstrap（在 scripts/ 里）
    spec = importlib.util.spec_from_file_location("demo_life_e1_4b_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)
        sys.path.remove(scripts)


@pytest.fixture(scope="module")
def world(demo, bundle4b, scoring4b):
    return demo.World(bundle4b, scoring4b)


def _by_clone(batch):
    return {r["clone_id"]: r for r in batch["clones"]}


def test_one_clone_stopping_does_not_end_the_batch(demo, world, bundle4b):
    batch = demo.run_batch(world, bundle4b.protocol, CLONES)
    recs = _by_clone(batch)
    assert [r["clone_id"] for r in batch["clones"]] == CLONES  # 所有预定分身都在
    assert {c: recs[c]["category"] for c in CLONES} == {
        "u31.a61.c0": "completed",
        "u33.a61.c0": "feedback_blocked",
        "u34.a61.c0": "exited",
    }
    assert batch["batch_status"] == "finished_partially_complete"  # 执行结束、结果部分完整
    # 不补停止之后的年份：u31 三年，u33 与 u34 各一年
    assert [len(batch["runs"][c]) for c in CLONES] == [3, 1, 1]
    assert [recs[c]["years_recorded"] for c in CLONES] == [3, 1, 1]
    # 反馈受阻者保留当年结算；出局者保留出局年月与已发生的生活
    (u33,) = batch["runs"]["u33.a61.c0"]
    assert u33.settlement.status == "completed" and u33.settlement.outcome is not None
    assert u33.next_state is None and recs["u33.a61.c0"]["last_settled_year"] == 1
    (u34,) = batch["runs"]["u34.a61.c0"]
    assert recs["u34.a61.c0"]["exit"] == {
        "year": 1,
        "month": 4,
        "reason": "minimum_living_unaffordable",
    }
    assert u34.settlement.outcome is not None and len(u34.settlement.outcome.months) == 4
    # 按城市的互斥计数，合计等于原计划人数
    row = batch["per_city"][A61]
    assert row["planned"] == 3
    assert {k: v for k, v in row.items() if v and k != "planned"} == {
        "completed": 1,
        "feedback_blocked": 1,
        "exited": 1,
    }


def test_running_in_a_batch_equals_running_alone(demo, world, bundle4b):
    for protocol in (bundle4b.protocol, variant(bundle4b.protocol, **OFF)):
        batch = demo.run_batch(world, protocol, CLONES)
        for clone in CLONES:
            alone = world.run(protocol, clone)
            assert [r.model_dump() for r in batch["runs"][clone]] == [r.model_dump() for r in alone]


def test_the_off_arm_batch_keeps_each_stop_separate(demo, world, bundle4b):
    """关闭反馈：u31 第 2 年每月负担 255 > 250，计划不可行（第 1 年保留）；u33 照常完成三年；u34 出局。"""
    batch = demo.run_batch(world, variant(bundle4b.protocol, **OFF), CLONES)
    recs = _by_clone(batch)
    assert {c: recs[c]["category"] for c in CLONES} == {
        "u31.a61.c0": "plan_infeasible",
        "u33.a61.c0": "completed",
        "u34.a61.c0": "exited",
    }
    u31 = recs["u31.a61.c0"]
    assert (u31["stop_year"], u31["last_settled_year"]) == (2, 1)
    # u33 完成了全部三个行动年，但三年的满足感都不完整："完成"不等于"能完整评分"
    assert recs["u33.a61.c0"]["years_scored_completely"] == 0
    assert "满足感完整的年份：0 个" in recs["u33.a61.c0"]["stop_reason"]
    assert u31["problems"] == ["energy_over_limit（第 1 月）"]
    assert len(batch["runs"]["u31.a61.c0"]) == 2  # 失败的第 2 年只有诊断，没有补造的生活账目
    assert batch["runs"]["u31.a61.c0"][1].settlement.outcome is None


def test_a_missing_plan_is_an_input_problem_reported_per_city(demo, world, bundle4b):
    """u31.b62.c0 只声明了第 1 年的计划（N = 3）：第 1 年照常交接，之后报"第 2 年没有预先声明的计划"。"""
    batch = demo.run_batch(world, bundle4b.protocol, [*CLONES, "u31.b62.c0"])
    rec = _by_clone(batch)["u31.b62.c0"]
    assert rec["category"] == "input_or_data_error"
    assert "第 2 年没有预先声明的计划" in rec["stop_reason"]
    assert batch["per_city"]["US:SYNTH2026:B62"]["planned"] == 1
    assert batch["per_city"][A61]["planned"] == 3


def test_an_unexpected_error_is_reported_and_stops_the_batch(demo, world, bundle4b):
    """意外程序错误不被吞掉：显式记录，停止批次，后面的分身标"未运行"，不假定错误只影响一个分身。"""

    def runner(protocol, clone):
        if clone == "u33.a61.c0":
            raise RuntimeError("模拟的程序错误")
        return world.run(protocol, clone)

    batch = demo.run_batch(world, bundle4b.protocol, CLONES, runner=runner)
    recs = _by_clone(batch)
    assert batch["batch_status"] == "stopped_unexpected_error"
    assert [recs[c]["category"] for c in CLONES] == ["completed", "unexpected_error", "not_run"]
    assert recs["u33.a61.c0"]["stop_reason"] == "RuntimeError: 模拟的程序错误"
    assert "u34.a61.c0" not in batch["runs"]
    row = batch["per_city"][A61]
    assert row["planned"] == 3 == row["completed"] + row["unexpected_error"] + row["not_run"]


def test_the_three_capabilities_are_reported_separately(demo, world, bundle4b):
    """能执行 ≠ 能完整评分 ≠ 能更新精力：逐年分开报告，缺口写明在哪里。"""
    recs = _by_clone(demo.run_batch(world, bundle4b.protocol, CLONES))
    (u33,) = recs["u33.a61.c0"]["years"]
    assert u33["can_execute"] == "能执行"
    assert (
        u33["can_score_completely"] == "不完整：物质细项 ['home_cleaning'] 有正权重但没有评分分项"
    )
    assert u33["can_update_energy"] == "不能确定（反馈信息不足）"
    u31 = recs["u31.a61.c0"]["years"]
    assert [y["can_score_completely"] for y in u31] == ["完整", "完整", "完整"]
    assert [y["can_update_energy"] for y in u31] == [
        "已确定：264.01 负担点",
        "已确定：264.01 负担点",
        "不需要（下一年是终态）",
    ]
    (u34,) = recs["u34.a61.c0"]["years"]
    assert u34["can_execute"] == "执行到出局（第 4 月）"
    assert u34["can_update_energy"] == "不适用（出局）"
