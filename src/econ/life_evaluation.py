"""E1-6A 单分身跨期评价（`DECISIONS/0028`）：核对实际轨迹 → 逐月汇总 L_actual → 期末估值 V_reference → 合成 J。

    L_actual = Σ_{m=1..T} δ^(m−1) S_m ÷ Σ_{m=1..T} δ^(m−1)                      （T = 12N，N 读协议）
    J        = [Σ_m δ^(m−1) S_m + Σ_{k=1..H} δ^(T+k−1) S_future[选中,k]] ÷ (W_actual + W_future)

N = 3、H = 12、δ = 1 时 J = 0.75 L_actual + 0.25 V_reference。J 是 [0, 1] 的无量纲分数，不是货币、不是"真实幸福的百分比"。

没有新的生活数学：S_m 是评分器对同一次实际生活给出的逐月满足感，这里只把它的浮点结果按最短十进制写法变成精确分数
（``econ.life_feedback.score_fraction``）再加权。不对城市做 z-score，不取最后一年代替全部生活，不对已经递减映射过的月度分
再取对数，不用现金或工资代替满足感。

**只接受受控运行里产生的结果**：每一年的 ``LifeYearResult`` 必须带着 ``run_life_year`` 当场封存的年度绑定（私有属性）。
核对：封存与对象指纹；协议、目录、税、评分输入与评价器拿到的相同（产品陈述与研究真值不混）；同一人、分身、城市；年份
从 1 起连续、不重复；第 y 年正式交接的状态正是第 y+1 年绑定里的年初状态。只凭相同的字符串 ID 或外部给的哈希不足以
证明配对；从 JSON 还原的结果没有封存的绑定，明确拒绝（请从保存的原始输入重放）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from common.ids import CityId
from common.life_contracts import (
    MONTHS_PER_YEAR,
    CityLifeCatalog,
    ExactAmount,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    model_fingerprint,
)
from common.life_evaluation_contracts import (
    PLANNING_STOP_TEXT,
    ActualLifeScore,
    ActualMonth,
    CloneEvaluation,
    CombinedScore,
    EvaluationSpec,
    LifeEvent,
    MonthFact,
    PlanningStop,
    TerminalValuation,
    TrajectoryRecord,
    YearTrace,
)
from common.life_feedback_contracts import LifeYearResult, ScoredYear
from common.life_scoring_contracts import HardRequirementResult
from econ.life_feedback import feedback_record, score_fraction
from econ.life_settlement import LifeTaxBinding
from econ.life_terminal_value import check_city_binding, requirement_trajectories, value_terminal
from econ.life_year import ScoringInputs, _scoring_fingerprint, _tax_fingerprint, handoff_from

_OWNER = {
    "complete": "completed_scored",
    "life_exit": "life_exit",
    "stopped": "stopped_kept_as_is",
    "score_incomplete": "simulation_incomplete",
    "feedback_blocked": "simulation_incomplete",
    "settlement_failed": "simulation_incomplete",
    "not_finished": "simulation_incomplete",
}


@dataclass(frozen=True)
class VerifiedTrajectory:
    """核对过的实际轨迹：每年的交接结果与封存绑定、分类记录、终态（正常结束时）与第 N 年的绑定，以及从封存
    绑定取得的身份（人、分身、城市、重复分身序号）。"""

    results: tuple[LifeYearResult, ...]
    years: tuple[ScoredYear, ...]
    record: TrajectoryRecord
    terminal: PersonYearState | None
    last_year: ScoredYear | None
    person_id: str
    clone_id: str
    city: CityId
    repeat_index: int | None


def _events(results: Sequence[LifeYearResult]) -> tuple[LifeEvent, ...]:
    out: list[LifeEvent] = []
    for r in results:
        o = r.settlement.outcome
        if o is None:
            continue
        if o.health is not None:
            for m in o.health.months:
                if m.onset is not None:
                    out.append(
                        LifeEvent(
                            year_index=o.year_index,
                            month=m.month,
                            event_kind="illness_onset",
                            detail=f"新发{'小病' if m.onset == 'minor' else '大病'}（{m.onset_source}）",
                        )
                    )
                if m.sick_leave:
                    out.append(
                        LifeEvent(
                            year_index=o.year_index,
                            month=m.month,
                            event_kind="sick_leave",
                            detail="大病病假：没有工资",
                        )
                    )
                if m.treatment is not None and m.treatment.status == "treated":
                    paid = m.treatment.amount_paid
                    out.append(
                        LifeEvent(
                            year_index=o.year_index,
                            month=m.month,
                            event_kind="treated",
                            detail=f"治疗，付 {paid.amount:,.2f}" if paid is not None else "治疗",
                        )
                    )
        if o.exit is not None:
            out.append(
                LifeEvent(
                    year_index=o.exit.year_index,
                    month=o.exit.month,
                    event_kind="exit",
                    detail=f"付不起最低住房与食品，差 {o.exit.shortfall.amount:,.2f}",
                )
            )
        if o.stop is not None:
            out.append(
                LifeEvent(
                    year_index=o.stop.year_index,
                    month=o.stop.month,
                    event_kind="stop",
                    detail=f"{o.stop.kind}：{o.stop.reason}"[:400],
                )
            )
    return tuple(out)


def verify_trajectory(
    *,
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs,
    results: Sequence[LifeYearResult],
) -> VerifiedTrajectory:
    """核对一个分身的实际轨迹并分类。混用、断档、重复、错终态、没有封存绑定都直接拒绝（``LifeContractError``）；
    合法但没有完整结束的轨迹按最后一次交接分类（出局、停止、反馈受阻、结算失败、没跑完）。"""
    if type(protocol) is not LifeProtocol or type(catalog) is not CityLifeCatalog:
        raise LifeContractError("protocol 必须是 LifeProtocol，catalog 必须是 CityLifeCatalog")
    if not results:
        raise LifeContractError("没有实际轨迹：至少要有第 1 年的交接结果")
    protocol_fp, catalog_fp = model_fingerprint(protocol), model_fingerprint(catalog)
    tax_fp, scoring_fp = _tax_fingerprint(tax), _scoring_fingerprint(scoring)
    years: list[ScoredYear] = []
    for r in results:
        if type(r) is not LifeYearResult:
            raise LifeContractError(f"实际轨迹只接受 LifeYearResult，得到 {type(r).__name__}")
        y = r.scored_year
        who = f"{r.clone_id} 第 {r.year_index} 年"
        if y is None or not y.sealed:
            raise LifeContractError(
                f"{who}：结果没有带着封存的年度绑定——从 JSON 还原或手工构造的结果证明不了"
                "账目、分数与交接来自同一次生活。本轮不支持直接导入，请从保存的原始输入重放"
            )
        problems = y.object_problems()
        if problems:
            raise LifeContractError(f"{who}：年度绑定被改动过：" + "；".join(problems))
        if str(catalog.city) != str(y.city):
            raise LifeContractError(
                f"{who}：评价器拿到的是 {catalog.city} 的目录，封存的实际生活在 {y.city}"
            )
        if r.binding_fingerprint != y.binding_fingerprint:
            raise LifeContractError(f"{who}：交接结果与年度绑定指向不同的生活")
        if model_fingerprint(r.settlement) != model_fingerprint(y.settlement) or (
            (r.score is None) != (y.score is None)
            or (r.score is not None and model_fingerprint(r.score) != y.score_fingerprint)
        ):
            raise LifeContractError(f"{who}：交接结果里的账目或分数不是年度绑定里的那一份")
        # E1-6A 收尾 F1：外层标签必须就是封存的那一次生活的身份——成绩只认封存记录，不认信封上的城市
        outer = (r.person_id, r.clone_id, str(r.city), r.year_index)
        sealed = (y.person_id, y.clone_id, str(y.city), y.year_index)
        if outer != sealed:
            bad = [
                f"{name}：外层写 {a}，封存记录是 {b}"
                for name, a, b in zip(("人", "分身", "城市", "年份"), outer, sealed, strict=True)
                if a != b
            ]
            raise LifeContractError(
                f"第 {len(years) + 1} 份年度结果的外层身份与它封存的实际生活不一致（{'；'.join(bad)}）："
                "不能把一次生活的成绩换个标签归给别的分身、城市或年份"
            )
        if y.protocol_fingerprint != protocol_fp:
            raise LifeContractError(f"{who}：这一年在另一份协议下结算——不能和这份协议的评价混用")
        if y.catalog_fingerprint != catalog_fp:
            raise LifeContractError(f"{who}：这一年用的城市目录不是评价器拿到的这一份")
        if y.tax_fingerprint != tax_fp:
            raise LifeContractError(f"{who}：这一年用的税绑定不是评价器拿到的这一份")
        if y.scoring_inputs_fingerprint != scoring_fp:
            raise LifeContractError(
                f"{who}：这一年的评分输入（规格、城市评分条件、偏好、硬要求）与评价器拿到的不同——"
                "产品陈述与研究真值不混用，估值也必须用同一份评分输入"
            )
        # 交接不信结果对象里的字段：从封存绑定按这份协议重新推出反馈记录、交接种类与交出去的状态
        record = feedback_record(protocol=protocol, year=y)
        handoff, handed = handoff_from(record, y)
        if model_fingerprint(record) != model_fingerprint(r.feedback):
            raise LifeContractError(f"{who}：反馈记录不是由这一年的封存绑定按这份协议得出的")
        if r.handoff != handoff or (
            (handed is None) != (r.next_state is None)
            or (handed is not None and model_fingerprint(handed) != model_fingerprint(r.next_state))
        ):
            raise LifeContractError(
                f"{who}：交接结果（{r.handoff}）或交出去的状态不是封存绑定推出的那一份（{handoff}）——"
                "终态与下一年状态只认真实交接"
            )
        years.append(y)
    first = years[0]
    ident = (first.person_id, first.clone_id, str(first.city))
    for y in years:
        if (y.person_id, y.clone_id, str(y.city)) != ident:
            raise LifeContractError(
                f"轨迹里混了别的分身或别的城：{(y.person_id, y.clone_id, str(y.city))} ≠ {ident}"
            )
    if scoring.preference_input.person_id != first.person_id:
        raise LifeContractError("评分输入的偏好属于另一个人")
    got = [y.year_index for y in years]
    if got != list(range(1, len(results) + 1)):
        raise LifeContractError(f"年份必须从第 1 年起连续、不重复，得到 {got}")
    n = protocol.simulation_years
    if len(results) > n:
        raise LifeContractError(f"轨迹有 {len(results)} 年，超过协议的 N = {n}")
    for i in range(len(results) - 1):
        r, nxt = results[i], years[i + 1]
        if r.handoff != "next_year_ready" or r.next_state is None:
            raise LifeContractError(
                f"第 {r.year_index} 年的交接是 {r.handoff}，之后不该还有第 {r.year_index + 1} 年"
            )
        if model_fingerprint(r.next_state) != nxt.start_fingerprint:
            raise LifeContractError(
                f"第 {r.year_index + 1} 年的年初状态不是第 {r.year_index} 年正式交接的那一份——不能把不同试算的年份接在一起"
            )
    last, last_year = results[-1], years[-1]
    status = {
        "terminal": "complete",
        "exited": "life_exit",
        "stopped": "stopped",
        "feedback_blocked": "feedback_blocked",
        "settlement_failed": "settlement_failed",
        "next_year_ready": "not_finished",
    }[last.handoff]
    terminal = None
    reasons: list[str] = []
    if status == "complete":
        if last.year_index != n or last.next_state is None or last.next_state.status != "terminal":
            raise LifeContractError(
                f"终态必须由第 N = {n} 年的最后一次真实交接产生，得到第 {last.year_index} 年的 {last.handoff}"
            )
        terminal = last.next_state
    elif status == "not_finished":
        reasons.append(f"轨迹只到第 {last.year_index} 年，协议的 N = {n}：之后的月份没有模拟")
    elif status == "life_exit":
        reasons.append("最低住房与食品付不起而出局：确认的生活出局（不进城市平均，也不当未知分数）")
    elif status == "stopped":
        reasons.append(
            "E1-5C 停止（应急安排不足或资料缺口）：原样保留种类与原因，不自动改称生活出局"
        )
    elif status == "feedback_blocked":
        reasons.append("反馈信息不足，下一年额度无法确定：模拟问题，不解释为生活差")
    else:
        reasons.append(
            f"第 {last.year_index} 年结算失败（{last.settlement.status}）：模拟问题，不解释为生活差"
        )
    out = last.settlement.outcome
    record = TrajectoryRecord(
        status=status,  # type: ignore[arg-type]
        owner_category=_OWNER[status],  # type: ignore[arg-type]
        years=tuple(
            YearTrace(
                year_index=r.year_index,
                handoff=r.handoff,
                settlement_status=r.settlement.status,
                problem_codes=tuple(p.code for p in r.settlement.problems),
                annual_status=None if r.score is None else r.score.annual_status,
                annual_mean=None if r.score is None else r.score.annual_mean,
                binding_fingerprint=r.binding_fingerprint,
                score_fingerprint=None if r.score is None else model_fingerprint(r.score),
                start_fingerprint=y.start_fingerprint,
            )
            for r, y in zip(results, years, strict=True)
        ),
        events=_events(results),
        exit=None if out is None else out.exit,
        stop=None if out is None else out.stop,
        terminal_fingerprint=None if terminal is None else model_fingerprint(terminal),
        reasons=tuple(reasons),
        month_facts=_month_facts(results),
    )
    health = first.start.health
    return VerifiedTrajectory(
        results=tuple(results),
        years=tuple(years),
        record=record,
        terminal=terminal,
        last_year=last_year if status == "complete" else None,
        person_id=first.person_id,
        clone_id=first.clone_id,
        city=first.city,
        repeat_index=None if health is None else health.repeat_index,
    )


def _month_facts(results: Sequence[LifeYearResult]) -> tuple[MonthFact, ...]:
    """已经提交的每个月的事实摘要：月度账目原样，加上同月的健康记录（有健康规则时）。"""
    out: list[MonthFact] = []
    for r in results:
        o = r.settlement.outcome
        if o is None:
            continue
        health = {m.month: m for m in o.health.months} if o.health is not None else {}
        for m in o.months:
            h = health.get(m.month)
            t = None if h is None else h.treatment
            out.append(
                MonthFact(
                    year_index=m.year_index,
                    month=m.month,
                    cash_start=m.cash_start,
                    income_gross=m.income_gross,
                    income_tax=m.income_tax,
                    minimum_required=m.minimum_required,
                    minimum_check=m.minimum_check,
                    spent=m.spent,
                    cash_end=m.cash_end,
                    health_state=None if h is None else h.state,
                    sick_leave=False if h is None else h.sick_leave,
                    treatment_status=None if t is None else t.status,
                    treatment_paid=None if t is None else t.amount_paid,
                )
            )
    return tuple(out)


def actual_life_score(
    spec: EvaluationSpec, protocol: LifeProtocol, trajectory: VerifiedTrajectory
) -> tuple[ActualLifeScore, list[Fraction]]:
    """逐月汇总实际生活。返回（结果、T 个月的精确分数——不完整时为空列表）。"""
    delta = spec.discount_factor
    n = protocol.simulation_years
    total = MONTHS_PER_YEAR * n
    by_year = {r.year_index: r for r in trajectory.results}
    months: list[ActualMonth] = []
    scores: list[Fraction] = []
    missing: list[str] = []
    hard: list[HardRequirementResult] = []
    not_simulated = 0
    known_w, known_sum = Fraction(0), Fraction(0)
    weight_total = sum((delta**k for k in range(total)), Fraction(0))
    for y in range(1, n + 1):
        r = by_year.get(y)
        for m in range(1, MONTHS_PER_YEAR + 1):
            index = (y - 1) * MONTHS_PER_YEAR + m
            w = delta ** (index - 1)
            fields: dict[str, object] = dict(
                index=index, year_index=y, month=m, weight=ExactAmount.of(w)
            )
            if r is None:
                fields.update(status="not_simulated", reason="轨迹在这之前结束，这个月没有模拟")
                not_simulated += 1
            elif r.score is None:
                status = "not_simulated" if r.settlement.outcome is None else "incomplete"
                why = (
                    f"第 {y} 年结算失败（{r.settlement.status}），没有生活结果"
                    if r.settlement.outcome is None
                    else f"第 {y} 年没有评分：{r.score_problem}"
                )
                fields.update(status=status, reason=why)
                not_simulated += status == "not_simulated"
            else:
                ms = r.score.months[m - 1]
                fields["status"] = ms.status
                if ms.status == "scored":
                    assert ms.u is not None
                    s, text = score_fraction(ms.u)
                    fields.update(score_decimal=text, contribution=ExactAmount.of(w * s))
                    known_w += w
                    known_sum += w * s
                    scores.append(s)
                else:
                    fields["reason"] = "；".join(ms.reasons)[:400] or ms.status
            months.append(ActualMonth.model_validate(fields))
        if r is not None and r.score is not None:
            hard.extend(r.score.hard_requirements)
    unscored = [x for x in months if x.status != "scored"]
    if trajectory.record.status == "complete" and unscored:
        trajectory_status = "score_incomplete"
    else:
        trajectory_status = trajectory.record.status
    complete = trajectory_status == "complete" and not unscored
    if not complete:
        by_status: dict[str, list[int]] = {}
        for x in unscored:
            by_status.setdefault(x.status, []).append(x.index)
        missing = [f"{k}：第 {v} 个月" for k, v in by_status.items()]
    notes: list[str] = []
    annual = [r.score.annual_mean for r in trajectory.results if r.score is not None]
    mean_of_means = None
    if complete and len(annual) == n and all(a is not None for a in annual):
        mean_of_means = sum(annual) / n  # type: ignore[arg-type]
        l_float = float(known_sum / known_w)
        if delta == 1 and mean_of_means != l_float:
            notes.append(
                f"按逐月精确分数汇总得 {l_float!r}；先对评分器的年度浮点均值再平均得 {mean_of_means!r}："
                "两者只在最末几位不同，来自评分器浮点求和的舍入顺序，不是公式不同（逐月汇总是依据）"
            )
    score = ActualLifeScore(
        status="complete" if complete else "incomplete",
        horizon_years=n,
        months_total=total,
        discount_factor=spec.monthly_discount_factor,
        weight_total=ExactAmount.of(weight_total),
        known_weight=ExactAmount.of(known_w),
        known_weighted_sum=ExactAmount.of(known_sum),
        l_actual=ExactAmount.of(known_sum / weight_total) if complete else None,
        l_actual_display=round(float(known_sum / weight_total), 6) if complete else None,
        months=tuple(months),
        missing=tuple(missing),
        hard_requirements=requirement_trajectories(hard, not_simulated),
        mean_of_annual_means=mean_of_means,
        notes=tuple(notes),
    )
    return score, (scores if complete else [])


def combine(
    spec: EvaluationSpec,
    actual: ActualLifeScore,
    actual_scores: Sequence[Fraction],
    valuation: TerminalValuation,
) -> CombinedScore:
    """J = [Σ_m δ^(m−1) S_m + Σ_k δ^(T+k−1) S_future] ÷ (W_actual + W_future)。两部分都完整才给。"""
    delta = spec.discount_factor
    t, h = actual.months_total, spec.valuation_horizon_months
    w_actual = actual.weight_total.fraction()
    w_future = sum((delta ** (t + k) for k in range(h)), Fraction(0))
    share_a, share_f = w_actual / (w_actual + w_future), w_future / (w_actual + w_future)
    head = dict(
        weight_actual=ExactAmount.of(w_actual),
        weight_future=ExactAmount.of(w_future),
        share_actual=ExactAmount.of(share_a),
        share_future=ExactAmount.of(share_f),
    )
    reasons = []
    if actual.status != "complete":
        reasons.append("实际生活不完整，没有 L_actual")
    if valuation.status != "complete":
        reasons.append(f"期末估值不完整（{valuation.status}）")
    if reasons:
        return CombinedScore(
            status="incomplete",
            reasons=(*reasons, "J 为空：不改成 L_actual，也不重新归一化权重"),
            **head,  # type: ignore[arg-type]
        )
    selected = next(p for p in valuation.plans if p.plan_id == valuation.selected_plan_id)
    future = [Fraction(Decimal(s)) for s in selected.month_scores if s is not None]
    if len(future) != h or len(actual_scores) != t:
        raise LifeContractError("实际或估值期的逐月分数不齐：不能合成")
    num = sum(((delta**k) * s for k, s in enumerate(actual_scores)), Fraction(0))
    num += sum(((delta ** (t + k)) * s for k, s in enumerate(future)), Fraction(0))
    j = num / (w_actual + w_future)
    return CombinedScore(
        status="complete",
        j=ExactAmount.of(j),
        j_display=round(float(j), 6),
        **head,  # type: ignore[arg-type]
    )


ASSUMPTIONS: tuple[str, ...] = (
    "全部参数是合成探索设定（δ、H、参考方案与城市绑定、样例的价格、工资、疾病与评分参数），不是实证结论",
    "L_actual 是实际 N 年逐月满足感的加权平均；V_reference 是从真实终态出发、参考集合内最高可实现的估值期满足感，"
    "不是预测，也不是全局最优；J 是二者按月份权重的合成，不是货币，也不是现实幸福的百分比",
    "估值期 12 个月、没有新发疾病（不是把概率改成 0）、价格冻结在当地基年口径、不新开或续学课程；估值期之后的剩余资产不再计值",
    "估值期的精力额度用第 N 年完整年度满足感按原反馈政策重算，只在估值上下文里，不改真实终态",
)


def evaluate_clone(
    *,
    spec: EvaluationSpec,
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs,
    results: Sequence[LifeYearResult],
    planning_stop: PlanningStop | None = None,
) -> CloneEvaluation:
    """一个分身的跨期评价：实际 L_actual、期末 V_reference、合成 J，以及给 E1-6B 的分类与事件链。

    不改任何实际结果：只读核对过的交接结果；估值在独立的上下文里结算，真实终态不变。

    ``planning_stop``（E1-7A，DECISIONS/0030）：轨迹停在一次正常交接之后，是因为下一年的计划没有被接受（修订耗尽、城市资料
    问题或全局调用上限用尽）。只能接在 ``not_finished`` 的最后一年之后；它只给分类写明原因，已提交的月份、L、V、J 的算法
    都不变。"""
    if type(spec) is not EvaluationSpec:
        raise LifeContractError(f"spec 必须是 EvaluationSpec，得到 {type(spec).__name__}")
    check_city_binding(spec, catalog)
    trajectory = verify_trajectory(
        protocol=protocol, catalog=catalog, tax=tax, scoring=scoring, results=results
    )
    actual, scores = actual_life_score(spec, protocol, trajectory)
    record = trajectory.record
    if planning_stop is not None:
        if type(planning_stop) is not PlanningStop:
            raise LifeContractError(
                f"planning_stop 必须是 PlanningStop，得到 {type(planning_stop).__name__}"
            )
        last = record.years[-1].year_index
        if record.status != "not_finished" or planning_stop.year_index != last + 1:
            raise LifeContractError(
                f"规划停止写的是第 {planning_stop.year_index} 年，但轨迹的结局是 {record.status}（最后一年第 {last} 年）："
                "只能接在一次正常交接（next_year_ready）的最后一年之后"
            )
        data = record.model_dump(mode="python")
        data.update(
            planning_stop=planning_stop,
            reasons=(
                *record.reasons,
                f"第 {planning_stop.year_index} 年的计划没有被接受（{PLANNING_STOP_TEXT[planning_stop.kind]}）："
                f"{planning_stop.detail}"[:400],
            ),
        )
        record = TrajectoryRecord.model_validate(data)
    if record.status == "complete" and actual.status != "complete":
        data = record.model_dump(mode="python")
        data.update(
            status="score_incomplete",
            owner_category="simulation_incomplete",
            reasons=(
                *record.reasons,
                "终态正常，但有月份的评分不完整（正权重的资料未知等）：模拟问题，不解释为生活差",
            ),
        )
        record = TrajectoryRecord.model_validate(data)
    valuation = value_terminal(
        spec=spec,
        protocol=protocol,
        catalog=catalog,
        tax=tax,
        scoring=scoring,
        terminal=trajectory.terminal,
        last_year=trajectory.last_year,
        trajectory_status=record.status,
    )
    combined = combine(spec, actual, scores, valuation)
    policy = protocol.energy_feedback
    fields = dict(
        spec_id=spec.spec_id,
        spec_version=spec.version,
        spec_content_fingerprint=spec.content_fingerprint,
        execution_revision=spec.execution_revision,
        person_id=trajectory.person_id,
        clone_id=trajectory.clone_id,
        city=trajectory.city,
        repeat_index=trajectory.repeat_index,
        preference_source=policy.preference_source if policy is not None else "unknown",
        protocol_fingerprint=model_fingerprint(protocol),
        catalog_fingerprint=model_fingerprint(catalog),
        tax_fingerprint=_tax_fingerprint(tax),
        scoring_inputs_fingerprint=_scoring_fingerprint(scoring),
        trajectory=record,
        actual=actual,
        valuation=valuation,
        combined=combined,
        assumptions=ASSUMPTIONS,
    )
    unsigned = CloneEvaluation.model_construct(**fields, result_fingerprint="0" * 64)
    return CloneEvaluation(**fields, result_fingerprint=unsigned.expected_result_fingerprint())
