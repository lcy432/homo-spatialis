"""公开错误的白名单与内部诊断（E3-A 收尾 F4、F5，DECISIONS/0034 第九节）。

给用户看的错误、暂停与分身状态说明**只由结构化类别生成**：类别代码、中文原因、影响、下一步，再加一个不含内部内容的审计编号。
异常原文、内部路径、配置内容与参数不进入任何公开消息——不是靠正则删掉路径（异常里还可能有任意内部文字），而是公开文字根本不
从异常文本拼出来。完整原文照样保留，供维护者排查：

* 运行中的异常：E1 运行归档本来就保存（``failures.json``、``progress.json``、调用记录）；审计编号指向“运行编号 / 分身 / 记录文件”。
* 预检查与编译时的拒绝（还没有运行归档）：写进编译目录下的 ``diagnostics/<审计编号>.json``（服务端内部目录，不是公开响应）。
  审计编号由类别与原文的 SHA-256 前 16 位组成：同一原文得到同一编号，编号本身不泄露原文。

没有列在这里的类别不会出现在公开消息里：E1 新增了状态而这里没有登记时，测试会失败（不静默显示原文）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from common.life_aggregation_contracts import EntryRecord
from common.life_planning_contracts import RunProgress as E1RunProgress
from common.product_contracts import ProductIssue, PublicExplanation
from product.answers import issue

_NOT_IN_MEAN = "不进完成者平均；城市的模拟缺失范围把它当作未知（0 到 1 之间都保留），不写成生活出局"

#: 分身类别 / 缺失原因 → （公开类别代码，原因，影响，下一步）。键与 E1 的 C / E / M 与 ``MissingReason`` 一一对应。
CLONE_TEXT: dict[str, tuple[str, str, str, str]] = {
    "C": (
        "complete",
        "三年生活正常完成，实际评分与期末估值都完整",
        "计入这座城完成且可评价分身的平均",
        "看逐年生活事实；这是合成模型下的结果，不是现实预测",
    ),
    "E": (
        "life_exit",
        "确认的生活出局：付不起最低住房与食品",
        "不进完成者平均，也不进模拟缺失范围；出局比例以原计划人数为分母",
        "看事件链了解经过（环境记录，不是因果证明）",
    ),
    "not_run": (
        "not_run",
        "这个分身没有运行或没有返回结果（例如批次在轮到它之前暂停，或全局调用上限用尽）",
        _NOT_IN_MEAN,
        "批次的问题处理之后，用新的运行重跑",
    ),
    "plan_or_execution": (
        "plan_or_execution_incomplete",
        "年度计划没有完成（例如规划修订次数用完、计划始终不合法）",
        _NOT_IN_MEAN,
        "这是规划或模拟的问题，不是生活出局；改进规划器或说明之后重跑",
    ),
    "data_missing": (
        "data_missing",
        "结算要用的城市资料缺失或不完整",
        _NOT_IN_MEAN,
        "补齐资料之后重跑",
    ),
    "score_incomplete": (
        "score_incomplete",
        "生活正常结束，但有月份的评分资料未知（有正权重的项目资料缺失）",
        _NOT_IN_MEAN,
        "补齐评分资料之后重算",
    ),
    "feedback_blocked": (
        "feedback_blocked",
        "上一年评分不完整，下一年的精力额度无法确定，生活没有继续",
        _NOT_IN_MEAN,
        "补齐评分资料之后重跑",
    ),
    "stopped": (
        "stopped",
        "疾病之后应急安排不足或治疗资料缺口，模拟在那个月停止（模拟的支持边界，不是生活出局）",
        _NOT_IN_MEAN,
        "看事件记录；改进应急安排或补齐治疗资料之后重跑",
    ),
    "valuation_incomplete": (
        "valuation_incomplete",
        "实际生活完整，但期末估值不完整或没有可用的参考分支",
        _NOT_IN_MEAN,
        "这是评价环节的问题；补齐资料或参考模板之后重算",
    ),
    "input_config_program_error": (
        "program_error",
        "模拟程序或配置出错，这个分身没有完成（不是生活出局）",
        _NOT_IN_MEAN,
        "维护者按审计编号查看内部记录、修复之后用新的运行重跑；这里不显示内部错误内容",
    ),
}

#: 缺失原因 → 内部记录在运行归档里的位置（审计编号用；不含内容）。
_RECORD_OF = {
    "input_config_program_error": "failures.json",
    "not_run": "progress.json",
    "plan_or_execution": "progress.json",
}

_HALT_TEXT: dict[str, str] = {
    "service_unavailable": "模型服务暂时不可用（超时、断连、限流或服务端错误），批次停下",
    "credentials": "模型服务的凭证不可用，批次停下",
    "account": "模型服务账户的状态不允许调用，批次停下",
    "bad_request": "模型服务不支持这次请求的配置，批次停下",
    "budget": "付费预算不足以发出下一次请求，批次停下",
    "input_limit": "下一份请求超过了输入长度上限，批次停下（不删减事实）",
    "uncertain_send": "上一次请求可能已经发出但结果没有可靠落盘，为免重复付费，批次停下",
    "request_check": "最终请求没有通过信息隔离检查，批次停下",
    "model_mismatch": "服务返回的模型与设定不同，回复不能用于规划，批次停下",
}


def clone_explanation(entry: EntryRecord, run_id: str) -> PublicExplanation:
    """一个名单项的公开说明：只看 E1 的类别与缺失原因，不读 ``entry.detail``（那里可能有异常原文）。"""
    key = entry.category if entry.category in ("C", "E") else (entry.missing_reason or "")
    if key not in CLONE_TEXT:
        raise KeyError(
            f"E1 的类别或缺失原因 {key!r} 没有登记公开说明：先在 product.public_errors 登记，不能显示内部原文"
        )
    category, reason, impact, action = CLONE_TEXT[key]
    ref = None
    if entry.category == "M":
        record = _RECORD_OF.get(entry.missing_reason or "", "evaluations/")
        ref = f"run:{run_id} clone:{entry.clone_id} record:{record}"
    return PublicExplanation(
        category=category, reason_zh=reason, impact_zh=impact, next_action_zh=action, audit_ref=ref
    )


#: 未正常完成的运行状态 → （公开类别，原因，这种状态本身的影响，维护者要做的事）。报告有没有生成、能不能恢复两句**不写死在这里**，
#: 由 ``progress_pause`` 按运行归档的实际情况补上：E1 的程序错误暂停与调用上限用尽会把名单跑完并写出 C / E / M 报告，停下与中断
#: 不写报告；恢复规则在 E1（``resume_run``），这里只照实转述。
_PAUSE_TEXT: dict[str, tuple[str, str, str, str]] = {
    "paused": (
        "batch_paused_program_error",
        "批次因程序或配置问题暂停：出错的分身记为程序错误，还没轮到的分身保留为未运行",
        "这两类分身都是模拟缺失（不是生活出局），不进完成者平均",
        "维护者按审计编号查看内部记录并修复",
    ),
    "budget_exhausted": (
        "call_limit_reached",
        "全局调用上限用尽，后面的分身没有运行",
        "没有运行的分身记为未运行（模拟缺失），不写成生活出局",
        "由所有者决定是否提高上限",
    ),
    "interrupted": (
        "interrupted",
        "运行在两次规划调用之间中断",
        "已经提交的年份保留在归档里",
        "",
    ),
}


def progress_pause(
    p: E1RunProgress, *, report_available: bool, resumable: bool
) -> PublicExplanation | None:
    """运行进度的公开说明：只看进度的结构化状态（``status``、``halt_kind``），不读 ``pause_reason`` 原文。

    “评价与汇总生成了没有”“能不能恢复”按调用方传入的实际情况写（``report_available`` 是归档里有没有报告，``resumable`` 是 E1
    的恢复规则允不允许），所以同一份进度消息不会一边说有报告、一边说没生成。"""
    ref = f"run:{p.run_id} record:progress.json"
    if p.status in ("completed", "running"):
        return None
    if p.status == "halted":
        kind = p.halt_kind or ""
        if kind not in _HALT_TEXT:
            raise KeyError(f"批次停下的原因 {kind!r} 没有登记公开说明")
        category, reason = f"batch_halted_{kind}", _HALT_TEXT[kind]
        impact, step = "不是生活结果", "维护者处理服务、凭证、预算或请求的问题"
        audit = ref
    elif p.status in _PAUSE_TEXT:
        category, reason, impact, step = _PAUSE_TEXT[p.status]
        audit = f"{ref}, failures.json" if p.status == "paused" else ref
    else:
        raise KeyError(f"运行状态 {p.status!r} 没有登记公开说明")
    report = (
        "评价与汇总已经生成，可以查看结果（C / E / M 按上面的分类）"
        if report_available
        else "还没有生成评价与汇总，不擅自给出分类或分数"
    )
    resume = (
        "可以恢复同一次运行（恢复前逐条核对归档，已用的调用次数与预算不清零）；系统不会替分身生成计划"
        if resumable
        else "这次运行不能恢复，处理之后用新的运行重跑"
    )
    return PublicExplanation(
        category=category,
        reason_zh=reason,
        impact_zh=f"{impact}；{report}",
        next_action_zh=f"{step}；{resume}" if step else resume,
        audit_ref=audit,
    )


def record_internal(
    directory: Path, kind: str, detail: str, context: dict[str, Any] | None = None
) -> str:
    """把完整原文写进内部诊断目录，返回不含内容的审计编号（类别 + 原文 SHA-256 前 16 位）。"""
    digest = hashlib.sha256(f"{kind}\n{detail}".encode()).hexdigest()[:16]
    ref = f"{kind}-{digest}"
    path = Path(directory) / "diagnostics" / f"{ref}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"audit_ref": ref, "kind": kind, "detail": detail, "context": context or {}},
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return ref


def e1_refusal_issue(directory: Path, detail: str) -> ProductIssue:
    """E1 运行前检查拒绝：原文只进内部诊断，公开的是类别说明与审计编号。"""
    ref = record_internal(directory, "e1-preflight-refused", detail)
    return issue(
        "e1_preflight_refused",
        "run",
        "运行前的一致性检查没有通过：编译出的输入、资料包或 E1 配置之间有矛盾（这不是你的答案的问题）",
        "这次比较不能启动；系统不会删掉城市或分身再声称执行了原比较",
        "请维护者按审计编号查看内部诊断并修复资料或配置，之后重新预检查",
        audit_ref=ref,
    )


def profile_contract_issue(directory: Path, detail: str) -> ProductIssue:
    """确认画像没通过 E1 画像契约（审阅本应已经拦下）：原文只进内部诊断。"""
    ref = record_internal(directory, "profile-contract-rejected", detail)
    return issue(
        "profile_contract_rejected",
        "confirmed",
        "确认画像没有通过 E1 画像契约的检查（例如同一项需求有两条硬要求）",
        "不能编译成 E1 输入，这次比较不能启动",
        "回到审阅页修正之后重新确认；如果审阅页没有提示，请维护者按审计编号排查",
        audit_ref=ref,
    )
