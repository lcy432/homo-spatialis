"""E1-6A 单分身跨期评价的离线演示（`DECISIONS/0028`）。零大模型调用。

    uv run python scripts/demo_life_e1_6a.py --out runs/<新目录>

用合成样例 ``configs/life/synthetic_e1_6a_evaluation.yaml``、评分配置 ``scoring_e1_6a_evaluation.yaml`` 与评价规格
``evaluation_e1_6a_v2.yaml``（2026-10-12 起：E1-6A 收尾把参考方案与治疗选择分开，见 DECISIONS/0029），每个分身按**预先写好的年度计划**实际生活三年（结算 → 评分 → 反馈，现有引擎原样），再做
单分身跨期评价：

    L_actual    = 实际 36 个月逐月满足感的加权平均（主设定 δ = 1）
    V_reference = 从真实终态出发，四个基础生活模板（带病期初每个模板展开治 / 不治两个分支）在 12 个月估值期里
                  真实结算、评分，参考集合内能完整执行的最高者
    J           = 0.75 L_actual + 0.25 V_reference（N = 3、H = 12、δ = 1）

四个可手算的故事：同样三年、期末现金不同；还能用的物品 vs 已到期的物品；学完的资格 vs 只学了一半；健康结束 vs 带着病
结束（另有资料缺口的一例）。再加交通—健康反例、δ = 0.99 的非 1 对照与 N = 1 对照。

全部数字来自合成样例：不是现实价格、工资、医疗或心理规律，**不是城市评价，也不排序城市**。V_reference 是参考集合内的
最高可实现评分，不是预测，也不是全局最优。目录已存在就拒绝，不覆盖旧结果。退出码：0 = 全部核对通过；1 = 有核对不通过，
或输入不合法。
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
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    YearAction,
    model_fingerprint,
)
from common.life_evaluation_contracts import (
    RESERVED_EVALUATION_KEYS,
    CloneEvaluation,
    EvaluationSpec,
    PlanEvaluation,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy, settle_year
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_evaluation_config import load_evaluation_config
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_6a_evaluation.yaml")
SCORING = Path("configs/life/scoring_e1_6a_evaluation.yaml")
EVALUATION = Path("configs/life/evaluation_e1_6a_v2.yaml")
KEY_SOURCES = (
    "src/common/life_evaluation_contracts.py",
    "src/common/life_feedback_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/ledger/life_evaluation_config.py",
    "src/ledger/life_catalog.py",
    "src/econ/life_evaluation.py",
    "src/econ/life_terminal_value.py",
    "src/econ/life_year.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_6a.py",
)

#: 几种月份的逐月满足感（评分器的最短十进制写法）。手算见下面的 HAND 与 docs/design/e1-6-evaluation.md。
S = {
    "basic": "0.5420722891566265",
    "basic_laundry": "0.5820722891566266",
    "basic_course": "0.5598500669344042",
    "sick_basic": "0.3594838709677419",
    "comfort": "0.6566137566137566",
    "accountant_basic": "0.5316923076923077",
    "accountant_comfort": "0.6481481481481481",
    "commute_well": "0.5692307692307692",
    "commute_sick": "0.6193548387096774",
}


def _hand() -> dict[str, Fraction]:
    """按评分规格的公式手算（精确分数）：S = 0.8 × B + 0.2 × H；B 按去掉医疗维后的权重
    物质 1/3、安全 1/9、发展 2/9、休闲 1/9、环境 1/9、交通 1/9 加权；s = z/(z+τ)（不超过上限 0.9）。
    交通：k = 0.25 + 0.75 × D/20，T_eq = k × T，s = 60/(60 + T_eq)。"""
    fr = Fraction

    def sat(z: Fraction, tau: int) -> Fraction:
        return min(fr(9, 10), z / (z + tau))

    def transport(minutes: int, visits: int) -> Fraction:
        k = fr(1, 4) + fr(3, 4) * fr(visits, 20)
        return fr(60) / (60 + k * minutes)

    def b(fresh: int, laundry: bool, leisure: int, growth: Fraction, minutes: int, visits: int):
        material = (
            fr(1, 2) * sat(fr(6), 6)  # 600 平方英尺 × 0.01 = 6（百平方英尺）
            + fr(3, 10) * sat(fr(fresh), 7)
            + fr(1, 5) * (sat(fr(3), 1) if laundry else 0)
        )
        safety = sat(fr(3), 3)
        opportunity = sat(2 + 12 * growth, 2)
        leisure_s = sat(fr(4 + leisure), 12)
        environment = sat(fr(3), 3)
        return (
            fr(1, 3) * material
            + fr(1, 9) * safety
            + fr(2, 9) * opportunity
            + fr(1, 9) * leisure_s
            + fr(1, 9) * environment
            + fr(1, 9) * transport(minutes, visits)
        )

    def s(bb: Fraction, h: Fraction) -> Fraction:
        return fr(4, 5) * bb + fr(1, 5) * h

    return {
        "basic": s(b(3, False, 0, fr(0), 70, 10), fr(1)),
        "basic_laundry": s(b(3, True, 0, fr(0), 70, 10), fr(1)),
        "basic_course": s(b(3, False, 0, fr(1, 12), 70, 10), fr(1)),
        "sick_basic": s(b(3, False, 0, fr(0), 70, 0), fr(0)),
        "comfort": s(b(21, True, 8, fr(0), 30, 10), fr(1)),
        "accountant_basic": s(b(3, False, 0, fr(0), 70, 20), fr(1)),
        "accountant_comfort": s(b(21, True, 8, fr(0), 30, 20), fr(1)),
        # 只看重交通的人：B 就是交通分
        "commute_well": s(transport(70, 20), fr(1)),
        "commute_sick": s(transport(70, 0), fr(0)),
    }


def f(text: str) -> Fraction:
    return Fraction(Decimal(text))


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
    files = [*KEY_SOURCES, LIFE.as_posix(), SCORING.as_posix(), EVALUATION.as_posix()]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "fingerprints_sha256": {f_: _sha256(REPO / f_) for f_ in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _revise(model: Any, **changes: Any) -> Any:
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


def _money(m: Any) -> str:
    return "—" if m is None else f"{m.amount:,.0f}"


def _v(x: Any) -> str:
    return "—" if x is None else f"{float(x.fraction()):.6f}"


class World:
    def __init__(self, bundle: LifeBundle, scoring: ScoringConfig, spec: EvaluationSpec) -> None:
        self.bundle, self.scoring, self.spec = bundle, scoring, spec

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

    def live(
        self,
        clone: str,
        protocol: LifeProtocol | None = None,
        start: PersonYearState | None = None,
    ) -> list[LifeYearResult]:
        state, catalog, tax, scoring = self.inputs(clone)
        state = start or state
        out: list[LifeYearResult] = []
        for plan in self.bundle.plans_for(clone):
            r = run_life_year(
                protocol=protocol or self.bundle.protocol,
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
        self,
        clone: str,
        results: list[LifeYearResult],
        spec: EvaluationSpec | None = None,
        protocol: LifeProtocol | None = None,
    ) -> CloneEvaluation:
        _, catalog, tax, scoring = self.inputs(clone)
        return evaluate_clone(
            spec=spec or self.spec,
            protocol=protocol or self.bundle.protocol,
            catalog=catalog,
            tax=tax,
            scoring=scoring,
            results=results,
        )


def plan_of(ev: CloneEvaluation, plan_id: str) -> PlanEvaluation:
    return next(p for p in ev.valuation.plans if p.plan_id == plan_id)


_STATUS = {
    "complete": "完整",
    "infeasible": "已知不可行",
    "excluded_hard_requirement": "违反硬要求",
    "unevaluable": "无法评价",
}


def _plan_cell(p: PlanEvaluation) -> str:
    """一个参考方案的结果，一句话：完整的给 V 与现金；没完整的给类别与最关键的一条原因。"""
    if p.status == "complete":
        return f"完整：V = {_v(p.v_plan)}；现金 {_money(p.cash_start)} → {_money(p.cash_end)}"
    why = p.reasons[0] if p.reasons else ""
    if p.reason_kind == "no_qualification" and "不满足资格 " in why:
        detail = "资格不足：缺 " + why.split("不满足资格 ", 1)[1].split("（", 1)[0]
    elif p.reason_kind == "slot_unavailable":
        detail = "本城确定没有这一项"
    elif p.reason_kind == "data_error" and "treatment_data_unknown" in p.problem_codes:
        detail = "资料缺口：要治疗，但本城治疗资料未知"
    else:
        detail = why.split("（", 1)[0][:48]
    return f"{_STATUS[p.status]}：{detail}"


def _state_lines(ev: CloneEvaluation) -> list[str]:
    ctx = ev.valuation.context
    if ctx is None:
        return ["没有估值上下文（实际生活没有正常结束）"]
    s = ctx.start
    hold = (
        "；".join(
            f"{h.opportunity_id} ×{h.quantity:g}（剩 {h.remaining_months} 个月，估值期后还剩 {h.beyond_horizon_months}）"
            for h in s.holdings
        )
        or "没有"
    )
    return [
        f"现金 {_money(s.cash)}；估值期月度精力额度 {float(s.energy_level.fraction()):.2f}"
        f"（终态里第 3 年用的是 {float(ctx.quota.terminal_quota.fraction()):.2f}）",
        f"持有物：{hold}",
        f"能力：{'、'.join(s.abilities) or '没有'}；当前岗位：{'、'.join(s.positions) or '没有'}",
        f"培训：{'；'.join(s.training) or '没有'}；疾病：{s.illness or '健康'}",
    ]


def _story_table(evs: dict[str, CloneEvaluation], labels: dict[str, str]) -> list[str]:
    clones = list(evs)
    head = "| | " + " | ".join(labels[c] for c in clones) + " |"
    rows = [head, "|---" * (len(clones) + 1) + "|"]

    def row(name: str, cells: list[str]) -> None:
        rows.append(f"| {name} | " + " | ".join(cells) + " |")

    row("L_actual（36 个月）", [_v(evs[c].actual.l_actual) for c in clones])
    row(
        "期末现金",
        [
            _money(evs[c].valuation.context.start.cash) if evs[c].valuation.context else "—"
            for c in clones
        ],
    )
    pids: list[
        str
    ] = []  # v2：健康期初每个模板一个分支，带病期初每个模板治 / 不治两个分支——各分身的分支可以不同
    for c in clones:
        pids += [p.plan_id for p in evs[c].valuation.plans if p.plan_id not in pids]
    for pid in pids:
        row(
            pid,
            [
                _plan_cell(plan_of(evs[c], pid))
                if any(p.plan_id == pid for p in evs[c].valuation.plans)
                else "—（这个分身没有这个分支）"
                for c in clones
            ],
        )
    row(
        "V_reference（选中）",
        [
            f"{_v(evs[c].valuation.v_reference)}（{evs[c].valuation.selected_plan_id or evs[c].valuation.status}）"
            for c in clones
        ],
    )
    row("J = 0.75 L + 0.25 V", [_v(evs[c].combined.j) for c in clones])
    return rows


def _monthly_csv(ev: CloneEvaluation) -> str:
    lines = ["part,index,year,month,status,health,score,weight,contribution"]
    for m in ev.actual.months:
        lines.append(
            f"actual,{m.index},{m.year_index},{m.month},{m.status},,{m.score_decimal or ''},"
            f"{m.weight.exact},{m.contribution.exact if m.contribution else ''}"
        )
    v = ev.valuation
    if v.selected_plan_id is not None:
        sel = plan_of(ev, v.selected_plan_id)
        t = ev.actual.months_total
        delta = Fraction(Decimal(ev.valuation.discount_factor))
        for k, s in enumerate(sel.month_scores, start=1):
            w = delta ** (t + k - 1)
            lines.append(
                f"future:{sel.plan_id},{t + k},{v.context.valuation_year},{k},scored,"
                f"{sel.health_states[k - 1] if sel.health_states else ''},{s},{w},"
                f"{w * f(s) if s else ''}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-6A 单分身跨期评价的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, _ = load_life_bundle(LIFE, require_settlement_ready=True)
        scoring, _ = load_scoring_config(SCORING, bundle)
        spec = load_evaluation_config(EVALUATION, bundle, scoring).spec
    except LifeContractError as exc:
        print(exc)
        return 1
    w = World(bundle, scoring, spec)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    hand = _hand()
    for key, text in S.items():
        check(
            f"月度满足感手算：{key}",
            abs(float(hand[key]) - float(f(text))) < 1e-12,
            f"手算 {float(hand[key]):.12f}，评分器 {text}（评分器用浮点，差 < 1e-12）",
        )

    lives = {s.clone_id: w.live(s.clone_id) for s in bundle.initial_states}
    terminal_fps = {
        c: model_fingerprint(rs[-1].next_state) for c, rs in lives.items() if rs[-1].next_state
    }
    evs = {c: w.evaluate(c, rs) for c, rs in lives.items()}
    sb, sc = f(S["basic"]), f(S["comfort"])

    def x(a: Any) -> Fraction | None:
        return None if a is None else a.fraction()

    # ------------------------------------------------------------ 故事一：同样三年，期末现金不同
    lo, hi = evs["u61.a93.low"], evs["u62.a93.rich"]
    check(
        "故事一：前三年相同",
        [m.score_decimal for m in lo.actual.months] == [m.score_decimal for m in hi.actual.months]
        and x(lo.actual.l_actual) == x(hi.actual.l_actual) == sb,
        "u61、u62 每个月都是基本生活：L = 0.542072",
    )
    check(
        "故事一：期末现金 4,820 / 13,320",
        (lo.valuation.context.start.cash.amount, hi.valuation.context.start.cash.amount)
        == (4820, 13320),
        "500 + 36 × 120 = 4,820；9,000 + 36 × 120 = 13,320",
    )
    p2lo, p2hi = plan_of(lo, "p2_current_comfort"), plan_of(hi, "p2_current_comfort")
    check(
        "故事一：较舒适的方案只有现金多的人付得起",
        p2lo.reason_kind == "plan_infeasible"
        and "第 7 月" in p2lo.reasons[0]
        and p2hi.cash_end.amount == 13320 - 2400 - 12 * 410,
        "4,820 + 1,500 − 1,910 − 2,400 = 2,010，之后每月 −410，第 7 月 1,520 − 380 < 1,400；"
        "13,320 − 2,400 − 12 × 410 = 6,000",
    )
    check(
        "故事一：V 与 J",
        x(lo.valuation.v_reference) == sb
        and x(hi.valuation.v_reference) == sc
        and x(hi.combined.j) == Fraction(3, 4) * sb + Fraction(1, 4) * sc,
        "u61：V = 0.542072（p1）；u62：V = 0.656614（p2），J = 0.75 × 0.542072 + 0.25 × 0.656614 = 0.570708",
    )

    # ------------------------------------------------------------ 现金网格：同一套三年生活，只改开局现金
    base61 = bundle.initial_state("u61.a93.low")
    assert base61 is not None
    grid = []
    for c in (500, 2000, 2999, 3000, 4000, 9000, 20000):
        start = _revise(base61, cash={"amount": c, "numeraire": "USD_2024_LOCAL"})
        rs = w.live("u61.a93.low", start=start)
        ev = w.evaluate("u61.a93.low", rs)
        grid.append(
            (
                c,
                rs[-1].next_state.cash.amount,
                ev.valuation.selected_plan_id,
                x(ev.valuation.v_reference),
                x(ev.combined.j),
                x(ev.actual.l_actual),
            )
        )
    vs = [g[3] for g in grid]
    check(
        "现金网格：V 不随现金下降，付得起之后饱和",
        vs == sorted(vs) and vs == [sb] * 3 + [sc] * 4 and {g[5] for g in grid} == {sb},
        "期末现金 = 开局 + 36 × 120；较舒适方案要期末 2,400 + 12 × 410 = 7,320（恰好等于也够）；L 都是 0.542072",
    )

    # ------------------------------------------------------------ 故事二：还能用的物品 vs 已到期的物品
    dr, us = evs["u63.a93.dryer"], evs["u64.a93.used"]
    (h,) = dr.valuation.context.start.holdings
    p2dr, p2us = plan_of(dr, "p2_current_comfort"), plan_of(us, "p2_current_comfort")
    check(
        "故事二：洗烘一体机照用、不重复付款、不重置寿命",
        (h.remaining_months, h.beyond_horizon_months) == (108, 96)
        and len(p2dr.skipped_purchases) == 1
        and p2dr.cash_end.amount == 5420 - 12 * 410
        and [(e.remaining_months) for e in p2dr.end.holdings] == [96],
        "120 − 12 = 108 → 估值期末 96；现金 5,420 − 12 × 410 = 500（没有再付 2,400）",
    )
    check(
        "故事二：二手洗衣机已到期，要重新买设备的方案付不起",
        us.valuation.context.start.holdings == ()
        and p2us.reason_kind == "plan_infeasible"
        and "第 12 月" in p2us.reasons[0],
        "7,220 + 1,500 − 1,910 − 2,400 = 4,410，之后每月 −410：第 12 月 1,810 − 1,400 = 410 < 450",
    )
    check(
        "故事二：现金更多的 u64，V 反而更低",
        x(dr.valuation.v_reference) == sc and x(us.valuation.v_reference) == sb,
        "u63：0.656614；u64：0.542072；L 相同 = (24 × 0.542072 + 12 × 0.582072) / 36",
    )

    # ------------------------------------------------------------ 故事三：学完的资格 vs 只学了一半
    le, ha = evs["u65.a93.learned"], evs["u66.a93.halfway"]
    check(
        "故事三：学完的资格从第 4 年（估值期）起可用",
        le.valuation.context.start.abilities == ("accounting_cert（第 4 年起可用）",)
        and le.valuation.selected_plan_id == "p4_qualified_comfort"
        and plan_of(le, "p4_qualified_comfort").cash_end.amount == 5120 + 12 * 1090 - 2400,
        "会计每月 3,000：5,120 + 12 × (3,000 − 1,910) − 2,400 = 15,800",
    )
    check(
        "故事三：只学了一半——没有资格、不续学、不退款",
        all(
            plan_of(ha, p).reason_kind == "no_qualification"
            for p in ("p3_qualified_basic", "p4_qualified_comfort")
        )
        and any("6/12" in n for n in ha.valuation.not_valued)
        and plan_of(ha, "p1_current_basic").cash_end.amount == 5120 + 12 * 120,
        "已付学费 1,200 不退；6/12 的进度不续学，这部分潜力在报告里列为未估值",
    )
    check(
        "故事三：工资不进满足感——会计的基本生活分数反而略低",
        f(S["accountant_basic"]) < sb and x(le.valuation.v_reference) == f(S["accountant_comfort"]),
        "会计每月到岗 20 次（门店 10 次），广义交通分更低；资格的作用是让较舒适的生活付得起",
    )

    # ------------------------------------------------------------ 故事四：健康结束 vs 带着病结束；资料缺口
    we, si, gap = evs["u67.a93.well"], evs["u68.a93.sick"], evs["u69.b94.gap"]
    # v2：同一个模板 p1 的治 / 不治两个分支（v1 的 p1 与 p5）
    p1si, p5si = plan_of(si, "p1_current_basic.treat"), plan_of(si, "p1_current_basic.decline")
    ss = f(S["sick_basic"])
    check(
        "故事四：期末的大病在估值期照常走完，不发生新病",
        p1si.health_states == ("major",) + ("healthy",) * 11
        and p5si.health_states == ("major", "major") + ("healthy",) * 10
        and all(p.new_onsets == 0 for p in si.valuation.plans),
        "终态病了 1 个月；治疗：第 1 月末恢复；不治疗：共 3 个月，第 2 月末恢复",
    )
    check(
        "故事四：估值期现金",
        p1si.cash_end.amount == 6820 - 1380 - 2000 + 11 * 120
        and p5si.cash_end.amount == 6820 - 2 * 1380 + 10 * 120,
        "治疗：6,820 − 1,380 − 2,000 + 11 × 120 = 4,760；不治疗：6,820 − 2 × 1,380 + 10 × 120 = 5,260",
    )
    check(
        "故事四：V 与 J",
        x(p1si.v_plan) == (ss + 11 * sb) / 12
        and x(si.valuation.v_reference) == (ss + 11 * sb) / 12
        and x(we.valuation.v_reference) == sc,
        "u68：(0.359484 + 11 × 0.542072) / 12 = 0.526857；u67：0.656614",
    )
    kinds = {p.plan_id: p.reason_kind for p in gap.valuation.plans}
    check(
        "故事四：资料缺口——不给 V，也不给 J",
        gap.valuation.status == "incomplete"
        and kinds["p1_current_basic.treat"] == "data_error"
        and kinds["p3_qualified_basic.treat"] == "slot_unavailable"
        and gap.valuation.best_known_plan_id == "p1_current_basic.decline"
        and gap.combined.j is None,
        "B94 住院资料未知：要治疗的方案无法评价；B94 确定没有会计岗位：已知不可行；"
        "已知最好结果（p1 的不治疗分支）只作诊断",
    )

    # ------------------------------------------------------------ 交通—健康反例
    cs, cw = evs["u70.a93.commute_sick"], evs["u71.a93.commute_well"]
    check(
        "反例：只看重交通的人，大病月分数更高（原样展示）",
        cs.actual.months[4].score_decimal == S["commute_sick"]
        and x(cs.actual.l_actual) > x(cw.actual.l_actual)
        and x(cs.combined.j) > x(cw.combined.j),
        "上班月 0.8 × 60/130 + 0.2 = 0.569231；大病月 0.8 × 60/77.5 + 0 = 0.619355",
    )

    # ------------------------------------------------------------ 额度、终态隔离、观察
    q63 = dr.valuation.context.quota
    check(
        "估值额度用第 3 年的完整年度满足感",
        q63.score_decimal == S["basic_laundry"]
        and x(q63.quota) == Fraction(26455, 100)
        and x(q63.terminal_quota) == Fraction(26355, 100),
        "250 × (1 + 0.10 × 0.582072) = 264.5518 → 264.55；终态里的 263.55 是第 3 年自己用的额度",
    )
    check(
        "真实终态没有被估值改动",
        all(
            model_fingerprint(lives[c][-1].next_state) == fp
            and evs[c].valuation.context.terminal_fingerprint == fp
            for c, fp in terminal_fps.items()
            if evs[c].valuation.context is not None
        ),
        "估值前后终态指纹相同；估值上下文只在估值入口构造",
    )
    s63 = lives["u63.a93.dryer"][-1].next_state
    _, cat63, tax63, _ = w.inputs("u63.a93.dryer")
    refused = settle_year(
        protocol=bundle.protocol,
        state=s63,
        action=YearAction.model_validate(
            {
                "person_id": "u63",
                "clone_id": "u63.a93.dryer",
                "year_index": 4,
                "items": [],
                "emergency": {"minor": "treat_if_possible", "major": "treat_if_possible"},
            }
        ),
        catalog=cat63,
        tax=tax63,
    )
    check(
        "普通结算照旧拒绝真实终态",
        refused.status == "input_error"
        and [p.code for p in refused.problems] == ["state_not_active"],
        "终态不接受实际行动的规则没有放宽",
    )
    r2 = lives["u63.a93.dryer"][1]
    p = bundle.protocol
    obs = build_observation(
        permission=standard_product_permission("demo"),
        profile=next(x_ for x_ in bundle.stated_profiles if x_.person_id == "u63"),
        state=r2.next_state,
        catalog=cat63,
        last_feedback=visible_feedback(r2.settlement),
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=p.health_rules,
        health_input=p.health_input,
    )
    payload = observation_payload(obs)
    text = json.dumps(payload, ensure_ascii=False)
    check(
        "评价内容不进观察",
        not any(f'"{k}"' in text for k in RESERVED_EVALUATION_KEYS),
        "第 3 年年初的观察里没有 L、V、J、参考方案或评价参数",
    )

    # ------------------------------------------------------------ δ = 0.99 与 N = 1 对照
    s99 = _revise(spec, spec_id="eval.e1-6a.delta099", monthly_discount_factor="0.99")
    e99 = w.evaluate("u68.a93.sick", lives["u68.a93.sick"], spec=s99)
    d = Fraction(99, 100)
    wts = [d**k for k in range(36)]
    ms = [sb] * 35 + [ss]
    check(
        "δ = 0.99 对照：权重与方向",
        [m.weight.fraction() for m in e99.actual.months] == wts
        and x(e99.actual.l_actual) == sum(a * b for a, b in zip(wts, ms, strict=True)) / sum(wts)
        and x(e99.actual.l_actual) > x(si.actual.l_actual)
        and x(e99.valuation.v_reference) < x(si.valuation.v_reference),
        "生病的月份在实际生活末尾（权重小）、估值期开头（权重大）：L 变高、V 变低；只验证机制",
    )
    p1y = _revise(bundle.protocol, simulation_years=1)
    one = {c: w.evaluate(c, w.live(c, p1y), protocol=p1y) for c in ("u61.a93.low", "u62.a93.rich")}
    check(
        "N = 1 对照：T = 12，实际与估值各占一半",
        all(e.actual.months_total == 12 for e in one.values())
        and x(one["u62.a93.rich"].combined.share_actual) == Fraction(1, 2)
        and x(one["u62.a93.rich"].combined.j) == (sb + sc) / 2,
        "u62：一年后现金 10,440 付得起较舒适方案，J = (0.542072 + 0.656614) / 2 = 0.599343",
    )
    replay = w.evaluate("u62.a93.rich", w.live("u62.a93.rich"))
    check(
        "同样输入重放逐字节相同",
        replay.model_dump_json() == hi.model_dump_json(),
        f"结果指纹 {hi.result_fingerprint[:16]}…",
    )

    # ------------------------------------------------------------ 写出
    args.out.mkdir(parents=True)
    (args.out / "configs").mkdir()
    for src in (LIFE, SCORING, EVALUATION):
        shutil.copyfile(REPO / src, args.out / "configs" / src.name)

    def dump(name: str, data: Any) -> None:
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    (args.out / "evaluations").mkdir()
    (args.out / "monthly").mkdir()
    for c, ev in evs.items():
        dump(f"evaluations/{c}.json", ev.model_dump(mode="json"))
        (args.out / "monthly" / f"{c}.csv").write_text(
            _monthly_csv(ev), encoding="utf-8", newline="\n"
        )
    dump("evaluations/u68.a93.sick.delta099.json", e99.model_dump(mode="json"))
    for c, ev in one.items():
        dump(f"evaluations/{c}.n1.json", ev.model_dump(mode="json"))
    dump(
        "plan_selection.json",
        {
            c: {
                "trajectory": ev.trajectory.status,
                "owner_category": ev.trajectory.owner_category,
                "l_actual": None if ev.actual.l_actual is None else ev.actual.l_actual.exact,
                "valuation": ev.valuation.status,
                "selected": ev.valuation.selected_plan_id,
                "v_reference": None
                if ev.valuation.v_reference is None
                else ev.valuation.v_reference.exact,
                "best_known": None
                if ev.valuation.best_known is None
                else [ev.valuation.best_known_plan_id, ev.valuation.best_known.exact],
                "j": None if ev.combined.j is None else ev.combined.j.exact,
                "plans": [
                    {
                        "plan_id": p_.plan_id,
                        "status": p_.status,
                        "reason_kind": p_.reason_kind,
                        "v_plan": None if p_.v_plan is None else p_.v_plan.exact,
                        "cash_start": None if p_.cash_start is None else p_.cash_start.amount,
                        "cash_end": None if p_.cash_end is None else p_.cash_end.amount,
                        "reasons": list(p_.reasons),
                        "skipped_purchases": list(p_.skipped_purchases),
                    }
                    for p_ in ev.valuation.plans
                ],
            }
            for c, ev in evs.items()
        },
    )
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "reproduce": "uv run python scripts/demo_life_e1_6a.py --out <新目录>",
        },
    )
    dump("environment.json", _environment())
    dump(
        "identity.json",
        {
            "evaluation_spec_id": spec.spec_id,
            "evaluation_spec_content_fingerprint": spec.content_fingerprint,
            "delta099_spec_content_fingerprint": s99.content_fingerprint,
            "scoring_spec_content_fingerprint": scoring.spec.content_fingerprint,
            "protocol_fingerprint": model_fingerprint(bundle.protocol),
            "result_fingerprints": {c: ev.result_fingerprint for c, ev in evs.items()},
        },
    )
    dump("checks.json", [{"check": n, "passed": ok, "detail": d_} for n, ok, d_ in checks])
    (args.out / "checks.md").write_text(
        "\n".join(
            ["# E1-6A 演示的核对", ""]
            + [f"- {'通过' if ok else '不通过'}　{n}：{d_}" for n, ok, d_ in checks]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    plans_md = [
        f"* `{p_.template_id}`（{p_.label}）：{p_.description}" for p_ in spec.life_templates
    ]
    md = [
        "# E1-6A 单分身跨期评价：四个故事（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；分数是 [0, 1] 的无量纲满足感，不是货币，也不是现实幸福的百分比。全部数字"
        "来自合成样例，**不是现实价格、工资、医疗或心理规律，不是城市评价，也不排序城市**。实际三年的年度计划是**分身的预设"
        "计划**；参考估值用评价规格里预先声明的四个基础生活模板（估值期初带病时，每个模板再分治 / 不治两个分支），不是"
        "代理的真实行动。",
        "",
        "## 公式（主设定 δ = 1，N = 3，H = 12）",
        "",
        "* L_actual = 实际 36 个月逐月满足感 S_m 的平均（36 个月都有合法评分才给）；",
        "* V_p = 参考方案 p 在估值期 12 个月里真实结算、评分后的平均；V_reference = 参考集合内能完整执行、完整评分、满足硬要求"
        "的最高者——**不是预测，也不是全局最优**；",
        "* J = (36 × L_actual + 12 × V_reference) / 48 = 0.75 L_actual + 0.25 V_reference。",
        "",
        "参考方案：",
        "",
        *plans_md,
        "",
        "几种月份的满足感（S = 0.8 × B + 0.2 × H；手算见 `docs/design/e1-6-evaluation.md`）：基本生活 0.542072；加一台洗衣设备"
        " 0.582072；上会计课的月份 0.559850；大病病假 0.359484；较舒适（近处住房、新鲜食材、健身、洗衣设备）0.656614；会计 + 基本"
        " 0.531692；会计 + 较舒适 0.648148。",
        "",
        "## 故事一：同样三年，期末现金不同（u61 开局 500，u62 开局 9,000）",
        "",
        "两人三年里每个月都是门店兼职 1,500、远处住房 1,000、基本食品 380，每月净存 120。",
        "",
        *_story_table(
            {"u61.a93.low": lo, "u62.a93.rich": hi},
            {"u61.a93.low": "u61（期末 4,820）", "u62.a93.rich": "u62（期末 13,320）"},
        ),
        "",
        "u61 期初状态：" + "；".join(_state_lines(lo)),
        "",
        "**多留现金能做什么（同一套三年生活，只改开局现金）：** 较舒适方案一年要 2,400（洗烘一体机）+ 12 × 410 = 7,320 的缓冲。"
        "现金不够时它已知不可行；够了之后再多的现金不再提高这 12 个月的估值（有限期估值的饱和，如实报告）。",
        "",
        "| 开局现金 | 期末现金 | 选中方案 | V_reference | J |",
        "|---|---|---|---|---|",
        *[
            f"| {g[0]:,} | {g[1]:,.0f} | {g[2]} | {float(g[3]):.6f} | {float(g[4]):.6f} |"
            for g in grid
        ],
        "",
        "## 故事二：还能用的物品 vs 已到期的物品（u63 vs u64）",
        "",
        "两人第 3 年第 1 月都买了洗衣设备：u63 买 10 年寿命的洗烘一体机（2,400），u64 买只能再用 1 年的二手洗衣机（600）。"
        "前三年分数相同；u64 期末现金更多，但设备第 3 年末到期。",
        "",
        *_story_table(
            {"u63.a93.dryer": dr, "u64.a93.used": us},
            {
                "u63.a93.dryer": "u63（洗烘一体机剩 108 个月）",
                "u64.a93.used": "u64（二手洗衣机已到期）",
            },
        ),
        "",
        "u63 期初状态：" + "；".join(_state_lines(dr)),
        "",
        "## 故事三：学完的资格 vs 只学了一半（u65 vs u66）",
        "",
        "u65 第 3 年上满 12 个月会计课，资格从第 4 年起可用；u66 第 7 月才开课，期末 6/12，学费已付。",
        "",
        *_story_table(
            {"u65.a93.learned": le, "u66.a93.halfway": ha},
            {"u65.a93.learned": "u65（学完）", "u66.a93.halfway": "u66（学了一半）"},
        ),
        "",
        "u66 期初状态：" + "；".join(_state_lines(ha)),
        "",
        "## 故事四：健康结束 vs 带着病结束（u67 vs u68），以及资料缺口（u69）",
        "",
        "u68 第 3 年第 12 月得大病、计划不治，带着病了 1 个月的大病进入终态；估值期没有新发疾病，但这场病照常走完。"
        "u69 在 B94 同样带着大病结束，而 B94 的住院资料未知。",
        "",
        *_story_table(
            {"u67.a93.well": we, "u68.a93.sick": si, "u69.b94.gap": gap},
            {
                "u67.a93.well": "u67（健康）",
                "u68.a93.sick": "u68（大病，A93）",
                "u69.b94.gap": "u69（大病，B94 资料缺口）",
            },
        ),
        "",
        f"u68 的估值期：治疗方案逐月健康 {list(p1si.health_states[:3])}…；不治疗方案 {list(p5si.health_states[:3])}…",
        "",
        "## 交通—健康反例（u70 vs u71，原样展示）",
        "",
        "两人只看重交通（交通 90、健康风险应对 10，后者不进满足感）。u70 第 1 年第 5 月大病病假：那个月不用到岗，广义交通分"
        " 60/(60 + 0.25 × 70) 高于上班月的 60/(60 + 70)，多出的部分超过健康项扣掉的 0.2。"
        f"结果 u70 的 L = {_v(cs.actual.l_actual)} 高于没生病的 u71（{_v(cw.actual.l_actual)}），J 也更高"
        f"（{_v(cs.combined.j)} vs {_v(cw.combined.j)}）。这是 E1-5A 广义交通与 E1-5C 健康项相加的已知反例；"
        "本轮不调疾病惩罚、不改交通权重，留给后续讨论。",
        "",
        "## 对照",
        "",
        f"* δ = 0.99（只验证机制，不是主设定）：u68 的 L = {_v(e99.actual.l_actual)}（主设定 {_v(si.actual.l_actual)}），"
        f"V = {_v(e99.valuation.v_reference)}（主设定 {_v(si.valuation.v_reference)}），实际与估值的权重份额 "
        f"{float(e99.combined.share_actual.fraction()):.4f} / {float(e99.combined.share_future.fraction()):.4f}。",
        f"* N = 1：u62 的 T = 12，J = {_v(one['u62.a93.rich'].combined.j)} = (0.542072 + 0.656614) / 2。",
        "",
        "## 假设（影响谁）",
        "",
        *[f"* {a}" for a in ASSUMPTIONS_TEXT],
        "",
        "## 手算核对",
        "",
        *[f"- {'通过' if ok else '不通过'}　{n}：{d_}" for n, ok, d_ in checks],
    ]
    (args.out / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    for n, ok, d_ in checks:
        print(f"- {'通过' if ok else '不通过'}　{n}：{d_}")
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


ASSUMPTIONS_TEXT = (
    "基础生活模板只有四个（带病期初每个模板治 / 不治两个分支）：V_reference 只在这些分支里取最高，不是全局最优；愿意过"
    "别的生活方式的人被低估",
    "估值期没有新发疾病（确定性条件，不是把患病概率改成 0）：对健康风险高的人，估值偏乐观",
    "价格冻结在当地基年口径，目录不变：对价格会大幅变化的城市不适用",
    "不新开、不续学课程：只学了一半的人，已付学费与进度的潜力没有估值（列在未估值清单里）",
    "估值期只有 12 个月，之后的现金与物品不再计值：长寿命物品、大额存款的长期价值被低估",
    "δ = 1 只是主设定，不代表任何人的真实时间偏好；δ = 0.99 对照只验证机制",
)


if __name__ == "__main__":
    sys.exit(main())
