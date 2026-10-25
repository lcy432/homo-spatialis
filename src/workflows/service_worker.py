"""单个 worker（DECISIONS/0043）：独立进程，整个生命周期持有进程文件锁（portalocker），串行执行服务库里的任务。

* 第二个 worker 拒绝启动（锁拿不到就退出），不凭“心跳过期”抢占还在工作的进程。前一个进程死掉之后锁随进程释放，新的 worker
  先做过期检查，再把上一个进程留下的进行中任务退回待办；恢复交给原流程与 E1 自己的核对（归档、请求明细与账本逐条核对），不重置
  次数、账本或尝试计数，也不新建运行目录。
* 任务：``preflight``（排队检查：准备工作目录与快照，经 LangGraph 做运行前检查，不扣次数）、``run``（最后核对 → 经 LangGraph
  确认、登记、启动 E1、投影；首次启动 E1 之前把预留转为已使用）、``resume``（服务端判定安全的恢复）、``delete``（安全收尾后清理）。
* 一次运行进行中时，监测线程按绝对路径读 E1 的进度与这次运行的账本，写白名单进度快照（不切换工作目录、不推进流程）；维护线程
  按时检查过期并清理不在执行中的意图。删除与完成同时发生时删除优先：只有仍在“运行中”的意图才会被写回进度或结果。
* 停下时（E3C 收尾 R4、R6）：能不能由用户恢复按 E1 的进度、请求尝试、预算与发送状态判断（``RunDir.resume_state``），按钮、公开进度
  与说明用同一个结论；说明按服务库里的实际记录写（有没有开始、预留有没有真的退回），不按停止原因猜。
* 重启时（R3）：先按截止时间处理过期——上一个进程留下的“运行中”记录没有执行者，同样按期限处理——然后才把剩下的任务退回待办。
* 日志只记任务种类、意图编号、公开代码、异常类型与代码位置，不记答案、模型正文、异常信息或任何秘密。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import portalocker

from common.product_contracts import ConfirmedProfile, ProductResult
from product.display.document import doc_version, explained
from product.execution import ExecutionSpec
from product.service.actions import ServiceContext
from product.service.explain import service_explanation
from product.service.tokens import support_ref
from product.workspace import SnapshotError
from workflows.ports import AttentionRequiredError
from workflows.runtime import WorkflowRefusedError
from workflows.service_runs import RunDir, TransportFactory

log = logging.getLogger("workflows.service_worker")

#: 启动之前就停下的公开代码 → 服务说明的代码（没有转为已使用的预留退回）
_PRESTART = {
    "paid_calls_disabled": "paid_calls_disabled",
    "credentials_missing": "credentials_missing",
    "start_refused": "start_refused",
    "model_mismatch": "model_mismatch",
    "request_too_large": "request_too_large",
}


class WorkerLockedError(RuntimeError):
    """另一个 worker 正持有锁。"""


class Worker:
    def __init__(
        self,
        ctx: ServiceContext,
        *,
        transport_factory: TransportFactory | None = None,
        faults: set[str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.transport_factory = transport_factory
        self.faults = faults
        self.sleep = sleep
        self._guard = threading.Lock()
        self._diag_lock = threading.Lock()
        self.active: str | None = None
        self._lock: portalocker.Lock | None = None
        self.diagnostics = ctx.data_root / "diagnostics" / "worker.jsonl"

    # ------------------------------------------------------------------ 进程锁

    def acquire(self) -> None:
        path = self.ctx.data_root / "worker.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            str(path),
            mode="a",
            flags=portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING,
            fail_when_locked=True,
        )
        try:
            lock.acquire()
        except portalocker.exceptions.LockException as exc:
            raise WorkerLockedError("另一个 worker 正在运行：拒绝启动（不抢占）") from exc
        self._lock = lock

    def release(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def __enter__(self) -> Worker:
        self.acquire()
        self.recover()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    # ------------------------------------------------------------------ 启动与循环

    def recover(self) -> None:
        """启动时：先按截止时间处理过期（停机之后，到期的不再恢复或付费；上一个进程留下的“运行中”记录没有执行者，同样按期限
        处理），再把剩下的进行中任务退回待办。"""
        self.maintain()
        for job in self.store.requeue_active():
            self._diag("requeued", job["intent_id"], kind=job["kind"])

    def run_pending(self, limit: int = 10_000) -> int:
        """执行到没有待办任务为止（测试与一次性运行用）。返回执行的任务数。"""
        n = 0
        while n < limit and self.step():
            n += 1
        return n

    def run_forever(self, stop: threading.Event) -> None:
        keeper = threading.Thread(target=self._maintenance_loop, args=(stop,), daemon=True)
        keeper.start()
        while not stop.is_set():
            if not self.step():
                stop.wait(self.ctx.config.worker_poll_seconds)

    def step(self) -> bool:
        with self._guard:
            job = self.store.take_job()
            self.active = None if job is None else job["intent_id"]
        if job is None:
            return False
        intent = job["intent_id"]
        try:
            if job["kind"] == "preflight":
                self._preflight(intent)
            elif job["kind"] in ("run", "resume"):
                self._run(intent, job["kind"])
            elif job["kind"] == "delete":
                self._delete(intent)
        except Exception as exc:  # 程序错误：意图停在等维护者（或开始前退回预留），不重试
            self._internal(intent, exc)
        finally:
            with self._guard:
                self.active = None
            self.store.finish_job(job["job_id"])
        return True

    # ------------------------------------------------------------------ 运行前检查（排队检查，不扣次数）

    def _preflight(self, intent: str) -> None:
        row = self.store.intent_row(intent)
        if row is None or row["stage"] != "checking":
            return
        body = self.store.profile_body(row["confirmed_id"])
        if body is None:
            self._block(intent, "reference_stale", only=("checking",))
            return
        confirmed = ConfirmedProfile.model_validate_json(body)
        try:
            # 这次运行意图创建时保存的有效设置；缺了就拒绝，不回退到服务现在的默认值
            spec = ExecutionSpec.model_validate_json(row["execution_body"])
        except (TypeError, ValueError):
            self._block(intent, "settings_missing", only=("checking",))
            return
        rd = RunDir(self.ctx, intent)
        snapshot = rd.prepare(confirmed, spec)
        self.store.update_intent(
            intent,
            only_stages=("checking",),
            snapshot=json.dumps(snapshot, sort_keys=True),
        )
        profile_ref, inputs_ref = rd.refs(confirmed.confirmed_id)
        with rd.runtime(faults=self.faults) as rt:
            try:
                view = rt.status(intent)
            except WorkflowRefusedError:
                view = rt.start(
                    workflow_id=intent,
                    profile=profile_ref,
                    inputs=inputs_ref,
                    idempotency_key=row["workflow_key"],
                )
            else:
                if view["waiting_for"] in ("confirmation", "revision"):
                    view = rt.revise(intent, profile=profile_ref, inputs=inputs_ref)
                elif view["waiting_for"] is None and not view["finished"]:
                    view = rt.resume(intent)  # 上一个进程在检查中途退出
        self._publish_preflight(intent, view)

    def _publish_preflight(self, intent: str, view: dict[str, Any]) -> None:
        pre = view.get("preflight") or {}
        public = pre.get("public")
        product = isinstance(public, dict) and "preflight_id" in public
        if view["stage"] == "awaiting_confirmation" and view.get("confirm_with") and product:
            self.store.update_intent(
                intent,
                only_stages=("checking",),
                stage="awaiting_confirmation",
                waiting_for="user",
                preflight_fingerprint=view["confirm_with"],
                preflight_body=json.dumps(public, ensure_ascii=False),
                preflight_display=RunDir(self.ctx, intent).display_doc(
                    f"preflight-{public['preflight_fingerprint'][:16]}"
                ),
                explanation=None,
                explanation_display=None,
            )
            return
        if view["stage"] == "blocked":
            code = (pre.get("block") or {}).get("code") or "preflight_blocked"
            self._block(
                intent,
                code,
                only=("checking",),
                preflight_body=json.dumps(public, ensure_ascii=False) if product else None,
                preflight_display=RunDir(self.ctx, intent).display_doc(
                    f"preflight-{public['preflight_fingerprint'][:16]}"
                )
                if product
                else None,
            )
            return
        self._block(intent, "internal_error", only=("checking",), support=self._error_ref(view))

    # ------------------------------------------------------------------ 运行与恢复

    def _run(self, intent: str, kind: str) -> None:
        row = self.store.intent_row(intent)
        if row is None:
            return
        if row["stage"] == "queued":
            if not self.store.mark_started(intent):
                return
        elif row["stage"] != "running":
            return  # 取消、删除、过期或已经结束：不开始
        rd = RunDir(self.ctx, intent)
        try:
            snapshot = json.loads(row["snapshot"]) if row["snapshot"] else None
            if not snapshot:
                raise SnapshotError("没有记下资料快照")
            rd.verify(snapshot)  # 已确认的运行绑定当时那份快照：缺失或被改动就停，不拿新资料冒充
            execution = rd.saved_request().execution
        except (SnapshotError, OSError, ValueError):
            self._stop(intent, "snapshot_broken")
            return
        if (
            execution is not None
            and execution.planner_kind == "llm"
            and not self.ctx.config.live_calls_enabled
        ):
            self._stop(intent, "paid_calls_disabled")
            return
        run_id = row["run_id"]
        monitor = _Monitor(self, intent, run_id, self.ctx.config.progress_poll_seconds)
        monitor.start()
        try:
            with rd.runtime(
                transport_factory=self.transport_factory,
                faults=self.faults,
                on_start=lambda _run_id: self._on_start(intent),
                sleep=self.sleep,
            ) as rt:
                if kind == "resume":
                    view = rt.resolve(intent, "retry")
                else:
                    view = rt.status(intent)
                    if view["waiting_for"] == "confirmation":
                        view = rt.confirm(intent, row["preflight_fingerprint"])
                    elif view["waiting_for"] is None and not view["finished"]:
                        view = rt.resume(
                            intent
                        )  # 上一个进程在流程中途退出：交给原流程与 E1 的恢复核对
        finally:
            monitor.stop()
        self._publish_run(intent, view)
        after = self.store.intent_row(intent)
        self._write_progress(intent, run_id, resumable=bool(after and after["resume_allowed"]))

    def _on_start(self, intent: str) -> None:
        """首次启动 E1 之前的最后一步：删除或取消优先；有效的预留转为已使用（重复调用不重复扣）。"""
        if self.store.stop_requested(intent):
            raise AttentionRequiredError("run_deleted")
        try:
            self.store.consume(intent, "首次启动 E1")
        except RuntimeError as exc:
            raise AttentionRequiredError("start_refused") from exc

    def _publish_run(self, intent: str, view: dict[str, Any]) -> None:
        attention = view.get("attention") or {}
        code = attention.get("code")
        if view["stage"] == "delivered" and view.get("result"):
            result = ProductResult.model_validate(view["result"])
            published = self.store.finish_intent(
                intent,
                result_body=result.model_dump_json(),
                result_fingerprint=result.result_fingerprint,
                days=self.ctx.config.result_retention_days,
                result_display=RunDir(self.ctx, intent).display_doc(f"result-{result.run_id}"),
            )
            self._diag("delivered" if published else "delivery_skipped", intent)
            return
        if code == "run_deleted":
            return  # 用户删除：收尾与清理由删除任务做
        if not self.store.consumed(intent):
            if view["stage"] in ("awaiting_confirmation", "blocked"):
                reason = "inputs_changed"  # 确认之后的再核对发现条件变了
            else:
                reason = _PRESTART.get(code or "", "internal_error")
            self._stop(intent, reason, support=self._error_ref(view))
            return
        if view["stage"] == "needs_attention" and code:
            self._stop(intent, code, support=self._error_ref(view))
            return
        self._stop(intent, "internal_error", support=self._error_ref(view))

    def _stop(self, intent: str, code: str, *, support: str | None = None) -> None:
        """运行停下。按服务库里的实际记录处理：还没有启动 E1（次数没有转为已使用）→ 退回预留，意图回到“检查没有通过”（用户可以
        取消后重新准备）；已经启动 → 不退次数，服务不可用时再按 E1 的记录判断能不能真的恢复，能就等用户点“恢复”，不能就等维护者。"""
        ref = support or support_ref()
        state = self.store.quota_state(intent)
        if not state["used"]:
            released = self.store.release(intent, f"启动前停下：{code}") or state["released"]
            self._diag("stopped", intent, code=code, support=ref, started=False, released=released)
            self._block(intent, code, only=("queued", "running"), support=ref, released=released)
            return
        resumable = False
        if code == "service_unavailable":
            row = self.store.intent_row(intent)
            ok, why = RunDir(self.ctx, intent).resume_state(row["run_id"]) if row else (False, None)
            resumable, code = ok, (code if ok else why or "run_halted")
        self._diag("stopped", intent, code=code, support=ref, started=True, resumable=resumable)
        explanation, display = explained(
            service_explanation,
            pack_fingerprint=self.ctx.pack.fingerprint,
            code=code,
            phase="after_start",
            resumable=resumable,
            support=ref,
        )
        self.store.update_intent(
            intent,
            only_stages=("running",),
            stage="needs_attention",
            waiting_for="user" if resumable else "maintainer",
            resume_allowed=int(resumable),
            support_ref=ref,
            explanation=explanation,
            explanation_display=display,
        )

    def _block(
        self,
        intent: str,
        code: str,
        *,
        only: tuple[str, ...],
        support: str | None = None,
        preflight_body: str | None = None,
        preflight_display: str | None = None,
        released: bool = False,
    ) -> None:
        explanation, display = explained(
            service_explanation,
            pack_fingerprint=self.ctx.pack.fingerprint,
            code=code,
            phase="before_start",
            released=released,
            support=support,
        )
        self.store.update_intent(
            intent,
            only_stages=only,
            stage="blocked",
            waiting_for="user",
            preflight_fingerprint=None,
            preflight_body=preflight_body,
            preflight_display=preflight_display,
            support_ref=support,
            explanation=explanation,
            explanation_display=display,
        )

    # ------------------------------------------------------------------ 进度快照（只读）

    def _write_progress(
        self,
        intent: str,
        run_id: str | None,
        *,
        resumable: bool = False,
        running_now: bool = False,
    ) -> None:
        if run_id is None:
            return
        try:
            row = self.store.intent_row(intent)
            found = RunDir(self.ctx, intent).progress_with_display(
                run_id,
                self.store.now(),
                resumable=resumable,
                running_now=running_now,
                resource_version=doc_version(row["preflight_display"]) if row else None,
            )
        except Exception:  # 进度文件正被替换、账本正被写：这一轮跳过，下一轮再读
            return
        if found is not None:
            snap, display = found
            self.store.update_intent(
                intent,
                only_stages=("running", "needs_attention", "finished"),
                progress_body=snap.model_dump_json(),
                progress_display=json.dumps(display, ensure_ascii=False),
            )

    # ------------------------------------------------------------------ 删除、过期与清理

    def _delete(self, intent: str) -> None:
        row = self.store.intent_row(intent)
        if row is None or row["stage"] != "deleting":
            return
        self._clean(intent, final="deleted")

    def _clean(self, intent: str, *, final: str | None) -> None:
        """先记下最小费用摘要，再删登记过的工作目录，最后清掉库里的个人内容（中途崩溃也能重做，摘要不丢）。"""
        rd = RunDir(self.ctx, intent)
        if rd.root.exists():
            summary = rd.cost_summary()
            if summary is not None:
                self.store.save_cost_summary(intent, summary)
            rd.remove()
        if final is not None:
            self.store.purge_intent(intent, stage=final, cost_summary=None)
        self._diag("cleaned", intent, stage=final or "")

    def maintain(self) -> None:
        """过期（按注入的时钟）与清理：不碰正在执行的那一个意图；其他“运行中”的记录没有执行者，按期限处理。"""
        with self._guard:
            expired = self.store.expire_due(active=self.active)
        for intent in expired:
            self._diag("expired", intent)
        for row in self.store.cleanup_candidates():
            intent = row["intent_id"]
            with self._guard:
                if intent == self.active:
                    continue
                if row["stage"] == "deleting":
                    self._clean(intent, final="deleted")
                elif RunDir(self.ctx, intent).root.exists():
                    self._clean(intent, final="expired" if row["stage"] == "expired" else None)

    def _maintenance_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.ctx.config.maintenance_seconds):
            try:
                self.maintain()
            except Exception as exc:
                self._internal(None, exc)

    # ------------------------------------------------------------------ 诊断

    def _internal(self, intent: str | None, exc: BaseException) -> None:
        ref = support_ref()
        where = " < ".join(
            f"{Path(f.filename).name}:{f.lineno} {f.name}"
            for f in reversed(traceback.extract_tb(exc.__traceback__)[-8:])
        )
        log.error("worker internal_error %s %s at %s", ref, type(exc).__name__, where)
        self._diag("internal_error", intent, support=ref, exception=type(exc).__name__, at=where)
        if intent is None:
            return
        row = self.store.intent_row(intent)
        if row is None:
            return
        if row["stage"] == "checking":
            self._block(intent, "internal_error", only=("checking",), support=ref)
        elif row["stage"] in ("queued", "running"):
            self._stop(intent, "internal_error", support=ref)

    def _error_ref(self, view: dict[str, Any]) -> str:
        """流程公开视图里的审计编号只记进 worker 的诊断，对外给一个新的支持编号。"""
        ref = support_ref()
        err = view.get("error") or {}
        att = view.get("attention") or {}
        self._diag(
            "workflow_ref",
            view.get("workflow_id"),
            support=ref,
            audit=err.get("audit_ref") or att.get("audit_ref") or "",
            code=err.get("code") or att.get("code") or "",
        )
        return ref

    def _diag(self, event: str, intent: str | None, **fields: Any) -> None:
        self.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": self.store.now(), "event": event, "intent": intent, **fields}
        with self._diag_lock, self.diagnostics.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class _Monitor:
    """运行进行中按时写进度快照（只读，绝对路径；不切换工作目录、不推进流程）。"""

    def __init__(self, worker: Worker, intent: str, run_id: str | None, every: float) -> None:
        self.worker, self.intent, self.run_id, self.every = worker, intent, run_id, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            self.worker._write_progress(self.intent, self.run_id, running_now=True)
