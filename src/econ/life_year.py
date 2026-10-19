"""E1-4B 年度交接：结算 → 实际生活评分 → 下一年月度精力额度（`DECISIONS/0021`）。很薄的一层编排。

    第 y 年年初状态 + 年度计划
        → settle_year（钱、时间、精力按月结算；不读评分或偏好）
        → score_year（只读这一年的年初状态与实际结果；不改账目）
        → econ.life_feedback（上一年完整满意度 → 下一年额度）
        → 第 y+1 年年初的正式状态（额度已确定）

不另造评分器，也不复制资源算法：结算、评分、反馈各用已有的那一份。评分在这里对**本次**结算结果调用，
年初状态就是传进来的那一份——不会拿已经推进到第 y+1 年的状态去评第 y 年，也不会用一个同名但来自另一份
账目的分数。不缓存。

结算与评分在 ``settle_and_score`` 里一起完成，并当场封存成年度绑定 ``ScoredYear``（DECISIONS/0021 第十三节）——这是
唯一产生封存绑定的地方。反馈只从封存的绑定生成，也只写回绑定里的那一份候选状态。``run_life_year`` 就是
``settle_and_score`` → 反馈 → 交接。结算之前核对实际用来评分的规格内容就是反馈政策声明的那一套
（``common.life_scoring_contracts.scoring_content_problems``，与加载器同一份），年度绑定记下它；下一年开始前由
``common.life_contracts.energy_quota_problems`` 拿额度依据里的评分内容与当前政策声明比较（DECISIONS/0025）。

偏好输入是六维产品画像（``ProductLifeProfile``，DECISIONS/0034），问卷版本必须与协议声明的独立健康回答相同。

这不是通用 runner：不调用 LLM、不重试、不管理多分身；多年度只是调用方按年循环（完整闭环在代理层）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from common.life_contracts import (
    FEEDBACK_HANDOFF_REVISION,
    PRODUCT_SIX_DIM_SOURCE,
    RESOURCE_RULES_REVISION,
    CityLifeCatalog,
    HardRequirement,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    YearAction,
    model_fingerprint,
)
from common.life_feedback_contracts import EnergyFeedbackRecord, LifeYearResult, ScoredYear
from common.life_product_profile import ProductLifeProfile
from common.life_scoring_contracts import (
    CityScoringConditions,
    ExploratoryYearScore,
    LifeScoringSpec,
    PreferenceInsufficient,
    scoring_content_problems,
)
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_scoring import preference_from_confirmed_profile, score_year
from econ.life_settlement import LifeTaxBinding, settle_year

#: 反馈政策声明的偏好来源 → 唯一接受的偏好输入类型。不在表里的来源或类型一律拒绝。
PREFERENCE_INPUT_TYPES: dict[str, type] = {PRODUCT_SIX_DIM_SOURCE: ProductLifeProfile}


@dataclass(frozen=True)
class ScoringInputs:
    """对这一年实际结果评分要用的东西。偏好输入是六维产品画像（``ProductLifeProfile``），类型必须与反馈政策声明的来源一致。"""

    spec: LifeScoringSpec
    conditions: CityScoringConditions
    preference_input: ProductLifeProfile
    hard_requirements: tuple[HardRequirement, ...] = ()


def _check_scoring_inputs(
    protocol: LifeProtocol, state: PersonYearState, scoring: ScoringInputs
) -> None:
    policy = protocol.energy_feedback
    if (
        type(scoring.spec) is not LifeScoringSpec
        or type(scoring.conditions) is not CityScoringConditions
    ):
        raise LifeContractError("评分输入必须是 LifeScoringSpec 与 CityScoringConditions")
    if (scoring.spec.spec_id, scoring.spec.scoring_version) != (
        policy.scoring_spec_id,
        policy.scoring_version,
    ):
        raise LifeContractError(
            f"评分规格是 {scoring.spec.spec_id}（{scoring.spec.scoring_version}），反馈政策声明的是 "
            f"{policy.scoring_spec_id}（{policy.scoring_version}）"
        )
    problems = scoring_content_problems(policy, scoring.spec)
    if problems:  # 编号与版本相同、内容不同——不结算，不留下账目
        raise LifeContractError("；".join(message for _, message in problems))
    want_type = PREFERENCE_INPUT_TYPES.get(policy.preference_source)
    if want_type is None:
        raise LifeContractError(
            f"反馈政策的偏好来源 {policy.preference_source} 没有登记对应的输入类型：不结算，也不当成研究真值"
        )
    if type(scoring.preference_input) is not want_type:
        raise LifeContractError(
            f"反馈政策的偏好来源是 {policy.preference_source}，只接受 {want_type.__name__}，得到 "
            f"{type(scoring.preference_input).__name__}"
        )
    if scoring.preference_input.person_id != state.person_id:
        raise LifeContractError(
            f"偏好属于 {scoring.preference_input.person_id}，这一年是 {state.person_id} 的生活"
        )
    declared = protocol.health_input
    if declared.questionnaire_version != scoring.preference_input.health_questionnaire_version:
        raise LifeContractError(
            f"画像的健康问卷版本是 {scoring.preference_input.health_questionnaire_version}，协议声明的是 "
            f"{declared.questionnaire_version}——旧答案不能改读成新题目的答案；不结算"
        )


def _score(
    protocol: LifeProtocol,
    state: PersonYearState,
    catalog: CityLifeCatalog,
    outcome: object,
    scoring: ScoringInputs,
) -> tuple[ExploratoryYearScore | None, str | None]:
    kind = type(scoring.preference_input)
    if kind is not ProductLifeProfile:
        raise LifeContractError(f"不认识的偏好输入类型 {kind.__name__}：不评分")
    pref = preference_from_confirmed_profile(scoring.preference_input)
    if isinstance(pref, PreferenceInsufficient):
        return None, f"偏好信息不足（{pref.source}）：{pref.message}"
    score = score_year(
        protocol=protocol,
        spec=scoring.spec,
        conditions=scoring.conditions,
        catalog=catalog,
        state=state,
        outcome=outcome,  # type: ignore[arg-type]
        preference=pref,
        hard_requirements=tuple(scoring.hard_requirements),
    )
    return score, None


def _digest(parts: object) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tax_fingerprint(tax: LifeTaxBinding) -> str:
    """税绑定的声明身份（函数本身不序列化；合成税函数带着它的税策略，一并计入）。"""
    policy = getattr(tax.function, "policy", None)
    return _digest(
        {
            "policy_id": tax.policy_id,
            "status": tax.status,
            "money_basis_id": tax.money_basis_id,
            "jurisdiction": str(tax.jurisdiction),
            "function": f"{type(tax.function).__module__}.{type(tax.function).__qualname__}",
            "function_policy": None if policy is None else model_fingerprint(policy),
        }
    )


def _scoring_fingerprint(scoring: ScoringInputs) -> str:
    return _digest(
        {
            "spec": model_fingerprint(scoring.spec),
            "conditions": model_fingerprint(scoring.conditions),
            "preference": model_fingerprint(scoring.preference_input),
            "preference_type": type(scoring.preference_input).__name__,
            "hard_requirements": [model_fingerprint(h) for h in scoring.hard_requirements],
        }
    )


def tax_binding_fingerprint(tax: LifeTaxBinding) -> str:
    """税绑定的声明身份（公共名字，算法就是年度绑定用的那一份）。E1-6B 冻结名单时用它记下每座城的税绑定。"""
    return _tax_fingerprint(tax)


def scoring_inputs_fingerprint(scoring: ScoringInputs) -> str:
    """评分输入（规格、城市评分条件、偏好、硬要求）的身份（公共名字，算法就是年度绑定用的那一份）。E1-6B 冻结名单
    时用它记下每个名单项应有的评分输入。"""
    return _scoring_fingerprint(scoring)


def _check_protocol(protocol: LifeProtocol, state: PersonYearState) -> None:
    if type(protocol) is not LifeProtocol:
        raise LifeContractError(f"protocol 必须是 LifeProtocol，得到 {type(protocol).__name__}")
    if type(state) is not PersonYearState:
        raise LifeContractError(f"state 必须是 PersonYearState，得到 {type(state).__name__}")


def settle_and_score(
    *,
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs | None,
) -> ScoredYear:
    """结算一个分身的一年，再对**这一份**结算结果、用这一年**年初**状态调用评分器，当场封存成年度绑定。

    这是唯一产生封存绑定（``ScoredYear.sealed``）的地方：绑定里的账目、分数与候选状态由这里亲自算出，
    不接受外部传入的分数或结算结果。依据（协议、年初状态、计划、目录、税绑定、评分输入、实际结果、候选状态、
    分数）都记成 SHA-256；同样的输入重放得到同样的绑定。
    """
    _check_protocol(protocol, state)
    if scoring is not None:
        _check_scoring_inputs(protocol, state, scoring)
    settlement = settle_year(
        protocol=protocol, state=state, action=action, catalog=catalog, tax=tax
    )
    score: ExploratoryYearScore | None = None
    score_problem: str | None = None
    committed = (
        settlement.status in ("completed", "exited", "stopped") and settlement.outcome is not None
    )
    if committed and scoring is not None:
        score, score_problem = _score(protocol, state, catalog, settlement.outcome, scoring)
    elif committed:
        score_problem = "没有给评分输入"
    out, nxt = settlement.outcome, settlement.next_state
    content = scoring.spec.content_fingerprint if scoring is not None else None
    fields = dict(
        person_id=state.person_id,
        clone_id=state.clone_id,
        city=state.city,
        year_index=state.year_index,
        start=state,
        settlement=settlement,
        score=score,
        score_problem=score_problem,
        protocol_fingerprint=model_fingerprint(protocol),
        start_fingerprint=model_fingerprint(state),
        action_fingerprint=model_fingerprint(action),
        catalog_fingerprint=model_fingerprint(catalog),
        tax_fingerprint=_tax_fingerprint(tax),
        scoring_inputs_fingerprint=None if scoring is None else _scoring_fingerprint(scoring),
        outcome_fingerprint=None if out is None else model_fingerprint(out),
        candidate_fingerprint=None if nxt is None else model_fingerprint(nxt),
        score_fingerprint=None if score is None else model_fingerprint(score),
        feedback_revision=FEEDBACK_HANDOFF_REVISION,
        scoring_content_fingerprint=content,
    )
    unsealed = ScoredYear.model_construct(**fields, binding_fingerprint="0" * 64)
    year = ScoredYear(**fields, binding_fingerprint=unsealed.expected_binding_fingerprint())
    year._sealed = (
        True  # 封存：只在真正结算并评分之后设，构造、model_validate 与 JSON 还原都得到 False
    )
    return year


def handoff_from(
    record: EnergyFeedbackRecord, year: ScoredYear
) -> tuple[str, PersonYearState | None]:
    """反馈记录 + 封存的年度绑定 → 年度交接（交接种类与交出去的状态）。``run_life_year`` 与 E1-6A 的轨迹核对
    （``econ.life_evaluation``）共用这一份：核对时从封存绑定重新推出交接，不相信结果对象里的字段。"""
    settlement = year.settlement
    if record.decision == "settlement_failed":
        return "settlement_failed", None
    if record.decision == "exited":
        return "exited", settlement.next_state
    if record.decision == "stopped":  # E1-5C：停止状态原样带过去，不复活、不推进
        return "stopped", settlement.next_state
    if record.decision == "no_next_action_year":
        return "terminal", settlement.next_state
    if record.decision == "blocked":
        return "feedback_blocked", None
    assert settlement.next_state is not None
    return "next_year_ready", apply_feedback(settlement.next_state, record)


def run_life_year(
    *,
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
    scoring: ScoringInputs | None,
) -> LifeYearResult:
    """结算一个分身的一年，评分，再按反馈政策确定下一年的月度精力额度。

    ``state`` 是这一年**年初**的正式状态（额度已确定）。``scoring`` 为空时只能在反馈关闭或 α = 0 时
    继续到下一年；反馈开着却没有完整评分，返回 ``feedback_blocked``：这一年的结算照常保留，但不返回可以
    直接开始下一年的状态。结算、评分与交接都经同一份年度绑定（``settle_and_score``）。
    """
    year = settle_and_score(
        protocol=protocol, state=state, action=action, catalog=catalog, tax=tax, scoring=scoring
    )
    policy = protocol.energy_feedback
    assert policy is not None
    settlement, score, score_problem = year.settlement, year.score, year.score_problem
    notes: list[str] = []
    committed = (
        settlement.status in ("completed", "exited", "stopped") and settlement.outcome is not None
    )
    record = feedback_record(protocol=protocol, year=year)

    handoff, next_state = handoff_from(record, year)
    if handoff == "feedback_blocked":
        notes.append(
            "下一年的精力额度无法确定：结算里的下一年候选状态标着额度待定，不能拿来结算或构造观察"
        )
    if not policy.effective and score_problem is not None and committed:
        notes.append(f"反馈关闭或 α = 0：评分缺失不阻断下一年（{score_problem}）")

    rules = protocol.resource_rules
    result = LifeYearResult(
        person_id=state.person_id,
        clone_id=state.clone_id,
        city=state.city,
        year_index=state.year_index,
        protocol_id=protocol.protocol_id,
        life_rules_version=protocol.life_rules_version,
        resource_rules_version=rules.version,
        resource_rules_revision=RESOURCE_RULES_REVISION,
        feedback_policy_id=policy.policy_id,
        feedback_policy_version=policy.version,
        feedback_revision=record.feedback_revision,
        binding_fingerprint=year.binding_fingerprint,
        handoff=handoff,  # type: ignore[arg-type]
        settlement=settlement,
        score=score,
        score_problem=score_problem,
        feedback=record,
        next_state=next_state,
        notes=tuple(notes),
    )
    # E1-6A：把这一次封存的年度绑定挂在结果的私有属性上（不序列化、不参与校验），供跨期评价核对同一次生活
    result._scored_year = year
    return result
