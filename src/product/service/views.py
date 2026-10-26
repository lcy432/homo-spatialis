"""服务的公开视图：从服务库的行生成白名单消息（会话、次数、规则、草稿、运行意图、运行列表）。

视图只读服务库里 worker 写下的公开快照；生成视图不会调用规划器、推进流程或重算结果。流程内部编号、checkpoint、文件路径、
堆栈与内部诊断正文都不在这里。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from common.product_contracts import (
    ConfirmedRef,
    DeletionState,
    DraftView,
    IntRange,
    MoneyRange,
    ProductMeta,
    ProductPreflight,
    ProgressSummary,
    PublicExplanation,
    QuotaSummary,
    RunIntentView,
    RunOptionsPolicy,
    RunProgress,
    RunSettingsView,
    RunSummary,
    ServiceRules,
    StartAuthorizationView,
    UserInputDraft,
)
from product.display import Code, Join, Num, t
from product.execution import ExecutionSpec, RunPolicy
from product.migration import migration_for
from product.pack import DataPack
from product.service.config import ServiceConfig
from product.service.explain import service_explanation
from product.service.store import Quota, intent_deadline, start_authorization

STAGE_ZH = {
    "checking": "排队检查中：运行前检查由服务按顺序执行，不使用次数",
    "awaiting_confirmation": "等你确认开始",
    "blocked": "运行前检查没有通过",
    "queued": "已确认，排队等待首次启动",
    "running": "模拟进行中",
    "needs_attention": "停下了，需要处理",
    "finished": "本次服务流程结束（结果里逐城写明完成、出局与模拟缺失）",
    "cancelled": "开始前已取消",
    "deleting": "正在删除",
    "deleted": "已删除",
    "expired": "已过期",
}
#: 恢复之后又在排队的运行：公开阶段同样是 queued，说明照实写“已经开始过”
QUEUED_RESUME_ZH = "已经开始过，排队等待恢复（不另扣次数）"


def stage_text(stage: str) -> str:
    return t(f"stage.{stage}", STAGE_ZH[stage])


def quota_view(q: Quota) -> QuotaSummary:
    return QuotaSummary(**q.as_dict())


def _money(value: object) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def options_view(config: ServiceConfig, policy: RunPolicy) -> RunOptionsPolicy:
    """用户能选什么（默认值与允许范围），网页照着显示控件。"""
    notes = [
        t(
            "rules.options.cities",
            "城市来自你确认的画像：可以只比较其中几座，但都要在允许的城市里，不会替你删城或补城",
        ),
        t(
            "rules.options.clones",
            "每城分身 {low}–{high} 个（不选就是 {default} 个）；分身越多，"
            "同一座城的结果越能看出运气的影响，但规划请求与费用也按比例增加",
            low=Num(str(policy.clones_min)),
            high=Num(str(policy.clones_max)),
            default=Num(str(policy.clones_default)),
        ),
        t(
            "rules.options.years",
            "年数只开放资料包配套验证过的 {years} 年",
            years=Join(tuple(Num(str(y)) for y in policy.years_allowed), zh_sep=", ", en_sep=", "),
        ),
        t(
            "rules.options.cost",
            "费用上限 {low}–{high} 元（不选就是 {default} 元）："
            "这次运行的全部城市、全部分身、全部服务尝试共用；扩大规模不会自动加预算，费用不够发下一次请求时就停下",
            low=Num(_money(policy.cost_min)),
            high=Num(_money(policy.cost_max)),
            default=Num(_money(policy.cost_default)),
        ),
    ]
    return RunOptionsPolicy(
        cities_allowed=tuple(policy.cities_allowed),
        clones_per_city=IntRange(
            default=policy.clones_default, minimum=policy.clones_min, maximum=policy.clones_max
        ),
        years_default=policy.years_default,
        years_allowed=tuple(policy.years_allowed),
        max_cost=MoneyRange(
            default=_money(policy.cost_default),
            minimum=_money(policy.cost_min),
            maximum=_money(policy.cost_max),
            currency="CNY",
        ),
        notes_zh=tuple(notes),
    )


def rules_view(config: ServiceConfig, policy: RunPolicy, spec: ExecutionSpec) -> ServiceRules:
    """服务的规则。``spec`` 是按默认选项、全部允许城市算出的设置（只用来显示默认规模与费用上限）。"""
    notes = [
        t(
            "rules.runs_total",
            "每个专属空间一共 {runs} 次启动模拟的机会，不自动重置；无论选多大的规模，真正开始一次就用掉一次机会，"
            "不保证每次所有分身都完整",
            runs=Num(str(config.runs_total)),
        ),
        t(
            "rules.default_scale",
            "默认比较 {cities} 个都会区、每城 {clones} 个分身、每个分身 {years} 年",
            cities=Num(str(len(spec.cities))),
            clones=Num(str(len(spec.repeat_indexes))),
            years=Num(str(spec.years)),
        ),
        t(
            "rules.result_retention",
            "结果从运行首次结束起保存 {days} 天，查看不续期；可以随时删除",
            days=Num(str(config.result_retention_days)),
        ),
        t(
            "rules.profile_retention",
            "确认画像从首次确认起 {days} 天内可以用来准备新的模拟",
            days=Num(str(config.profile_retention_days)),
        ),
    ]
    if spec.planner_kind == "llm":
        notes.append(
            t(
                "rules.cost_cap",
                "默认每次运行的费用上限是 {cost} {currency}（应用侧按报价预留，不是供应商账单上限），不自动加预算",
                cost=Num(str(spec.max_cost)),
                currency=Code(str(spec.currency)),
            )
        )
        if not config.live_calls_enabled:
            notes.append(
                t("rules.paid_off", "付费模型调用现在没有开启：可以填写与检查，但不能开始模拟")
            )
    else:
        notes.append(
            t(
                "rules.mock",
                "这个服务实例使用离线规划器（mock）：没有网络调用、费用为 0，不代表真实模型的能力",
            )
        )
    return ServiceRules(
        runs_total=config.runs_total,
        cities=len(spec.cities),
        clones_per_city=len(spec.repeat_indexes),
        years=spec.years,
        planner_kind=spec.planner_kind,
        live_calls_enabled=config.live_calls_enabled,
        max_cost=spec.max_cost or "0",
        currency=spec.currency or "CNY",
        result_retention_days=config.result_retention_days,
        draft_retention_days=config.draft_retention_days,
        intent_retention_days=config.intent_retention_days,
        session_days=config.session_days,
        profile_retention_days=config.profile_retention_days,
        options=options_view(config, policy),
        notes_zh=tuple(notes),
    )


def settings_view(row: Mapping[str, Any]) -> RunSettingsView | None:
    """这次运行保存的有效设置（删除、过期之后已经清掉，就不显示）。"""
    if row["execution_body"] is None:
        return None
    return settings_from_spec(
        ExecutionSpec.model_validate_json(row["execution_body"]), row["execution_fingerprint"]
    )


def settings_from_spec(spec: ExecutionSpec, fingerprint: str | None = None) -> RunSettingsView:
    """一份有效设置的公开写法（运行意图保存的那一份，或设置预览按当前政策算出的那一份）。"""
    llm = spec.planner_kind == "llm"
    return RunSettingsView(
        cities=tuple(spec.cities),
        clones_per_city=len(spec.repeat_indexes),
        years=spec.years,
        clones_total=spec.clones,
        planner_kind=spec.planner_kind,
        max_cost=spec.max_cost,  # mock 不花钱，但用户选的上限照样是这次设置的一部分（进入设置的身份）
        currency=spec.currency,
        call_limit=spec.global_call_limit,
        service_attempt_limit=spec.max_requests if llm else None,
        settings_fingerprint=fingerprint or spec.fingerprint,
    )


def draft_view(
    meta: ProductMeta, row: Mapping[str, Any], pack: DataPack | None = None
) -> DraftView:
    """一份草稿；给了资料包时附上它进入新问卷要做的迁移（``product.migration``，读取不改写草稿）。"""
    draft = UserInputDraft.model_validate_json(row["body"])
    return DraftView(
        meta=meta,
        draft=draft,
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        migration=None if pack is None else migration_for(pack, draft),
    )


def _json(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def allowed_actions(
    row: Mapping[str, Any], quota: Quota, *, started: bool, start_allowed: bool
) -> tuple[str, ...]:
    """此刻服务允许的动作——与动作接口用同一份规则（读取之后状态变了，动作接口仍可能拒绝）：

    * ``submit``：等确认、还有可用次数，而且这次的规划器现在能开始（真实模型需要服务开启付费调用；离线 mock 不受这个开关影响）；
      带着一次开始授权的意图没有它（由服务按授权开始，不需要第二次确认）；
    * ``start_again``：一次开始的授权作废之后、意图仍在等确认，可以重新取设置预览、再“确认并开始”一次（同样要有次数、能开始）；
    * ``cancel``：还没有开始过（服务库里没有启动记录）的检查中、等确认、检查没通过或排队等首次启动——恢复之后又在排队的不能取消。"""
    stage = row["stage"]
    auth = start_authorization(row)
    out: list[str] = []
    can_start = stage == "awaiting_confirmation" and quota.available >= 1 and start_allowed
    if can_start and auth is None:
        out.append("submit")
    if can_start and auth is not None and auth["state"] == "void":
        out.append("start_again")
    if stage in ("checking", "awaiting_confirmation", "blocked", "queued") and not started:
        out.append("cancel")
    if row["progress_body"] is not None and stage not in ("deleting", "deleted", "expired"):
        out.append("view_progress")
    if row["result_body"] is not None and stage == "finished":
        out.append("view_result")
    if stage == "needs_attention" and row["resume_allowed"]:
        out.append("resume")
    if row["run_id"] is not None and stage not in ("deleting", "deleted", "expired"):
        out.append("delete")  # 还没有确认开始的意图用“取消”
    if stage in ("finished", "cancelled", "deleted", "expired") and quota.available >= 1:
        out.append("create_new_intent")
    return tuple(out)


def waiting_for(row: Mapping[str, Any], *, start_allowed: bool = True) -> str:
    stage = row["stage"]
    auth = start_authorization(row)
    if stage == "awaiting_confirmation" and auth is not None and auth["state"] == "pending":
        return "service"  # 检查通过、授权还没用：服务正按授权开始
    if stage == "awaiting_confirmation" and not start_allowed:
        return "maintainer"  # 付费调用没有开启：要等维护者开启，不是等用户
    if stage in ("awaiting_confirmation", "blocked"):
        return "user"
    if stage == "needs_attention":
        return "user" if row["resume_allowed"] else "maintainer"
    if stage in ("checking", "queued", "running", "deleting"):
        return "service"
    return "none"


def needs_live_calls(row: Mapping[str, Any]) -> bool:
    """这次运行的有效设置用真实模型规划器（需要服务开启付费调用）。"""
    if row["execution_body"] is None:
        return False
    return ExecutionSpec.model_validate_json(row["execution_body"]).planner_kind == "llm"


#: 开始授权作废、意图仍在等确认时的说明代码（检查没通过等改了阶段的，用那一次自己保存的说明）
_VOID_EXPLAINED = {
    "conditions_changed",
    "consent_changed",
    "paid_calls_disabled",
    "quota_exhausted",
    "authorization_mismatch",
    "data_pack_unavailable",
    "internal_error",
}


def start_view(row: Mapping[str, Any]) -> StartAuthorizationView | None:
    auth = start_authorization(row)
    if auth is None:
        return None
    return StartAuthorizationView(
        mode="start_when_ready",
        state=auth["state"],
        consent_fingerprint=auth["consent_fingerprint"],
        acknowledged=tuple(auth["acknowledged"]),
        authorized_at=auth["authorized_at"],
        used_at=auth.get("used_at"),
        void_reason=auth.get("void_reason"),
    )


def confirmation_source(row: Mapping[str, Any]) -> str | None:
    """已经确认开始时，确认来自哪里（还没确认开始，或删除、过期之后内容已经清掉，就是空）。"""
    if row["run_id"] is None or row["stage"] in ("deleting", "deleted", "expired"):
        return None
    auth = start_authorization(row)
    return "start_authorization" if auth is not None and auth["state"] == "used" else "user_confirm"


def _summary(
    phase: str,
    sentence: str,
    at: str,
    *,
    mode: str = "indeterminate",
    counts: tuple[int, int] | None = None,
) -> ProgressSummary:
    return ProgressSummary(
        phase=phase,  # type: ignore[arg-type]
        sentence_zh=sentence,
        updated_at=at,
        mode=mode,  # type: ignore[arg-type]
        settled_clone_years=None if counts is None else counts[0],
        planned_clone_years=None if counts is None else counts[1],
    )


def _clone_years(row: Mapping[str, Any]) -> tuple[tuple[int, int] | None, str | None, str | None]:
    """（已结算 / 计划的分身年度数，快照的时间，E1 的批次状态）。快照记下了 E1 的已结算年度数与计划总数就用它们（出局或中断之后没有
    经历的年份不算）；旧快照没有时，只有名单里每个分身都已经有结局记录才按记录相加（那时是准确的），否则不给计数——不拿只数了已结束
    分身的下界冒充进度。没有快照就是空。"""
    snap = _json(row["progress_body"])
    if snap is None:
        return None, None, None
    when, stage = snap.get("updated_at"), snap.get("stage")
    if snap.get("clone_years_settled") is not None and snap.get("clone_years_planned"):
        settled, planned = int(snap["clone_years_settled"]), int(snap["clone_years_planned"])
        return (min(settled, planned), planned), when, stage
    if row["execution_body"] is None:
        return None, when, stage
    spec = ExecutionSpec.model_validate_json(row["execution_body"])
    clones = snap.get("clones", ())
    if len(clones) != spec.clones:
        return None, when, stage
    planned = spec.clones * spec.years
    settled = sum(int(c["years_committed"]) for c in clones)
    return (min(settled, planned), planned), when, stage


def progress_summary(
    row: Mapping[str, Any], *, started: bool, start_allowed: bool
) -> ProgressSummary:
    """等待页的一句当前状态与进度条（只读，按服务库的阶段与 E1 的进度快照当场生成；见 ``ProgressSummary``）。"""
    stage = row["stage"]
    at = row["updated_at"]
    auth = start_authorization(row)
    pending = auth is not None and auth["state"] == "pending"
    if stage == "checking":
        return _summary(
            "checking",
            t("progress_summary.checking_authorized", "正在后台检查；通过后按你的确认自动开始")
            if pending
            else t("progress_summary.checking", "正在后台检查这次模拟能不能开始（不使用次数）"),
            at,
        )
    if stage == "awaiting_confirmation":
        if pending:
            return _summary(
                "checking", t("progress_summary.starting", "检查通过，正在按你的确认开始"), at
            )
        if auth is not None:
            sentence = t(
                "progress_summary.start_void", "开始授权已经作废：需要你看过说明后重新确认"
            )
        elif not start_allowed:
            sentence = t("progress_summary.paid_off", "检查通过，等维护者开启付费模型调用")
        else:
            sentence = t("progress_summary.awaiting_confirmation", "检查通过，等你确认开始")
        return _summary("needs_attention", sentence, at)
    if stage == "blocked":
        return _summary(
            "needs_attention",
            t("progress_summary.blocked", "开始之前停下了：原因与下一步见说明"),
            at,
        )
    if stage == "queued":
        return _summary(
            "queued",
            t("progress_summary.queued_resume", "已经开始过，排队等待恢复")
            if started
            else t("progress_summary.queued", "已确认开始，排队等待启动"),
            at,
        )
    counts, snap_at, e1_stage = _clone_years(row)
    when = snap_at or at
    if stage == "running":
        if row["progress_body"] is None:
            return _summary(
                "simulating",
                t("progress_summary.started", "模拟刚开始，第一份进度还没有写下"),
                at,
            )
        if counts is None:  # 旧版本写下的快照：没有记下已结算的年度数，不拿下界冒充
            return _summary(
                "simulating",
                t(
                    "progress_summary.simulating_uncounted",
                    "模拟进行中（这份进度快照没有记下已结算的分身年度数）",
                ),
                when,
            )
        if e1_stage == "completed":
            return _summary(
                "assembling_result",
                t("progress_summary.assembling", "模拟已经结束，正在整理结果"),
                when,
            )
        return _summary(
            "simulating",
            t(
                "progress_summary.simulating",
                "已结算 {settled}/{planned} 个分身年度",
                settled=Num(str(counts[0])),
                planned=Num(str(counts[1])),
            ),
            when,
            mode="clone_years",
            counts=counts,
        )
    if stage == "needs_attention":
        if counts is None:
            return _summary(
                "needs_attention", t("progress_summary.stopped", "停下了，需要处理"), at
            )
        return _summary(
            "needs_attention",
            t(
                "progress_summary.stopped_resumable",
                "停下了：已结算 {settled}/{planned} 个分身年度，可以恢复",
                settled=Num(str(counts[0])),
                planned=Num(str(counts[1])),
            )
            if row["resume_allowed"]
            else t(
                "progress_summary.stopped_maintainer",
                "停下了：已结算 {settled}/{planned} 个分身年度，等维护者处理",
                settled=Num(str(counts[0])),
                planned=Num(str(counts[1])),
            ),
            when,
            mode="clone_years",
            counts=counts,
        )
    if stage == "finished":
        if row["result_body"] is None:  # 结果读不到就不说完成
            return _summary(
                "needs_attention",
                t("progress_summary.result_unreadable", "模拟结束了，但结果暂时读不到"),
                at,
            )
        return _summary(
            "finished",
            t(
                "progress_summary.finished_counted",
                "结果可以查看：已结算 {settled}/{planned} 个分身年度（完成、出局与模拟缺失见结果）",
                settled=Num(str(counts[0])),
                planned=Num(str(counts[1])),
            )
            if counts is not None
            else t("progress_summary.finished", "结果可以查看（完成、出局与模拟缺失见结果）"),
            when if counts is not None else at,
            mode="finished",
            counts=counts,
        )
    if stage == "cancelled":
        return _summary("cancelled", t("progress_summary.cancelled", "开始之前已取消"), at)
    if stage == "expired":
        return _summary("expired", t("progress_summary.expired", "已过期"), at)
    return _summary(
        "deleted",
        t("progress_summary.deleting", "正在删除")
        if stage == "deleting"
        else t("progress_summary.deleted", "已删除"),
        at,
    )


def intent_view(
    meta: ProductMeta,
    row: Mapping[str, Any],
    quota: Quota,
    *,
    started: bool,
    live_calls_enabled: bool,
    intent_days: int,
) -> RunIntentView:
    """``started``：服务库里有这次运行的启动记录（首次启动时预留转为已使用）；``live_calls_enabled`` 是服务现在的付费开关。"""
    gone = row["stage"] in ("deleting", "deleted", "expired")
    pre = _json(row["preflight_body"])
    progress = _json(row["progress_body"])
    explanation = _json(row["explanation"])
    auth = start_authorization(row)
    start_allowed = live_calls_enabled or not needs_live_calls(row)
    stage_zh = stage_text(row["stage"])
    if row["stage"] == "queued":
        # 排队时公开阶段相同，说明按实际的启动记录分开：等首次启动（预留着 1 次、可以取消）或等恢复（已经开始过）
        if started:
            stage_zh = t("stage.queued_resume", QUEUED_RESUME_ZH)
            explanation = service_explanation("queued_resume", phase="after_start").model_dump(
                mode="json"
            )
        else:
            explanation = service_explanation("queued_first_start").model_dump(mode="json")
    elif row["stage"] == "awaiting_confirmation" and auth is not None:
        if auth["state"] == "pending":
            stage_zh = t("stage.awaiting_confirmation_authorized", "检查通过，正在按你的确认开始")
        elif auth["state"] == "void":
            stage_zh = t("stage.awaiting_confirmation_void", "开始授权已经作废，等你重新确认")
            if auth.get("void_reason") in _VOID_EXPLAINED:
                explanation = service_explanation(f"start_{auth['void_reason']}").model_dump(
                    mode="json"
                )
    elif row["stage"] == "awaiting_confirmation" and not start_allowed:
        explanation = service_explanation("paid_calls_off_waiting").model_dump(mode="json")
    elif row["stage"] == "checking" and auth is not None and auth["state"] == "pending":
        stage_zh = t(
            "stage.checking_authorized",
            "排队检查中：检查通过后按你的确认自动开始（检查不使用次数）",
        )
    expires, basis = intent_deadline(row, intent_days)
    if row["stage"] in ("deleted", "deleting"):
        deletion = DeletionState(
            state="deleted" if row["stage"] == "deleted" else "deleting",
            requested_at=row["delete_requested_at"],
            completed_at=row["deleted_at"],
        )
    else:
        deletion = DeletionState(state="none")
    return RunIntentView(
        meta=meta,
        intent_id=row["intent_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        stage_zh=stage_zh,
        waiting_for=waiting_for(row, start_allowed=start_allowed),  # type: ignore[arg-type]
        confirmed=ConfirmedRef(
            confirmed_id=row["confirmed_id"], content_fingerprint=row["confirmed_fingerprint"]
        ),
        preflight=None if gone or pre is None else ProductPreflight.model_validate(pre),
        confirm_with=row["preflight_fingerprint"]
        if row["stage"] == "awaiting_confirmation"
        else None,
        progress=None if gone or progress is None else RunProgress.model_validate(progress),
        result_available=not gone and row["result_body"] is not None and row["stage"] == "finished",
        allowed_actions=allowed_actions(  # type: ignore[arg-type]
            row, quota, started=started, start_allowed=start_allowed
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=expires,
        expiry_basis=basis,  # type: ignore[arg-type]
        deletion=deletion,
        explanation=None
        if gone or explanation is None
        else PublicExplanation.model_validate(explanation),
        support_ref=row["support_ref"],
        quota=quota_view(quota),
        settings=None if gone else settings_view(row),
        start=None if gone else start_view(row),
        confirmation_source=confirmation_source(row),  # type: ignore[arg-type]
        progress_summary=progress_summary(row, started=started, start_allowed=start_allowed),
    )


def run_summary(row: Mapping[str, Any], *, started: bool, intent_days: int) -> RunSummary:
    expires, basis = intent_deadline(row, intent_days)
    return RunSummary(
        intent_id=row["intent_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        stage_zh=t("stage.queued_resume", QUEUED_RESUME_ZH)
        if row["stage"] == "queued" and started
        else stage_text(row["stage"]),
        result_available=row["result_body"] is not None and row["stage"] == "finished",
        created_at=row["created_at"],
        finished_at=row["first_finished_at"],
        expires_at=expires,
        expiry_basis=basis,  # type: ignore[arg-type]
        settings=None if row["stage"] in ("deleting", "deleted", "expired") else settings_view(row),
    )
