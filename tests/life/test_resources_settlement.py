"""E1-4A：月度时间与精力接进真正的结算路径（`DECISIONS/0020` 第七节）。

每条测试对应 E1-4A prompt 第七节的一项验收。数字来自 ``configs/life/synthetic_e1_4a.yaml``
文末的手算；小时与负担用精确分数比较，金额用整数分比较，都不加容差。
"""

from __future__ import annotations

import ast
import copy
from fractions import Fraction
from pathlib import Path

import pytest

from common.life_contracts import MonthResources, PersonYearState, SettlementResult
from econ.life_settlement import settle_plans, settle_year
from ledger.life_catalog import load_life_bundle, parse_bundle

from .conftest import FIXTURE_E1_4A, opportunity
from .settle_support import (
    bundle_from,
    catalog_of,
    cents,
    flows,
    item,
    plan_of,
    plus_items,
    revise,
    settle,
    state_of,
    tax_of,
    with_items,
)

F = Fraction  # 手算用的分数简写


def _months(result: SettlementResult) -> tuple[MonthResources, ...]:
    assert result.outcome is not None, (result.status, [p.message for p in result.problems])
    assert result.outcome.resources is not None
    return result.outcome.resources.months


def _hours(record: MonthResources, category: str, key: str | None = None) -> F:
    return sum(
        (
            c.hours.fraction()
            for c in record.contributions
            if c.category == category and (key is None or (c.task or c.opportunity_id) == key)
        ),
        F(0),
    )


def _burden(record: MonthResources, category: str, key: str | None = None) -> F:
    return sum(
        (
            c.burden.fraction()
            for c in record.contributions
            if c.category == category and (key is None or (c.task or c.opportunity_id) == key)
        ),
        F(0),
    )


def _used(record: MonthResources) -> tuple[F, F]:
    assert record.time_used is not None and record.energy_used is not None
    return record.time_used.fraction(), record.energy_used.fraction()


def _only_problem(result: SettlementResult):
    assert result.outcome is None and result.next_state is None
    (p,) = result.problems
    return p


BASE_ITEMS = (
    {"opportunity_id": "a51.housing.near", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a51.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a51.job.office", "quantity": 1, "start_month": 1, "end_month": 12},
)


# --------------------------------------------------------------------------
# 三个手算故事
# --------------------------------------------------------------------------


def test_living_far_saves_rent_and_costs_commute_time(bundle4a):
    """验收 5：20 个到岗日，往返 30 与 70 分钟分别占 10 与 23⅓ 小时；租金差与时间差同时保留。"""
    near, far = settle(bundle4a, "u21.a51.near"), settle(bundle4a, "u21.a51.far")
    assert near.status == far.status == "completed"
    for a, b in zip(_months(near), _months(far), strict=True):
        assert _hours(a, "commute") == 10 and _hours(b, "commute") == F(70, 3)
        assert _used(a) == (235, 207) and _used(b) == (F(745, 3), 217)
        assert (a.commute_roundtrip_minutes, b.commute_roundtrip_minutes) == (30, 70)
        assert _hours(b, "commute") - _hours(a, "commute") == F(40, 3)
    rent_near = [cents(f.amount) for f in flows(near, kind="expense", oid="a51.housing.near")]
    rent_far = [cents(f.amount) for f in flows(far, kind="expense", oid="a51.housing.far")]
    assert rent_near == [-140000] * 12 and rent_far == [-100000] * 12


def test_buying_convenience_frees_time_without_double_counting(bundle4a):
    """验收 9（上）：现成餐 + 每周保洁比自己做多花钱、少花时间；做饭只算实际那份方案。"""
    base, conv = settle(bundle4a, "u21.a51.near"), settle(bundle4a, "u21.a51.convenience")
    assert conv.status == "completed"
    for a, b in zip(_months(base), _months(conv), strict=True):
        assert _hours(a, "food_prep") == 40 and _hours(b, "food_prep") == 10
        assert [c.opportunity_id for c in b.contributions if c.category == "food_prep"] == [
            "a51.food.ready_meals"
        ]
        assert _hours(a, "household_task", "cleaning") == 10
        cleaning = next(c for c in b.contributions if c.task == "cleaning")
        assert (cleaning.hours.fraction(), cleaning.opportunity_id) == (2, "a51.service.cleaning")
        assert "由它承担 8 小时" in cleaning.basis
        assert _hours(b, "service", "a51.service.cleaning") == 0  # 外包服务本身不占本人时间
        assert _used(b) == (197, F(936, 5))
        assert _used(a)[0] - _used(b)[0] == 38
    # 钱照常多花：现成餐每月多 250，保洁每周 40 按 52/12 摊到每月
    food = [cents(f.amount) for f in flows(conv, kind="expense", oid="a51.food.ready_meals")]
    assert food == [-70000] * 12
    assert (
        -sum(cents(f.amount) for f in flows(conv, kind="expense", oid="a51.service.cleaning"))
        == 208000
    )


def test_a_cleaning_service_not_bought_gives_the_chore_back(bundle4a, raw4a):
    """验收 9（下）：保洁那个月没买成，打扫回到基准 10 小时；买成的月份是 2 小时。"""
    opportunity(raw4a, "a51.service.cleaning")["price"]["amount"]["amount"] = 1000
    b = bundle_from(raw4a)
    r = settle(b, "u21.a51.convenience")
    assert r.status == "completed"
    covered = set(item(r, "a51.service.cleaning").covered_months)
    assert covered and covered != set(range(1, 13))  # 有买成的月，也有没买成的月
    for m in _months(r):
        row = next(c for c in m.contributions if c.task == "cleaning")
        if m.month in covered:
            assert (row.hours.fraction(), row.opportunity_id) == (2, "a51.service.cleaning")
        else:
            assert (row.hours.fraction(), row.opportunity_id) == (10, None)


def test_energy_alone_can_make_a_plan_infeasible(bundle4a):
    """验收 2：钱够、时间够，第 3 月起的夜校让负担 253 > 230。"""
    p = _only_problem(settle(bundle4a, "u22.a51.c0"))
    assert (p.code, p.month) == ("energy_over_limit", 3)
    x = p.resource
    assert x is not None and x.resource == "energy"
    assert (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (230, 253, 23)
    assert ("training", "a51.training.bookkeeping", F(36)) in {
        (c.category, c.opportunity_id, c.burden.fraction()) for c in x.top_contributors
    }
    # 同一个月的时间 248⅓ + 30 = 278⅓ ≤ 365，所以只报精力
    assert "精力超限" in p.message and "没有提交账目" in p.message


def test_time_alone_can_make_a_plan_infeasible_and_months_do_not_borrow(bundle4a):
    """验收 1 与 4：第 6 月 375 > 365；全年合计 2,960 ≤ 4,380 也不能跨月借。"""
    p = _only_problem(settle(bundle4a, "u21.a51.volunteer"))
    assert (p.code, p.month) == ("time_over_limit", 6)
    x = p.resource
    assert x is not None
    assert (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (365, 375, 10)
    assert x.top_contributors[1].opportunity_id == "a51.service.volunteering"
    # 只看全年总量是够的：12 × 235 + 140 = 2,960 ≤ 4,380
    state = state_of(bundle4a, "u21.a51.volunteer")
    assert state.time_budget_hours >= 12 * 235 + 140


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"time_budget_hours": 2820}, "completed", None),
        ({"time_budget_hours": 2819.99}, "plan_infeasible", "time_over_limit"),
        ({"energy": {"level": 207}}, "completed", None),
        ({"energy": {"level": 206.99}}, "plan_infeasible", "energy_over_limit"),
    ],
)
def test_exactly_at_the_limit_passes_through_settlement(bundle4a, change, status, code):
    """验收 3：每月 235 小时、207 负担点；额度恰好相等通过，少一个最小声明精度就失败。"""
    s = state_of(bundle4a, "u21.a51.near")
    if "energy" in change:
        change = {"energy": revise(s.energy, **change["energy"])}
    r = settle(bundle4a, "u21.a51.near", state=revise(s, **change))
    assert r.status == status
    if code:
        assert (r.problems[0].code, r.problems[0].month) == (code, 1)


# --------------------------------------------------------------------------
# 住房兜底：降级后按实际住房重新核算
# --------------------------------------------------------------------------


def test_a_downgrade_to_a_far_home_can_make_time_infeasible(bundle4a):
    """验收 6：u23 第 11 月存款不够住近处，退回远处；通勤 35/3 小时让 143⅔ > 140。"""
    p = _only_problem(settle(bundle4a, "u23.a51.c0"))
    assert (p.code, p.month) == ("time_over_limit", 11)
    x = p.resource
    assert x is not None
    assert (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (
        140,
        F(431, 3),
        F(11, 3),
    )
    commute = next(c for c in x.top_contributors if c.category == "commute")
    # 通勤行归到岗位，依据里写明按实际（降级后的）住房算
    assert (commute.opportunity_id, commute.hours.fraction()) == ("a51.job.parttime", F(35, 3))
    assert "a51.housing.far" in commute.basis


def test_the_original_home_alone_was_feasible(bundle4a):
    """对照：u23 存款够一直住近处时，每月 137 ≤ 140，可行。"""
    s = state_of(bundle4a, "u23.a51.c0")
    r = settle(
        bundle4a,
        "u23.a51.c0",
        state=revise(s, cash={"amount": 20000, "numeraire": "USD_2024_LOCAL"}),
    )
    assert r.status == "completed"
    assert {m.housing_opportunity_id for m in _months(r)} == {"a51.housing.near"}
    assert {_used(m)[0] for m in _months(r)} == {137}


def test_a_downgrade_that_still_fits_completes_the_year(bundle4a):
    """验收 6 的正常对照：同样的计划与存款，u24 每月 150 小时，降级后 143⅔ ≤ 150，过完全年。"""
    r = settle(bundle4a, "u24.a51.c0")
    assert r.status == "completed"
    months = _months(r)
    for m in months[:10]:
        assert (m.housing_opportunity_id, _used(m)[0], _hours(m, "commute")) == (
            "a51.housing.near",
            137,
            5,
        )
    for m in months[10:]:
        assert (m.housing_opportunity_id, m.commute_roundtrip_minutes) == ("a51.housing.far", 70)
        assert (_used(m)[0], _hours(m, "commute")) == (F(431, 3), F(35, 3))
    assert item(r, "a51.housing.near").status == "downgraded_to_minimum"
    assert item(r, "a51.housing.far", fallback=True).covered_months == (11, 12)
    assert cents(r.outcome.cash_end) == 1000


# --------------------------------------------------------------------------
# 工作方式：远程、混合、兼职；薪酬与劳动绑定
# --------------------------------------------------------------------------


def test_remote_and_hybrid_work_by_their_declared_arrangements(bundle4a):
    """验收 7：远程住远处通勤 0；混合办公每月 8 个到岗日、往返 80 分钟 = 32/3 小时。"""
    remote, hybrid = settle(bundle4a, "u21.b52.remote"), settle(bundle4a, "u21.b52.hybrid")
    assert remote.status == hybrid.status == "completed"
    for a, b in zip(_months(remote), _months(hybrid), strict=True):
        assert (a.commute_status, a.onsite_visits, _hours(a, "commute")) == ("no_onsite_work", 0, 0)
        assert (b.commute_status, b.onsite_visits, _hours(b, "commute")) == (
            "computed",
            8,
            F(32, 3),
        )
        assert _used(a) == (213, F(343, 2)) and _used(b) == (F(671, 3), F(391, 2))
        assert _burden(a, "work") == 144  # 远程每工时负担 0.9


def test_remote_and_hybrid_are_executable_but_existing_commute_scoring_does_not_cover_them(
    bundle4a, scoring3s
):
    """资源可执行 ≠ E1-3 通勤满足感可评：现有方案级通勤只评恰好一份固定线下工作。"""
    from econ.life_scoring import _InUse, _score_plan_commute

    comp = next(c for c in scoring3s.spec.components if c.plan_commute is not None)
    cond = next(
        c
        for cc in scoring3s.city_conditions
        for c in cc.components
        if c.component_id == comp.component_id and c.status == "plan_level"
    )
    b52 = next(c for c in bundle4a.catalogs if c.city.code == "B52")
    home = [_InUse("housing", b52.opportunity("b52.housing.far"), 1.0)]
    for job_id, known in (
        ("b52.job.office", True),
        ("b52.job.hybrid", False),
        ("b52.job.remote", False),
    ):
        month = _score_plan_commute(comp, cond, home, [b52.opportunity(job_id)])
        assert (month.status == "known") is known, (job_id, month.reasons)
        if not known:
            assert "不支持" in month.reasons[0]


def test_a_job_keeps_its_hours_when_the_plan_tries_to_change_them(bundle4a):
    """验收 8：不能保留全职工资、把工时或到岗写成零。"""
    plan = plan_of(bundle4a, "u21.a51.near")
    data = plan.model_dump(mode="json")
    for it in data["items"]:
        if it["opportunity_id"] == "a51.job.office":
            it["arrangement"] = {"onsite_days_per_month": 0, "work_hours_per_month": 0}
    r = settle(bundle4a, "u21.a51.near", plan=type(plan).model_validate(data))
    assert r.status == "input_error" and r.outcome is None
    assert "job_arrangement_fixed" in {p.code for p in r.problems}


def test_a_job_cannot_offer_optional_hours_in_the_catalog(raw4a):
    opportunity(raw4a, "a51.job.office")["arrangement_options"] = {"onsite_days_per_month": [0, 20]}
    b = bundle_from(raw4a)
    r = settle(b, "u21.a51.near")
    assert r.status == "data_error" and r.outcome is None
    assert any("不能列可选安排" in p.message for p in r.problems)


# --------------------------------------------------------------------------
# 多份工作（r2，独立审核 F2 与所有者 2026-10-08 裁决）
# --------------------------------------------------------------------------
#
# 取代 r1 的 test_two_onsite_jobs_in_one_month_are_unsupported：那条断言"同月两份到岗工作是输入错误"，
# 所有者已撤回这条政策。新规则：资金、时间、精力够就能执行；每个岗位每次到岗一趟完整往返。

SECOND = "a51.job.parttime_second"


def _with_second_parttime(raw: dict, **changes) -> dict:
    """照独立审核的反例：复制一份线下兼职，换 ID（同一个到岗地点、同样的工时与到岗次数）。"""
    second = copy.deepcopy(opportunity(raw, "a51.job.parttime"))
    second["opportunity_id"] = SECOND
    second["label"] = "第二份零售兼职（同一个到岗地点）"
    second.update(changes)
    next(c for c in raw["catalogs"] if c["city"]["code"] == "A51")["opportunities"].append(second)
    return raw


def _two_parttime_plan(b, clone="u21.a51.near", *, keep=("housing", "food", "durable", "service")):
    plan = plan_of(b, clone)
    items = [
        i.model_dump(mode="json") for i in plan.items if i.opportunity_id.split(".")[1] in keep
    ]
    for oid in ("a51.job.parttime", SECOND):
        items.append({"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12})
    return with_items(plan, *items)


def test_two_part_time_jobs_add_up_wages_hours_commute_and_burden(raw4a):
    """独立审核反例：两份 80 工时、各到岗 10 次的兼职代替全职文员，住近处（往返 30 分钟）。

    工作 80 + 80 = 160；通勤 30 × 10 / 60 + 30 × 10 / 60 = 5 + 5 = 10；做饭 40、洗衣 3、打扫 10、健身 12
    → 235 小时；负担 160 + 7.5 + 20 + 1.5 + 6 + 12 = 207。工资每月 1,000 + 1,000；全年 24,000，合成税在年内
    累计超过 12,000 后（第 7 月起）每月预扣 400。
    """
    b = bundle_from(_with_second_parttime(raw4a))
    s = revise(state_of(b, "u21.a51.near"), cash={"amount": 10000, "numeraire": "USD_2024_LOCAL"})
    r = settle(b, "u21.a51.near", state=s, plan=_two_parttime_plan(b))
    assert r.status == "completed", [p.message for p in r.problems]
    for m in _months(r):
        assert _used(m) == (235, 207)
        assert _hours(m, "work", "a51.job.parttime") == _hours(m, "work", SECOND) == 80
        assert _hours(m, "commute", "a51.job.parttime") == _hours(m, "commute", SECOND) == 5
        assert (m.onsite_visits, m.job_workdays_sum) == (20, 20)
    gross = [cents(x.income_gross) for x in r.outcome.months]
    tax = [cents(x.income_tax) for x in r.outcome.months]
    assert gross == [200000] * 12
    assert tax == [0] * 6 + [40000] * 6
    assert sum(cents(f.amount) for f in flows(r, kind="income_gross", oid=SECOND)) == 1200000


@pytest.mark.parametrize(
    ("change", "code", "used", "limit"),
    [
        # 只有时间超：预算调到每月 300 小时、额度调高到 500
        ({"time_budget_hours": 3600, "energy": {"level": 500}}, "time_over_limit", 320, 300),
        # 只有精力超：默认每月 365 小时、250 负担点
        ({}, "energy_over_limit", F(1163, 4), 250),
    ],
)
def test_two_jobs_over_a_limit_are_plan_infeasible_not_input_errors(
    bundle4a, change, code, used, limit
):
    """全职文员 + 线下兼职：工作 240、通勤 10 + 5；时间 320 小时、负担 290.75 点。"""
    s = state_of(bundle4a, "u21.a51.near")
    if "energy" in change:
        change = {**change, "energy": revise(s.energy, **change["energy"])}
    plan = plus_items(
        plan_of(bundle4a, "u21.a51.near"),
        {"opportunity_id": "a51.job.parttime", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    p = _only_problem(settle(bundle4a, "u21.a51.near", plan=plan, state=revise(s, **change)))
    assert (p.code, p.month) == (code, 1)
    assert (p.resource.used.fraction(), p.resource.limit.fraction()) == (used, limit)
    assert p.resource.resource_check == "complete"


def test_a_job_that_is_not_executed_adds_no_wage_hours_or_commute(raw4a):
    """第二份兼职要求的资格这个人没有：被拒，不发工资、不占工时、不产生通勤。"""
    b = bundle_from(_with_second_parttime(raw4a, eligibility=["occupation:SOC2018:99-9999"]))
    # 只剩一份 1,000 的工资、每月支出约 1,850：存款给 20,000，保证全年过得完，只看第二份岗位
    s = revise(state_of(b, "u21.a51.near"), cash={"amount": 20000, "numeraire": "USD_2024_LOCAL"})
    r = settle(b, "u21.a51.near", state=s, plan=_two_parttime_plan(b))
    assert r.status == "completed"
    assert item(r, SECOND).status == "rejected" and not flows(r, oid=SECOND)
    for m in _months(r):
        assert _hours(m, "work", SECOND) == 0 and _hours(m, "commute", SECOND) == 0
        assert (_hours(m, "work"), _hours(m, "commute"), m.onsite_visits) == (80, 5, 10)


def test_after_a_downgrade_every_job_commutes_from_the_actual_home(raw4a):
    """两份兼职、存款 0、声明住房兜底：第 10 月给最低食品预留 380 后只剩 1,370 < 1,400，退回远处。

    第 1–9 月两份岗位各 30 × 10 / 60 = 5 小时；第 10–12 月各 70 × 10 / 60 = 35/3 小时——不会远处租金配
    近处通勤。
    """
    b = bundle_from(_with_second_parttime(raw4a))
    s = revise(state_of(b, "u21.a51.near"), cash={"amount": 0, "numeraire": "USD_2024_LOCAL"})
    plan = revise(_two_parttime_plan(b, keep=("housing", "food")), minimum_fallback=["housing"])
    r = settle(b, "u21.a51.near", state=s, plan=plan)
    assert r.status == "completed", [p.message for p in r.problems]
    for m in _months(r):
        far = m.month >= 10
        assert m.housing_opportunity_id == ("a51.housing.far" if far else "a51.housing.near")
        for job in ("a51.job.parttime", SECOND):
            assert _hours(m, "commute", job) == (F(35, 3) if far else 5)
    assert item(r, "a51.housing.near").downgraded_from_month == 10


# --------------------------------------------------------------------------
# 资料缺失不当零（结算这一层也拦）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("break_it", "needle"),
    [
        (
            lambda raw: opportunity(raw, "a51.housing.far")["spec"].pop(
                "commute_roundtrip_minutes"
            ),
            "往返分钟",
        ),
        (lambda raw: opportunity(raw, "a51.food.fresh").pop("resource"), "缺资源事实"),
        (
            lambda raw: opportunity(raw, "a51.food.fresh").__setitem__(
                "time_cost_hours_per_year", 480
            ),
            "不再使用",
        ),
        (
            lambda raw: opportunity(raw, "a51.service.cleaning")["resource"]["substitutes"][
                0
            ].__setitem__("task", "ironing"),
            "没有在协议的 household_tasks 里声明",
        ),
        (
            lambda raw: opportunity(raw, "a51.durable.washer")["resource"]["substitutes"][
                0
            ].__setitem__("residual_hours_per_month", 13),
            "替代不能让家务变多",
        ),
        (
            lambda raw: opportunity(raw, "a51.job.office")["spec"].__setitem__(
                "work_mode", "remote"
            ),
            "不一致",
        ),
        (
            lambda raw: opportunity(raw, "a51.food.fresh")["resource"].__setitem__("hours", 40.125),
            "精度细于",
        ),
    ],
)
def test_missing_or_contradictory_resource_data_is_a_data_error(raw4a, break_it, needle):
    break_it(raw4a)
    b = bundle_from(raw4a)  # 只做结构解析，不做加载器的交叉检查：结算自己也要拦住
    r = settle(b, "u21.a51.near")
    assert r.status == "data_error" and r.outcome is None and r.next_state is None
    assert any(needle in p.message for p in r.problems), [p.message for p in r.problems]


def test_an_old_energy_number_under_the_new_rules_is_a_data_error(bundle4a):
    """旧尺度的精力数值不是月度额度：新协议下缺额度依据、尺度不符，都是资料错误，不当零。"""
    s = state_of(bundle4a, "u21.a51.near")
    old = revise(
        s,
        energy={"level": 11, "scale_version": "synthetic_fixture_only_energy_v0"},
        resource_budget_basis=None,
    )
    r = settle(bundle4a, "u21.a51.near", state=old)
    assert r.status == "data_error" and r.outcome is None
    text = " ".join(p.message for p in r.problems)
    assert "资源预算依据" in text and "尺度" in text
    r = settle(bundle4a, "u21.a51.near", state=revise(s, time_budget_hours=4380.005))
    assert r.status == "data_error" and "精度细于" in r.problems[0].message


# --------------------------------------------------------------------------
# 实际执行口径
# --------------------------------------------------------------------------


def test_an_unaffordable_optional_activity_takes_no_time(bundle4a):
    """验收 10：20 张音乐会票第 1 月买不起，不占时间；买得起的两张只占发生的那一个月。"""
    plan = plan_of(bundle4a, "u21.a51.near")
    concert = {"opportunity_id": "a51.experience.concert", "quantity": 20, "month": 1}
    r = settle(bundle4a, "u21.a51.near", plan=plus_items(plan, concert))
    assert r.status == "completed"
    assert item(r, "a51.experience.concert").status == "rejected"
    assert {_used(m) for m in _months(r)} == {(235, 207)}  # 没买成：哪个月都不占

    r = settle(
        bundle4a, "u21.a51.near", plan=plus_items(plan, {**concert, "quantity": 2, "month": 2})
    )
    assert item(r, "a51.experience.concert").status == "executed"
    months = _months(r)
    assert _hours(months[1], "experience") == 8 and _burden(months[1], "experience") == 2
    assert _used(months[0]) == _used(months[2]) == (235, 207)  # 一次体验只在发生的月份


def test_a_free_activity_that_happens_still_takes_time(bundle4a):
    plan = plan_of(bundle4a, "u21.a51.near")
    r = settle(
        bundle4a,
        "u21.a51.near",
        plan=plus_items(
            plan, {"opportunity_id": "a51.experience.park_festival", "quantity": 1, "month": 5}
        ),
    )
    m5 = _months(r)[4]
    assert (_hours(m5, "experience"), _burden(m5, "experience")) == (6, 3)
    assert flows(r, oid="a51.experience.park_festival")[0].amount.amount == 0


def test_a_passive_item_creates_no_hours(bundle4a):
    plan = plan_of(bundle4a, "u21.a51.near")
    r = settle(
        bundle4a,
        "u21.a51.near",
        plan=plus_items(plan, {"opportunity_id": "a51.durable.phone", "quantity": 1, "month": 1}),
    )
    assert {_used(m) for m in _months(r)} == {(235, 207)}
    assert all(c.opportunity_id != "a51.durable.phone" for m in _months(r) for c in m.contributions)


# --------------------------------------------------------------------------
# 年付、持有物与培训：不重复、跨年继续、到期停止
# --------------------------------------------------------------------------


def _year_two(bundle4a, year1_items, year2_items, clone="u21.a51.near"):
    plan = plan_of(bundle4a, clone)
    y1 = with_items(plan, *year1_items)
    y2 = with_items(plan, *year2_items, year_index=2)
    s = state_of(bundle4a, clone)
    cat = catalog_of(bundle4a, s)
    return settle_plans(
        protocol=bundle4a.protocol, state=s, plans=[y1, y2], catalog=cat, tax=tax_of(bundle4a, cat)
    )


def test_prepaid_service_and_training_count_once_and_cross_into_the_next_year(bundle4a):
    """验收 11：年卡第 7 月买、覆盖到下一年第 6 月；夜校第 7 月报名、12 个月读完（第 2 年只剩 6 个月）。"""
    gym = {"opportunity_id": "a51.service.gym_plan", "quantity": 1, "start_month": 7}
    school = {
        "opportunity_id": "a51.training.bookkeeping",
        "quantity": 1,
        "start_month": 7,
        "end_month": 12,
    }
    washer = {"opportunity_id": "a51.durable.washer", "quantity": 1, "month": 1}
    r1, r2 = _year_two(
        bundle4a,
        [*BASE_ITEMS, washer, gym, school],
        [*BASE_ITEMS, {**school, "start_month": 1, "end_month": 6}],
    )
    assert r1.status == r2.status == "completed"
    y1, y2 = _months(r1), _months(r2)
    for m in y1:
        on = m.month >= 7
        assert _hours(m, "service", "a51.service.gym_plan") == (12 if on else 0)  # 不是 24
        assert _hours(m, "training") == (30 if on else 0)
    for m in y2:
        on = m.month <= 6
        assert _hours(m, "service", "a51.service.gym_plan") == (12 if on else 0)  # 继承，到期停
        assert _hours(m, "training") == (30 if on else 0)  # 读满 12 个月后不再占用
        assert _hours(m, "household_task", "laundry") == 3  # 洗衣机还在服务期
    held = next(
        h for h in r2.outcome.holdings_service if h.opportunity_id == "a51.service.gym_plan"
    )
    assert held.covered_months == (1, 2, 3, 4, 5, 6) and held.expired


def test_an_expired_appliance_stops_replacing_the_chore(bundle4a, raw4a):
    opportunity(raw4a, "a51.durable.washer")["duration"]["lasts_years"] = 1
    b = bundle_from(raw4a)
    washer = {"opportunity_id": "a51.durable.washer", "quantity": 1, "month": 7}
    r1, r2 = _year_two(b, [*BASE_ITEMS, washer], list(BASE_ITEMS))
    assert [_hours(m, "household_task", "laundry") for m in _months(r1)] == [12] * 6 + [3] * 6
    assert [_hours(m, "household_task", "laundry") for m in _months(r2)] == [3] * 6 + [12] * 6


# --------------------------------------------------------------------------
# 精力不结转、不反馈；偏好与理由不影响资源结算
# --------------------------------------------------------------------------


def test_unused_energy_does_not_carry_over(bundle4a):
    """验收 12：u22 前两个月各剩 13，第 3 月额度仍是 230，不是 256。"""
    s = state_of(bundle4a, "u22.a51.c0")
    plan = plan_of(bundle4a, "u22.a51.c0")
    first_two = with_items(
        plan,
        *[
            i.model_dump(mode="json")
            for i in plan.items
            if i.opportunity_id != "a51.training.bookkeeping"
        ],
    )
    ok = settle(bundle4a, "u22.a51.c0", plan=first_two, state=s)
    assert {(m.energy_quota.fraction(), m.energy_remaining.fraction()) for m in _months(ok)} == {
        (230, 13)
    }
    p = _only_problem(settle(bundle4a, "u22.a51.c0"))
    assert p.resource is not None and p.resource.limit.fraction() == 230


def test_the_next_year_gets_the_same_budget_and_quota(bundle4a):
    r = settle(bundle4a, "u21.a51.near")
    s, nxt = state_of(bundle4a, "u21.a51.near"), r.next_state
    assert nxt is not None and nxt.year_index == 2
    assert (nxt.time_budget_hours, nxt.energy, nxt.resource_budget_basis) == (
        s.time_budget_hours,
        s.energy,
        s.resource_budget_basis,
    )


def test_hidden_preferences_and_reasons_do_not_change_the_resource_ledger(bundle4a, raw4a):
    base = settle(bundle4a, "u21.a51.near")
    plan = plan_of(bundle4a, "u21.a51.near")
    reworded = settle(
        bundle4a, "u21.a51.near", plan=revise(plan, rationale="完全不同的理由：我只想离公园近。")
    )
    for t in raw4a["internal_truths"]:
        if t["person_id"] == "u21":
            t["dimension_weights"] = {**t["dimension_weights"], "material": 0.05, "commute": 0.40}
    for p in raw4a["stated_profiles"]:
        if p["person_id"] == "u21":
            p["dimension_points"] = {**p["dimension_points"], "material": 5, "commute": 40}
    other_truth = settle(bundle_from(raw4a), "u21.a51.near")
    dump = base.outcome.resources.model_dump()
    assert reworded.outcome.resources.model_dump() == dump
    assert other_truth.outcome.resources.model_dump() == dump


def test_settlement_does_not_read_scoring():
    tree = ast.parse(Path("src/econ/life_settlement.py").read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if "scoring" in m or "coverage" in m}


# --------------------------------------------------------------------------
# 失败无副作用、确定性重放
# --------------------------------------------------------------------------


@pytest.mark.parametrize("clone", ["u21.a51.volunteer", "u22.a51.c0", "u23.a51.c0"])
def test_an_infeasible_trial_commits_nothing_and_replays_identically(bundle4a, clone):
    """验收 13：不可行试算不改输入、不返回账本或下一年状态；同一输入再跑一遍完全一样。"""
    s, plan = state_of(bundle4a, clone), plan_of(bundle4a, clone)
    cat = catalog_of(bundle4a, s)
    before = (s.model_dump(), plan.model_dump(), cat.model_dump())
    first = settle(bundle4a, clone)
    second = settle(bundle4a, clone)
    assert first.status == "plan_infeasible"
    assert first.outcome is None and first.next_state is None
    assert (s.model_dump(), plan.model_dump(), cat.model_dump()) == before
    assert first.model_dump() == second.model_dump()


def test_completed_years_replay_identically(bundle4a):
    for clone in ("u21.a51.near", "u24.a51.c0", "u25.a51.c0"):
        assert settle(bundle4a, clone).model_dump() == settle(bundle4a, clone).model_dump()


# --------------------------------------------------------------------------
# 出局与终态
# --------------------------------------------------------------------------


def test_the_exit_month_keeps_the_wage_and_its_work(bundle4a):
    """验收 14：u25 第 4 月入账 1,000 后付不起最低生活；那个月的工作照记，通勤未知，之后未发生。

    r2：出局当月是"只检查已知下限"——80 ≤ 150 没超，资金出局照常保留，但不说这个月通过了完整检查。
    """
    r = settle(bundle4a, "u25.a51.c0")
    assert r.status == "exited" and r.outcome.exit.month == 4
    money = r.outcome.months
    assert [m.month for m in money] == [1, 2, 3, 4]
    assert (cents(money[3].income_gross), cents(money[3].spent), money[3].minimum_check) == (
        100000,
        0,
        "failed",
    )
    months = _months(r)
    assert months[2].housing_opportunity_id == "a51.housing.far" and _used(months[2])[0] == F(
        431, 3
    )
    m4 = months[3]
    assert m4.resource_check == "known_lower_bound" and m4.within_limits is None
    assert (m4.time_used.fraction(), m4.time_remaining) == (80, None)  # 已知下限，不是完整用量
    assert [(c.category, c.hours.fraction(), c.burden.fraction()) for c in m4.contributions] == [
        ("work", 80, 80)
    ]
    assert (m4.commute_status, m4.housing_opportunity_id, m4.onsite_visits) == (
        "unknown_no_housing",
        None,
        10,
    )
    assert all(m.resource_check == "not_reached" and not m.contributions for m in months[4:])
    assert 4 in item(r, "a51.job.parttime").covered_months  # 工资与劳动在同一个月
    # 摘要不把部分检查说成全年通过
    assert r.outcome.energy_feasibility == "monthly_quota_checked_until_exit"
    assert any("只检查已知下限" in n and "不是全年通过" in n for n in r.outcome.notes)


# --------------------------------------------------------------------------
# 出局当月的已知下限（r2，独立审核 F1）
# --------------------------------------------------------------------------


def _remote_parttime(raw: dict) -> dict:
    """照独立审核：把兼职改成全远程（没有到岗，通勤确定为零，不涉及住房）。"""
    job = opportunity(raw, "a51.job.parttime")
    job["spec"]["work_mode"] = "remote"
    job["resource"]["job"].update(work_mode="remote", onsite_days_per_month=0)
    return raw


@pytest.mark.parametrize("resource", ["time", "energy"])
@pytest.mark.parametrize("cash", [0, 300, 10000])
def test_poverty_cannot_bypass_a_known_overrun(raw4a, resource, cash):
    """远程兼职每月 80 小时、80 负担点；预算或额度只有 60。

    存款 0 或 300：第 1 月入账 1,000 后付不起最低生活 1,380，按旧规则会出局并留下工资——现在已知劳动
    就超了，判计划不可行（已知下限）。存款 10,000：照常过第 1 月，完整检查也超。存款多少都不能让这份
    超负荷劳动变成可执行。
    """
    b = bundle_from(_remote_parttime(raw4a))
    s = state_of(b, "u25.a51.c0")
    change = (
        {"time_budget_hours": 720} if resource == "time" else {"energy": revise(s.energy, level=60)}
    )
    s = revise(s, cash={"amount": cash, "numeraire": "USD_2024_LOCAL"}, **change)
    before = s.model_dump()
    first = settle(b, "u25.a51.c0", state=s)
    p = next(p for p in first.problems if p.code == f"{resource}_over_limit")
    assert first.status == "plan_infeasible" and first.outcome is None and first.next_state is None
    assert p.month == 1
    poor = cash + 1000 < 1380  # 0 与 300：第 1 月入账后仍付不起最低住房 1,000 + 最低食品 380
    assert p.resource.resource_check == ("known_lower_bound" if poor else "complete")
    if poor:
        assert (p.resource.used.fraction(), p.resource.excess.fraction()) == (80, 20)
        assert "已知下限" in p.message and "资金出局不能绕过" in p.message
    assert s.model_dump() == before
    assert settle(b, "u25.a51.c0", state=s).model_dump() == first.model_dump()


def test_a_remote_exit_month_within_limits_stays_an_exit(raw4a):
    """远程兼职、存款 0、默认预算：第 1 月就付不起最低生活。已知 80 ≤ 150，照常出局；通勤确定为零。"""
    b = bundle_from(_remote_parttime(raw4a))
    s = revise(state_of(b, "u25.a51.c0"), cash={"amount": 0, "numeraire": "USD_2024_LOCAL"})
    r = settle(b, "u25.a51.c0", state=s)
    assert r.status == "exited" and r.outcome.exit.month == 1
    m1 = _months(r)[0]
    assert (m1.resource_check, m1.commute_status, m1.onsite_visits) == (
        "known_lower_bound",
        "no_onsite_work",
        0,
    )
    assert m1.commute_relative_burden.fraction() == 0 and m1.within_limits is None
    assert {m.resource_check for m in _months(r)[1:]} == {"not_reached"}


def test_unscheduled_time_expires_each_month(bundle4a):
    """235 小时已安排 + 130 小时未安排 = 365。每月都是 130，不累积成 260、390……也不要求安排满。"""
    r = settle(bundle4a, "u21.a51.near")
    assert r.status == "completed"
    assert {(m.time_remaining.fraction(), m.time_budget.fraction()) for m in _months(r)} == {
        (130, 365)
    }
    assert {m.energy_remaining.fraction() for m in _months(r)} == {43}


def test_an_exited_clone_takes_no_more_actions(bundle4a):
    r = settle(bundle4a, "u25.a51.c0")
    nxt = r.next_state
    assert isinstance(nxt, PersonYearState) and nxt.status == "exited"
    again = settle(
        bundle4a,
        "u25.a51.c0",
        state=nxt,
        plan=revise(plan_of(bundle4a, "u25.a51.c0"), year_index=1),
    )
    assert again.status == "input_error" and again.problems[0].code == "state_not_active"


def test_three_years_then_a_terminal_state(bundle4a):
    """N = 3：三年都按月核算、额度不变；第 4 年是终态，不接受行动。"""
    plan = plan_of(bundle4a, "u21.a51.near")
    keep = [
        i.model_dump(mode="json") for i in plan.items if i.opportunity_id != "a51.durable.washer"
    ]
    plans = [plan, with_items(plan, *keep, year_index=2), with_items(plan, *keep, year_index=3)]
    s = state_of(bundle4a, "u21.a51.near")
    cat = catalog_of(bundle4a, s)
    results = settle_plans(
        protocol=bundle4a.protocol, state=s, plans=plans, catalog=cat, tax=tax_of(bundle4a, cat)
    )
    assert [r.status for r in results] == ["completed"] * 3
    for r in results:
        assert {(m.resource_check, _used(m)) for m in _months(r)} == {("complete", (235, 207))}
        assert r.outcome.resources.rules_revision == "r2-2026-10-08"
    terminal = results[-1].next_state
    assert terminal is not None and (terminal.status, terminal.year_index) == ("terminal", 4)
    r = settle_year(
        protocol=bundle4a.protocol,
        state=terminal,
        action=revise(plan, year_index=3),
        catalog=cat,
        tax=tax_of(bundle4a, cat),
    )
    assert r.status == "input_error" and r.problems[0].code == "state_not_active"


# --------------------------------------------------------------------------
# 加载与重放
# --------------------------------------------------------------------------


def test_the_fixture_loads_settlement_ready_but_not_scoring_ready():
    b, report = load_life_bundle(FIXTURE_E1_4A, require_settlement_ready=True)
    assert report.settlement_ready and not report.scoring_ready
    assert "energy_feedback" in report.undecided_policies
    policy = b.protocol.math_policies["energy_feedback"]
    assert policy.settled_parts and policy.open_parts  # 约束已定，反馈（E1-4B）仍开放


def test_parse_and_load_agree_on_the_fixture(raw4a, bundle4a):
    assert parse_bundle(raw4a).model_dump() == bundle4a.model_dump()
