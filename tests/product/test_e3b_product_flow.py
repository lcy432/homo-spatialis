"""E3B 六城真实资料包走产品链（DECISIONS/0042）：审阅 → 确认 → 编译 → E1 预检查 → 离线三年 → 评价 → 零调用重放。

为了让测试快，只用两座城（休斯敦、西雅图）、每城一个分身；完整的六城 × 4 分身 × 3 年由 ``scripts/run_product_full.py --mock
--pack configs/product/e3b_us_v1_pack.yaml`` 跑。人物是合成的（u501 文员），城市资料是冻结的真实资料包。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from product import scenarios as sc

E3B = Path("configs/product/e3b_us_v1_pack.yaml")
HOU, SEA = "US:CBSA2023:26420", "US:CBSA2023:42660"


@pytest.fixture
def real_pack(monkeypatch):
    monkeypatch.setattr(sc, "PACK_PATH", E3B)


def test_free_text_does_not_grant_the_credential_the_start_job_needs(real_pack, tmp_path):
    s = sc.preflight_only(tmp_path, "nohs", cities=(HOU,))
    assert s.pre.preflight.status == "blocked"
    codes = {i.code for i in s.pre.preflight.issues}
    assert codes == {"start_job_requires_unconfirmed_ability"}


def test_two_real_cities_live_three_years_and_replay_with_zero_calls(real_pack, tmp_path):
    from orchestration.life_runner import preflight_from_snapshot, replay_run

    s = sc.closed_loop(
        tmp_path,
        "e3b",
        draft_kwargs={"abilities": ("edu_at_least_hs",), "cities": (HOU, SEA)},
        repeat_indexes=(0,),
    )
    assert s.pre.preflight.status == "ready"
    assert s.result is not None and s.result.evaluation_status == "generated"
    # 两城的生活都过完三年；休斯敦评分与估值完整。西雅图：参考生活的固定住房（PI-47：最便宜的住房、居民通勤最短片区的一卧
    # 中档）从这个分身的岗位片区出发超过 60 分钟通勤上限，估值不完整，报模拟问题，不填 0（2026-10-23 重建资料包后菜单重抽，
    # 目录里的一卧中档换了一批）
    assert all(y.status == "completed" for c in s.result.clones for y in c.years)
    assert {c.clone_ref: (c.category, c.missing_reason) for c in s.result.clones} == {
        "u501.26420.r0": ("C", None),
        "u501.42660.r0": ("M", "valuation_incomplete"),
    }
    start = s.pre.preflight.start
    assert start is not None and start.occupation_label == "办公室文员"
    run_dir = next((s.work / "archive").iterdir())
    rep = replay_run(preflight_from_snapshot(run_dir), run_dir)
    assert rep["planner_calls"] == 0 and rep["bindings_identical"]
    assert rep["evaluations_identical"] and rep["reports_identical"]
