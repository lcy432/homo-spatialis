"""E1-5C：疾病、应急与恢复的离线演示（`DECISIONS/0027`）。零大模型调用。

    uv run python scripts/demo_life_e1_5c.py --out runs/<新目录>

读 E1-5C 的合成生活输入与评分配置，按预先声明的年度计划（**分身的预设计划**，不是 LLM 理解或由健康输入生成）逐年做真实的
年度交接（结算 → 评分 → 反馈），输出：

* 手算故事：小病治疗 / 不治 / 治不起；大病病假与住院；大病不治到出局；上课时大病；跨年的病与目标；应急安排不足的停止与
  把健身列进暂停顺序的对照；同一个人在资料未知的城市因资料缺口停止；
* γ = 0 对照臂（S = B，下一年额度不同；旧额度不能跨臂）；
* 无病对照臂：与 E1-5B 规则下同一份生活的账目、活动与资源逐项比较；
* 配对随机数臂：同序号跨城共用随机数、重放与顺序无关、内部抽签记录（不进观察）；
* 小批次：一个分身出局或停止，其他分身照常；按城市互斥计数，不算完成者平均；
* 第 2 年年初的观察样例：看得到病情、规则与本城治疗，看不到风险映射、概率表与评分参数。

全部数字来自合成样例：不是医学结论、现实价格或工资，也不排序城市。目录已存在就拒绝，不覆盖旧结果。
退出码：0 = 全部核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    IllnessEventSource,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
)
from common.life_feedback_contracts import LifeYearResult
from common.life_scoring_contracts import LifeScoringSpec
from econ.life_health import illness_key, uniform_of
from econ.life_settlement import binding_from_policy, settle_year
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle, parse_bundle
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_5c_health.yaml")
SCORING = Path("configs/life/scoring_e1_5c_health.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/common/life_feedback_contracts.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_health.py",
    "src/econ/life_settlement.py",
    "src/econ/life_resources.py",
    "src/econ/life_career.py",
    "src/econ/life_savings.py",
    "src/econ/life_scoring.py",
    "src/econ/life_coverage.py",
    "src/econ/life_feedback.py",
    "src/econ/life_year.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_5c.py",
    "tests/life/test_health_illness.py",
    "tests/life/test_health_scoring.py",
)
STORIES = {
    "u91.a91.treat": "小病，条件允许就治：门诊 300，当月月末恢复（u91，A91）",
    "u91.b92.treat": "同一个人、同一件小病，在治疗资料未知的 B92：资料缺口，停在第 3 月（u91，B92）",
    "u92.a91.decline": "小病，计划不治：病 2 个月，自然恢复（u92）",
    "u93.a91.broke": "小病，治不起；第 2 月治疗已经没用（u93）",
    "u94.a91.major": "大病，整月病假、住院，月末恢复（u94）",
    "u95.a91.decline": "大病不治：病假 3 个月，第 7 月付不起最低生活而出局（u95）",
    "u96.a91.study": "上会计课时大病：病假、停课、住院，第 2 年补上那个月（u96）",
    "u97.a91.crossyear": "跨年的病与资金目标；带着没好的病进入终态（u97）",
    "u98.a91.stop": "全职会计 + 健身、没列暂停顺序：第 5 月小病，应急安排不足而停止（u98）",
    "u99.a91.pause": "同样的生活，把健身列进暂停顺序：第 5 月暂停健身、治疗（u99）",
}
CLINIC = "a91.care.clinic"
HOSPITAL = "a91.care.hospital"
STATUS_LABEL = {
    "treated": "已治疗",
    "declined": "计划不治",
    "no_benefit": "治疗没有作用",
    "unavailable": "本城没有",
    "unaffordable": "治不起",
    "resources_insufficient": "时间精力放不下",
    "data_gap": "资料缺口",
}


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
    files = [*KEY_SOURCES, str(LIFE), str(SCORING)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _c(x: Any) -> int | None:
    """金额 → 整数分（来自整数分记账，没有舍入问题）。"""
    return None if x is None else round(x.amount * 100)


def _fmt(cents: int | None) -> str:
    return "—" if cents is None else f"{Decimal(cents) / 100:,.2f}"


def _revise(model: Any, **changes: Any) -> Any:
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


class World:
    """一份生活输入 + 一份评分配置：按年做真实的年度交接。``protocol`` 可以换成对照臂。"""

    def __init__(self, bundle: LifeBundle, scoring: ScoringConfig) -> None:
        self.b = bundle
        self.scoring = scoring

    def profile(self, person: str) -> Any:
        return next(p for p in self.b.stated_profiles if p.person_id == person)

    def year(
        self,
        protocol: LifeProtocol,
        state: PersonYearState,
        plan: Any,
        scoring: ScoringConfig | None = None,
        profile: Any = None,
    ) -> LifeYearResult:
        cfg = scoring or self.scoring
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        prof = profile or self.profile(state.person_id)
        return run_life_year(
            protocol=protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=binding_from_policy(self.b.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
            scoring=ScoringInputs(
                spec=cfg.spec,
                conditions=cfg.conditions_for(str(state.city)),  # type: ignore[arg-type]
                preference_input=prof,
                hard_requirements=prof.hard_requirements,
            ),
        )

    def run(
        self, protocol: LifeProtocol, clone: str, scoring: ScoringConfig | None = None
    ) -> tuple[list[PersonYearState], list[LifeYearResult]]:
        state = self.b.initial_state(clone)
        assert state is not None
        starts, results = [state], []
        for plan in self.b.plans_for(clone):
            r = self.year(protocol, state, plan, scoring)
            results.append(r)
            if r.next_state is None or r.handoff != "next_year_ready":
                break
            state = r.next_state
            starts.append(state)
        return starts, results


# ---------------------------------------------------------------------- 一年的摘要


def _health_rows(r: LifeYearResult) -> list[dict[str, Any]]:
    out = r.settlement.outcome
    if out is None or out.health is None:
        return []
    rows = []
    for x in out.health.months:
        tr = x.treatment
        rows.append(
            {
                "month": x.month,
                "record": x.record,
                "state": x.state,
                "onset": x.onset,
                "episode_month": x.episode_month,
                "sick_leave": x.sick_leave,
                "paused": [a.opportunity_id for a in x.paused],
                "treatment": None if tr is None else tr.status,
                "treatment_paid": None if tr is None else _c(tr.amount_paid),
                "recovery_burden": None if x.recovery_burden is None else x.recovery_burden.value,
                "transition": x.transition,
            }
        )
    return rows


def _year_row(start: PersonYearState, r: LifeYearResult) -> dict[str, Any]:
    s = r.settlement
    out = s.outcome
    row: dict[str, Any] = {
        "year": r.year_index,
        "settlement": s.status,
        "handoff": r.handoff,
        "problems": [f"{p.code}（第 {p.month} 月）" for p in s.problems],
    }
    if out is None:
        return row
    months = {m.month: m for m in out.months}
    res = {m.month: m for m in out.resources.months} if out.resources is not None else {}
    row.update(
        {
            "cash_start": _c(out.cash_start),
            "cash_end": _c(out.cash_end),
            "month_ends": [_c(months[m].cash_end) if m in months else None for m in range(1, 13)],
            "energy_used": [
                res[m].energy_used.value if m in res and res[m].energy_used is not None else None
                for m in range(1, 13)
            ],
            "resource_check": [res[m].resource_check if m in res else None for m in range(1, 13)],
            "medical_flows": [
                (f.month, _c(f.amount))
                for f in out.cash_flows
                if f.opportunity_id in (CLINIC, HOSPITAL)
            ],
            "health": _health_rows(r),
            "exit": None
            if out.exit is None
            else {"month": out.exit.month, "shortfall": _c(out.exit.shortfall)},
            "stop": None
            if out.stop is None
            else {"month": out.stop.month, "kind": out.stop.kind, "reason": out.stop.reason},
            "learning": [
                {
                    "course": x.opportunity_id,
                    "attended": list(x.months_attended),
                    "paused": list(x.months_paused),
                    "progress": f"{x.progress_before}→{x.progress_after}/{x.months_required}",
                    "fee": _c(x.fee_charged),
                    "granted": list(x.granted),
                }
                for x in (out.career.learning if out.career is not None else ())
            ],
            "positions": [
                {
                    "job": p.opportunity_id,
                    "paid_months": list(p.months),
                    "sick_leave_months": list(p.sick_leave_months),
                }
                for p in (out.career.positions if out.career is not None else ())
            ],
            "savings_events": [(e.kind, e.month) for e in out.savings.events]
            if out.savings is not None
            else [],
        }
    )
    if r.score is not None:
        row["S"] = r.score.annual_mean
        row["score_status"] = r.score.annual_status
        row["monthly_u"] = [m.u for m in r.score.months]
        row["monthly_B"] = [m.daily_life for m in r.score.months]
        row["monthly_health"] = [
            None if m.health is None else {"state": m.health.state, "deduction": m.health.deduction}
            for m in r.score.months
        ]
    else:
        row["score_problem"] = r.score_problem
    row["quota_this_year"] = start.energy.level
    row["next_quota"] = r.next_state.energy.level if r.handoff == "next_year_ready" else None
    if r.next_state is not None and r.next_state.health is not None:
        h = r.next_state.health
        row["next_health"] = {
            "status": h.status,
            "age": h.age_years,
            "episode": None if h.episode is None else h.episode.model_dump(mode="json"),
            "history": [e.episode_id for e in h.history],
        }
    return row


# ---------------------------------------------------------------------- 对照臂


def _gamma_zero(world: World) -> tuple[LifeProtocol, ScoringConfig, dict[str, Any]]:
    raw = world.scoring.spec.model_dump(mode="json")
    raw["spec_id"] = "score.e1-5c.gamma0"
    raw["health"]["gamma"] = 0.0
    spec = LifeScoringSpec.model_validate(raw)
    cond = tuple(_revise(c, spec_id=spec.spec_id) for c in world.scoring.city_conditions)
    group = _revise(world.scoring.comparison_group, group_id="group.e1-5c.gamma0")
    cfg = _revise(world.scoring, spec=spec, city_conditions=cond, comparison_group=group)
    p = world.b.protocol
    policy = _revise(
        p.energy_feedback,
        policy_id="feedback.e1-5c.gamma0",
        scoring_spec_id=spec.spec_id,
        scoring_content_fingerprint=spec.content_fingerprint,
    )
    proto = _revise(p, protocol_id="life-v1-e1-5c-health-gamma0", energy_feedback=policy)
    spec2 = _revise(spec, protocol_id=proto.protocol_id)
    cfg = _revise(cfg, spec=spec2)
    arm = {
        "spec": spec2.model_dump(mode="json"),
        "energy_feedback": policy.model_dump(mode="json"),
        "content_fingerprint": spec2.content_fingerprint,
    }
    return proto, cfg, arm


def _source(mode: str, **kw: Any) -> IllnessEventSource:
    data: dict[str, Any] = {
        "mode": mode,
        "stream_version": "e1-5c-illness-stream-v1",
        "note": kw.pop("note", "演示对照臂的事件来源"),
    }
    data.update(kw)
    return IllnessEventSource.model_validate(data)


def _strip_health(raw: dict[str, Any]) -> dict[str, Any]:
    """同一份生活输入去掉疾病部分，改成 E1-5B 规则（无病对照的参照）。"""
    raw = copy.deepcopy(raw)
    proto = raw["protocol"]
    for key in ("health_rules", "illness_events", "health_input"):
        proto.pop(key)
    proto["life_rules_version"] = "e1-5b-monthly-savings-v1"
    for cat in raw["catalogs"]:
        cat.pop("treatments")
    for s in raw["initial_states"]:
        s.pop("health")
    for p in raw["plans"]:
        p.pop("emergency")
    for p in raw["stated_profiles"] + raw["internal_truths"]:
        p.pop("healthcare_semantics")
    return raw


# ---------------------------------------------------------------------- 小批次


def run_batch(
    world: World,
    protocol: LifeProtocol,
    clones: list[str],
    runner: Callable[[LifeProtocol, str], tuple[list[PersonYearState], list[LifeYearResult]]]
    | None = None,
) -> dict[str, Any]:
    """演示层的薄循环（不是正式调度）：每个分身单独运行；预期的停止只停这个分身；意外程序错误显式报告并停止批次。

    报告保留全部预定分身与停止原因，类别互斥、能与原计划人数核对；不计算完成者平均，不补停止之后的年份。
    """
    runner = runner or (lambda p, c: world.run(p, c))
    records: list[dict[str, Any]] = []
    runs: dict[str, list[LifeYearResult]] = {}
    status = "finished"
    for i, clone in enumerate(clones):
        state = world.b.initial_state(clone)
        assert state is not None
        rec: dict[str, Any] = {"clone_id": clone, "city": str(state.city)}
        try:
            _, results = runner(protocol, clone)
        except Exception as exc:
            rec.update(category="unexpected_error", stop_reason=f"{type(exc).__name__}: {exc}")
            records.append(rec)
            for later in clones[i + 1 :]:
                s = world.b.initial_state(later)
                records.append(
                    {"clone_id": later, "city": str(s.city) if s else "?", "category": "not_run"}
                )
            status = "stopped_unexpected_error"
            break
        runs[clone] = results
        last = results[-1]
        out = last.settlement.outcome
        if last.handoff == "stopped":
            assert out is not None and out.stop is not None
            cat = f"stopped_{out.stop.kind}"
            why = f"第 {out.stop.year_index} 年第 {out.stop.month} 月：{out.stop.reason}"
        elif last.handoff == "exited":
            assert out is not None and out.exit is not None
            cat, why = "exited", f"第 {out.exit.year_index} 年第 {out.exit.month} 月付不起最低生活"
        elif last.handoff == "settlement_failed":
            cat = last.settlement.status
            why = "；".join(p.message for p in last.settlement.problems)[:300]
        elif last.handoff == "feedback_blocked":
            cat, why = "feedback_blocked", last.score_problem or "反馈信息不足"
        else:
            cat, why = "completed", "按预先声明的计划过完"
        rec.update(category=cat, stop_reason=why, years_recorded=len(results))
        records.append(rec)
    per_city: dict[str, dict[str, int]] = {}
    for rec in records:
        row = per_city.setdefault(rec["city"], {"planned": 0})
        row["planned"] += 1
        row[rec["category"]] = row.get(rec["category"], 0) + 1
    if status == "finished" and any(r["category"] != "completed" for r in records):
        status = "finished_partially_complete"
    return {"batch_status": status, "clones": records, "per_city": per_city, "runs": runs}


# ---------------------------------------------------------------------- 主程序


def main() -> int:
    ap = argparse.ArgumentParser(
        description="E1-5C：疾病、应急与恢复的离线演示（不评城市、不排序）"
    )
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
    proto = bundle.protocol
    assert proto.health_rules is not None and proto.energy_feedback is not None

    # ---------------------------------------------------------------- 主臂：指定事件
    stories: dict[str, list[dict[str, Any]]] = {}
    raw_runs: dict[str, tuple[list[PersonYearState], list[LifeYearResult]]] = {}
    for clone in STORIES:
        starts, results = world.run(proto, clone)
        raw_runs[clone] = (starts, results)
        stories[clone] = [_year_row(s, r) for s, r in zip(starts, results, strict=False)]

    # ---------------------------------------------------------------- γ = 0 对照臂
    proto0, cfg0, arm0 = _gamma_zero(world)
    gamma0: dict[str, Any] = {}
    for clone in STORIES:
        starts, results = world.run(proto0, clone, cfg0)
        main_results = raw_runs[clone][1]
        gamma0[clone] = {
            "S_gamma0": [r.score.annual_mean if r.score else None for r in results],
            "S_main": [r.score.annual_mean if r.score else None for r in main_results],
            "u_equals_B_every_month": all(
                m.u == m.daily_life for r in results if r.score for m in r.score.months
            ),
            "year1_ledger_identical": results[0].settlement.model_dump_json(exclude={"next_state"})
            == main_results[0].settlement.model_dump_json(exclude={"next_state"}),
            "next_quota_gamma0": [
                r.next_state.energy.level if r.handoff == "next_year_ready" else None
                for r in results
            ],
            "next_quota_main": [
                r.next_state.energy.level if r.handoff == "next_year_ready" else None
                for r in main_results
            ],
        }
    y1_main = raw_runs["u94.a91.major"][1][0]
    cat94 = bundle.catalog_for(str(y1_main.next_state.city))  # type: ignore[union-attr]
    assert cat94 is not None
    crossed = settle_year(
        protocol=proto0,
        state=y1_main.next_state,  # type: ignore[arg-type]
        action=world.b.plans_for("u91.a91.treat")[0].model_copy(
            update={"clone_id": "u94.a91.major", "person_id": "u94", "year_index": 2}
        ),
        catalog=cat94,
        tax=binding_from_policy(bundle.tax_policy(cat94.tax_policy_id)),  # type: ignore[arg-type]
    )

    # ---------------------------------------------------------------- 无病对照臂 vs E1-5B 规则
    raw_life = yaml.safe_load(LIFE.read_text(encoding="utf-8"))
    old = parse_bundle(_strip_health(raw_life))
    none_proto = _revise(proto, illness_events=_source("none"))
    no_illness: dict[str, Any] = {}
    for clone in STORIES:
        s_new, s_old = bundle.initial_state(clone), old.initial_state(clone)
        assert s_new is not None and s_old is not None
        c_new, c_old = bundle.catalog_for(str(s_new.city)), old.catalog_for(str(s_old.city))
        assert c_new is not None and c_old is not None
        new = settle_year(
            protocol=none_proto,
            state=s_new,
            action=bundle.plans_for(clone)[0],
            catalog=c_new,
            tax=binding_from_policy(bundle.tax_policy(c_new.tax_policy_id)),  # type: ignore[arg-type]
        )
        ref = settle_year(
            protocol=old.protocol,
            state=s_old,
            action=old.plans_for(clone)[0],
            catalog=c_old,
            tax=binding_from_policy(old.tax_policy(c_old.tax_policy_id)),  # type: ignore[arg-type]
        )
        same = (
            new.outcome is not None
            and ref.outcome is not None
            and new.outcome.model_dump_json(exclude={"health"}) == ref.outcome.model_dump_json()
            and new.next_state.model_dump_json(exclude={"health"})  # type: ignore[union-attr]
            == ref.next_state.model_dump_json()  # type: ignore[union-attr]
        )
        no_illness[clone] = {
            "status_new": new.status,
            "status_e1_5b": ref.status,
            "ledger_activities_resources_identical": same,
            "all_months_healthy": new.outcome is not None
            and all(x.state == "healthy" for x in new.outcome.health.months),  # type: ignore[union-attr]
        }

    # ---------------------------------------------------------------- 配对随机数臂
    rand_proto = _revise(
        proto,
        illness_events=_source(
            "random",
            pairing_id="pair.e1-5c.demo",
            seed=20261011,
            note="配对随机数：键 = 流版本、配对身份、种子、人、重复序号、绝对月份、事件类别",
        ),
    )
    rand: dict[str, Any] = {}
    draws_log: dict[str, Any] = {}
    for clone in STORIES:
        starts_r, results = world.run(rand_proto, clone)
        rand[clone] = [_year_row(s, r) for s, r in zip(starts_r, results, strict=False)]
        draws_log[clone] = [
            {
                "year": r.year_index,
                "month": x.month,
                "uniform": x.draw.uniform.exact,
                "key_sha256": x.draw.key_fingerprint,
                "age_band": x.draw.age_band,
                "burden_band": x.draw.burden_band,
                "burden_from": x.draw.burden_from,
                "p_minor": x.draw.p_minor.exact,
                "p_major": x.draw.p_major.exact,
                "result": x.draw.result,
            }
            for r in results
            if r.settlement.outcome is not None and r.settlement.outcome.health is not None
            for x in r.settlement.outcome.health.months
            if x.draw is not None
        ]
    replay = {
        clone: [r.model_dump_json() for r in world.run(rand_proto, clone)[1]]
        == [r.model_dump_json() for r in world.run(rand_proto, clone)[1]]
        for clone in ("u91.a91.treat", "u98.a91.stop")
    }
    pa = {(d["year"], d["month"]): d["uniform"] for d in draws_log["u91.a91.treat"]}
    pb = {(d["year"], d["month"]): d["uniform"] for d in draws_log["u91.b92.treat"]}
    paired = sorted(set(pa) & set(pb))
    src = rand_proto.illness_events
    assert src is not None
    u_rep0, _ = uniform_of(illness_key(src, "u91", 0, 1, 1))
    u_rep1, _ = uniform_of(illness_key(src, "u91", 1, 1, 1))
    events_random = {
        clone: [
            (row["year"], h["month"], h["onset"])
            for row in rand[clone]
            for h in row.get("health", [])
            if h["onset"] is not None
        ]
        for clone in STORIES
    }

    # ---------------------------------------------------------------- 小批次（主臂；正序与倒序）
    clones = list(STORIES)
    batch = run_batch(world, proto, clones)
    batch_rev = run_batch(world, proto, list(reversed(clones)))
    batch_same = all(
        [r.model_dump_json() for r in batch["runs"][c]]
        == [r.model_dump_json() for r in batch_rev["runs"][c]]
        for c in clones
    )

    # ---------------------------------------------------------------- 只改健康输入
    high = _revise(
        world.profile("u91"),
        dimension_points={
            "material": 15,
            "safety": 5,
            "healthcare": 55,
            "opportunity": 10,
            "leisure": 5,
            "environment": 5,
            "commute": 5,
        },
    )
    s91 = bundle.initial_state("u91.a91.treat")
    assert s91 is not None
    plan91 = bundle.plans_for("u91.a91.treat")[0]
    low_r = world.year(proto, s91, plan91)
    high_r = world.year(proto, s91, plan91, profile=high)
    input_only = {
        "settlement_identical": low_r.settlement.model_dump_json()
        == high_r.settlement.model_dump_json(),
        "monthly_u_identical": [m.u for m in low_r.score.months]  # type: ignore[union-attr]
        == [m.u for m in high_r.score.months],  # type: ignore[union-attr]
        "effective_weights": high_r.score.health_weighting.effective_weights,  # type: ignore[union-attr]
        "raw_healthcare": [0.1, 0.55],
        "next_quota": [low_r.next_state.energy.level, high_r.next_state.energy.level],  # type: ignore[union-attr]
    }

    # ---------------------------------------------------------------- 第 2 年年初的观察（u97）
    starts97, results97 = raw_runs["u97.a91.crossyear"]
    state2 = starts97[1]
    cat97 = bundle.catalog_for(str(state2.city))
    assert cat97 is not None
    obs = build_observation(
        permission=standard_product_permission(proto.info_permission_version),
        profile=world.profile("u97"),
        state=state2,
        catalog=cat97,
        last_feedback=visible_feedback(results97[0].settlement),
        resource_rules=proto.resource_rules,
        energy_feedback=proto.energy_feedback,
        career_rules=proto.career_rules,
        savings_rules=proto.savings_rules,
        health_rules=proto.health_rules,
        health_input=proto.health_input,
    )
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)

    # ---------------------------------------------------------------- 手算核对
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    def first(clone: str) -> dict[str, Any]:
        return stories[clone][0]

    def h(clone: str, month: int, year_i: int = 0) -> dict[str, Any]:
        return next(x for x in stories[clone][year_i]["health"] if x["month"] == month)

    s = first("u91.a91.treat")
    check(
        "小病治疗：付一次、当月月末恢复",
        s["month_ends"][2] == 56000
        and s["medical_flows"] == [(3, -30000)]
        and h("u91.a91.treat", 3)["transition"] == "recovered_treated"
        and s["cash_end"] == 164000
        and s["energy_used"][2] == 137.75,
        "第 3 月 740 + 1,500 − 1,380 − 300 = 560；恢复 10 h × 2.0 + 门诊 2 h × 1.0 → 当月负担 115.75 + 22 = 137.75；年末 1,640",
    )
    s = first("u91.b92.treat")
    check(
        "资料缺口：停在第 3 月，不当免费、不当没有",
        s["settlement"] == "stopped"
        and s["stop"]["kind"] == "data_gap"
        and s["month_ends"][:3] == [62000, 74000, None]
        and h("u91.b92.treat", 3)["record"] == "blocked",
        "同一件小病在 B92：门诊资料未知，计划要治——资料缺口；第 1、2 月保留（余额 740），这不是看不起病",
    )
    s = first("u92.a91.decline")
    check(
        "小病不治：病 2 个月，没有医疗支出",
        [h("u92.a91.decline", m)["transition"] for m in (3, 4)]
        == ["continues", "recovered_natural"]
        and s["medical_flows"] == []
        and s["cash_end"] == 194000,
        "第 3、4 月小病（每月多 20 负担），第 4 月月末自然恢复；年末 500 + 12 × 120 = 1,940",
    )
    s = first("u93.a91.broke")
    check(
        "治不起，之后治疗也没用",
        h("u93.a91.broke", 1)["treatment"] == "unaffordable"
        and h("u93.a91.broke", 2)["treatment"] == "no_benefit"
        and s["medical_flows"] == []
        and s["month_ends"][:2] == [12000, 24000],
        "第 1 月住房食品之后只剩 120 < 300；第 2 月本来就在月末结束，不购买；第 2 月月末自然恢复",
    )
    s = first("u94.a91.major")
    check(
        "大病：整月病假、住院、月末恢复",
        s["month_ends"][3:5] == [348000, 10000]
        and s["medical_flows"] == [(5, -200000)]
        and h("u94.a91.major", 5)["sick_leave"]
        and s["energy_used"][4] == 95
        and s["cash_end"] == 94000,
        "第 5 月没有工资：3,480 − 1,380 − 2,000 = 100；负担 = 食品 15 + 洗衣 6 + 打扫 6 + 恢复 60 + 住院 8 = 95；年末 940",
    )
    s = first("u95.a91.decline")
    check(
        "大病不治：三个月病假后出局",
        s["settlement"] == "exited"
        and s["exit"] == {"month": 7, "shortfall": 66000}
        and s["month_ends"][4:7] == [210000, 72000, 72000],
        "第 5 月 3,480 − 1,380 = 2,100；第 6 月 720；第 7 月没有工资、720 < 1,380 → 出局（缺 660）",
    )
    y1, y2 = stories["u96.a91.study"][:2]
    check(
        "学习暂停：不增加进度、不重收学费，第 2 年补上",
        y1["learning"][0]["paused"] == [4]
        and y1["learning"][0]["progress"] == "0→11/12"
        and y1["learning"][0]["fee"] == 120000
        and y2["learning"][0]["fee"] == 0
        and y2["learning"][0]["granted"] == ["accounting_cert"]
        and y1["month_ends"][3] == 78000
        and y2["cash_end"] == 318000,
        "第 4 月大病：停课、病假、住院（4,160 − 1,380 − 2,000 = 780）；参加 11/12；第 2 年第 1 月学完、不再收学费，能力第 3 年起可用",
    )
    y97 = stories["u97.a91.crossyear"]
    check(
        "跨年的病：接着数月、不重复收费",
        [x["episode_month"] for x in y97[1]["health"][:2]] == [2, 3]
        and y97[1]["health"][1]["transition"] == "recovered_natural"
        and all(not y["medical_flows"] for y in y97)
        and y97[0]["cash_end"] == 394000
        and y97[1]["month_ends"][:2] == [256000, 118000],
        "第 1 年第 12 月大病不治：5,320 − 1,380 = 3,940；第 2 年第 1 月 2,560、第 2 月 1,180，第 2 月月末自然恢复（病 3 个月）",
    )
    check(
        "病假少挣的钱让目标逾期；之后照常兑现",
        y97[1]["savings_events"] == [("due_unrealized", 6)]
        and ("realized", 3) in y97[2]["savings_events"]
        and y97[2]["cash_end"] == 142000,
        "第 2 年第 6 月 1,660 < 2,400：到期未兑现（不出局）；第 3 年改时点到第 3 月买下：2,740 − 2,400 = 340；年末 1,420",
    )
    term = y97[2]
    check(
        "终态带着没好的病，不自动痊愈",
        term["handoff"] == "terminal"
        and term["next_health"]["status"] == "minor"
        and term["next_health"]["episode"]["months_ill"] == 1
        and term["next_health"]["history"] == ["ill-y1m12"],
        "第 3 年第 12 月小病不治；终态里仍是小病（病了 1 个月）；第 1 年的大病已结束，只在病史里出现一次",
    )
    s98, s99 = first("u98.a91.stop"), first("u99.a91.pause")
    check(
        "应急安排不足：停在第 5 月，第 1–4 月保留",
        s98["settlement"] == "stopped"
        and s98["stop"]["kind"] == "emergency_insufficient"
        and s98["month_ends"][:5] == [256000, 412000, 568000, 724000, None]
        and s98["resource_check"][4] == "blocked"
        and "252.5" in s98["stop"]["reason"],
        "工作 176 + 通勤 17.5 + 食品 15 + 洗衣 6 + 打扫 6 + 健身 12 = 232.5；小病加恢复 20 与门诊 2 = 254.5；没有可暂停的活动，放弃治疗仍是 252.5 > 250",
    )
    check(
        "把健身列进暂停顺序：暂停健身、治疗，照常过完",
        s99["settlement"] == "completed"
        and h("u99.a91.pause", 5)["paused"] == ["a91.service.gym"]
        and h("u99.a91.pause", 5)["treatment"] == "treated"
        and s99["energy_used"][4] == 242.5
        and s99["month_ends"][4] == 856000
        and s99["cash_end"] == 1948000,
        "第 5 月暂停健身（不付款）：220.5 + 20 + 2 = 242.5 ≤ 250；7,240 + 3,000 − 1,380 − 300 = 8,560；年末 19,480",
    )
    u91 = first("u91.a91.treat")
    check(
        "健康项：同等病情扣分相同，付款不加分",
        u91["monthly_health"][2]["deduction"] == 0.1
        and first("u92.a91.decline")["monthly_u"][2] == u91["monthly_u"][2]
        and u91["monthly_B"][2] == u91["monthly_B"][1]
        and first("u94.a91.major")["monthly_health"][4]["deduction"] == 0.2,
        "γ = 0.2：小病扣 0.2 × (1 − 0.5) = 0.1、大病扣 0.2；付了 300 治疗的人与没治的人第 3 月分数相同；治疗月的日常生活分 B 与健康月相同",
    )
    check(
        "γ = 0 对照：S = B，账目相同，额度不同；旧额度不能跨臂",
        all(v["u_equals_B_every_month"] and v["year1_ledger_identical"] for v in gamma0.values())
        and gamma0["u94.a91.major"]["next_quota_gamma0"][0]
        != gamma0["u94.a91.major"]["next_quota_main"][0]
        and crossed.status == "input_error"
        and "scoring_content_changed" in {p.code for p in crossed.problems},
        "γ = 0 的规格是另一个评分内容身份：同一份结算，分数 S = B；主臂的第 2 年额度拿到 γ = 0 臂里用被拒绝",
    )
    check(
        "无病对照：账目、活动与资源与 E1-5B 规则下逐项相同",
        all(
            v["ledger_activities_resources_identical"] and v["all_months_healthy"]
            for v in no_illness.values()
        ),
        "关掉新事件，十个分身第 1 年的现金流、行动执行、持有物、资源账、目标与下一年状态（健康部分以外）逐字节相同",
    )
    check(
        "配对随机数：同序号跨城共用、重放相同、序号不同则不同",
        bool(paired)
        and all(pa[k] == pb[k] for k in paired)
        and all(replay.values())
        and u_rep0 != u_rep1,
        f"u91 在 A91 与 B92 共同抽签的 {len(paired)} 个月用同一个随机数；同一输入重跑逐字节相同；重复序号 0 与 1 的随机数不同",
    )
    check(
        "只改健康输入：现金、病情与分数都不变",
        input_only["settlement_identical"] and input_only["monthly_u_identical"],
        "u91 的健康风险应对点数 10 → 55（六维比例不变）：结算逐字节相同，逐月分数逐位相同",
    )
    cats = {r["clone_id"]: r["category"] for r in batch["clones"]}
    check(
        "小批次：出局与停止只停自己，计数可核对，不算完成者平均",
        cats["u95.a91.decline"] == "exited"
        and cats["u98.a91.stop"] == "stopped_emergency_insufficient"
        and cats["u91.b92.treat"] == "stopped_data_gap"
        and sum(1 for c in cats.values() if c == "completed") == 7
        and all(
            row["planned"] == sum(v for k, v in row.items() if k != "planned")
            for row in batch["per_city"].values()
        )
        and batch_same,
        "十个预定分身全部在报告里；按城市互斥计数加起来等于原计划人数；正序与倒序运行结果相同",
    )
    hidden = (
        "under_40",
        "moderate",
        "heavy",
        "p_minor",
        "uniform",
        "seed",
        "pairing_id",
        "gamma",
        "state_values",
    )
    check(
        "观察：看得到病情与规则，看不到风险映射与评分参数",
        payload["health"]["state"] == "major"
        and payload["health"]["ongoing"]["natural_end"].startswith(
            "不治疗的话，第 2 年第 2 月月末恢复"
        )
        and payload["channels"]["health_risk_information"] == "default_no_risk_relation"
        and not any(w in blob for w in hidden),
        "第 2 年年初：自己正在生大病（病了 1 个月）、不治的话第 2 月月末恢复、本城门诊与住院的价格和时间、规则说明；没有概率表、风险档、随机数或 γ、H",
    )

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg_dir = args.out / "configs"
    (cfg_dir / "arms").mkdir(parents=True)
    shutil.copyfile(LIFE, cfg_dir / LIFE.name)
    shutil.copyfile(SCORING, cfg_dir / SCORING.name)

    def dump(name: str, data: Any) -> None:
        path = args.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
            newline="\n",
        )

    dump("configs/arms/gamma0.json", arm0)
    dump(
        "configs/arms/no_new_events.json",
        {"illness_events": none_proto.illness_events.model_dump(mode="json")},
    )  # type: ignore[union-attr]
    dump("configs/arms/paired_random.json", {"illness_events": src.model_dump(mode="json")})
    dump("environment.json", _environment())
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": "uv run python scripts/demo_life_e1_5c.py --out <新目录>",
        },
    )
    hr, ev = proto.health_rules, proto.illness_events
    assert ev is not None
    dump(
        "protocol.json",
        {
            "protocol": proto.model_dump(mode="json"),
            "health_rules_content_fingerprint": hr.content_fingerprint,
            "illness_source_content_fingerprint": ev.content_fingerprint,
            "scoring_spec": {
                "spec_id": scoring.spec.spec_id,
                "scoring_version": scoring.spec.scoring_version,
                "content_fingerprint": scoring.spec.content_fingerprint,
            },
            "feedback_policy_content_fingerprint": proto.energy_feedback.content_fingerprint,
            "feedback_handoff_revision": proto.energy_feedback.handoff_revision,
            "formal_scoring_ready": proto.scoring_ready,
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
    dump(
        "gamma0_control.json",
        {"clones": gamma0, "crossed_quota_problems": [p.code for p in crossed.problems]},
    )
    dump("no_illness_control.json", no_illness)
    dump(
        "paired_random.json",
        {
            "note": "内部记录（不进观察）：每个健康月份月初的抽签。u = 随机键 SHA-256 的前 64 位 ÷ 2^64",
            "events": events_random,
            "paired_months_u91": [f"第 {y} 年第 {m} 月" for y, m in paired],
            "replay_identical": replay,
            "draws": draws_log,
            "stories": rand,
        },
    )
    dump(
        "batch_report.json",
        {k: v for k, v in batch.items() if k != "runs"} | {"reverse_order_identical": batch_same},
    )
    dump("health_input_only.json", input_only)
    dump(
        "observations/u97_year2_payload.json",
        {
            "note": "u97 第 2 年年初的完整可见观察（标准产品许可）；last_feedback 是第 1 年的事实",
            **payload,
        },
    )
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])

    lines = [
        "# E1-5C：疾病、应急与恢复（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；全部数字来自合成样例 `configs/life/synthetic_e1_5c_health.yaml`，**不是医学结论、"
        "现实价格或工资，也不是城市排行**。年度计划是**分身的预设计划**（写在样例里），不是 LLM 的理解，也不是由医疗点数生成的。",
        "",
        "读表时分清：**月初病情**（决定这个月能不能上班、健康分）、**治疗**（付了钱、当月月末恢复，不补当月工资或学习）、"
        "**恢复**（治疗后或按病程自然恢复）、**钱**（同一本现金账）、**分数**（S = 0.8 × 日常生活分 B + 0.2 × H(月初状态)）。",
        "",
    ]
    for clone, title in STORIES.items():
        lines += [f"## {title}", ""]
        for row in stories[clone]:
            lines.append(
                f"* 第 {row['year']} 年：结算 {row['settlement']}，交接 {row['handoff']}；年末现金 {_fmt(row.get('cash_end'))}；"
                f"年度满足感 {row.get('S') if row.get('S') is None else round(row['S'], 6)}"
                + (f"；停止：{row['stop']['reason']}" if row.get("stop") else "")
                + (f"；出局：第 {row['exit']['month']} 月" if row.get("exit") else "")
            )
            sick = [
                x
                for x in row.get("health", [])
                if x["state"] != "healthy" or x["record"] != "lived"
            ]
            if sick:
                lines += [
                    "",
                    "  | 月 | 月初病情 | 第几个病月 | 病假 | 暂停 | 治疗 | 付款 | 月末 | 月末现金 |",
                    "  |---|---|---|---|---|---|---|---|---|",
                ]
                for x in sick:
                    cash = row["month_ends"][x["month"] - 1]
                    lines.append(
                        f"  | {x['month']} | {x['state']} | {x['episode_month']} | {'是' if x['sick_leave'] else '否'} | "
                        f"{'、'.join(x['paused']) or '—'} | {STATUS_LABEL.get(x['treatment'], '—') if x['treatment'] else '—'} | "
                        f"{_fmt(x['treatment_paid'])} | {x['transition']} | {_fmt(cash)} |"
                    )
                lines.append("")
        lines.append("")
    lines += [
        "## 对照与边界",
        "",
        f"* γ = 0 对照：每个月 S = B；第 1 年账目与主臂逐字节相同；u94 的第 2 年额度主臂 {gamma0['u94.a91.major']['next_quota_main'][0]}、"
        f"γ = 0 臂 {gamma0['u94.a91.major']['next_quota_gamma0'][0]}；主臂的额度拿到 γ = 0 臂里用被拒绝（{', '.join(p.code for p in crossed.problems)}）。",
        "* 无病对照：关掉新事件，十个分身第 1 年的账目、活动、资源与目标与 E1-5B 规则下的同一份生活逐字节相同（只多出健康记录）。",
        f"* 配对随机数：u91 在 A91 与 B92 共同抽签的 {len(paired)} 个月用同一个随机数；重放逐字节相同；内部抽签记录见 `paired_random.json`"
        "（不进观察）。随机臂的新发事件："
        + "；".join(f"{c} {v}" for c, v in events_random.items() if v),
        "* 随机臂里各分身的逐年结果："
        + "；".join(
            f"{c} " + "、".join(f"第 {r['year']} 年 {r['settlement']}" for r in rows)
            for c, rows in rand.items()
        )
        + "。预设的年度计划是为指定事件臂写的：随机臂里没有生病的人会提前学完课程或买下目标物品，之后年份的预设计划就不再"
        "合法（输入错误，什么都不提交；E1-7 的重试会让代理改计划）——这不是疾病机制的失败。",
        f"* 小批次：{json.dumps(batch['per_city'], ensure_ascii=False)}——按城市互斥计数，不算完成者平均。",
        "* 只改健康输入（10 → 55 点，六维比例不变）：结算与逐月分数都不变。",
        "",
        "## 手算核对",
        "",
    ]
    for name, ok, detail in checks:
        lines.append(f"- {'通过' if ok else '**不通过**'}　{name}：{detail}")
    lines += [
        "",
        "停止（应急安排不足、资料缺口）是**模拟器的支持边界**，不是现实中活不下去，也不是出局；正式重试在 E1-7，停止与出局怎样进入"
        "评价在 E1-6。期末仍未恢复的疾病怎样评价也留给 E1-6。",
        "",
    ]
    (args.out / "README.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (args.out / "checks.md").write_text(
        "\n".join(f"- {'通过' if ok else '不通过'}　{n}：{d}" for n, ok, d in checks) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for name, ok, detail in checks:
        print(f"- {'通过' if ok else '不通过'}　{name}：{detail}")
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
