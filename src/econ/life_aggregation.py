"""E1-6B 城市汇总（`DECISIONS/0029`）：冻结名单 → 核对并分类每个名单项 → 城市报告、城市比较与评价对照。

    完整评价分身的平均分 = Σ_{i∈C} J_i ÷ C（同一批 C 的 L、V 平均一起给）
    出局比例 = E ÷ N，缺失比例 = M ÷ N（N = 原计划人数 = C + E + M）
    模拟缺失范围：每个 M 项
        下界_i = (K + W_future × v_low) ÷ (W_actual + W_future)
        上界_i = (K + W_actual − W_known + W_future × v_high) ÷ (W_actual + W_future)
      K 是可信的已知实际月份的加权分数和、W_known 是它们的权重和；V 完整时 v_low = v_high = V，否则 0 与 1；完整 J 的项取
      [J, J]；城市范围 = Σ 下界_i ÷ (C + M) 到 Σ 上界_i ÷ (C + M)；确认出局者不进范围

没有新的生活数学：J、L、V、逐月贡献与时间权重都原样取自 E1-6A 的单分身评价（``econ.life_evaluation``），全程精确分数；显示
值四舍五入到 6 位另存，不参与判断。范围不是置信区间，不是抽样误差，不是疾病概率，也不是全体真实人生的均值区间；它不要求
缺失随机，不补零，不插补，不重加权，不做 bootstrap。

**2026-10-12 收尾（审核 R1–R3，执行修订 ``e1-6b-r2``）**：冻结前的名单检查（``ledger.life_roster.roster_problems``）加了标准开局 v1
与每个运行臂的完整跨城评价配置检查，冻结名单与报告记下通过的检查版本；硬要求汇总写明核验人数与未核验人数；敏感性对照先按报告里的
评价指纹把传入的证据一一配对，再比较封存的生活绑定，证据不配套与生活不同分开报告。

**只接受核对过的评价结果**：结果指纹重算（不信 ``model_copy`` 改过的对象），身份、运行臂（协议、评价内容、执行修订、δ、H、N）、
城市目录与税、评分输入、第 1 年开局都对照冻结名单；对不上的那一项记为输入 / 配置 / 程序错误（M），不提取其中任何"已知
月份"去收窄范围。名单外的结果单列拒收；名单自身的冲突在冻结时就拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction

from common.life_aggregation_contracts import (
    AGGREGATION_REVISION,
    MISSING_REASON_TEXT,
    PLANNING_STOP_REASON,
    START_CONDITION_RULE,
    ArmReport,
    ChainStep,
    CityComparison,
    CityReport,
    EntryRecord,
    FrozenArm,
    FrozenCity,
    FrozenEntry,
    FrozenRoster,
    HardRequirementSummary,
    ReasonCount,
    RejectedSubmission,
    RosterConfig,
    RunFailure,
    SensitivityChange,
    SensitivityReport,
)
from common.life_contracts import (
    MONTHS_PER_YEAR,
    ExactAmount,
    LifeContractError,
    canonical_fingerprint,
    model_fingerprint,
)
from common.life_evaluation_contracts import PLANNING_STOP_TEXT, CloneEvaluation, EvaluationSpec
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, scoring_inputs_fingerprint, tax_binding_fingerprint
from ledger.life_catalog import LifeBundle
from ledger.life_roster import common_start_digest, roster_problems
from ledger.life_scoring_config import ScoringConfig

#: M 的原因在报告里的排列顺序（与契约的 ``MissingReason`` 一致）。
_REASON_ORDER = tuple(MISSING_REASON_TEXT)

ASSUMPTIONS: tuple[str, ...] = (
    "完整评价分身的平均分只用实际生活完成、实际评分完整、期末估值完整、J 合法的分身；它不代表原计划全部分身，也不代表"
    "全部可能人生",
    "确认的生活出局（付不起最低住房与食品）不进均值、不进缺失范围；出局比例与缺失比例都除以原计划人数",
    "模拟缺失范围给尚不能计算的分身保留 [下界, 上界]：已知的实际月份照算，未知的实际月份与不完整的估值取 0 与 1 两端；"
    "不是置信区间、不是抽样误差、不是疾病概率，也不预测没跑完的分身继续运行后一定能完成",
    "J、L、V 来自 E1-6A 的单分身评价：有限的基础生活模板、估值期没有新发疾病、价格冻结、不续学课程、估值只看 12 个月；"
    "交通—健康反例（只看重交通的人，大病月分数可能高于上班月）原样保留",
    "全部数字来自合成样例：不是现实城市评价，也不排序真实城市",
    "城市比较只接受标准开局 v1：同一用户的每个分身都从第 1 年、还没有任何生活记录的开局出发（没有开局持有物、培训进度、资金目标、"
    "病情与病史），个人条件逐字段一致；用户已有的家具、病史、在学课程等还不能导入，不能当成已经模拟过",
)


def _ex(x: Fraction) -> ExactAmount:
    return ExactAmount.of(x)


def _disp(x: Fraction | None) -> float | None:
    return None if x is None else round(float(x), 6)


def _weights(spec: EvaluationSpec, years: int) -> tuple[Fraction, Fraction]:
    """W_actual = Σ_{m=1..T} δ^(m−1)，W_future = Σ_{k=1..H} δ^(T+k−1)（T = 12N）。与 E1-6A 的算法相同。"""
    d = spec.discount_factor
    t, h = MONTHS_PER_YEAR * years, spec.valuation_horizon_months
    return (
        sum((d**k for k in range(t)), Fraction(0)),
        sum((d ** (t + k) for k in range(h)), Fraction(0)),
    )


# ---------------------------------------------------------------------- 冻结名单


def freeze_roster(
    config: RosterConfig,
    bundle: LifeBundle,
    scoring: ScoringConfig,
    specs: Mapping[str, EvaluationSpec],
) -> FrozenRoster:
    """看结果之前冻结原计划名单：先做 ledger 的跨对象检查，再记下每个运行臂、每座城、每个名单项的内容身份。"""
    problems = roster_problems(config, bundle, scoring, dict(specs))
    if problems:
        raise LifeContractError("名单检查失败：\n  " + "\n  ".join(problems))
    if not config.entries:
        raise LifeContractError(
            "原计划名单为空：没有计划样本，不能汇总（不除零，不返回空报告冒充结果）"
        )
    protocol = bundle.protocol
    arms = []
    for a in config.arms:
        spec = specs[a.arm_id]
        w_actual, w_future = _weights(spec, protocol.simulation_years)
        arms.append(
            FrozenArm(
                arm_id=a.arm_id,
                role=a.role,
                evaluation_spec_id=spec.spec_id,
                evaluation_spec_content_fingerprint=spec.content_fingerprint,
                execution_revision=spec.execution_revision,
                discount_factor=str(Decimal(spec.monthly_discount_factor).normalize()),
                horizon_months=spec.valuation_horizon_months,
                protocol_id=protocol.protocol_id,
                protocol_fingerprint=model_fingerprint(protocol),
                simulation_years=protocol.simulation_years,
                scoring_content_fingerprint=scoring.spec.content_fingerprint,
                weight_actual=_ex(w_actual),
                weight_future=_ex(w_future),
            )
        )
    cities = []
    for city in dict.fromkeys(str(e.city) for e in config.entries):
        catalog = bundle.catalog_for(city)
        assert catalog is not None  # roster_problems 已核对
        policy = bundle.tax_policy(catalog.tax_policy_id)
        if policy is None:
            raise LifeContractError(f"{city}: 目录引用的税策略 {catalog.tax_policy_id} 不存在")
        cities.append(
            FrozenCity(
                city=catalog.city,
                catalog_fingerprint=model_fingerprint(catalog),
                tax_fingerprint=tax_binding_fingerprint(binding_from_policy(policy)),
            )
        )
    users = {u.user_id: u for u in config.users}
    entries = []
    for e in config.entries:
        state = bundle.initial_state(e.clone_id)
        assert state is not None
        # E3-A：旧七维陈述或六维产品画像（roster_problems 已核对类型）；评分输入身份照旧由 scoring_inputs_fingerprint 记下
        profile = bundle.profile_for(users[e.user_id].person_id)
        assert profile is not None
        conditions = scoring.conditions_for(str(e.city))
        if conditions is None:
            raise LifeContractError(f"{e.clone_id}: 评分配置没有 {e.city} 的城市评分条件")
        entries.append(
            FrozenEntry(
                user_id=e.user_id,
                person_id=users[e.user_id].person_id,
                clone_id=e.clone_id,
                city=e.city,
                repeat_index=e.repeat_index,
                start_fingerprint=model_fingerprint(state),
                common_start_fingerprint=canonical_fingerprint(common_start_digest(state)),
                scoring_inputs_fingerprint=scoring_inputs_fingerprint(
                    ScoringInputs(
                        spec=scoring.spec,
                        conditions=conditions,
                        preference_input=profile,
                        hard_requirements=profile.hard_requirements,
                    )
                ),
                hard_requirement_needs=tuple(sorted({h.need for h in profile.hard_requirements})),
            )
        )
    return FrozenRoster(
        roster_id=config.roster_id,
        status=config.status,
        aggregation_version=config.aggregation_version,
        classification_rule="c_e_m_owner_2026_10_12_v1",
        range_rule="known_months_plus_unit_bounds_v1",
        start_condition_rule=START_CONDITION_RULE,  # roster_problems 已按这一版检查过
        aggregation_revision=AGGREGATION_REVISION,
        comparison_group_id=config.comparison_group_id,
        users=config.users,
        arms=tuple(arms),
        cities=tuple(cities),
        entries=tuple(entries),
        note=config.note,
    )


# ---------------------------------------------------------------------- 核对与分类


def _verification_problems(
    entry: FrozenEntry, arm: FrozenArm, city: FrozenCity, ev: CloneEvaluation
) -> list[str]:
    out: list[str] = []
    if ev.result_fingerprint != ev.expected_result_fingerprint():
        out.append("结果内容与它记下的指纹不一致（可能被改写过）")
    ident = (ev.person_id, ev.clone_id, str(ev.city))
    want = (entry.person_id, entry.clone_id, str(entry.city))
    if ident != want:
        out.append(f"身份 {ident} 不是名单项 {want}")
    # 重复分身序号取自封存年初状态的健康记录；没有健康规则的协议没有记下它（为空），无从核对，只比人、分身、城市
    if ev.repeat_index is not None and ev.repeat_index != entry.repeat_index:
        out.append(f"重复分身序号 {ev.repeat_index} 不是名单项的 {entry.repeat_index}")
    if (ev.spec_id, ev.spec_content_fingerprint, ev.execution_revision) != (
        arm.evaluation_spec_id,
        arm.evaluation_spec_content_fingerprint,
        arm.execution_revision,
    ):
        out.append(f"评价规格（{ev.spec_id}）不是运行臂 {arm.arm_id} 冻结的那一份")
    if ev.protocol_fingerprint != arm.protocol_fingerprint:
        out.append("实际生活用的协议不是冻结名单的那一份")
    if (ev.catalog_fingerprint, ev.tax_fingerprint) != (
        city.catalog_fingerprint,
        city.tax_fingerprint,
    ):
        out.append(f"城市目录或税绑定不是 {city.city} 冻结的那一份")
    if ev.scoring_inputs_fingerprint != entry.scoring_inputs_fingerprint:
        out.append("评分输入（规格、城市评分条件、偏好、硬要求）不是名单项冻结的那一份")
    first = ev.trajectory.years[0] if ev.trajectory.years else None
    if first is None or first.start_fingerprint != entry.start_fingerprint:
        out.append("实际生活不是从名单项冻结的第 1 年开局出发的")
    if (
        ev.actual.months_total != MONTHS_PER_YEAR * arm.simulation_years
        or ev.actual.weight_total.fraction() != arm.weight_actual.fraction()
        or ev.combined.weight_future.fraction() != arm.weight_future.fraction()
        or ev.valuation.horizon_months != arm.horizon_months
    ):
        out.append("时间权重、实际月数或估值期长度与运行臂不一致（不同 δ、H 或 N 不能静默平均）")
    return out


def missing_range_bounds(
    k: Fraction, w_known: Fraction, w_actual: Fraction, w_future: Fraction, v: Fraction | None
) -> tuple[Fraction, Fraction, Fraction, Fraction]:
    """一个 M 项的范围：返回（v_low, v_high, 下界, 上界）。0 ≤ 下界 ≤ 上界 ≤ 1 由契约检查，矛盾就报错，不裁剪。"""
    v_low, v_high = (v, v) if v is not None else (Fraction(0), Fraction(1))
    total = w_actual + w_future
    lower = (k + w_future * v_low) / total
    upper = (k + (w_actual - w_known) + w_future * v_high) / total
    return v_low, v_high, lower, upper


def _identity(entry: FrozenEntry) -> dict[str, object]:
    return {
        "user_id": entry.user_id,
        "person_id": entry.person_id,
        "clone_id": entry.clone_id,
        "city": entry.city,
        "repeat_index": entry.repeat_index,
    }


def _untrusted(
    entry: FrozenEntry,
    arm: FrozenArm,
    reason: str,
    detail: str,
    *,
    unexpected: bool = False,
    trajectory_status: str | None = None,
) -> EntryRecord:
    """没有可信记录的 M 项：K = W_known = 0，V 取 0 与 1，范围 [0, 1]。"""
    w_actual, w_future = arm.weight_actual.fraction(), arm.weight_future.fraction()
    v_low, v_high, lower, upper = missing_range_bounds(
        Fraction(0), Fraction(0), w_actual, w_future, None
    )
    return EntryRecord(
        **_identity(entry),  # type: ignore[arg-type]
        category="M",
        missing_reason=reason,  # type: ignore[arg-type]
        detail=detail[:600],
        trajectory_status=trajectory_status,
        known_weighted_sum=_ex(Fraction(0)),
        known_weight=_ex(Fraction(0)),
        weight_actual=arm.weight_actual,
        weight_future=arm.weight_future,
        v_low=_ex(v_low),
        v_high=_ex(v_high),
        lower=_ex(lower),
        upper=_ex(upper),
        unexpected_error=unexpected,
    )


def _missing_reason(ev: CloneEvaluation) -> tuple[str, str]:
    """一个核对过、但不是 C 也不是 E 的结果属于哪一类 M（按最早失败的环节）。"""
    t = ev.trajectory
    if t.status == "stopped":
        kind = None if t.stop is None else t.stop.kind
        what = {"data_gap": "资料缺口", "emergency_insufficient": "应急安排不足"}.get(
            kind or "", "停止"
        )
        return "stopped", f"E1-5C 停止（{what}）：模拟器的支持边界，原样保留，不改称生活出局"
    if t.status == "feedback_blocked":
        return (
            "feedback_blocked",
            "反馈开着而上一年评分不完整，下一年的精力额度无法确定：模拟问题，不解释为生活差",
        )
    if t.status == "settlement_failed":
        last = t.years[-1]
        codes = set(last.problem_codes)
        if last.settlement_status == "data_error":
            return (
                "data_missing",
                f"第 {last.year_index} 年结算要用的资料缺失或不完整（{sorted(codes)}）",
            )
        if last.settlement_status == "plan_infeasible" or (
            last.settlement_status == "input_error" and codes and codes <= {"qualification_missing"}
        ):
            return (
                "plan_or_execution",
                f"第 {last.year_index} 年的计划没有执行（{last.settlement_status}，{sorted(codes)}）：计划问题，可修改重试",
            )
        return (
            "input_config_program_error",
            f"第 {last.year_index} 年输入错误（{last.settlement_status}，{sorted(codes)}）",
        )
    if t.status == "not_finished":
        s = t.planning_stop
        if s is not None:  # E1-7A：下一年的计划没有被接受——原因细分，已提交的月份照常进入已知部分
            text = (
                f"第 {s.year_index} 年的计划没有被接受（{PLANNING_STOP_TEXT[s.kind]}，用了 {s.attempts} 次规划调用）："
                f"{s.detail}——此前提交的第 1–{t.years[-1].year_index} 年照常算作已知月份"
            )
            return PLANNING_STOP_REASON[s.kind], text[:600]
        return "not_run", f"只返回到第 {t.years[-1].year_index} 年：之后的年份没有返回"
    if t.status == "score_incomplete":
        return "score_incomplete", "终态正常，但有月份有正权重的资料未知，实际评分不完整：不补零"
    v = ev.valuation
    why = v.reasons[0] if v.reasons else v.status
    known = ""
    if v.best_known is not None and v.best_known_plan_id is not None:
        known = (
            f"（已知最好结果 {v.best_known_plan_id} = {float(v.best_known.fraction()):.6f} 只是诊断，"
            "不当作 V，也不用来收窄范围）"
        )
    text = f"实际生活完整，期末估值不完整（{v.status}）{known}：{why}"
    return "valuation_incomplete", text[:600]


def _money(m: object) -> str:
    amount = getattr(m, "amount", None)
    return "—" if amount is None else f"{amount:,.0f}"


def event_chain(ev: CloneEvaluation) -> tuple[tuple[ChainStep, ...], tuple[str, ...]]:
    """出局与停止的中文事实链（只取已核对的逐月事实、事件与出局 / 停止记录；带年月与金额），以及不互斥的事件标签。
    只写事实发生的顺序，不宣称某项开支贡献了多少因果影响；资料未知不写成"当地没有"，停止不写成"看不起病"。"""
    t = ev.trajectory
    tags: list[str] = []
    if any(f.sick_leave for f in t.month_facts):
        tags.append("出现过大病病假")
    if any(f.health_state == "minor" for f in t.month_facts):
        tags.append("出现过小病")
    if any(f.treatment_paid is not None for f in t.month_facts):
        tags.append("记录到治疗费用")
    if t.exit is not None:
        tags.append("最低住房与食品付不起")
    if t.stop is not None:
        tags.append("资料缺口停止" if t.stop.kind == "data_gap" else "应急安排不足停止")
    end = t.exit or t.stop
    if end is None:
        return (), tuple(tags)
    end_index = (end.year_index - 1) * MONTHS_PER_YEAR + end.month
    onsets = [
        (e.year_index - 1) * MONTHS_PER_YEAR + e.month
        for e in t.events
        if e.event_kind == "illness_onset"
        and 0 <= end_index - ((e.year_index - 1) * MONTHS_PER_YEAR + e.month) < MONTHS_PER_YEAR
    ]
    start_index = min([end_index - 2, *onsets])
    source_text = {"injected": "实验指定的事件", "random": "按风险表抽中"}
    keyed: list[
        tuple[int, int, ChainStep]
    ] = []  # （月序号, 同月里的先后, 步骤）：发病在前，当月账目在后
    for e in t.events:
        index = (e.year_index - 1) * MONTHS_PER_YEAR + e.month
        if e.event_kind != "illness_onset" or not start_index <= index <= end_index:
            continue
        detail = e.detail
        for raw, text in source_text.items():
            detail = detail.replace(f"（{raw}）", f"（{text}）")
        keyed.append(
            (
                index,
                0,
                ChainStep(
                    year_index=e.year_index,
                    month=e.month,
                    text=f"第 {e.year_index} 年第 {e.month} 月月初{detail}",
                    source="event",
                ),
            )
        )
    for f in t.month_facts:
        index = (f.year_index - 1) * MONTHS_PER_YEAR + f.month
        if not start_index <= index < end_index or f.minimum_check == "failed":
            continue
        parts = [f"月初现金 {_money(f.cash_start)}"]
        parts.append(
            f"收入 {_money(f.income_gross)}" + ("（大病病假，没有工资）" if f.sick_leave else "")
        )
        parts.append(f"付出 {_money(f.spent)}")
        if f.treatment_status == "treated":
            parts.append(f"其中治疗 {_money(f.treatment_paid)}")
        elif f.treatment_status == "unaffordable":
            parts.append("要治疗，但付完住房与食品后现金不够，没有治疗")
        elif f.treatment_status == "declined":
            parts.append("按年度计划不治疗")
        elif f.treatment_status == "unavailable":
            parts.append("本城确定没有这种治疗")
        parts.append(f"月末现金 {_money(f.cash_end)}")
        state = {"major": "大病", "minor": "小病"}.get(f.health_state or "", "")
        keyed.append(
            (
                index,
                1,
                ChainStep(
                    year_index=f.year_index,
                    month=f.month,
                    text=f"第 {f.year_index} 年第 {f.month} 月"
                    + (f"（{state}）" if state else "")
                    + "："
                    + "，".join(parts),
                    source="month_fact",
                ),
            )
        )
    steps = [s for _, _, s in sorted(keyed, key=lambda k: (k[0], k[1]))]
    if t.exit is not None:
        x = t.exit
        need = x.minimum_housing_cost.amount + x.minimum_food_cost.amount
        steps.append(
            ChainStep(
                year_index=x.year_index,
                month=x.month,
                text=(
                    f"第 {x.year_index} 年第 {x.month} 月：可用现金 {_money(x.cash_available)}，最低住房 "
                    f"{_money(x.minimum_housing_cost)} + 最低食品 {_money(x.minimum_food_cost)} = {need:,.0f}，"
                    f"差 {_money(x.shortfall)} → 付不起最低生活，出局"
                ),
                source="exit_record",
            )
        )
    if t.stop is not None:
        s = t.stop
        what = "资料缺口" if s.kind == "data_gap" else "应急安排不足"
        steps.append(
            ChainStep(
                year_index=s.year_index,
                month=s.month,
                text=f"第 {s.year_index} 年第 {s.month} 月停止（{what}）：{s.reason}"[:300],
                source="stop_record",
            )
        )
    return tuple(steps), tuple(tags)


def _from_evaluation(
    entry: FrozenEntry, arm: FrozenArm, city: FrozenCity, ev: CloneEvaluation
) -> EntryRecord:
    problems = _verification_problems(entry, arm, city, ev)
    if problems:
        return _untrusted(
            entry,
            arm,
            "input_config_program_error",
            "结果核对不通过，不提取其中任何月份：" + "；".join(problems),
        )
    t, a, v, c = ev.trajectory, ev.actual, ev.valuation, ev.combined
    chain, tags = event_chain(ev)
    base: dict[str, object] = {
        **_identity(entry),
        "trajectory_status": t.status,
        "evaluation_fingerprint": ev.result_fingerprint,
        "selected_plan_id": v.selected_plan_id,
        "known_weighted_sum": a.known_weighted_sum,
        "known_weight": a.known_weight,
        "weight_actual": arm.weight_actual,
        "weight_future": arm.weight_future,
        "hard_requirements": a.hard_requirements,
        "event_chain": chain,
        "event_tags": tags,
    }
    if (
        t.status == "life_exit"
        and t.exit is not None
        and t.exit.reason == "minimum_living_unaffordable"
    ):
        return EntryRecord(
            **base,  # type: ignore[arg-type]
            category="E",
            detail=(
                f"第 {t.exit.year_index} 年第 {t.exit.month} 月付不起最低住房与食品（差 {_money(t.exit.shortfall)}）："
                "确认的生活出局，不进均值、不进缺失范围，单独报告"
            ),
        )
    if (
        t.status == "complete"
        and a.status == "complete"
        and v.status == "complete"
        and c.status == "complete"
        and c.j is not None
    ):
        return EntryRecord(
            **base,  # type: ignore[arg-type]
            category="C",
            detail=f"完整 J（选中的参考分支 {v.selected_plan_id}）",
            j=c.j,
            l_actual=a.l_actual,
            v_reference=v.v_reference,
            lower=c.j,
            upper=c.j,
        )
    reason, detail = _missing_reason(ev)
    vv = v.v_reference.fraction() if v.status == "complete" and v.v_reference is not None else None
    v_low, v_high, lower, upper = missing_range_bounds(
        a.known_weighted_sum.fraction(),
        a.known_weight.fraction(),
        arm.weight_actual.fraction(),
        arm.weight_future.fraction(),
        vv,
    )
    return EntryRecord(
        **base,  # type: ignore[arg-type]
        category="M",
        missing_reason=reason,  # type: ignore[arg-type]
        detail=detail,
        l_actual=a.l_actual,
        v_reference=v.v_reference if vv is not None else None,
        v_low=_ex(v_low),
        v_high=_ex(v_high),
        lower=_ex(lower),
        upper=_ex(upper),
    )


def classify_arm(
    roster: FrozenRoster,
    arm_id: str,
    evaluations: Sequence[CloneEvaluation],
    failures: Sequence[RunFailure] = (),
) -> tuple[tuple[EntryRecord, ...], tuple[RejectedSubmission, ...]]:
    """每个原计划名单项恰好一条记录（按名单顺序；与结果的提交顺序无关）；名单外的结果单列拒收。"""
    arm = roster.arm(arm_id)
    if arm is None:
        raise LifeContractError(f"冻结名单里没有运行臂 {arm_id}")
    planned = {e.clone_id for e in roster.entries}
    evs: dict[str, list[CloneEvaluation]] = {}
    fails: dict[str, list[RunFailure]] = {}
    rejected: list[RejectedSubmission] = []
    for ev in evaluations:
        if type(ev) is not CloneEvaluation:
            raise LifeContractError(f"汇总只接受 CloneEvaluation，得到 {type(ev).__name__}")
        if ev.clone_id in planned:
            evs.setdefault(ev.clone_id, []).append(ev)
        else:
            rejected.append(
                RejectedSubmission(
                    clone_id=ev.clone_id[:64],
                    reason="不在原计划名单上：不计入任何城市、任何分母",
                )
            )
    for f in failures:
        if type(f) is not RunFailure:
            raise LifeContractError(f"运行失败记录必须是 RunFailure，得到 {type(f).__name__}")
        if f.clone_id in planned:
            fails.setdefault(f.clone_id, []).append(f)
        else:
            rejected.append(
                RejectedSubmission(
                    clone_id=f.clone_id, reason="名单外的运行失败记录：不计入任何城市"
                )
            )
    records = []
    for entry in roster.entries:
        city = roster.city(str(entry.city))
        assert city is not None  # 冻结名单的校验保证
        got_ev, got_fail = evs.get(entry.clone_id, []), fails.get(entry.clone_id, [])
        if len(got_ev) + len(got_fail) > 1:
            records.append(
                _untrusted(
                    entry,
                    arm,
                    "input_config_program_error",
                    f"这个名单项收到 {len(got_ev)} 份评价结果、{len(got_fail)} 条失败记录：不静默选其中一份",
                )
            )
        elif got_ev:
            records.append(_from_evaluation(entry, arm, city, got_ev[0]))
        elif got_fail and got_fail[0].planning_stop is not None:
            s = got_fail[0].planning_stop  # E1-7A：第 1 年的计划就没有被接受，没有任何实际生活
            records.append(
                _untrusted(
                    entry,
                    arm,
                    PLANNING_STOP_REASON[s.kind],
                    f"第 1 年的计划没有被接受（{PLANNING_STOP_TEXT[s.kind]}，用了 {s.attempts} 次规划调用）："
                    f"{s.detail}——没有任何实际生活",
                )
            )
        elif got_fail:
            f = got_fail[0]
            stage = {"life": "实际生活", "evaluation": "评价", "planning": "规划"}[f.stage]
            kind = (
                "意外程序错误（不假定只影响这个分身，先查原因）"
                if f.error_kind == "unexpected"
                else "输入或配置被拒绝"
            )
            records.append(
                _untrusted(
                    entry,
                    arm,
                    "input_config_program_error",
                    f"{stage}阶段{kind}：{f.message}",
                    unexpected=f.error_kind == "unexpected",
                )
            )
        else:
            records.append(
                _untrusted(entry, arm, "not_run", "名单上有、没有收到任何结果（未运行或未返回）")
            )
    return tuple(records), tuple(sorted(rejected, key=lambda r: (r.clone_id, r.reason)))


# ---------------------------------------------------------------------- 城市报告与比较


def _hard_summaries(
    records: Sequence[EntryRecord], needs: Sequence[str] = ()
) -> tuple[HardRequirementSummary, ...]:
    """每条硬要求一份汇总：用户声明的（冻结名单记下的）与记录里出现的都列出来。分母是这座城的原计划人数；有可信记录的名单项
    算"核验"，按四类计数；没有可信记录的算"未核验"（不是"核验过、没问题"）。"""
    by_need: dict[str, dict[str, list[str]]] = {n: {} for n in needs}
    for r in records:
        for h in r.hard_requirements:
            by_need.setdefault(h.need, {}).setdefault(h.requirement_summary, []).append(r.clone_id)
    out = []
    for need, by in sorted(by_need.items()):
        checked = {c for ids in by.values() for c in ids}
        not_checked = [r.clone_id for r in records if r.clone_id not in checked]
        out.append(
            HardRequirementSummary(
                need=need,
                planned=len(records),
                checked=len(checked),
                satisfied_every_month=len(by.get("satisfied_every_month", [])),
                violated=len(by.get("violated", [])),
                unknown=len(by.get("unknown", [])),
                satisfied_in_lived_months=len(by.get("satisfied_in_lived_months", [])),
                not_checked=len(not_checked),
                violated_clone_ids=tuple(by.get("violated", [])),
                unknown_clone_ids=tuple(by.get("unknown", [])),
                not_checked_clone_ids=tuple(not_checked),
            )
        )
    return tuple(out)


def city_report(
    arm_id: str,
    user_id: str,
    person_id: str,
    records: Sequence[EntryRecord],
    hard_requirement_needs: Sequence[str] = (),
) -> CityReport:
    """一个用户在一座城的报告（纯算术：只读名单项记录）。名单项为空时拒绝（没有计划样本，不除零）。
    ``hard_requirement_needs`` 是用户声明的硬要求（冻结名单记下的），没有可信记录的名单项在硬要求汇总里算"未核验"。"""
    if not records:
        raise LifeContractError("这座城没有原计划名单项：没有计划样本，不能出报告（不除零）")
    city = records[0].city
    if any((r.user_id, str(r.city)) != (user_id, str(city)) for r in records):
        raise LifeContractError("一份城市报告只能放同一用户、同一城的名单项")
    records = sorted(records, key=lambda r: (r.repeat_index, r.clone_id))
    n = len(records)
    comp = [r for r in records if r.category == "C"]
    exits = [r for r in records if r.category == "E"]
    miss = [r for r in records if r.category == "M"]
    mean_j = mean_l = mean_v = None
    if comp:
        mean_j = sum((r.j.fraction() for r in comp), Fraction(0)) / len(comp)  # type: ignore[union-attr]
        mean_l = sum((r.l_actual.fraction() for r in comp), Fraction(0)) / len(comp)  # type: ignore[union-attr]
        mean_v = sum((r.v_reference.fraction() for r in comp), Fraction(0)) / len(comp)  # type: ignore[union-attr]
    lower = upper = None
    if comp or miss:
        pool = comp + miss
        lower = sum((r.lower.fraction() for r in pool), Fraction(0)) / len(pool)  # type: ignore[union-attr]
        upper = sum((r.upper.fraction() for r in pool), Fraction(0)) / len(pool)  # type: ignore[union-attr]
    reasons = []
    for reason in _REASON_ORDER:
        ids = [r.clone_id for r in miss if r.missing_reason == reason]
        if ids:
            reasons.append(ReasonCount(reason=reason, count=len(ids), clone_ids=tuple(ids)))  # type: ignore[arg-type]
    notes = []
    if not comp and miss:
        notes.append("没有完整评价的分身：完整者均值为空（不填 0，不用 L 代替 J），只给缺失范围")
    if not comp and not miss:
        notes.append("全部确认出局：均值与缺失范围都为空；出局比例 100%")
    if len(comp) == 1:
        notes.append("只有 1 个完整评价分身：均值就是它的 J，不是稳定的抽样证据")
    if any(r.unexpected_error for r in records):
        notes.append("有意外程序错误：不能假定它只影响这一个分身，先查原因再解读这份报告")
    hard = _hard_summaries(records, hard_requirement_needs)
    if any(h.violated or h.unknown or h.not_checked for h in hard):
        notes.append(
            "有分身没有满足、无法判断或尚未核验用户的硬要求（未核验 = 没有可信记录，不是核验过、没问题）：见硬要求一节；"
            "均值高不代表完全符合用户要求"
        )
    return CityReport(
        arm_id=arm_id,
        user_id=user_id,
        person_id=person_id,
        city=city,
        planned=n,
        complete=len(comp),
        exits=len(exits),
        missing=len(miss),
        complete_share=_ex(Fraction(len(comp), n)),
        exit_share=_ex(Fraction(len(exits), n)),
        missing_share=_ex(Fraction(len(miss), n)),
        mean_j=None if mean_j is None else _ex(mean_j),
        mean_l=None if mean_l is None else _ex(mean_l),
        mean_v=None if mean_v is None else _ex(mean_v),
        mean_j_display=_disp(mean_j),
        mean_l_display=_disp(mean_l),
        mean_v_display=_disp(mean_v),
        range_lower=None if lower is None else _ex(lower),
        range_upper=None if upper is None else _ex(upper),
        range_lower_display=_disp(lower),
        range_upper_display=_disp(upper),
        range_denominator=len(comp) + len(miss),
        missing_reasons=tuple(reasons),
        exit_clone_ids=tuple(r.clone_id for r in exits),
        hard_requirements=hard,
        entries=tuple(records),
        notes=tuple(notes),
    )


def compare_cities(a: CityReport, b: CityReport) -> CityComparison:
    """同一运行臂、同一用户两座城的比较：均值差（两边都有 C 时）与范围差 [A 下 − B 上, A 上 − B 下]。"""
    if (a.arm_id, a.user_id) != (b.arm_id, b.user_id):
        raise LifeContractError("只比较同一运行臂、同一用户的两座城")
    mean_diff = None
    if a.mean_j is not None and b.mean_j is not None:
        mean_diff = _ex(a.mean_j.fraction() - b.mean_j.fraction())
    low = high = None
    direction = "not_available"
    if a.range_lower is not None and b.range_lower is not None:
        lo = a.range_lower.fraction() - b.range_upper.fraction()  # type: ignore[union-attr]
        hi = a.range_upper.fraction() - b.range_lower.fraction()  # type: ignore[union-attr]
        low, high = _ex(lo), _ex(hi)
        direction = "a_higher" if lo > 0 else "b_higher" if hi < 0 else "undetermined"
    note = {
        "a_higher": "在这些缺失范围下 A 城始终更高：方向一致；不是显著性检验，也不是总体因果结论",
        "b_higher": "在这些缺失范围下 B 城始终更高：方向一致；不是显著性检验，也不是总体因果结论",
        "undetermined": "范围差含 0：在这些缺失范围下不能确定方向",
        "not_available": "有一座城全部确认出局，没有范围可比；看出局比例",
    }[direction]
    note += f"。出局比例 A {a.exit_share.value:.0%}、B {b.exit_share.value:.0%}，有效分身数 A {a.complete}/{a.planned}、B {b.complete}/{b.planned}"
    return CityComparison(
        arm_id=a.arm_id,
        user_id=a.user_id,
        city_a=a.city,
        city_b=b.city,
        planned_a=a.planned,
        planned_b=b.planned,
        complete_a=a.complete,
        complete_b=b.complete,
        exit_share_a=a.exit_share,
        exit_share_b=b.exit_share,
        mean_difference=mean_diff,
        range_difference_low=low,
        range_difference_high=high,
        direction=direction,  # type: ignore[arg-type]
        note=note[:600],
    )


def aggregate_arm(
    roster: FrozenRoster,
    arm_id: str,
    evaluations: Sequence[CloneEvaluation],
    failures: Sequence[RunFailure] = (),
) -> ArmReport:
    """一个运行臂的汇总：分类每个名单项，按"用户 × 城市"出报告，同一用户两两比较城市。"""
    arm = roster.arm(arm_id)
    if arm is None:
        raise LifeContractError(f"冻结名单里没有运行臂 {arm_id}")
    records, rejected = classify_arm(roster, arm_id, evaluations, failures)
    persons = {u.user_id: u.person_id for u in roster.users}
    needs: dict[tuple[str, str], set[str]] = {}
    for e in roster.entries:
        needs.setdefault((e.user_id, str(e.city)), set()).update(e.hard_requirement_needs)
    cities = [str(c.city) for c in roster.cities]
    reports = []
    comparisons = []
    for u in roster.users:
        by_city = {
            c: [r for r in records if r.user_id == u.user_id and str(r.city) == c] for c in cities
        }
        user_reports = [
            city_report(
                arm_id,
                u.user_id,
                persons[u.user_id],
                rs,
                hard_requirement_needs=sorted(needs.get((u.user_id, c), set())),
            )
            for c, rs in by_city.items()
            if rs
        ]
        reports += user_reports
        for i, ra in enumerate(user_reports):
            for rb in user_reports[i + 1 :]:
                comparisons.append(compare_cities(ra, rb))
    fields = dict(
        roster_id=roster.roster_id,
        roster_fingerprint=roster.content_fingerprint,
        start_condition_rule=roster.start_condition_rule,
        aggregation_revision=roster.aggregation_revision,
        arm_id=arm.arm_id,
        role=arm.role,
        evaluation_spec_id=arm.evaluation_spec_id,
        evaluation_spec_content_fingerprint=arm.evaluation_spec_content_fingerprint,
        discount_factor=arm.discount_factor,
        horizon_months=arm.horizon_months,
        simulation_years=arm.simulation_years,
        city_reports=tuple(reports),
        comparisons=tuple(comparisons),
        rejected_submissions=rejected,
        unexpected_errors=sum(r.unexpected_error for r in records),
        assumptions=ASSUMPTIONS,
    )
    unsigned = ArmReport.model_construct(**fields, report_fingerprint="0" * 64)
    return ArmReport(**fields, report_fingerprint=unsigned.expected_report_fingerprint())  # type: ignore[arg-type]


# ---------------------------------------------------------------------- 评价对照


def _pair_evidence(
    report: ArmReport, evaluations: Sequence[CloneEvaluation]
) -> tuple[dict[str, CloneEvaluation], list[str], dict[str, str]]:
    """把传入的评价结果按报告里每个名单项记下的评价指纹一一配对（2026-10-12 收尾 R3）。返回（配对成功的分身 → 评价结果,
    证据问题, 报告里没有可信评价的名单项 → 原因）。

    * 报告引用了某份评价、传入的却缺了、多了、重复、指纹不同（旧缓存或另一次运行）或来自别的评价规格，都是证据问题；
      不用字典覆盖静默挑一份；
    * 报告里没有可信评价的名单项（没返回、核对不通过、输入或程序错误）保留原来的缺失含义：传入的同一分身的结果不拿来补，
      也不算证据问题；
    * 名单外的结果只有报告已经单列拒收的才不算多余。"""
    entries = {e.clone_id: e for cr in report.city_reports for e in cr.entries}
    rejected = {r.clone_id for r in report.rejected_submissions}
    by_clone: dict[str, list[CloneEvaluation]] = {}
    for ev in evaluations:
        by_clone.setdefault(ev.clone_id, []).append(ev)
    arm = f"运行臂 {report.arm_id}"
    paired: dict[str, CloneEvaluation] = {}
    problems = [
        f"{arm}：{clone} 不是这份报告的名单项，报告也没有单列拒收它——多出来的证据"
        for clone in sorted(set(by_clone) - set(entries) - rejected)
    ]
    untrusted: dict[str, str] = {}
    for clone, entry in sorted(entries.items()):
        got, want = by_clone.get(clone, []), entry.evaluation_fingerprint
        if want is None:
            untrusted[clone] = (
                MISSING_REASON_TEXT[entry.missing_reason]
                if entry.missing_reason
                else entry.category
            )
        elif not got:
            problems.append(f"{arm}：{clone} 缺少报告引用的那份评价结果（{want[:12]}…）")
        elif len(got) > 1:
            problems.append(f"{arm}：{clone} 收到 {len(got)} 份评价结果——不静默选其中一份")
        elif got[0].result_fingerprint != want:
            ev = got[0]
            if (ev.spec_id, ev.spec_content_fingerprint) != (
                report.evaluation_spec_id,
                report.evaluation_spec_content_fingerprint,
            ):
                problems.append(
                    f"{arm}：{clone} 收到的评价来自评价规格 {ev.spec_id}，不是这个运行臂的"
                )
            else:
                problems.append(
                    f"{arm}：{clone} 收到的评价结果（{ev.result_fingerprint[:12]}…）不是报告引用的那一份"
                    f"（{want[:12]}…）——可能是旧缓存或另一次运行"
                )
        else:
            paired[clone] = got[0]
    return paired, problems, untrusted


def sensitivity(
    main: ArmReport,
    other: ArmReport,
    main_evaluations: Sequence[CloneEvaluation],
    other_evaluations: Sequence[CloneEvaluation],
) -> SensitivityReport:
    """主臂与一个评价对照臂：完整评价人数、L / V / J 的均值、范围与比较方向的变化，以及为什么。

    "两臂是同一批实际生活、只改了评价假设"要用证据说明：先把每一边传入的评价结果按报告里的评价指纹一一配对（``_pair_evidence``），
    证据不配套就报 ``evidence_mismatch``（不能判断，也不等于生活不同）；配套之后，比较两边都有可信评价的分身逐年封存的生活绑定——
    有不同就是 ``different``（这不是只改评价假设的对照），全部相同才是 ``identical``；没有任何可比较的分身是
    ``no_comparable_evidence``。两边都没有可信评价的分身（例如没返回）列在"未比较"里，不说成"已经证明相同"。核验失败不改写、
    不删减两份报告，也不取消主结果；均值与方向的变化照样列出，解释看状态。"""
    for rep in (main, other):  # 不信 model_copy 改过的报告：内容必须对得上它记下的指纹
        if rep.report_fingerprint != rep.expected_report_fingerprint():
            raise LifeContractError(
                f"运行臂 {rep.arm_id} 的汇总报告内容与它记下的指纹不一致（可能被改写过）：不能比较"
            )
    if main.roster_fingerprint != other.roster_fingerprint:
        raise LifeContractError("两个运行臂不是同一份冻结名单：不能比较")
    for ev in (*main_evaluations, *other_evaluations):
        if ev.result_fingerprint != ev.expected_result_fingerprint():
            raise LifeContractError(
                f"{ev.clone_id} 的评价结果内容与它记下的指纹不一致（可能被改写过）：不能用来判断两臂是不是同一批生活"
            )
    paired_m, problems_m, untrusted_m = _pair_evidence(main, main_evaluations)
    paired_o, problems_o, untrusted_o = _pair_evidence(other, other_evaluations)
    problems = [*problems_m, *problems_o]
    compared, different, not_compared = [], [], []
    for clone in sorted({e.clone_id for cr in main.city_reports for e in cr.entries}):
        a, b = paired_m.get(clone), paired_o.get(clone)
        if a is None or b is None:
            why = []
            if clone in untrusted_m:
                why.append(f"主臂没有可信评价（{untrusted_m[clone]}）")
            elif a is None:
                why.append("主臂的证据不配套")
            if clone in untrusted_o:
                why.append(f"对照臂没有可信评价（{untrusted_o[clone]}）")
            elif b is None:
                why.append("对照臂的证据不配套")
            not_compared.append(f"{clone}：" + "；".join(why))
            continue
        compared.append(clone)
        if tuple(y.binding_fingerprint for y in a.trajectory.years) != tuple(
            y.binding_fingerprint for y in b.trajectory.years
        ):
            different.append(clone)
    if problems:
        status = "evidence_mismatch"
        note = (
            f"传入的评价结果与两份报告不配套（{len(problems)} 处，见证据问题）：不能判断两臂是不是同一批实际生活，这不等于"
            "生活不同；两份报告本身不受影响"
        )
    elif different:
        status = "different"
        note = (
            f"实际生活不同：{'、'.join(different)} 两臂的逐年封存绑定不一样——这不是只改评价假设的对照，均值与方向的变化"
            "不能只归因于评价规格"
        )
    elif compared:
        status = "identical"
        note = (
            f"同一批实际生活：{len(compared)} 个分身两臂的逐年封存绑定逐一相同，只换了评价规格；完整评价人数变化的原因见各城的"
            "换类说明"
            + (
                f"；{len(not_compared)} 个分身两边没有都可信的评价，没有比较"
                if not_compared
                else ""
            )
        )
    else:
        status = "no_comparable_evidence"
        note = "没有任何分身两臂都有可信评价：不能证明两臂是同一批实际生活"
    changes = []
    for rm in main.city_reports:
        ro = next(
            r for r in other.city_reports if (r.user_id, str(r.city)) == (rm.user_id, str(rm.city))
        )
        moved = []
        for em, eo in zip(rm.entries, ro.entries, strict=True):
            if (em.category, em.missing_reason) != (eo.category, eo.missing_reason):
                moved.append(
                    f"{em.clone_id}：{em.category}"
                    + (f"（{MISSING_REASON_TEXT[em.missing_reason]}）" if em.missing_reason else "")
                    + f" → {eo.category}"
                    + (f"（{MISSING_REASON_TEXT[eo.missing_reason]}）" if eo.missing_reason else "")
                    + f"：{eo.detail}"[:300]
                )
        changes.append(
            SensitivityChange(
                user_id=rm.user_id,
                city=rm.city,
                complete_main=rm.complete,
                complete_other=ro.complete,
                mean_j_main=rm.mean_j,
                mean_j_other=ro.mean_j,
                mean_l_main=rm.mean_l,
                mean_l_other=ro.mean_l,
                mean_v_main=rm.mean_v,
                mean_v_other=ro.mean_v,
                range_main_lower=rm.range_lower,
                range_main_upper=rm.range_upper,
                range_other_lower=ro.range_lower,
                range_other_upper=ro.range_upper,
                moved=tuple(moved),
            )
        )
    direction_changes = []
    for cm in main.comparisons:
        co = next(
            c
            for c in other.comparisons
            if (c.user_id, str(c.city_a), str(c.city_b))
            == (cm.user_id, str(cm.city_a), str(cm.city_b))
        )
        if cm.direction != co.direction:
            direction_changes.append(
                f"{cm.user_id}：{cm.city_a} 对 {cm.city_b} 的方向从 {cm.direction} 变成 {co.direction}"
            )
    return SensitivityReport(
        main_arm_id=main.arm_id,
        other_arm_id=other.arm_id,
        aggregation_revision=AGGREGATION_REVISION,
        lives_check=status,  # type: ignore[arg-type]
        actual_lives_identical=status == "identical",
        compared_clone_ids=tuple(compared),
        different_clone_ids=tuple(different),
        not_compared=tuple(not_compared),
        evidence_problems=tuple(problems),
        changes=tuple(changes),
        direction_changes=tuple(direction_changes),
        note=note[:600],
    )
