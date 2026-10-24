"""参考住房选择器（E3B 收尾，``reference_housing_coverage_commute_v3``）：覆盖 3/5、实际通勤检查与档次排序。

合成样例 K51：近处片区到就业中心公共交通往返 30 分钟、月租 1,400；远处 70 分钟、月租 1,000（两处都是一卧中档）；文员每月
到岗 20 次。``with_car`` 再加远处开车 40 分钟（要用车权）。全部手算，不调用模型。
"""

from __future__ import annotations

import pytest

from common.life_contracts import HardRequirement
from common.life_evaluation_contracts import HousingSelector, coverage_status

from .conftest import K51
from .test_e3b_engine import add_home_area, catalog_with, opp, with_car

CENTER = "k51.area.center"


def selector(kind: str, *cands: tuple[str, str, str, int, int]) -> HousingSelector:
    return HousingSelector.model_validate(
        {
            "rule_id": "reference_housing_coverage_commute_v3",
            "kind": kind,
            "coverage_threshold": "3/5",
            "commute_identity": "0" * 64,
            "work_areas": [{"area_id": CENTER, "inbound_weight": 100}],
            "candidates": [
                {
                    "opportunity_id": oid,
                    "area": area,
                    "coverage": cov,
                    "covered_weight": covered,
                    "unknown_weight": unknown,
                }
                for oid, area, cov, covered, unknown in cands
            ],
        }
    )


NEAR = ("k51.housing.near", "k51.area.near", "eligible", 100, 0)
FAR = ("k51.housing.far", "k51.area.far", "eligible", 100, 0)


def limit(minutes: int) -> tuple[HardRequirement, ...]:
    return (
        HardRequirement(
            need="housing_commute",
            requirement={"category": "housing", "max_commute_roundtrip_minutes": minutes},
        ),
    )


def pick(cat, sel, *, car=False, jobs=None, requirements=()):
    from econ.life_terminal_value import _select_housing

    jobs = [cat.opportunity("k51.job.clerk")] if jobs is None else jobs
    functions = frozenset({"car_access"} if car else ())
    return _select_housing("housing_basic", sel, cat, jobs, functions, requirements)


def with_west(d: dict) -> None:
    """再加一个没有任何线路记录的居住片区和一处更便宜的房子（月租 900）：线路缺失，没有明确不可达证据。"""
    with_car(d)
    add_home_area(d, "k51.area.west", 50)
    d["commute"]["legs"].pop()  # add_home_area 顺手加的线去掉：这个片区到就业中心没有任何方式的记录
    west = dict(opp(d, "k51.housing.far"))
    west.update(opportunity_id="k51.housing.west", area="k51.area.west")
    west["price"] = {**west["price"], "amount": {**west["price"]["amount"], "amount": 900}}
    d["opportunities"].append(west)
    for m in d["minimum_living"]:
        if m["need"] == "housing":
            m["qualifying_opportunity_ids"] = sorted(
                [*m["qualifying_opportunity_ids"], "k51.housing.west"]
            )


WEST = ("k51.housing.west", "k51.area.west", "eligible", 100, 0)


@pytest.fixture
def cat(bundle):
    return catalog_with(bundle, with_car)


def test_basic_takes_the_cheapest_home_whose_actual_commute_works(cat):
    o, kind, _, rec = pick(cat, selector("basic", NEAR, FAR))
    assert o.opportunity_id == "k51.housing.far" and kind is None
    assert rec.status == "selected" and rec.longest_roundtrip_minutes == 70
    assert rec.onsite_job_areas == (CENTER,) and rec.functions == ()
    assert (rec.passed, rec.unknown, rec.failed) == (2, 0, 0)
    assert rec.covered_weight == 100 and rec.total_weight == 100


def test_the_hard_commute_limit_rules_out_the_slow_home_without_a_car(cat):
    o, _, _, rec = pick(cat, selector("basic", NEAR, FAR), requirements=limit(60))
    assert o.opportunity_id == "k51.housing.near" and rec.failed == 1
    assert rec.commute_limits == (60,)


def test_a_car_template_may_drive_and_a_no_car_template_may_not(cat):
    o, _, _, rec = pick(cat, selector("basic", NEAR, FAR), car=True, requirements=limit(60))
    assert o.opportunity_id == "k51.housing.far"  # 开车 40 分钟 ≤ 60
    assert rec.longest_roundtrip_minutes == 40 and rec.functions == ("car_access",)


def test_comfort_minimises_the_longest_actual_trip_before_rent(cat):
    o, _, _, rec = pick(cat, selector("comfort", NEAR, FAR))
    assert o.opportunity_id == "k51.housing.near" and rec.longest_roundtrip_minutes == 30
    o, _, _, _ = pick(cat, selector("comfort", NEAR, FAR), car=True)
    assert o.opportunity_id == "k51.housing.near"  # 近处公交 30 分钟仍比远处开车 40 分钟短


def test_without_onsite_jobs_the_commute_value_is_zero_and_rent_decides(cat):
    clerk = cat.opportunity("k51.job.clerk")
    job = clerk.resource.job.model_copy(update={"onsite_days_per_month": 0})
    remote = clerk.model_copy(update={"resource": clerk.resource.model_copy(update={"job": job})})
    o, _, _, rec = pick(cat, selector("comfort", NEAR, FAR), jobs=[remote], requirements=limit(10))
    assert o.opportunity_id == "k51.housing.far" and rec.longest_roundtrip_minutes == 0
    assert rec.remote_jobs == 1 and rec.onsite_job_areas == ()


def test_a_missing_line_is_unknown_not_unreachable(bundle):
    cat = catalog_with(bundle, with_west)
    o, kind, _, rec = pick(cat, selector("basic", NEAR, FAR, WEST))
    # 西边更便宜但线路资料缺失：选已知合格里最便宜的远处，并记下排在前面的未知候选
    assert (
        o.opportunity_id == "k51.housing.far" and rec.unknown == 1 and rec.unknown_ranked_ahead == 1
    )
    o, kind, why, rec = pick(cat, selector("basic", NEAR, FAR, WEST), requirements=limit(20))
    assert o is None and kind == "slot_unknown" and rec.status == "unknown"
    assert (rec.passed, rec.unknown, rec.failed) == (0, 1, 2) and "资料未知" in why


def test_every_candidate_known_to_fail_is_unavailable(cat):
    o, kind, why, rec = pick(cat, selector("basic", NEAR, FAR), requirements=limit(20))
    assert o is None and kind == "slot_unavailable" and rec.status == "unavailable"
    assert "超过通勤硬要求 2 个" in why


def test_coverage_unknown_candidates_are_never_selected_as_known(cat):
    shaky = ("k51.housing.far", "k51.area.far", "unknown", 50, 50)
    o, _, _, rec = pick(cat, selector("basic", NEAR, shaky))
    assert o.opportunity_id == "k51.housing.near" and rec.unknown_ranked_ahead == 1


def test_coverage_threshold_is_three_fifths_with_unknowns_kept_in_the_denominator():
    assert coverage_status(60, 0, 100) == "eligible"
    assert coverage_status(59, 1, 100) == "unknown"
    assert coverage_status(59, 0, 100) == "excluded"
    with pytest.raises(ValueError, match="不能默认等权"):
        coverage_status(0, 0, 0)
    with pytest.raises(ValueError, match="按阈值 3/5"):
        selector("basic", ("k51.housing.far", "k51.area.far", "eligible", 59, 1))


def test_the_ledger_recomputes_coverage_from_the_catalog(bundle):
    from ledger.life_evaluation_config import _selector_problems

    cat = catalog_with(bundle, with_west)
    assert _selector_problems("housing_basic", selector("basic", NEAR, FAR), cat) == []
    lie = ("k51.housing.west", "k51.area.west", "eligible", 100, 0)  # 西边其实一条线都没有
    problems = _selector_problems("housing_basic", selector("basic", lie), cat)
    assert len(problems) == 1 and "重算的 0 / 100" in problems[0]
    assert K51 in problems[0]


def with_annex(d: dict) -> None:
    """再加一个就业片区（只有近处有一条公交线，往返 50 分钟）和那里的一份兼职：多岗时逐一检查，取最长往返。"""
    with_car(d)
    src = d["areas"][0]["typical_source"]
    d["areas"].append(
        {"area_id": "k51.area.annex", "label": "annex", "kind": "work", "note": "测试片区"}
    )
    leg = dict(d["commute"]["legs"][0])  # 近处 → 就业中心
    d["commute"]["legs"].append({**leg, "work_area": "k51.area.annex", "roundtrip_minutes": 50})
    job = dict(opp(d, "k51.job.shop_parttime"))
    job.update(opportunity_id="k51.job.annex_parttime", area="k51.area.annex")
    d["opportunities"].append(job)
    assert src


def test_every_actual_job_is_checked_and_an_uncovered_one_is_not_waved_through(bundle):
    cat = catalog_with(bundle, with_annex)
    jobs = [cat.opportunity("k51.job.clerk"), cat.opportunity("k51.job.annex_parttime")]
    o, _, _, rec = pick(cat, selector("basic", NEAR, FAR), jobs=jobs)
    # 远处到兼职片区一条线都没有：资料未知，不能蒙混执行；近处两份岗位都到得了，最长 50 分钟
    assert o.opportunity_id == "k51.housing.near" and rec.longest_roundtrip_minutes == 50
    assert rec.onsite_job_areas == ("k51.area.annex", CENTER) and rec.unknown == 1
    o, kind, _, rec = pick(cat, selector("basic", NEAR, FAR), jobs=jobs, requirements=limit(40))
    assert o is None and kind == "slot_unknown"  # 近处超限（已知），远处未知 → 不是确定没有


def test_a_new_binding_rule_changes_the_evaluation_identity_but_not_the_scoring(
    evaluation, scoring
):
    from common.life_evaluation_contracts import EvaluationSpec

    data = evaluation.model_dump(mode="json")
    slot = next(s["slot_id"] for s in data["slots"] if s["role"] == "housing")
    sel = selector("basic", NEAR, FAR).model_dump(mode="json")
    for b in data["city_bindings"]:
        for t in b["targets"]:
            if t["slot_id"] == slot:
                t.clear()
                t.update(slot_id=slot, status="selector", housing_selector=sel)
    changed = EvaluationSpec.model_validate(data)
    assert changed.content_fingerprint != evaluation.content_fingerprint
    assert len(changed.life_templates) == len(evaluation.life_templates)
    scoring_before = scoring.spec.model_dump_json()
    assert scoring.spec.model_dump_json() == scoring_before  # 评分规格没有跟着动
