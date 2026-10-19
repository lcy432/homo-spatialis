"""E1-5A 契约与加载检查（`DECISIONS/0023`）：协议门控、旧对象序列化不变、能力与岗位记录的形状、资格判断的口径。

这里只测形状与加载器的交叉检查；学习怎样变成能力、能力怎样打开岗位，见 ``test_career_settlement.py``。
"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    CAREER_OBSERVATION_FIELDS,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    AbilityScope,
    CareerRules,
    EmploymentRecord,
    HeldAbility,
    LearningYearRecord,
    LifeContractError,
    PersonYearState,
    TrainingProgress,
    qualification_gaps,
)
from ledger.life_catalog import cross_validate, parse_bundle

from .career_support import (
    ACCOUNTANT,
    CLERK,
    FOOD,
    HOME,
    HR_NETCO,
    job,
    plan_for,
    run_clone,
    settle,
)
from .feedback_support import profile_of, run_years
from .settle_support import catalog_of, revise, state_of

SOC_CLERK = {"scheme": "SOC2018", "code": "43-6014"}
SOC_ACCOUNTANT = {"scheme": "SOC2018", "code": "13-2011"}


def _state(raw, clone_id):
    return next(s for s in raw["initial_states"] if s["clone_id"] == clone_id)


def _opportunity(raw, oid):
    return next(
        o for c in raw["catalogs"] for o in c["opportunities"] if o["opportunity_id"] == oid
    )


def _errors(raw) -> list[str]:
    return list(cross_validate(parse_bundle(raw)).errors)


def _year2(bundle5a, scoring5a) -> PersonYearState:
    """会计班学员真实过完第 1 年后的第 2 年年初状态（反馈已确定额度）：有 accounting_cert，第 2 年起可用。"""
    (y1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    assert y1.next_state is not None and y1.next_state.year_index == 2
    return y1.next_state


def _year2_clerk(bundle5a):
    return plan_for(bundle5a, "u51.a71.study", 2, HOME, FOOD, job(CLERK))


# --------------------------------------------------------------------------
# 协议门控：新规则版本必须声明职业规则，旧规则版本不能带；旧配置不会静默获得学习收益
# --------------------------------------------------------------------------


def test_the_e1_5a_protocol_must_declare_its_career_rules(raw5a):
    raw5a["protocol"].pop("career_rules")
    with pytest.raises(LifeContractError, match="必须声明 career_rules"):
        parse_bundle(raw5a)


def test_the_e1_5a_protocol_must_also_declare_its_feedback(raw5a):
    raw5a["protocol"].pop("energy_feedback")
    with pytest.raises(LifeContractError, match="energy_feedback"):
        parse_bundle(raw5a)


def test_an_old_protocol_cannot_borrow_career_rules(raw4b, raw5a):
    raw4b["protocol"]["career_rules"] = copy.deepcopy(raw5a["protocol"]["career_rules"])
    with pytest.raises(LifeContractError, match="不读职业规则"):
        parse_bundle(raw4b)


def test_an_old_catalog_with_learning_outcomes_is_a_data_error(raw4b):
    _opportunity(raw4b, "a61.training.evening_course")["grants"] = ["accounting_cert"]
    errors = cross_validate(parse_bundle(raw4b)).errors
    assert any("不读雇主与学习成果" in e for e in errors)


def test_an_old_state_with_abilities_or_employment_is_a_data_error(raw4b):
    s = _state(raw4b, "u31.a61.c0")
    s["abilities"] = [{"ability_id": "accounting_cert", "source": "initial", "usable_from_year": 1}]
    errors = cross_validate(parse_bundle(raw4b)).errors
    assert any("不读能力、岗位记录与完成年月" in e for e in errors)


# --------------------------------------------------------------------------
# 兼容：没有用到的新字段不序列化，旧对象的输出与指纹逐字节不变
# --------------------------------------------------------------------------


def test_old_objects_serialize_without_any_career_field(bundle4b, scoring4b):
    """E1-4B 主臂真实跑两年（第 2 年上完夜校）：协议、目录、状态、账目、反馈与观察都没有职业字段。"""
    assert "career_rules" not in bundle4b.protocol.model_dump(mode="json")
    for c in bundle4b.catalogs:
        for o in c.opportunities:
            assert not {"employer_id", "grants"} & set(o.model_dump(mode="json"))
    y1, y2, *_ = run_years(bundle4b, scoring4b, "u31.a61.c0")
    for r in (y1, y2):
        assert not {"abilities", "employment"} & set(r.next_state.model_dump(mode="json"))
        assert "career" not in r.settlement.outcome.model_dump(mode="json")
        assert "career" not in visible_feedback(r.settlement).model_dump(mode="json")
    (tr,) = y2.next_state.training
    assert tr.completed  # 旧协议照旧：完成了，但不写完成年月，也不授予能力
    assert not {"completed_year", "completed_month"} & set(tr.model_dump(mode="json"))
    obs = build_observation(
        permission=standard_product_permission("e1-5a-test"),
        profile=profile_of(bundle4b, "u31"),
        state=y2.next_state,
        catalog=catalog_of(bundle4b, y2.next_state),
        last_feedback=visible_feedback(y2.settlement),
        resource_rules=bundle4b.protocol.resource_rules,
        energy_feedback=bundle4b.protocol.energy_feedback,
    )
    payload = observation_payload(obs)
    assert not CAREER_OBSERVATION_FIELDS & set(payload)
    for o in payload["opportunities"]:
        assert not {"employer_id", "grants", "qualification"} & set(o)
    for t in payload["training"]:
        assert not {"fee_paid", "completed_year", "completed_month"} & set(t)


# --------------------------------------------------------------------------
# 能力词表与职业规则
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [
        {"kind": "employer"},
        {"kind": "general", "employer_id": "firm.netco"},
        {"kind": "occupation"},
        {"kind": "general", "occupation": SOC_CLERK},
    ],
)
def test_a_scope_says_exactly_where_it_applies(scope):
    with pytest.raises(ValidationError, match="作用域"):
        AbilityScope.model_validate(scope)


def test_career_rules_refuse_duplicates_and_undeclared_employers(bundle5a):
    rules = bundle5a.protocol.career_rules
    data = rules.model_dump(mode="python")
    twice = {**data, "abilities": [*data["abilities"], data["abilities"][0]]}
    with pytest.raises(ValidationError, match="能力 ID 重复"):
        CareerRules.model_validate(twice)
    lonely = {
        **data,
        "employers": [e for e in data["employers"] if e["employer_id"] != "firm.netco"],
    }
    with pytest.raises(ValidationError, match=r"没有声明的雇主 firm\.netco"):
        CareerRules.model_validate(lonely)


def test_the_rules_fingerprint_covers_scope_not_wording(bundle5a):
    rules = bundle5a.protocol.career_rules
    data = rules.model_dump(mode="python")
    reworded = copy.deepcopy(data)
    reworded["abilities"][0]["label"] = "会计从业资格（改了名字）"
    reworded["note"] = "只改说明"
    assert CareerRules.model_validate(reworded).content_fingerprint == rules.content_fingerprint
    widened = copy.deepcopy(data)
    hr = next(a for a in widened["abilities"] if a["ability_id"] == "hr_rules_netco")
    hr["scope"] = {"kind": "general"}
    assert CareerRules.model_validate(widened).content_fingerprint != rules.content_fingerprint


# --------------------------------------------------------------------------
# 能力、岗位记录、学习记录的形状
# --------------------------------------------------------------------------


def test_a_learned_ability_is_usable_from_the_next_year_only():
    ok = HeldAbility(
        ability_id="accounting_cert",
        source="training",
        source_opportunity_id="a71.training.accounting_course",
        acquired_year=2,
        acquired_month=6,
        usable_from_year=3,
    )
    assert ok.usable_from_year == 3
    with pytest.raises(ValidationError, match="下一年生效"):
        HeldAbility.model_validate({**ok.model_dump(), "usable_from_year": 2})
    with pytest.raises(ValidationError, match="开局已有的能力没有来源课程"):
        HeldAbility(
            ability_id="accounting_cert", source="initial", acquired_year=1, usable_from_year=1
        )


def test_a_state_cannot_hold_an_ability_twice_or_use_it_early(bundle5a, scoring5a):
    learned = {
        "ability_id": "accounting_cert",
        "source": "training",
        "source_opportunity_id": "a71.training.accounting_course",
        "acquired_year": 1,
        "acquired_month": 12,
        "usable_from_year": 2,
    }
    with pytest.raises(ValidationError, match="还不能用"):
        revise(
            state_of(bundle5a, "u51.a71.study"), abilities=[learned]
        )  # 第 1 年年初拿着第 1 年学会的能力
    s2 = _year2(bundle5a, scoring5a)
    assert [a.model_dump(mode="json") for a in s2.abilities] == [learned]
    with pytest.raises(ValidationError, match="不叠加等级"):
        revise(s2, abilities=[learned, learned])
    this_year = {**learned, "acquired_year": 2, "usable_from_year": 3}
    with pytest.raises(ValidationError, match="还不能用"):
        revise(s2, abilities=[learned, {**this_year, "ability_id": "python_programming"}])


def test_an_employment_record_is_internally_consistent():
    clerk = {"opportunity_id": CLERK, "occupation": SOC_CLERK, "employer_id": "firm.netco"}
    with pytest.raises(ValidationError, match="不在岗位记录里"):
        EmploymentRecord(
            as_of="start_declaration", positions=[clerk], current_position_ids=[ACCOUNTANT]
        )
    with pytest.raises(ValidationError, match="开局声明的岗位没有结算年份与领薪月份"):
        EmploymentRecord(
            as_of="start_declaration",
            positions=[{**clerk, "months": [1]}],
            current_position_ids=[CLERK],
        )
    with pytest.raises(ValidationError, match="必须有实际领薪月份"):
        EmploymentRecord(as_of="settled_year", settled_year=1, positions=[clerk])


def test_two_current_jobs_are_not_summarised_by_the_first_one(bundle5a, scoring5a):
    """多份工作：当前岗位职业不止一个时兼容摘要为空，不随便取第一份；岗位集合原样保留。"""
    both = EmploymentRecord(
        stated_occupation=SOC_CLERK,
        as_of="settled_year",
        settled_year=1,
        positions=[
            {
                "opportunity_id": CLERK,
                "occupation": SOC_CLERK,
                "employer_id": "firm.netco",
                "months": [12],
            },
            {
                "opportunity_id": ACCOUNTANT,
                "occupation": SOC_ACCOUNTANT,
                "employer_id": "firm.netco",
                "months": [12],
            },
        ],
        current_position_ids=[CLERK, ACCOUNTANT],
    )
    assert both.occupation_summary() is None
    s2 = _year2(bundle5a, scoring5a)
    with pytest.raises(ValidationError, match="兼容摘要"):
        revise(s2, employment=both.model_dump(mode="python"))  # occupation 仍写文员
    ok = revise(s2, occupation=None, employment=both.model_dump(mode="python"))
    assert [p.opportunity_id for p in ok.employment.current_positions()] == [CLERK, ACCOUNTANT]


def test_only_a_course_grants_and_only_once_per_ability(raw5a):
    _opportunity(raw5a, CLERK)["grants"] = ["accounting_cert"]
    with pytest.raises(LifeContractError, match="只有培训完成后才授予能力"):
        parse_bundle(raw5a)


def test_completion_needs_enough_months_and_a_consistent_record():
    with pytest.raises(ValidationError, match="不能写完成"):
        TrainingProgress(
            opportunity_id="a71.training.accounting_course",
            months_participated=6,
            months_required=12,
            fee_paid=True,
            enrolled_year=1,
            enrolled_month=7,
            completed_year=1,
            completed_month=12,
        )
    base = dict(
        opportunity_id="a71.training.accounting_course",
        fee_charged={"amount": 0, "numeraire": "USD_2024_LOCAL"},
        months_required=12,
    )
    with pytest.raises(ValidationError, match="进度只因实际参加的月份增加"):
        LearningYearRecord(**base, months_attended=(1, 2), progress_before=0, progress_after=3)
    with pytest.raises(ValidationError, match="授予能力必须在完成之后"):
        LearningYearRecord(
            **base,
            months_attended=(1,),
            progress_before=0,
            progress_after=1,
            granted=("accounting_cert",),
            usable_from_year=2,
        )


# --------------------------------------------------------------------------
# 资格判断的口径：年初状态；看不懂或旧写法不当作满足；职业与雇主读当前岗位，不读陈述的身份
# --------------------------------------------------------------------------


def test_requirements_read_the_current_job_not_the_stated_identity(bundle5a):
    s = state_of(bundle5a, "u51.a71.nostudy")
    left = revise(
        s,
        occupation=None,
        employment={
            "stated_occupation": SOC_CLERK,
            "as_of": "settled_year",
            "settled_year": 1,
            "positions": [
                {
                    "opportunity_id": CLERK,
                    "occupation": SOC_CLERK,
                    "employer_id": "firm.netco",
                    "months": [1, 2],
                }
            ],
            "current_position_ids": [],
        },
    )
    hr = catalog_of(bundle5a, s).opportunity(HR_NETCO)
    probe = hr.model_copy(
        update={
            "eligibility": (
                "occupation:SOC2018:43-6014",
                "employer:firm.netco",
                "skill:typing>=1",
                "gibberish",
            )
        }
    )
    gaps = {g.kind: g for g in qualification_gaps(probe, left)}
    assert set(gaps) == {"occupation", "employer", "skill", "unknown"}
    assert "不当作满足" in gaps["unknown"].reason
    assert gaps["skill"].reason.startswith("新协议不认 skill 写法")
    # 年初仍在网鹊科技做文员：同样两条职业与雇主要求都满足
    assert {g.kind for g in qualification_gaps(probe, s)} == {"skill", "unknown"}


# --------------------------------------------------------------------------
# 加载器的状态检查：资料错误，不猜
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"employment": None}, "必须写岗位记录"),
        ({"skills": {"bookkeeping": 1.0}}, "不读数值 skills"),
        (
            {
                "abilities": [
                    {"ability_id": "forklift_licence", "source": "initial", "usable_from_year": 1}
                ]
            },
            "没有在 career_rules.abilities 里声明",
        ),
    ],
)
def test_initial_state_problems_are_data_errors(raw5a, change, message):
    for s in raw5a["initial_states"]:
        if s["person_id"] == "u51":
            s.update(copy.deepcopy(change))
    errors = _errors(raw5a)
    assert any(message in e for e in errors), errors


def test_a_position_must_match_the_catalog(raw5a):
    s = _state(raw5a, "u53.a71.c0")
    s["employment"] = {
        "as_of": "start_declaration",
        "positions": [
            {"opportunity_id": CLERK, "occupation": SOC_ACCOUNTANT, "employer_id": "firm.netco"}
        ],
        "current_position_ids": [CLERK],
    }
    s["occupation"] = SOC_ACCOUNTANT
    assert any("职业或雇主与目录不一致" in e for e in _errors(raw5a))


def test_a_claimed_course_must_really_grant_the_ability(bundle5a, scoring5a):
    s2 = _year2(bundle5a, scoring5a)
    forged = revise(
        s2,
        abilities=[
            {
                "ability_id": "python_programming",
                "source": "training",
                "source_opportunity_id": "a71.training.accounting_course",
                "acquired_year": 1,
                "acquired_month": 12,
                "usable_from_year": 2,
            }
        ],
    )
    r = settle(bundle5a, forged, _year2_clerk(bundle5a))
    assert r.status == "data_error"
    assert any("没有声明授予它" in p.message for p in r.problems)


def test_a_completed_course_without_its_ability_is_not_guessed(bundle5a, scoring5a):
    s2 = _year2(bundle5a, scoring5a)
    (tr,) = s2.training
    assert (tr.completed, tr.completed_year, tr.completed_month) == (True, 1, 12)
    r = settle(bundle5a, revise(s2, abilities=[]), _year2_clerk(bundle5a))
    assert r.status == "data_error"
    assert any("不猜学习成果" in p.message for p in r.problems)


def test_clones_of_one_person_share_one_start(raw5a):
    _state(raw5a, "u51.a71.hr")["abilities"] = [
        {"ability_id": "accounting_cert", "source": "initial", "usable_from_year": 1}
    ]
    assert any("同一个人只有一份起点" in e for e in _errors(raw5a))
    _state(raw5a, "u51.a71.hr")["abilities"] = []
    for s in raw5a["initial_states"]:
        if s["person_id"] == "u51":
            s["employment"] = {**s["employment"], "stated_occupation": SOC_ACCOUNTANT}
    assert any("与用户陈述" in e for e in _errors(raw5a))


def test_career_rules_reach_the_report_summary_without_scores(bundle5a):
    report = cross_validate(bundle5a)
    assert report.errors == () and report.settlement_ready
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "rank" not in blob and "utility" not in blob
    assert isinstance(state_of(bundle5a, "u51.a71.study"), PersonYearState)
