"""E1-5A：学习 → 能力 → 下一年岗位 → 实际收入（`DECISIONS/0023`）。按 E1-5A prompt 第九节的验收分节。

金额按当地基年不变价（USD_2024_LOCAL），比较整数分；时间是小时，负担是负担点；不加容差。手算见
``configs/life/synthetic_e1_5a.yaml`` 文末。
"""

from __future__ import annotations

import copy

import pytest

from common.life_contracts import qualification_gaps
from ledger.life_catalog import cross_validate, parse_bundle

from .career_support import (
    ACCOUNTANT,
    ACCOUNTING,
    BASIC_FOOD,
    CLERK,
    DEVELOPER,
    FAR_HOME,
    FOOD,
    HOME,
    HR_LEDGERCO,
    HR_NETCO,
    HR_TRAINING,
    PYTHON,
    WASHER,
    course,
    income,
    job,
    max_burden,
    max_hours,
    plan_for,
    run_clone,
    settle,
    year,
)
from .feedback_support import variant
from .settle_support import catalog_of, cents, revise, state_of, with_items


def _codes(r) -> set[str]:
    return {p.code for p in r.problems}


def _learning(r, oid):
    return next(x for x in r.settlement.outcome.career.learning if x.opportunity_id == oid)


# --------------------------------------------------------------------------
# 1：只有实际有效参加才增加进度；只付钱、只写理由都不增长能力
# --------------------------------------------------------------------------


def test_only_attended_months_add_progress(bundle5a, scoring5a):
    (y1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.midyear")
    rec = _learning(y1, ACCOUNTING)
    assert rec.months_attended == (7, 8, 9, 10, 11, 12)  # 第 1–6 月不在计划里，不增加进度
    assert (rec.progress_before, rec.progress_after, rec.months_required) == (0, 6, 12)
    assert rec.completed_month is None and rec.granted == ()
    assert cents(rec.fee_charged) == 1_200_00
    assert y1.next_state.abilities == ()
    (tr,) = y1.next_state.training
    assert (tr.months_participated, tr.fee_paid, tr.completed_year) == (6, True, None)


def test_a_rationale_alone_teaches_nothing(bundle5a, scoring5a):
    s = state_of(bundle5a, "u51.a71.nostudy")
    plan = with_items(
        plan_for(bundle5a, "u51.a71.nostudy", 1, HOME, FOOD, job(CLERK), WASHER),
        HOME,
        FOOD,
        job(CLERK),
        WASHER,
        rationale="每天下班都在努力自学 Python，相信明年就能做开发",
    )
    r = year(bundle5a, scoring5a, s, plan)
    assert r.settlement.outcome.career.learning == ()
    assert (r.next_state.abilities, r.next_state.training) == ((), ())


def test_paying_and_attending_one_month_is_not_completion(bundle5a, scoring5a):
    s = state_of(bundle5a, "u51.a71.nostudy")
    plan = plan_for(
        bundle5a, "u51.a71.nostudy", 1, HOME, FOOD, job(CLERK), WASHER, course(ACCOUNTING, 12, 12)
    )
    r = year(bundle5a, scoring5a, s, plan)
    rec = _learning(r, ACCOUNTING)
    assert (cents(rec.fee_charged), rec.months_attended, rec.progress_after) == (1_200_00, (12,), 1)
    assert r.next_state.abilities == ()  # 付了学费、参加 1 个月，不等于学会


# --------------------------------------------------------------------------
# 2：已有进度跨年延续；一次性学费不重复扣
# --------------------------------------------------------------------------


def test_progress_carries_over_and_the_fee_is_charged_once(bundle5a, scoring5a):
    _, y2, y3 = run_clone(bundle5a, scoring5a, "u51.a71.midyear")
    rec2 = _learning(y2, ACCOUNTING)
    assert rec2.months_attended == (1, 2, 3, 4, 5, 6)
    assert (rec2.progress_before, rec2.progress_after, rec2.completed_month) == (6, 12, 6)
    assert cents(rec2.fee_charged) == 0
    assert not [
        f
        for f in y2.settlement.outcome.cash_flows
        if f.opportunity_id == ACCOUNTING and f.kind == "expense"
    ]
    assert rec2.granted == ("accounting_cert",) and rec2.usable_from_year == 3
    (held,) = y2.next_state.abilities
    assert (held.acquired_year, held.acquired_month, held.usable_from_year) == (2, 6, 3)
    # 第 2 年第 6 月学完，第 3 年才做会计：48,000 − 7,200
    assert income(y3) == (48000, 7200)
    assert cents(y3.settlement.outcome.cash_end) == 37_700_00


# --------------------------------------------------------------------------
# 3：零价自学合法，但学习的时间与负担实际计入
# --------------------------------------------------------------------------


def test_free_self_study_still_costs_time_and_burden(bundle5a, scoring5a):
    py, *_ = run_clone(bundle5a, scoring5a, "u51.a71.python")
    no, *_ = run_clone(bundle5a, scoring5a, "u51.a71.nostudy")
    assert cents(_learning(py, PYTHON).fee_charged) == 0
    assert (
        cents(py.settlement.outcome.cash_end) == cents(no.settlement.outcome.cash_end) == 11_300_00
    )
    # 每月 30 小时 × 每小时 1.0：负担 195 → 225，时间 223 → 253（与工作时间分开计）
    assert (max_burden(no), max_burden(py)) == (195.0, 225.0)
    assert (max_hours(no), max_hours(py)) == (223.0, 253.0)
    assert [a.ability_id for a in py.next_state.abilities] == ["python_programming"]


def test_an_unknown_price_is_not_free(raw5a, scoring5a):
    o = next(x for x in raw5a["catalogs"][0]["opportunities"] if x["opportunity_id"] == PYTHON)
    o["availability"] = "unknown"
    o.pop("price")
    b = parse_bundle(raw5a)
    s = state_of(b, "u51.a71.python")
    r = settle(
        b, s, plan_for(b, "u51.a71.python", 1, HOME, FOOD, job(CLERK), WASHER, course(PYTHON))
    )
    assert r.status == "completed"  # 学不了这门课，但生活照常
    rec = next(x for x in r.outcome.career.learning if x.opportunity_id == PYTHON)
    assert (rec.months_attended, rec.progress_after) == ((), 0)
    assert r.next_state.abilities == ()


def test_learning_must_take_positive_time_and_burden(raw5a):
    o = next(x for x in raw5a["catalogs"][0]["opportunities"] if x["opportunity_id"] == PYTHON)
    o["resource"]["hours"] = 0
    errors = cross_validate(parse_bundle(raw5a)).errors
    assert any("学习必须按实际参加的月份占用正的时间与负担" in e for e in errors)


# --------------------------------------------------------------------------
# 4：钱、时间、精力任一约束让年度试算失败或学习项被拒，都不留下成果
# --------------------------------------------------------------------------


def test_overloaded_study_fails_the_whole_year_and_keeps_nothing(bundle5a):
    s = state_of(bundle5a, "u51.a71.nostudy")
    both = plan_for(
        bundle5a,
        "u51.a71.nostudy",
        1,
        HOME,
        FOOD,
        job(CLERK),
        WASHER,
        course(ACCOUNTING),
        course(PYTHON),
    )
    r = settle(bundle5a, s, both)
    # 195 + 48 + 30 = 273 > 250：计划不可行，整份试算不提交——没有学费、进度或能力
    assert r.status == "plan_infeasible" and "energy_over_limit" in _codes(r)
    assert r.outcome is None and r.next_state is None


def test_too_little_time_for_study_fails_the_whole_year(bundle5a):
    s = revise(state_of(bundle5a, "u51.a71.python"), time_budget_hours=3000)  # 250 小时/月 < 253
    r = settle(
        bundle5a,
        s,
        plan_for(bundle5a, "u51.a71.python", 1, HOME, FOOD, job(CLERK), WASHER, course(PYTHON)),
    )
    assert r.status == "plan_infeasible" and "time_over_limit" in _codes(r)
    assert r.outcome is None and r.next_state is None


def test_an_unaffordable_fee_teaches_nothing(bundle5a):
    s = revise(
        state_of(bundle5a, "u51.a71.study"),
        cash={"amount": 0, "numeraire": "USD_2024_LOCAL"},
    )
    r = settle(
        bundle5a,
        s,
        plan_for(bundle5a, "u51.a71.study", 1, HOME, FOOD, job(CLERK), course(ACCOUNTING)),
    )
    # 第 1 月：0 + 3,000 − 1,400 − 450 = 1,150 < 学费 1,200：这门课被拒，生活照常；没付钱、没进度、没能力
    assert r.status == "completed"
    rec = next(x for x in r.outcome.career.learning if x.opportunity_id == ACCOUNTING)
    assert (cents(rec.fee_charged), rec.months_attended, rec.progress_after) == (0, (), 0)
    assert r.next_state.abilities == () and r.next_state.training == ()


# --------------------------------------------------------------------------
# 5 / 6：未完成、当年刚完成、年初已有三种状态；旧写法不能绕过下一年生效
# --------------------------------------------------------------------------


def test_not_done_done_this_year_and_held_at_start_differ(bundle5a, scoring5a):
    # 没学：第 2 年选会计岗位 → 年初不满足要求，整份计划退回
    (no1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.nostudy")
    r = settle(
        bundle5a,
        no1.next_state,
        plan_for(bundle5a, "u51.a71.nostudy", 2, HOME, FOOD, job(ACCOUNTANT)),
    )
    assert r.status == "input_error" and "qualification_missing" in _codes(r)
    # 当年刚学完（第 2 年第 6 月）：同一年下半年也不能做会计
    (mid1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.midyear")
    same_year = plan_for(
        bundle5a,
        "u51.a71.midyear",
        2,
        HOME,
        FOOD,
        job(CLERK, 1, 6),
        job(ACCOUNTANT, 7, 12),
        course(ACCOUNTING, 1, 6),
    )
    r = settle(bundle5a, mid1.next_state, same_year)
    assert r.status == "input_error" and "qualification_missing" in _codes(r)
    # 年初已有（第 1 年第 12 月学完）：第 2 年就能做会计
    (_, st2, _) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    assert st2.settlement.status == "completed" and income(st2) == (48000, 7200)


@pytest.mark.parametrize(
    "tag", ["training_completed:a71.training.accounting_course", "skill:accounting_cert>=1"]
)
def test_old_tags_cannot_bypass_the_next_year_rule(raw5a, tag):
    o = next(x for x in raw5a["catalogs"][0]["opportunities"] if x["opportunity_id"] == ACCOUNTANT)
    o["eligibility"] = [tag]
    b = parse_bundle(raw5a)
    report = cross_validate(b)
    assert any("旧写法" in e for e in report.settlement_problems + report.errors)
    s = state_of(b, "u51.a71.study")
    r = settle(
        b, s, plan_for(b, "u51.a71.study", 1, HOME, FOOD, job(CLERK), WASHER, course(ACCOUNTING))
    )
    assert r.status == "data_error"


def test_e1_5a_tags_are_refused_by_old_protocols(raw4b):
    o = next(
        x
        for x in raw4b["catalogs"][0]["opportunities"]
        if x["opportunity_id"] == "a61.job.parttime"
    )
    o["eligibility"] = ["ability:accounting_cert"]
    report = cross_validate(parse_bundle(raw4b))
    assert any("E1-5A 的写法" in e for e in report.settlement_problems)


# --------------------------------------------------------------------------
# 8：无关能力、错误作用域不能满足要求；未定义的引用明确报告
# --------------------------------------------------------------------------


def test_an_unrelated_qualification_does_not_open_a_job(bundle5a, scoring5a):
    (st1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    r = settle(
        bundle5a, st1.next_state, plan_for(bundle5a, "u51.a71.study", 2, HOME, FOOD, job(DEVELOPER))
    )
    # 会计资格不满足同一家互联网公司开发岗位的 Python 要求
    assert r.status == "input_error"
    assert any("python_programming" in p.message for p in r.problems)


def test_company_knowledge_does_not_transfer_to_another_employer(bundle5a, scoring5a):
    hr1, hr2, _ = run_clone(bundle5a, scoring5a, "u51.a71.hr")
    assert income(hr2) == (44000, 6400)  # 网鹊科技的人事岗位：要求的正是网鹊科技的规则
    r = settle(
        bundle5a, hr1.next_state, plan_for(bundle5a, "u51.a71.hr", 2, HOME, FOOD, job(HR_LEDGERCO))
    )
    assert r.status == "input_error"
    assert any("hr_rules_ledgerco" in p.message for p in r.problems)


@pytest.mark.parametrize(
    ("target", "change", "message"),
    [
        (HR_LEDGERCO, {"eligibility": ["ability:hr_rules_netco"]}, "作用域冲突"),
        (DEVELOPER, {"eligibility": ["ability:rust_programming"]}, "没有声明的能力"),
        (PYTHON, {"grants": ["data_science"]}, "授予了没有声明的能力"),
        (CLERK, {"employer_id": "firm.ghost"}, "没有在 career_rules.employers 里声明"),
        (PYTHON, {"grants": ["hr_rules_netco"]}, "只能由这家雇主的培训授予"),
        (HR_TRAINING, {"eligibility": ["employer:firm.ghost"]}, "没有声明的雇主"),
    ],
)
def test_scope_conflicts_and_undefined_references_are_data_errors(raw5a, target, change, message):
    o = next(x for x in raw5a["catalogs"][0]["opportunities"] if x["opportunity_id"] == target)
    o.update(change)
    errors = cross_validate(parse_bundle(raw5a)).errors
    assert any(message in e for e in errors), errors


# --------------------------------------------------------------------------
# 9：重复完成、重复交接与重放都不叠加；输入不被修改
# --------------------------------------------------------------------------


def test_an_occupation_scoped_ability_belongs_to_that_occupation(raw5a):
    """职业作用域：只用于会计职业的审计方法，会计岗位可以要求；开发岗位要求它就是作用域冲突（资料错误），不猜。"""
    rules = raw5a["protocol"]["career_rules"]
    rules["abilities"].append(
        {
            **rules["abilities"][0],
            "ability_id": "audit_method",
            "kind": "knowledge",
            "label": "审计方法（合成）",
            "description": "只对会计职业有用的审计流程",
            "scope": {"kind": "occupation", "occupation": {"scheme": "SOC2018", "code": "13-2011"}},
        }
    )
    opps = raw5a["catalogs"][0]["opportunities"]
    next(x for x in opps if x["opportunity_id"] == ACCOUNTANT)["eligibility"].append(
        "ability:audit_method"
    )
    assert cross_validate(parse_bundle(raw5a)).errors == ()  # 会计岗位要求会计职业的能力：合法
    next(x for x in opps if x["opportunity_id"] == DEVELOPER)["eligibility"] = [
        "ability:audit_method"
    ]
    errors = cross_validate(parse_bundle(raw5a)).errors
    assert any("只用于职业 SOC2018:13-2011" in e and "作用域冲突" in e for e in errors), errors


def test_a_second_course_for_the_same_ability_adds_nothing(raw5a, scoring5a):
    opps = raw5a["catalogs"][0]["opportunities"]
    base = next(x for x in opps if x["opportunity_id"] == ACCOUNTING)
    opps.append(
        {
            **base,
            "opportunity_id": "a71.training.accounting_refresher",
            "label": "会计复习班（12 个月）",
            "price": {**base["price"], "amount": {**base["price"]["amount"], "amount": 600}},
            "resource": {**base["resource"], "hours": 20},
        }
    )
    b = parse_bundle(raw5a)
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config("configs/life/scoring_e1_5a.yaml", b)
    (st1, *_) = run_clone(b, config, "u51.a71.study")
    plan2 = plan_for(
        b,
        "u51.a71.study",
        2,
        HOME,
        FOOD,
        job(ACCOUNTANT),
        course("a71.training.accounting_refresher"),
    )
    r = year(b, config, st1.next_state, plan2)
    rec = _learning(r, "a71.training.accounting_refresher")
    assert (rec.completed_month, rec.granted, rec.already_held) == (12, (), ("accounting_cert",))
    (held,) = r.next_state.abilities  # 仍然只有一项，保留第 1 年第 12 月的来源
    assert (held.ability_id, held.acquired_year, held.source_opportunity_id) == (
        "accounting_cert",
        1,
        ACCOUNTING,
    )
    assert income(r) == (48000, 7200)  # 不因为又学了一遍而多涨一次工资


def test_replays_are_identical_and_inputs_untouched(bundle5a, scoring5a):
    start = state_of(bundle5a, "u51.a71.study")
    before = start.model_dump_json()
    a = run_clone(bundle5a, scoring5a, "u51.a71.study")
    b = run_clone(bundle5a, scoring5a, "u51.a71.study")
    assert [r.model_dump_json() for r in a] == [r.model_dump_json() for r in b]
    assert start.model_dump_json() == before


# --------------------------------------------------------------------------
# 10：取得资格不等于涨薪；实际选择并执行新岗位后，工资、税、通勤与资源按原来的账算
# --------------------------------------------------------------------------


def test_a_qualification_alone_does_not_raise_pay(bundle5a, scoring5a):
    _, stay2, _ = run_clone(bundle5a, scoring5a, "u51.a71.stay")
    _, study2, _ = run_clone(bundle5a, scoring5a, "u51.a71.study")
    assert [a.ability_id for a in stay2.settlement.next_state.abilities] == ["accounting_cert"]
    assert income(stay2) == (36000, 4800)  # 有资格、仍选文员：工资不变
    assert income(study2) == (48000, 7200)
    assert cents(stay2.settlement.outcome.cash_end) == 19_100_00
    assert cents(study2.settlement.outcome.cash_end) == 28_700_00


def test_the_new_job_runs_through_the_existing_accounts(bundle5a, scoring5a):
    _, study2, _ = run_clone(bundle5a, scoring5a, "u51.a71.study")
    out = study2.settlement.outcome
    wages = [f.amount.amount for f in out.cash_flows if f.kind == "income_gross"]
    assert wages == [4000.0] * 12  # 48,000 / 12，只有这一份岗位
    m1 = out.resources.months[0]
    rows = {(c.category, c.opportunity_id): c for c in m1.contributions}
    assert rows[("work", ACCOUNTANT)].burden.fraction() == 176  # 160 × 1.1
    assert rows[("commute", ACCOUNTANT)].hours.fraction() == 10  # 30 分钟 × 20 次 / 60
    assert m1.energy_used.fraction() == 211
    assert study2.next_state.occupation.code == "13-2011"
    assert study2.next_state.employment.current_position_ids == (ACCOUNTANT,)
    # 不会悄悄同时保留原岗位：第 2 年只有会计这一份
    assert [p.opportunity_id for p in study2.next_state.employment.positions] == [ACCOUNTANT]


# --------------------------------------------------------------------------
# 11：职业与雇主门槛读真实延续的岗位，不读永不更新的初始身份
# --------------------------------------------------------------------------


def test_employer_requirements_read_the_actual_current_job(bundle5a, scoring5a):
    """辞职的下半年没有线下工作，方案级通勤无从评分、反馈开着就是 feedback_blocked（E1-3/E1-4B 规则），
    所以这一条走反馈关闭的对照臂：它只看岗位记录怎么延续，不看满足感。"""
    off = variant(bundle5a.protocol, mode="disabled", policy_id="feedback.e1-5a.off")
    s = state_of(bundle5a, "u51.a71.nostudy")
    # 辞职后要靠存款过下半年：改住远处、吃基本食材（每月 1,380），年末现金 2,540，不出局
    quit_half = plan_for(
        bundle5a, "u51.a71.nostudy", 1, FAR_HOME, BASIC_FOOD, job(CLERK, 1, 6), WASHER
    )
    r = year(bundle5a, scoring5a, s, quit_half, protocol=off, with_scoring=False)
    assert r.handoff == "next_year_ready" and cents(r.settlement.outcome.cash_end) == 2_540_00
    nxt = r.next_state
    # 第 6 月以后没有岗位：年末不在网鹊科技，职业摘要为空；用户陈述的职业原样保留
    assert nxt.employment.current_position_ids == () and nxt.occupation is None
    assert nxt.employment.stated_occupation.code == "43-6014"
    blocked = settle(
        bundle5a,
        nxt,
        plan_for(bundle5a, "u51.a71.nostudy", 2, FAR_HOME, BASIC_FOOD, course(HR_TRAINING)),
        protocol=off,
    )
    assert blocked.status == "input_error" and "qualification_missing" in _codes(blocked)
    assert any("firm.netco" in p.message for p in blocked.problems)


def test_two_jobs_are_both_recorded_not_the_first_as_the_whole(raw5a, scoring5a):
    """多份工作照常执行：岗位记录写两份、当前岗位两份；两份职业不同，兼容摘要为空——不拿第一份冒充全部。
    两份到岗工作的月份，方案级通勤只支持"恰好一份线下工作"，反馈开着时报评分不完整（E1-3 支持边界，如实报告）。"""
    opps = raw5a["catalogs"][0]["opportunities"]
    clerk = next(x for x in opps if x["opportunity_id"] == CLERK)
    part = copy.deepcopy(clerk)
    part.update(
        opportunity_id="a71.job.bookkeeper_ledgerco",
        label="记账员（账房事务所，兼职）",
        occupation={"scheme": "SOC2018", "code": "43-3031"},
        employer_id="firm.ledgerco",
        compensation={
            **clerk["compensation"],
            "amount": {**clerk["compensation"]["amount"], "amount": 12000},
        },
    )
    part["resource"] = copy.deepcopy(clerk["resource"])
    part["resource"]["job"].update(
        work_hours_per_month=40, workdays_per_month=5, onsite_days_per_month=5
    )
    opps.append(part)
    b = parse_bundle(raw5a)
    assert cross_validate(b).errors == ()
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config("configs/life/scoring_e1_5a.yaml", b)
    s = state_of(b, "u51.a71.nostudy")
    plan = plan_for(
        b, "u51.a71.nostudy", 1, HOME, FOOD, job(CLERK), job(part["opportunity_id"]), WASHER
    )
    on = year(b, config, s, plan)
    # 资源：160 + 40 工时，通勤 30 分钟 ×（20 + 5）次 / 60 = 12.5 小时；负担比只做文员多 40 + 2.5 × 0.75 → 236.875 ≤ 250
    assert on.settlement.status == "completed" and max_burden(on) == 236.875
    # 工资合并计税：(36,000 + 12,000 − 12,000) × 20% = 7,200
    assert income(on) == (48000, 7200)
    assert on.handoff == "feedback_blocked"  # 两份到岗工作：通勤满足感不支持 → 年度评分不完整
    off = variant(b.protocol, mode="disabled", policy_id="feedback.e1-5a.off")
    r = year(b, config, s, plan, protocol=off, with_scoring=False)
    nxt = r.next_state
    assert [p.opportunity_id for p in nxt.employment.positions] == [CLERK, part["opportunity_id"]]
    assert nxt.employment.current_position_ids == (CLERK, part["opportunity_id"])
    assert nxt.occupation is None  # 两个职业：不取第一份
    probe = (
        catalog_of(b, nxt)
        .opportunity(HR_TRAINING)
        .model_copy(
            update={"eligibility": ("employer:firm.ledgerco", "occupation:SOC2018:43-3031")}
        )
    )
    assert qualification_gaps(probe, nxt) == []  # 雇主与职业门槛读两份当前岗位


# --------------------------------------------------------------------------
# 12：出局、终态、反馈受阻不产生虚构的后续学习或收入
# --------------------------------------------------------------------------


def test_an_exit_keeps_real_progress_and_grants_nothing(bundle5a, scoring5a):
    (r,) = run_clone(bundle5a, scoring5a, "u53.a71.c0")
    assert r.handoff == "exited" and r.settlement.outcome.exit.month == 3
    rec = _learning(r, PYTHON)
    assert (rec.months_attended, rec.progress_after) == ((1, 2), 2)
    assert r.next_state.status == "exited" and r.next_state.abilities == ()
    (tr,) = r.next_state.training
    assert tr.months_participated == 2


def test_the_terminal_state_keeps_abilities_but_takes_no_action(bundle5a, scoring5a):
    *_, y3 = run_clone(bundle5a, scoring5a, "u51.a71.stay")
    term = y3.next_state
    assert (y3.handoff, term.status, term.year_index) == ("terminal", "terminal", 4)
    py = next(a for a in term.abilities if a.ability_id == "python_programming")
    assert (py.acquired_year, py.usable_from_year) == (3, 4)  # 学会了，但之后没有行动年
    r = settle(bundle5a, term, plan_for(bundle5a, "u51.a71.stay", 3, HOME, FOOD, job(DEVELOPER)))
    assert r.status == "input_error" and "state_not_active" in _codes(r)


def test_blocked_feedback_keeps_the_settled_learning(bundle5a, scoring5a):
    (r,) = run_clone(bundle5a, scoring5a, "u54.a71.c0")
    assert (r.handoff, r.next_state) == ("feedback_blocked", None)
    rec = _learning(r, ACCOUNTING)
    assert rec.granted == ("accounting_cert",) and rec.usable_from_year == 2
    candidate = r.settlement.next_state  # 学习事实留在结算结果里，候选状态额度待定
    assert [a.ability_id for a in candidate.abilities] == ["accounting_cert"]
    assert candidate.energy_quota_basis.status == "pending_feedback"


def test_the_paid_job_is_the_one_actually_executed(bundle5a, scoring5a):
    """取得开发能力后仍要在计划里选开发岗位；第 2 年的工资只来自实际执行的那一份。"""
    _, py2, _ = run_clone(bundle5a, scoring5a, "u51.a71.python")
    assert income(py2) == (60000, 9600)
    assert cents(py2.settlement.outcome.cash_end) == 39_500_00
    assert max_burden(py2) == 227.0  # 192 + 35
    assert [p.opportunity_id for p in py2.settlement.outcome.career.positions] == [DEVELOPER]
    assert HR_NETCO not in [p.opportunity_id for p in py2.settlement.outcome.career.positions]
