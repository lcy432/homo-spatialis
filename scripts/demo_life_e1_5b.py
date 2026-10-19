"""E1-5B：目标储蓄的离线演示（`DECISIONS/0026`）。零大模型调用。

    uv run python scripts/demo_life_e1_5b.py --out runs/<新目录>

读 E1-5B 的合成生活输入与评分配置，按预先声明的年度计划逐年做**真实的**年度交接（结算 → 评分 → 反馈），输出三个
可以手算的故事：

* 故事一（u81）：第 1 年攒钱，第 2 年实际买下洗烘一体机；
* 故事二（u82）：攒会计课学费并付清，但课程还要继续上，学会要等到第 3 年；
* 故事三（u83）：一笔计划内的额外开支（探亲往返——**不是**疾病或随机事件）推迟了目标：第 2 年到期未兑现、不出局，
  第 3 年改时点后买下。

另有：同一份生活事实只改目标（没有目标、只改说明文字、只改目标时点、换成学费目标）——现金流、逐月分数与下一年额度
完全相同；第 3 年年初的观察样例（自己的目标、余额、报价、差距与未兑现原因）；手算核对。

区分六件事：钱还在（余额）、钱够报价（只是比较）、付了学费 / 买了东西（兑现）、学会了（完成课程）、获得服务（物品
服务月）、产生满足感（评分器按原规则）。全部数字来自合成样例：不是现实价格、工资或储蓄行为；不排序城市。

目录已存在就拒绝，不覆盖旧结果。退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    LifeContractError,
    PersonYearState,
    YearAction,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_5b_savings.yaml")
SCORING = Path("configs/life/scoring_e1_5b_savings.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_billing.py",
    "src/common/life_scoring_contracts.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_settlement.py",
    "src/econ/life_savings.py",
    "src/econ/life_resources.py",
    "src/econ/life_career.py",
    "src/econ/life_scoring.py",
    "src/econ/life_feedback.py",
    "src/econ/life_year.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_5b.py",
    "tests/life/test_savings_goals.py",
)
STORIES = {
    "u81.a81.save": "故事一：第 1 年攒钱，第 2 年实际买下洗烘一体机（u81）",
    "u82.a81.course": "故事二：攒会计课学费并付清，但课程还要继续上（u82）",
    "u83.a81.delay": "故事三：一笔额外开支推迟了目标，分身照常生活（u83）",
}
DRYER = "a81.durable.washer_dryer"
COURSE = "a81.training.accounting_course"
VISIT = "a81.experience.family_visit"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment(revision: str) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, str(LIFE), str(SCORING)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "feedback_handoff_revision": revision,
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _m(x: Any) -> float | None:
    """金额 → 浮点（来自整数分，可以直接比较）。"""
    return None if x is None else x.amount


def _fmt(x: Any) -> str:
    return "—" if x is None else f"{Decimal(repr(x)):,.2f}"


class World:
    """一份生活输入 + 一份评分配置：按年做真实的年度交接。"""

    def __init__(self, bundle: LifeBundle, scoring: ScoringConfig) -> None:
        self.b = bundle
        self.scoring = scoring

    def profile(self, person: str) -> Any:
        return next(p for p in self.b.stated_profiles if p.person_id == person)

    def year(self, state: PersonYearState, plan: YearAction) -> LifeYearResult:
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        profile = self.profile(state.person_id)
        return run_life_year(
            protocol=self.b.protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=binding_from_policy(self.b.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
            scoring=ScoringInputs(
                spec=self.scoring.spec,
                conditions=self.scoring.conditions_for(str(state.city)),
                preference_input=profile,
                hard_requirements=profile.hard_requirements,
            ),
        )

    def run(self, clone: str) -> tuple[list[PersonYearState], list[LifeYearResult]]:
        state = self.b.initial_state(clone)
        assert state is not None
        starts, results = [state], []
        for plan in self.b.plans_for(clone):
            r = self.year(state, plan)
            results.append(r)
            if r.next_state is None or r.handoff != "next_year_ready":
                break
            state = r.next_state
            starts.append(state)
        return starts, results


def _year_row(start: PersonYearState, r: LifeYearResult) -> dict[str, Any]:
    out = r.settlement.outcome
    assert out is not None
    income = sum(Decimal(repr(f.amount.amount)) for f in out.cash_flows if f.kind == "income_gross")
    spent = -sum(Decimal(repr(f.amount.amount)) for f in out.cash_flows if f.kind == "expense")
    sv = out.savings
    assert sv is not None
    key_items = [
        {
            "opportunity_id": i.opportunity_id,
            "plan_order": i.plan_order,
            "status": i.status,
            "covered_months": list(i.covered_months),
            "amount_paid": i.amount_paid.amount,
            "rejections": [x.reason for x in i.rejections],
        }
        for i in out.items
        if i.opportunity_id in (DRYER, COURSE, VISIT)
    ]
    learning = [
        {
            "course": x.opportunity_id,
            "fee_charged": x.fee_charged.amount,
            "months_attended": list(x.months_attended),
            "progress": f"{x.progress_before}→{x.progress_after}/{x.months_required}",
            "completed_month": x.completed_month,
            "granted": list(x.granted),
            "usable_from_year": x.usable_from_year,
        }
        for x in (out.career.learning if out.career is not None else ())
    ]
    nxt = r.settlement.next_state
    goals = [] if nxt is None else [g.model_dump(mode="json") for g in nxt.savings_goals or ()]
    return {
        "year": r.year_index,
        "settlement": r.settlement.status,
        "handoff": r.handoff,
        "cash_start": _m(out.cash_start),
        "income": float(income),
        "spending": float(spent),
        "cash_end": _m(out.cash_end),
        "directive": sv.directive,
        "directive_goal_id": sv.directive_goal_id,
        "linked_plan_order": sv.linked_plan_order,
        "events": [e.model_dump(mode="json") for e in sv.events],
        "progress": [
            {
                "month": g.month,
                "cash": _m(g.cash),
                "quote": _m(g.quote),
                "shortfall": _m(g.shortfall),
                "cash_covers_quote": g.cash_covers_quote,
                "status": g.status,
            }
            for g in sv.progress
        ],
        "realization": None if sv.realization is None else sv.realization.model_dump(mode="json"),
        "notes": list(sv.notes),
        "key_items": key_items,
        "learning": learning,
        "S": r.score.annual_mean if r.score is not None else None,
        "score_status": r.score.annual_status if r.score is not None else r.score_problem,
        "quota_this_year": start.energy.level,
        # 只有还有下一行动年才有"下一年额度"；终态与出局没有
        "next_quota": r.next_state.energy.level if r.handoff == "next_year_ready" else None,
        "goals_after_year": goals,
    }


def _progress_table(row: dict[str, Any]) -> list[str]:
    lines = [
        "| 时点 | 余额（钱还在） | 当前报价 | 还差多少 | 余额不少于报价？（只是比较） | 目标状态 |",
        "|---|---|---|---|---|---|",
    ]
    for p in row["progress"]:
        when = "年初" if p["month"] is None else f"第 {p['month']} 月末"
        cover = (
            "—" if p["cash_covers_quote"] is None else ("是" if p["cash_covers_quote"] else "否")
        )
        lines.append(
            f"| {when} | {_fmt(p['cash'])} | {_fmt(p['quote'])} | {_fmt(p['shortfall'])} | {cover} | {p['status']} |"
        )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-5B：目标储蓄的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, life_report = load_life_bundle(LIFE, require_settlement_ready=True)
        scoring, score_report = load_scoring_config(SCORING, bundle)
    except LifeContractError as exc:
        print(exc)
        return 1
    world = World(bundle, scoring)
    policy = bundle.protocol.energy_feedback
    rules = bundle.protocol.savings_rules
    assert policy is not None and rules is not None

    stories: dict[str, list[dict[str, Any]]] = {}
    raw: dict[str, tuple[list[PersonYearState], list[LifeYearResult]]] = {}
    for clone in STORIES:
        starts, results = world.run(clone)
        raw[clone] = (starts, results)
        stories[clone] = [_year_row(s, r) for s, r in zip(starts, results, strict=False)]

    # ---------------------------------------------------------------- 同一份生活事实只改目标
    base = bundle.plans_for("u81.a81.save")[0]
    start81 = bundle.initial_state("u81.a81.save")
    assert start81 is not None

    def variant(goal: dict[str, Any] | None) -> YearAction:
        data = base.model_dump(mode="json")
        data["savings_goal"] = goal
        return YearAction.model_validate(data)

    dryer_goal = base.savings_goal.model_dump(mode="json")  # type: ignore[union-attr]
    variants = {
        "原计划（设目标 dryer，第 2 年第 6 月）": base,
        "没有任何目标": variant(None),
        "只改说明文字": variant({**dryer_goal, "note": "只改说明文字，其余一样"}),
        "只改目标时点（第 3 年第 12 月）": variant(
            {**dryer_goal, "target_year": 3, "target_month": 12}
        ),
        "换成学费目标": variant(
            {
                "action": "set",
                "goal_id": "tuition",
                "opportunity_id": COURSE,
                "quantity": 1,
                "target_year": 2,
                "target_month": 7,
            }
        ),
    }
    vres = {k: world.year(start81, v) for k, v in variants.items()}

    def money(r: LifeYearResult) -> str:
        out = r.settlement.outcome
        assert out is not None
        return out.model_dump_json(include={"cash_flows", "months", "items", "holdings_service"})

    ref = vres["原计划（设目标 dryer，第 2 年第 6 月）"]

    def goal_records(r: LifeYearResult) -> str:
        """这一年的目标记录与带进下一年的目标（说明文字记在后者里）。"""
        out, nxt = r.settlement.outcome, r.next_state
        assert out is not None and out.savings is not None and nxt is not None
        goals = [g.model_dump(mode="json") for g in nxt.savings_goals or ()]
        return json.dumps([out.savings.model_dump(mode="json"), goals], ensure_ascii=False)

    same_facts = {
        k: {
            "cash_end": _m(r.settlement.outcome.cash_end),  # type: ignore[union-attr]
            "money_and_execution_identical": money(r) == money(ref),
            "monthly_u_identical": [m.u for m in r.score.months]  # type: ignore[union-attr]
            == [m.u for m in ref.score.months],  # type: ignore[union-attr]
            "S": r.score.annual_mean,  # type: ignore[union-attr]
            "next_quota": r.next_state.energy.level,  # type: ignore[union-attr]
            "goal_events": [
                e.kind
                for e in r.settlement.outcome.savings.events  # type: ignore[union-attr]
            ],
            "goal_records_differ_from_original": goal_records(r) != goal_records(ref),
        }
        for k, r in vres.items()
    }

    # ---------------------------------------------------------------- 第 3 年年初的观察（u83）
    starts83, results83 = raw["u83.a81.delay"]
    state3 = starts83[2]
    catalog = bundle.catalog_for(str(state3.city))
    assert catalog is not None
    fb = visible_feedback(results83[1].settlement)
    obs = build_observation(
        permission=standard_product_permission(bundle.protocol.info_permission_version),
        profile=world.profile("u83"),
        state=state3,
        catalog=catalog,
        last_feedback=fb,
        resource_rules=bundle.protocol.resource_rules,
        energy_feedback=policy,
        career_rules=bundle.protocol.career_rules,
        savings_rules=rules,
    )
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)

    # ---------------------------------------------------------------- 手算核对
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    s1, s2, s3 = (stories[c] for c in STORIES)
    p11, p12 = s1[0]["progress"], s1[1]["progress"]
    check(
        "故事一·攒钱不动钱",
        s1[0]["cash_start"] == 500
        and s1[0]["cash_end"] == 1940
        and p11[-1]["shortfall"] == 460
        and not [i for i in s1[0]["key_items"] if i["opportunity_id"] == DRYER],
        "第 1 年 500 + 12 × 120 = 1,940；洗烘一体机 2,400，年末还差 460；建立目标没有任何现金流",
    )
    check(
        "故事一·钱够了不等于买了",
        p12[4]["month"] == 4
        and p12[4]["cash"] == 2420
        and p12[4]["cash_covers_quote"] is True
        and p12[4]["status"] == "active",
        "第 2 年第 4 月末 1,940 + 4 × 120 = 2,420 ≥ 2,400：余额不少于报价，但没有买，目标仍是进行中",
    )
    dryer2 = next(i for i in s1[1]["key_items"] if i["opportunity_id"] == DRYER)
    r12 = s1[1]["realization"]
    out12 = raw["u81.a81.save"][1][1].settlement.outcome
    assert out12 is not None
    service = [h for h in out12.holdings_service if h.opportunity_id == DRYER]
    check(
        "故事一·买一次、扣一次、一份服务",
        dryer2["amount_paid"] == 2400
        and dryer2["covered_months"] == [5]
        and r12 is not None
        and (r12["month"], r12["evidence"], r12["on_time"])
        == (5, "durable_purchase_executed", True)
        and p12[5]["cash"] == 140
        and s1[1]["cash_end"] == 980
        and len(service) == 1
        and service[0].covered_months == tuple(range(5, 13)),
        "第 5 月 2,420 + 1,500 − 1,380 − 2,400 = 140，兑现（按时）；年末 140 + 7 × 120 = 980；洗烘一体机第 5–12 月"
        "提供服务（一份）",
    )
    r22 = s2[1]["realization"]
    learn2, learn3 = s2[1]["learning"][0], s2[2]["learning"][0]
    check(
        "故事二·付了学费不等于学会",
        s2[0]["cash_end"] == 1740
        and [p["month"] for p in s2[0]["progress"] if p["cash_covers_quote"]] == [8, 9, 10, 11, 12]
        and r22 is not None
        and (r22["month"], r22["evidence"]) == (7, "tuition_paid_in_full")
        and s2[1]["cash_end"] == 1980
        and learn2["progress"] == "0→6/12"
        and learn2["granted"] == []
        and learn3["fee_charged"] == 0
        and (learn3["completed_month"], learn3["usable_from_year"]) == (6, 4)
        and s2[2]["cash_end"] == 3420,
        "第 1 年 300 → 1,740（第 8 月末 1,260 起余额不少于学费 1,200，但没付）；第 2 年第 7 月付清学费，兑现，当年参加 6/12、"
        "没有能力；第 3 年不再收学费，第 6 月学完，能力从第 4 年（终态）起才可用",
    )
    events3 = s3[1]["events"]
    check(
        "故事三·额外开支让目标推迟，不出局",
        s3[1]["progress"][2]["cash"] == 1180
        and s3[1]["progress"][6]["cash"] == 1660
        and s3[1]["progress"][6]["status"] == "overdue"
        and [e["kind"] for e in events3] == ["due_unrealized"]
        and "资金不足" in events3[0]["note"]
        and s3[1]["cash_end"] == 2380
        and s3[1]["progress"][-1]["shortfall"] == 20
        and s3[1]["handoff"] == "next_year_ready",
        "第 2 年第 2 月探亲 1,500：2,560 + 1,500 − 1,380 − 1,500 = 1,180；第 6 月 1,660 < 2,400，购买因资金不足被拒、"
        "月末到期未兑现；年末 2,380（差 20）；生活照常进入第 3 年",
    )
    goals3 = s3[2]["goals_after_year"][0]
    check(
        "故事三·改时点后买下，历史保留",
        [e["kind"] for e in s3[2]["events"]] == ["rescheduled", "realized"]
        and s3[2]["realization"]["month"] == 3
        and s3[2]["realization"]["on_time"]
        and s3[2]["progress"][3]["cash"] == 340
        and s3[2]["cash_end"] == 1420
        and [e["kind"] for e in goals3["events"]]
        == ["created", "due_unrealized", "rescheduled", "realized"],
        "第 3 年改时点到第 3 月：2,620 + 1,500 − 1,380 − 2,400 = 340，兑现；年末 1,420；目标历史 = 建立、到期未兑现、"
        "改时点、兑现",
    )
    check(
        "只改目标：钱、分数、额度都不变",
        all(
            v["money_and_execution_identical"] and v["monthly_u_identical"]
            for v in same_facts.values()
        )
        and len({v["next_quota"] for v in same_facts.values()}) == 1
        and all(
            v["goal_records_differ_from_original"]
            for k, v in same_facts.items()
            if k != "原计划（设目标 dryer，第 2 年第 6 月）"
        ),
        "五种目标写法下，现金流、逐月分数与下一年额度逐项相同；只有目标记录（这一年的目标事实与带进下一年的目标，"
        "以及包含它们的指纹）不同——只改说明文字时，变的是带进下一年的目标里的说明",
    )
    g = obs.savings.open_goal  # type: ignore[union-attr]
    check(
        "观察：自己的目标、余额、报价、差距与原因",
        g is not None
        and (g.status, g.past_due) == ("overdue", True)
        and (g.cash.amount, g.quote_now.amount, g.shortfall.amount, g.cash_covers_quote)  # type: ignore[union-attr]
        == (2380, 2400, 20, False)
        and any(e.kind == "due_unrealized" and "资金不足" in e.note for e in g.events)
        and "annual_mean" not in blob
        and "rho" not in blob,
        "第 3 年年初：余额 2,380、当前报价 2,400、还差 20、余额不少于报价？否；到期未兑现的原因可见；没有分数或评分参数",
    )
    check(
        "评分不变：沿用同一个评分内容身份",
        scoring.spec.content_fingerprint == policy.scoring_content_fingerprint
        and policy.scoring_content_fingerprint
        == "5b985b4ac351cc26c35039c969ebc2bb839cb20b058256c95ae97db80b3a8768"
        and policy.handoff_revision == "r3-2026-10-09",
        "E1-5B 的评分规格与 E1-5A 补充同一套内容（5b985b4a…），年度交接修订 r3；目标不进入评分",
    )

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg = args.out / "configs"
    cfg.mkdir()
    shutil.copyfile(LIFE, cfg / LIFE.name)
    shutil.copyfile(SCORING, cfg / SCORING.name)

    def dump(name: str, data: Any) -> None:
        path = args.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    dump("environment.json", _environment(policy.handoff_revision))
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": "uv run python scripts/demo_life_e1_5b.py --out <新目录>",
        },
    )
    dump(
        "protocol.json",
        {
            "protocol": bundle.protocol.model_dump(mode="json"),
            "savings_rules_content_fingerprint": rules.content_fingerprint,
            "career_rules_content_fingerprint": bundle.protocol.career_rules.content_fingerprint,  # type: ignore[union-attr]
            "feedback_policy_content_fingerprint": policy.content_fingerprint,
            "scoring_spec": {
                "spec_id": scoring.spec.spec_id,
                "scoring_version": scoring.spec.scoring_version,
                "content_fingerprint": scoring.spec.content_fingerprint,
            },
            "feedback_handoff_revision": policy.handoff_revision,
            "formal_scoring_ready": bundle.protocol.scoring_ready,
        },
    )
    dump(
        "inputs.json",
        {
            "life_file": str(LIFE),
            "life_sha256": _sha256(LIFE),
            "scoring_file": str(SCORING),
            "scoring_sha256": _sha256(SCORING),
            "life_report": life_report.model_dump(mode="json"),
            "scoring_report": score_report.model_dump(mode="json"),
        },
    )
    dump("stories.json", stories)
    dump("same_facts_goal_variants.json", same_facts)
    dump(
        "observations/u83_year3_payload.json",
        {
            "note": "u83 第 3 年年初的完整可见观察（标准产品许可）；last_feedback 是第 2 年的事实",
            **payload,
        },
    )
    dump(
        "checks.json",
        [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks],
    )

    lines = [
        "# E1-5B：目标储蓄（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；全部数字来自合成样例 `configs/life/synthetic_e1_5b_savings.yaml`，**不是现实"
        "价格、工资或储蓄行为，也不是城市排行**。三个人每月收入 1,500（税档 40,000 以内 0%），付远处一居室 1,000 + 基本食品 "
        "380，每月净存 120。",
        "",
        "读表时分清六件事：**钱还在**（余额）、**钱够报价**（余额不少于报价，只是比较）、**付了钱**（兑现：买到东西或付清学费）、"
        "**学会了**（课程参加满 12 个月）、**获得服务**（物品的服务月）、**产生满足感**（评分器按原规则）。目标只是年度计划里的"
        "软约束：不另开账户、不圈存、不扣款、不加分。",
        "",
    ]
    for clone, title in STORIES.items():
        lines += [f"## {title}", ""]
        lines += [
            "| 年 | 年初现金 | 收入 | 支出 | 年末现金 | 年初指令 | 目标事件 | 兑现依据 | 年度满足感 S | 本年额度 → 下一年 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for y in stories[clone]:
            ev = "、".join(
                f"{e['kind']}" + (f"（第 {e['month']} 月）" if e["month"] else "（年初）")
                for e in y["events"]
            )
            rz = y["realization"]
            rz_text = (
                "—"
                if rz is None
                else f"第 {rz['month']} 月 {rz['evidence']}，付 {_fmt(rz['amount_paid']['amount'])}"
                + ("，按时" if rz["on_time"] else "，晚于目标时点")
            )
            lines.append(
                f"| {y['year']} | {_fmt(y['cash_start'])} | {_fmt(y['income'])} | {_fmt(y['spending'])} | "
                f"{_fmt(y['cash_end'])} | {y['directive'] or '（不写，原样延续）'} | {ev or '—'} | {rz_text} | "
                f"{y['S']:.6f} | {y['quota_this_year']} → {y['next_quota'] if y['next_quota'] is not None else '终态'} |"
            )
        lines.append("")
        for y in stories[clone]:
            if not y["progress"]:
                continue
            lines += [f"第 {y['year']} 年目标进展：", "", *_progress_table(y), ""]
            for n in y["notes"]:
                lines.append(f"* {n}")
            if y["learning"]:
                for x in y["learning"]:
                    lines.append(
                        f"* 学习：{x['course']} 本年实付学费 {_fmt(x['fee_charged'])}，参加第 {x['months_attended']} 月，进度 "
                        f"{x['progress']}"
                        + (
                            f"，第 {x['completed_month']} 月学完，能力 {x['granted']} 第 {x['usable_from_year']} 年起可用"
                            if x["completed_month"]
                            else "，还没学完"
                        )
                    )
            lines.append("")
    lines += [
        "## 同一份生活事实，只改目标",
        "",
        "u81 第 1 年同一份行动（远处一居室、基本食品、门店兼职），目标写法不同：",
        "",
        "| 目标写法 | 年末现金 | 现金流与执行逐项相同 | 逐月分数相同 | S | 下一年额度 | 目标事件 |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, v in same_facts.items():
        lines.append(
            f"| {k} | {_fmt(v['cash_end'])} | {'是' if v['money_and_execution_identical'] else '否'} | "
            f"{'是' if v['monthly_u_identical'] else '否'} | {v['S']:.6f} | {v['next_quota']} | "
            f"{'、'.join(v['goal_events']) or '—'} |"
        )
    lines += [
        "",
        "## 手算核对",
        "",
    ]
    for name, ok, detail in checks:
        lines.append(f"- {'通过' if ok else '**不通过**'}　{name}：{detail}")
    lines += [
        "",
        "故事三的额外开支是一笔计划内的探亲往返，用来证明目标既挡不住实际支出、也不会复制或藏起一笔钱；它**不是**随机"
        "疾病或应急机制（那是 E1-5C）。期末未花的现金、未兑现的目标与物品残值怎样评价留给 E1-6。",
        "",
    ]
    (args.out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    (args.out / "checks.md").write_text(
        "\n".join(f"- {'通过' if ok else '不通过'}　{n}：{d}" for n, ok, d in checks) + "\n",
        encoding="utf-8",
    )
    for name, ok, detail in checks:
        print(f"- {'通过' if ok else '不通过'}　{name}：{detail}")
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
