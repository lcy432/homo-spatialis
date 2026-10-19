"""E1-4B 满足感 → 下一年月度精力额度的离线演示（`DECISIONS/0021`）。零大模型调用。

    uv run python scripts/demo_life_e1_4b.py --out runs/<新目录>

读 E1-4B 合成生活输入与探索评分配置，按预先声明的年度计划逐年做**真实的**年度交接：结算 → 评分器 → 反馈 →
下一年结算（不手填满足感分数），并输出：

* 主闭环（α = 0.10）三年：每年实际账目、评分、基础额度、更新前后额度、交接结果，第 4 年终态；
* 开关对照与 α 敏感性（关闭、α = 0、α = 0.05），从相同初始状态分别运行；关闭的分支停在哪里就停在哪里，
  不补出之后的生活；
* 机制检查：满意度下降、跨城差异、缺分阻断（及同一缺失下关闭反馈照常继续）、出局；
* 关闭反馈与 E1-4A 的逐项比较（预先声明允许不同的字段）；
* 标准产品观察、隐藏精力观察与文字理解对照的信息条件；
* r2 修复（DECISIONS/0021 第十三节）：同编号改政策内容被拒绝、不一致状态不构造观察（F1）；住近处与住远处
  两次真实试算不能互换账目、分数与候选状态（F2）；
* 小批次（演示层的薄循环，不是正式多分身 runner）：一个正常完成、一个反馈受阻、一个生活出局的分身放在一起跑，
  停止的分身不终止其他分身，报告保留全部预定分身与停止原因；"能执行—能完整评分—能更新精力"逐年对照表；
* 手算核对。

派生的对照臂配置原样存进运行目录，文件头写明改了什么。把配置副本、源码指纹、实际命令、协议与结果写进一个
**新的**运行目录——目录已存在就拒绝，不覆盖旧结果。

**合成设定：** 反馈强度、评分参数、负担系数与额度都是手工设定；对照只验证接线，不证明开启反馈让产品更准确，
也不排序城市。

退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入不合法。
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
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    ObservationPermission,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    FEEDBACK_HANDOFF_REVISION,
    RESOURCE_RULES_REVISION,
    LifeContractError,
    LifeProtocol,
    SettlementResult,
    YearAction,
    to_fraction,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_settlement import SETTLEMENT_CONVENTION, binding_from_policy, settle_year
from econ.life_year import ScoringInputs, run_life_year, settle_and_score
from ledger.life_catalog import LifeBundle, cross_validate, load_life_bundle, parse_bundle
from ledger.life_scoring_config import load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_4b.yaml")
SCORING = Path("configs/life/scoring_e1_4b.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_feedback_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_resources.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/econ/life_feedback.py",
    "src/econ/life_year.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_4b.py",
    "tests/life/test_feedback.py",
    "tests/life/test_feedback_wiring.py",
    "tests/life/test_feedback_bindings.py",
    "tests/life/test_feedback_batch.py",
    "tests/life/feedback_support.py",
)
#: 对照臂：由主配置只改反馈政策派生。
ARMS: dict[str, dict[str, Any]] = {
    "main_alpha_0.10": {},
    "off": {"mode": "disabled", "policy_id": "feedback.e1-4b.off"},
    "alpha_0": {"alpha": 0.0, "policy_id": "feedback.e1-4b.alpha0"},
    "alpha_0.05": {"alpha": 0.05, "policy_id": "feedback.e1-4b.alpha005"},
}
#: 关闭反馈与 E1-4A 比较时预先声明允许不同的字段。
ALLOWED_OUTCOME_DIFF = ("notes",)
ALLOWED_STATE_DIFF = ("energy_quota_basis",)
F = Fraction


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
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "resource_rules_revision": RESOURCE_RULES_REVISION,
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _arm(raw: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(raw)
    data["protocol"]["energy_feedback"].update(changes)
    return data


def _revise(model: Any, **changes: Any) -> Any:
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


def _same_id(protocol: LifeProtocol, **changes: Any) -> LifeProtocol:
    """编号与版本不变、只改政策内容的协议（独立审核 F1 的构造）。"""
    return _revise(protocol, energy_feedback=_revise(protocol.energy_feedback, **changes))


def _far_plan(plan: YearAction) -> YearAction:
    """同一份计划，只把 A61 的近处住房换成远处住房（同人、同城、同年的另一次真实试算）。"""
    items = tuple(
        _revise(i, opportunity_id="a61.housing.far")
        if i.opportunity_id == "a61.housing.near"
        else i
        for i in plan.items
    )
    return _revise(plan, items=items)


def _as_e1_4a(raw: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(raw)
    data["protocol"]["life_rules_version"] = "e1-4a-monthly-v1"
    data["protocol"].pop("energy_feedback")
    for s in data["initial_states"]:
        s.pop("energy_quota_basis")
    return data


class World:
    def __init__(self, bundle: LifeBundle, scoring: Any) -> None:
        self.b, self.scoring = bundle, scoring

    def inputs(self, state: Any) -> ScoringInputs:
        profile = next(p for p in self.b.stated_profiles if p.person_id == state.person_id)
        return ScoringInputs(
            spec=self.scoring.spec,
            conditions=self.scoring.conditions_for(str(state.city)),
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )

    def city_of(self, clone: str) -> str:
        state = self.b.initial_state(clone)
        assert state is not None
        return str(state.city)

    def run(
        self, protocol: LifeProtocol, clone: str, *, with_scoring: bool = True
    ) -> list[LifeYearResult]:
        state = self.b.initial_state(clone)
        assert state is not None
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        tax = binding_from_policy(self.b.tax_policy(catalog.tax_policy_id))  # type: ignore[arg-type]
        out: list[LifeYearResult] = []
        for plan in self.b.plans_for(clone):
            r = run_life_year(
                protocol=protocol,
                state=state,
                action=plan,
                catalog=catalog,
                tax=tax,
                scoring=self.inputs(state) if with_scoring else None,
            )
            out.append(r)
            if r.handoff != "next_year_ready" or r.next_state is None:
                break
            state = r.next_state
        return out


def _settle_chain(bundle: LifeBundle, clone: str) -> list[SettlementResult]:
    state = bundle.initial_state(clone)
    assert state is not None
    catalog = bundle.catalog_for(str(state.city))
    assert catalog is not None
    tax = binding_from_policy(bundle.tax_policy(catalog.tax_policy_id))  # type: ignore[arg-type]
    out: list[SettlementResult] = []
    for plan in bundle.plans_for(clone):
        r = settle_year(
            protocol=bundle.protocol, state=state, action=plan, catalog=catalog, tax=tax
        )
        out.append(r)
        if r.status != "completed" or r.next_state is None or r.next_state.status != "active":
            break
        state = r.next_state
    return out


def _dec(q: Fraction | str | None) -> str | None:
    """精确有理数的十进制写法：有限小数原样写全（不舍入），否则写分数。"""
    if q is None:
        return None
    q = Fraction(q)
    d, n2, n5 = q.denominator, 0, 0
    while d % 2 == 0:
        d, n2 = d // 2, n2 + 1
    while d % 5 == 0:
        d, n5 = d // 5, n5 + 1
    if d != 1:
        return f"{q.numerator}/{q.denominator}"
    n = max(n2, n5)
    digits = str(abs(q.numerator * 10**n // q.denominator)).rjust(n + 1, "0")
    text = f"{digits[:-n]}.{digits[-n:]}" if n else digits
    return f"-{text}" if q < 0 else text


def _money(values: Any) -> str:
    return _dec(sum((to_fraction(v.amount) for v in values), Fraction(0))) or "0"


def _year_row(r: LifeYearResult) -> dict[str, Any]:
    out = r.settlement.outcome
    f = r.feedback
    months = out.resources.months if out is not None and out.resources is not None else ()
    ledger = out.months if out is not None else ()
    used = [m.energy_used.fraction() for m in months if m.energy_used is not None]
    left_e = [m.energy_remaining.fraction() for m in months if m.energy_remaining is not None]
    left_t = [m.time_remaining.fraction() for m in months if m.time_remaining is not None]
    return {
        "year": r.year_index,
        "settlement": r.settlement.status,
        "problems": [(p.code, p.month) for p in r.settlement.problems],
        "problem_messages": [
            f"{p.code}（第 {p.month} 月）：{p.message}" for p in r.settlement.problems
        ],
        "ledger": None
        if out is None
        else {
            "money_basis": out.money_basis_id,
            "cash_start": _dec(to_fraction(out.cash_start.amount)),
            "income_gross": _money(m.income_gross for m in ledger),
            "income_tax": _money(m.income_tax for m in ledger),
            "spent": _money(m.spent for m in ledger),
            "cash_end": _dec(to_fraction(out.cash_end.amount)) if out.cash_end else None,
            "months_settled": len(ledger),
            "items": [f"{x.opportunity_id}：{x.status}" for x in out.items],
            "exit": None if out.exit is None else out.exit.model_dump(mode="json"),
        },
        "quota_used_this_year": sorted({_dec(m.energy_quota.fraction()) for m in months}),
        "max_monthly_burden": _dec(max(used)) if used else None,
        "min_energy_remaining": _dec(min(left_e)) if left_e else None,
        "min_time_remaining_hours": _dec(min(left_t)) if left_t else None,
        "score_status": r.score.annual_status if r.score else None,
        "score_mean": r.score.annual_mean if r.score else None,
        "score_monthly_u": [m.u for m in r.score.months] if r.score else None,
        "score_problem": r.score_problem,
        "decision": f.decision,
        "base": _dec(f.base_quota.fraction()),
        "previous": _dec(f.previous_quota.fraction()),
        "score_decimal": f.score_decimal,
        "raw": _dec(f.raw_quota.fraction()) if f.raw_quota else None,
        "new": _dec(f.new_quota.fraction()) if f.new_quota else None,
        "handoff": r.handoff,
        "next_year_quota": _dec(to_fraction(r.next_state.energy.level))
        if r.next_state and r.next_state.status == "active"
        else None,
        "reasons": list(f.reasons),
        "notes": list(r.notes),
    }


LEDGER_HEAD = "\n".join(
    [
        "| 年 | 结算 | 年初现金 | 税前收入 | 所得税 | 支出 | 年末现金 | 本年每月额度 | 月最大负担 "
        "| 月最少剩余额度 | 月最少剩余时间（小时） |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
)
FEEDBACK_HEAD = "\n".join(
    [
        "| 年 | 年度满足感 S（状态） | 反馈决定 | 基础额度 | 本年额度 | 未量化 E_raw | 下一年额度 | 交接 |",
        "|---|---|---|---|---|---|---|---|",
    ]
)


def _fmt_ledger(row: dict[str, Any]) -> str:
    g = row["ledger"]
    if g is None:
        cells = ["—"] * 5
    else:
        cells = [
            g["cash_start"],
            g["income_gross"],
            g["income_tax"],
            g["spent"],
            g["cash_end"] or "—",
        ]
    q = "、".join(row["quota_used_this_year"]) or "—"
    rest = [row["max_monthly_burden"], row["min_energy_remaining"], row["min_time_remaining_hours"]]
    return (
        "| "
        + " | ".join([str(row["year"]), row["settlement"], *cells, q, *(_show(x) for x in rest)])
        + " |"
    )


def _show(x: str | None) -> str:
    """表格里的写法：无限小数的分数后面附两位小数的近似值，只供阅读。"""
    if x is None:
        return "—"
    return f"{x}（≈{float(Fraction(x)):.2f}）" if "/" in x else x


def _fmt_feedback(row: dict[str, Any]) -> str:
    s = row["score_decimal"] or (None if row["score_mean"] is None else repr(row["score_mean"]))
    status = row["score_status"] or (row["score_problem"] or "未评分")
    q = "、".join(row["quota_used_this_year"]) or "—"
    cells = [
        str(row["year"]),
        f"{s or '—'}（{status}）",
        row["decision"],
        row["base"],
        q,
        row["raw"] or "—",
        row["next_year_quota"] or "—",
        row["handoff"],
    ]
    return "| " + " | ".join(cells) + " |"


def _compare_off_with_a(
    off_runs: list[LifeYearResult], a_runs: list[SettlementResult]
) -> dict[str, Any]:
    diffs: list[str] = []
    observed: list[dict[str, Any]] = []
    if [r.settlement.status for r in off_runs] != [r.status for r in a_runs]:
        diffs.append("结算状态序列不同")
    for b, a in zip(off_runs, a_runs, strict=False):
        if [p.model_dump() for p in b.settlement.problems] != [p.model_dump() for p in a.problems]:
            diffs.append(f"第 {b.year_index} 年问题清单不同")
        if a.outcome is None or b.settlement.outcome is None:
            continue
        bo = b.settlement.outcome.model_dump(mode="json")
        ao = a.outcome.model_dump(mode="json")
        for k in sorted(set(ao) | set(bo)):
            if ao.get(k) != bo.get(k):
                observed.append(
                    {
                        "year": b.year_index,
                        "object": "outcome",
                        "field": k,
                        "e1_4a": ao.get(k),
                        "e1_4b_off": bo.get(k),
                    }
                )
                if k not in ALLOWED_OUTCOME_DIFF:
                    diffs.append(f"第 {b.year_index} 年结果字段 {k} 不同")
        nb = (b.next_state or b.settlement.next_state).model_dump(mode="json")  # type: ignore[union-attr]
        na = a.next_state.model_dump(mode="json")  # type: ignore[union-attr]
        for k in sorted(set(na) | set(nb)):
            if na.get(k) != nb.get(k):
                observed.append(
                    {
                        "year": b.year_index,
                        "object": "next_state",
                        "field": k,
                        "e1_4a": na.get(k),
                        "e1_4b_off": nb.get(k),
                    }
                )
                if k not in ALLOWED_STATE_DIFF:
                    diffs.append(f"第 {b.year_index} 年下一状态字段 {k} 不同")
    return {
        "allowed_outcome_diff": list(ALLOWED_OUTCOME_DIFF),
        "allowed_state_diff": list(ALLOWED_STATE_DIFF),
        "observed_differences": observed,
        "unexpected_differences": diffs,
    }


# ==========================================================================
# 小批次：演示层的薄循环（不是正式多分身 runner；正式调度、重试与异常隔离在 E1-7）
# ==========================================================================

#: 停止原因的互斥分类（每个预定分身恰好一类，合计等于原计划人数）。
STOP_CATEGORIES: tuple[str, ...] = (
    "completed",  # 正常到达终态：完成全部行动年
    "exited",  # 生活出局：按模型规则付不起最低住房与食品
    "feedback_blocked",  # 反馈信息不足：已结算，但下一年额度定不下来
    "plan_infeasible",  # 计划不可行：违反资金、时间或精力约束（重试留 E1-7）
    "input_or_data_error",  # 输入或资料错误：不记为生活失败
    "unexpected_error",  # 意外程序错误：显式报告，批次停止
    "not_run",  # 批次在意外程序错误之后停止，没有运行
)
CATEGORY_LABEL = {
    "completed": "正常完成",
    "exited": "生活出局",
    "feedback_blocked": "反馈信息不足",
    "plan_infeasible": "计划不可行",
    "input_or_data_error": "输入或资料错误",
    "unexpected_error": "意外程序错误",
    "not_run": "未运行（批次已停止）",
}


def _year_capability(r: LifeYearResult) -> dict[str, Any]:
    """一年里三件事分开说：生活能否执行、满足感能否完整评分、下一年精力能否确定。"""
    s, f, score = r.settlement, r.feedback, r.score
    if s.status == "completed":
        execute = "能执行"
    elif s.status == "exited":
        execute = (
            f"执行到出局（第 {s.outcome.exit.month} 月）"
            if s.outcome and s.outcome.exit
            else "出局"
        )
    elif s.status == "plan_infeasible":
        execute = "不能执行（计划不可行）"
    else:
        execute = f"没有执行（{s.status}）"
    if score is None:
        scored = f"未评分（{r.score_problem}）" if r.score_problem else "未评分（没有生活结果）"
    elif score.annual_status == "complete":
        scored = "完整"
    elif score.annual_status == "exited":
        scored = f"出局年份单列（已评第 {list(score.months_scored)} 月）"
    else:
        gaps = sorted({x for m in score.months if m.status != "scored" for x in m.reasons})
        scored = "不完整：" + "；".join(gaps)
    update = {
        "applied": f"已确定：{_dec(f.new_quota.fraction()) if f.new_quota else ''} 负担点",
        "off_disabled": "不需要（关闭反馈，基础额度）",
        "off_alpha_zero": "不需要（α = 0，基础额度）",
        "blocked": "不能确定（反馈信息不足）",
        "no_next_action_year": "不需要（下一年是终态）",
        "exited": "不适用（出局）",
        "settlement_failed": "不能确定（这一年没有生活结果）",
    }[f.decision]
    return {
        "year": r.year_index,
        "settlement": s.status,
        "score_status": score.annual_status if score else None,
        "feedback_decision": f.decision,
        "handoff": r.handoff,
        "can_execute": execute,
        "can_score_completely": scored,
        "can_update_energy": update,
    }


def classify_clone(
    clone: str, city: str, runs: list[LifeYearResult], has_next_plan: bool
) -> dict[str, Any]:
    """一个分身的最终状态与停止原因。不补停止之后的年份。"""
    rec: dict[str, Any] = {"clone_id": clone, "city": city, "years_recorded": len(runs)}
    rec["years"] = [_year_capability(r) for r in runs]
    if not runs:
        return {**rec, "category": "input_or_data_error", "stop_reason": "没有预先声明的年度计划"}
    last = runs[-1]
    committed = [r.year_index for r in runs if r.settlement.status in ("completed", "exited")]
    rec["last_settled_year"] = committed[-1] if committed else None
    rec["stop_year"] = last.year_index
    complete = sum(1 for r in runs if r.score is not None and r.score.annual_status == "complete")
    rec["years_scored_completely"] = complete
    if last.handoff == "terminal":
        cat = "completed"
        why = f"完成全部 {len(runs)} 个行动年，进入终态（其中满足感完整的年份：{complete} 个）"
    elif last.handoff == "exited":
        ex = last.settlement.outcome.exit  # type: ignore[union-attr]
        rec["exit"] = {"year": ex.year_index, "month": ex.month, "reason": ex.reason}
        cat, why = (
            "exited",
            f"第 {ex.year_index} 年第 {ex.month} 月付不起最低住房与食品；出局事实与已发生的生活保留",
        )
    elif last.handoff == "feedback_blocked":
        cat = "feedback_blocked"
        why = (
            f"第 {last.year_index} 年已结算并保留；第 {last.year_index + 1} 年额度无法确定："
            + "；".join(last.feedback.reasons[:1])
        )
    elif last.handoff == "settlement_failed":
        status = last.settlement.status
        probs = [
            f"{p.code}（第 {p.month} 月）" if p.month else p.code for p in last.settlement.problems
        ]
        rec["problems"] = probs
        cat = "plan_infeasible" if status == "plan_infeasible" else "input_or_data_error"
        why = f"第 {last.year_index} 年试算 {status}：{'、'.join(probs)}；这一年没有生活账目，之前的年份保留"
    elif last.handoff == "next_year_ready" and not has_next_plan:
        cat, why = "input_or_data_error", f"第 {last.year_index + 1} 年没有预先声明的计划"
    else:  # pragma: no cover - 循环只在上面几种结果处停
        raise AssertionError(f"{clone}: 意外的交接结果 {last.handoff}")
    return {**rec, "category": cat, "stop_reason": why}


def run_batch(
    world: World, protocol: LifeProtocol, clones: list[str], *, runner: Any = None
) -> dict[str, Any]:
    """按顺序跑一组分身。预期的停止（出局、反馈信息不足、计划不可行、输入或资料错误）只停这个分身；
    意外的程序错误显式记下并**停止整个批次**——不假定它只影响一个分身，也不吞掉后当正常缺失。"""
    runner = runner or world.run
    records: list[dict[str, Any]] = []
    runs: dict[str, list[LifeYearResult]] = {}
    status = "finished"
    for i, clone in enumerate(clones):
        city = world.city_of(clone)
        try:
            rs = runner(protocol, clone)
        except Exception as exc:  # 只为显式报告；下面立即停止批次，不继续跑其他分身
            records.append(
                {
                    "clone_id": clone,
                    "city": city,
                    "category": "unexpected_error",
                    "stop_reason": f"{type(exc).__name__}: {exc}"[:400],
                    "years_recorded": 0,
                    "years": [],
                }
            )
            for rest in clones[i + 1 :]:
                records.append(
                    {
                        "clone_id": rest,
                        "city": world.city_of(rest),
                        "category": "not_run",
                        "stop_reason": f"批次在 {clone} 的意外程序错误之后停止：不假定错误只影响一个分身",
                        "years_recorded": 0,
                        "years": [],
                    }
                )
            status = "stopped_unexpected_error"
            break
        runs[clone] = rs
        planned_years = {p.year_index for p in world.b.plans_for(clone)}
        has_next = bool(rs) and (rs[-1].year_index + 1) in planned_years
        records.append(classify_clone(clone, city, rs, has_next))
    cities: dict[str, dict[str, int]] = {}
    for rec in records:
        row = cities.setdefault(rec["city"], {"planned": 0, **{c: 0 for c in STOP_CATEGORIES}})
        row["planned"] += 1
        row[rec["category"]] += 1
    for row in cities.values():
        assert row["planned"] == sum(
            row[c] for c in STOP_CATEGORIES
        )  # 分类互斥、能与原计划人数核对
    if status == "finished":
        done = all(r["category"] == "completed" for r in records)
        status = "all_completed" if done else "finished_partially_complete"
    return {
        "protocol_id": protocol.protocol_id,
        "feedback_policy_id": protocol.energy_feedback.policy_id
        if protocol.energy_feedback
        else None,
        "batch_status": status,
        "planned_clones": clones,
        "per_city": cities,
        "clones": records,
        "runs": runs,
    }


BATCH_NOTE = (
    "这是执行状态与完整程度的报告，不是结论：停止的分身保留在原计划人数里，没有补充新分身顶替；"
    "完成者的数字不能当作整座城市的平均；缺分不记成零；本报告不计算分数、阈值或城市排名。"
    "生活出局、部分轨迹与模型缺失怎样进入三年期评价，留到 E1-6 在正式比较之前确定。"
)


def batch_markdown(title: str, batch: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"批次状态：`{batch['batch_status']}`（协议 {batch['protocol_id']}，反馈政策 {batch['feedback_policy_id']}）。"
        + BATCH_NOTE,
        "",
        "| 城市 | 原计划 | " + " | ".join(CATEGORY_LABEL[c] for c in STOP_CATEGORIES) + " |",
        "|---|---|" + "---|" * len(STOP_CATEGORIES),
    ]
    for city, row in batch["per_city"].items():
        lines.append(
            f"| {city} | {row['planned']} | "
            + " | ".join(str(row[c]) for c in STOP_CATEGORIES)
            + " |"
        )
    lines += ["", "| 分身 | 最终状态 | 记录的年份 | 停止原因 |", "|---|---|---|---|"]
    for rec in batch["clones"]:
        lines.append(
            f"| {rec['clone_id']} | {CATEGORY_LABEL[rec['category']]} | {rec['years_recorded']} | {rec['stop_reason']} |"
        )
    lines += [
        "",
        "“正常完成”只表示完成了全部行动年，不表示满足感完整：能否完整评分逐年见下表。",
        "",
        "逐年三件事（生活能否执行 / 满足感能否完整评分 / 下一年精力能否确定）：",
        "",
        "| 分身 | 年 | 能执行吗 | 能完整评分吗 | 能更新精力吗 |",
        "|---|---|---|---|---|",
    ]
    for rec in batch["clones"]:
        for y in rec["years"]:
            lines.append(
                f"| {rec['clone_id']} | {y['year']} | {y['can_execute']} | {y['can_score_completely']} | {y['can_update_energy']} |"
            )
    return [*lines, ""]


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-4B 年度反馈的离线演示（不评城市、不排序）")
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
    raw = yaml.safe_load(LIFE.read_text(encoding="utf-8"))

    arms_raw = {name: _arm(raw, changes) for name, changes in ARMS.items()}
    arms: dict[str, LifeProtocol] = {}
    for name, data in arms_raw.items():
        b = parse_bundle(data)
        errors = cross_validate(b).errors
        if errors:
            print(f"对照臂 {name} 的配置不合法：{errors}")
            return 1
        arms[name] = b.protocol
    world = World(bundle, scoring)

    main_runs = {name: world.run(protocol, "u31.a61.c0") for name, protocol in arms.items()}
    off_noscore = world.run(arms["off"], "u31.a61.c0", with_scoring=False)
    cross = world.run(arms["main_alpha_0.10"], "u31.b62.c0")
    down = world.run(arms["main_alpha_0.10"], "u31.a61.down")
    gap_on = world.run(arms["main_alpha_0.10"], "u33.a61.c0")
    gap_off = world.run(arms["off"], "u33.a61.c0")
    exit_on = world.run(arms["main_alpha_0.10"], "u34.a61.c0")
    a_bundle = parse_bundle(_as_e1_4a(raw))
    equivalence = {
        clone: _compare_off_with_a(
            world.run(arms["off"], clone, with_scoring=False), _settle_chain(a_bundle, clone)
        )
        for clone in ("u31.a61.c0", "u31.a61.down", "u33.a61.c0", "u34.a61.c0")
    }

    # ---------------------------------------------------------------- 观察（信息条件）
    year1 = main_runs["main_alpha_0.10"][0]
    state2 = year1.next_state
    assert state2 is not None
    profile = next(p for p in bundle.stated_profiles if p.person_id == "u31")
    catalog = bundle.catalog_for(str(state2.city))
    assert catalog is not None
    pv = bundle.protocol.info_permission_version

    def observe(
        permission: ObservationPermission, state: Any, policy: Any, fb: Any
    ) -> dict[str, Any]:
        obs = build_observation(
            permission=permission,
            profile=profile,
            state=state,
            catalog=catalog,  # type: ignore[arg-type]
            last_feedback=fb,
            resource_rules=bundle.protocol.resource_rules,
            energy_feedback=policy,
        )
        return observation_payload(obs)

    hidden_perm = ObservationPermission(
        **{**standard_product_permission(pv).model_dump(), "show_energy_level": False}
    )
    research_perm = ObservationPermission(
        condition="research_text_only",
        show_stated_dimension_points=False,
        show_soft_preferences=False,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=False,
        show_stated_material_points=False,
        permission_version=pv,
    )
    fb1 = visible_feedback(year1.settlement)
    payload_on = observe(
        standard_product_permission(pv), state2, arms["main_alpha_0.10"].energy_feedback, fb1
    )
    payload_hidden = observe(hidden_perm, state2, arms["main_alpha_0.10"].energy_feedback, fb1)
    off_state2 = main_runs["off"][0].next_state
    payload_research_off = observe(
        research_perm,
        off_state2,
        arms["off"].energy_feedback,
        visible_feedback(main_runs["off"][0].settlement),
    )
    research_on_refused = False
    try:
        observe(research_perm, state2, arms["main_alpha_0.10"].energy_feedback, fb1)
    except LifeContractError:
        research_on_refused = True
    channels = {
        "standard_product_on": payload_on["channels"],
        "product_energy_hidden_on": payload_hidden["channels"],
        "research_text_only_off": payload_research_off["channels"],
        "research_text_only_on_refused": research_on_refused,
    }

    # ---------------------------------------------------------------- r2：F1 政策内容绑定
    main_protocol = arms["main_alpha_0.10"]
    plan2 = next(p for p in bundle.plans_for("u31.a61.c0") if p.year_index == 2)
    tax = binding_from_policy(bundle.tax_policy(catalog.tax_policy_id))  # type: ignore[arg-type]
    policy_binding: dict[str, Any] = {
        "content_fingerprints": {n: p.energy_feedback.content_fingerprint for n, p in arms.items()},  # type: ignore[union-attr]
        "boosted_state": {
            "year": state2.year_index,
            "quota": state2.energy.level,
            "policy_fingerprint": state2.energy_quota_basis.policy_fingerprint,  # type: ignore[union-attr]
        },
        "same_id_changes": {},
    }
    for label, changes in (
        ("mode_disabled", {"mode": "disabled"}),
        ("alpha_0.09", {"alpha": 0.09}),
    ):
        altered = _same_id(main_protocol, **changes)
        r = settle_year(protocol=altered, state=state2, action=plan2, catalog=catalog, tax=tax)  # type: ignore[arg-type]
        try:
            observe(research_perm, state2, altered.energy_feedback, None)
            refused = None
        except LifeContractError as exc:
            refused = str(exc)[:300]
        policy_binding["same_id_changes"][label] = {
            "changes": changes,
            "policy_id_unchanged": altered.energy_feedback.policy_id
            == main_protocol.energy_feedback.policy_id,  # type: ignore[union-attr]
            "content_fingerprint": altered.energy_feedback.content_fingerprint,  # type: ignore[union-attr]
            "settlement_status": r.status,
            "problem_codes": [p.code for p in r.problems],
            "observation_refused": refused,
        }

    # ---------------------------------------------------------------- r2：F2 同一次生活绑定
    start1 = bundle.initial_state("u31.a61.c0")
    plan1 = next(p for p in bundle.plans_for("u31.a61.c0") if p.year_index == 1)
    common_args = dict(
        protocol=main_protocol, state=start1, catalog=catalog, tax=tax, scoring=world.inputs(start1)
    )
    near = settle_and_score(action=plan1, **common_args)  # type: ignore[arg-type]
    far = settle_and_score(action=_far_plan(plan1), **common_args)  # type: ignore[arg-type]
    near_rec = feedback_record(protocol=main_protocol, year=near)
    far_rec = feedback_record(protocol=main_protocol, year=far)
    attempts: dict[str, str | None] = {}
    for label, attempt in (
        (
            "far_ledger_with_near_score",
            lambda: feedback_record(
                protocol=main_protocol, year=far.model_copy(update={"score": near.score})
            ),
        ),
        (
            "near_record_on_far_candidate",
            lambda: apply_feedback(far.settlement.next_state, near_rec),
        ),  # type: ignore[arg-type]
        (
            "split_arguments_r1_style",
            lambda: feedback_record(
                protocol=main_protocol, start=start1, settlement=far.settlement, score=near.score
            ),
        ),
    ):
        try:
            attempt()
            attempts[label] = None
        except LifeContractError as exc:
            attempts[label] = str(exc)[:300]
    year_binding = {
        "near": {
            "score": near.score.annual_mean if near.score else None,
            "cash_end": near.settlement.outcome.cash_end.amount
            if near.settlement.outcome
            else None,  # type: ignore[union-attr]
            "next_quota": apply_feedback(near.settlement.next_state, near_rec).energy.level,  # type: ignore[arg-type]
            "binding_fingerprint": near.binding_fingerprint,
        },
        "far": {
            "score": far.score.annual_mean if far.score else None,
            "cash_end": far.settlement.outcome.cash_end.amount if far.settlement.outcome else None,  # type: ignore[union-attr]
            "next_quota": apply_feedback(far.settlement.next_state, far_rec).energy.level,  # type: ignore[arg-type]
            "binding_fingerprint": far.binding_fingerprint,
        },
        "mismatch_attempts_refused": attempts,
    }

    # ---------------------------------------------------------------- 小批次
    batch_clones = ["u31.a61.c0", "u33.a61.c0", "u34.a61.c0"]
    batches = {
        "main_alpha_0.10": run_batch(world, main_protocol, batch_clones),
        "off": run_batch(world, arms["off"], batch_clones),
    }
    alone = {name: {c: world.run(arms[name], c) for c in batch_clones} for name in batches}
    same_alone = all(
        [r.model_dump() for r in batches[name]["runs"][c]]
        == [r.model_dump() for r in alone[name][c]]
        for name in batches
        for c in batch_clones
    )

    # ---------------------------------------------------------------- 手算核对
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    m = main_runs["main_alpha_0.10"]
    q1 = {x.energy_quota.fraction() for x in m[0].settlement.outcome.resources.months}  # type: ignore[union-attr]
    check(
        "第一年用基础额度",
        q1 == {250},
        "u31 在 A61 第 1 年 12 个月都是 250 负担点（没有虚构的第零年满意度）",
    )
    s1 = m[0].score.annual_mean if m[0].score else None
    check(
        "第一年满足感与下一年额度",
        s1 == 0.5604166666666667 and m[0].feedback.new_quota.fraction() == F(26401, 100),  # type: ignore[union-attr]
        "S₁ = 0.5604166666666667；250 × (1 + 0.10 × S₁) = 264.0104… → 向下保留到 0.01 得 264.01",
    )
    q2 = {x.energy_quota.fraction() for x in m[1].settlement.outcome.resources.months}  # type: ignore[union-attr]
    check(
        "下一年 12 个月同一额度并被结算使用",
        q2 == {F(26401, 100)} and m[1].settlement.status == "completed",
        "第 2 年每月额度 264.01；负担 255（含夜校 48）≤ 264.01，可行",
    )
    check(
        "不复利",
        m[1].next_state is not None and m[1].next_state.energy.level == 264.01,
        "第 2 年同样的生活、同样的 S → 第 3 年仍是 264.01，不从 264.01 再乘一次",
    )
    check(
        "终态不计算加成",
        len(m) == 3
        and m[2].handoff == "terminal"
        and m[2].feedback.decision == "no_next_action_year",
        "第 3 年照常评分（S ≈ 0.4927），下一年是终态，不为不存在的行动年计算加成",
    )
    off = main_runs["off"]
    check(
        "开关对照",
        m[0].settlement.outcome == off[0].settlement.outcome
        and off[0].next_state is not None
        and off[0].next_state.energy.level == 250
        and off[1].settlement.status == "plan_infeasible"
        and len(off) == 2,
        "第 1 年生活结果逐项相同；第 2 年额度 264.01 vs 250；负担 255 的计划开启可行、关闭不可行（停在第 2 年）",
    )
    a05 = main_runs["alpha_0.05"]
    a0 = main_runs["alpha_0"]
    check(
        "α 敏感性",
        a05[0].next_state is not None
        and a05[0].next_state.energy.level == 257.0
        and a05[1].settlement.status == "completed"
        and a0[0].next_state is not None
        and a0[0].next_state.energy.level == 250
        and a0[1].settlement.status == "plan_infeasible",
        "α = 0 → 250（第 2 年不可行）；α = 0.05 → 250 × (1 + 0.05 × S₁) = 257.005… → 257.00（可行）；α = 0.10 → 264.01",
    )
    check(
        "满意度下降后加成减少",
        len(down) == 3
        and down[1].next_state is not None
        and down[1].next_state.energy.level == 262.31
        and down[0].next_state is not None
        and down[0].next_state.energy.level == 264.01,
        "第 2 年住远处、吃基本食品：S₂ ≈ 0.492654 → 250 × (1 + 0.1 × S₂) = 262.316… → 262.31（< 264.01，≥ 250）",
    )
    check(
        "跨城基础相同、额度可以不同",
        cross[0].next_state is not None and cross[0].next_state.energy.level == 264.73,
        "B62 第 1 年 S ≈ 0.589205 → 264.73；A61 是 264.01；两城基础都是 250",
    )
    check(
        "缺分阻断反馈、关闭照常",
        len(gap_on) == 1
        and gap_on[0].handoff == "feedback_blocked"
        and gap_on[0].settlement.status == "completed"
        and [r.handoff for r in gap_off] == ["next_year_ready", "next_year_ready", "terminal"],
        "u33 给家务点了 20 点而规格没有家务分项：开启时第 1 年结算保留、第 2 年额度无法确定；关闭时三年照常，额度 250",
    )
    check(
        "出局不更新",
        len(exit_on) == 1
        and exit_on[0].handoff == "exited"
        and exit_on[0].feedback.decision == "exited",
        "u34 第 4 月付不起最低生活出局：不更新精力、不复活",
    )
    check(
        "关闭反馈复现 E1-4A",
        all(not v["unexpected_differences"] for v in equivalence.values()),
        f"四个分身逐年比较账目、问题、结果与下一状态，只允许 {list(ALLOWED_OUTCOME_DIFF + ALLOWED_STATE_DIFF)} 不同",
    )
    check(
        "关闭臂不需要评分",
        off_noscore[0].handoff == "next_year_ready" and off_noscore[0].score is None,
        "没有评分输入，关闭反馈照样交接到第 2 年（额度 250）",
    )
    text_on = json.dumps(payload_on, ensure_ascii=False)
    check(
        "产品观察与信息条件",
        payload_on["energy_level"] == 264.01
        and "annual_mean" not in text_on
        and "0.5604" not in text_on
        and payload_on["channels"]["study_condition"] == "dynamic_life_feedback"
        and payload_hidden["channels"]["study_condition"] == "dynamic_life_feedback"
        and payload_hidden["energy_level"] is None
        and payload_research_off["channels"]["study_condition"] == "static_text_understanding"
        and research_on_refused,
        "标准产品看到本年额度 264.01 与单位、不看到分数；隐藏精力仍是含生活反馈的动态条件；文字理解对照必须关闭反馈",
    )
    again = world.run(arms["main_alpha_0.10"], "u31.a61.c0")
    check(
        "确定性重放",
        [r.model_dump() for r in again] == [r.model_dump() for r in m],
        "同一输入再跑一遍，三年的交接结果逐字相同",
    )
    changes = policy_binding["same_id_changes"]
    check(
        "r2 政策内容绑定（F1）",
        all(
            v["policy_id_unchanged"]
            and v["settlement_status"] == "input_error"
            and "feedback_policy_content_changed" in v["problem_codes"]
            and v["observation_refused"]
            for v in changes.values()
        )
        and "energy_quota_not_base" in changes["mode_disabled"]["problem_codes"],
        "第 2 年带着 264.01 的状态，政策编号与版本不变、改成关闭或 α = 0.09：结算是输入错误（不是生活不可行），"
        "不沿用旧加成也不静默恢复 250；不为它构造观察，不会被标成静态文字理解条件",
    )
    check(
        "r2 同一次生活绑定（F2）",
        year_binding["near"]["next_quota"] == 264.01
        and year_binding["far"]["next_quota"] == 263.49
        and year_binding["near"]["score"] == 0.5604166666666667
        and year_binding["far"]["score"] == 0.5399038461538461
        and all(v is not None for v in attempts.values()),
        "住近处 S = 0.5604166666666667 → 264.01，住远处 S = 0.5399038461538461 → 263.49；"
        "远房账目配近房分数、近房记录写到远房候选状态、r1 式分别传参，三种都被拒绝",
    )
    bm = batches["main_alpha_0.10"]
    cats = {r["clone_id"]: r["category"] for r in bm["clones"]}
    check(
        "小批次：停止的分身不终止其他分身",
        cats
        == {"u31.a61.c0": "completed", "u33.a61.c0": "feedback_blocked", "u34.a61.c0": "exited"}
        and bm["batch_status"] == "finished_partially_complete"
        and [len(bm["runs"][c]) for c in batch_clones] == [3, 1, 1]
        and bm["per_city"]["US:SYNTH2026:A61"]["planned"] == 3
        and same_alone,
        "主臂三个分身：u31 完成三年、u33 第 1 年后反馈信息不足、u34 第 1 年第 4 月出局；三人都在报告里，"
        "u31 不受另两人影响；不补停止之后的年份；每人单独运行与在批次里运行的结果逐字相同",
    )
    bo = batches["off"]
    cats_off = {r["clone_id"]: r["category"] for r in bo["clones"]}
    check(
        "小批次（关闭反馈）",
        cats_off
        == {"u31.a61.c0": "plan_infeasible", "u33.a61.c0": "completed", "u34.a61.c0": "exited"},
        "关闭反馈的同一批人：u31 第 2 年每月负担 255 > 250 计划不可行（第 1 年保留），u33 照常完成三年，u34 出局",
    )

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg = args.out / "configs"
    (cfg / "arms").mkdir(parents=True)
    shutil.copyfile(LIFE, cfg / LIFE.name)
    shutil.copyfile(SCORING, cfg / SCORING.name)
    for name, data in arms_raw.items():
        if not ARMS[name]:
            continue
        (cfg / "arms" / f"{name}.yaml").write_text(
            f"# 对照臂 {name}：由 {LIFE.as_posix()} 只改反馈政策 {ARMS[name]} 派生（原文件不改）。"
            "注释在派生时丢失。\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def dump(name: str, data: Any) -> None:
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    dump("environment.json", _environment())
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": "uv run python scripts/demo_life_e1_4b.py --out <新目录>",
        },
    )
    dump(
        "protocol.json",
        {
            "protocol": bundle.protocol.model_dump(mode="json"),
            "arms": {name: p.energy_feedback.model_dump(mode="json") for name, p in arms.items()},  # type: ignore[union-attr]
            "policy_content_fingerprints": policy_binding["content_fingerprints"],
            "feedback_handoff_revision": FEEDBACK_HANDOFF_REVISION,
            "resource_rules_revision": RESOURCE_RULES_REVISION,
            "settlement_convention": SETTLEMENT_CONVENTION,
            "scoring_spec": {
                "spec_id": scoring.spec.spec_id,
                "scoring_version": scoring.spec.scoring_version,
            },
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
            "arms": ARMS,
        },
    )
    results = {
        "u31.a61.c0": {name: [_year_row(r) for r in runs] for name, runs in main_runs.items()},
        "u31.a61.c0 off（不给评分输入）": [_year_row(r) for r in off_noscore],
        "u31.b62.c0 main": [_year_row(r) for r in cross],
        "u31.a61.down main": [_year_row(r) for r in down],
        "u33.a61.c0 main": [_year_row(r) for r in gap_on],
        "u33.a61.c0 off": [_year_row(r) for r in gap_off],
        "u34.a61.c0 main": [_year_row(r) for r in exit_on],
    }
    dump("yearly_results.json", results)
    dump(
        "life_year_results_main.json",
        [r.model_dump(mode="json") for r in main_runs["main_alpha_0.10"]],
    )
    dump("off_vs_e1_4a.json", equivalence)
    dump("information_conditions.json", channels)
    dump("product_payload_year2_standard.json", payload_on)
    dump("product_payload_year2_energy_hidden.json", payload_hidden)
    dump("policy_binding.json", policy_binding)
    dump("year_binding.json", year_binding)
    dump(
        "batch_report.json",
        {name: {k: v for k, v in b.items() if k != "runs"} for name, b in batches.items()}
        | {"note": BATCH_NOTE, "same_as_running_alone": same_alone},
    )
    batch_lines = [
        "# 小批次：分身停止与整场运行（合成，演示层薄循环）",
        "",
        "单个分身的预期停止（生活出局、反馈信息不足、计划不可行、输入或资料错误）只停这个分身；意外程序错误会"
        "显式记录并停止整个批次。每个分身单独运行与放在批次里运行，结果逐字相同："
        f"{'是' if same_alone else '否'}。",
        "",
        *batch_markdown("主臂（α = 0.10）", batches["main_alpha_0.10"]),
        *batch_markdown("关闭反馈", batches["off"]),
    ]
    (args.out / "batch_report.md").write_text("\n".join(batch_lines) + "\n", encoding="utf-8")
    lines = [
        "# E1-4B 年度反馈演示（合成）",
        "",
        "公式：下一年额度 = 基础额度 × (1 + α × 上一年完整年度满足感)，再向下保留到 0.01 负担点。"
        "满足感是 0–1 的无量纲数；额度单位是负担点；α 是无量纲比例。",
        "",
    ]
    for title, runs in (
        *[(f"u31.a61.c0（{name}）", rs) for name, rs in main_runs.items()],
        ("u31.a61.c0（off，不给评分输入）", off_noscore),
        ("u31.b62.c0（主臂，跨城）", cross),
        ("u31.a61.down（主臂，满意度下降）", down),
        ("u33.a61.c0（主臂，缺分）", gap_on),
        ("u33.a61.c0（off，同一缺失）", gap_off),
        ("u34.a61.c0（主臂，出局）", exit_on),
    ):
        rows = [_year_row(r) for r in runs]
        lines += [
            f"## {title}",
            "",
            "账目与资源（金额按当地基年不变价；额度与负担单位是负担点）：",
            "",
        ]
        lines += [LEDGER_HEAD, *(_fmt_ledger(x) for x in rows), ""]
        lines += ["评分与反馈：", "", FEEDBACK_HEAD, *(_fmt_feedback(x) for x in rows), ""]
        for x in rows:
            items = "、".join(x["ledger"]["items"]) if x["ledger"] else "无（没有生活结果）"
            lines += [f"- 第 {x['year']} 年执行：{items}"]
            lines += [f"- 第 {x['year']} 年结算问题：{p}" for p in x["problem_messages"]]
            lines += [f"- 第 {x['year']} 年反馈：{'；'.join(x['reasons'])}"]
            lines += [f"- 第 {x['year']} 年备注：{n}" for n in x["notes"]]
        lines += [""]
    (args.out / "yearly_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    check_lines = [
        "# 手算核对",
        "",
        "额度按精确分数比较；满足感取评分器输出的最短十进制写法；不加容差。",
        "",
        *(f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks),
    ]
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-4B 年度反馈（运行证据）",
                "",
                "零大模型调用的离线演示（`DECISIONS/0021`）。全部数值是合成设定下的结果；对照只验证接线，**不证明开启",
                "反馈让产品更准确，也不是城市排行**。",
                "",
                "- `command.json` / `environment.json`：实际命令、git 状态、执行修订与源码指纹",
                "- `configs/`：生活输入与评分配置副本；`configs/arms/`：派生的对照臂（只改反馈政策）",
                "- `protocol.json` / `inputs.json`：协议、各臂反馈政策、评分规格与加载检查报告",
                "- `yearly_results.json` / `yearly_tables.md`：每年实际账目要点、评分、基础额度、更新前后额度、交接与原因",
                "- `life_year_results_main.json`：主闭环三年的完整年度交接结果（内部对象，含评分与反馈记录）",
                "- `off_vs_e1_4a.json`：关闭反馈与 E1-4A 的逐项比较（预先声明允许不同的字段）",
                "- `information_conditions.json`、`product_payload_year2_*.json`：信息条件与实际 payload",
                "- `policy_binding.json`：r2 F1——同编号改政策内容在结算与观察处被拒绝",
                "- `year_binding.json`：r2 F2——住近处 / 住远处两次真实试算不能互换账目、分数与候选状态",
                "- `batch_report.json` / `batch_report.md`：小批次的停止原因、按城市的互斥计数、逐年三件事对照",
                "- `checks.md` / `checks.json`：手算核对",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for line in check_lines[4:]:
        print(line)
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
