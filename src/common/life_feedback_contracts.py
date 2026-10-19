"""E1-4B 满足感 → 下一年月度精力额度的内部记录（`DECISIONS/0021`）。只有数据形状与局部约束。

这里的对象都是**内部对象**：反馈记录带着上一年的年度满足感分数与计算过程，年度交接结果带着整份结算、
评分与反馈。它们本身、以及它们原样序列化后的字典，都不能进代理观察——登记在 ``internal_feedback_models``，
与评分侧的 ``internal_scoring_models`` 合成同一份清单（``common.life_scoring_contracts.internal_models``），
正式加载、可见投影与最终 payload 三处检查都读它。

计算在 ``econ.life_feedback``（纯函数）；年度交接在 ``econ.life_year``（结算 → 评分 → 反馈）。

r2（2026-10-08 修复，DECISIONS/0021 第十三节）新增年度绑定 ``ScoredYear``：同一个年初状态的这一次实际结算、
对这份结果的实际评分、以及它产生的候选状态，在真正结算并评分的那一步一起建立并封存。反馈只从封存的绑定生成，
也只能写回绑定里的那一份候选状态——同人、同城、同年、同版本不足以认定是同一次生活。

r3（2026-10-09 修复，DECISIONS/0025）：声明了评分内容身份的政策下，年度绑定还记下实际评分规格的内容身份，反馈记录
写下政策声明的身份（两者必须相同），额度依据沿用它——下一年开始前与当前政策声明的比较。旧格式政策照旧是 r2，
这些字段不出现，产物逐字节不变。
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, model_validator

from common.ids import CityId
from common.life_contracts import (
    FEEDBACK_HANDOFF_REVISION,
    FEEDBACK_HANDOFF_REVISIONS,
    SCORING_CONTENT_HANDOFF_REVISION,
    ContractModel,
    EnergyFeedbackPolicy,
    ExactAmount,
    FeedbackPreferenceSource,
    IdStr,
    PersonYearState,
    SettlementResult,
    YearIndex,
    model_fingerprint,
)
from common.life_scoring_contracts import ExploratoryYearScore

_SHA256 = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=_SHA256)]
#: 年度交接的执行修订：r2 = 旧格式政策（没有声明评分内容身份），r3 = 声明了的（DECISIONS/0025）。
HandoffRevision = Literal["r2-2026-10-08", "r3-2026-10-09"]


class ScoredYear(ContractModel):
    """一次年度交接的绑定（r2）：年初状态 → 这一次实际结算 → 对这份结果的实际评分 → 它产生的候选状态。

    **只由 ``econ.life_year.settle_and_score`` 建立**：它自己调用结算器，再对这份结算结果、用这个年初状态调用
    评分器，然后封存（私有标记 ``_sealed``）。直接构造、``model_validate`` 或从 JSON 还原得到的对象都没有封存
    标记，反馈函数不接受——不能把任意一份分数和一份账目放在一起、各算一次指纹就当成"同一次生活"。

    依据（全部是确定的 SHA-256，可以重放，没有随机运行编号）：协议；年初状态；年度计划；城市目录；税绑定的声明
    身份；评分输入（规格、城市评分条件、偏好、硬要求）；实际结果；候选状态；分数；r3 下还有实际评分规格的内容身份。
    ``binding_fingerprint`` 是它们
    （加上人、分身、城市、年份与执行修订）的指纹。构造时核对对象与指纹一致；反馈生成与应用时再重算核对，
    事后替换其中任何一项都会被发现。这不是抵抗任意伪造全部内部数据的安全机制。

    内部对象：整体与原样序列化后的字典都不能进代理观察（登记在 ``internal_feedback_models``）。
    """

    person_id: IdStr
    clone_id: IdStr
    city: CityId
    year_index: YearIndex
    start: PersonYearState
    settlement: SettlementResult
    score: ExploratoryYearScore | None = None
    #: 有结算结果却没有评分时的说明（没有给评分输入、产品偏好信息不足）。
    score_problem: str | None = Field(default=None, max_length=600)
    protocol_fingerprint: Sha256
    start_fingerprint: Sha256
    action_fingerprint: Sha256
    catalog_fingerprint: Sha256
    tax_fingerprint: Sha256
    #: 没有给评分输入时为空。
    scoring_inputs_fingerprint: Sha256 | None = None
    outcome_fingerprint: Sha256 | None = None
    candidate_fingerprint: Sha256 | None = None
    score_fingerprint: Sha256 | None = None
    feedback_revision: HandoffRevision
    #: r3：这一年实际用来评分的规格的内容身份（``LifeScoringSpec.content_fingerprint``）；没有给评分输入时为空。
    #: r2 不写；没有用到时不序列化。
    scoring_content_fingerprint: Sha256 | None = Field(default=None, exclude_if=lambda v: v is None)
    binding_fingerprint: Sha256
    _sealed: bool = PrivateAttr(default=False)

    @property
    def sealed(self) -> bool:
        """是否由真正结算并评分的那一步封存。构造、``model_validate``、JSON 还原都得到 False。"""
        return self._sealed

    def evidence(self) -> dict[str, object]:
        """参与 ``binding_fingerprint`` 的全部依据（按名字排序后取 SHA-256）。r3 多一项实际评分内容身份；
        r2 的依据与修复前逐字节相同。"""
        out = self._evidence_r2()
        if self.feedback_revision == SCORING_CONTENT_HANDOFF_REVISION:
            out["scoring_content"] = self.scoring_content_fingerprint
        return out

    def _evidence_r2(self) -> dict[str, object]:
        return {
            "feedback_revision": self.feedback_revision,
            "person_id": self.person_id,
            "clone_id": self.clone_id,
            "city": str(self.city),
            "year_index": self.year_index,
            "protocol": self.protocol_fingerprint,
            "start": self.start_fingerprint,
            "action": self.action_fingerprint,
            "catalog": self.catalog_fingerprint,
            "tax": self.tax_fingerprint,
            "scoring_inputs": self.scoring_inputs_fingerprint,
            "outcome": self.outcome_fingerprint,
            "candidate": self.candidate_fingerprint,
            "score": self.score_fingerprint,
        }

    def expected_binding_fingerprint(self) -> str:
        text = json.dumps(
            self.evidence(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def object_problems(self) -> list[str]:
        """绑定里的对象与记下的指纹是否一致（构造时与使用时都调用）。空列表表示一致。"""
        out: list[str] = []
        s, out_obj, nxt = self.start, self.settlement.outcome, self.settlement.next_state
        if (s.person_id, s.clone_id, str(s.city), s.year_index) != (
            self.person_id,
            self.clone_id,
            str(self.city),
            self.year_index,
        ):
            out.append("年初状态不是这个分身这一年的")
        if out_obj is not None and (out_obj.person_id, out_obj.clone_id, out_obj.year_index) != (
            s.person_id,
            s.clone_id,
            s.year_index,
        ):
            out.append("结算结果不是这个年初状态的结果")
        pairs = (
            ("年初状态", s, self.start_fingerprint),
            ("实际结果", out_obj, self.outcome_fingerprint),
            ("候选状态", nxt, self.candidate_fingerprint),
            ("分数", self.score, self.score_fingerprint),
        )
        for name, obj, fp in pairs:
            if (obj is None) != (fp is None) or (obj is not None and model_fingerprint(obj) != fp):
                out.append(f"{name}与绑定时记下的指纹不一致")
        if self.score is not None and self.score_problem is not None:
            out.append("有分数就不该有评分问题说明")
        if (
            self.feedback_revision != SCORING_CONTENT_HANDOFF_REVISION
            and self.scoring_content_fingerprint is not None
        ):
            out.append("只有 r3 的年度绑定记评分内容身份")
        if self.feedback_revision == SCORING_CONTENT_HANDOFF_REVISION and (
            self.scoring_inputs_fingerprint is None
        ) != (self.scoring_content_fingerprint is None):
            out.append("r3 下给了评分输入就必须记下实际评分规格的内容身份，没给就不能记")
        if self.binding_fingerprint != self.expected_binding_fingerprint():
            out.append("绑定指纹与依据不一致")
        return out

    @model_validator(mode="after")
    def _consistent(self) -> ScoredYear:
        problems = self.object_problems()
        if problems:
            raise ValueError("年度绑定不一致：" + "；".join(problems))
        return self


#: 一次年度交接对下一年额度做了什么：
#:   applied              按上一年完整满意度算出新额度
#:   off_disabled         政策关闭：额度用基础值，不需要评分
#:   off_alpha_zero       政策开着但 α = 0：额度用基础值，不需要评分
#:   blocked              反馈开着、有下一行动年，但没有可用的完整评分：下一年额度无法确定
#:   no_next_action_year  第 N 年正常结束，下一年是终态，不计算加成
#:   exited               这一年出局：不更新精力、不复活
#:   stopped              （E1-5C）这一年在疾病之后停止（应急规则不足或资料缺口）：不更新精力、不推进
#:   settlement_failed    计划不可行或输入、资料错误：没有生活结果，不推进状态
FeedbackDecision = Literal[
    "applied",
    "off_disabled",
    "off_alpha_zero",
    "blocked",
    "no_next_action_year",
    "exited",
    "stopped",
    "settlement_failed",
]


class EnergyFeedbackRecord(ContractModel):
    """一次年度交接的内部反馈记录：能追到人、分身、城市、来源年与目标年、政策、分数来源、基础额度、
    原额度、新额度、未量化值、量化规则，以及为什么更新、关闭、无法计算或无需更新。

    额度单位是负担点（``energy_unit``），满足感是 0–1 的无量纲数，α 是无量纲比例——三者不混用。
    """

    person_id: IdStr
    clone_id: IdStr
    city: CityId
    source_year: YearIndex
    target_year: YearIndex | None
    policy_id: IdStr
    policy_version: str
    mode: Literal["enabled", "disabled"]
    declared_alpha: Annotated[float, Field(ge=0)]
    effective_alpha: Annotated[float, Field(ge=0)]
    decision: FeedbackDecision
    scoring_spec_id: IdStr
    scoring_version: str
    preference_source: FeedbackPreferenceSource
    #: 评分器给出的年度状态（complete / incomplete_months / exited）与年均分；没评分时为空。
    score_annual_status: str | None = None
    score_annual_mean: float | None = None
    #: 进入确定性计算的十进制写法（评分器浮点数的最短十进制表示）。
    score_decimal: str | None = None
    energy_unit: str
    base_quota: ExactAmount
    previous_quota: ExactAmount
    #: 未量化的 E_raw = base × (1 + α × S)，精确分数。
    raw_quota: ExactAmount | None = None
    #: 下一年实际使用的额度（向下保留到 0.01 负担点）。
    new_quota: ExactAmount | None = None
    #: 声明的上限 base × (1 + α)。
    quota_upper_bound: ExactAmount
    quantization: Literal["floor_to_0.01"]
    #: 这一年结算结果与评分的 SHA-256（原样序列化后计算）。r2 起它们来自年度绑定 ``ScoredYear``，
    #: 分数与账目的对应由绑定保证，不由这两个指纹单独证明。
    outcome_fingerprint: Annotated[str, Field(pattern=_SHA256)] | None = None
    score_fingerprint: Annotated[str, Field(pattern=_SHA256)] | None = None
    #: r2：反馈政策的内容指纹、年度绑定的指纹、这次交接对应的候选状态的指纹、执行修订。
    #: 反馈只能写回 ``candidate_fingerprint`` 指向的那一份候选状态。
    policy_fingerprint: Sha256
    binding_fingerprint: Sha256
    candidate_fingerprint: Sha256 | None = None
    feedback_revision: HandoffRevision
    #: r3：政策声明、年度交接核对过的评分内容身份——下一年额度的来源评分内容。r2 不写；没有用到时不序列化。
    scoring_content_fingerprint: Sha256 | None = Field(default=None, exclude_if=lambda v: v is None)
    reasons: tuple[Annotated[str, Field(min_length=1, max_length=400)], ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> EnergyFeedbackRecord:
        d = self.decision
        if (d in ("applied", "off_disabled", "off_alpha_zero")) != (self.new_quota is not None):
            raise ValueError("只有确定了下一年额度的交接（反馈已算或已关闭）才有新额度")
        if d in ("applied", "off_disabled", "off_alpha_zero", "blocked") and (
            self.target_year != self.source_year + 1
        ):
            raise ValueError("下一行动年必须正好是来源年的下一年")
        if (
            d in ("no_next_action_year", "exited", "stopped", "settlement_failed")
            and self.target_year is not None
        ):
            raise ValueError(f"{d} 没有下一行动年")
        if d == "applied":
            if (
                self.score_decimal is None
                or self.raw_quota is None
                or self.score_fingerprint is None
            ):
                raise ValueError("反馈已算必须写明分数的十进制写法、未量化值与评分指纹")
            if self.score_annual_status != "complete":
                raise ValueError("只有 12 个月都已评分的完整年度才能用于反馈")
        if d in ("off_disabled", "off_alpha_zero") and self.raw_quota is not None:
            raise ValueError("关闭的反馈不计算加成")
        if (
            d in ("applied", "off_disabled", "off_alpha_zero")
            and self.candidate_fingerprint is None
        ):
            raise ValueError("确定了下一年额度的交接必须指向对应的候选状态")
        if d == "blocked" and not self.reasons:
            raise ValueError("反馈信息不足必须列出原因")
        if (self.feedback_revision == SCORING_CONTENT_HANDOFF_REVISION) != (
            self.scoring_content_fingerprint is not None
        ):
            raise ValueError("r3 的反馈记录必须写评分内容身份，r2 的不能写")
        return self


#: 年度交接的结果：
#:   next_year_ready   下一年额度已确定，``next_state`` 可以直接开始下一年
#:   terminal          第 N 年正常结束，``next_state`` 是终态
#:   exited            这一年出局，``next_state`` 是出局状态
#:   settlement_failed 计划不可行或输入、资料错误：没有生活结果，不推进状态
#:   feedback_blocked  这一年的真实结算已完成并保留，但下一年额度无法确定（反馈信息不足）：
#:                     **没有**可直接开始下一年的状态，候选状态只留在结算结果里（标着待定，不能结算）
#:   stopped           （E1-5C）这一年在疾病之后停止：``next_state`` 是停止状态，停止月之前的生活保留，不再继续
Handoff = Literal[
    "next_year_ready", "terminal", "exited", "stopped", "settlement_failed", "feedback_blocked"
]


class LifeYearResult(ContractModel):
    """一年的年度交接（结算 → 评分 → 反馈）结果。内部对象：整体不能进代理观察。

    一次运行由这些版本共同标识：协议身份与生活规则版本、月度资源规则版本与执行修订、评分规格与版本、
    反馈政策与版本（含开关与 α）。信息条件（代理看到什么）在观察一侧另记。
    """

    person_id: IdStr
    clone_id: IdStr
    city: CityId
    year_index: YearIndex
    protocol_id: IdStr
    life_rules_version: str
    resource_rules_version: str
    resource_rules_revision: str
    feedback_policy_id: IdStr
    feedback_policy_version: str
    #: r2：年度交接的执行修订与这一次年度绑定的指纹（与反馈记录里的一致）。r3 见 DECISIONS/0025。
    feedback_revision: HandoffRevision
    binding_fingerprint: Sha256
    handoff: Handoff
    settlement: SettlementResult
    score: ExploratoryYearScore | None = None
    #: 有评分输入却没能给出评分（例如产品偏好信息不足）时的说明。
    score_problem: str | None = Field(default=None, max_length=600)
    feedback: EnergyFeedbackRecord
    #: 可以继续使用的正式状态：下一年额度已确定的年初状态，或终态、出局状态。其余情况为空。
    next_state: PersonYearState | None = None
    notes: tuple[Annotated[str, Field(min_length=1, max_length=400)], ...] = ()
    #: E1-6A（DECISIONS/0028）：``econ.life_year.run_life_year`` 当场封存的年度绑定。**私有属性**：不序列化、不参与校验，
    #: 已有输出逐字节不变；从 JSON 还原或直接构造的结果没有它（跨期评价因此拒绝"直接导入"，要求从原始输入重放）。
    _scored_year: ScoredYear | None = PrivateAttr(default=None)

    @property
    def scored_year(self) -> ScoredYear | None:
        """这一次交接所用的封存年度绑定（只有 ``run_life_year`` 产生的结果才有）。"""
        return self._scored_year

    @model_validator(mode="after")
    def _consistent(self) -> LifeYearResult:
        h, nxt = self.handoff, self.next_state
        if (h in ("settlement_failed", "feedback_blocked")) != (nxt is None):
            raise ValueError("没有推进或下一年额度未定时不返回状态；其余情况必须返回")
        if nxt is not None:
            want = {
                "next_year_ready": "active",
                "terminal": "terminal",
                "exited": "exited",
                "stopped": "stopped",
            }[h]
            if nxt.status != want:
                raise ValueError(f"{h} 的下一状态必须是 {want}，得到 {nxt.status}")
            basis = nxt.energy_quota_basis
            if h == "next_year_ready" and (basis is None or basis.status == "pending_feedback"):
                raise ValueError("可以开始下一年的状态必须已经确定额度，不能是待定的候选状态")
        committed = ("completed", "exited", "stopped")
        if h == "settlement_failed" and self.settlement.status in committed:
            raise ValueError("结算已完成却标成失败")
        if h != "settlement_failed" and self.settlement.status not in committed:
            raise ValueError("结算没有完成就不能交接")
        if self.binding_fingerprint != self.feedback.binding_fingerprint:
            raise ValueError("年度交接结果与反馈记录指向不同的年度绑定")
        if self.feedback_revision != self.feedback.feedback_revision:
            raise ValueError("年度交接结果与反馈记录的执行修订不同")
        return self


def internal_feedback_models() -> tuple[type[ContractModel], ...]:
    """E1-4B 反馈侧的内部对象类型。它们本身、以及原样序列化后的字典，都不能进观察。"""
    return (EnergyFeedbackPolicy, EnergyFeedbackRecord, LifeYearResult, ScoredYear)


# 上面三处 HandoffRevision 与常量保持一致
assert FEEDBACK_HANDOFF_REVISION == "r2-2026-10-08"
assert FEEDBACK_HANDOFF_REVISIONS == (FEEDBACK_HANDOFF_REVISION, SCORING_CONTENT_HANDOFF_REVISION)
assert SCORING_CONTENT_HANDOFF_REVISION == "r3-2026-10-09"
