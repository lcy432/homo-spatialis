"""服务流程的公开说明（E3C 收尾 R6）：原因、影响与下一步按**实际记录**生成，不按停止原因猜。

* ``phase``：这次模拟有没有真的开始（E1 启动过、次数已经转为已使用）——开始之后不能再说“没有开始”；
* ``released``：预留的次数是不是真的退回了——没退回就不能说“已经退回”；
* ``resumable``：服务端判定能不能由用户点“恢复”继续（按 E1 的归档、请求尝试、预算与发送状态，见 ``workflows.service_runs``）。

下一步与 ``allowed_actions`` 一致：开始前停下的意图（检查没有通过）能取消再重新准备；开始之后停下的只能恢复（真能继续时）、等维护者
或删除。全部是固定文字，不透传内部原文。
"""

from __future__ import annotations

from typing import Literal

from common.product_contracts import PublicExplanation

Phase = Literal["before_start", "after_start"]

#: 公开代码 → 发生了什么（与阶段无关）
REASONS: dict[str, str] = {
    "preflight_blocked": "运行前检查没有通过（原因逐条写在运行前说明里）",
    "reference_stale": "确认的画像或这次的运行设置在检查时对不上",
    "inputs_changed": "确认开始之后、启动之前的最后核对发现运行条件变了",
    "options_not_allowed": "服务的允许范围变了，这次选的规模或费用上限已经不能用",
    "settings_missing": "这次运行保存的设置缺失或损坏",
    "snapshot_broken": "这次模拟绑定的资料快照缺失或被改动",
    "paid_calls_disabled": "服务现在没有开启付费模型调用",
    "credentials_missing": "真实模型的凭证现在不可用",
    "start_refused": "启动前的最后核对没有通过",
    "model_mismatch": "模型服务的型号与设定不符",
    "request_too_large": "首年规划请求超过规划器的单次输入上限",
    "service_unavailable": "模型服务暂时不可用，模拟按规则在年度边界或两份候选之间停下",
    "attempts_exhausted": "模型服务暂时不可用，这一份规划的服务尝试次数已经用完",
    "uncertain_send": "有一次模型请求是否已经发出无法确认",
    "budget_blocked": "这次运行的费用上限或请求次数上限不够发出下一次请求",
    "archive_conflict": "运行记录与绑定的输入对不上",
    "run_binding_conflict": "找到的运行与这次确认登记的运行不一致",
    "run_partially_initialized": "运行目录在启动时只建了一半（进程中断）",
    "run_halted": "模拟按 E1 的规则停下",
    "result_unavailable": "模拟已结束，但结果暂时无法读取",
    "internal_error": "服务出现了内部问题",
}

#: 开始之后的额外影响（在“已经用掉 1 次机会”之后补一句）
_AFTER_IMPACT: dict[str, str] = {
    "service_unavailable": "已经保存的回复不会重新请求",
    "attempts_exhausted": "这份规划不会自动再发请求，也不会重置尝试次数或另开账本",
    "uncertain_send": "为了不重复付费，不自动重发；这次的预留费用照原样保留",
    "budget_blocked": "不会自动追加预算；已经完成的部分保留",
    "model_mismatch": "那份回复不用于规划；不自动换模型",
    "archive_conflict": "原始记录没有被改动",
    "snapshot_broken": "系统不会拿新资料冒充旧资料继续",
    "paid_calls_disabled": "系统不会悄悄换成离线规划器继续",
    "credentials_missing": "没有发出新的请求，也不会换成离线规划器",
}

#: 开始之前的额外影响
_BEFORE_IMPACT: dict[str, str] = {
    "snapshot_broken": "系统不会拿新资料冒充旧资料",
    "paid_calls_disabled": "系统不会悄悄换成离线规划器",
    "credentials_missing": "没有发出任何请求，也不会换成离线规划器",
    "request_too_large": "没有发出请求；系统不会删减选项或换模型",
    "model_mismatch": "没有用任何回复规划",
}

#: 开始之前停下时的下一步（意图停在“检查没有通过”，可以取消再重新准备）
_BEFORE_NEXT: dict[str, str] = {
    "preflight_blocked": "按说明修改答案，重新审阅、确认画像后再准备一次",
    "paid_calls_disabled": "等服务开启后，取消这次准备并重新准备",
    "credentials_missing": "等维护者处理后，取消这次准备并重新准备",
    "request_too_large": "等维护者调整后，取消这次准备并重新准备",
    "options_not_allowed": "取消这次准备，按服务规则里现在允许的范围重新准备",
}


def service_explanation(
    code: str,
    *,
    phase: Phase = "before_start",
    released: bool = False,
    resumable: bool = False,
    support: str | None = None,
) -> PublicExplanation:
    """按实际记录写说明。``phase`` / ``released`` / ``resumable`` 都来自服务库与 E1 的记录，不由停止原因推断。"""
    known = code in REASONS
    key = code if known else "internal_error"
    reason = REASONS[key]
    if phase == "after_start":
        parts = ["模拟已经开始，这次运行已经用掉 1 次机会（开始之后不退）"]
        if key in _AFTER_IMPACT:
            parts.append(_AFTER_IMPACT[key])
        impact = "；".join(parts)
        if resumable:
            step = "稍后点“恢复”继续同一次运行（不另扣次数）"
        else:
            step = "不需要重新填写：等维护者处理，或删除这次运行；不会提供无效的“恢复”"
    else:
        lead = "没有开始模拟，预留的次数已经退回" if released else "没有开始模拟，没有使用次数"
        impact = lead + ("；" + _BEFORE_IMPACT[key] if key in _BEFORE_IMPACT else "")
        step = _BEFORE_NEXT.get(
            key, "取消这次准备后重新准备；问题一直出现时请联系维护者并提供支持编号"
        )
    return PublicExplanation(
        category=f"service_{key}",
        reason_zh=reason,
        impact_zh=impact,
        next_action_zh=step,
        audit_ref=support,
    )
