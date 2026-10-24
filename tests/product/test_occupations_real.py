"""E3B 职业与岗位一侧（DECISIONS/0042）：支持范围、能力只从结构化事实来、岗位工资与安排能手算核对、开局规则不替换职业、培训只授予
范围内要求证书的职业的能力。只读 ``tests/fixtures/life_items/``，不读原始缓存、不联网。"""

from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from common.life_contracts import AbilitySpec, parse_eligibility_tag
from product.realdata.life_freeze import freeze
from product.realdata.occupations import Scope, job_opportunity, start_job, wage_support

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
    assert scope.required("49-3023") == ("soc_49_3023_award",)  # 中学后非学位证书：职业专属
    assert scope.required("13-2011") == ("edu_at_least_bachelor",)
    assert scope.required("41-1011") == ("edu_at_least_hs", "soc_41_1011_experience")


def test_ability_vocabulary_is_valid_and_scoped(scope, frozen):
    specs = [AbilitySpec.model_validate(a) for a in frozen[1]["abilities"]]
    ids = [a.ability_id for a in specs]
    assert len(ids) == len(set(ids))
    by = {a.ability_id: a for a in specs}
    assert by["edu_at_least_bachelor"].scope.kind == "general"
    assert (
        by["soc_49_3023_award"].scope.kind == "occupation"
        and by["soc_49_3023_award"].scope.occupation.code == "49-3023"
    )
    assert by["soc_35_1012_experience"].kind == "knowledge"
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
    ] == ["soc_41_1011_experience"]
    assert (
        scope.from_confirmed(education_level=None, experience=(("41-2031", 0),))["abilities"] == []
    )
    # 当了十年会计也不授予学位或证书
    assert (
        scope.from_confirmed(education_level=None, experience=(("13-2011", 10),))["abilities"] == []
    )
    r = scope.from_confirmed(education_level="hs", awards=("49-3023", "13-2011"))
    assert r["abilities"] == ["edu_at_least_hs", "soc_49_3023_award"] and len(r["notes"]) == 1
    with pytest.raises(ValueError):
        scope.from_confirmed(education_level="phd")


def templates(frozen, cbsa: str) -> dict[str, dict]:
    return {x["soc"]: x for x in city(frozen, cbsa)["job_templates"]}


def test_job_pay_and_arrangement_hand_check(frozen):
    """岗位 = 模板 × 年薪 × （每周工时，就业片区）：薪酬 = 年薪 × 每周工时 ÷ 40；每月工时 = 每周 × 52 ÷ 12；每周 5 天、每月 22 天。"""
    rules = frozen[1]["job_rules"]

    def job(soc: str, hours: int):
        tpl = templates(frozen, "35620")[soc]
        return job_opportunity(
            tpl,
            rules,
            annual=Decimal(tpl["median_annual"]),
            weekly_hours=hours,
            area="nyc.area.w.x",
            opportunity_id=f"nyc.job.{soc}.test",
            wage_label="p50",
            numeraire="USD_2024_LOCAL",
            mapping_version="test",
            hours_level="test",
        )

    fast = job("35-3023", 36)
    # 中位年薪 35,377.14 × 每周 36 小时 ÷ 40 = 31,839.426 → 31,839.43；每月 36 × 52 ÷ 12 = 156 小时；每周 5 天 → 每月 22 天
    assert fast.compensation.amount.amount == 31839.43
    arr = fast.resource.job
    assert (
        arr.work_mode,
        arr.work_hours_per_month,
        arr.workdays_per_month,
        arr.onsite_days_per_month,
    ) == ("onsite", 156.0, 22, 22)
    acct = job("13-2011", 40)
    assert acct.compensation.amount.amount == 104972.84  # 每周 40 小时：年薪原样
    # 40 × 52 ÷ 12 = 173.33…；hybrid 每 5 天到岗 3 天：22 × 3 ÷ 5 = 13.2 → 13
    assert acct.resource.job.work_hours_per_month == 173.33
    assert acct.resource.job.onsite_days_per_month == 13
    assert acct.eligibility == ("ability:edu_at_least_bachelor",)
    # 低工时 = 每天工作短，不减少到岗天数：每周 10 小时 → 每月 43.33 小时，仍是 22 天
    short = job("35-3023", 10)
    assert short.resource.job.work_hours_per_month == 43.33
    assert short.resource.job.workdays_per_month == 22


def test_templates_carry_twenty_wage_points_and_leave_the_tails_unidentified(frozen, scope):
    tps = templates(frozen, "16980")
    assert set(tps) <= set(scope.supported) and len(tps) >= 30
    assert not any(
        i.startswith("chi.job.") for i in by_id(frozen, "16980")
    )  # 岗位在资料包组装时展开
    w = tps["43-9061"]["wage"]
    assert len(w["points"]) == 20 and w["unidentified_tails"] == "1/5"
    assert all(p["mass"] == "1/25" for p in w["points"])
    assert [p["quantile"] for p in w["points"][:3]] == ["0.12", "0.16", "0.20"]
    annual = [Decimal(p["annual"]) for p in w["points"]]
    assert annual == sorted(annual)


def test_wage_support_is_piecewise_linear_between_published_quantiles():
    w = {
        "annual_wage_p10": "20000",
        "annual_wage_p25": "35000",
        "annual_wage_median": "50000",
        "annual_wage_p75": "70000",
        "annual_wage_p90": "100000",
    }
    pts = wage_support(w)["points"]
    # 分位 0.12：20,000 + 15,000 × (0.12 − 0.10) ÷ 0.15 = 22,000；分位 0.52：50,000 + 20,000 × 0.02 ÷ 0.25 = 51,600
    assert pts[0]["annual"] == "22000.00" and pts[10]["annual"] == "51600.00"
    # p90 没有发布（OEWS 的 # 顶码）：落在 p75–p90 那一段的区间都是未识别，不补
    cut = wage_support({**w, "annual_wage_p90": None})
    assert len(cut["points"]) == 16 and cut["unidentified_inside"] == str(Fraction(4, 25))


def test_start_job_uses_the_median_and_never_substitutes(frozen, cfgs):
    index = city(frozen, "16980")["start_jobs"]
    ok = start_job(index, cfgs[1], "43-9061")
    assert ok == {
        "status": "ok",
        "opportunity_id": "chi.job.43-9061.start",
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
    t = items["nyc.training.ipeds.183725.4"]["opportunity"]
    src = next(
        p
        for p in sl["metros"]["35620"]["training"]
        if p["record_id"] == "training.ipeds2023.183725.4"
    )
    assert t["grants"] == [
        "soc_49_2022_award"
    ]  # CIP 47.0103 → 49-2022 在范围内且要求证书；同一 CIP 的其他职业不在范围
    assert (
        t["duration"]["completion_years"] == 1 and t["resource"]["hours"] == 104.0
    )  # 24 × 52 ÷ 12
    assert t["price"]["amount"]["amount"] == float(Fraction(src["tuition"]))
    assert "start_dates_unknown" in items["nyc.training.ipeds.183725.4"]["gaps"]
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
