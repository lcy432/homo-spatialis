"""E1-5B：目标储蓄（`DECISIONS/0026`）。按 prompt 第八节的 14 项验收分节。

样例 ``configs/life/synthetic_e1_5b_savings.yaml``：门店兼职每月 1,500、远处一居室 1,000 + 基本食品 380，每月净存 120；
税档 40,000 以内 0%。洗烘一体机 2,400、会计课学费 1,200、探亲往返 1,500（计划内的普通开支，**不是**疾病事件）。
手算见样例文末。金额都按整数分比较，没有容差。
"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    GoalQuote,
    LifeContractError,
    PersonYearState,
    SavingsGoal,
    SavingsGoalDirective,
    YearAction,
)
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_settlement import settle_year
from ledger.life_catalog import parse_bundle

from .career_support import run_clone, year
from .feedback_support import bound_year
from .settle_support import catalog_of, cents, plan_of, revise, state_of, tax_of, with_items

HOME = {"opportunity_id": "a81.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a81.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}
SHOP = {"opportunity_id": "a81.job.shop_parttime", "quantity": 1, "start_month": 1, "end_month": 12}
DRYER = "a81.durable.washer_dryer"
WASHER = "a81.durable.washer"
COURSE = "a81.training.accounting_course"
FREE = "a81.training.bookkeeping_selfstudy"
VISIT = "a81.experience.family_visit"
SAVE, LEARN, DELAY = "u81.a81.save", "u82.a81.course", "u83.a81.delay"


def buy(oid: str, month: int, goal: str | None = None, quantity: int = 1) -> dict:
    item = {"opportunity_id": oid, "quantity": quantity, "month": month}
    return item | ({"goal_id": goal} if goal else {})


def course(start: int, end: int, goal: str | None = None) -> dict:
    item = {"opportunity_id": COURSE, "quantity": 1, "start_month": start, "end_month": end}
    return item | ({"goal_id": goal} if goal else {})


def set_goal(goal_id: str, oid: str, year_: int, month: int, **kw) -> dict:
    return {
        "action": "set",
        "goal_id": goal_id,
        "opportunity_id": oid,
        "quantity": 1,
        "target_year": year_,
        "target_month": month,
    } | kw


def plan(bundle, clone: str, year_: int, *items: dict, goal: dict | None = None) -> YearAction:
    return with_items(plan_of(bundle, clone, year_), *items, savings_goal=goal)


def run(bundle, config, state, action, **kw):
    return year(bundle, config, state, action, **kw)


def flows_for(r, oid: str) -> list[int]:
    return [cents(f.amount) for f in r.settlement.outcome.cash_flows if f.opportunity_id == oid]


def progress(r) -> list[tuple]:
    return [
        (
            g.month,
            cents(g.cash),
            None if g.shortfall is None else cents(g.shortfall),
            g.cash_covers_quote,
            g.status,
        )
        for g in r.settlement.outcome.savings.progress
    ]


def goal_of(state: PersonYearState, goal_id: str) -> SavingsGoal:
    return next(g for g in state.savings_goals if g.goal_id == goal_id)


def money_facts(r) -> tuple:
    """钱、执行与资源的全部事实（不含目标记录）。"""
    out = r.settlement.outcome
    return (
        out.model_dump_json(include={"cash_flows", "months", "items", "holdings_service"}),
        out.resources.model_dump_json(),
        cents(out.cash_end),
    )


# --------------------------------------------------------------------------
# 1、12：建立目标不动钱、不加分；相同生活事实只改目标，钱、分数、精力都不变
# --------------------------------------------------------------------------


def test_a_goal_moves_no_money_and_changes_no_score_or_energy(bundle_sv, scoring_sv):
    start = state_of(bundle_sv, SAVE)
    base = plan_of(bundle_sv, SAVE, 1)
    variants = {
        "goal": base,
        "no_goal": with_items(base, HOME, FOOD, SHOP, savings_goal=None),
        "other_text": with_items(
            base,
            HOME,
            FOOD,
            SHOP,
            savings_goal=set_goal("dryer", DRYER, 2, 6, note="只改说明文字"),
        ),
        "other_target": with_items(
            base, HOME, FOOD, SHOP, savings_goal=set_goal("dryer", DRYER, 3, 12)
        ),
        "tuition_goal": with_items(
            base, HOME, FOOD, SHOP, savings_goal=set_goal("tuition", COURSE, 2, 7)
        ),
    }
    runs = {k: run(bundle_sv, scoring_sv, start, v) for k, v in variants.items()}
    ref = runs["goal"]
    assert cents(ref.settlement.outcome.cash_end) == 194_000  # 500 + 12 × 120
    assert flows_for(ref, DRYER) == []  # 建立目标不产生任何现金流
    for name, r in runs.items():
        assert money_facts(r) == money_facts(ref), name
        assert [m.u for m in r.score.months] == [m.u for m in ref.score.months], name
        assert r.next_state.energy.level == ref.next_state.energy.level, name
    # 文字目标（旧的 goals）同样只是文字
    texted = revise(start, goals=("攒钱买洗烘一体机",))
    r = run(bundle_sv, scoring_sv, texted, base)
    assert money_facts(r) == money_facts(ref) and r.score.annual_mean == ref.score.annual_mean
    assert texted.savings_goals == ()  # 文字不会被解析成资金目标
    # 只有目标记录与包含它的指纹不同
    assert runs["no_goal"].settlement.outcome.savings.events == ()
    assert (
        runs["goal"].settlement.outcome.model_dump_json()
        != runs["no_goal"].settlement.outcome.model_dump_json()
    )


# --------------------------------------------------------------------------
# 2、3：真实留存现金跨年增加覆盖报价的能力；够了也不自动兑现
# --------------------------------------------------------------------------


def test_real_cash_builds_coverage_across_years(bundle_sv, scoring_sv):
    y1, y2, _ = run_clone(bundle_sv, scoring_sv, SAVE)
    p1 = progress(y1)
    assert p1[0] == (None, 50_000, 190_000, False, "active")  # 年初：余额 500，差 1,900
    assert p1[-1] == (12, 194_000, 46_000, False, "active")  # 年末：1,940，差 460
    assert [x[2] for x in p1] == sorted((x[2] for x in p1), reverse=True)  # 差距只因真实留存缩小
    p2 = progress(y2)
    assert p2[0] == (None, 194_000, 46_000, False, "active")  # 跨年延续：同一个余额、同一个差距
    assert p2[4] == (4, 242_000, 0, True, "active")  # 第 4 月末余额不少于报价——仍未兑现
    assert p2[5] == (5, 14_000, None, None, "realized")  # 第 5 月实际买下才兑现


def test_enough_cash_is_not_a_purchase(bundle_sv, scoring_sv):
    y1 = run(bundle_sv, scoring_sv, state_of(bundle_sv, LEARN), plan_of(bundle_sv, LEARN, 1))
    covered = [x for x in progress(y1) if x[3]]
    assert [x[0] for x in covered] == [8, 9, 10, 11, 12]  # 第 8 月末 1,260 起就够 1,200
    assert all(x[4] == "active" for x in covered)
    assert y1.settlement.outcome.savings.realization is None
    # 第 2 年不安排关联的报名：到第 7 月月末记"到期未兑现"，钱没有被动用，课也没有开始
    y2 = run(bundle_sv, scoring_sv, y1.next_state, plan(bundle_sv, LEARN, 2, HOME, FOOD, SHOP))
    out = y2.settlement.outcome
    assert flows_for(y2, COURSE) == [] and out.career.learning == ()
    due = [e for e in out.savings.events if e.kind == "due_unrealized"]
    assert [(e.year_index, e.month) for e in due] == [(2, 7)]
    assert "没有关联目标的购买或报名" in due[0].note
    assert goal_of(y2.next_state, "tuition").status == "overdue"
    assert cents(out.cash_end) == 174_000 + 12 * 12_000  # 一分没少


# --------------------------------------------------------------------------
# 4、5：买一次扣一次、只有一份服务；付清学费不等于学会
# --------------------------------------------------------------------------


def test_one_purchase_one_charge_one_service(bundle_sv, scoring_sv):
    _, y2, y3 = run_clone(bundle_sv, scoring_sv, SAVE)
    assert flows_for(y2, DRYER) == [-240_000]
    out = y2.settlement.outcome
    service = [h for h in out.holdings_service if h.opportunity_id == DRYER]
    assert len(service) == 1 and service[0].quantity == 1
    assert service[0].covered_months == tuple(range(5, 13))
    r = out.savings.realization
    assert (r.year_index, r.month, r.plan_order, cents(r.amount_paid)) == (2, 5, 3, 240_000)
    assert r.evidence == "durable_purchase_executed" and r.on_time
    assert [h.opportunity_id for h in y2.next_state.holdings] == [DRYER]
    # 服务按原物品规则评分（洗衣有了），目标本身不另加分：第 3 年没有任何目标事件，分数照旧由生活事实决定
    laundry = [
        next(c for c in m.components if c.component_id == "laundry").s for m in y2.score.months
    ]
    assert laundry[:4] == [0.0] * 4 and laundry[4:] == [laundry[4]] * 8 and laundry[4] > 0
    assert y3.settlement.outcome.savings.events == () and flows_for(y3, DRYER) == []
    assert cents(y3.settlement.outcome.cash_end) == 98_000 + 12 * 12_000


def test_paid_tuition_is_not_learning(bundle_sv, scoring_sv):
    _, y2, y3 = run_clone(bundle_sv, scoring_sv, LEARN)
    r = y2.settlement.outcome.savings.realization
    assert (r.month, r.evidence, cents(r.amount_paid)) == (7, "tuition_paid_in_full", 120_000)
    assert "不表示学完或取得资格" in r.note
    rec = y2.settlement.outcome.career.learning[0]
    assert (rec.progress_before, rec.progress_after, rec.completed_month, rec.granted) == (
        0,
        6,
        None,
        (),
    )
    assert y2.next_state.abilities == ()  # 付清了学费，还没学会
    assert flows_for(y3, COURSE) == []  # 第 3 年继续上课不再收费
    done = y3.settlement.outcome.career.learning[0]
    assert (done.completed_month, done.granted, done.usable_from_year) == (
        6,
        ("accounting_cert",),
        4,
    )


# --------------------------------------------------------------------------
# 6、7：取消、替换不动钱；额外支出让差距变大，没有隐含专款
# --------------------------------------------------------------------------


def test_cancel_and_replace_move_no_money(bundle_sv, scoring_sv):
    (y1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    start = y1.next_state
    kept = run(bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP))
    cancel = {"action": "cancel", "goal_id": "dryer"}
    cancelled = run(
        bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, goal=cancel)
    )
    replace = set_goal("course", COURSE, 3, 1) | {"action": "replace", "replaces": "dryer"}
    replaced = run(
        bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, goal=replace)
    )
    for r in (cancelled, replaced):
        assert money_facts(r) == money_facts(kept)  # 不退款、不加钱
        assert r.score.annual_mean == kept.score.annual_mean  # 不发满足感
        assert r.next_state.energy.level == kept.next_state.energy.level
    old = goal_of(cancelled.next_state, "dryer")
    assert old.status == "cancelled" and old.events[-1].kind == "cancelled"
    assert cancelled.settlement.outcome.savings.progress == ()  # 没有未结束的目标就不再比较
    gone = goal_of(replaced.next_state, "dryer")
    new = goal_of(replaced.next_state, "course")
    assert (gone.status, gone.replaced_by) == ("replaced", "course")
    assert (new.status, new.events[0].replaces) == ("active", "dryer")
    assert [g.goal_id for g in replaced.next_state.savings_goals] == ["dryer", "course"]


def test_extra_spending_widens_the_gap_without_a_hidden_fund(bundle_sv, scoring_sv):
    """一笔计划内的普通开支（探亲）——这不是随机疾病机制，只是证明目标既不挡住实际支出，也不复制资金。"""
    (y1, *_) = run_clone(bundle_sv, scoring_sv, DELAY)
    y2 = run(bundle_sv, scoring_sv, y1.next_state, plan_of(bundle_sv, DELAY, 2))
    assert flows_for(y2, VISIT) == [-150_000]  # 探亲照付，目标挡不住
    p = progress(y2)
    assert p[1] == (1, 256_000, 0, True, "active")
    assert p[2] == (2, 118_000, 122_000, False, "active")  # 差距随真实余额变大
    assert p[6] == (6, 166_000, 74_000, False, "overdue")  # 第 6 月买不起
    rejected = next(i for i in y2.settlement.outcome.items if i.opportunity_id == DRYER)
    assert rejected.status == "rejected" and "资金不足" in rejected.rejections[0].reason
    # 同样的生活、没有任何目标：钱分毫不差——目标没有藏起一笔钱，也没有多出一笔钱
    bare = plan(bundle_sv, DELAY, 2, HOME, FOOD, SHOP, buy(VISIT, 2), {**buy(DRYER, 6)}, goal=None)
    no_goal_state = revise(y1.next_state, savings_goals=())
    r = run(bundle_sv, scoring_sv, no_goal_state, bare)
    assert cents(r.settlement.outcome.cash_end) == cents(y2.settlement.outcome.cash_end) == 238_000


# --------------------------------------------------------------------------
# 8：缺钱、缺货、未知报价、错误口径、零学费、已持有、已付学费、未关联——都不会制造成功的目标
# --------------------------------------------------------------------------


def _codes(r) -> set[str]:
    return {p.code for p in r.settlement.problems}


def _catalog_with(bundle, **changes_by_id):
    cat = bundle.catalogs[0]
    opps = tuple(
        revise(o, **changes_by_id[o.opportunity_id]) if o.opportunity_id in changes_by_id else o
        for o in cat.opportunities
    )
    return revise(bundle, catalogs=(revise(cat, opportunities=opps),))


def test_missing_cash_stock_or_quote_never_realizes(bundle_sv, scoring_sv):
    # 缺钱：u83 第 2 年第 6 月（见上）——目标是逾期，不是兑现
    (_, y2, _) = run_clone(bundle_sv, scoring_sv, DELAY)
    assert y2.settlement.outcome.savings.realization is None
    # 缺货：第 1 年设目标时可得，第 2 年目录写确定缺货——关联购买被拒，差距记为未知（不补零）
    (s1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    gone = _catalog_with(bundle_sv, **{DRYER: {"availability": "unavailable", "price": None}})
    r = run(gone, scoring_sv, s1.next_state, plan_of(bundle_sv, SAVE, 2))
    sv = r.settlement.outcome.savings
    assert sv.realization is None and flows_for(r, DRYER) == []
    assert all(g.quote is None and g.shortfall is None for g in sv.progress)
    assert any("没有可用报价" in n for n in sv.notes)
    # 未知报价：不能建目标
    unknown = _catalog_with(bundle_sv, **{DRYER: {"availability": "unknown", "price": None}})
    r = run(unknown, scoring_sv, state_of(bundle_sv, SAVE), plan_of(bundle_sv, SAVE, 1))
    assert r.settlement.status == "input_error" and "goal_rejected" in _codes(r)
    assert any("报价未知" in p.message for p in r.settlement.problems)


def test_wrong_basis_zero_tuition_held_or_paid_goals_are_refused(bundle_sv, scoring_sv):
    start = state_of(bundle_sv, SAVE)
    # 错误口径：目录本身就不收别的口径的报价；目标与账户口径不同也构造不出来
    cat = bundle_sv.catalogs[0]
    eur = cat.opportunity(DRYER).price.model_dump(mode="python")
    eur["amount"] = {"amount": 2400, "numeraire": "EUR_2024_LOCAL"}
    with pytest.raises(ValidationError, match="不同口径的钱不能进同一本账"):
        _catalog_with(bundle_sv, **{DRYER: {"price": eur}})
    (s1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    g = goal_of(s1.next_state, "dryer").model_dump(mode="python")
    g["money_basis_id"] = "EUR_2024_LOCAL"
    with pytest.raises(ValidationError, match="口径"):
        SavingsGoal.model_validate(g)
    with pytest.raises(ValidationError, match="口径不一致"):
        GoalQuote.model_validate(
            {
                **goal_of(s1.next_state, "dryer").quote.model_dump(mode="python"),
                "unit_price": {"amount": 2400, "numeraire": "EUR_2024_LOCAL"},
            }
        )
    cases = {
        "零学费": (start, set_goal("free", FREE, 1, 12), "报价是 0"),
        "目标年超出 N": (start, set_goal("late", DRYER, 4, 1), "不在本年"),
        "不是一次性购买": (start, set_goal("rent", "a81.housing.near", 1, 12), "只能是一次性购买"),
        "已持有": (
            revise(
                start,
                holdings=(
                    {
                        "opportunity_id": WASHER,
                        "quantity": 1,
                        "acquired_year": 0,
                        "remaining_months": 60,
                    },
                ),
            ),
            set_goal("w2", WASHER, 1, 12),
            "已持有",
        ),
    }
    for name, (st, goal, word) in cases.items():
        r = settle_year(
            protocol=bundle_sv.protocol,
            state=st,
            action=plan(bundle_sv, SAVE, st.year_index, HOME, FOOD, SHOP, goal=goal),
            catalog=catalog_of(bundle_sv, st),
            tax=tax_of(bundle_sv, catalog_of(bundle_sv, st)),
        )
        assert r.status == "input_error" and r.outcome is None, name
        assert any(word in p.message for p in r.problems), (name, [p.message for p in r.problems])
    # 学费已结清：第 1 年上了半年课（学费付过），第 2 年再为这门课设学费目标——没有要攒的学费，旧付款也不冒充新成果
    y1 = run(
        bundle_sv,
        scoring_sv,
        revise(state_of(bundle_sv, LEARN), cash={"amount": 5000, "numeraire": "USD_2024_LOCAL"}),
        plan(bundle_sv, LEARN, 1, HOME, FOOD, SHOP, course(1, 6)),
    )
    assert y1.next_state.training[0].fee_paid
    r = run(
        bundle_sv,
        scoring_sv,
        y1.next_state,
        plan(bundle_sv, LEARN, 2, HOME, FOOD, SHOP, goal=set_goal("t2", COURSE, 2, 12)),
    )
    assert r.settlement.status == "input_error"
    assert any("已经结清" in p.message for p in r.settlement.problems)


def test_links_must_match_and_unlinked_purchases_do_not_count(bundle_sv, scoring_sv):
    (s1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    start = s1.next_state
    # 机会对得上、数量不对；关联一项不是购买或报名的行动；关联不存在的目标——都是计划错误，整份退回
    for items, word in (
        ((HOME, FOOD, SHOP, buy(DRYER, 5, "dryer", quantity=2)), "数量"),
        ((HOME | {"goal_id": "dryer"}, FOOD, SHOP), "只有一次性购买的耐用品或培训报名"),
        ((HOME, FOOD, SHOP, buy(DRYER, 5, "nope")), "不存在或已结束"),
    ):
        r = run(bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, *items))
        assert r.settlement.status == "input_error" and r.settlement.outcome is None
        assert "goal_link_invalid" in _codes(r)
        assert any(word in p.message for p in r.settlement.problems), word
    # 两条行动同时关联：契约层就拒绝（一笔交易最多兑现一个目标，一个目标也只由一条行动兑现）
    with pytest.raises(ValidationError, match="都写了 goal_id"):
        plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, buy(DRYER, 5, "dryer"), buy(WASHER, 5, "dryer"))
    # 买了目标的东西但没写 goal_id：钱照付、东西照买，目标不算兑现
    r = run(bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, buy(DRYER, 5)))
    assert flows_for(r, DRYER) == [-240_000]
    sv = r.settlement.outcome.savings
    assert sv.realization is None and goal_of(r.next_state, "dryer").status == "overdue"
    assert any("没有写 goal_id" in n for n in sv.notes)


# --------------------------------------------------------------------------
# 9、10：到期未兑现不是出局；目标正确进入下一年与终态
# --------------------------------------------------------------------------


def test_overdue_is_not_an_exit_and_goals_carry_to_the_terminal_state(bundle_sv, scoring_sv):
    y1, y2, y3 = run_clone(bundle_sv, scoring_sv, DELAY)
    assert (y2.settlement.status, y2.handoff, y2.next_state.status) == (
        "completed",
        "next_year_ready",
        "active",
    )
    assert goal_of(y2.next_state, "dryer").status == "overdue"  # 逾期的目标原样进入下一年
    assert y1.next_state.savings_goals[0].status == "active"  # 未到期的目标原样进入下一年
    term = y3.next_state
    assert term.status == "terminal" and goal_of(term, "dryer").status == "realized"
    kinds = [e.kind for e in goal_of(term, "dryer").events]
    assert kinds == ["created", "due_unrealized", "rescheduled", "realized"]  # 历史依据都在
    assert cents(term.cash) == 142_000
    # 第 3 年不买、只把时点改到第 12 月：年末到期未兑现，终态保存这个逾期目标与余额
    lazy = plan(
        bundle_sv,
        DELAY,
        3,
        HOME,
        FOOD,
        SHOP,
        goal={"action": "reschedule", "goal_id": "dryer", "target_year": 3, "target_month": 12},
    )
    r = run(bundle_sv, scoring_sv, y2.next_state, lazy)
    assert r.settlement.outcome.savings.progress[0].status == "active"  # 改时点后回到进行中
    assert r.handoff == "terminal" and goal_of(r.next_state, "dryer").status == "overdue"
    assert cents(r.next_state.cash) == 238_000 + 12 * 12_000
    # 终态不再接受行动
    r = settle_year(
        protocol=bundle_sv.protocol,
        state=term,
        action=lazy,
        catalog=catalog_of(bundle_sv, term),
        tax=tax_of(bundle_sv, catalog_of(bundle_sv, term)),
    )
    assert r.status == "input_error" and "state_not_active" in {p.code for p in r.problems}


def test_an_exit_keeps_the_facts_before_it(bundle_sv, scoring_sv):
    poor = revise(state_of(bundle_sv, SAVE), cash={"amount": 100, "numeraire": "USD_2024_LOCAL"})
    # 没有工作、每月要 1,380：第 1 月就付不起最低生活
    r = run(
        bundle_sv,
        scoring_sv,
        poor,
        plan(bundle_sv, SAVE, 1, HOME, FOOD, goal=set_goal("dryer", DRYER, 2, 6)),
    )
    assert r.handoff == "exited" and r.next_state.exit.month == 1
    sv = r.settlement.outcome.savings
    assert [e.kind for e in sv.events] == ["created"]  # 年初的指令是出局前发生的事实
    assert [g.month for g in sv.progress] == [None]  # 出局当月起不再更新
    assert goal_of(r.next_state, "dryer").status == "active"
    assert any("出局" in n for n in sv.notes)


# --------------------------------------------------------------------------
# 11：整份试算失败、反馈受阻、重复交接、跨试算混用
# --------------------------------------------------------------------------


def test_failed_trials_leave_nothing_behind(bundle_sv, scoring_sv):
    tired = revise(
        state_of(bundle_sv, LEARN),
        cash={"amount": 5000, "numeraire": "USD_2024_LOCAL"},  # 付得起学费，课才真的开始
        energy={"level": 150, "scale_version": "e1-4a-monthly-quota-v1"},
        energy_quota_basis={"base_level": 150, "status": "base_first_year"},
    )
    action = plan(
        bundle_sv,
        LEARN,
        1,
        HOME,
        FOOD,
        SHOP,
        course(1, 12, "tuition"),
        goal=set_goal("tuition", COURSE, 1, 1),
    )
    r = run(bundle_sv, scoring_sv, tired, action)
    # 每月负担 115.75 + 48 = 163.75 > 150：整份计划不可行，什么都不提交
    assert r.settlement.status == "plan_infeasible"
    assert (r.settlement.outcome, r.next_state) == (None, None)
    bad = plan(bundle_sv, LEARN, 1, HOME, FOOD, SHOP, goal=set_goal("free", FREE, 1, 12))
    r = run(bundle_sv, scoring_sv, state_of(bundle_sv, LEARN), bad)
    assert r.settlement.status == "input_error" and (r.settlement.outcome, r.next_state) == (
        None,
        None,
    )


def test_blocked_feedback_keeps_the_year_facts_but_no_next_state(bundle_sv, scoring_sv):
    (y1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    r = run(bundle_sv, scoring_sv, y1.next_state, plan_of(bundle_sv, SAVE, 2), with_scoring=False)
    assert r.handoff == "feedback_blocked" and r.next_state is None
    assert r.settlement.outcome.savings.realization.month == 5  # 已经发生的现金与目标事实保留
    cand = r.settlement.next_state
    assert cand.energy_quota_basis.status == "pending_feedback"
    assert goal_of(cand, "dryer").status == "realized"


def test_handoffs_are_not_repeated_or_mixed(bundle_sv, scoring_sv):
    (y1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    start = y1.next_state
    bought = bound_year(
        bundle_sv, scoring_sv, SAVE, state=start, action=plan_of(bundle_sv, SAVE, 2)
    )
    skipped = bound_year(
        bundle_sv,
        scoring_sv,
        SAVE,
        state=start,
        action=plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP),
    )
    rec = feedback_record(protocol=bundle_sv.protocol, year=bought)
    once = apply_feedback(bought.settlement.next_state, rec)
    events = goal_of(once, "dryer").events
    with pytest.raises(LifeContractError, match="重复交接不叠加"):
        apply_feedback(once, rec)
    assert goal_of(once, "dryer").events == events  # 没有多出一条兑现
    # 没买的那次试算的现金 + 买了的那次的兑现记录：拒绝
    with pytest.raises(LifeContractError, match="另一次年度结算的候选状态"):
        apply_feedback(skipped.settlement.next_state, rec)
    with pytest.raises(LifeContractError, match="被改动过"):
        feedback_record(
            protocol=bundle_sv.protocol,
            year=skipped.model_copy(update={"settlement": bought.settlement}),
        )


# --------------------------------------------------------------------------
# 13：旧协议、旧格式
# --------------------------------------------------------------------------


def test_old_protocols_do_not_silently_enable_goals(bundle5a, scoring5a, raw_sv):
    s = state_of(bundle5a, "u51.a71.study")
    action = plan_of(bundle5a, "u51.a71.study", 1)
    goal = SavingsGoalDirective(
        action="set",
        goal_id="g",
        opportunity_id="a71.durable.washer",
        quantity=1,
        target_year=2,
        target_month=1,
    )
    r = settle_year(
        protocol=bundle5a.protocol,
        state=s,
        action=action.model_copy(update={"savings_goal": goal}),
        catalog=catalog_of(bundle5a, s),
        tax=tax_of(bundle5a, catalog_of(bundle5a, s)),
    )
    assert r.status == "input_error" and "goal_not_supported" in {p.code for p in r.problems}
    r = settle_year(
        protocol=bundle5a.protocol,
        state=revise(s, savings_goals=()),
        action=action,
        catalog=catalog_of(bundle5a, s),
        tax=tax_of(bundle5a, catalog_of(bundle5a, s)),
    )
    assert r.status == "input_error" and "savings_state" in {p.code for p in r.problems}
    # 旧协议的结果与状态里没有任何目标字段（逐字节兼容）
    (old, *_) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    blob = old.model_dump_json()
    assert "savings" not in blob and "goal_id" not in blob
    # 新版本漏写目标规则、旧版本带目标规则：都拒绝
    data = copy.deepcopy(raw_sv)
    del data["protocol"]["savings_rules"]
    with pytest.raises(LifeContractError, match="必须声明 savings_rules"):
        parse_bundle(data)
    data = copy.deepcopy(raw_sv)
    data["protocol"]["life_rules_version"] = "e1-5a-monthly-career-v1"
    with pytest.raises(LifeContractError, match="不读目标储蓄规则"):
        parse_bundle(data)
    # 新协议的状态必须写 savings_goals（空列表也要写）
    data = copy.deepcopy(raw_sv)
    del data["initial_states"][0]["savings_goals"]
    from ledger.life_catalog import cross_validate

    report = cross_validate(parse_bundle(data))
    assert any("必须写 savings_goals" in e for e in report.errors)


# --------------------------------------------------------------------------
# 14：可见投影与来源；内部对象不泄漏
# --------------------------------------------------------------------------


def test_the_agent_sees_its_goal_balance_quote_gap_and_reasons(bundle_sv, scoring_sv):
    _, y2, _ = run_clone(bundle_sv, scoring_sv, DELAY)
    state = y2.next_state
    fb = visible_feedback(y2.settlement)
    obs = build_observation(
        permission=standard_product_permission("e1-5b-test"),
        profile=next(p for p in bundle_sv.stated_profiles if p.person_id == "u83"),
        state=state,
        catalog=catalog_of(bundle_sv, state),
        last_feedback=fb,
        resource_rules=bundle_sv.protocol.resource_rules,
        energy_feedback=bundle_sv.protocol.energy_feedback,
        career_rules=bundle_sv.protocol.career_rules,
        savings_rules=bundle_sv.protocol.savings_rules,
    )
    g = obs.savings.open_goal
    assert (g.goal_id, g.status, g.past_due) == ("dryer", "overdue", True)
    assert (cents(g.cash), cents(g.quote_now), cents(g.shortfall), g.cash_covers_quote) == (
        238_000,
        240_000,
        2_000,
        False,
    )
    assert cents(g.quote_at_creation) == 240_000 and "不是价格预测" in g.quote_source
    assert any(e.kind == "due_unrealized" and "资金不足" in e.note for e in g.events)
    assert any("只是一条比较" in r for r in obs.savings.rules)
    assert fb.savings is not None and fb.savings.progress[2].month == 2
    assert any("未兑现" in n for n in fb.savings.notes)
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)
    assert "dryer" in blob and "rho" not in blob and "annual_mean" not in blob
    # 内部对象塞进可见字段：拒绝
    with pytest.raises(LifeContractError, match="出现在可见字段"):
        observation_payload(obs.model_copy(update={"goals": (goal_of(state, "dryer"),)}))
    # 新协议的状态必须同时给目标规则；旧协议不能给
    with pytest.raises(LifeContractError, match="savings_rules"):
        build_observation(
            permission=standard_product_permission("e1-5b-test"),
            profile=next(p for p in bundle_sv.stated_profiles if p.person_id == "u83"),
            state=state,
            catalog=catalog_of(bundle_sv, state),
            resource_rules=bundle_sv.protocol.resource_rules,
            energy_feedback=bundle_sv.protocol.energy_feedback,
            career_rules=bundle_sv.protocol.career_rules,
        )


# --------------------------------------------------------------------------
# 契约形状
# --------------------------------------------------------------------------


def test_directive_and_goal_shapes():
    with pytest.raises(ValidationError, match="必须写机会"):
        SavingsGoalDirective(action="set", goal_id="g")
    with pytest.raises(ValidationError, match="不改目标年月"):
        SavingsGoalDirective(action="keep", goal_id="g", target_year=2, target_month=1)
    with pytest.raises(ValidationError, match="replace 必须写"):
        SavingsGoalDirective(
            action="replace",
            goal_id="g",
            opportunity_id="x",
            quantity=1,
            target_year=2,
            target_month=1,
        )
    with pytest.raises(ValidationError, match="必须写新的目标年月"):
        SavingsGoalDirective(action="reschedule", goal_id="g")


def test_the_protocol_identity_covers_the_savings_rules(bundle_sv):
    rules = bundle_sv.protocol.savings_rules
    assert rules.content()["realization"] == "linked_executed_full_payment"
    renamed = revise(rules, note="只改说明")
    assert renamed.content_fingerprint == rules.content_fingerprint
    with pytest.raises(ValidationError):
        revise(rules, max_open_goals=2)  # 本轮只允许一个未结束的目标


def test_a_goal_can_be_set_and_realized_in_the_same_year(bundle_sv, scoring_sv):
    rich = revise(state_of(bundle_sv, SAVE), cash={"amount": 3000, "numeraire": "USD_2024_LOCAL"})
    r = run(
        bundle_sv,
        scoring_sv,
        rich,
        plan(
            bundle_sv,
            SAVE,
            1,
            HOME,
            FOOD,
            SHOP,
            buy(DRYER, 2, "dryer"),
            goal=set_goal("dryer", DRYER, 1, 6),
        ),
    )
    sv = r.settlement.outcome.savings
    assert [e.kind for e in sv.events] == ["created", "realized"]
    # 第 2 月：3,000 + 120（第 1 月）= 3,120；3,120 + 1,500 − 1,380 − 2,400 = 840
    assert (sv.realization.month, cents(r.settlement.outcome.months[1].cash_end)) == (2, 84_000)
    assert [g.month for g in sv.progress] == [None, 1, 2]  # 兑现之后不再比较


def test_directives_must_point_at_the_one_open_goal(bundle_sv, scoring_sv):
    (y1, *_) = run_clone(bundle_sv, scoring_sv, SAVE)
    start = y1.next_state  # 有一个未结束的目标 dryer
    bad = {
        "已有目标还要再设一个": set_goal("second", WASHER, 2, 6),
        "保留的不是现有目标": {"action": "keep", "goal_id": "other"},
        "取消的不是现有目标": {"action": "cancel", "goal_id": "other"},
        "替换的不是现有目标": set_goal("new", WASHER, 2, 6)
        | {"action": "replace", "replaces": "other"},
    }
    for name, goal in bad.items():
        r = run(bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, goal=goal))
        assert r.settlement.status == "input_error" and "goal_rejected" in _codes(r), name
    # 取消之后，下一年用同一个 ID 再设：ID 全史唯一，旧记录不被覆盖
    cancel = {"action": "cancel", "goal_id": "dryer"}
    y2 = run(bundle_sv, scoring_sv, start, plan(bundle_sv, SAVE, 2, HOME, FOOD, SHOP, goal=cancel))
    again = plan(bundle_sv, SAVE, 3, HOME, FOOD, SHOP, goal=set_goal("dryer", WASHER, 3, 6))
    r = run(bundle_sv, scoring_sv, y2.next_state, again)
    assert r.settlement.status == "input_error"
    assert any("已经用过" in p.message for p in r.settlement.problems)


def test_every_internal_savings_type_is_refused_by_name(bundle_sv, scoring_sv):
    """变异检查发现的缺口（2026-10-09）：只塞一个目标对象时，扫描可能因为它里面的报价依据而拒绝，掩盖了目标类型本身
    没有登记。这里把每个内部类型单独塞进可见字段，要求按**它自己的类型名**被拒绝。"""
    _, y2, _ = run_clone(bundle_sv, scoring_sv, SAVE)
    out = y2.settlement.outcome
    goal = goal_of(y2.next_state, "dryer")
    internals = (
        bundle_sv.protocol.savings_rules,
        goal,
        goal.quote,
        goal.events[0],
        goal.realization,
        out.savings.progress[0],
        out.savings,
    )
    state = y2.next_state
    obs = build_observation(
        permission=standard_product_permission("e1-5b-test"),
        profile=next(p for p in bundle_sv.stated_profiles if p.person_id == "u81"),
        state=state,
        catalog=catalog_of(bundle_sv, state),
        resource_rules=bundle_sv.protocol.resource_rules,
        energy_feedback=bundle_sv.protocol.energy_feedback,
        career_rules=bundle_sv.protocol.career_rules,
        savings_rules=bundle_sv.protocol.savings_rules,
    )
    for obj in internals:
        name = type(obj).__name__
        with pytest.raises(LifeContractError, match=f"^{name} 出现在可见字段"):
            observation_payload(obs.model_copy(update={"goals": (obj,)}))
