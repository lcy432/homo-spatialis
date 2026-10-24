"""每次模拟的工作目录与流程接线（worker 用；HTTP 进程不 import 这里的执行部分）。

一次运行意图一个独立目录（只在服务数据目录里，路径登记在 ``ServiceContext.run_dir``）：

    runs/<意图编号>/ws/               E3 工作目录：资料包与基础样例的快照（每个文件的 SHA-256 记在服务库）、已确认画像、
                                     本次输入、编译产物、E1 运行归档、这次运行自己的新账本（``ws/budget/``）
    runs/<意图编号>/workflow.sqlite   这次模拟的 LangGraph checkpoint
    runs/<意图编号>/diagnostics/      流程的内部诊断（原文只在这里，对外只有公开代码与支持编号）

流程照旧走 ``workflows.runtime.WorkflowRuntime``（预检查 → 等确认 → 再核对 → 登记 → E1 → 投影），本模块只把服务的东西注入
``workflows.e3_adapters.E3Context``：规划器工厂（mock，或 ``LlmPlanner`` + ``make_transport`` + 本次运行的 ``RoundBudget``）、
发送许可与停止（服务库里串行裁决）、首次启动前把预留转为已使用、以服务库为唯一权威的提交登记。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentlayer.life_llm import LlmPlanner, make_transport
from agentlayer.life_mock import mock_planner_for
from common.config import ConfigError, ModelsConfig
from common.life_planning_contracts import LlmSettings
from common.product_contracts import ConfirmedProfile, PublicExplanation, RunProgress
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import RunWorld
from product.bridge import cost_status, progress
from product.execution import ExecutionSpec, service_pack
from product.service.actions import ServiceContext
from product.workspace import prepare_workspace, verify_snapshot
from workflows.e3_adapters import (
    RUN_REQUEST_VERSION,
    E3Context,
    E3Workspace,
    RunRequest,
    e3_services,
)
from workflows.ports import AttentionRequiredError
from workflows.runtime import WorkflowRuntime

#: 编译目录名与本次输入名（每个意图一个工作目录，名字固定）
LABEL = "svc"
TransportFactory = Callable[[LlmSettings], Any]


class ServiceRunControl:
    """``RunControl``：停止信号与逐次发送许可都问服务库（删除标记与许可在同一个库里串行裁决，没有批量预先批准）。"""

    def __init__(self, ctx: ServiceContext, intent_id: str) -> None:
        self.store, self.intent_id = ctx.store, intent_id

    def stop_requested(self) -> bool:
        return self.store.stop_requested(self.intent_id)

    def permit_send(self, key: str) -> bool:
        return self.store.permit_send(self.intent_id, key)


class ServiceSubmissionRegistry:
    """``bridge.SubmissionRegistry``：服务端 SQLite 是唯一的权威提交登记（没有第二份 JSON 登记）。运行编号在确认开始时已经写下；
    LangGraph 的提交节点在这里登记同一个编号，对不上就停在运行绑定冲突。"""

    def __init__(self, ctx: ServiceContext, intent_id: str) -> None:
        self.store, self.intent_id = ctx.store, intent_id

    def find(self, key: str) -> dict[str, str] | None:
        row = self.store.intent_row(self.intent_id)
        if row is None or row["workflow_key"] != key or row["registered_at"] is None:
            return None
        return {"run_id": row["run_id"], "preflight_fingerprint": row["preflight_fingerprint"]}

    def add(self, key: str, run_id: str, preflight_fingerprint: str) -> None:
        if not self.store.register_run(
            self.intent_id, key=key, run_id=run_id, preflight_fingerprint=preflight_fingerprint
        ):
            raise AttentionRequiredError("run_binding_conflict", audit_ref=f"svc-run-{run_id}")


def planner_factory(
    ctx: ServiceContext,
    control: ServiceRunControl,
    transport_factory: TransportFactory | None,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[RunWorld], Any]:
    """按运行配置造规划器。真实模型：付费开关、模型角色表、凭证逐项核对，任何一项不行都停在公开代码——不换成 mock。账本是这次
    运行工作目录里的 ``budget/``（调用发生在工作目录里，``RoundBudget`` 建立时就定成绝对路径）。"""

    def build(world: RunWorld) -> Any:
        spec = world.config.planner
        if spec.identity.kind == "mock":
            if ctx.spec.planner_kind != "mock":
                raise AttentionRequiredError("archive_conflict")
            return mock_planner_for(spec)
        if ctx.spec.planner_kind != "llm" or spec.llm is None:
            raise AttentionRequiredError("archive_conflict")
        if not ctx.config.live_calls_enabled:
            raise AttentionRequiredError("paid_calls_disabled")
        settings = spec.llm
        role = ModelsConfig.load(ctx.repo / ctx.config.models_config).spec(settings.model_role)
        if (role.provider, role.base_url, role.model) != (
            settings.provider,
            settings.base_url,
            settings.model,
        ):
            raise AttentionRequiredError("model_mismatch")
        if transport_factory is not None:
            transport = transport_factory(settings)
        else:
            try:
                key = role.resolve().api_key
            except ConfigError as exc:
                raise AttentionRequiredError("credentials_missing") from exc
            transport = make_transport(settings, api_key=key)
        if world.config.paid_budget is None:
            raise AttentionRequiredError("archive_conflict")
        return LlmPlanner(
            settings,
            transport=transport,
            budget=RoundBudget(world.config.paid_budget),
            control=control,
            sleep=sleep,
        )

    return build


class RunDir:
    """一个意图的工作目录与流程（路径都是绝对路径）。"""

    def __init__(self, ctx: ServiceContext, intent_id: str) -> None:
        self.ctx, self.intent_id = ctx, intent_id
        self.root = ctx.run_dir(intent_id)
        self.ws_root = self.root / "ws"

    # ---- 快照与输入 ----
    def prepare(self, confirmed: ConfirmedProfile, spec: ExecutionSpec) -> dict[str, str]:
        """准备（或在重新检查时刷新）工作目录：复制资料包与基础样例并记下快照，写已确认画像与本次输入。只用于还没有确认开始的意图。"""
        snapshot = prepare_workspace(self.ws_root, self.ctx.repo, models=spec.planner_kind == "llm")
        ws = E3Workspace(self.ws_root)
        ws.save_confirmed(confirmed)
        ws.save_request(LABEL, self.request(spec))
        return snapshot

    def request(self, spec: ExecutionSpec) -> RunRequest:
        return RunRequest(
            version=RUN_REQUEST_VERSION,
            label=LABEL,
            pack_path=spec.pack_path,
            pack_fingerprint=spec.pack_fingerprint,
            repeat_indexes=spec.repeat_indexes,
            execution=spec,
            note="E3C 托管服务的一次模拟（正式运行设置由服务配置产生）",
        )

    def refs(self, confirmed_id: str) -> tuple[Any, Any]:
        ws = E3Workspace(self.ws_root)
        return ws.confirmed_ref(confirmed_id), ws.request_ref(LABEL)

    def saved_request(self) -> RunRequest:
        path = self.ws_root / "workflow/requests" / f"{LABEL}.json"
        return RunRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def verify(self, snapshot: dict[str, str]) -> None:
        verify_snapshot(self.ws_root, snapshot)

    # ---- 流程 ----
    def runtime(
        self,
        *,
        transport_factory: TransportFactory | None = None,
        faults: set[str] | None = None,
        on_start: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> WorkflowRuntime:
        ctx = self.ctx
        control = ServiceRunControl(ctx, self.intent_id)
        e3 = E3Context(
            E3Workspace(self.ws_root),
            faults=faults,
            planner_factory=planner_factory(ctx, control, transport_factory, sleep),
            control=control,
            on_start=on_start,
            registry=ServiceSubmissionRegistry(ctx, self.intent_id),
            service="hosted_service",
            workers=ctx.config.workers,
        )
        return WorkflowRuntime(
            self.root / "workflow.sqlite",
            e3_services(e3.ws, context=e3),
            diagnostics_dir=self.root / "diagnostics",
        )

    # ---- 公开快照（只读，绝对路径；监测线程用，不切换工作目录、不推进流程） ----
    def archive(self, run_id: str) -> Path:
        return self.ws_root / "archive" / run_id

    def progress_snapshot(self, run_id: str, updated_at: str) -> RunProgress | None:
        run_dir = self.archive(run_id)
        if not (run_dir / "progress.json").is_file():
            return None
        req = self.saved_request()
        assert req.execution is not None
        pack = service_pack(self.ws_root / req.pack_path, req.execution)
        return progress(
            pack,
            run_dir,
            execution=req.execution,
            ledger_dir=self.ws_root / "budget",
            updated_at=updated_at,
        )

    def cost_summary(self) -> dict[str, Any] | None:
        """删除后保留的最小费用摘要（金额、尝试次数；不确定的金额照原样保留，不改成 0）。mock 运行没有账本。"""
        try:
            req = self.saved_request()
        except (OSError, ValueError):
            return None
        spec = req.execution
        if spec is None or spec.planner_kind == "mock":
            return {"planner": "mock", "cost": "0"}
        c = cost_status(spec, self.ws_root / "budget", calls_used=0, call_limit=0)
        return {
            "planner": "llm",
            "currency": c.currency,
            "price_version": c.price_version,
            "max_cost": c.max_cost,
            "estimated_cost": c.estimated_cost,
            "settled_cost": c.settled_cost,
            "uncertain_cost": c.uncertain_cost,
            "in_flight_cost": c.in_flight_cost,
            "service_attempts": c.service_attempts,
            "uncertain_attempts": c.uncertain_attempts,
            "note": "应用侧按报价的估算，不是供应商账单；删除只清掉本地内容，不代表供应商那边的记录被删除",
        }

    # ---- 清理（只删登记过的路径） ----
    def remove(self) -> None:
        remove_tree(self.root, self.ctx.data_root / "runs")


def remove_tree(path: Path, allowed_root: Path) -> None:
    """删除一个登记过的运行目录。只接受 ``allowed_root`` 下面的一层目录；拒绝路径穿越与链接（包括 Windows 的目录联接），不跟随链接。"""
    path, allowed_root = Path(path), Path(allowed_root).resolve()
    if path.parent.resolve() != allowed_root or path.name in ("", ".", ".."):
        raise ValueError("只删除服务数据目录里登记的运行目录")
    if not path.exists() and not path.is_symlink():
        return
    if _is_link(path):
        raise ValueError("运行目录是链接：拒绝删除（不跟随链接）")
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames + filenames:
            if _is_link(Path(dirpath) / name):
                raise ValueError("运行目录里有链接：拒绝删除（不跟随链接），等维护者核对")

    def _retry(func: Callable[..., Any], p: str, _exc: Any) -> None:
        os.chmod(p, stat.S_IWRITE)  # Windows：只读文件先去掉只读再删
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=_retry)


def _is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)
    return bool(is_junction and is_junction())


def explanation(code: str, *, support: str | None = None) -> PublicExplanation:
    """服务流程的公开说明（固定中文，不透传内部原文）。"""
    reason, impact, step = EXPLANATIONS.get(code, EXPLANATIONS["internal_error"])
    return PublicExplanation(
        category=f"service_{code}" if code in EXPLANATIONS else "service_internal_error",
        reason_zh=reason,
        impact_zh=impact,
        next_action_zh=step,
        audit_ref=support,
    )


#: 公开代码 → (原因, 影响, 下一步)。都是固定文字。
EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "preflight_blocked": (
        "运行前检查没有通过（原因逐条写在运行前说明里）",
        "这次模拟不能开始，没有使用次数",
        "按说明修改答案，重新审阅、确认画像后再准备一次",
    ),
    "reference_stale": (
        "确认的画像或服务的运行设置在检查时对不上",
        "这次模拟不能开始，没有使用次数",
        "重新审阅并确认画像后再准备一次",
    ),
    "inputs_changed": (
        "确认开始之后、启动之前的最后核对发现运行条件变了",
        "没有开始模拟，预留的次数已经退回",
        "取消这次准备，重新准备并查看新的运行前说明",
    ),
    "snapshot_broken": (
        "这次模拟绑定的资料快照缺失或被改动",
        "没有开始模拟（预留的次数已经退回）；系统不会拿新资料冒充旧资料",
        "取消这次准备后重新准备；问题一直出现时请联系维护者并提供支持编号",
    ),
    "paid_calls_disabled": (
        "服务现在没有开启付费模型调用",
        "没有开始模拟，预留的次数已经退回；系统不会悄悄换成离线规划器",
        "等服务开启后，取消这次准备并重新准备",
    ),
    "credentials_missing": (
        "真实模型的凭证现在不可用",
        "没有发出任何请求，预留的次数已经退回；系统不会换成离线规划器",
        "等维护者处理后，取消这次准备并重新准备",
    ),
    "start_refused": (
        "启动前的最后核对没有通过",
        "没有开始模拟",
        "查看这次运行的状态；需要的话重新准备",
    ),
    "service_unavailable": (
        "模型服务暂时不可用，模拟按规则在年度边界或两份候选之间暂停",
        "已经保存的回复不会重新请求；次数不会因此另扣",
        "稍后点“恢复”继续同一次运行",
    ),
    "uncertain_send": (
        "有一次模型请求是否已经发出无法确认",
        "为了不重复付费，模拟已暂停；这次的预留费用照原样保留",
        "不需要重新填写；等维护者核对后处理（不会自动重发）",
    ),
    "budget_blocked": (
        "这次运行的费用或请求次数到了上限",
        "模拟停下，不会自动追加预算；已经完成的部分保留",
        "查看进度；需要维护者决定后续",
    ),
    "model_mismatch": (
        "模型服务返回的型号与设定不符",
        "那份回复不用于规划；模拟停下，不自动换模型",
        "等维护者核对",
    ),
    "archive_conflict": (
        "运行记录与绑定的输入对不上",
        "模拟停下，原始记录没有被改动",
        "等维护者核对",
    ),
    "run_binding_conflict": (
        "找到的运行与这次确认登记的运行不一致",
        "模拟停下，没有开始新的模拟",
        "等维护者核对",
    ),
    "run_partially_initialized": (
        "运行目录在启动时只建了一半（进程中断）",
        "系统不猜它的状态，也不删掉重建",
        "等维护者核对",
    ),
    "run_halted": (
        "模拟按 E1 的规则停下",
        "流程不会自动重试，也不会再请求规划",
        "等维护者核对",
    ),
    "result_unavailable": (
        "模拟已结束，但结果暂时无法读取",
        "没有重新模拟",
        "等维护者核对",
    ),
    "internal_error": (
        "服务出现了内部问题",
        "这次模拟停在当前位置，没有重新开始，也没有重复使用次数",
        "等维护者处理；联系时请提供支持编号",
    ),
}
