"""E1-6B 城市汇总的离线演示（`DECISIONS/0029`）。零大模型调用。

    uv run python scripts/demo_life_e1_6b.py --out runs/<新目录>

顺序：先读原计划名单 ``configs/life/roster_e1_6b.yaml``，**冻结并写盘**（看任何结果之前）；再按名单逐个分身，用预先写好
的年度计划实际生活三年（结算 → 评分 → 反馈，现有引擎原样）。名单里刻意有一项不运行（u101.a95.r4，模拟"结果没有返回"）。
然后用三个运行臂（主设定；δ = 0.99；只含基本生活模板）对**同一批**实际生活做 E1-6A 的单分身评价，最后按"用户 × 城市"
汇总：

    完整评价分身的平均分 = Σ J ÷ C（同一批 C 的 L、V 平均一起给）
    出局比例 = E ÷ N，缺失比例 = M ÷ N（N = 原计划人数 = C + E + M）
    模拟缺失范围 = Σ 下界 ÷ (C + M) 到 Σ 上界 ÷ (C + M)（完整的取 [J, J]，确认出局者不进范围）

另有两个**公式构造单测**（10 人里 6 人完整、合计 3.6……；一份部分轨迹），与真实引擎的结果分开展示。

全部数字来自合成样例：不是现实城市评价，也不排序真实城市；范围不是置信区间。目录已存在就拒绝，不覆盖旧结果。退出码：
0 = 全部核对通过；1 = 有核对不通过、有意外程序错误，或输入不合法。
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
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.ids import CityId
from common.life_aggregation_contracts import (
    MISSING_REASON_TEXT,
    RESERVED_AGGREGATION_KEYS,
    ArmReport,
    CityReport,
    EntryRecord,
    RunFailure,
)
from common.life_contracts import ExactAmount, LifeContractError, model_fingerprint
from common.life_evaluation_contracts import CloneEvaluation, EvaluationSpec, PlanEvaluation
from common.life_feedback_contracts import LifeYearResult
from econ.life_aggregation import (
    ASSUMPTIONS,
    aggregate_arm,
    city_report,
    freeze_roster,
    missing_range_bounds,
    sensitivity,
)
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle
from ledger.life_roster import load_roster, roster_problems, standard_start_problems
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
ROSTER = Path("configs/life/roster_e1_6b.yaml")
#: E1-6A 演示的评分配置：E1-6B 样例的评分规格与它内容身份相同（只换了编号与比较组），月份分可以沿用那边的手算。
E1_6A_SCORING = Path("configs/life/scoring_e1_6a_evaluation.yaml")
E1_6A_LIFE = Path("configs/life/synthetic_e1_6a_evaluation.yaml")
#: 刻意不运行的名单项（模拟"结果没有返回"）：它仍在原计划名单里，进分母与缺失范围。
NOT_RETURNED = frozenset({"u101.a95.r4"})
MAIN, D099, BASIC = "arm.main", "arm.delta099", "arm.basic_only"
A95, B96 = "US:SYNTH2026:A95", "US:SYNTH2026:B96"
KEY_SOURCES = (
    "src/common/life_aggregation_contracts.py",
    "src/common/life_evaluation_contracts.py",
    "src/ledger/life_roster.py",
    "src/ledger/life_evaluation_config.py",
    "src/econ/life_aggregation.py",
    "src/econ/life_evaluation.py",
    "src/econ/life_terminal_value.py",
    "src/econ/life_year.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_6b.py",
)


def _hand() -> dict[str, Fraction]:
    """逐月满足感的手算（精确分数）。S = 0.8 × B + 0.2 × H（健康 H = 1、小病 0.5、大病 0）。

    α（标准偏好）：B 按去掉医疗维后的权重 物质 1/3、安全 1/9、发展 2/9、休闲 1/9、环境 1/9、交通 1/9 加权，基本生活的
    各维 s = z/(z+τ) 与 E1-6A 演示相同；β（只看重交通）：B 就是交通分。交通 k = 0.25 + 0.75 × D/20，T_eq = k × T，
    s = 60/(60 + T_eq)；T 是住房往返分钟（A95 远处 70、B96 远处 90、近处 30），D 是当月到岗次数（门店兼职 10，大病病假 0）。
    """
    fr = Fraction

    def sat(z: Fraction, tau: int) -> Fraction:
        return min(fr(9, 10), z / (z + tau))

    def transport(minutes: int, visits: int) -> Fraction:
        return fr(60) / (60 + (fr(1, 4) + fr(3, 4) * fr(visits, 20)) * minutes)

    rest = (
        fr(1, 3) * (fr(1, 2) * sat(fr(6), 6) + fr(3, 10) * sat(fr(3), 7))
        + fr(1, 9) * sat(fr(3), 3)
        + fr(2, 9) * sat(fr(2), 2)
        + fr(1, 9) * sat(fr(4), 12)
        + fr(1, 9) * sat(fr(3), 3)
    )

    def alpha(minutes: int, visits: int, h: Fraction) -> Fraction:
        return fr(4, 5) * (rest + fr(1, 9) * transport(minutes, visits)) + fr(1, 5) * h

    def beta(minutes: int, visits: int, h: Fraction) -> Fraction:
        return fr(4, 5) * transport(minutes, visits) + fr(1, 5) * h

    return {
        "alpha_a95_basic": alpha(70, 10, fr(1)),
        "alpha_a95_major": alpha(70, 0, fr(0)),
        "alpha_a95_minor": alpha(70, 10, fr(1, 2)),
        "alpha_b96_basic": alpha(90, 10, fr(1)),
        "alpha_b96_minor": alpha(90, 10, fr(1, 2)),
        "beta_a95_basic": beta(70, 10, fr(1)),
        "beta_a95_major": beta(70, 0, fr(0)),
        "beta_near_well": beta(30, 10, fr(1)),
        "beta_b96_basic": beta(90, 10, fr(1)),
        "beta_b96_major": beta(90, 0, fr(0)),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _v(x: ExactAmount | Fraction | None) -> str:
    if x is None:
        return "—"
    q = x.fraction() if isinstance(x, ExactAmount) else x
    return f"{float(q):.6f}"


def _pct(x: ExactAmount) -> str:
    return f"{float(x.fraction()):.0%}"


def _money(m: Any) -> str:
    return "—" if m is None else f"{m.amount:,.0f}"


def _short(clone: str) -> str:
    return clone.rsplit(".", 1)[-1]


def _city(city: Any) -> str:
    return str(city).rsplit(":", 1)[-1]


class Runner:
    """按冻结名单跑实际生活与评价。不是 E1-7 的调度器：只按样例里预先写好的年度计划逐年调用 ``run_life_year``，再对
    每个运行臂调用 ``evaluate_clone``；``LifeContractError`` 记成"输入或配置被拒绝"，其他异常记成意外程序错误。"""

    def __init__(
        self, bundle: LifeBundle, scoring: ScoringConfig, specs: dict[str, EvaluationSpec]
    ) -> None:
        self.bundle, self.scoring, self.specs = bundle, scoring, specs

    def inputs(self, clone: str):
        s = self.bundle.initial_state(clone)
        assert s is not None
        catalog = self.bundle.catalog_for(str(s.city))
        assert catalog is not None
        policy = self.bundle.tax_policy(catalog.tax_policy_id)
        assert policy is not None
        profile = next(p for p in self.bundle.stated_profiles if p.person_id == s.person_id)
        scoring = ScoringInputs(
            spec=self.scoring.spec,
            conditions=self.scoring.conditions_for(str(s.city)),
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )
        return s, catalog, binding_from_policy(policy), scoring

    def live(self, clone: str) -> list[LifeYearResult]:
        state, catalog, tax, scoring = self.inputs(clone)
        out: list[LifeYearResult] = []
        for plan in self.bundle.plans_for(clone):
            r = run_life_year(
                protocol=self.bundle.protocol,
                state=state,
                action=plan,
                catalog=catalog,
                tax=tax,
                scoring=scoring,
            )
            out.append(r)
            if r.handoff != "next_year_ready":
                break
            assert r.next_state is not None
            state = r.next_state
        return out

    def evaluate(
        self, clone: str, results: list[LifeYearResult], spec: EvaluationSpec
    ) -> CloneEvaluation:
        _, catalog, tax, scoring = self.inputs(clone)
        return evaluate_clone(
            spec=spec,
            protocol=self.bundle.protocol,
            catalog=catalog,
            tax=tax,
            scoring=scoring,
            results=results,
        )


def _constructed(
    category: str,
    clone: str,
    j: Fraction | None = None,
    *,
    k: Fraction = Fraction(0),
    w_known: Fraction = Fraction(0),
    v: Fraction | None = None,
) -> EntryRecord:
    """公式构造单测用的名单项记录（δ = 1、N = 3、H = 12：W_actual = 36、W_future = 12）。不是模拟结果。"""
    w_a, w_f = Fraction(36), Fraction(12)
    base: dict[str, Any] = {
        "user_id": "user.constructed",
        "person_id": "u900",
        "clone_id": clone,
        "city": CityId("US", "SYNTH2026", "X01"),
        "repeat_index": 0,
        "category": category,
        "detail": "公式构造单测：手工给定的名单项，不是模拟结果",
        "weight_actual": ExactAmount.of(w_a),
        "weight_future": ExactAmount.of(w_f),
        "known_weighted_sum": ExactAmount.of(k),
        "known_weight": ExactAmount.of(w_known),
    }
    if category == "C":
        assert j is not None
        e = ExactAmount.of(j)
        base.update(j=e, l_actual=e, v_reference=e, lower=e, upper=e)
    elif category == "M":
        v_low, v_high, lo, hi = missing_range_bounds(k, w_known, w_a, w_f, v)
        base.update(
            missing_reason="not_run",
            v_low=ExactAmount.of(v_low),
            v_high=ExactAmount.of(v_high),
            lower=ExactAmount.of(lo),
            upper=ExactAmount.of(hi),
        )
    return EntryRecord.model_validate(base)


def _report(rep: ArmReport, user: str, city: str) -> CityReport:
    return next(r for r in rep.city_reports if (r.user_id, str(r.city)) == (user, city))


def _entry(cr: CityReport, clone: str) -> EntryRecord:
    return next(e for e in cr.entries if e.clone_id == clone)


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _reason_text(e: EntryRecord) -> str:
    """M 项的原因（停止写明是资料缺口还是应急安排不足，取自已核对的停止记录生成的标签）。"""
    if e.missing_reason == "stopped":
        kind = "资料缺口" if "资料缺口停止" in e.event_tags else "应急安排不足"
        return f"E1-5C 停止：{kind}"
    return MISSING_REASON_TEXT[e.missing_reason]  # type: ignore[index]


def _unknown_share(e: EntryRecord) -> Fraction:
    """这个 M 项的总权重里有多少是未知的（范围宽度）：(W_actual − W_known + W_future × (v_high − v_low)) ÷ 全部权重。"""
    wa, wf = e.weight_actual.fraction(), e.weight_future.fraction()
    gap = wa - e.known_weight.fraction()
    gap += wf * (e.v_high.fraction() - e.v_low.fraction())  # type: ignore[union-attr]
    return gap / (wa + wf)


def explain(cr: CityReport) -> str:
    """ "这个均值用了谁；没有用谁；这个范围在担心什么"。"""
    used = [_short(e.clone_id) for e in cr.entries if e.category == "C"]
    if used:
        s1 = f"这个均值用了 {'、'.join(used)} 共 {cr.complete} 个完整评价的分身（原计划 {cr.planned} 个）"
    else:
        s1 = f"没有完整评价的分身，所以没有均值（不填 0，不用 L 代替 J；原计划 {cr.planned} 个）"
    left = []
    for e in cr.entries:
        if e.category == "E":
            left.append(f"{_short(e.clone_id)}——确认出局，单独报告，不进均值也不进范围")
        elif e.category == "M":
            left.append(f"{_short(e.clone_id)}——{_reason_text(e)}")
    s2 = "没有用：" + "；".join(left) if left else "没有排除任何人"
    worries = [
        f"{_short(e.clone_id)} 还有 {float(_unknown_share(e)):.0%} 的权重未知"
        for e in cr.entries
        if e.category == "M"
    ]
    if cr.range_lower is None:
        s3 = "没有范围：全部确认出局，看出局比例"
    elif worries:
        s3 = (
            f"范围 [{_v(cr.range_lower)}, {_v(cr.range_upper)}]（分母 C + M = {cr.range_denominator}）担心的是："
            + "，".join(worries)
            + "——这些部分可能落在 0 到 1 之间任何位置"
        )
    else:
        s3 = "范围退化为均值：没有尚不能计算的分身"
    return f"{s1}。{s2}。{s3}。"


def _city_md(cr: CityReport) -> list[str]:
    rows = [
        f"### {cr.user_id} 在 {_city(cr.city)}",
        "",
        f"原计划 {cr.planned} 人：完整 C = {cr.complete}（{_pct(cr.complete_share)}），确认出局 E = {cr.exits}"
        f"（{_pct(cr.exit_share)}），模拟缺失 M = {cr.missing}（{_pct(cr.missing_share)}）。",
        "",
        f"* 完整评价分身的平均分 J = {_v(cr.mean_j)}；同一批 C 的 L = {_v(cr.mean_l)}、V = {_v(cr.mean_v)}",
        f"* 模拟缺失范围 [{_v(cr.range_lower)}, {_v(cr.range_upper)}]，分母 C + M = {cr.range_denominator}"
        "（不是置信区间）",
        f"* {explain(cr)}",
        "",
        "| 分身 | 类别 | 原因 | L | V | J | 下界 | 上界 | 已知权重 / 全部实际权重 | 选中的参考分支 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in cr.entries:
        reason = _reason_text(e) if e.category == "M" else ("确认出局" if e.category == "E" else "")
        known = (
            "—（不进范围）"
            if e.category == "E"
            else f"{e.known_weight.exact} / {e.weight_actual.exact}"
        )
        rows.append(
            f"| {e.clone_id} | {e.category} | {reason} | {_v(e.l_actual)} | {_v(e.v_reference)} | "
            f"{_v(e.j)} | {_v(e.lower)} | {_v(e.upper)} | {known} | {e.selected_plan_id or '—'} |"
        )
    rows.append("")
    for e in cr.entries:
        if e.event_chain:
            rows.append(
                f"{e.clone_id} 的事件链（标签：{'、'.join(e.event_tags)}；只写顺序，不宣称因果贡献）："
            )
            rows.append("")
            rows += [f"{i}. {s.text}" for i, s in enumerate(e.event_chain, start=1)]
            rows.append("")
        elif e.category == "M":
            rows.append(f"{e.clone_id}：{e.detail}")
            rows.append("")
    for h in cr.hard_requirements:
        unchecked = (
            f"，尚未核验 {h.not_checked} 人（{'、'.join(h.not_checked_clone_ids)}：没有可信记录，不是核验过、没问题）"
            if h.not_checked
            else "，没有未核验的人"
        )
        rows.append(
            f"硬要求 `{h.need}`：原计划 {h.planned} 人，有可信记录核验了 {h.checked} 人——每月都满足 "
            f"{h.satisfied_every_month} 人，违反 {h.violated} 人"
            f"{'（' + '、'.join(h.violated_clone_ids) + '）' if h.violated_clone_ids else ''}，无法判断 {h.unknown} 人，"
            f"只在活着的月份里满足 {h.satisfied_in_lived_months} 人{unchecked}。违反不删人、不扣分、不改出局。"
        )
        rows.append("")
    rows += [f"> {n}" for n in cr.notes]
    if cr.notes:
        rows.append("")
    return rows


def _plan_row(p: PlanEvaluation) -> str:
    branch = {"untriggered": "未触发", "treat_if_possible": "条件允许就治", "decline": "不治疗"}[
        p.treatment_branch
    ]
    why = "" if p.status == "complete" else _clip(p.reasons[0] if p.reasons else "", 90)
    months = "、".join(
        f"{s}×{n}"
        for s, n in _runs(
            [{"major": "大病", "minor": "小病", "healthy": "健康"}[h] for h in p.health_states]
        )
    )
    return (
        f"| {p.plan_id} | {p.template_id} | {branch} | {p.status} | {_v(p.v_plan)} | "
        f"{_money(p.cash_start)} → {_money(p.cash_end)} | {months or '—'} | {why} |"
    )


def _runs(xs: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for x in xs:
        if out and out[-1][0] == x:
            out[-1] = (x, out[-1][1] + 1)
        else:
            out.append((x, 1))
    return out


def _ledger_csv(rep: ArmReport) -> str:
    lines = [
        "arm,user,city,clone,repeat,category,missing_reason,j,l_actual,v_reference,lower,upper,"
        "known_weighted_sum,known_weight,weight_actual,weight_future,selected_branch,unexpected_error,detail"
    ]
    for cr in rep.city_reports:
        for e in cr.entries:
            cells = [
                rep.arm_id,
                e.user_id,
                str(e.city),
                e.clone_id,
                str(e.repeat_index),
                e.category,
                e.missing_reason or "",
                *[
                    "" if a is None else a.exact
                    for a in (e.j, e.l_actual, e.v_reference, e.lower, e.upper)
                ],
                e.known_weighted_sum.exact,
                e.known_weight.exact,
                e.weight_actual.exact,
                e.weight_future.exact,
                e.selected_plan_id or "",
                str(e.unexpected_error).lower(),
                '"' + e.detail.replace('"', "'") + '"',
            ]
            lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-6B 城市汇总的离线演示（合成，不排序真实城市）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        config, bundle, scoring, specs = load_roster(ROSTER)
        roster = freeze_roster(config, bundle, scoring, specs)
    except LifeContractError as exc:
        print(exc)
        return 1
    out = args.out
    out.mkdir(parents=True)

    def dump(name: str, data: Any) -> None:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    # ------------------------------------------------------------ 第 0 步：冻结名单，先写盘
    dump(
        "roster/frozen_roster.json",
        {
            "content_fingerprint": roster.content_fingerprint,
            "roster": roster.model_dump(mode="json"),
        },
    )
    log: list[dict[str, Any]] = [
        {
            "step": 0,
            "what": "freeze_roster",
            "roster_fingerprint": roster.content_fingerprint,
            "planned_entries": len(roster.entries),
            "note": "看任何结果之前冻结并写盘；之后的步骤只按这份名单运行",
        }
    ]

    # ------------------------------------------------------------ 第 1 步：按名单跑实际生活与三个臂的评价
    run = Runner(bundle, scoring, specs)
    lives: dict[str, list[LifeYearResult]] = {}
    evals: dict[str, list[CloneEvaluation]] = {a.arm_id: [] for a in roster.arms}
    failures: dict[str, list[RunFailure]] = {a.arm_id: [] for a in roster.arms}
    unexpected = False
    for e in roster.entries:
        if e.clone_id in NOT_RETURNED:
            log.append({"step": len(log), "clone": e.clone_id, "what": "not_run_by_design"})
            continue
        try:
            lives[e.clone_id] = run.live(e.clone_id)
        except LifeContractError as exc:
            for arm in failures:
                failures[arm].append(
                    RunFailure(
                        clone_id=e.clone_id,
                        stage="life",
                        error_kind="contract",
                        message=str(exc)[:600],
                    )
                )
            log.append({"step": len(log), "clone": e.clone_id, "what": "life_refused"})
            continue
        except Exception as exc:  # 意外程序错误：显式报告，不吞掉
            unexpected = True
            for arm in failures:
                failures[arm].append(
                    RunFailure(
                        clone_id=e.clone_id,
                        stage="life",
                        error_kind="unexpected",
                        message=f"{type(exc).__name__}: {exc}"[:600],
                    )
                )
            log.append({"step": len(log), "clone": e.clone_id, "what": "life_unexpected_error"})
            continue
        handoffs = [r.handoff for r in lives[e.clone_id]]
        log.append({"step": len(log), "clone": e.clone_id, "what": "lived", "handoffs": handoffs})
        for arm in roster.arms:
            try:
                evals[arm.arm_id].append(
                    run.evaluate(e.clone_id, lives[e.clone_id], specs[arm.arm_id])
                )
            except LifeContractError as exc:
                failures[arm.arm_id].append(
                    RunFailure(
                        clone_id=e.clone_id,
                        stage="evaluation",
                        error_kind="contract",
                        message=str(exc)[:600],
                    )
                )
            except Exception as exc:  # 同上：意外程序错误显式报告
                unexpected = True
                failures[arm.arm_id].append(
                    RunFailure(
                        clone_id=e.clone_id,
                        stage="evaluation",
                        error_kind="unexpected",
                        message=f"{type(exc).__name__}: {exc}"[:600],
                    )
                )

    # ------------------------------------------------------------ 第 2 步：汇总
    reports = {
        a.arm_id: aggregate_arm(roster, a.arm_id, evals[a.arm_id], failures[a.arm_id])
        for a in roster.arms
    }
    sens = {
        other: sensitivity(reports[MAIN], reports[other], evals[MAIN], evals[other])
        for other in (D099, BASIC)
    }
    main_rep = reports[MAIN]
    ev_main = {e.clone_id: e for e in evals[MAIN]}

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    def x(a: ExactAmount | None) -> Fraction | None:
        return None if a is None else a.fraction()

    def fd(text: str | None) -> Fraction:
        assert text is not None
        return Fraction(Decimal(text))

    # ------------------------------------------------------------ 月份分的手算
    hand = _hand()
    e16a, _ = load_scoring_config(E1_6A_SCORING, _e16a_bundle())
    check(
        "评分内容与 E1-6A 演示相同",
        e16a.spec.content_fingerprint == scoring.spec.content_fingerprint,
        f"评分规格内容身份 {scoring.spec.content_fingerprint[:16]}…（只换了编号与比较组）",
    )
    month = {
        "alpha_a95_basic": ev_main["u101.a95.r0"].actual.months[0],
        "alpha_a95_major": ev_main["u101.a95.r1"].actual.months[16],
        "alpha_a95_minor": ev_main["u101.a95.r3"].actual.months[14],
        "alpha_b96_basic": ev_main["u101.b96.r0"].actual.months[0],
        "alpha_b96_minor": ev_main["u101.b96.r3"].actual.months[14],
        "beta_a95_basic": ev_main["u102.a95.r0"].actual.months[0],
        "beta_a95_major": ev_main["u102.a95.r1"].actual.months[35],
        "beta_b96_basic": ev_main["u102.b96.r0"].actual.months[0],
        "beta_b96_major": ev_main["u102.b96.r1"].actual.months[35],
    }
    score: dict[str, Fraction] = {}
    for key, m in month.items():
        score[key] = fd(m.score_decimal)
        check(
            f"月度满足感手算：{key}",
            abs(float(hand[key]) - float(score[key])) < 1e-12,
            f"手算 {float(hand[key]):.12f}，评分器 {m.score_decimal}（第 {m.year_index} 年第 {m.month} 月；评分器用浮点，差 < 1e-12）",
        )
    commute_gap = Fraction(4, 5) * Fraction(1, 9) * (60 / Fraction(415, 4) - 60 / Fraction(465, 4))
    check(
        "B96 与 A95 的基本月份只差交通一项",
        abs(float(score["alpha_a95_basic"] - score["alpha_b96_basic"] - commute_gap)) < 1e-12,
        "0.8 × 1/9 × (60/103.75 − 60/116.25) = 0.005527：B96 远处住房往返 90 分钟（A95 70 分钟），其余都一样",
    )

    # ------------------------------------------------------------ 名单与人数守恒
    again = freeze_roster(config, bundle, scoring, specs)
    check(
        "名单冻结前通过标准开局 v1 与每个运行臂的跨城配置检查",
        (roster.start_condition_rule, roster.aggregation_revision)
        == ("v1_standard_start_2026_10_12", "e1-6b-r2")
        and roster_problems(config, bundle, scoring, specs) == []
        and all(
            not standard_start_problems(bundle.initial_state(e.clone_id)) for e in roster.entries
        ),
        "14 个名单项都是第 1 年、没有生活记录的开局（没有持有物、培训进度、资金目标、病情与病史），同一用户的个人条件逐字段"
        "一致；三个运行臂的评价规格都通过完整的跨城检查",
    )
    check(
        "名单先冻结，重新冻结得到同一内容身份",
        again.content_fingerprint == roster.content_fingerprint
        and log[0]["what"] == "freeze_roster",
        f"名单指纹 {roster.content_fingerprint[:16]}…；冻结写盘是第 0 步，第一份实际生活是第 1 步",
    )
    planned = sorted(e.clone_id for e in roster.entries)
    conserve = True
    for rep in reports.values():
        seen = sorted(e.clone_id for cr in rep.city_reports for e in cr.entries)
        conserve &= seen == planned and all(
            cr.planned == cr.complete + cr.exits + cr.missing == len(cr.entries)
            for cr in rep.city_reports
        )
    check(
        "人数守恒（三个臂、每个用户 × 城市）",
        conserve,
        f"原计划 {len(planned)} 项，每项恰好一条记录；N = C + E + M",
    )
    a_a = _report(main_rep, "user.alpha", A95)
    r4 = _entry(a_a, "u101.a95.r4")
    check(
        "没有返回的分身仍在分母里",
        (r4.category, r4.missing_reason, x(r4.lower), x(r4.upper)) == ("M", "not_run", 0, 1),
        "u101.a95.r4 没有运行：M（未运行 / 未返回），范围 [0, 1]",
    )

    # ------------------------------------------------------------ α 在 A95：完成、出局、缺失同城
    sa, sm, sn = score["alpha_a95_basic"], score["alpha_a95_major"], score["alpha_a95_minor"]
    j0 = sa
    j1 = Fraction(3, 4) * (35 * sa + sm) / 36 + Fraction(1, 4) * sa
    j3 = Fraction(3, 4) * (35 * sa + sn) / 36 + Fraction(1, 4) * sa
    js = [x(_entry(a_a, f"u101.a95.r{i}").j) for i in (0, 1, 3)]
    check(
        "α 在 A95：三个完整分身的 J",
        js == [j0, j1, j3],
        "r0 健康：J = 0.542072；r1 第 2 年第 5 月大病（治疗）：L = (35 × 0.542072 + 0.359484)/36，"
        "J = 0.75 L + 0.25 × 0.542072 = 0.538268；r3 第 2 年第 3 月小病（门诊）：J = 0.539989",
    )
    check(
        "α 在 A95：均值只用 C，出局与缺失比例除以原计划人数",
        x(a_a.mean_j) == (j0 + j1 + j3) / 3
        and (x(a_a.exit_share), x(a_a.missing_share)) == (Fraction(1, 5), Fraction(1, 5))
        and a_a.exit_clone_ids == ("u101.a95.r2",),
        "(0.542072 + 0.538268 + 0.539989)/3 = 0.540110；出局 1/5 = 20%，缺失 1/5 = 20%",
    )
    check(
        "α 在 A95：范围的分母是 C + M = 4，出局者不在里面",
        a_a.range_denominator == 4
        and (x(a_a.range_lower), x(a_a.range_upper))
        == ((j0 + j1 + j3) / 4, (j0 + j1 + j3 + 1) / 4),
        "[1.620329/4, (1.620329 + 1)/4] = [0.405082, 0.655082]；r4 取 [0, 1]",
    )
    r2 = _entry(a_a, "u101.a95.r2")
    t2 = ev_main["u101.a95.r2"].trajectory
    m12 = next(f for f in t2.month_facts if (f.year_index, f.month) == (1, 2))
    check(
        "α r2 在 A95 的出局事件链能追到记录",
        r2.category == "E"
        and (m12.cash_start.amount, m12.income_gross.amount, m12.spent.amount, m12.cash_end.amount)
        == (2120, 0, 1380, 740)
        and m12.treatment_status == "unaffordable"
        and (t2.exit.year_index, t2.exit.month, t2.exit.shortfall.amount) == (1, 3, 640)  # type: ignore[union-attr]
        and [s.source for s in r2.event_chain]
        == ["month_fact", "event", "month_fact", "exit_record"],
        "第 2 月大病：病假收入 0，付住房食品 1,380 → 740，住院 2,000 付不起；第 3 月 740 < 1,000 + 380，差 640 → 出局",
    )

    # ------------------------------------------------------------ α 在 B96：同样的事件，停止不是出局
    a_b = _report(main_rep, "user.alpha", B96)
    sb, sbn = score["alpha_b96_basic"], score["alpha_b96_minor"]
    b1, b2 = _entry(a_b, "u101.b96.r1"), _entry(a_b, "u101.b96.r2")
    check(
        "α 在 B96：同样的大病在这里是 E1-5C 停止（资料缺口），不是出局",
        a_b.exits == 0
        and (b1.missing_reason, b2.missing_reason) == ("stopped", "stopped")
        and _entry(a_a, "u101.a95.r1").category == "C"
        and r2.category == "E",
        "B96 住院资料未知：要治疗就停在发病那个月；A95 的 r1 治好了（C），r2 付不起最低生活（E）",
    )
    check(
        "α 在 B96：停止前的月份收窄范围",
        (x(b1.known_weight), x(b1.known_weighted_sum)) == (16, 16 * sb)
        and (x(b1.lower), x(b1.upper)) == (16 * sb / 48, (16 * sb + 20 + 12) / 48)
        and (x(b2.known_weight), x(b2.lower), x(b2.upper)) == (1, sb / 48, (sb + 35 + 12) / 48),
        "r1 停在第 2 年第 5 月：已知 16 个月，[16 × 0.536545/48, (8.584718 + 20 + 12)/48] = [0.178848, 0.845515]；"
        "r2 停在第 1 年第 2 月：已知 1 个月，[0.011178, 0.990345]",
    )
    jb = [sb, sb - Fraction(3, 4) * (sb - sbn) * 2 / 36, sb]
    check(
        "α 在 B96：均值与范围",
        [x(_entry(a_b, f"u101.b96.r{i}").j) for i in (0, 3, 4)] == jb
        and x(a_b.mean_j) == sum(jb) / 3
        and (x(a_b.range_lower), x(a_b.range_upper))
        == ((sum(jb) + x(b1.lower) + x(b2.lower)) / 5, (sum(jb) + x(b1.upper) + x(b2.upper)) / 5),  # type: ignore[operator]
        "r3 小病没有门诊（B96 确定没有），病 2 个月照常上班：J = 0.536545 − 0.75 × 2 × 0.1/36 = 0.532378；"
        "均值 0.535156；范围分母 5",
    )

    # ------------------------------------------------------------ β：completed_scored 但 J 为空；硬要求
    b_a, b_b = _report(main_rep, "user.beta", A95), _report(main_rep, "user.beta", B96)
    bb1 = _entry(b_b, "u102.b96.r1")
    ev_bb1 = ev_main["u102.b96.r1"]
    lb = (35 * score["beta_b96_basic"] + score["beta_b96_major"]) / 36
    check(
        "β r1 在 B96：实际三年完整但估值不完整 → 缺失，不进均值",
        ev_bb1.trajectory.owner_category == "completed_scored"
        and ev_bb1.combined.j is None
        and (bb1.category, bb1.missing_reason) == ("M", "valuation_incomplete")
        and x(bb1.l_actual) == lb
        and (x(bb1.lower), x(bb1.upper)) == (36 * lb / 48, (36 * lb + 12) / 48),
        "带着大病进入估值期，B96 住院资料未知 → 治疗分支无法评价，V 不完整；只有未来 12 个月未知："
        "L = 0.612040，[0.459030, 0.709030]，宽度 12/48 = 0.25",
    )
    ba0 = _entry(b_a, "u102.a95.r0")
    check(
        "β r0 在 A95：V 是近处住房的较舒适生活",
        ba0.selected_plan_id == "p2_current_comfort"
        and abs(float(x(ba0.v_reference) - hand["beta_near_well"])) < 1e-12  # type: ignore[operator]
        and x(ba0.j)
        == Fraction(3, 4) * score["beta_a95_basic"] + Fraction(1, 4) * x(ba0.v_reference),  # type: ignore[operator]
        "近处住房往返 30 分钟：0.8 × 60/78.75 + 0.2 = 0.809524；J = 0.75 × 0.662651 + 0.25 × 0.809524 = 0.699369",
    )
    (h,) = b_b.hard_requirements
    check(
        "β 在 B96：硬要求违反单独报告，不删人",
        (h.need, h.planned, h.checked, h.not_checked) == ("short_commute", 2, 2, 0)
        and (h.violated, h.violated_clone_ids) == (2, ("u102.b96.r0", "u102.b96.r1"))
        and _entry(b_b, "u102.b96.r0").category == "C"
        and any("硬要求" in n for n in b_b.notes),
        "B96 远处住房往返 90 分钟 > 70：两个分身每月都违反；r0 仍是完整评价（J = 0.662058）",
    )

    # ------------------------------------------------------------ F3：同一生活下治 / 不治（β r1 在 A95）
    ev_ba1 = ev_main["u102.a95.r1"]
    plans = {p.plan_id: p for p in ev_ba1.valuation.plans}
    tr, de = plans["p1_current_basic.treat"], plans["p1_current_basic.decline"]
    sw, ss = score["beta_a95_basic"], score["beta_a95_major"]
    check(
        "F3：带病终态，每个模板治 / 不治两个分支，最多 12 次试算",
        ev_ba1.valuation.branching == "continuing_illness_treat_or_decline"
        and ev_ba1.valuation.trials == len(plans) == 8
        and {p.template_id for p in plans.values()}
        == {t.template_id for t in specs[MAIN].life_templates},
        "四个基础生活模板 × 治 / 不治 = 8 次试算",
    )
    check(
        "F3：同一基础生活，只差治疗选择（现金与病程可手算）",
        [i.opportunity_id for i in tr.items] == [i.opportunity_id for i in de.items]
        and (tr.cash_end.amount, de.cash_end.amount)
        == (6820 - 1380 - 2000 + 11 * 120, 6820 - 2 * 1380 + 10 * 120)  # type: ignore[union-attr]
        and tr.health_states[:2] == ("major", "healthy")
        and de.health_states[:3] == ("major", "major", "healthy")
        and (x(tr.v_plan), x(de.v_plan)) == ((ss + 11 * sw) / 12, (2 * ss + 10 * sw) / 12),
        "治：6,820 − 1,380 − 2,000 + 11 × 120 = 4,760，第 1 月末恢复；不治：6,820 − 2 × 1,380 + 10 × 120 = 5,260，"
        "病满 3 个月（终态已病 1 个月）",
    )
    check(
        "F3：选最高完整分支；分身自己的计划是不治，这只是参考估值",
        ev_ba1.valuation.selected_plan_id == "p1_current_basic.treat"
        and ev_ba1.valuation.selected_treatment == "treat_if_possible"
        and x(ev_ba1.valuation.v_reference) == (ss + 11 * sw) / 12,
        "β 门店兼职每月到岗 10 次：大病月 0.619355 < 上班月 0.662651，少病一个月更好；V = 0.659043",
    )

    # ------------------------------------------------------------ 城市比较、顺序、重放、对照
    comps = {c.user_id: c for c in main_rep.comparisons}
    ok_cmp = True
    for c in comps.values():
        ra = _report(main_rep, c.user_id, str(c.city_a))
        rb = _report(main_rep, c.user_id, str(c.city_b))
        lo = x(ra.range_lower) - x(rb.range_upper)  # type: ignore[operator]
        hi = x(ra.range_upper) - x(rb.range_lower)  # type: ignore[operator]
        ok_cmp &= (x(c.range_difference_low), x(c.range_difference_high)) == (lo, hi)
        ok_cmp &= c.direction == (
            "a_higher" if lo > 0 else "b_higher" if hi < 0 else "undetermined"
        )
    check(
        "城市差值范围 = [A 下 − B 上, A 上 − B 下]",
        ok_cmp and all(c.direction == "undetermined" for c in comps.values()),
        "α：[−0.283183, 0.295984]；β：[−0.005436, 0.119564]——都含 0：这些缺失范围下不能确定方向（不是显著性检验）",
    )
    shuffled = aggregate_arm(roster, MAIN, list(reversed(evals[MAIN])), failures[MAIN])
    check(
        "调换输入顺序，报告逐字节相同",
        shuffled.model_dump_json() == main_rep.model_dump_json(),
        f"报告指纹 {main_rep.report_fingerprint[:16]}…",
    )
    replay = run.evaluate("u101.a95.r1", run.live("u101.a95.r1"), specs[MAIN])
    check(
        "同样输入重放，单分身结果逐字节相同",
        replay.model_dump_json() == ev_main["u101.a95.r1"].model_dump_json(),
        f"u101.a95.r1 结果指纹 {replay.result_fingerprint[:16]}…",
    )
    s99, sbo = sens[D099], sens[BASIC]
    beta_b_small = next(c for c in sbo.changes if (c.user_id, str(c.city)) == ("user.beta", B96))
    check(
        "评价对照的证据逐项配对后才说是同一批生活",
        all(
            s.lives_check == "identical"
            and len(s.compared_clone_ids) == 13
            and len(s.not_compared) == 1
            and s.not_compared[0].startswith("u101.a95.r4：主臂没有可信评价（未运行 / 未返回）")
            for s in (s99, sbo)
        ),
        "两个对照臂各比较了 13 个分身的逐年封存绑定；u101.a95.r4 两边都没有可信评价，列为未比较，不说成已证明相同",
    )
    check(
        "评价对照用同一批实际生活，不覆盖主结果",
        s99.actual_lives_identical
        and sbo.actual_lives_identical
        and main_rep.report_fingerprint == main_rep.expected_report_fingerprint()
        and len(beta_b_small.moved) == 1
        and beta_b_small.moved[0].startswith("u102.b96.r0：C → M"),
        "只含基本生活模板时，β 在 B96 的 r0 没有满足硬要求的可行分支 → 估值不完整（C → M），不是出局",
    )
    check(
        "δ = 0.99：β 的比较方向变化被列出",
        any("user.beta" in d for d in s99.direction_changes),
        "；".join(s99.direction_changes) or "（没有方向变化）",
    )

    # ------------------------------------------------------------ 公式构造单测（不是模拟结果）
    hand_city = city_report(
        "arm.constructed",
        "user.constructed",
        "u900",
        [_constructed("C", f"c{i}", Fraction(6, 10)) for i in range(6)]
        + [_constructed("E", f"e{i}") for i in range(2)]
        + [_constructed("M", f"m{i}") for i in range(2)],
    )
    check(
        "构造单测：10 人里 6 人完整（合计 3.6）、2 人出局、2 人缺失",
        (x(hand_city.mean_j), x(hand_city.exit_share), x(hand_city.missing_share))
        == (Fraction(3, 5), Fraction(1, 5), Fraction(1, 5))
        and (x(hand_city.range_lower), x(hand_city.range_upper))
        == (Fraction(45, 100), Fraction(7, 10)),
        "均值 3.6/6 = 0.6；出局 20%；缺失 20%；范围 [3.6/8, 5.6/8] = [0.45, 0.70]",
    )
    partial = _constructed("M", "p0", k=24 * Fraction(6, 10), w_known=Fraction(24))
    check(
        "构造单测：部分记录 K = 14.4、W_known = 24 → [0.3, 0.8]",
        (x(partial.lower), x(partial.upper)) == (Fraction(3, 10), Fraction(4, 5)),
        "下界 (14.4 + 12 × 0)/48 = 0.3；上界 (14.4 + 36 − 24 + 12 × 1)/48 = 0.8",
    )

    # ------------------------------------------------------------ 隔离：汇总对象不进观察
    clone = "u102.a95.r0"
    r_y1 = lives[clone][0]
    _, cat, _, _ = run.inputs(clone)
    p = bundle.protocol
    obs = build_observation(
        permission=standard_product_permission("demo"),
        profile=next(pr for pr in bundle.stated_profiles if pr.person_id == "u102"),
        state=r_y1.next_state,
        catalog=cat,
        last_feedback=visible_feedback(r_y1.settlement),
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=p.health_rules,
        health_input=p.health_input,
    )
    text = json.dumps(observation_payload(obs), ensure_ascii=False)
    refused = 0
    for obj in (roster, main_rep, a_a, r2):
        for payload in (obj, obj.model_dump(mode="json")):
            try:
                assert_payload_clean({"notes": [payload]})
            except LifeContractError:
                refused += 1
    check(
        "城市均值、范围与名单不进观察",
        not any(f'"{k}"' in text for k in RESERVED_AGGREGATION_KEYS) and refused == 8,
        "第 2 年年初的观察里没有汇总键；名单、报告、城市报告、名单项记录本身与原样字典都被拒绝",
    )

    # ------------------------------------------------------------ 写出
    (out / "configs").mkdir()
    for src in (
        ROSTER,
        Path(config.life_input),
        Path(config.scoring_config),
        *(Path(a.evaluation_config) for a in config.arms),
    ):
        shutil.copyfile(REPO / src, out / "configs" / src.name)
    dump("run_log.json", log)
    for c, ev in ev_main.items():
        dump(f"evaluations/{MAIN}/{c}.json", ev.model_dump(mode="json"))
    dump(
        "evaluations/summary.json",
        {
            arm: {
                ev.clone_id: {
                    "result_fingerprint": ev.result_fingerprint,
                    "trajectory": ev.trajectory.status,
                    "owner_category": ev.trajectory.owner_category,
                    "l_actual": None if ev.actual.l_actual is None else ev.actual.l_actual.exact,
                    "valuation": ev.valuation.status,
                    "selected": ev.valuation.selected_plan_id,
                    "v_reference": None
                    if ev.valuation.v_reference is None
                    else ev.valuation.v_reference.exact,
                    "j": None if ev.combined.j is None else ev.combined.j.exact,
                    "branches": {pl.plan_id: pl.status for pl in ev.valuation.plans},
                    "binding_fingerprints": [y.binding_fingerprint for y in ev.trajectory.years],
                }
                for ev in evs
            }
            for arm, evs in evals.items()
        },
    )
    for arm, rep in reports.items():
        dump(f"reports/{arm}.json", rep.model_dump(mode="json"))
        (out / "ledger").mkdir(exist_ok=True)
        (out / "ledger" / f"{arm}.csv").write_text(_ledger_csv(rep), encoding="utf-8", newline="\n")
    for arm, s in sens.items():
        dump(f"sensitivity/{arm}.json", s.model_dump(mode="json"))
    dump(
        "constructed_examples.json",
        {
            "note": "公式构造单测：手工给定的名单项，不是模拟结果",
            "ten_planned": hand_city.model_dump(mode="json"),
            "partial_record": partial.model_dump(mode="json"),
        },
    )
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "reproduce": "uv run python scripts/demo_life_e1_6b.py --out <新目录>",
        },
    )
    status = _git("status", "--porcelain")
    inputs = [
        ROSTER.as_posix(),
        config.life_input,
        config.scoring_config,
        *(a.evaluation_config for a in config.arms),
    ]
    dump(
        "environment.json",
        {
            "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "git_head": _git("rev-parse", "HEAD"),
            "git_dirty": None if status is None else bool(status),
            "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
            "fingerprints_sha256": {f_: _sha256(REPO / f_) for f_ in [*KEY_SOURCES, *inputs]},
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    )
    dump(
        "identity.json",
        {
            "roster_id": roster.roster_id,
            "roster_fingerprint": roster.content_fingerprint,
            "aggregation_version": roster.aggregation_version,
            "classification_rule": roster.classification_rule,
            "range_rule": roster.range_rule,
            "start_condition_rule": roster.start_condition_rule,
            "aggregation_revision": roster.aggregation_revision,
            "arms": {
                a.arm_id: {
                    "evaluation_spec_id": a.evaluation_spec_id,
                    "evaluation_spec_content_fingerprint": a.evaluation_spec_content_fingerprint,
                    "execution_revision": a.execution_revision,
                    "discount_factor": a.discount_factor,
                    "report_fingerprint": reports[a.arm_id].report_fingerprint,
                }
                for a in roster.arms
            },
            "scoring_spec_content_fingerprint": scoring.spec.content_fingerprint,
            "protocol_fingerprint": model_fingerprint(bundle.protocol),
            "main_result_fingerprints": {c: ev.result_fingerprint for c, ev in ev_main.items()},
        },
    )
    dump("checks.json", [{"check": n, "passed": ok, "detail": d_} for n, ok, d_ in checks])
    (out / "checks.md").write_text(
        "\n".join(
            ["# E1-6B 演示的核对", ""]
            + [f"- {'通过' if ok else '不通过'}　{n}：{d_}" for n, ok, d_ in checks]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "README.md").write_text(
        "\n".join(_readme(roster, main_rep, sens, ev_ba1, hand_city, partial, checks, specs))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for n, ok, d_ in checks:
        print(f"- {'通过' if ok else '不通过'}　{n}：{d_}")
    if unexpected:
        print("有意外程序错误：见各臂报告的 unexpected_errors")
    print(f"\n结果已写入 {out}（合成演示，不排序真实城市）")
    return 0 if all(ok for _, ok, _ in checks) and not unexpected else 1


def _e16a_bundle() -> LifeBundle:
    from ledger.life_catalog import load_life_bundle

    bundle, _ = load_life_bundle(E1_6A_LIFE, require_settlement_ready=True)
    return bundle


def _readme(
    roster: Any,
    main_rep: ArmReport,
    sens: dict[str, Any],
    ev_f3: CloneEvaluation,
    hand_city: CityReport,
    partial: EntryRecord,
    checks: list[tuple[str, bool, str]],
    specs: dict[str, EvaluationSpec],
) -> list[str]:
    md = [
        "# E1-6B 城市汇总：完整者均值、确认出局与模拟缺失范围（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；分数是 [0, 1] 的无量纲满足感，不是货币，也不是现实幸福的百分比。全部数字来自"
        "合成样例，**不是现实城市评价，不排序真实城市**。实际三年的年度计划是分身的**预设计划**；参考估值用评价规格里预先声明的"
        "基础生活模板（估值期初带病时，每个模板再分治 / 不治两个分支）。",
        "",
        "## 顺序：先冻结名单，再运行",
        "",
        f"名单 `{roster.roster_id}`（内容身份 `{roster.content_fingerprint[:16]}…`）在跑任何生活之前冻结并写盘"
        "（`roster/frozen_roster.json`，`run_log.json` 第 0 步）。名单里记下了每个运行臂的协议、评价规格内容身份、δ、H、N 与时间"
        "权重，各城目录与税的指纹，每个名单项的开局、共同开局摘要与评分输入的指纹。u101.a95.r4 按设计**不运行**（模拟结果没有"
        "返回），它仍在名单里。",
        "",
        "| 用户 | 人 | 说明 | A95 | B96 |",
        "|---|---|---|---|---|",
    ]
    for u in roster.users:
        cells = []
        for city in (A95, B96):
            ids = [
                _short(e.clone_id)
                for e in roster.entries
                if e.user_id == u.user_id and str(e.city) == city
            ]
            cells.append("、".join(ids))
        md.append(f"| {u.user_id} | {u.person_id} | {u.label} | {cells[0]} | {cells[1]} |")
    md += [
        "",
        "## 公式",
        "",
        "* 完整评价分身的平均分 = Σ J ÷ C（同一批 C 的 L、V 平均一起给；C = 0 时为空，不填 0，不用 L 代替 J）；",
        "* 出局比例 = E ÷ N，缺失比例 = M ÷ N，N = 原计划人数 = C + E + M；",
        "* 每个 M 项：下界 = (K + W_future × v_low) ÷ (W_actual + W_future)，上界 = (K + W_actual − W_known + W_future × v_high) ÷ "
        "(W_actual + W_future)；K 是可信的已知实际月份的加权分数和，W_known 是它们的权重；V 完整时 v_low = v_high = V，否则 0 与 1；",
        "* 城市范围 = Σ 下界 ÷ (C + M) 到 Σ 上界 ÷ (C + M)；完整的取 [J, J]；确认出局者不进范围。",
        "",
        "主设定 δ = 1、N = 3、H = 12：W_actual = 36、W_future = 12。范围是“在当前评分尺度与已确认出局的排除规则下，给模拟尚不能计算"
        "的部分保留的可能范围”——**不是** 95% 置信区间，不是抽样误差，不是疾病概率，也不是全体真实人生的均值区间。",
        "",
        "几种月份的满足感（S = 0.8 × B + 0.2 × H；手算见 `docs/design/e1-6-evaluation.md`）：α 在 A95 基本生活 0.542072、大病病假"
        " 0.359484、小病 0.442072；α 在 B96 基本生活 0.536545（只差交通：0.8 × 1/9 × (60/103.75 − 60/116.25) = 0.005527）；"
        "β（只看重交通）在 A95 上班月 0.662651、大病月 0.619355，近处住房 0.809524，在 B96 上班月 0.612903。",
        "",
        "## 主臂（δ = 1，四个基础生活模板）的城市报告",
        "",
    ]
    for cr in main_rep.city_reports:
        md += _city_md(cr)
    md += ["## 城市比较（同一用户、同一运行臂）", ""]
    for c in main_rep.comparisons:
        md.append(
            f"* {c.user_id}：{_city(c.city_a)} 对 {_city(c.city_b)}，完整者均值差 {_v(c.mean_difference)}（两边有效分身 "
            f"{c.complete_a}/{c.planned_a} 与 {c.complete_b}/{c.planned_b}），范围差 [{_v(c.range_difference_low)}, "
            f"{_v(c.range_difference_high)}]：{c.note}"
        )
    md += [
        "",
        "## 缺失集中在哪里",
        "",
        "| 用户 | 城市 | 原因 | 人数 | 占原计划 | 分身 |",
        "|---|---|---|---|---|---|",
    ]
    for cr in main_rep.city_reports:
        for rc in cr.missing_reasons:
            md.append(
                f"| {cr.user_id} | {_city(cr.city)} | {MISSING_REASON_TEXT[rc.reason]} | {rc.count} | "
                f"{rc.count}/{cr.planned} | {'、'.join(rc.clone_ids)} |"
            )
        if cr.exits:
            md.append(
                f"| {cr.user_id} | {_city(cr.city)} | （确认出局，不是缺失） | {cr.exits} | {cr.exits}/{cr.planned} | "
                f"{'、'.join(cr.exit_clone_ids)} |"
            )
    md += [
        "",
        "不从这么小的样本拟合“哪类人容易失败”；这张表只让读者看出缺失集中在哪座城、哪种原因。",
        "",
        "## 评价对照（同一批实际生活，只改评价假设）",
        "",
    ]
    for arm, s in sens.items():
        md.append(
            f"### {arm}（{'δ = 0.99，只验证机制' if arm == D099 else '只含基本生活模板 ' + '、'.join(t.template_id for t in specs[arm].life_templates)}）"
        )
        md.append("")
        status = {
            "identical": "同一批实际生活（证据逐项配对后核对过）",
            "different": "实际生活不同——不是只改评价假设的对照",
            "evidence_mismatch": "证据与报告不配套——不能判断",
            "no_comparable_evidence": "没有可比较的证据——不能证明",
        }[s.lives_check]
        md.append(
            f"证据核对：{status}（`lives_check = {s.lives_check}`）。比较了 {len(s.compared_clone_ids)} 个分身的逐年封存"
            f"生活绑定；没有比较 {len(s.not_compared)} 个：{'；'.join(s.not_compared) or '无'}。{s.note}"
        )
        md.append("")
        md.append(
            "| 用户 | 城市 | 完整人数（主 → 对照） | J 均值（主 → 对照） | L 均值（主 → 对照） "
            "| V 均值（主 → 对照） | 范围（主 → 对照） | 换了类别的分身 |"
        )
        md.append("|---|---|---|---|---|---|---|---|")
        for ch in s.changes:
            md.append(
                f"| {ch.user_id} | {_city(ch.city)} | {ch.complete_main} → {ch.complete_other} | "
                f"{_v(ch.mean_j_main)} → {_v(ch.mean_j_other)} | "
                f"{_v(ch.mean_l_main)} → {_v(ch.mean_l_other)} | "
                f"{_v(ch.mean_v_main)} → {_v(ch.mean_v_other)} | "
                f"[{_v(ch.range_main_lower)}, {_v(ch.range_main_upper)}] → "
                f"[{_v(ch.range_other_lower)}, {_v(ch.range_other_upper)}] | {'；'.join(ch.moved) or '—'} |"
            )
        md.append("")
        md.append(
            "L、V、J 的均值各自用这个臂自己的那一批完整分身；两臂的完整分身不同时，差别的原因见“换了类别的分身”。"
        )
        md.append("")
        md.append(
            "比较方向的变化："
            + ("；".join(s.direction_changes) if s.direction_changes else "没有")
            + "。"
        )
        md.append("")
    v = ev_f3.valuation
    md += [
        "## 同一生活下治 / 不治（E1-6A 收尾 F3；β r1 在 A95）",
        "",
        "β 的分身第 3 年第 12 月得大病，**它自己的年度计划写的是大病不治**，于是带着病了 1 个月的大病进入终态（现金 6,820）。参考"
        f"估值不替分身做决定，而是把每个基础生活模板分成“条件允许就治”与“不治疗”两个分支，各自交给 E1-5C 结算"
        f"（治疗的资金、时间、精力、供给与病程规则照旧），共 {v.trials} 次试算，取参考集合内能完整执行的最高者。",
        "",
        "| 分支 | 模板 | 治疗选择 | 状态 | V | 现金（估值期初 → 末） | 逐月健康 | 没有完整的原因 |",
        "|---|---|---|---|---|---|---|---|",
        *[_plan_row(pl) for pl in v.plans],
        "",
        f"选中 `{v.selected_plan_id}`，V = {_v(v.v_reference)}。β 门店兼职每月只到岗 10 次：大病月 0.619355 低于上班月 0.662651，"
        "所以少病一个月更好。这只是参考估值，不声称实际分身一定会这样选择。",
        "",
        "交通—健康反例原样保留：对只看重交通、每月到岗 20 次的会计（E1-6A 样例的 u70），大病病假月 0.619355 反而高于上班月"
        " 0.569231。审核的 F3 反例就建在它上面（N = 1，带着大病结束，期末现金 3,700 与 3,850，其余相同）：v1 的较舒适方案写死"
        "“条件允许就治”，钱多的人治了病、分数反而更低；v2 里同一个较舒适模板有“不治疗”分支，钱多的人也能过"
        "钱少的人那份生活，两人的 V 在这个例子里相同，钱多的人选中 `p2_current_comfort.decline`——这是这个例子的事实，不是"
        "“现金更多 V 不下降”的一般定理（该验收要求已由所有者撤回）。见 `phase1/` 与 "
        "`tests/life/test_evaluation_closeout.py`。本轮不调疾病惩罚、不改交通权重，也不保证这个反例消失。",
        "",
        "## 公式构造单测（手工给定的名单项，不是模拟结果）",
        "",
        f"* 原计划 10 人：6 人 J 完整、合计 3.6；2 人确认出局；2 人完全缺失 → 均值 {_v(hand_city.mean_j)}，出局 "
        f"{_pct(hand_city.exit_share)}，缺失 {_pct(hand_city.missing_share)}，范围 [{_v(hand_city.range_lower)}, "
        f"{_v(hand_city.range_upper)}] = [3.6/8, 5.6/8]。",
        f"* 部分记录（δ = 1）：已知 24 个月、每月 0.6（K = 14.4），另外 12 个实际月与 12 个未来月未知 → [{_v(partial.lower)}, "
        f"{_v(partial.upper)}]。",
        "",
        "## 假设（影响谁）",
        "",
        *[f"* {a}" for a in ASSUMPTIONS],
        "* 名单里每城的重复分身很少（α 5 个、β 2 个）：均值不是稳定的抽样证据；缺失范围也不预测没跑完的分身继续运行后一定能完成",
        "",
        "## 核对",
        "",
        *[f"- {'通过' if ok else '不通过'}　{n}：{d_}" for n, ok, d_ in checks],
        "",
        "文件：`roster/frozen_roster.json`（冻结名单）、`run_log.json`（运行顺序）、`ledger/<臂>.csv`（分类账）、"
        "`evaluations/arm.main/`（主臂单分身结果全文）、`evaluations/summary.json`（三个臂的单分身摘要）、`reports/<臂>.json`"
        "（城市报告）、`sensitivity/<臂>.json`（评价对照）、`constructed_examples.json`（构造单测）、`checks.json`、"
        "`identity.json`、`environment.json`。",
    ]
    return md


if __name__ == "__main__":
    sys.exit(main())
