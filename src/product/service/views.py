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
    ProductMeta,
    ProductPreflight,
    PublicExplanation,
    QuotaSummary,
    RunIntentView,
    RunProgress,
    RunSummary,
    ServiceRules,
    UserInputDraft,
)
from product.execution import ExecutionSpec
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


def rules_view(config: ServiceConfig, spec: ExecutionSpec) -> ServiceRules:
    notes = [
        f"每个专属空间一共 {config.runs_total} 次启动模拟的机会，不自动重置；3 次是启动机会，不保证每次所有分身都完整",
        f"每次固定比较 {len(spec.cities)} 个都会区、每城 {len(spec.repeat_indexes)} 个分身、每个分身 {spec.years} 年",
        f"结果从运行首次结束起保存 {config.result_retention_days} 天，查看不续期；可以随时删除",
    ]
    if spec.planner_kind == "llm":
        notes.append(
            f"每次运行的费用由应用侧按报价控制在 {spec.max_cost} {spec.currency} 以内（不是供应商账单上限），不自动加预算"
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
        notes_zh=tuple(notes),
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
    )
