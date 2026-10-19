"""满足感 → 下一年月度精力额度的有界反馈（E1-4B，`DECISIONS/0021`）。纯函数：无 I/O、无随机、无 LLM。

    E_raw(y+1) = E_base × (1 + α × S_y)
    下一年额度 = E_raw 向下保留到 0.01 负担点

生活含义：上一年过得越满意（S 越接近 1），下一年每个月能承受的活动负担越多，但最多多出 α（本版 ≤ 10%）；
过得不满意也不会低于自己的基础额度。只看上一年，每次都从固定的基础额度算，不会年年复利。

* ``S_y`` 只取 E1-3 探索评分器对**这一年实际结算结果**给出的完整年度平均满足感（12 个月都已评分）；
  不用 LLM 自报、行动理由、单个维度、部分月份的平均、覆盖率，也不用现金、收入或剩余时间换算。
* 评分器的浮点结果按它的最短十进制写法（``repr``）变成精确分数，再做确定性计算；先保留未量化的
  ``E_raw``，再向下保留到 0.01 负担点，量化前后都记下来。不加 epsilon。
* 关闭反馈或 α = 0：下一年额度就是基础值，不需要评分。
* 反馈开着、有下一行动年，但没有完整评分：返回"反馈信息不足"（``blocked``）并列出原因——不补零、不用
  已知月份平均、不重新归一化偏好、不维持旧加成，也不擅自恢复基础额度说成反馈已完成。
* 分数的人、分身、城市、年份、评分规格、偏好来源对不上，直接拒绝（``LifeContractError``）。

r2（2026-10-08 修复，DECISIONS/0021 第十三节）：

* ``feedback_record`` 只接受年度绑定 ``ScoredYear``——同一个年初状态的这一次实际结算、对这份结果的实际评分
  与它产生的候选状态，由 ``econ.life_year.settle_and_score`` 在真正结算并评分时一起封存。分别传入的
  ``start`` / ``settlement`` / ``score`` 被明确拒绝：同人、同城、同年、同版本不足以证明是同一次生活。
* ``apply_feedback`` 只把额度写进记录指向的那一份候选状态（内容指纹相同），并核对政策内容指纹。
* 年初状态与候选状态的政策核对用 ``common.life_contracts.energy_quota_problems``，与加载、结算、观察同一份。

r3（2026-10-09 修复，DECISIONS/0025）：政策声明了评分内容身份时，记录写下它（并核对年度绑定里实际评分规格的
身份与之相同），``apply_feedback`` 核对候选状态记下的身份并写进下一年的额度依据。

签名里只有协议（反馈政策）与年度绑定；没有偏好真值或行动理由。年度交接（结算 → 评分 → 本模块）在
``econ.life_year``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from common.life_contracts import (
    EnergyFeedbackPolicy,
    EnergyQuotaBasis,
    EnergyState,
    ExactAmount,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    model_fingerprint,
    to_fraction,
)
from common.life_feedback_contracts import EnergyFeedbackRecord, ScoredYear
from common.life_scoring_contracts import ExploratoryYearScore

#: 量化单位：0.01 负担点（与资源输入的两位小数一致，量化后的额度能直接进入下一年的资源检查）。
QUANTUM = Fraction(1, 100)


def fingerprint(model: object) -> str:
    """一个契约对象原样序列化后的 SHA-256（``common.life_contracts.model_fingerprint``）。

    指纹只说明一份对象的内容；"分数来自这一份账目"由年度绑定 ``ScoredYear`` 保证，不由两个指纹单独证明。
    """
    return model_fingerprint(model)  # type: ignore[arg-type]


def score_fraction(value: float) -> tuple[Fraction, str]:
    """评分器的年度满足感（浮点）→ 最短十进制写法 → 精确分数。必须有限且在 [0, 1]。

    不重写评分器的浮点算法；这里只把它的输出按明确的十进制表示接进确定性计算。
    """
    if isinstance(value, bool) or not isinstance(value, float) or not math.isfinite(value):
        raise LifeContractError(f"年度满足感必须是有限的浮点数，得到 {value!r}")
    text = repr(value)
    q = Fraction(Decimal(text))
    if not 0 <= q <= 1:
        raise LifeContractError(f"年度满足感必须在 [0, 1] 内，得到 {text}")
    return q, text


def floor_to_quantum(x: Fraction) -> Fraction:
    """向下保留到 0.01：floor(x × 100) / 100。不四舍五入，不加 epsilon。"""
    return Fraction(math.floor(x / QUANTUM)) * QUANTUM


@dataclass(frozen=True)
class QuotaComputation:
    """一次额度计算的全部中间量（精确分数）。"""

    score: Fraction
    score_text: str
    raw: Fraction
    quantized: Fraction
    upper: Fraction


def quota_from_score(base_level: float, alpha: float, score: float) -> QuotaComputation:
    """E_raw = base × (1 + α × S)，再向下保留到 0.01。手算：base 250、α 0.10、S 0.6 → 265。"""
    base, a = to_fraction(base_level), to_fraction(alpha)
    if base <= 0:
        raise LifeContractError(f"基础额度必须为正，得到 {base_level!r}")
    if not 0 <= a <= to_fraction(0.10):
        raise LifeContractError(f"α 必须在 [0, 0.10] 内，得到 {alpha!r}")
    s, text = score_fraction(score)
    raw = base * (1 + a * s)
    quantized = floor_to_quantum(raw)
    upper = base * (1 + a)
    if (
        not base <= quantized <= upper
    ):  # 由 0 ≤ S ≤ 1、base 是两位小数保证；到这里说明前面有人绕过了检查
        raise LifeContractError(f"量化后的额度 {quantized} 越出 [{base}, {upper}]")
    return QuotaComputation(score=s, score_text=text, raw=raw, quantized=quantized, upper=upper)


def _policy(protocol: LifeProtocol) -> EnergyFeedbackPolicy:
    policy = protocol.energy_feedback
    if policy is None:
        raise LifeContractError(
            f"协议 {protocol.protocol_id} 没有满足感反馈政策：E1-4A 与更早的协议额度固定，不做年度反馈"
        )
    return policy


def _quota_basis(state: PersonYearState) -> EnergyQuotaBasis:
    basis = state.energy_quota_basis
    if basis is None:
        raise LifeContractError(f"{state.clone_id}: 没有基础额度与额度依据，不能做年度反馈")
    return basis


def _check_score(
    policy: EnergyFeedbackPolicy, start: PersonYearState, score: ExploratoryYearScore
) -> None:
    """分数必须属于同一个人、分身、城市和年份，并来自政策声明的评分规格与偏好来源。不对就拒绝。"""
    if type(score) is not ExploratoryYearScore:
        raise LifeContractError(
            f"反馈只接受评分器给出的 ExploratoryYearScore，得到 {type(score).__name__}"
        )
    mine = (start.person_id, start.clone_id, str(start.city), start.year_index)
    theirs = (score.person_id, score.clone_id, str(score.city), score.year_index)
    if mine != theirs:
        raise LifeContractError(
            f"分数属于 {theirs}，这一年是 {mine}：不能拿另一个分身、另一座城或另一年的分数更新额度"
        )
    if (score.spec_id, score.scoring_version) != (policy.scoring_spec_id, policy.scoring_version):
        raise LifeContractError(
            f"分数来自评分规格 {score.spec_id}（{score.scoring_version}），反馈政策声明的是 "
            f"{policy.scoring_spec_id}（{policy.scoring_version}）"
        )
    if score.preference_source != policy.preference_source:
        raise LifeContractError(
            f"分数用的偏好来源是 {score.preference_source}，反馈政策声明的是 {policy.preference_source}"
            "——产品与研究两条路径不混"
        )


def verified_year(protocol: LifeProtocol, year: object) -> ScoredYear:
    """核对一份年度绑定可以用来生成反馈：由真正结算并评分的那一步封存、对象没有被替换、属于这个协议。

    核对了什么：① 类型是 ``ScoredYear`` 且有封存标记（直接构造、``model_validate``、JSON 还原的都没有）；
    ② 年初状态、实际结果、候选状态与分数逐个重算指纹，与封存时记下的一致，绑定指纹与依据一致（事后用
    ``model_copy`` 换掉其中一项会被发现）；③ 绑定时的协议就是现在传入的协议。
    """
    if type(year) is not ScoredYear:
        raise LifeContractError(
            f"反馈只接受年度绑定 ScoredYear（econ.life_year.settle_and_score），得到 {type(year).__name__}"
        )
    if not year.sealed:
        raise LifeContractError(
            f"{year.clone_id} 第 {year.year_index} 年的年度绑定没有封存：只有 econ.life_year.settle_and_score "
            "在真正结算并评分之后建立的绑定才算同一次生活；直接构造或从字典还原的绑定不被接受"
        )
    problems = year.object_problems()
    if problems:
        raise LifeContractError(
            f"{year.clone_id} 第 {year.year_index} 年的年度绑定被改动过：" + "；".join(problems)
        )
    if year.protocol_fingerprint != model_fingerprint(protocol):
        raise LifeContractError(
            "年度绑定是在另一份协议下建立的：分数、账目与政策必须属于同一次交接"
        )
    return year


def feedback_record(
    *,
    protocol: LifeProtocol,
    year: ScoredYear | None = None,
    **legacy: object,
) -> EnergyFeedbackRecord:
    """一次年度交接对下一年额度的决定。``year`` 是年度绑定：这一年**年初**状态、它的这一次实际结算、评分器
    对**这一份**结果的评分（没有评分时为空并写明原因），以及结算产生的候选状态。

    r1 的调用方式（分别传 ``start``、``settlement``、``score``）被明确拒绝：两次真实生活即使同人、同城、同年，
    也可能有不同的账目与分数，分别传入无法证明它们来自同一次生活。
    """
    if legacy or year is None:
        names = ", ".join(sorted(legacy)) or "（没有给 year）"
        raise LifeContractError(
            f"feedback_record 只接受年度绑定 year=ScoredYear，不再接受分别传入的 {names}：账目、分数与候选状态"
            "必须由 econ.life_year.settle_and_score 在同一次结算并评分时一起建立"
        )
    year = verified_year(protocol, year)
    policy = _policy(protocol)
    if year.feedback_revision != policy.handoff_revision:
        raise LifeContractError(
            f"年度绑定的执行修订是 {year.feedback_revision}，这份政策下应是 {policy.handoff_revision}"
        )
    if (
        year.scoring_content_fingerprint is not None
        and year.scoring_content_fingerprint != policy.scoring_content_fingerprint
    ):
        raise LifeContractError(
            f"这一年实际用来评分的规格内容身份是 {year.scoring_content_fingerprint}，反馈政策声明的是 "
            f"{policy.scoring_content_fingerprint}：分数来自另一套评分内容，不能确定下一年额度"
        )
    rules = protocol.resource_rules
    assert rules is not None  # E1-4B 的协议必须带月度资源规则（契约检查）
    start, settlement = year.start, year.settlement
    score, score_problem = year.score, year.score_problem
    if start.status != "active":
        raise LifeContractError(f"{start.clone_id}: 年初状态是 {start.status}，不是一个行动年")
    basis = _quota_basis(start)
    if basis.status == "pending_feedback":
        raise LifeContractError(f"{start.clone_id}: 年初额度还没确定，这一年不该被结算")
    base, previous = to_fraction(basis.base_level), to_fraction(start.energy.level)
    head = dict(
        person_id=start.person_id,
        clone_id=start.clone_id,
        city=start.city,
        source_year=start.year_index,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        mode=policy.mode,
        declared_alpha=policy.alpha,
        effective_alpha=policy.alpha if policy.effective else 0.0,
        scoring_spec_id=policy.scoring_spec_id,
        scoring_version=policy.scoring_version,
        preference_source=policy.preference_source,
        energy_unit=rules.energy_unit,
        base_quota=ExactAmount.of(base),
        previous_quota=ExactAmount.of(previous),
        quota_upper_bound=ExactAmount.of(base * (1 + to_fraction(policy.alpha))),
        quantization="floor_to_0.01",
        policy_fingerprint=policy.content_fingerprint,
        binding_fingerprint=year.binding_fingerprint,
        candidate_fingerprint=year.candidate_fingerprint,
        feedback_revision=policy.handoff_revision,
        scoring_content_fingerprint=policy.scoring_content_fingerprint,
    )
    out, nxt = settlement.outcome, settlement.next_state
    if out is not None:
        who = (out.person_id, out.clone_id, out.year_index)
        if who != (start.person_id, start.clone_id, start.year_index):
            raise LifeContractError(f"结算结果 {who} 不是这个年初状态的结果")
        head["outcome_fingerprint"] = year.outcome_fingerprint
    if score is not None:
        _check_score(policy, start, score)
        exited_score = score.annual_status == "exited"
        if out is not None and exited_score != (out.settlement_status == "exited"):
            raise LifeContractError("分数的出局状态与结算结果对不上：分数不是这一份账目的评分")
        stopped_score = score.annual_status == "stopped"
        if out is not None and stopped_score != (out.settlement_status == "stopped"):
            raise LifeContractError("分数的停止状态与结算结果对不上：分数不是这一份账目的评分")
        head.update(
            score_annual_status=score.annual_status,
            score_annual_mean=score.annual_mean,
            score_fingerprint=year.score_fingerprint,
        )

    if settlement.status not in ("completed", "exited", "stopped") or out is None or nxt is None:
        return EnergyFeedbackRecord(
            **head,
            target_year=None,
            decision="settlement_failed",
            reasons=(
                f"结算状态 {settlement.status}：没有生活结果，不产生满意度反馈，也不推进状态",
            ),
        )
    if nxt.status == "exited":
        return EnergyFeedbackRecord(
            **head,
            target_year=None,
            decision="exited",
            reasons=("这一年出局：不更新精力、不复活；出局信息保留在结算结果里",),
        )
    if nxt.status == "stopped":
        return EnergyFeedbackRecord(
            **head,
            target_year=None,
            decision="stopped",
            reasons=(
                "这一年在疾病之后停止（应急安排不足或资料缺口）：不更新精力、不推进；停止月之前的生活保留在结算结果里",
            ),
        )
    if nxt.status == "terminal":
        return EnergyFeedbackRecord(
            **head,
            target_year=None,
            decision="no_next_action_year",
            reasons=(
                f"第 {start.year_index} 年是最后一个行动年（N = {protocol.simulation_years}）："
                "下一年是终态，不为不存在的行动年计算加成；这一年照常评分与记录",
            ),
        )

    target = start.year_index + 1
    if not policy.effective:
        decision = "off_disabled" if policy.mode == "disabled" else "off_alpha_zero"
        why = "反馈政策关闭" if policy.mode == "disabled" else "反馈政策开着但 α = 0"
        return EnergyFeedbackRecord(
            **head,
            target_year=target,
            decision=decision,  # type: ignore[arg-type]
            new_quota=ExactAmount.of(base),
            reasons=(f"{why}：下一年额度就是基础值 {basis.base_level:g}，不需要评分",),
        )

    reasons: list[str] = []
    if score is None:
        reasons.append(score_problem or "没有这一年的评分：反馈开着时必须对实际结算结果评分")
    elif score.annual_status != "complete" or score.annual_mean is None:
        reasons.append(
            f"年度评分不完整（{score.annual_status}）：第 {list(score.months_incomplete)} 月有正权重的分项"
            "数据未知——不补零、不用已知月份平均、不重新归一化偏好"
        )
    if reasons:
        return EnergyFeedbackRecord(
            **head,
            target_year=target,
            decision="blocked",
            reasons=(
                *reasons,
                "反馈信息不足：下一年额度无法确定。这一年的真实结算照常保留，不是计划不可行；"
                "也不维持旧加成或擅自恢复基础额度说成反馈已完成",
            ),
        )
    assert score is not None and score.annual_mean is not None
    try:
        q = quota_from_score(basis.base_level, policy.alpha, score.annual_mean)
    except LifeContractError as exc:
        return EnergyFeedbackRecord(
            **head,
            target_year=target,
            decision="blocked",
            reasons=(f"年度满足感不能用于计算：{exc}", "反馈信息不足：下一年额度无法确定"),
        )
    return EnergyFeedbackRecord(
        **head,
        target_year=target,
        decision="applied",
        score_decimal=q.score_text,
        raw_quota=ExactAmount.of(q.raw),
        new_quota=ExactAmount.of(q.quantized),
        reasons=(
            f"E_raw = {basis.base_level:g} × (1 + {policy.alpha:g} × {q.score_text}) = {float(q.raw):.6f}，"
            f"向下保留到 0.01 得 {float(q.quantized):.2f} 负担点；只用第 {start.year_index} 年，"
            "从固定基础额度算，不复利",
        ),
    )


def apply_feedback(candidate: PersonYearState, record: EnergyFeedbackRecord) -> PersonYearState:
    """把一次已确定的额度写进下一年的年初状态。只接受结算刚产生的候选状态（额度待定），而且必须是
    记录所说的那个分身、那一年——同一次交接重复应用，或拿别的年份、别的分身的记录，都被拒绝。

    r2：候选状态必须**正是**这条记录的年度绑定里的那一份（内容指纹相同），并且由同一份政策内容产生。
    同人、同城、同年的另一次生活（例如住远处的试算）的候选状态不接受这条记录。"""
    if record.decision not in ("applied", "off_disabled", "off_alpha_zero"):
        raise LifeContractError(f"反馈记录是 {record.decision}：没有确定的下一年额度可写")
    basis = candidate.energy_quota_basis
    if candidate.status != "active" or basis is None or basis.status != "pending_feedback":
        raise LifeContractError(
            f"{candidate.clone_id}: 只能把额度写进结算刚产生、额度待定的候选状态；这个状态已经确定过额度"
            "（重复交接不叠加加成）"
        )
    if (candidate.person_id, candidate.clone_id, str(candidate.city), candidate.year_index) != (
        record.person_id,
        record.clone_id,
        str(record.city),
        record.target_year,
    ):
        raise LifeContractError(
            f"记录属于 {record.clone_id} 第 {record.target_year} 年，候选状态是 {candidate.clone_id} "
            f"第 {candidate.year_index} 年"
        )
    if basis.source_year != record.source_year or basis.policy_id != record.policy_id:
        raise LifeContractError("候选状态的来源年或反馈政策与记录不一致")
    if basis.policy_fingerprint != record.policy_fingerprint:
        raise LifeContractError(
            "候选状态由另一份政策内容产生（编号可能相同）：不能用这份政策的反馈记录确定它的额度"
        )
    if basis.scoring_content_fingerprint != record.scoring_content_fingerprint:
        raise LifeContractError(
            f"候选状态记下的评分内容是 {basis.scoring_content_fingerprint}，反馈记录的是 "
            f"{record.scoring_content_fingerprint}：额度来自另一套评分内容，不能写回"
        )
    if (
        record.candidate_fingerprint is None
        or model_fingerprint(candidate) != record.candidate_fingerprint
    ):
        raise LifeContractError(
            f"{candidate.clone_id} 第 {candidate.year_index} 年：这条反馈记录属于另一次年度结算的候选状态——"
            "同人、同城、同年的另一次生活（不同的账目、现金或物品）不能借用它的额度"
        )
    if to_fraction(basis.base_level) != record.base_quota.fraction():
        raise LifeContractError("候选状态的基础额度与记录不一致：基础额度不随年份改变")
    assert record.new_quota is not None
    new = record.new_quota.fraction()
    level = float(new)
    if to_fraction(level) != new:  # 量化到 0.01 后，浮点的最短写法就是这个两位小数
        raise LifeContractError(f"额度 {new} 不能精确写成两位小数")
    data = candidate.model_dump(mode="python")
    data["energy"] = EnergyState(level=level, scale_version=candidate.energy.scale_version)
    data["energy_quota_basis"] = EnergyQuotaBasis(
        base_level=basis.base_level,
        status="feedback_applied" if record.decision == "applied" else "feedback_off",
        policy_id=record.policy_id,
        policy_version=record.policy_version,
        source_year=record.source_year,
        record_fingerprint=fingerprint(record),
        policy_fingerprint=record.policy_fingerprint,
        scoring_content_fingerprint=record.scoring_content_fingerprint,
    )
    return PersonYearState.model_validate(data)
