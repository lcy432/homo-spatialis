"""E1-5C 收尾修订的离线演示（`DECISIONS/0027` 收尾修订，2026-10-11）。零大模型调用。

    uv run python scripts/demo_life_e1_5c_closeout.py --out runs/<新目录>

用 E1-5C 的合成生活输入与评分配置（不改它们），把独立审核发现的三个收尾项各跑一遍：

* F1：同一个病人不能被告知两种恢复日期——规则冲突时观察与结算给出同一个判断；共同初始状态照样可以用于不同规则的实验臂；
* F2：独立审核的反例（开局 4,000；第 3 月小病、第 4 月大病；第 3 月计划买 2,400 的洗烘一体机），没有声明与声明了
  "生病月份取消这次购买"两种计划，经新接口 ``cancel_purchases_when_ill`` 真实执行；另有健康月份照常买、资金不足与取消分开、
  关联资金目标的取消、分身可见的事实；
* F3：H(健康) 固定为 1——其他值在构造评分规格时就被拒绝；默认分数不变；
* 留给 E1-6 的反例：只看重交通的人，大病月的总分可能高于上班月（符合当前公式，不是算术错误，本轮不处理）。

年度计划是**预先写好的分身计划**，不是 LLM 的理解，也不是由医疗点数生成的。全部数字来自合成样例：不是医学结论、现实价格
或工资，也不排序城市；这些例子只证明声明的安排能产生预期后果，不证明取消购买在所有情景中都是最好的选择。
目录已存在就拒绝，不覆盖旧结果。退出码：0 = 全部核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    RESERVED_HEALTH_KEYS,
    IllnessEventSource,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    YearAction,
)
from common.life_feedback_contracts import LifeYearResult
from common.life_scoring_contracts import HealthStateValues
from econ.life_settlement import binding_from_policy, settle_year
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_5c_health.yaml")
SCORING = Path("configs/life/scoring_e1_5c_health.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/ledger/life_catalog.py",
    "src/econ/life_health.py",
    "src/econ/life_settlement.py",
    "src/econ/life_savings.py",
    "src/econ/life_scoring.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_5c_closeout.py",
    "tests/life/test_health_closeout.py",
)
DRYER = "a91.durable.washer_dryer"
HOME = {"opportunity_id": "a91.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a91.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}
SHOP = {"opportunity_id": "a91.job.shop_parttime", "quantity": 1, "start_month": 1, "end_month": 12}
OFFICE = {
    "opportunity_id": "a91.job.accountant_office",
    "quantity": 1,
    "start_month": 1,
    "end_month": 12,
}
TREAT = {"minor": "treat_if_possible", "major": "treat_if_possible"}
DECLINE = {"minor": "decline", "major": "decline"}
CANCEL = "cancel_purchases_when_ill"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, LIFE.as_posix(), SCORING.as_posix()]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _revise(model: Any, **changes: Any) -> Any:
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


def _cents(money: Any) -> int:
    return round(money.amount * 100)


def _yuan(cents: int | None) -> str:
    return "—" if cents is None else f"{cents / 100:,.2f}"


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


class World:
    def __init__(self, bundle: LifeBundle, scoring: ScoringConfig) -> None:
        self.b = bundle
        self.scoring = scoring

    def state(self, clone: str, cash: float | None = None) -> PersonYearState:
        s = self.b.initial_state(clone)
        assert s is not None
        return s if cash is None else _revise(s, cash=replace(s.cash, amount=cash))

    def profile(self, person: str) -> Any:
        return next(p for p in self.b.stated_profiles if p.person_id == person)

    def plan(
        self, clone: str, year: int, *items: dict[str, Any], emergency: dict[str, Any]
    ) -> YearAction:
        s = self.state(clone)
        return YearAction.model_validate(
            {
                "person_id": s.person_id,
                "clone_id": clone,
                "year_index": year,
                "items": list(items),
                "emergency": emergency,
            }
        )

    def source(self, *events: tuple[str, int, int, str]) -> IllnessEventSource:
        return IllnessEventSource.model_validate(
            {
                "mode": "injected",
                "stream_version": "e1-5c-illness-stream-v1",
                "note": "收尾演示的指定事件",
                "injected": [
                    {
                        "person_id": p,
                        "repeat_index": 0,
                        "year_index": y,
                        "month": m,
                        "severity": sev,
                        "note": "收尾演示",
                    }
                    for p, y, m, sev in events
                ],
            }
        )

    def settle(self, protocol: LifeProtocol, state: PersonYearState, action: YearAction) -> Any:
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        return settle_year(
            protocol=protocol,
            state=state,
            action=action,
            catalog=catalog,
            tax=binding_from_policy(self.b.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
        )

    def year(
        self,
        protocol: LifeProtocol,
        state: PersonYearState,
        action: YearAction,
        profile: Any = None,
    ) -> LifeYearResult:
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        prof = profile or self.profile(state.person_id)
        return run_life_year(
            protocol=protocol,
            state=state,
            action=action,
            catalog=catalog,
            tax=binding_from_policy(self.b.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
            scoring=ScoringInputs(
                spec=self.scoring.spec,
                conditions=self.scoring.conditions_for(str(state.city)),  # type: ignore[arg-type]
                preference_input=prof,
                hard_requirements=prof.hard_requirements,
            ),
        )

    def observe(
        self,
        protocol: LifeProtocol,
        state: PersonYearState,
        health_rules: Any,
        last_feedback: Any = None,
    ) -> Any:
        catalog = self.b.catalog_for(str(state.city))
        return build_observation(
            permission=standard_product_permission(protocol.info_permission_version),
            profile=self.profile(state.person_id),
            state=state,
            catalog=catalog,  # type: ignore[arg-type]
            last_feedback=last_feedback,
            resource_rules=protocol.resource_rules,
            energy_feedback=protocol.energy_feedback,
            career_rules=protocol.career_rules,
            savings_rules=protocol.savings_rules,
            health_rules=health_rules,
            health_input=protocol.health_input,
        )


def _months(settlement: Any) -> list[dict[str, Any]]:
    """逐月：月末现金、月初病情、治疗、洗烘一体机这一项在这个月的结果。"""
    out = settlement.outcome
    dryer = next((i for i in out.items if i.opportunity_id == DRYER), None)
    rows = []
    for m in out.months:
        h = out.health.month(m.month)
        what = None
        if dryer is not None and m.month in dryer.requested_months:
            if m.month in dryer.covered_months:
                what = "买下"
            elif any(p.month == m.month for p in dryer.months_paused):
                what = "按声明因病取消"
            elif any(r.month == m.month for r in dryer.rejections):
                what = "被拒：" + next(r.reason for r in dryer.rejections if r.month == m.month)
            else:
                what = "没有执行"
        rows.append(
            {
                "month": m.month,
                "cash_end": _cents(m.cash_end),
                "state": None if h is None else h.state,
                "treatment": None if h is None or h.treatment is None else h.treatment.status,
                "dryer": what,
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-5C 收尾修订的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, _ = load_life_bundle(LIFE, require_settlement_ready=True)
        scoring, _ = load_scoring_config(SCORING, bundle)
    except LifeContractError as exc:
        print(exc)
        return 1
    w = World(bundle, scoring)
    proto = bundle.protocol
    rules = proto.health_rules
    assert rules is not None
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    # ================================================================ F1
    y1_97 = w.year(proto, w.state("u97.a91.crossyear"), bundle.plans_for("u97.a91.crossyear")[0])
    s2 = y1_97.next_state
    assert s2 is not None
    longer = _revise(rules, major=_revise(rules.major, untreated_months=5))
    ok_obs = w.observe(proto, s2, rules)
    settle_longer = w.settle(
        _revise(proto, health_rules=longer), s2, bundle.plans_for("u97.a91.crossyear")[1]
    )
    try:
        w.observe(proto, s2, longer)
        refused = None
    except LifeContractError as exc:
        refused = str(exc)
    problem = next((p for p in settle_longer.problems if p.code == "health_rules_changed"), None)
    f1 = {
        "state": "u97 第 1 年第 12 月得大病不治，带着病了 1 个月的大病进入第 2 年",
        "original_rules": {
            "major_untreated_months": rules.major.untreated_months,
            "natural_end_shown": ok_obs.health.ongoing.natural_end,
        },
        "changed_rules": {
            "major_untreated_months": longer.major.untreated_months,
            "same_version": longer.version == rules.version,
            "settlement": settle_longer.status,
            "settlement_problem": None if problem is None else [problem.code, problem.message],
            "observation_refused_with": refused,
        },
    }
    initial = w.state("u91.a91.treat")
    arms = []
    for arm in (rules, longer):
        obs = w.observe(proto, initial, arm)
        r = w.settle(
            _revise(proto, health_rules=arm), initial, bundle.plans_for("u91.a91.treat")[0]
        )
        arms.append(
            {
                "major_untreated_months": arm.major.untreated_months,
                "observation_months_if_untreated": obs.health.severity_rules[1].months_if_untreated,
                "settlement": r.status,
            }
        )
    f1["initial_state_arms"] = arms
    check(
        "F1 同一个病人不会被告知两种恢复日期",
        ok_obs.health.ongoing.natural_end.startswith("不治疗的话，第 2 年第 2 月月末恢复")
        and settle_longer.status == "input_error"
        and problem is not None
        and refused is not None
        and f"[health_rules_changed] {problem.message}" in refused
        and "第 2 年第 4 月" not in refused,
        "原规则（大病不治 3 个月）：观察写第 2 年第 2 月月末恢复；只把病程改成 5 个月（编号、版本不变）：结算报 health_rules_changed，"
        "观察构造用同一份判断、同一句说明拒绝，不再先写出“第 4 月月末恢复”",
    )
    check(
        "F1 共同初始状态照样能用于不同规则的实验臂",
        [a["settlement"] for a in arms] == ["completed", "completed"]
        and [a["observation_months_if_untreated"] for a in arms] == [3, 5],
        "第 1 年的共同初始状态没有绑定：病程 3 个月与 5 个月两臂都能构造观察、都能结算",
    )

    # ================================================================ F2
    events = (("u91", 1, 3, "minor"), ("u91", 1, 4, "major"))
    proto_f2 = _revise(proto, illness_events=w.source(*events))
    start = w.state("u91.a91.treat", cash=4000)
    clone = "u91.a91.treat"
    buy3 = {"opportunity_id": DRYER, "quantity": 1, "month": 3}
    plain = w.plan(clone, 1, HOME, FOOD, SHOP, buy3, emergency=dict(TREAT))
    declared = w.plan(clone, 1, HOME, FOOD, SHOP, buy3, emergency={**TREAT, CANCEL: [DRYER]})
    ra = w.year(proto_f2, start, plain)
    rb = w.year(proto_f2, start, declared)
    out_b = rb.settlement.outcome
    dryer_b = next(i for i in out_b.items if i.opportunity_id == DRYER)
    rows_a, rows_b = _months(ra.settlement), _months(rb.settlement)
    f2 = {
        "setup": "u91 开局 4,000；每月工资 1,500，住房加食品 1,380；第 3 月小病（门诊 300），第 4 月大病（住院 2,000）；"
        "第 3 月计划买 2,400 的洗烘一体机；小病、大病都选条件允许就治",
        "without_declaration": {
            "settlement": ra.settlement.status,
            "exit_month": ra.settlement.outcome.exit.month if ra.settlement.outcome.exit else None,
            "exit_shortfall": _cents(ra.settlement.outcome.exit.shortfall)
            if ra.settlement.outcome.exit
            else None,
            "months": rows_a,
        },
        "with_declaration": {
            "settlement": rb.settlement.status,
            "handoff": rb.handoff,
            "dryer_status": dryer_b.status,
            "dryer_reason": dryer_b.months_paused[0].reason if dryer_b.months_paused else None,
            "dryer_paid": _cents(dryer_b.amount_paid),
            "months": rows_b,
            "annual_score": rb.score.annual_mean if rb.score else None,
            "monthly_health_deduction": [m.health.deduction for m in rb.score.months]
            if rb.score
            else None,
        },
    }
    check(
        "F2 没有声明：第 3 月照买，第 4 月住不起院，第 5 月出局",
        ra.settlement.status == "exited"
        and f2["without_declaration"]["exit_month"] == 5
        and [r["cash_end"] for r in rows_a] == [412000, 424000, 166000, 28000, 28000]
        and rows_a[3]["treatment"] == "unaffordable"
        and rows_a[2]["dryer"] == "买下"
        and f2["without_declaration"]["exit_shortfall"] == 110000,
        "第 3 月 4,240 + 1,500 − 1,380 − 300 − 2,400 = 1,660；第 4 月没有工资：1,660 − 1,380 = 280 < 2,000，住不起院、"
        "继续病；第 5 月 280 < 1,380，出局（缺 1,100）",
    )
    check(
        "F2 有声明：执行器因病取消第 3 月的购买，第 4 月住院，全年过完",
        rb.settlement.status == "completed"
        and [r["cash_end"] for r in rows_b[:4]] == [412000, 424000, 406000, 68000]
        and rows_b[-1]["cash_end"] == 164000
        and rows_b[2]["dryer"] == "按声明因病取消"
        and [r["treatment"] for r in rows_b[2:4]] == ["treated", "treated"]
        and dryer_b.status == "cancelled_when_ill"
        and _cents(dryer_b.amount_paid) == 0
        and not any(f.opportunity_id == DRYER for f in out_b.cash_flows),
        "购买仍在计划里，由真实执行器因患病取消：4,240 + 1,500 − 1,380 − 300 = 4,060；第 4 月 4,060 − 1,380 − 2,000 = 680，"
        "住院、月末恢复；之后每月净存 120，年末 680 + 8 × 120 = 1,640",
    )

    # 健康月份照常买；资金不足与取消分开
    buy6 = {"opportunity_id": DRYER, "quantity": 1, "month": 6}
    poor6 = w.settle(
        proto_f2,
        start,
        w.plan(clone, 1, HOME, FOOD, SHOP, buy6, emergency={**TREAT, CANCEL: [DRYER]}),
    )
    rich6 = w.settle(
        proto_f2,
        w.state(clone, cash=10000),
        w.plan(clone, 1, HOME, FOOD, SHOP, buy6, emergency={**TREAT, CANCEL: [DRYER]}),
    )
    poor6_item = next(i for i in poor6.outcome.items if i.opportunity_id == DRYER)
    rich6_item = next(i for i in rich6.outcome.items if i.opportunity_id == DRYER)
    f2["healthy_month_purchase"] = {
        "start_4000": {
            "status": poor6_item.status,
            "reason": poor6_item.rejections[0].reason if poor6_item.rejections else None,
        },
        "start_10000": {"status": rich6_item.status, "covered": list(rich6_item.covered_months)},
    }
    check(
        "F2 健康月份照常买；资金不足与按声明取消分开记",
        poor6_item.status == "rejected"
        and "资金不足" in poor6_item.rejections[0].reason
        and not poor6_item.months_paused
        and rich6_item.status == "executed"
        and rich6_item.covered_months == (6,),
        "同样声明，购买放在第 6 月（已经康复）：开局 4,000 时第 6 月只有 920，记“资金不足”被拒；开局 10,000 时照常买下。"
        "前几个月的病不会把之后健康月份的购买一起取消",
    )

    # 关联资金目标的购买被取消
    a2 = w.plan(
        "u97.a91.crossyear",
        2,
        HOME,
        FOOD,
        SHOP,
        {"opportunity_id": DRYER, "quantity": 1, "month": 2, "goal_id": "dryer"},
        emergency={**DECLINE, CANCEL: [DRYER]},
    )
    r97 = w.settle(proto, s2, a2)
    sv = r97.outcome.savings
    due = [e for e in sv.events if e.kind == "due_unrealized"]
    goal97 = next(g for g in r97.next_state.savings_goals if g.goal_id == "dryer")
    f2["linked_goal"] = {
        "setup": "u97 的资金目标：第 2 年第 6 月买洗烘一体机。关联的购买放在第 2 月（仍在第 1 年年末那场大病里）并声明生病就取消",
        "dryer_status": next(i for i in r97.outcome.items if i.opportunity_id == DRYER).status,
        "due_unrealized": [(e.month, e.note) for e in due],
        "goal_status": goal97.status,
        "life_status": r97.next_state.status,
        "cash_end_m2": _cents(r97.outcome.months[1].cash_end),
        "cash_end_m6": _cents(r97.outcome.months[5].cash_end),
    }
    check(
        "F2 关联目标的购买被取消：目标不兑现，到期记未兑现，不出局",
        f2["linked_goal"]["dryer_status"] == "cancelled_when_ill"
        and [m for m, _ in f2["linked_goal"]["due_unrealized"]] == [6]
        and "关联的购买已取消" in due[0].note
        and goal97.status == "overdue"
        and r97.next_state.status == "active"
        and f2["linked_goal"]["cash_end_m6"] == 166000,
        "取消不兑现目标；第 6 月月末记“到期未兑现：关联的购买已取消”（不写成被拒）；同一本现金账：第 6 月末 1,180 + 4 × 120 = 1,660",
    )

    # 分身看得到自己的声明与已经发生的取消
    fb = visible_feedback(rb.settlement)
    obs_b = w.observe(proto_f2, rb.next_state, rules, last_feedback=fb)
    payload_b = observation_payload(obs_b)
    ex = next(x for x in fb.executed if x.opportunity_id == DRYER)
    f2["visible"] = {
        "declared_purchase_cancellations": list(fb.health.declared_purchase_cancellations),
        "month3_paused": [(v.opportunity_id, v.why) for v in fb.health.months[2].paused],
        "executed_item": {
            "status": ex.status,
            "reason": ex.reason,
            "months_paused": ex.months_paused,
        },
        "rule_text": next(line for line in payload_b["health"]["rules"] if CANCEL in line),
    }
    blob = json.dumps(payload_b, ensure_ascii=False)
    check(
        "F2 分身看得到自己的声明与已经发生的取消，看不到风险映射",
        f2["visible"]["declared_purchase_cancellations"] == [DRYER]
        and ex.status == "cancelled_when_ill"
        and not (_keys(payload_b) & RESERVED_HEALTH_KEYS)
        and rules.content_fingerprint not in blob,
        "上一年的反馈里有“你声明过生病时取消洗烘一体机的购买”与“第 3 月因患病取消”；没有概率表、风险档、随机数或规则指纹",
    )

    # ================================================================ F3
    try:
        HealthStateValues(healthy=0.8, minor=0.5, major=0.0)
        anchor_refusal = None
    except ValidationError as exc:
        anchor_refusal = exc.errors()[0]["msg"]
    deductions = f2["with_declaration"]["monthly_health_deduction"]
    f3 = {"healthy_0_8_refused_with": anchor_refusal, "default_deductions_u91": deductions}
    check(
        "F3 H(健康) 固定为 1：0.8 在构造评分规格时就被拒绝",
        anchor_refusal is not None and "H(健康) 固定为 1" in anchor_refusal,
        "1 是健康项的标尺（没有疾病造成的扣减），不是整体生活满分；要改变健康项的分量请改 γ 或小病、大病的 H",
    )
    check(
        "F3 默认分数不变：健康扣 0、小病扣 0.1、大病扣 0.2",
        deductions is not None and deductions[:5] == [0.0, 0.0, 0.1, 0.2, 0.0],
        "γ = 0.2：小病 0.2 × (1 − 0.5) = 0.1，大病 0.2 × (1 − 0) = 0.2，健康 0.2 × (1 − 1) = 0",
    )

    # ================================================================ 留给 E1-6 的反例
    p98 = w.profile("u98")
    points = dict.fromkeys(p98.dimension_points, 0)
    points.update(healthcare=10, commute=90)
    commute_only = _revise(p98, dimension_points=points, material_points=None)
    proto_c = _revise(proto, illness_events=w.source(("u98", 1, 5, "major")))
    rc = w.year(
        proto_c,
        w.state("u98.a91.stop"),
        w.plan("u98.a91.stop", 1, HOME, FOOD, OFFICE, emergency=dict(TREAT)),
        profile=commute_only,
    )
    work_m, sick_m = rc.score.months[3], rc.score.months[4]
    e16 = {
        "profile_points": points,
        "working_month": {"S": work_m.u, "B": work_m.daily_life},
        "major_illness_month": {
            "S": sick_m.u,
            "B": sick_m.daily_life,
            "deduction": sick_m.health.deduction,
        },
        "status": "已登记，留给 E1-6；不为消除它加疾病惩罚、改通勤公式或裁剪分数",
    }
    check(
        "E1-6 反例照旧可复现：只看重交通的人，大病月总分高于上班月",
        sick_m.u > work_m.u
        and abs(work_m.u - 0.5692307692307692) < 1e-15
        and abs(sick_m.u - 0.6193548387096774) < 1e-15
        and sick_m.health.deduction == 0.2,
        "七维点数只给交通 90、医疗 10：上班月 S = 0.5692，大病月 S = 0.6194（不用到岗，交通分高出的部分超过共同扣分 0.2）。"
        "符合当前公式、不是算术错误；它说明的是单月分数的含义，留给 E1-6 结合完整轨迹、现金、期末健康与参数敏感性讨论",
    )

    # ================================================================ 写出
    args.out.mkdir(parents=True)
    (args.out / "configs").mkdir()
    for src in (LIFE, SCORING):
        shutil.copyfile(REPO / src, args.out / "configs" / src.name)
    arms_in = {
        "f2_events": [list(e) for e in events],
        "f2_start_cash": 4000,
        "f2_plan_without_declaration": plain.model_dump(mode="json"),
        "f2_plan_with_declaration": declared.model_dump(mode="json"),
        "f1_changed_rules": {
            "major_untreated_months": 5,
            "content_fingerprint": longer.content_fingerprint,
        },
        "e16_profile_points": points,
        "e16_events": [["u98", 1, 5, "major"]],
    }

    def dump(name: str, data: Any) -> None:
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    dump("configs/arms.json", arms_in)
    dump("f1_observation_rules.json", f1)
    dump("f2_purchase_cancellation.json", f2)
    dump("f3_health_anchor.json", f3)
    dump("e1_6_commute_health_counterexample.json", e16)
    (args.out / "observations").mkdir()
    dump("observations/u91_year2_payload.json", payload_b)
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "reproduce": "uv run python scripts/demo_life_e1_5c_closeout.py --out <新目录>",
        },
    )
    dump("environment.json", _environment())
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])
    lines = ["# 收尾演示的核对", ""]
    lines += [f"- {'通过' if ok else '不通过'}　{n}：{d}" for n, ok, d in checks]
    (args.out / "checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def table(rows: list[dict[str, Any]]) -> list[str]:
        out = ["| 月 | 月初病情 | 治疗 | 洗烘一体机 | 月末现金 |", "|---|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| {r['month']} | {r['state']} | {r['treatment'] or '—'} | {r['dryer'] or '—'} | {_yuan(r['cash_end'])} |"
            )
        return out

    md = [
        "# E1-5C 收尾修订：三个例子（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；全部数字来自合成样例 `configs/life/synthetic_e1_5c_health.yaml`，**不是医学结论、"
        "现实价格或工资，也不是城市排行**。年度计划是**分身的预设计划**，不是 LLM 的理解，也不是由医疗点数生成的。",
        "",
        "## F1：为什么不能告诉同一个病人两种恢复日期",
        "",
        f"* {f1['state']}。原规则大病不治 {rules.major.untreated_months} 个月，观察写：{f1['original_rules']['natural_end_shown']}。",
        f"* 只把病程改成 {longer.major.untreated_months} 个月（编号、版本不变）：结算 {settle_longer.status}"
        f"（{problem.code if problem else '—'}）；观察构造用同一份判断拒绝：",
        f"  > {refused}",
        "* 第 1 年的共同初始状态没有绑定，两种规则的实验臂都能从它开始："
        + "；".join(
            f"病程 {a['major_untreated_months']} 个月 → 结算 {a['settlement']}" for a in arms
        ),
        "",
        "## F2：生病时取消洗烘一体机的购买，保住住院的钱",
        "",
        f"{f2['setup']}。",
        "",
        "**没有声明**（第 3 月照买）：",
        "",
        *table(rows_a),
        "",
        f"第 5 月出局（缺 {_yuan(f2['without_declaration']['exit_shortfall'])}）。",
        "",
        "**声明了 `cancel_purchases_when_ill: [a91.durable.washer_dryer]`**（购买仍在计划里，由执行器因患病取消）：",
        "",
        *table(rows_b),
        "",
        f"洗烘一体机：{dryer_b.status}，付款 {_yuan(_cents(dryer_b.amount_paid))}；原因：{f2['with_declaration']['dryer_reason']}。",
        "",
        f"* 健康月份照常买：同样声明、购买放在第 6 月——开局 4,000 时记“{f2['healthy_month_purchase']['start_4000']['reason']}”"
        f"（被拒，不是取消）；开局 10,000 时照常买下。",
        f"* 关联资金目标：{f2['linked_goal']['setup']}。结果：{f2['linked_goal']['due_unrealized'][0][1] if due else '—'}",
        f"* 分身下一年看得到：声明 {f2['visible']['declared_purchase_cancellations']}；第 3 月 {f2['visible']['month3_paused']}；"
        f"规则说明：{f2['visible']['rule_text']}",
        "",
        "这只证明声明的应急安排能产生预期后果，**不证明**取消购买在所有情景中都是最好的选择。",
        "",
        "## F3：为什么健康项的 1 是评分基准",
        "",
        "S = (1 − γ) × B + γ × H。H(健康) 固定为 1：它是健康项的标尺，表示“没有疾病造成的扣减”，不表示这个人的全部生活已经满分。"
        "所以相对健康的直接扣分就是 γ × (1 − H)：小病 0.2 × 0.5 = 0.1，大病 0.2 × 1 = 0.2，健康 0。",
        "",
        f"* H(健康) = 0.8 在构造评分规格时就被拒绝：{anchor_refusal}",
        f"* 默认配置（γ = 0.2，H = 1 / 0.5 / 0）的分数与额度不变；上面 F2 的有声明一例，前 5 个月的直接扣分：{deductions[:5] if deductions else None}",
        "",
        "## 留给 E1-6：只看重交通的人，大病月的分数可能更高",
        "",
        f"七维点数只给交通 90、医疗 10（其余 0），第 5 月大病：上班月 S = {work_m.u:.4f}（B = {work_m.daily_life:.4f}），"
        f"大病月 S = {sick_m.u:.4f}（B = {sick_m.daily_life:.4f}，共同扣分 {sick_m.health.deduction}）。不用到岗，交通分高出的部分超过了"
        "共同的健康扣分。这符合当前公式，不是算术错误；本轮不为它加疾病惩罚、改通勤公式或裁剪分数，也不宣称加入期末现金后必然消失。"
        "E1-6 要结合完整轨迹、现金与物品、期末健康以及参数敏感性讨论它的含义。",
        "",
        "## 手算核对",
        "",
        *[f"- {'通过' if ok else '不通过'}　{n}：{d}" for n, ok, d in checks],
    ]
    (args.out / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    for n, ok, d in checks:
        print(f"- {'通过' if ok else '不通过'}　{n}：{d}")
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
