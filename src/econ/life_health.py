"""疾病、应急与恢复的逐月处理（E1-5C，`DECISIONS/0027`）。纯函数与一个逐月记录器：无 I/O、无 LLM。

结算（``econ.life_settlement``）是唯一调用方，每个月按这个顺序用它：

    月初   tracker.month_start(m)        定这个月的状态：延续中的疾病照旧；健康月份才看新事件（指定事件或配对随机数）
    执行   decide_treatment(...)          治不治：年度计划的选择、治疗还有没有用、本城有没有（钱与时间在结算里判断）
           recovery_load(...)             生病月份的恢复时间与负担（进同一本时间与精力账）
    月末   tracker.month_end(m, ...)      记下这个月的事实，并按声明的规则转移：继续生病、治疗后恢复、自然恢复
    出局   tracker.exit_month(m)          出局当月：不治疗、不转移，疾病原样带进出局状态
    停止   tracker.blocked(m, ...)        停止月：事件保留，执行没有提交
    年末   tracker.finish(status)         这一年的健康事实（进结算结果）与下一年（或终态、出局、停止）的健康状态

**随机数不是 Python 的 ``random`` 或 ``hash()``。** 一个月的随机数只由键决定：流版本、配对身份、种子、人、重复分身
序号、绝对月份（(年 − 1) × 12 + 月）与事件类别。键按规范 JSON 写出，取 SHA-256 的前 64 位 ÷ 2^64，得到 [0, 1) 内的
精确分数。键里没有城市、分身编号、遍历顺序、重试次数，也没有评分或风险规则的指纹：同序号的分身跨城共用运气；同一份
输入重放、重试或增减别的分身，都得到同一个数。共用运气不等于共用结果——两座城的概率不同，同一个数可以一个发病、一个不发。

**概率只读上一个完整月份。** 风险表按年龄段 × 上月实际工作相对负担（工作负担 ÷ 当月额度）取一格；本月生病之后的
工作量不进入本月的概率。第一个月没有上月记录，用风险表声明的基础档。健康输入（医疗点数）不进这里的任何函数。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction

from common.life_contracts import (
    MONTHS_PER_YEAR,
    ExactAmount,
    HealthBinding,
    HealthMonth,
    HealthRules,
    HealthState,
    HealthYear,
    IllnessEpisode,
    IllnessEventSource,
    LifeContractError,
    PausedActivity,
    PersonYearState,
    RiskDraw,
    TreatmentOffer,
    TreatmentOutcome,
    TreatmentRecord,
    to_fraction,
)

#: 随机流里"新发疾病"这一类事件的名字（键的一部分）。
ONSET_CATEGORY = "illness_onset"
_TWO_64 = 2**64


# ==========================================================================
# 随机数：稳定的键 → [0, 1) 内的精确分数
# ==========================================================================


def illness_key(
    source: IllnessEventSource, person_id: str, repeat_index: int, year: int, month: int
) -> dict[str, object]:
    """一个月新发疾病的随机键。没有城市、分身编号、遍历顺序、重试次数或规则指纹。"""
    return {
        "stream": source.stream_version,
        "pairing_id": source.pairing_id,
        "seed": source.seed,
        "person_id": person_id,
        "repeat_index": repeat_index,
        "absolute_month": (year - 1) * MONTHS_PER_YEAR + month,
        "category": ONSET_CATEGORY,
    }


def uniform_of(key: dict[str, object]) -> tuple[Fraction, str]:
    """键 → （u，键的 SHA-256）。u = SHA-256 前 64 位 ÷ 2^64，精确分数，落在 [0, 1)。"""
    text = json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Fraction(int(digest[:16], 16), _TWO_64), digest


# ==========================================================================
# 风险格与抽签
# ==========================================================================


@dataclass(frozen=True)
class RiskLookup:
    """这个月适用的一格概率，以及上月负担从哪来。"""

    age_band: str
    burden_band: str
    last_burden: Fraction | None
    burden_from: str
    p_minor: Fraction
    p_major: Fraction


def risk_lookup(
    rules: HealthRules,
    age: int,
    last_burden: Fraction | None,
    last_from: tuple[int, int] | None,
) -> RiskLookup:
    """年龄段 × 上一个完整月份的工作相对负担 → 月概率。第一个月（没有上月记录）用声明的基础档。"""
    table = rules.risk
    band = table.age_band_of(age)
    if band is None:
        raise LifeContractError(f"年龄 {age} 不在风险表的任何年龄段里")
    if last_burden is None:
        burden_band = table.first_month_burden_band
        burden_from = f"没有上月记录：用声明的基础档 {burden_band}"
    else:
        burden_band = table.burden_band_of(last_burden).band_id
        assert last_from is not None
        burden_from = f"第 {last_from[0]} 年第 {last_from[1]} 月的工作相对负担"
    cell = table.cell(band.band_id, burden_band)
    return RiskLookup(
        age_band=band.band_id,
        burden_band=burden_band,
        last_burden=last_burden,
        burden_from=burden_from,
        p_minor=to_fraction(cell.minor),
        p_major=to_fraction(cell.major),
    )


def classify(u: Fraction, p_minor: Fraction, p_major: Fraction) -> str:
    """u < p_大病 → 大病；p_大病 ≤ u < p_大病 + p_小病 → 小病；其余没有事件。"""
    if u < p_major:
        return "major"
    if u < p_major + p_minor:
        return "minor"
    return "none"


def draw_onset(
    rules: HealthRules,
    source: IllnessEventSource,
    *,
    person_id: str,
    repeat_index: int,
    age: int,
    year: int,
    month: int,
    last_burden: Fraction | None,
    last_from: tuple[int, int] | None,
) -> RiskDraw:
    """一个健康月份月初的抽签（只在 ``random`` 来源下调用）。"""
    look = risk_lookup(rules, age, last_burden, last_from)
    u, digest = uniform_of(illness_key(source, person_id, repeat_index, year, month))
    return RiskDraw(
        stream_version=source.stream_version,
        key_fingerprint=digest,
        uniform=ExactAmount.of(u),
        age_years=age,
        age_band=look.age_band,
        burden_band=look.burden_band,
        last_work_relative_burden=None if last_burden is None else ExactAmount.of(last_burden),
        burden_from=look.burden_from,
        p_minor=ExactAmount.of(look.p_minor),
        p_major=ExactAmount.of(look.p_major),
        result=classify(u, look.p_minor, look.p_major),  # type: ignore[arg-type]
    )


# ==========================================================================
# 治疗、恢复负担与月末转移
# ==========================================================================


def treatment_has_benefit(rules: HealthRules, episode: IllnessEpisode) -> bool:
    """这个病月接受治疗有没有用：不治的话疾病还会延续到下个月，治疗才改变转移。"""
    return episode.months_ill < rules.severity_rules(episode.severity).untreated_months


def decide_treatment(
    rules: HealthRules, choice: str, offer: TreatmentOffer | None, episode: IllnessEpisode
) -> str:
    """治不治（钱与时间、精力在结算里判断）：返回 ``attempt``，或不尝试的原因。

    顺序：计划明确不治 → 治疗没有用（疾病本来就在这个月结束；不需要任何资料）→ 本城资料未知（资料缺口）→
    本城确定没有。都不是就尝试。"""
    if choice == "decline":
        return "declined"
    if not treatment_has_benefit(rules, episode):
        return "no_benefit"
    if offer is None:  # 加载检查保证每座城都写了；到这里说明有人绕过了检查
        raise LifeContractError(f"本城没有 {episode.severity} 的治疗条目（资料检查应已拦下）")
    if offer.availability == "unknown":
        return "data_gap"
    if offer.availability == "unavailable":
        return "unavailable"
    return "attempt"


@dataclass(frozen=True)
class ExtraLoad:
    """月度资源账里的一项附加负担：生病月份的恢复，或接受治疗那个月的就医（同一个资源算法计入）。"""

    category: str  # recovery / treatment
    hours: Fraction
    burden_per_hour: Fraction
    basis: str
    opportunity_id: str | None = None
    task: str | None = None


def recovery_load(rules: HealthRules, severity: str) -> ExtraLoad:
    sr = rules.severity_rules(severity)
    label = "小病" if severity == "minor" else "大病"
    return ExtraLoad(
        category="recovery",
        hours=to_fraction(sr.recovery_hours_per_month),
        burden_per_hour=to_fraction(sr.recovery_burden_per_hour),
        basis=(
            f"{label}的恢复：本人每月额外 {sr.recovery_hours_per_month:g} 小时 × 每小时负担 "
            f"{sr.recovery_burden_per_hour:g}（全组共用的合成规则）"
        ),
        task=f"recovery_{severity}",
    )


def treatment_load(offer: TreatmentOffer) -> ExtraLoad:
    assert offer.visit_hours is not None and offer.burden_per_hour is not None
    return ExtraLoad(
        category="treatment",
        hours=to_fraction(offer.visit_hours),
        burden_per_hour=to_fraction(offer.burden_per_hour),
        basis=(
            f"就医：{offer.label} {offer.visit_hours:g} 小时 × 每小时负担 {offer.burden_per_hour:g}"
            "（本城治疗目录）"
        ),
        opportunity_id=offer.treatment_id,
    )


def transition(rules: HealthRules, episode: IllnessEpisode | None, treated: bool) -> str:
    """月末转移：健康 → 保持健康；治疗过 → 当月月末恢复；到了不治疗的病程月数 → 自然恢复；否则继续生病。"""
    if episode is None:
        return "stays_healthy"
    if treated:
        return "recovered_treated"
    if episode.months_ill >= rules.severity_rules(episode.severity).untreated_months:
        return "recovered_natural"
    return "continues"


def _revised(episode: IllnessEpisode, **changes: object) -> IllnessEpisode:
    data = episode.model_dump(mode="python")
    data.update(changes)
    return IllnessEpisode.model_validate(data)


# ==========================================================================
# 一年的逐月记录器
# ==========================================================================


@dataclass(frozen=True)
class MonthStart:
    """月初定下的这个月的健康状态（一旦定下就算发生了，试算失败也不重抽）。"""

    month: int
    state: str  # healthy / minor / major
    episode: IllnessEpisode | None
    onset: str | None  # 这个月新发的病情
    onset_source: str | None
    injected_skipped: bool
    draw: RiskDraw | None


class HealthYearTracker:
    """一个分身一年的健康记录（E1-5C）。由结算按月初 → 月末（或出局、停止）→ 年末的顺序驱动。

    ``new_events``：是否发生新事件。年初的无事件试算（判定计划本身可行不可行）里为 False——年初已在延续的疾病照常
    按规则走，指定事件与随机抽签都不发生。
    """

    def __init__(
        self,
        *,
        rules: HealthRules,
        source: IllnessEventSource,
        state: PersonYearState,
        new_events: bool,
    ) -> None:
        h = state.health
        if h is None:
            raise LifeContractError(f"{state.clone_id}: 状态没有健康状态，不能按疾病规则结算")
        self.rules = rules
        self.source = source
        self.new_events = new_events
        self.person_id = state.person_id
        self.year = state.year_index
        self.age = h.age_years
        self.repeat_index = h.repeat_index
        self.episode: IllnessEpisode | None = h.episode
        self.history: list[IllnessEpisode] = list(h.history)
        self.last_burden: Fraction | None = (
            None if h.last_work_relative_burden is None else h.last_work_relative_burden.fraction()
        )
        self.last_from: tuple[int, int] | None = (
            None
            if h.last_burden_year is None or h.last_burden_month is None
            else (h.last_burden_year, h.last_burden_month)
        )
        self.months: list[HealthMonth] = []
        self.notes: list[str] = []
        self.started: MonthStart | None = None
        self.new_event_months: list[int] = []

    # ------------------------------------------------------------------ 月初

    def month_start(self, m: int) -> MonthStart:
        """定下第 m 月的状态。延续中的疾病多病一个月；健康月份才看新事件。"""
        inj = (
            self.source.injected_for(self.person_id, self.repeat_index, self.year, m)
            if self.new_events and self.source.mode == "injected"
            else None
        )
        if self.episode is not None:
            ep = _revised(self.episode, months_ill=self.episode.months_ill + 1)
            if ep.months_ill > self.rules.severity_rules(ep.severity).untreated_months:
                raise LifeContractError(f"{ep.episode_id}: 病程超过声明的月数——上个月末应已恢复")
            self.episode = ep
            start = MonthStart(m, ep.severity, ep, None, None, inj is not None, None)
            self.started = start
            return start
        draw: RiskDraw | None = None
        onset: str | None = None
        source: str | None = None
        if self.new_events and inj is not None:
            onset, source = inj.severity, "injected"
        elif self.new_events and self.source.mode == "random":
            draw = draw_onset(
                self.rules,
                self.source,
                person_id=self.person_id,
                repeat_index=self.repeat_index,
                age=self.age,
                year=self.year,
                month=m,
                last_burden=self.last_burden,
                last_from=self.last_from,
            )
            if draw.result != "none":
                onset, source = draw.result, "random"
        if onset is None:
            start = MonthStart(m, "healthy", None, None, None, False, draw)
        else:
            self.episode = IllnessEpisode(
                episode_id=f"ill-y{self.year}m{m:02d}",
                severity=onset,  # type: ignore[arg-type]
                source=source,  # type: ignore[arg-type]
                onset_year=self.year,
                onset_month=m,
                months_ill=1,
            )
            self.new_event_months.append(m)
            start = MonthStart(m, onset, self.episode, onset, source, False, draw)
        self.started = start
        return start

    def _head(self, start: MonthStart) -> dict[str, object]:
        ep = start.episode
        return dict(
            month=start.month,
            state=start.state,
            onset=start.onset,
            onset_source=start.onset_source,
            injected_skipped=start.injected_skipped,
            draw=start.draw,
            episode_id=None if ep is None else ep.episode_id,
            episode_month=None if ep is None else ep.months_ill,
        )

    # ------------------------------------------------------------------ 月末

    def month_end(
        self,
        start: MonthStart,
        *,
        sick_leave: bool,
        paused: tuple[PausedActivity, ...],
        treatment: TreatmentOutcome | None,
        treated: TreatmentRecord | None,
        recovery: ExtraLoad | None,
        work_relative_burden: Fraction,
        notes: tuple[str, ...] = (),
    ) -> HealthMonth:
        """一个过完的月份：记下事实，按规则转移，更新"上一个完整月份"的工作相对负担。"""
        kind = transition(self.rules, self.episode, treated is not None)
        record = HealthMonth(
            **self._head(start),  # type: ignore[arg-type]
            record="lived",
            sick_leave=sick_leave,
            paused=paused,
            treatment=treatment,
            recovery_hours=None if recovery is None else ExactAmount.of(recovery.hours),
            recovery_burden=None
            if recovery is None
            else ExactAmount.of(recovery.hours * recovery.burden_per_hour),
            transition=kind,  # type: ignore[arg-type]
            notes=notes,
        )
        self.months.append(record)
        if kind in ("recovered_treated", "recovered_natural"):
            assert self.episode is not None
            ended = _revised(
                self.episode,
                treatment=treated,
                ended=True,
                recovered_year=self.year,
                recovered_month=start.month,
                recovery="treated" if treated is not None else "natural",
            )
            self.history.append(ended)
            self.episode = None
        self.last_burden = work_relative_burden
        self.last_from = (self.year, start.month)
        return record

    def exit_month(
        self,
        start: MonthStart,
        *,
        sick_leave: bool,
        paused: tuple[PausedActivity, ...],
        notes: tuple[str, ...],
    ) -> None:
        """出局当月：不治疗、不计恢复负担、不转移；疾病原样带进出局状态。出局之前已经发生的暂停（第 2 步的岗位病假）
        照实记下，与岗位的执行记录一致；之后的步骤没有执行，没有别的暂停。"""
        self.months.append(
            HealthMonth(
                **self._head(start),  # type: ignore[arg-type]
                record="exit_month",
                sick_leave=sick_leave,
                paused=paused,
                transition="not_applicable",
                notes=notes,
            )
        )

    def blocked(
        self, start: MonthStart, *, treatment: TreatmentOutcome | None, notes: tuple[str, ...]
    ) -> None:
        """停止月：月初的事件已经发生、保留；这个月的执行没有提交，所以没有病假、暂停或付款的事实。"""
        self.months.append(
            HealthMonth(
                **self._head(start),  # type: ignore[arg-type]
                record="blocked",
                treatment=treatment,
                transition="not_applicable",
                notes=notes,
            )
        )

    # ------------------------------------------------------------------ 年末

    def binding(self) -> HealthBinding:
        return HealthBinding(
            rules_version=self.rules.version,
            rules_fingerprint=self.rules.content_fingerprint,
            source_fingerprint=self.source.content_fingerprint,
        )

    def finish(
        self, next_status: str, *, cancel_declared: tuple[str, ...] = ()
    ) -> tuple[HealthYear, HealthState]:
        """这一年的健康事实，与下一个状态的健康部分。下一年（含终态）年龄加一；出局与停止状态年龄不变。

        ``cancel_declared``（收尾修订）：这一年的年度计划声明"生病月份取消"的购买，原样记下（下一年的观察据此显示自己的
        声明）。
        """
        year = HealthYear(
            rules_version=self.rules.version,
            rules_fingerprint=self.rules.content_fingerprint,
            source_mode=self.source.mode,
            source_fingerprint=self.source.content_fingerprint,
            age_years=self.age,
            repeat_index=self.repeat_index,
            months=tuple(self.months),
            notes=tuple(n[:280] for n in self.notes),
            cancel_purchases_when_ill=cancel_declared,
        )
        older = next_status in ("active", "terminal")
        state = HealthState(
            age_years=self.age + 1 if older else self.age,
            repeat_index=self.repeat_index,
            episode=self.episode,
            history=tuple(self.history),
            last_work_relative_burden=None
            if self.last_burden is None
            else ExactAmount.of(self.last_burden),
            last_burden_year=None if self.last_from is None else self.last_from[0],
            last_burden_month=None if self.last_from is None else self.last_from[1],
            binding=self.binding(),
        )
        return year, state
