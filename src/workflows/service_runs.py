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
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from agentlayer.life_llm import LlmPlanner, make_transport, service_history
from agentlayer.life_mock import mock_planner_for
from common.config import ConfigError, ModelsConfig
from common.life_planning_contracts import LlmSettings
from common.life_planning_contracts import RunProgress as E1RunProgress
from common.product_contracts import ConfirmedProfile, RunProgress
from common.run_store import RunStore
from common.runlog import ProviderCallRecord
from orchestration.life_budget import RoundBudget, ledger_state, read_ledger
from orchestration.life_runner import (
    RunRefusedError,
    RunWorld,
    pending_input_checks,
    preflight_from_snapshot,
)
from product.bridge import cost_status, progress
from product.display.document import binding_document, capture
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
    *,
    expected: str,
) -> Callable[[RunWorld], Any]:
    """按运行配置造规划器。``expected`` 是这次运行保存的设置里的规划器种类（编译出的运行配置必须是同一种）。真实模型：付费开关、
    模型角色表、凭证逐项核对，任何一项不行都停在公开代码——不换成 mock。账本是这次运行工作目录里的 ``budget/``（调用发生在工作目录里，
    ``RoundBudget`` 建立时就定成绝对路径）。"""

    def build(world: RunWorld) -> Any:
        spec = world.config.planner
        if spec.identity.kind == "mock":
            if expected != "mock":
                raise AttentionRequiredError("archive_conflict")
            return mock_planner_for(spec)
        if expected != "llm" or spec.llm is None:
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

    def _planner_kind(self) -> str:
        execution = self.saved_request().execution
        if execution is None:
            raise AttentionRequiredError("archive_conflict")
        return execution.planner_kind

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
            planner_factory=planner_factory(
                ctx, control, transport_factory, sleep, expected=self._planner_kind()
            ),
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

    def progress_snapshot(
        self, run_id: str, updated_at: str, *, resumable: bool = False, running_now: bool = False
    ) -> RunProgress | None:
        """白名单进度快照。``resumable`` 是服务端的恢复许可（``resume_state``），公开进度与按钮用同一个结论；``running_now``
        表示监测线程在运行进行中写（见 ``product.bridge.progress``）。"""
        found = self.progress_with_display(
            run_id, updated_at, resumable=resumable, running_now=running_now
        )
        return None if found is None else found[0]

    def progress_with_display(
        self, run_id: str, updated_at: str, *, resumable: bool = False, running_now: bool = False
    ) -> tuple[RunProgress, dict[str, Any]] | None:
        """进度快照与它的显示绑定（同一次生成里记下的文字）。"""
        run_dir = self.archive(run_id)
        if not (run_dir / "progress.json").is_file():
            return None
        req = self.saved_request()
        assert req.execution is not None
        pack = service_pack(self.ws_root / req.pack_path, req.execution)
        snap, rec = capture(
            progress,
            pack,
            run_dir,
            execution=req.execution,
            ledger_dir=self.ws_root / "budget",
            updated_at=updated_at,
            resumable=resumable,
            running_now=running_now,
        )
        return snap, binding_document(
            snap.model_dump(mode="json"), rec, pack_fingerprint=pack.fingerprint
        )

    def display_doc(self, name: str) -> str | None:
        """适配器写下的显示绑定（``preflight-<身份前 16 位>`` / ``result-<运行编号>``）；没有就是 ``None``（显示为缺失，不在读取时补）。"""
        path = self.ws_root / "workflow" / "display" / f"{name}.json"
        try:
            return json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False)
        except (OSError, ValueError):
            return None

    def resume_state(self, run_id: str) -> tuple[bool, str | None]:
        """这次运行停下之后，用户点“恢复”能不能真的继续（只读：读 E1 的进度、请求明细与这次运行的账本，不调用规划器、不写文件）。
        判断与 E1 恢复时 ``LlmPlanner`` 的做法一致：每个正在规划的候选按 E1 的 ``service_history`` 看——回复已经可靠落盘就复用（能继续）；
        有一次可能已经发出、结果没落盘 → 发送不确定；返回型号不符 → 型号不符；这份候选的服务尝试（``1 + service_retries``）已经
        用完 → 尝试用完（E1 不会再发，也不清零计数）；账本不够再预留一次 → 预算不够。下一次预留的输入估计：有服务尝试的候选用它
        上一次尝试记下的估计；还没有任何服务尝试的候选（例如并行时别的分身停下、它停在发送之前）按 E1 恢复后要发的那份请求重建并
        估计（``pending_input_checks``，只读）——没有发送历史不等于没有输入（E3C 收尾复核 R4）。返回（能否继续，不能继续时的公开代码）。"""
        run_dir = self.archive(run_id)
        try:
            p = E1RunProgress.model_validate_json(
                (run_dir / "progress.json").read_text(encoding="utf-8")
            )
            spec = self.saved_request().execution
        except (OSError, ValueError):
            return False, "archive_conflict"
        if p.status not in ("running", "interrupted", "halted") or spec is None:
            return False, "run_halted"
        if p.status == "halted" and p.halt_kind not in (None, "service_unavailable"):
            return False, {
                "budget": "budget_blocked",
                "uncertain_send": "uncertain_send",
                "model_mismatch": "model_mismatch",
            }.get(p.halt_kind, "run_halted")
        settings = spec.llm_settings
        if settings is None:
            return True, None  # 离线 mock：没有服务尝试与账本，E1 从边界接着跑
        records = [
            ProviderCallRecord.model_validate(r)
            for r in RunStore(run_dir).records("provider_calls")
        ]
        allowed = 1 + settings.service_retries
        estimates: list[int] = []
        fresh: list[str] = []
        for pending in p.pending:
            request_id = (
                f"{p.run_id}/{pending.clone_id}/y{pending.year_index}/a{pending.attempts_used + 1}"
            )
            hist = service_history(
                records,
                request_id=request_id,
                run_id=p.run_id,
                settings=settings,
                price_version=spec.price_version,
            )
            if hist.problems:
                return False, "archive_conflict"
            if hist.pending is not None:
                return False, "uncertain_send"
            if hist.rejected is not None:
                return False, "model_mismatch"
            if hist.usable is not None:
                continue
            if len(hist.attempts) >= allowed:
                return False, "attempts_exhausted"
            if hist.attempts:
                estimates += [a.intent.input_tokens_estimate or 0 for a in hist.attempts[-1:]]
            else:
                fresh.append(request_id)
        if fresh or not p.pending:
            found = self._next_request_estimates(run_dir, settings, fresh)
            if isinstance(found, str):
                return False, found
            estimates += found
        if not self._budget_fits(spec, settings, max(estimates, default=0)):
            return False, "budget_blocked"
        return True, None

    def _next_request_estimates(
        self, run_dir: Path, settings: LlmSettings, request_ids: list[str]
    ) -> list[int] | str:
        """还没有发送历史、恢复之后要发的请求的输入估计（E1 的同一份构造与估计，只读）。进度里没有正在规划的候选（下一份请求还没有
        构造出来），或者重建失败但不是归档冲突时，按单次输入上限估计——E1 对任何一份请求的预留都不会超过它（超过就在发送前拒绝）——
        保守，不当作 0。归档冲突 → ``archive_conflict``；那份请求超过单次输入上限 → ``run_halted``（恢复之后 E1 也会在发送前停下）。"""
        cap = settings.max_input_tokens
        if not request_ids:
            return [cap]
        try:
            checks = pending_input_checks(preflight_from_snapshot(run_dir), run_dir)
        except RunRefusedError:
            return "archive_conflict"
        except Exception:  # 估计不出来就按上限，不放行一个可能马上停下的“恢复”
            return [cap] * len(request_ids)
        out: list[int] = []
        for request_id in request_ids:
            check = checks.get(request_id)
            if check is None:
                out.append(cap)
            elif not check.fits:
                return "run_halted"
            else:
                out.append(check.estimate)
        return out

    def _budget_fits(self, spec: ExecutionSpec, settings: LlmSettings, estimate: int) -> bool:
        """这本账还够不够再预留一次（按 E1 的预留写法：输入估计 × 输入单价 + 输出上限 × 输出单价，向上取到 0.000001 元）。"""
        found = read_ledger(self.ws_root / "budget")
        if found is None:
            return True
        header, events = found
        cur = (spec.currency or "CNY").lower()
        reserves, closes = ledger_state(events)
        committed = Decimal(0)
        for key, r in reserves.items():
            c = closes.get(key)
            committed += Decimal(r[f"reserved_{cur}"] if c is None else c[f"cost_{cur}"])
        price = spec.price or {}
        need = (
            Decimal(estimate) * Decimal(str(price.get("input_per_million", 0)))
            + Decimal(settings.max_output_tokens) * Decimal(str(price.get("output_per_million", 0)))
        ) / Decimal(1_000_000)
        need = need.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
        cap = header.get(f"max_cost_{cur}")
        if cap is not None and committed + need > Decimal(str(cap)):
            return False
        max_requests = header.get("max_requests")
        return max_requests is None or len(reserves) + 1 <= int(max_requests)

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
