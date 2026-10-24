"""外层流程状态：只有流程信息，没有生活状态。

checkpoint 里只存：流程编号与版本、已确认画像与输入（资料包 + 模拟设置）的引用和内容身份、预检查的引用与结论、
用户对“开始这次模拟”的确认、幂等键、E1 运行编号、结果引用、当前阶段、待处理事项。钱、病情、资格、年度评分等只在
E1 的运行归档里；需要展示时经 E3 投影现读，不在这里另存一份可以单独修改的副本。

所有值都是普通 JSON 类型（字符串、数字、布尔、列表、字典），这样 checkpoint 换进程读回时不需要反序列化任何业务对象，
也就不会绕过 E1 的年度封存检查。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict

# 流程版本：改了节点、边、状态字段的含义就换版本；旧版本的流程不能用新代码接着跑（runtime 拒绝，不迁移、不猜）。
FLOW_VERSION = "lg-product-flow-v0"


def fingerprint(obj: Any) -> str:
    """规范 JSON（键排序、紧凑分隔、保留中文）的 SHA-256。只用于流程自己的身份（例如默认幂等键）。"""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContentRef:
    """一份外部对象的引用：种类、在所属服务里的编号、内容身份（SHA-256 十六进制）。

    ``ref_id`` 只在所属服务里有意义（E3 的确认编号、资料包编号、预检查目录、E1 的运行编号……），流程不解析它；
    ``fingerprint`` 是那份内容的身份，内容变了身份就变，流程靠它判断“确认的还是不是同一份东西”。
    """

    kind: str
    ref_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.kind or not self.ref_id:
            raise ValueError("ContentRef 的 kind 与 ref_id 不能为空")
        if len(self.fingerprint) != 64 or any(
            c not in "0123456789abcdef" for c in self.fingerprint
        ):
            raise ValueError(
                f"ContentRef 的 fingerprint 必须是 64 位小写十六进制：{self.fingerprint!r}"
            )

    def as_state(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_state(cls, raw: dict[str, str]) -> ContentRef:
        return cls(kind=raw["kind"], ref_id=raw["ref_id"], fingerprint=raw["fingerprint"])


Stage = Literal[
    "checking",  # 正在做运行前检查
    "awaiting_confirmation",  # 检查通过，等用户确认“按这些条件开始这次模拟”
    "blocked",  # 检查没通过，等用户修改条件或取消
    "declined",  # 用户拒绝或取消，流程结束，没有启动模拟
    "submitting",  # 已确认，正在幂等登记运行
    "running",  # 正在启动或恢复 E1
    "needs_attention",  # E1 或提交停在需要人处理的状态，流程不自动重试
    "delivered",  # 结果已经可读；外层结束不代表每个分身都正常完成
    "abandoned",  # 维护者放弃这次运行（E1 的归档原样保留）
]

STAGE_ZH: dict[str, str] = {
    "checking": "正在做运行前检查",
    "awaiting_confirmation": "等待你确认是否开始这次模拟",
    "blocked": "运行前检查没有通过，需要修改条件",
    "declined": "已取消，没有开始模拟",
    "submitting": "正在登记这次模拟",
    "running": "模拟进行中",
    "needs_attention": "已暂停，需要处理",
    "delivered": "结果已生成",
    "abandoned": "已放弃这次运行",
}

TERMINAL_STAGES = frozenset({"declined", "delivered", "abandoned"})


class WorkflowState(TypedDict, total=False):
    """LangGraph 的状态。每个字段都是普通 JSON 值；ContentRef 存成 ``as_state()`` 的字典。"""

    flow_version: str
    workflow_id: str
    stage: str
    profile: dict[str, str]  # 已确认画像（E3 ConfirmedProfile）的引用
    inputs: dict[str, str]  # 资料包 + 模拟设置（城市、政策、模型、预算）的组合引用
    idempotency_key: str  # 稳定的幂等键；同一个键只能对应同一份预检查
    preflight: (
        dict[str, str] | None
    )  # 最近一次预检查的引用（它的身份绑定画像、输入、政策、模型与预算）
    preflight_runnable: bool
    preflight_block: str | None  # 不能运行时的公开代码
    confirmation: dict[str, Any] | None  # 用户对“开始这次模拟”的确认，绑定预检查身份
    run_id: str | None  # 绑定的 E1 运行；登记之后不能换
    archive: dict[str, str] | None  # E1 运行归档的引用（只读核对用）
    result: dict[str, str] | None  # 结果的引用（内容身份）；结果本身经 E3 投影现读
    attention: dict[str, str] | None  # 需要处理的事项：公开代码、审计编号、处理后回到哪个节点
    notice: str | None  # 最近一条提示的公开代码（例如确认已过期）


# 需要处理的事项与提示：公开代码 → 给用户看的中文。流程只公开这些固定文字，不透传任何内部异常原文。
PUBLIC_CODES: dict[str, str] = {
    # 需要处理
    "preflight_blocked": "运行前检查没有通过，本次模拟不能开始；请按检查结果修改条件后重新检查。",
    "reference_stale": "个人条件或模拟设置在检查之后发生了变化；请用最新的内容重新检查并确认。",
    "identity_conflict": "这个提交编号已经对应另一份运行前检查，不能用它开始不同的模拟。",
    "run_binding_conflict": "找到的运行与本流程登记的运行不一致，已停止；没有开始新的模拟。",
    "service_unavailable": "规划服务暂时不可用，模拟已按规则在年度边界暂停；稍后可以恢复，已保存的回复不会重新请求。",
    "uncertain_send": "有一次规划请求是否已经发出无法确认；为避免重复收费，模拟已暂停，需要维护者核对。",
    "budget_blocked": "本轮预算不足以继续；没有自动追加预算。",
    "model_mismatch": "服务返回的模型与设定不符，模拟已停止；没有自动重试，也没有更换模型。",
    "archive_conflict": "运行记录与冻结的输入对不上，已停止；原始记录没有被改动。",
    "result_unavailable": "模拟已结束，但结果暂时无法读取；没有重新模拟。",
    "not_wired": "这项服务尚未接入（原型阶段），没有开始模拟。",
    "run_partially_initialized": "运行目录在启动时只建了一半（进程中断）；不能猜它的状态，也不能删掉重建，需要维护者核对。",
    "run_halted": "模拟已按 E1 的规则停下，需要维护者核对后才能继续；流程不会自动重试，也不会再请求规划。",
    "real_planner_not_supported": "这个原型只接入了离线 mock 规划器；真实模型运行需要按 E1-7B 的规则另行授权预算后接入，没有调用任何模型。",
    "workspace_conflict": "这个运行名称已经用于另一组条件；请换一个名称后重新检查，已登记运行的编译输入不会被覆盖。",
    # 托管服务（E3C）
    "run_deleted": "这次运行已被用户删除：不再发出新的模型请求，也不会继续或恢复。",
    "paid_calls_disabled": "服务没有开启付费模型调用：没有开始模拟，也没有换成离线规划器。",
    "credentials_missing": "真实模型的凭证不可用：没有发送任何请求，也没有换成离线规划器。",
    "start_refused": "启动前的最后核对没有通过（次数预留已失效或运行已停止）：没有开始模拟。",
    "request_too_large": "首年规划请求超过规划器的单次输入上限：没有开始模拟，没有发出请求，也没有删减选项或换模型。",
    "internal_error": "系统内部出错，已记录供维护者查看；没有开始新的模拟。",
    # 提示
    "confirmation_stale": "你确认的不是最新一次运行前检查；请查看最新检查结果后再确认。",
    "inputs_changed": "确认之后重新核对时发现条件有变化，已重新检查；请再确认一次。",
    "already_confirmed": "这次模拟已经确认过，不需要重复确认。",
    "nothing_pending": "现在没有等待你处理的事项。",
    # 拒绝操作（流程状态不变）
    "workflow_not_found": "找不到这个流程编号。",
    "workflow_exists": "这个流程编号已经用于另一组条件；请换一个流程编号，或接着原来的流程。",
    "flow_version_mismatch": "这个流程由旧版本创建，当前版本不能接着运行；原记录保持不变。",
    "external_tracing_enabled": "检测到外部追踪上传已开启；本原型不允许把流程记录上传到外部平台，请先关闭。",
    "invalid_request": "请求的内容不完整或格式不对。",
    "idempotency_key_invalid": "提交编号不符合要求（长度或格式）；请换一个提交编号，没有登记任何运行。",
}

ATTENTION_RESUME_NODES = frozenset({"submit", "run_life", "deliver"})


def public_text(code: str | None) -> str | None:
    if code is None:
        return None
    return PUBLIC_CODES.get(code, PUBLIC_CODES["internal_error"])
