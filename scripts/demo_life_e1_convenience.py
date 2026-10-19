"""E1 补充"日常生活便利"的离线故事（`DECISIONS/0033`）。零大模型调用、零网络。

    uv run python scripts/demo_life_e1_convenience.py --out runs/<新目录>

用合成样例 ``configs/life/synthetic_e1_convenience.yaml``、评分配置 ``scoring_e1_convenience.yaml`` 与评价规格 ``evaluation_e1_convenience.yaml``，
按**预先写好的年度计划**经现有引擎（``econ.life_year.run_life_year``：结算 → 评分 → 反馈；``econ.life_evaluation.evaluate_clone``：跨期评价）
跑七个可以手算的故事，并逐项与手算（精确分数）核对：

1. 同一个人不用便利方案、买设备、买人工服务：现金、本人家务小时、负担、便利分；
2. 设备与服务同时在用：同一项家务只按残余最少的方案算，不重复减负、不重复加分；
3. 年中购买、已有设备到期、按月服务有几个月没买到：便利分随实际服务月变化；
4. 学习计划原本超出精力额度，用了便利方案之后可行：省下的时间与负担确实进入已有的执行链，学习要求与授予时点不变；
5. 两座城：K51 设备便宜、M52 人工服务便宜，效果定义相同——同样的减负，不同的花费，同样的便利分；
6. 期末已有设备、没有设备、一直买服务：参考估值的六个模板（两种岗位 × 三种生活方案）怎样用到它们；
7. 只改便利权重、计划相同：评分差异可以拆开解释；权重为 0 时，时间与负担的减少照样存在。

另有两项核对：设备买下的那个月与"年初就持有同一台设备"的月份便利分逐位相同（成交本身不计分）；评分规格里没有洗衣分项。

全部数字来自合成样例：不是现实价格、工资、时间利用或心理规律，**不是城市评价，也不排序城市**。目录已存在就拒绝，不覆盖旧结果。
退出码：0 = 全部核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.life_contracts import (
    Holding,
    LifeContractError,
    PersonYearState,
    StatedLifeProfile,
    YearAction,
)
from common.life_feedback_contracts import LifeYearResult
from common.life_scoring_contracts import DAILY_CONVENIENCE, LEGACY_LAUNDRY
from common.money import Money
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_evaluation_config import load_evaluation_config
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_convenience.yaml")
SCORING = Path("configs/life/scoring_e1_convenience.yaml")
EVALUATION = Path("configs/life/evaluation_e1_convenience.yaml")
KEY_SOURCES = (
    "src/common/life_scoring_contracts.py",
    "src/common/life_units.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_scoring.py",
    "src/econ/life_resources.py",
    "src/econ/life_settlement.py",
    "src/econ/life_year.py",
    "src/econ/life_evaluation.py",
    "src/econ/life_terminal_value.py",
    "scripts/demo_life_e1_convenience.py",
    "configs/life/synthetic_e1_convenience.yaml",
    "configs/life/scoring_e1_convenience.yaml",
    "configs/life/evaluation_e1_convenience.yaml",
)
K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"
TAU, CAP = Fraction(8), Fraction(9, 10)
HEALTHY = {"minor": "decline", "major": "decline", "pause_order": []}


# ---------------------------------------------------------------------- 手算（精确分数）


def s_convenience(q: Fraction) -> Fraction:
    """s = min(c, q/(q + τ))，τ = 8 小时/月，c = 0.9。"""
    return min(CAP, q / (q + TAU))


def month_u(
    *,
    s_c: Fraction,
    fresh: int = 3,
    minutes: int = 70,
    visits: int = 20,
    growth=0,
    gym=False,
    m_house=Fraction(3, 10),
    m_fresh=Fraction(3, 10),
    m_conv=Fraction(2, 5),
) -> Fraction:
    """u301 一个健康月份的总分（手算）：S = 0.8 × B + 0.2 × 1。

    六维有效权重（七维点数去掉医疗 10 点、按其余 90 点归一）：物质 40/90、安全 5/90、发展 10/90、休闲 15/90、环境 5/90、交通 15/90。
    物质 = 0.3 × 住房 + 0.3 × 新鲜食品 + 0.4 × 便利；住房 600 平方英尺 × 0.01 = 6，6/(6+6) = 0.5；新鲜食品 3 餐 → 3/10、21 餐 → 21/28；
    安全 3/(3+3)、发展 (2 + 12 g)/(… + 2)、休闲 (4 + 健身 8 次)/(… + 12)、环境 3/(3+3)；交通 k = 0.25 + 0.75 × D/20，T_eq = k × T，60/(60 + T_eq)。
    """
    fr = Fraction

    def sat(z, tau):
        return min(CAP, fr(z) / (fr(z) + tau))

    material = m_house * sat(6, 6) + m_fresh * sat(fresh, 7) + m_conv * s_c
    k = fr(1, 4) + fr(3, 4) * fr(visits, 20)
    commute = fr(60) / (60 + k * minutes)
    b = (
        fr(40, 90) * material
        + fr(5, 90) * sat(3, 3)
        + fr(10, 90) * sat(2 + 12 * fr(growth), 2)
        + fr(15, 90) * sat(4 + (8 if gym else 0), 12)
        + fr(5, 90) * sat(3, 3)
        + fr(15, 90) * commute
    )
    return fr(4, 5) * b + fr(1, 5)


# ---------------------------------------------------------------------- 引擎调用


class World:
    def __init__(self) -> None:
        self.bundle: LifeBundle
        self.bundle, _ = load_life_bundle(LIFE)
        self.scoring: ScoringConfig
        self.scoring, _ = load_scoring_config(SCORING, self.bundle)
        self.evaluation = load_evaluation_config(EVALUATION, self.bundle, self.scoring).spec
        self.protocol = self.bundle.protocol
        self.profile = next(p for p in self.bundle.stated_profiles if p.person_id == "u301")

    def catalog(self, city: str):
        cat = self.bundle.catalog_for(city)
        assert cat is not None
        return cat

    def tax(self, city: str):
        policy = self.bundle.tax_policy(self.catalog(city).tax_policy_id)
        assert policy is not None
        return binding_from_policy(policy)

    def inputs(self, city: str, profile: StatedLifeProfile | None = None) -> ScoringInputs:
        p = profile or self.profile
        cond = self.scoring.conditions_for(city)
        assert cond is not None
        return ScoringInputs(
            spec=self.scoring.spec,
            conditions=cond,
            preference_input=p,
            hard_requirements=p.hard_requirements,
        )

    def start(self, city: str) -> PersonYearState:
        clone = "u301.k51.r0" if city == K51 else "u301.m52.r0"
        state = self.bundle.initial_state(clone)
        assert state is not None
        return state

    def year(
        self, state: PersonYearState, items: list[dict[str, Any]], *, profile=None, emergency=None
    ) -> LifeYearResult:
        action = YearAction.model_validate(
            {
                "person_id": state.person_id,
                "clone_id": state.clone_id,
                "year_index": state.year_index,
                "items": items,
                "minimum_fallback": ["housing", "food"],
                "emergency": emergency or HEALTHY,
                "rationale": "离线故事：预先写好的计划",
            }
        )
        city = str(state.city)
        return run_life_year(
            protocol=self.protocol,
            state=state,
            action=action,
            catalog=self.catalog(city),
            tax=self.tax(city),
            scoring=self.inputs(city, profile),
        )


def whole(oid: str, start: int = 1, end: int = 12) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def once(oid: str, month: int) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "month": month}


def base_items(city: str, job: str = "clerk") -> list[dict[str, Any]]:
    c = "k51" if city == K51 else "m52"
    return [whole(f"{c}.housing.far"), whole(f"{c}.food.basic"), whole(f"{c}.job.{job}")]


def summarize(r: LifeYearResult) -> dict[str, Any]:
    """一年的可读摘要：现金、逐月家务小时与负担、便利的 q 与 s、逐月总分。"""
    out = r.settlement.outcome
    row: dict[str, Any] = {"settlement_status": r.settlement.status, "handoff": r.handoff}
    if out is None:
        row["problems"] = [p.code for p in r.settlement.problems]
        return row
    row["cash_end"] = str(out.months[-1].cash_end.amount) if out.months else None
    hours, burden = [], []
    assert out.resources is not None
    for m in out.resources.months:
        tasks = [c for c in m.contributions if c.category == "household_task"]
        hours.append(str(sum((c.hours.fraction() for c in tasks), Fraction(0))))
        burden.append(str(sum((c.burden.fraction() for c in tasks), Fraction(0))))
    row["household_hours"] = hours
    row["household_burden"] = burden
    row["energy_used_month1"] = (
        out.resources.months[0].energy_used.exact if out.resources.months[0].energy_used else None
    )
    score = r.score
    if score is not None:
        q, s, u = [], [], []
        for m in score.months:
            comp = next((c for c in m.components if c.component_id == DAILY_CONVENIENCE), None)
            q.append(
                comp.convenience.relief_hours.exact
                if comp is not None and comp.convenience
                else None
            )
            s.append(comp.s if comp is not None else None)
            u.append(m.u)
        row.update(
            q=q,
            s_convenience=s,
            u=u,
            annual_mean=score.annual_mean,
            annual_status=score.annual_status,
        )
    return row


# ---------------------------------------------------------------------- 故事


def check(results: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def as_fraction(x: float) -> Fraction:
    return Fraction(Decimal(repr(x)))


def run(out_dir: Path) -> dict[str, Any]:
    w = World()
    checks: list[dict[str, Any]] = []
    stories: dict[str, Any] = {}
    spec = w.scoring.spec

    # ---- 规格：没有洗衣分项；便利分项的 τ 与 c
    needs = [c.material_need for c in spec.material_components()]
    check(
        checks,
        "规格：物质三项且没有洗衣分项",
        needs == ["housing_space", "fresh_food", DAILY_CONVENIENCE] and LEGACY_LAUNDRY not in needs,
        f"物质细需求 {needs}",
    )
    conv = spec.component(DAILY_CONVENIENCE)
    assert conv is not None and conv.convenience is not None
    check(
        checks,
        "规格：τ = 8 小时/月、c = 0.9、任务组合 洗衣 + 打扫",
        (conv.tau, conv.convenience.cap, conv.convenience.tasks)
        == (8, 0.9, ("laundry", "cleaning")),
        f"τ = {conv.tau}, c = {conv.convenience.cap}, tasks = {conv.convenience.tasks}",
    )

    # ---- 故事 1：不用、设备、人工服务（K51，全职文员，远处住房、基本食品）
    s0 = w.start(K51)
    plan1 = {
        "none": base_items(K51),
        "device": [*base_items(K51), once("k51.durable.convenience_pack", 1)],
        "service": [*base_items(K51), whole("k51.service.chores")],
    }
    one = {k: summarize(w.year(s0, v)) for k, v in plan1.items()}
    stories["1_none_device_service"] = one
    hand = {
        "none": (Fraction(0), "14640", "22", "12"),
        "device": (Fraction(12), "13200", "10", "28/5"),
        "service": (Fraction(18), "12048", "4", "11/5"),
    }
    for k, (q, cash, hours, burden) in hand.items():
        r = one[k]
        s_hand = s_convenience(q)
        u_hand = month_u(s_c=s_hand)
        check(
            checks,
            f"故事 1 {k}：年末现金",
            Decimal(r["cash_end"]) == Decimal(cash),
            f"{r['cash_end']} vs 手算 {cash}",
        )
        check(
            checks,
            f"故事 1 {k}：每月家务小时与负担",
            set(r["household_hours"]) == {hours} and set(r["household_burden"]) == {burden},
            f"{set(r['household_hours'])} 小时 / {set(r['household_burden'])} 负担点",
        )
        check(
            checks,
            f"故事 1 {k}：q 与便利分",
            set(r["q"]) == {str(q)}
            and all(as_fraction(s) == as_fraction(float(s_hand)) for s in r["s_convenience"]),
            f"q = {set(r['q'])}，s = {set(r['s_convenience'])}（手算 {float(s_hand)!r}）",
        )
        check(
            checks,
            f"故事 1 {k}：月度总分（健康月份）",
            all(abs(u - float(u_hand)) < 1e-12 for u in r["u"]),
            f"u = {r['u'][0]!r}（手算 {float(u_hand)!r}）",
        )

    # ---- 故事 2：设备与服务同时在用
    both = summarize(
        w.year(
            s0,
            [
                *base_items(K51),
                whole("k51.service.chores"),
                once("k51.durable.convenience_pack", 1),
            ],
        )
    )
    stories["2_device_and_service"] = both
    check(
        checks,
        "故事 2：同一项家务只按残余最少的方案算（q = 18，不是 12 + 18）",
        set(both["q"]) == {"18"} and set(both["household_hours"]) == {"4"},
        f"q = {set(both['q'])}，每月本人家务 {set(both['household_hours'])} 小时",
    )
    check(
        checks,
        "故事 2：便利分与只买服务时逐位相同（不重复加分）",
        both["s_convenience"] == one["service"]["s_convenience"],
        f"{both['s_convenience'][0]!r} vs {one['service']['s_convenience'][0]!r}",
    )
    check(
        checks,
        "故事 2：现金照付两份",
        Decimal(both["cash_end"]) == Decimal("10608"),
        f"{both['cash_end']}（手算 14,640 − 1,440 − 2,592 = 10,608）",
    )

    # ---- 故事 3：年中购买、已有设备到期、按月服务有几个月没买到
    mid = summarize(w.year(s0, [*base_items(K51), once("k51.durable.convenience_pack", 7)]))
    expiring_state = s0.model_copy(
        update={
            "holdings": (
                Holding(
                    opportunity_id="k51.durable.convenience_pack",
                    quantity=1,
                    acquired_year=0,
                    remaining_months=5,
                ),
            )
        }
    )
    expiring = summarize(w.year(expiring_state, base_items(K51)))
    poor_state = s0.model_copy(update={"cash": Money(300.0, s0.cash.numeraire)})
    poor = summarize(
        w.year(poor_state, [*base_items(K51, "shop_parttime"), whole("k51.service.chores")])
    )
    stories["3a_mid_year_purchase"] = mid
    stories["3b_holding_expires"] = expiring
    stories["3c_service_unaffordable_months"] = poor
    check(
        checks,
        "故事 3a：第 7 月买设备，1–6 月 q = 0、7–12 月 q = 12",
        mid["q"] == ["0"] * 6 + ["12"] * 6,
        f"{mid['q']}",
    )
    check(
        checks,
        "故事 3b：年初的设备还剩 5 个月，1–5 月 q = 12、之后 q = 0",
        expiring["q"] == ["12"] * 5 + ["0"] * 7,
        f"{expiring['q']}",
    )
    want_poor = ["18", "18", "18", "0", "18", "0", "18", "0", "0", "0", "0", "0"]
    check(
        checks,
        "故事 3c：兼职、开局现金 300，杂务服务只有付得起的月份起作用",
        poor["q"] == want_poor,
        f"{poor['q']}（手算：1、2、3、5、7 月买到）",
    )

    # ---- 故事 4：学习计划原本超额，用了便利方案之后可行
    course = whole("k51.training.accounting_course")
    four = {
        "none": w.year(s0, [*base_items(K51), course]),
        "device": w.year(s0, [*base_items(K51), course, once("k51.durable.convenience_pack", 1)]),
        "service": w.year(s0, [*base_items(K51), course, whole("k51.service.chores")]),
    }
    stories["4_course_feasibility"] = {k: summarize(v) for k, v in four.items()}
    nomin = four["none"]
    check(
        checks,
        "故事 4：不用便利方案时计划不可行（精力 252.5 > 250）",
        nomin.settlement.status == "plan_infeasible",
        f"状态 {nomin.settlement.status}，问题 {[p.code for p in nomin.settlement.problems]}",
    )
    for k in ("device", "service"):
        r = four[k]
        out = r.settlement.outcome
        ok = r.settlement.status == "completed" and out is not None
        if ok:
            assert out is not None and out.resources is not None and r.next_state is not None
            used = out.resources.months[0].energy_used
            learn = out.career.learning[0] if out.career and out.career.learning else None
            held = {a.ability_id: a for a in r.next_state.abilities}
            want = {"device": "2461/10", "service": "2427/10"}[k]
            ok = (
                used is not None
                and used.exact == want
                and learn is not None
                and learn.completed_month == 12
                and "accounting_cert" in held
                and held["accounting_cert"].usable_from_year == 2
            )
        check(
            checks,
            f"故事 4 {k}：可行；精力每月 {'246.1' if k == 'device' else '242.7'}；第 12 月学完，资格第 2 年起可用",
            ok,
            f"状态 {r.settlement.status}",
        )

    # ---- 故事 5：两座城，同样的减负、不同的花费
    m0 = w.start(M52)
    five = {
        "K51_device_m1": one["device"],
        "K51_service": one["service"],
        "M52_device_m1": summarize(
            w.year(m0, [*base_items(M52), once("m52.durable.convenience_pack", 1)])
        ),
        "M52_device_m3": summarize(
            w.year(m0, [*base_items(M52), once("m52.durable.convenience_pack", 3)])
        ),
        "M52_service": summarize(w.year(m0, [*base_items(M52), whole("m52.service.chores")])),
    }
    stories["5_two_cities"] = five
    check(
        checks,
        "故事 5：M52 设备 6,000，第 1 月现金 4,120 付不起（不顺延），q 全年 0",
        five["M52_device_m1"]["q"] == ["0"] * 12
        and Decimal(five["M52_device_m1"]["cash_end"]) == Decimal("14640"),
        f"q = {set(five['M52_device_m1']['q'])}，年末现金 {five['M52_device_m1']['cash_end']}",
    )
    check(
        checks,
        "故事 5：M52 第 3 月买设备，3–12 月 q = 12；便利分与 K51 的设备月份逐位相同",
        five["M52_device_m3"]["q"] == ["0", "0"] + ["12"] * 10
        and five["M52_device_m3"]["s_convenience"][5] == one["device"]["s_convenience"][5],
        f"{five['M52_device_m3']['q']}",
    )
    check(
        checks,
        "故事 5：两城杂务服务的便利分逐位相同，花费 2,592 vs 1,296",
        five["M52_service"]["s_convenience"] == five["K51_service"]["s_convenience"]
        and Decimal(five["K51_service"]["cash_end"]) == Decimal("12048")
        and Decimal(five["M52_service"]["cash_end"]) == Decimal("13344"),
        f"K51 年末 {five['K51_service']['cash_end']}，M52 年末 {five['M52_service']['cash_end']}",
    )

    # ---- 成交本身不计分：买下的那个月与"年初就持有"的同一台设备，便利分逐位相同
    held_state = s0.model_copy(
        update={
            "holdings": (
                Holding(
                    opportunity_id="k51.durable.convenience_pack",
                    quantity=1,
                    acquired_year=0,
                    remaining_months=60,
                ),
            )
        }
    )
    held = summarize(w.year(held_state, base_items(K51)))
    stories["purchase_vs_held"] = {"bought_month_1": one["device"], "held_from_start": held}
    check(
        checks,
        "成交本身不计分：买下那年与年初已持有那年的逐月便利分与总分逐位相同",
        held["s_convenience"] == one["device"]["s_convenience"] and held["u"] == one["device"]["u"],
        f"现金不同（{one['device']['cash_end']} vs {held['cash_end']}），分数相同",
    )

    # ---- 故事 6：期末估值（三年实际生活 → 评价）
    def three_years(year_items) -> list[LifeYearResult]:
        st, res = s0, []
        for y in (1, 2, 3):
            r = w.year(st, year_items(y))
            res.append(r)
            assert r.next_state is not None, (y, r.settlement.status, r.handoff)
            st = r.next_state
        return res

    lives = {
        "device_year1": three_years(
            lambda y: (
                [*base_items(K51)] + ([once("k51.durable.convenience_pack", 1)] if y == 1 else [])
            )
        ),
        "never": three_years(lambda y: base_items(K51)),
        "service_every_year": three_years(
            lambda y: [*base_items(K51), whole("k51.service.chores")]
        ),
    }
    six: dict[str, Any] = {}
    for name, res in lives.items():
        ev = evaluate_clone(
            spec=w.evaluation,
            protocol=w.protocol,
            catalog=w.catalog(K51),
            tax=w.tax(K51),
            scoring=w.inputs(K51),
            results=res,
        )
        tv = ev.valuation
        plans = {
            p.plan_id: {
                "status": p.status,
                "reason_kind": p.reason_kind,
                "v_plan": p.v_plan_display,
                "skipped_purchases": list(p.skipped_purchases),
                "cash_end": None if p.cash_end is None else str(p.cash_end.amount),
            }
            for p in (tv.plans if tv is not None else ())
        }
        six[name] = {
            "terminal_holdings": [
                (h.opportunity_id, h.effective_remaining_months)
                for h in res[-1].next_state.holdings
            ],
            "l_actual": ev.actual.l_actual_display if ev.actual is not None else None,
            "v_reference": tv.v_reference_display if tv is not None else None,
            "best_plan": tv.selected_plan_id if tv is not None else None,
            "plans": plans,
        }
    stories["6_terminal_valuation"] = six
    d = six["device_year1"]["plans"]
    n = six["never"]["plans"]
    check(
        checks,
        "故事 6：期末还在服务期的设备照用——较舒适＋设备的模板不再买（不重复付款、不重置寿命）",
        any("照用旧的" in t for t in d["p2_current_device"]["skipped_purchases"])
        and not n["p2_current_device"]["skipped_purchases"],
        f"已有设备：{d['p2_current_device']['skipped_purchases']}",
    )
    check(
        checks,
        "故事 6：基本生活模板里，已有设备的人照样有便利分（V 更高）",
        (d["p1_current_basic"]["v_plan"] or 0) > (n["p1_current_basic"]["v_plan"] or 0),
        f"已有设备 {d['p1_current_basic']['v_plan']} vs 没有 {n['p1_current_basic']['v_plan']}",
    )
    check(
        checks,
        "故事 6：没有设备的人，较舒适＋设备模板在估值期第 1 月买一套；人工服务模板按月付费",
        n["p2_current_device"]["status"] == "complete"
        and n["p3_current_service"]["status"] == "complete",
        f"{n['p2_current_device']['status']} / {n['p3_current_service']['status']}",
    )
    check(
        checks,
        "故事 6：需要会计资格的三个模板在没有资格时是已知不可行（不是无法评价）",
        all(
            n[p]["reason_kind"] == "no_qualification"
            for p in ("p4_qualified_basic", "p5_qualified_device", "p6_qualified_service")
        ),
        f"{[n[p]['reason_kind'] for p in ('p4_qualified_basic', 'p5_qualified_device', 'p6_qualified_service')]}",
    )

    # ---- 故事 7：只改便利权重、计划相同
    zero = w.profile.model_copy(update={"material_points": {"housing_space": 50, "fresh_food": 50}})
    seven = {
        "weight_40_device": one["device"],
        "weight_0_device": summarize(w.year(s0, plan1["device"], profile=zero)),
        "weight_0_none": summarize(w.year(s0, plan1["none"], profile=zero)),
    }
    stories["7_weight_only"] = seven
    u40 = month_u(s_c=s_convenience(Fraction(12)))
    u0 = month_u(
        s_c=Fraction(0), m_house=Fraction(1, 2), m_fresh=Fraction(1, 2), m_conv=Fraction(0)
    )
    check(
        checks,
        "故事 7：同样买设备，便利权重 40 与 0 的总分差可以手算拆开",
        abs(seven["weight_40_device"]["u"][0] - float(u40)) < 1e-12
        and abs(seven["weight_0_device"]["u"][0] - float(u0)) < 1e-12,
        f"权重 40：{float(u40)!r}；权重 0：{float(u0)!r}；差 {float(u40 - u0)!r}",
    )
    check(
        checks,
        "故事 7：权重为 0 时设备不加分，但本人家务照样从 22 小时降到 10 小时",
        seven["weight_0_device"]["u"] == seven["weight_0_none"]["u"]
        and set(seven["weight_0_device"]["household_hours"]) == {"10"}
        and set(seven["weight_0_none"]["household_hours"]) == {"22"},
        f"分数相同 {seven['weight_0_device']['u'][0]!r}；家务 10 vs 22 小时",
    )
    four0 = w.year(
        s0, [*base_items(K51), course, once("k51.durable.convenience_pack", 1)], profile=zero
    )
    check(
        checks,
        "故事 7：权重为 0 不改变可行性——带课程的计划用了设备照样可行",
        four0.settlement.status == "completed",
        f"状态 {four0.settlement.status}",
    )

    return {"checks": checks, "stories": stories}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out_dir: Path = args.out
    if out_dir.exists():
        print(f"{out_dir} 已存在：不覆盖旧结果", file=sys.stderr)
        return 1
    try:
        result = run(out_dir)
    except LifeContractError as exc:
        print(f"输入不合法：{exc}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO
    ).stdout.strip()
    meta = {
        "written_at_local": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "git_head": head,
        "inputs": {p: _sha(REPO / p) for p in KEY_SOURCES},
    }
    (out_dir / "stories.json").write_text(
        json.dumps({"meta": meta, **result}, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    failed = [c for c in result["checks"] if not c["ok"]]
    lines = [
        "# 日常生活便利：离线故事核对",
        "",
        f"{len(result['checks'])} 项核对，{len(failed)} 项不通过。",
        "",
    ]
    for c in result["checks"]:
        lines.append(f"- {'✅' if c['ok'] else '❌'} {c['check']}：{c['detail']}")
    (out_dir / "stories.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for c in result["checks"]:
        print(f"{'通过' if c['ok'] else '不通过'}  {c['check']}：{c['detail']}")
    print(f"共 {len(result['checks'])} 项核对，不通过 {len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
