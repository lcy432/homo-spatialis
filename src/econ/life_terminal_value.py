"""E1-6A 期末估值（`DECISIONS/0028`）：从真实终态出发，按预先声明的少量参考方案，在估值期里真实结算、评分。

    真实终态（terminal，第 N+1 年）
        → 估值上下文（只在这里构造，记下每一项转换；真实终态不变）
        → 每个估值分支（基础生活模板 + 治疗选择）变成一份普通年度计划 → settle_year（现有的钱、时间、精力、疾病规则）
        → 现有评分器 → V_p = Σ_k δ^(k−1) S_future[p,k] ÷ Σ_k δ^(k−1)
        → V_reference = 参考集合内能完整执行、完整评分、已知满足硬要求的最高 V_p

治疗分支（E1-6A 收尾 F3，评价版本 v2）：估值期初健康时每个模板一个分支（没有新发疾病，治疗选择不起作用，记为"未触发"，
不代表用户偏好）；带着病进入估值期时，每个模板展开"条件允许就治"（``模板.treat``）与"不治疗"（``模板.decline``）两个分支，
只针对正在延续的这场病。每个分支照旧交给 E1-5C 结算：decline 就是不治，现金多也不改成治疗。

它回答"留下的资源（现金、物品、资格、健康）还能支持怎样的生活"，**不是**第 N+1 年的真实行动、不是对现实未来的预测，
也不是对全部可能行动的全局最优——只在声明的参考集合里取最高。有限期（H = 12 个月）：估值期之后的剩余资产不再追加
第二次终值，所以会低估更长远的收益。

没有复制任何工资、税、资源、病程或评分数学：结算用 ``econ.life_settlement.settle_year``，评分用 ``econ.life_year``
里同一份评分调用，额度用 ``econ.life_feedback.quota_from_score``。这里只有编排与适配：

* **估值上下文**：协议从实际协议派生——``simulation_years`` N → N+1（让第 N+1 年成为可以结算的一年），事件来源换成
  派生的 ``none``（"估值期没有新发疾病"的确定性条件，不是把患病概率改成 0）。状态从真实终态构造——``status`` 改为
  ``active``，年份不重置，现金、能力、岗位、培训、目标、疾病与病史原样；已到期（剩余 0 个月）的持有物不带入（与普通年度
  交接对活跃状态的要求一致）；健康绑定的事件来源改指派生来源（疾病规则指纹照常核对）；精力额度用第 N 年完整年度满足感
  按原反馈政策重算（关着时用基础值），只存在于估值上下文。普通 ``settle_year`` 仍然拒绝真实终态。
* **基础生活模板**：槽位 → 各城绑定的目录机会。确定没有 / 资格不足 / 估值期出局 / 计划不可行（含应急安排撑不住延续的
  疾病）/ 方案里任何一项付不起是"已知不可行"；资料未知（含要治疗而治疗资料未知）、评分不完整、硬要求未知、输入错误是
  "无法评价"——有无法评价的候选就不给 V_reference。估值期没有新发疾病，期末已在延续的疾病在年初的无事件试算里就会
  暴露（不可行或资料缺口，什么都不提交），所以估值期不会出现 E1-5C 的"停止"；万一出现，按意外结果无法评价。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from common.life_contracts import (
    MAX_SIMULATION_YEARS,
    MONTHS_PER_YEAR,
    CityLifeCatalog,
    ExactAmount,
    IllnessEventSource,
    LifeContractError,
    LifeOpportunity,
    LifeProtocol,
    PersonYearState,
    YearAction,
    canonical_fingerprint,
    health_rules_state_problems,
    model_fingerprint,
    qualification_gaps,
    to_fraction,
)
from common.life_evaluation_contracts import (
    CitySlotBinding,
    EvaluationSpec,
    LifeTemplate,
    PlanEvaluation,
    PlanItem,
    RequirementTrajectory,
    TerminalValuation,
    ValuationContext,
    ValuationHolding,
    ValuationQuota,
    ValuationStateSummary,
)
from common.life_feedback_contracts import ScoredYear
from common.life_scoring_contracts import HardRequirementResult
from econ.life_feedback import quota_from_score, score_fraction
from econ.life_settlement import LifeTaxBinding, settle_year
from econ.life_year import ScoringInputs, _check_scoring_inputs, _score
from ledger.life_evaluation_config import city_binding_problems

#: 派生事件来源的说明（写进派生协议；说明文字不参与事件来源的内容指纹）。
VALUATION_SOURCE_NOTE = (
    "E1-6A 估值期：没有新发疾病的确定性条件（不是把患病概率改成 0，也不保证现实中不会生病）；"
    "期末已在延续的疾病照常按规则走"
)


def check_city_binding(spec: EvaluationSpec, catalog: CityLifeCatalog) -> None:
    """正式评价入口在任何试算之前核对评价规格与这座城的目录（E1-6A 收尾 F4）：与加载器同一份按城检查
    （``ledger.life_evaluation_config.city_binding_problems``）。不通过是配置写错，明确拒绝——不伪装成城市资料未知，
    也不混进"无法评价"的方案。"""
    problems = city_binding_problems(spec, catalog)
    if problems:
        raise LifeContractError(
            "评价配置与本城目录对不上（这是配置写错，不是城市资料未知）：" + "；".join(problems)
        )


def requirement_trajectories(
    results: Sequence[HardRequirementResult], months_not_simulated: int = 0
) -> tuple[RequirementTrajectory, ...]:
    """把若干年的硬要求检验按"需求"合起来：任何一个月不满足 → violated；否则有未知 → unknown；否则有没生活的月份 →
    satisfied_in_lived_months；否则每个月都满足。``months_not_simulated`` 是轨迹提前结束后没有模拟的月数（算没生活）。"""
    by_need: dict[str, list[int]] = {}
    for r in results:
        acc = by_need.setdefault(r.need, [0, 0, 0, 0])
        acc[0] += len(r.months_satisfied)
        acc[1] += len(r.months_not_satisfied)
        acc[2] += len(r.months_unknown)
        acc[3] += len(r.months_not_lived)
    out = []
    for need, (ok, bad, unknown, not_lived) in by_need.items():
        not_lived += months_not_simulated
        if bad:
            summary = "violated"
        elif unknown:
            summary = "unknown"
        elif not_lived:
            summary = "satisfied_in_lived_months"
        else:
            summary = "satisfied_every_month"
        out.append(
            RequirementTrajectory(
                need=need,
                months_satisfied=ok,
                months_not_satisfied=bad,
                months_unknown=unknown,
                months_not_lived=not_lived,
                requirement_summary=summary,  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def weighted_mean(scores: Sequence[Fraction], delta: Fraction, start_power: int = 0) -> Fraction:
    """Σ_k δ^(start+k) s_k ÷ Σ_k δ^(start+k)（精确分数）。"""
    weights = [delta ** (start_power + k) for k in range(len(scores))]
    return sum((w * s for w, s in zip(weights, scores, strict=True)), Fraction(0)) / sum(
        weights, Fraction(0)
    )


# ---------------------------------------------------------------------- 估值上下文


def _derived_protocol(protocol: LifeProtocol) -> tuple[LifeProtocol, IllnessEventSource | None]:
    n = protocol.simulation_years
    if n + 1 > MAX_SIMULATION_YEARS:
        raise LifeContractError(
            f"协议的 N = {n} 已到上限 {MAX_SIMULATION_YEARS}，派生不出第 N+1 年的估值期"
        )
    data = protocol.model_dump(mode="python")
    data["simulation_years"] = n + 1
    source = None
    if protocol.illness_events is not None:
        source = IllnessEventSource(
            mode="none",
            stream_version=protocol.illness_events.stream_version,
            note=VALUATION_SOURCE_NOTE,
        )
        data["illness_events"] = source.model_dump(mode="python")
    return LifeProtocol.model_validate(data), source


def _valuation_quota(
    protocol: LifeProtocol, terminal: PersonYearState, last_year: ScoredYear
) -> ValuationQuota | str:
    """估值期的额度：开反馈时用第 N 年完整年度满足感重算；关着时用基础值。算不出来返回原因（不补零、不沿用旧额度）。"""
    policy = protocol.energy_feedback
    basis = terminal.energy_quota_basis
    if policy is None or basis is None:
        raise LifeContractError(
            f"协议 {protocol.protocol_id} 没有满足感反馈政策或终态没有额度依据：E1-6A 的估值只支持 E1-4B 起的协议"
        )
    base = to_fraction(basis.base_level)
    head = dict(
        source_year=terminal.year_index - 1,
        base_quota=ExactAmount.of(base),
        alpha=policy.alpha,
        terminal_quota=ExactAmount.of(to_fraction(terminal.energy.level)),
        policy_id=policy.policy_id,
        policy_fingerprint=policy.content_fingerprint,
        scoring_content_fingerprint=policy.scoring_content_fingerprint,
        source_binding_fingerprint=last_year.binding_fingerprint,
    )
    if not policy.effective:
        decision = "off_disabled" if policy.mode == "disabled" else "off_alpha_zero"
        return ValuationQuota(
            **head,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            quota=ExactAmount.of(base),
            note="反馈关闭或 α = 0：估值期额度就是基础值",
        )
    score = last_year.score
    if score is None or score.annual_status != "complete" or score.annual_mean is None:
        status = "没有评分" if score is None else score.annual_status
        return (
            f"估值期的精力额度无法确定：第 {terminal.year_index - 1} 年的年度满足感不完整（{status}）——反馈开着时额度要用"
            "这一年的完整年度满足感按原政策算；不补零、不沿用终态里第 N 年的旧额度，也不用三年总分、估值或 J 代替"
        )
    q = quota_from_score(basis.base_level, policy.alpha, score.annual_mean)
    return ValuationQuota(
        **head,  # type: ignore[arg-type]
        decision="applied",
        score_decimal=q.score_text,
        raw_quota=ExactAmount.of(q.raw),
        quota=ExactAmount.of(q.quantized),
        note=(
            f"E_raw = {basis.base_level:g} × (1 + {policy.alpha:g} × {q.score_text})，向下保留到 0.01：只用第 "
            f"{terminal.year_index - 1} 年；只存在于估值上下文，不改真实终态，估值期内不再滚动更新"
        ),
    )


def _valuation_state(
    terminal: PersonYearState,
    quota: ValuationQuota,
    protocol: LifeProtocol,
    source: IllnessEventSource | None,
) -> tuple[PersonYearState, tuple[str, ...], tuple[str, ...]]:
    """真实终态 → 估值年初状态。返回（状态、转换说明、不带入的到期物品）。不改传入的终态。"""
    policy = protocol.energy_feedback
    basis = terminal.energy_quota_basis
    assert policy is not None and basis is not None
    data = terminal.model_dump(mode="python")
    data["status"] = "active"
    level = float(quota.quota.fraction())
    if (
        to_fraction(level) != quota.quota.fraction()
    ):  # 量化到 0.01 后，浮点的最短写法就是这个两位小数
        raise LifeContractError(f"估值期额度 {quota.quota.exact} 不能精确写成两位小数")
    data["energy"] = {"level": level, "scale_version": terminal.energy.scale_version}
    data["energy_quota_basis"] = {
        "base_level": basis.base_level,
        "status": "feedback_applied" if quota.decision == "applied" else "feedback_off",
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "source_year": quota.source_year,
        "record_fingerprint": canonical_fingerprint(quota.model_dump(mode="json")),
        "policy_fingerprint": policy.content_fingerprint,
        "scoring_content_fingerprint": policy.scoring_content_fingerprint,
    }
    expired = tuple(
        h.opportunity_id for h in terminal.holdings if (h.effective_remaining_months or 0) == 0
    )
    data["holdings"] = [
        h.model_dump(mode="python") for h in terminal.holdings if h.opportunity_id not in expired
    ]
    conversions = [
        f"状态：terminal → active（只在估值上下文；第 {terminal.year_index} 年，年份不重置；真实终态不变）",
        f"精力额度：终态里的 {terminal.energy.level:g}（第 N 年用的）→ 估值期 {level:g}（{quota.decision}）",
        "现金、能力、岗位记录、培训进度、资金目标、疾病与病史：原样带入",
    ]
    if expired:
        conversions.append(f"已到期（剩余 0 个月）的持有物不带入：{list(expired)}")
    if terminal.health is not None and source is not None:
        binding = terminal.health.binding
        assert binding is not None  # 终态由结算产生，必然绑定（下面的检查保证）
        data["health"]["binding"] = {
            "rules_version": binding.rules_version,
            "rules_fingerprint": binding.rules_fingerprint,
            "source_fingerprint": source.content_fingerprint,
        }
        conversions.append(
            f"健康绑定：事件来源 {binding.source_fingerprint[:12]}… → 派生的 none 来源 "
            f"{source.content_fingerprint[:12]}…（疾病规则指纹不变）"
        )
    return PersonYearState.model_validate(data), tuple(conversions), expired


def _summary(state: PersonYearState, point: str, horizon: int) -> ValuationStateSummary:
    """估值期初或期末状态的摘要（只读状态，不改它）。"""
    usable = [
        f"{a.ability_id}（第 {a.usable_from_year} 年起可用）"
        for a in state.abilities
        if a.usable_from_year <= state.year_index
    ]
    pending = [
        f"{a.ability_id}（第 {a.usable_from_year} 年起才可用）"
        for a in state.abilities
        if a.usable_from_year > state.year_index
    ]
    episode = state.health.episode if state.health is not None else None
    return ValuationStateSummary(
        point=point,  # type: ignore[arg-type]
        year_index=state.year_index,
        cash=state.cash,
        energy_level=ExactAmount.of(to_fraction(state.energy.level)),
        holdings=tuple(
            ValuationHolding(
                opportunity_id=h.opportunity_id,
                quantity=h.quantity,
                remaining_months=h.effective_remaining_months or 0,
                beyond_horizon_months=max(
                    0,
                    (h.effective_remaining_months or 0)
                    - (horizon if point == "valuation_start" else 0),
                ),
            )
            for h in state.holdings
        ),
        abilities=tuple(usable + pending),
        positions=state.employment.current_position_ids if state.employment is not None else (),
        training=tuple(
            f"{t.opportunity_id}：已参加 {t.months_participated}/{t.months_required} 个月，学费"
            f"{'已付' if t.fee_paid else '未付'}{'，已完成' if t.completed else ''}"
            for t in state.training
        ),
        illness=None
        if episode is None
        else f"{'小病' if episode.severity == 'minor' else '大病'}（第 {episode.onset_year} 年第 "
        f"{episode.onset_month} 月发病，已病 {episode.months_ill} 个月）",
    )


# ---------------------------------------------------------------------- 参考方案


@dataclass(frozen=True)
class _Branch:
    """一个估值分支：基础生活模板 + 治疗选择（E1-6A 收尾 F3）。"""

    template: LifeTemplate
    treatment: str  # TreatmentBranch：untriggered / treat_if_possible / decline
    severity: str | None  # 正在延续的那场病；未触发时为空

    @property
    def plan_id(self) -> str:
        if self.treatment == "untriggered":
            return self.template.template_id
        suffix = "treat" if self.treatment == "treat_if_possible" else "decline"
        return f"{self.template.template_id}.{suffix}"

    def emergency(self) -> dict[str, str]:
        """年度计划的应急安排。未触发：估值期没有新发疾病，写 decline 只为满足年度计划的格式，不代表偏好；治：只对正在
        延续的那场病写"条件允许就治"（另一种病情估值期不会发生，写 decline）；不治：两种都 decline。"""
        out = {"minor": "decline", "major": "decline"}
        if self.treatment == "treat_if_possible":
            assert self.severity is not None
            out[self.severity] = "treat_if_possible"
        return out

    def fields(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "template_id": self.template.template_id,
            "treatment_branch": self.treatment,
            "treatment_severity": self.severity,
        }


def _branches(
    spec: EvaluationSpec, state: PersonYearState
) -> tuple[list[_Branch], str, str | None]:
    """按估值期初的健康状态展开分支：健康 → 每个模板一个（未触发）；带病 → 每个模板治 / 不治两个（只针对这场病）。"""
    episode = state.health.episode if state.health is not None else None
    if episode is None:
        return (
            [_Branch(t, "untriggered", None) for t in spec.life_templates],
            "healthy_start_single_branch",
            None,
        )
    s = episode.severity
    out = [
        b
        for t in spec.life_templates
        for b in (_Branch(t, "treat_if_possible", s), _Branch(t, "decline", s))
    ]
    return out, "continuing_illness_treat_or_decline", s


def _plan_failure(
    branch: _Branch,
    status: str,
    reason_kind: str,
    reasons: Sequence[str],
    items: Sequence[PlanItem] = (),
    skipped: Sequence[str] = (),
    **kw: object,
) -> PlanEvaluation:
    return PlanEvaluation(
        **branch.fields(),  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        reason_kind=reason_kind,  # type: ignore[arg-type]
        reasons=tuple(r[:400] for r in reasons),
        items=tuple(items),
        skipped_purchases=tuple(skipped),
        **kw,  # type: ignore[arg-type]
    )


def _slot_opportunity(
    binding: CitySlotBinding, catalog: CityLifeCatalog, slot_id: str
) -> tuple[LifeOpportunity | None, str | None, str | None]:
    """槽位 → 目录机会。返回（机会、失败分类、原因）：确定没有是已知不可行；资料未知是无法评价。"""
    target = binding.target(slot_id)
    if target is None:  # 规格校验保证每座城每个槽位都有绑定
        raise LifeContractError(f"{binding.city}: 槽位 {slot_id} 没有绑定")
    if target.status == "unavailable":
        return (
            None,
            "slot_unavailable",
            f"槽位 {slot_id} 在 {binding.city} 确定没有：{target.reason}",
        )
    if target.status == "unknown":
        return None, "slot_unknown", f"槽位 {slot_id} 在 {binding.city} 资料未知：{target.reason}"
    assert target.opportunity_id is not None
    o = catalog.opportunity(target.opportunity_id)
    if o is None:  # 配置加载已核对；到这里说明有人绕过了检查
        raise LifeContractError(f"槽位 {slot_id} 绑定的 {target.opportunity_id} 不在目录里")
    return _availability(o)


def _availability(o: LifeOpportunity) -> tuple[LifeOpportunity | None, str | None, str | None]:
    if o.availability == "unavailable":
        return None, "slot_unavailable", f"{o.opportunity_id} 在本城确定没有（目录写明缺货）"
    if o.availability == "unknown":
        return None, "slot_unknown", f"{o.opportunity_id} 的资料未知：不当零价，也不当可得"
    return o, None, None


_INFEASIBLE = {"slot_unavailable", "no_qualification"}
_TREATMENT_TEXT = {
    "untriggered": "未触发（估值期初健康）",
    "treat_if_possible": "条件允许就治",
    "decline": "不治疗",
}


def _plan_action(
    plan: LifeTemplate,
    spec: EvaluationSpec,
    binding: CitySlotBinding,
    catalog: CityLifeCatalog,
    state: PersonYearState,
) -> tuple[list[dict[str, object]], list[PlanItem], list[str], str | None, str | None]:
    """基础生活模板 → 年度计划的行动列表。返回（行动、方案条目、不重复购买的说明、失败分类、原因）。"""
    whole_year = {"quantity": 1, "start_month": 1, "end_month": MONTHS_PER_YEAR}
    actions: list[dict[str, object]] = []
    items: list[PlanItem] = []
    skipped: list[str] = []

    def add(slot: str, role: str, o: LifeOpportunity, **timing: int) -> None:
        actions.append({"opportunity_id": o.opportunity_id, **timing})
        items.append(
            PlanItem(
                plan_slot=slot,
                opportunity_id=o.opportunity_id,
                slot_role=role,  # type: ignore[arg-type]
                start_month=timing.get("start_month"),
                end_month=timing.get("end_month"),
                month=timing.get("month"),
            )
        )

    jobs: list[tuple[str, LifeOpportunity]] = []
    if plan.jobs == "current_positions":
        current = state.employment.current_position_ids if state.employment is not None else ()
        for jid in current:
            o = catalog.opportunity(jid)
            if o is None:
                return actions, items, skipped, "input_error", f"当前岗位 {jid} 不在本城目录里"
            o, kind, why = _availability(o)
            if o is None:
                return actions, items, skipped, kind, why
            jobs.append(("current_position", o))
    else:
        assert plan.job_slot is not None
        o, kind, why = _slot_opportunity(binding, catalog, plan.job_slot)
        if o is None:
            return actions, items, skipped, kind, why
        jobs.append((plan.job_slot, o))
    for slot, o in jobs:
        gaps = qualification_gaps(o, state)
        if gaps:
            detail = "；".join(f"{g.tag}（{g.reason}）" for g in gaps)
            return (
                actions,
                items,
                skipped,
                "no_qualification",
                f"{o.opportunity_id}：估值期初不满足资格 {detail}——未完成的课程不算取得资格",
            )
        add(slot, "job", o, **whole_year)
    for slot in (plan.housing_slot, plan.food_slot, *plan.service_slots):
        o, kind, why = _slot_opportunity(binding, catalog, slot)
        if o is None:
            return actions, items, skipped, kind, why
        add(slot, spec.slot_role(slot) or "service", o, **whole_year)
    for rule in plan.durable_purchases:
        # E1-6A 收尾 F2：槽位认得出是哪一件物品时，先看估值期初有没有它在服务期内——有就照用，不买，也就不需要新品
        # 可得或新品报价（家里的机器不受商店缺货影响）；没有才检查供给、价格与资金。槽位"确定没有 / 资料未知"认不出
        # 是哪一件，照旧按槽位处理；不做跨品牌替代或物品匹配。到期的持有物不在估值期初状态里，不会挡住正常购买。
        target = binding.target(rule.slot_id)
        if target is not None and target.status == "opportunity":
            assert target.opportunity_id is not None
            held = next(
                (
                    h
                    for h in state.holdings
                    if h.opportunity_id == target.opportunity_id
                    and (h.effective_remaining_months or 0) > 0
                ),
                None,
            )
            if held is not None:
                skipped.append(
                    f"{target.opportunity_id}：估值期初已持有 {held.quantity:g} 件、剩余 {held.effective_remaining_months} 个月——"
                    "照用旧的，不重复购买、不重复付款，也不重置寿命"
                )
                continue
        o, kind, why = _slot_opportunity(binding, catalog, rule.slot_id)
        if o is None:
            return actions, items, skipped, kind, why
        add(rule.slot_id, "durable", o, quantity=1, month=rule.month)
    return actions, items, skipped, None, None


def _evaluate_plan(
    *,
    branch: _Branch,
    spec: EvaluationSpec,
    binding: CitySlotBinding,
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs,
    state: PersonYearState,
    delta: Fraction,
) -> PlanEvaluation:
    raw, items, skipped, kind, why = _plan_action(branch.template, spec, binding, catalog, state)
    if kind is not None:
        assert why is not None
        status = "infeasible" if kind in _INFEASIBLE else "unevaluable"
        return _plan_failure(branch, status, kind, [why], items, skipped)
    data: dict[str, object] = {
        "person_id": state.person_id,
        "clone_id": state.clone_id,
        "year_index": state.year_index,
        "items": raw,
        "rationale": f"E1-6A 参考估值分支 {branch.plan_id}：估值专用，不是分身的真实行动",
    }
    if (
        protocol.health_rules is not None
    ):  # 只有带疾病规则的协议要写应急安排；旧协议写了反而是输入错误
        data["emergency"] = branch.emergency()
    action = YearAction.model_validate(data)
    common = dict(
        items=items,
        skipped=skipped,
        action_fingerprint=model_fingerprint(action),
        cash_start=state.cash,
    )
    result = settle_year(protocol=protocol, state=state, action=action, catalog=catalog, tax=tax)
    codes = tuple(p.code for p in result.problems)
    messages = [p.message for p in result.problems] or [f"结算状态 {result.status}"]
    out = result.outcome
    head = dict(
        settlement_status=result.status,
        problem_codes=codes,
        outcome_fingerprint=None if out is None else model_fingerprint(out),
        **common,
    )
    if out is not None and out.health is not None:
        head["health_states"] = tuple(m.state for m in out.health.months)
        head["new_onsets"] = sum(m.onset is not None for m in out.health.months)
        if branch.treatment == "untriggered" and any(s != "healthy" for s in head["health_states"]):
            return _plan_failure(
                branch,
                "unevaluable",
                "unexpected_settlement",
                ["估值期初健康、没有新发疾病，却出现了病月：估值器不认识这种结果"],
                **head,
            )
    if result.status == "exited":
        assert out is not None and out.exit is not None
        return _plan_failure(
            branch,
            "infeasible",
            "funds_exit",
            [
                f"估值期第 {out.exit.month} 月付不起最低住房与食品（差 {out.exit.shortfall.amount:,.2f}）："
                "这个参考方案在估值期内撑不下去——已知资金不足，不是资料问题"
            ],
            cash_end=out.months[-1].cash_end if out.months else None,
            **head,
        )
    if result.status == "plan_infeasible":
        return _plan_failure(branch, "infeasible", "plan_infeasible", messages, **head)
    if result.status == "input_error":
        if codes and set(codes) <= {"qualification_missing"}:
            return _plan_failure(branch, "infeasible", "no_qualification", messages, **head)
        return _plan_failure(branch, "unevaluable", "input_error", messages, **head)
    if result.status == "data_error":
        return _plan_failure(branch, "unevaluable", "data_error", messages, **head)
    if result.status != "completed" or out is None or result.next_state is None:
        return _plan_failure(branch, "unevaluable", "unexpected_settlement", messages, **head)

    head["cash_end"] = result.next_state.cash
    head["end"] = _summary(result.next_state, "plan_end", spec.valuation_horizon_months)
    # 方案里的每一项都要按声明执行。可得性与资格在变成计划之前已经核对过，剩下的被拒只能是钱不够：任何一个月被拒
    # （按月服务某个月付不起、条件购买买不起）就是这个方案付不起——已知不可行，不按"只执行了一部分"的生活评分。
    # 生病暂停（病假、大病暂停主动活动）不是被拒，是这个方案在估值期里的真实生活。
    for item in out.items:
        if item.rejections:
            r = item.rejections[0]
            return _plan_failure(
                branch,
                "infeasible",
                "purchase_unaffordable",
                [
                    f"计划的 {item.opportunity_id} 第 {r.month} 月没有执行（{r.reason}）：这个参考方案付不起；"
                    "不按只执行了一部分的生活评分"
                ],
                **head,
            )
        if item.role == "durable_good" and item.status != "executed":
            return _plan_failure(
                branch,
                "unevaluable",
                "unexpected_settlement",
                [
                    f"计划的购买 {item.opportunity_id} 状态是 {item.status}，又没有被拒记录：估值器不认识这种结果"
                ],
                **head,
            )
    score, problem = _score(protocol, state, catalog, out, scoring)
    if score is None:
        return _plan_failure(
            branch, "unevaluable", "preference_insufficient", [problem or "没有评分"], **head
        )
    head["score_fingerprint"] = model_fingerprint(score)
    month_scores = tuple(None if m.u is None else score_fraction(m.u)[1] for m in score.months)
    head["month_scores"] = month_scores
    hard = requirement_trajectories(score.hard_requirements)
    head["hard_requirements"] = hard
    if score.annual_status != "complete":
        return _plan_failure(
            branch,
            "unevaluable",
            "score_incomplete",
            [
                f"估值期评分不完整（{score.annual_status}）：第 {list(score.months_incomplete)} 月有正权重的资料未知——"
                "不补零、不用已知月份平均"
            ],
            **head,
        )
    if any(h.requirement_summary == "violated" for h in hard):
        bad = [h.need for h in hard if h.requirement_summary == "violated"]
        return _plan_failure(
            branch,
            "excluded_hard_requirement",
            "hard_requirement_violated",
            [f"估值期明确违反已声明的硬要求 {bad}：不作为满足这个用户要求的可选方案"],
            **head,
        )
    if any(h.requirement_summary == "unknown" for h in hard):
        unknown = [h.need for h in hard if h.requirement_summary == "unknown"]
        return _plan_failure(
            branch,
            "unevaluable",
            "hard_requirement_unknown",
            [f"硬要求 {unknown} 在估值期资料未知：不能当成满足"],
            **head,
        )
    values = [Fraction(Decimal(s)) for s in month_scores if s is not None]
    v = weighted_mean(values, delta)
    return PlanEvaluation(
        **branch.fields(),  # type: ignore[arg-type]
        status="complete",
        reason_kind="complete",
        v_plan=ExactAmount.of(v),
        v_plan_display=round(float(v), 6),
        **{k: v_ for k, v_ in head.items() if k != "skipped"},  # type: ignore[arg-type]
        skipped_purchases=tuple(skipped),
    )


def _not_valued(terminal: PersonYearState, horizon: int) -> tuple[str, ...]:
    out = ["估值期之后剩下的现金与资产不再追加第二次终值（有限期，会低估更长远的收益）"]
    for t in terminal.training:
        if not t.completed:
            out.append(
                f"未完成课程 {t.opportunity_id}：已参加 {t.months_participated}/{t.months_required} 个月，学费"
                f"{'已付' if t.fee_paid else '未付'}——本轮不续学、不退款、不补能力，这部分潜力没有估值"
            )
    for h in terminal.holdings:
        left = h.effective_remaining_months or 0
        if left > horizon:
            out.append(
                f"持有物 {h.opportunity_id} 在估值期之后还剩 {left - horizon} 个月服务：有限期估值不计，也不另加转售残值"
            )
    for g in terminal.savings_goals or ():
        if g.open:
            out.append(
                f"资金目标 {g.goal_id}（{g.status}）：不按目标名称或金额加分，不自动购买；估值中的购买不回写目标历史"
            )
    return tuple(out)


def value_terminal(
    *,
    spec: EvaluationSpec,
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs,
    terminal: PersonYearState | None,
    last_year: ScoredYear | None,
    trajectory_status: str,
) -> TerminalValuation:
    """对一个**正常结束**的真实终态做期末估值。``last_year`` 是第 N 年的封存年度绑定（额度要用它的完整年度满足感）。

    出局、停止、反馈受阻、计划失败或没跑完的轨迹返回 ``not_applicable``——估值器不复活它们。"""
    check_city_binding(spec, catalog)
    head = dict(
        horizon_months=spec.valuation_horizon_months,
        discount_factor=spec.monthly_discount_factor,
    )
    if trajectory_status not in ("complete", "score_incomplete") or terminal is None:
        return TerminalValuation(
            status="not_applicable",
            reasons=(
                f"实际生活没有正常结束（{trajectory_status}）：只对正常结束的终态估值；出局、停止、反馈受阻的状态"
                "不能被估值器复活",
            ),
            **head,  # type: ignore[arg-type]
        )
    if terminal.status != "terminal" or terminal.year_index != protocol.terminal_year_index:
        raise LifeContractError(
            f"{terminal.clone_id}: 估值只接受第 N+1 = {protocol.terminal_year_index} 年的真实终态，得到 "
            f"{terminal.status} 第 {terminal.year_index} 年"
        )
    if last_year is None or last_year.year_index != protocol.simulation_years:
        raise LifeContractError("估值需要第 N 年的封存年度绑定（额度来自它的完整年度满足感）")
    binding = spec.binding_for(str(terminal.city))
    if binding is None:
        raise LifeContractError(f"评价规格 {spec.spec_id} 没有 {terminal.city} 的槽位绑定")
    if protocol.health_rules is not None:
        problems = health_rules_state_problems(protocol.health_rules, terminal)
        source = protocol.illness_events
        bound = terminal.health.binding if terminal.health is not None else None
        if (
            problems
            or source is None
            or bound is None
            or bound.source_fingerprint != source.content_fingerprint
        ):
            raise LifeContractError(
                f"{terminal.clone_id}: 终态的健康部分不是由这份协议的疾病规则与事件来源产生的——不派生估值上下文："
                + "；".join(m for _, m in problems)
            )

    quota = _valuation_quota(protocol, terminal, last_year)
    not_valued = _not_valued(terminal, spec.valuation_horizon_months)
    if isinstance(quota, str):
        return TerminalValuation(
            status="incomplete",
            reasons=(quota,),
            not_valued=not_valued,
            **head,  # type: ignore[arg-type]
        )
    vprotocol, source = _derived_protocol(protocol)
    vstate, conversions, expired = _valuation_state(terminal, quota, vprotocol, source)
    _check_scoring_inputs(vprotocol, vstate, scoring)
    actual_source = protocol.illness_events
    context = ValuationContext(
        actual_protocol_fingerprint=model_fingerprint(protocol),
        valuation_protocol_fingerprint=model_fingerprint(vprotocol),
        simulation_years=protocol.simulation_years,
        valuation_year=vstate.year_index,
        actual_source_fingerprint=(
            actual_source.content_fingerprint
            if actual_source is not None
            else canonical_fingerprint({"mode": "no_health_rules"})
        ),
        valuation_source_fingerprint=(
            source.content_fingerprint
            if source is not None
            else canonical_fingerprint({"mode": "no_health_rules"})
        ),
        valuation_source_mode="none",
        terminal_fingerprint=model_fingerprint(terminal),
        valuation_start_fingerprint=model_fingerprint(vstate),
        dropped_expired_holdings=expired,
        quota=quota,
        start=_summary(vstate, "valuation_start", spec.valuation_horizon_months),
        conversions=(
            f"协议：simulation_years {protocol.simulation_years} → {vprotocol.simulation_years}；事件来源 → none"
            "（估值期没有新发疾病）；其余原样",
            *conversions,
        ),
    )
    delta = spec.discount_factor
    branches, branching, severity = _branches(spec, vstate)
    plans = tuple(
        _evaluate_plan(
            branch=b,
            spec=spec,
            binding=binding,
            protocol=vprotocol,
            catalog=catalog,
            tax=tax,
            scoring=scoring,
            state=vstate,
            delta=delta,
        )
        for b in branches
    )
    head.update(branching=branching, continuing_severity=severity, trials=len(plans))
    complete = [p for p in plans if p.status == "complete"]
    unevaluable = [p for p in plans if p.status == "unevaluable"]
    best: PlanEvaluation | None = None
    if complete:  # 并列最高时按分支编号的字典序选出展示的那一个，不影响数值
        top = max(_v(p) for p in complete)
        best = min((p for p in complete if _v(p) == top), key=lambda p: p.plan_id)
    if unevaluable:
        names = [f"{p.plan_id}（{p.reason_kind}）" for p in unevaluable]
        return TerminalValuation(
            status="incomplete",
            reasons=(
                f"参考集合里有可能适用但无法评价的分支 {names}：不给出完整的 V_reference；"
                + (
                    "已知的分支里没有任何一个能完整执行、完整评分并满足硬要求"
                    if best is None
                    else f"已知的分支里最好的是 {best.plan_id}，它只作诊断，不冒充整个参考集合的估值"
                ),
            ),
            context=context,
            plans=plans,
            best_known=None if best is None else best.v_plan,
            best_known_plan_id=None if best is None else best.plan_id,
            not_valued=not_valued,
            **head,  # type: ignore[arg-type]
        )
    if best is None:
        return TerminalValuation(
            status="no_feasible_plan",
            reasons=(
                "参考集合里没有能完整执行、完整评分并满足硬要求的分支（都已知不可行或违反硬要求）："
                "估值不完整，不填 0，也不把这个分身改成出局",
            ),
            context=context,
            plans=plans,
            not_valued=not_valued,
            **head,  # type: ignore[arg-type]
        )
    assert best.v_plan is not None
    return TerminalValuation(
        status="complete",
        reasons=(
            f"参考集合内能完整执行、完整评分、满足硬要求的最高者是 {best.plan_id}（模板 {best.template_id}，"
            f"治疗选择 {_TREATMENT_TEXT[best.treatment_branch]}）——这是参考集合里的最高可实现评分，不是预测明年的幸福，"
            "也不是全局最优生活，也不声称分身实际一定会这样选择",
        ),
        context=context,
        plans=plans,
        selected_plan_id=best.plan_id,
        selected_template_id=best.template_id,
        selected_treatment=best.treatment_branch,
        v_reference=best.v_plan,
        v_reference_display=round(float(best.v_plan.fraction()), 6),
        not_valued=not_valued,
        **head,  # type: ignore[arg-type]
    )


def _v(plan: PlanEvaluation) -> Fraction:
    assert plan.v_plan is not None
    return plan.v_plan.fraction()
