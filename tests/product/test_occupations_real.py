"""E3B 职业与岗位一侧（DECISIONS/0042）：支持范围、能力只从结构化事实来、岗位工资与安排能手算核对、开局规则不替换职业、培训只授予
范围内要求证书的职业的能力。只读 ``tests/fixtures/life_items/``，不读原始缓存、不联网。"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from common.life_contracts import AbilitySpec, parse_eligibility_tag
from product.realdata.life_freeze import freeze
from product.realdata.occupations import Scope, start_job

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/fixtures/life_items/slice_sample.json"


def _yaml(rel: str) -> dict:
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfgs():
    items = _yaml("configs/intake/life_items_us_v1.yaml")
    occ = _yaml("configs/intake/occupations_us_v1.yaml")
    baskets = _yaml("configs/intake/us_v1.yaml")["mapping"]["a_food"]["baskets"]
    return items, occ, baskets


@pytest.fixture(scope="module")
def sl():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scope(cfgs, sl):
    return Scope(cfgs[1], sl)


@pytest.fixture(scope="module")
def frozen(tmp_path_factory, sl, cfgs):
    out = tmp_path_factory.mktemp("occ")
    return out, freeze(sl, *cfgs, out)


def city(frozen, cbsa: str) -> dict:
    return json.loads((frozen[0] / f"{cbsa}.json").read_text(encoding="utf-8"))


def by_id(frozen, cbsa: str) -> dict[str, dict]:
    return {i["opportunity"]["opportunity_id"]: i for i in city(frozen, cbsa)["items"]}


def test_scope_is_consistent_with_ep_and_has_thirty_to_forty_occupations(scope):
    assert scope.problems() == []
    assert 30 <= len(scope.supported) <= 40
    assert not set(scope.supported) & set(scope.cfg["excluded"])


def test_required_abilities_follow_ep_education_and_experience(scope):
    assert scope.required("35-3023") == ()  # 没有正式学历要求
    assert scope.required("43-9061") == ("edu_at_least_hs",)
    assert scope.required("43-3031") == ("edu_at_least_some_college",)
    assert scope.required("49-3023") == ("award_49_3023",)  # 中学后非学位证书：职业专属
    assert scope.required("13-2011") == ("edu_at_least_bachelor",)
    assert scope.required("41-1011") == ("edu_at_least_hs", "exp_related_41_1011")


def test_ability_vocabulary_is_valid_and_scoped(scope, frozen):
    specs = [AbilitySpec.model_validate(a) for a in frozen[1]["abilities"]]
    ids = [a.ability_id for a in specs]
    assert len(ids) == len(set(ids))
    by = {a.ability_id: a for a in specs}
    assert by["edu_at_least_bachelor"].scope.kind == "general"
    assert (
        by["award_49_3023"].scope.kind == "occupation"
        and by["award_49_3023"].scope.occupation.code == "49-3023"
    )
    assert by["exp_related_35_1012"].kind == "knowledge"
    for item in by_id(frozen, "35620").values():
        for tag in item["opportunity"]["eligibility"]:
            parsed = parse_eligibility_tag(tag)
            assert parsed is not None and parsed[0] == "ability" and parsed[1] in by


def test_confirmed_facts_map_to_abilities_without_inferring_degrees(scope):
    r = scope.from_confirmed(education_level="bachelor")
    assert r["abilities"] == [
        "edu_at_least_associate",
        "edu_at_least_bachelor",
        "edu_at_least_hs",
        "edu_at_least_some_college",
    ]
    assert scope.from_confirmed(education_level=None, experience=(("41-2031", 2),))[
        "abilities"
    ] == ["exp_related_41_1011"]
    assert (
        scope.from_confirmed(education_level=None, experience=(("41-2031", 0),))["abilities"] == []
    )
    # 当了十年会计也不授予学位或证书
    assert (
        scope.from_confirmed(education_level=None, experience=(("13-2011", 10),))["abilities"] == []
    )
    r = scope.from_confirmed(education_level="hs", awards=("49-3023", "13-2011"))
    assert r["abilities"] == ["award_49_3023", "edu_at_least_hs"] and len(r["notes"]) == 1
    with pytest.raises(ValueError):
        scope.from_confirmed(education_level="phd")


def test_job_pay_and_arrangement_hand_check(frozen):
    jobs = by_id(frozen, "35620")
    fast = jobs["job.35620.35-3023.median"]["opportunity"]
    # 中位年薪 35,377.14 × 每周 36 小时 ÷ 40 = 31,839.426 → 31,839.43；每月 36 × 52 ÷ 12 = 156 小时；每周 5 天 → 每月 22 天
    assert fast["compensation"]["amount"]["amount"] == 31839.43
    job = fast["resource"]["job"]
    assert (
        job["work_mode"],
        job["work_hours_per_month"],
        job["workdays_per_month"],
        job["onsite_days_per_month"],
    ) == (
        "onsite",
        156.0,
        22,
        22,
    )
    acct = jobs["job.35620.13-2011.median"]["opportunity"]
    assert acct["compensation"]["amount"]["amount"] == 104972.84  # 每周 40 小时：年薪原样
    # 40 × 52 ÷ 12 = 173.33…；hybrid 每 5 天到岗 3 天：22 × 3 ÷ 5 = 13.2 → 13
    assert acct["resource"]["job"]["work_hours_per_month"] == 173.33
    assert acct["resource"]["job"]["onsite_days_per_month"] == 13
    assert acct["eligibility"] == ["ability:edu_at_least_bachelor"]


def test_jobs_carry_the_soc_leave_the_area_for_integration_and_come_in_three_tiers(frozen, scope):
    jobs = {k: v for k, v in by_id(frozen, "16980").items() if k.startswith("job.")}
    assert len(jobs) == 3 * len(scope.supported)
    for v in jobs.values():
        o = v["opportunity"]
        assert o.get("area") is None and o["occupation"]["scheme"] == "SOC2018"
        assert v["status"] == "needs_area" and "work_area_assigned_at_integration" in v["gaps"]
    p25, med, p75 = (
        jobs[f"job.16980.43-9061.{t}"]["opportunity"]["compensation"]["amount"]["amount"]
        for t in ("p25", "median", "p75")
    )
    assert p25 < med < p75


def test_start_job_uses_the_median_tier_and_never_substitutes(frozen, cfgs):
    index = city(frozen, "16980")["start_jobs"]
    ok = start_job(index, cfgs[1], "43-9061")
    assert ok == {
        "status": "ok",
        "opportunity_id": "job.16980.43-9061.median",
        "requires_confirmed_abilities": ["edu_at_least_hs"],
    }
    massage = start_job(index, cfgs[1], "31-9011")
    assert (
        massage["status"] == "rejected"
        and "执照" in massage["reason_zh"]
        and "不替换" in massage["reason_zh"]
    )
    unknown = start_job(index, cfgs[1], "11-1021")
    assert unknown["status"] == "rejected" and "不在支持范围" in unknown["reason_zh"]


def test_training_grants_only_in_scope_awards_and_only_whole_years(frozen, sl):
    items = by_id(frozen, "35620")
    t = items["training.35620.183725.4"]["opportunity"]
    src = next(
        p
        for p in sl["metros"]["35620"]["training"]
        if p["record_id"] == "training.ipeds2023.183725.4"
    )
    assert t["grants"] == [
        "award_49_2022"
    ]  # CIP 47.0103 → 49-2022 在范围内且要求证书；同一 CIP 的其他职业不在范围
    assert (
        t["duration"]["completion_years"] == 1 and t["resource"]["hours"] == 104.0
    )  # 24 × 52 ÷ 12
    assert t["price"]["amount"]["amount"] == float(Fraction(src["tuition"]))
    assert "start_dates_unknown" in items["training.35620.183725.4"]["gaps"]
    keys = {
        (e["kind"], e["key"])
        for e in city(frozen, "35620")["not_generated"]
        if e["domain"] == "training"
    }
    assert {
        ("gap", "length_not_whole_years"),
        ("not_applicable", "no_in_scope_award"),
        ("gap", "tuition"),
    } <= keys
