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
    PublicExplanation,
    QuotaSummary,
    RunIntentView,
    RunOptionsPolicy,
    RunProgress,
    RunSettingsView,
    RunSummary,
    ServiceRules,
    UserInputDraft,
)
from product.execution import ExecutionSpec, RunPolicy
from product.service.config import ServiceConfig
from product.service.store import Quota

STAGE_ZH = {
    "checking": "排队检查中：运行前检查由服务按顺序执行，不使用次数",
    "awaiting_confirmation": "等你确认开始",
    "blocked": "运行前检查没有通过",
    "queued": "已确认，排队等待开始",
    "running": "模拟进行中",
    "needs_attention": "停下了，需要处理",
    "finished": "本次服务流程结束（结果里逐城写明完成、出局与模拟缺失）",
    "cancelled": "开始前已取消",
    "deleting": "正在删除",
    "deleted": "已删除",
    "expired": "已过期",
}


def quota_view(q: Quota) -> QuotaSummary:
    return QuotaSummary(**q.as_dict())


def _money(value: object) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def options_view(config: ServiceConfig, policy: RunPolicy) -> RunOptionsPolicy:
    """用户能选什么（默认值与允许范围），网页照着显示控件。"""
    notes = [
        "城市来自你确认的画像：可以只比较其中几座，但都要在允许的城市里，不会替你删城或补城",
        f"每城分身 {policy.clones_min}–{policy.clones_max} 个（不选就是 {policy.clones_default} 个）；分身越多，"
        "同一座城的结果越能看出运气的影响，但规划请求与费用也按比例增加",
        f"年数只开放资料包配套验证过的 {', '.join(str(y) for y in policy.years_allowed)} 年",
        f"费用上限 {_money(policy.cost_min)}–{_money(policy.cost_max)} 元（不选就是 {_money(policy.cost_default)} 元）："
        "这次运行的全部城市、全部分身、全部服务尝试共用；扩大规模不会自动加预算，费用不够发下一次请求时就停下",
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
        f"每个专属空间一共 {config.runs_total} 次启动模拟的机会，不自动重置；无论选多大的规模，真正开始一次就用掉一次机会，"
        "不保证每次所有分身都完整",
        f"默认比较 {len(spec.cities)} 个都会区、每城 {len(spec.repeat_indexes)} 个分身、每个分身 {spec.years} 年",
        f"结果从运行首次结束起保存 {config.result_retention_days} 天，查看不续期；可以随时删除",
        f"确认画像从首次确认起 {config.profile_retention_days} 天内可以用来准备新的模拟",
    ]
    if spec.planner_kind == "llm":
        notes.append(
            f"默认每次运行的费用上限是 {spec.max_cost} {spec.currency}（应用侧按报价预留，不是供应商账单上限），不自动加预算"
        )
        if not config.live_calls_enabled:
            notes.append("付费模型调用现在没有开启：可以填写与检查，但不能开始模拟")
    else:
        notes.append(
            "这个服务实例使用离线规划器（mock）：没有网络调用、费用为 0，不代表真实模型的能力"
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
    spec = ExecutionSpec.model_validate_json(row["execution_body"])
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
        settings_fingerprint=row["execution_fingerprint"],
    )


def draft_view(meta: ProductMeta, row: Mapping[str, Any]) -> DraftView:
    return DraftView(
        meta=meta,
        draft=UserInputDraft.model_validate_json(row["body"]),
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )


def _json(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def allowed_actions(row: Mapping[str, Any], quota: Quota) -> tuple[str, ...]:
    stage = row["stage"]
    out: list[str] = []
    if stage == "awaiting_confirmation" and quota.available >= 1:
        out.append("submit")
    if stage in ("checking", "awaiting_confirmation", "blocked", "queued"):
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


def waiting_for(row: Mapping[str, Any]) -> str:
    stage = row["stage"]
    if stage in ("awaiting_confirmation", "blocked"):
        return "user"
    if stage == "needs_attention":
        return "user" if row["resume_allowed"] else "maintainer"
    if stage in ("checking", "queued", "running", "deleting"):
        return "service"
    return "none"


def intent_view(meta: ProductMeta, row: Mapping[str, Any], quota: Quota) -> RunIntentView:
    gone = row["stage"] in ("deleting", "deleted", "expired")
    pre = _json(row["preflight_body"])
    progress = _json(row["progress_body"])
    explanation = _json(row["explanation"])
    if row["stage"] in ("deleted", "deleting"):
        deletion = DeletionState(
            state="deleted" if row["stage"] == "deleted" else "deleting",
            requested_at=row["delete_requested_at"],
            completed_at=row["deleted_at"],
        )
    else:
        deletion = DeletionState(state="none")
    expires = row["result_expires_at"]
    return RunIntentView(
        meta=meta,
        intent_id=row["intent_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        stage_zh=STAGE_ZH[row["stage"]],
        waiting_for=waiting_for(row),  # type: ignore[arg-type]
        confirmed=ConfirmedRef(
            confirmed_id=row["confirmed_id"], content_fingerprint=row["confirmed_fingerprint"]
        ),
        preflight=None if gone or pre is None else ProductPreflight.model_validate(pre),
        confirm_with=row["preflight_fingerprint"]
        if row["stage"] == "awaiting_confirmation"
        else None,
        progress=None if gone or progress is None else RunProgress.model_validate(progress),
        result_available=not gone and row["result_body"] is not None and row["stage"] == "finished",
        allowed_actions=allowed_actions(row, quota),  # type: ignore[arg-type]
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=expires,
        deletion=deletion,
        explanation=None
        if gone or explanation is None
        else PublicExplanation.model_validate(explanation),
        support_ref=row["support_ref"],
        quota=quota_view(quota),
        settings=None if gone else settings_view(row),
    )


def run_summary(row: Mapping[str, Any]) -> RunSummary:
    return RunSummary(
        intent_id=row["intent_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        stage_zh=STAGE_ZH[row["stage"]],
        result_available=row["result_body"] is not None and row["stage"] == "finished",
        created_at=row["created_at"],
        finished_at=row["first_finished_at"],
        expires_at=row["result_expires_at"],
        settings=None if row["stage"] in ("deleting", "deleted", "expired") else settings_view(row),
    )
