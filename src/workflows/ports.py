"""流程调用的四个服务接口。真实实现见 ``e3_adapters``（接入 E3 / E1 的正式入口），流程本身的测试用 ``synthetic`` 的替身。

接口按“谁负责”划分：

* ``PreflightPort``（E3）：对已确认画像 + 输入做运行前检查，给出预检查的引用（内容身份绑定画像、城市、资料、政策、模型、
  预算）与能否运行；``describe`` 返回给用户看的公开内容（E3 的 ProductPreflight），流程不解析它。
* ``SubmissionPort``（E3）：用幂等键登记一次运行。同一个键 + 同一份预检查 → 同一个运行编号；同一个键 + 不同预检查 → 拒绝。
  只登记，不启动模拟。
* ``LifeRunPort``（E1，经 E3 的服务适配）：按运行编号查询、启动、恢复 E1 运行。查询只读；启动与恢复都交给 E1 原有入口，
  E1 自己负责年度候选、修订、服务重试、预算账本与归档，流程不另加重试。
* ``ProjectionPort``（E3）：把 E1 的运行进度与结果投影成公开结构（RunProgress / ProductResult），流程不重算任何分数。

所有接口在遇到“需要人处理”的情况时抛 ``AttentionRequiredError``（带公开代码与可选的审计编号），不要返回半成品；其他异常
视为程序错误，由 runtime 记进本地诊断、对外只显示 ``internal_error``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from workflows.state import ContentRef


class AttentionRequiredError(Exception):
    """服务停在需要人处理的状态（提交冲突、发送状态不确定、预算阻断、模型不符、归档冲突……）。

    ``code`` 必须是 ``state.PUBLIC_CODES`` 里的公开代码；``audit_ref`` 是服务自己的审计编号（不含异常原文或本机路径），
    维护者用它去查内部记录。
    """

    def __init__(self, code: str, *, audit_ref: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.audit_ref = audit_ref


class NotWiredError(Exception):
    """接口还没有接入（占位）：流程停在 ``not_wired``，不登记任何东西。将来接新服务时，没接好的接口抛它。"""


@dataclass(frozen=True)
class PreflightOutcome:
    ref: ContentRef  # kind = "preflight"
    runnable: bool
    block_code: str | None = (
        None  # runnable=False 时的公开代码（preflight_blocked / reference_stale / not_wired）
    )

    def __post_init__(self) -> None:
        if self.ref.kind != "preflight":
            raise ValueError("PreflightOutcome.ref 必须是 preflight 引用")
        if self.runnable == (self.block_code is not None):
            raise ValueError("能运行时不带 block_code，不能运行时必须带")


@dataclass(frozen=True)
class Receipt:
    run_id: str
    preflight_fingerprint: str
    newly_registered: bool  # 这一次调用是否新登记（重复提交为 False）


RunKind = Literal["not_started", "resumable", "completed", "needs_attention"]


@dataclass(frozen=True)
class RunInspection:
    """E1 运行的只读状态。

    * ``not_started``：已登记、还没有开始（没有任何规划调用或年度记录）；
    * ``resumable``：停在 E1 支持恢复的边界（年度之间、服务失败按规则暂停、进程在允许的位置中断）；
    * ``completed``：E1 已写完运行结果（评价与汇总）；
    * ``needs_attention``：发送状态不确定、预算阻断、模型不符、归档或身份冲突等，带公开代码。
    """

    run_id: str
    kind: RunKind
    archive: ContentRef | None = None
    code: str | None = None
    audit_ref: str | None = None

    def __post_init__(self) -> None:
        if (self.kind == "needs_attention") != (self.code is not None):
            raise ValueError("只有 needs_attention 带公开代码")


@dataclass(frozen=True)
class ResultView:
    ref: ContentRef  # kind = "result"；内容身份用来核对重复读取得到的是同一份结果
    public: dict[str, Any] = field(
        default_factory=dict
    )  # E3 的 ProductResult（公开结构），流程不解析


@runtime_checkable
class PreflightPort(Protocol):
    def check(self, profile: ContentRef, inputs: ContentRef) -> PreflightOutcome: ...

    def describe(self, preflight: ContentRef) -> dict[str, Any]: ...


@runtime_checkable
class SubmissionPort(Protocol):
    def submit(
        self, *, idempotency_key: str, preflight: ContentRef, confirmation: dict[str, Any]
    ) -> Receipt: ...


@runtime_checkable
class LifeRunPort(Protocol):
    def inspect(self, run_id: str) -> RunInspection: ...

    def start(self, run_id: str) -> RunInspection: ...

    def resume(self, run_id: str) -> RunInspection: ...


@runtime_checkable
class ProjectionPort(Protocol):
    def progress(self, run_id: str) -> dict[str, Any] | None: ...  # 还没有开始时为空，不编一个进度

    def result(self, run_id: str) -> ResultView: ...


@dataclass(frozen=True)
class WorkflowServices:
    preflight: PreflightPort
    submission: SubmissionPort
    life: LifeRunPort
    projection: ProjectionPort
    label: str = "unnamed"  # 写进诊断，区分合成替身与真实接入
