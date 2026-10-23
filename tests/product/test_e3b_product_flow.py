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
    # 没有走到 E1 加载：范围说明只有不依赖目录的几条（支持的职业与本版不支持的内容）
    assert {n.topic for n in s.pre.preflight.scope_notes} == {"occupations", "unsupported"}


def test_two_real_cities_live_three_years_replay_and_report_public_facts(real_pack, tmp_path):
    from orchestration.life_runner import preflight_from_snapshot, replay_run

    s = sc.closed_loop(
        tmp_path,
        "e3b",
        draft_kwargs={"abilities": ("edu_at_least_hs",), "cities": (HOU, SEA)},
        repeat_indexes=(0,),
    )
    assert s.pre.preflight.status == "ready"
    assert s.result is not None and s.result.evaluation_status == "generated"
    assert {c.category for c in s.result.clones} == {"C"}  # 两城都正常过完、评分与估值完整
    start = s.pre.preflight.start
    assert start is not None and start.occupation_label == "办公室文员"
    run_dir = next((s.work / "archive").iterdir())
    rep = replay_run(preflight_from_snapshot(run_dir), run_dir)
    assert rep["planner_calls"] == 0 and rep["bindings_identical"]
    assert rep["evaluations_identical"] and rep["reports_identical"]
    _check_public_facts(s)


def _check_public_facts(s) -> None:
    """结果里的片区、通勤、用车权、设备故障与税逐字段对得上 E1 的月度通勤记录与结算记录（DECISIONS/0042）。"""
    world, result = s.pre.world, s.result
    for clone in result.clones:
        cat = world.bundle.catalog_for(clone.city_ref)
        areas = {a.area_id: a for a in cat.areas}
        lives = s.outcome.lives[clone.clone_ref]
        assert len(clone.years) == len(lives) == 3
        for facts, life in zip(clone.years, lives, strict=True):
            out = life.settlement.outcome
            months = {m.month: m for m in out.resources.months}
            assert sorted(m for g in facts.commute for m in g.months) == list(range(1, 13))
            for g in facts.commute:
                for mo in g.months:
                    c = months[mo].commute
                    assert (c.status, c.home_area) == (g.status, g.home_area_id)
                    assert [(u.mode, u.roundtrip_minutes, u.onsite_visits) for u in c.uses] == [
                        (t.mode, t.roundtrip_minutes, t.onsite_visits) for t in g.trips
                    ]
                    assert g.monthly_paid.amount_minor == round(c.paid.amount * 100)
                assert g.home_area_label == areas[g.home_area_id].label
            assert sorted(m for h in facts.homes for m in h.months) == list(range(1, 13))
            for h in facts.homes:
                area = areas[cat.opportunity(h.opportunity_id).area]
                assert h.area_typical_roundtrip_minutes == area.typical_roundtrip_minutes
                assert all(months[m].housing_opportunity_id == h.opportunity_id for m in h.months)
            assert (facts.tax.policy_id, facts.tax.status) == (
                cat.tax_policy_id,
                "official_2024_rules",
            )
            # mock 不买用车权；资料包里的设备都是模拟期内不随机坏
            assert facts.car_access.status == "not_bought"
            assert facts.device_failures == () and out.device_failures == ()
    assert [d.city_ref for d in result.data_nature] == [HOU, SEA]
    assert all(d.build_status == "from_pack_report" for d in result.data_nature)
    assert any("冻结的真实资料包" in x for x in result.sources_zh)
    assert not any("全部是合成设定" in x for x in result.sources_zh)
