"""外层流程的操作入口：SQLite 持久化、按当前等待事项分派操作、公开视图与本地诊断。

一个流程编号（LangGraph 的 thread_id）就是一次外层流程：它不是用户身份，也不是访问权限。操作：

* ``start``：开始流程（立即做运行前检查）；同一编号、同一组条件重复开始只返回现状，不同条件拒绝；
* ``confirm``：确认“按这份运行前检查开始这次模拟”，必须带上那份检查的内容身份；
* ``decline``：拒绝（等待确认时）或取消（检查未通过时）；
* ``revise``：修改画像或输入（只在提交之前），回到运行前检查；
* ``resume``：进程中断后接着跑（停在等待事项上时什么都不做）；
* ``resolve``：处理需要人处理的事项（``retry`` 或 ``abandon``）；
* ``status``：公开视图。前端将来只读这个视图，不读 checkpoint、``graph.get_state`` 或内部异常。

本轮串行、单执行者：同一个数据库同一时间只应有一个进程在推进流程。这里没有跨进程锁，不支持并发推进。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from workflows.graph import build_graph
from workflows.ports import AttentionRequiredError, NotWiredError, WorkflowServices
from workflows.state import (
    FLOW_VERSION,
    STAGE_ZH,
    TERMINAL_STAGES,
    ContentRef,
    fingerprint,
    public_text,
)

TRACING_ENV = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)
_TRUTHY = {"1", "true", "yes", "on"}


class WorkflowRefusedError(Exception):
    """操作被拒绝，流程状态没有改变。``code`` 是公开代码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def public(self) -> dict[str, str]:
        return {"refused": self.code, "message_zh": public_text(self.code) or ""}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def default_idempotency_key(workflow_id: str) -> str:
    """默认幂等键：只由流程编号决定，所以同一流程的重复提交永远是同一个键。

    前端将来应当传自己的键（E3 RunSubmission 的幂等键），同一次“确认开始”的重复点击用同一个键，避免每次点击新开一个流程。
    """
    return "wf-" + fingerprint({"workflow_id": workflow_id, "purpose": "run_submission"})[:32]


class WorkflowRuntime:
    def __init__(
        self,
        db_path: Path | str,
        services: WorkflowServices,
        *,
        diagnostics_dir: Path | str | None = None,
    ) -> None:
        refuse_external_tracing()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.diagnostics_dir = (
            Path(diagnostics_dir) if diagnostics_dir else self.db_path.parent / "diagnostics"
        )
        self.services = services
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._graph = build_graph(services).compile(checkpointer=SqliteSaver(self._conn))

    # ---- 生命周期 ----
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> WorkflowRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def graph(self):  # 测试与诊断用；产品代码不应当读 checkpoint
        return self._graph

    # ---- 操作 ----
    def start(
        self,
        *,
        workflow_id: str,
        profile: ContentRef,
        inputs: ContentRef,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not workflow_id or not isinstance(workflow_id, str):
            raise WorkflowRefusedError("invalid_request")
        if profile.kind != "confirmed_profile" or inputs.kind != "run_inputs":
            raise WorkflowRefusedError("invalid_request")
        key = idempotency_key or default_idempotency_key(workflow_id)
        # 提交服务可以提供 key_problems：按它自己的规则（E3 是 RunSubmission 的字段约束）先校验，不等到确认之后才在登记时失败
        key_check = getattr(self.services.submission, "key_problems", None)
        if callable(key_check) and key_check(key):
            raise WorkflowRefusedError("idempotency_key_invalid")
        snap = self._snapshot(workflow_id)
        if snap is not None:
            values = snap.values
            self._check_version(values)
            same = (
                values.get("profile") == profile.as_state()
                and values.get("inputs") == inputs.as_state()
                and values.get("idempotency_key") == key
            )
            if not same:
                raise WorkflowRefusedError("workflow_exists")
            return self.status(workflow_id)
        initial = {
            "flow_version": FLOW_VERSION,
            "workflow_id": workflow_id,
            "stage": "checking",
            "profile": profile.as_state(),
            "inputs": inputs.as_state(),
            "idempotency_key": key,
            "preflight": None,
            "preflight_runnable": False,
            "preflight_block": None,
            "confirmation": None,
            "run_id": None,
            "archive": None,
            "result": None,
            "attention": None,
            "notice": None,
        }
        error = self._invoke(workflow_id, initial, "start")
        return self.status(workflow_id, error=error)

    def confirm(
        self,
        workflow_id: str,
        preflight_fingerprint: str,
        *,
        at: str | None = None,
        source: str = "user_confirm",
        authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """确认开始。``source`` 写明是谁确认的：``user_confirm``（用户在检查之后确认）或 ``start_authorization``（用户在检查之前的
        一次开始授权，``authorization`` 带它的同意身份与授权时间；DECISIONS/0048）。"""
        snap = self._require(workflow_id)
        if self._pending(snap) != "confirmation":
            done = snap.values.get("confirmation") or snap.values.get("run_id")
            return self.status(
                workflow_id, notice="already_confirmed" if done else "nothing_pending"
            )
        payload: dict[str, Any] = {
            "action": "accept",
            "preflight_fingerprint": str(preflight_fingerprint),
            "at": at or _now(),
            "source": source,
        }
        if authorization is not None:
            payload["authorization"] = dict(authorization)
        error = self._invoke(workflow_id, Command(resume=payload), "confirm")
        return self.status(workflow_id, error=error)

    def decline(self, workflow_id: str) -> dict[str, Any]:
        snap = self._require(workflow_id)
        pending = self._pending(snap)
        if pending == "confirmation":
            payload = {"action": "decline"}
        elif pending == "blocked":
            payload = {"action": "cancel"}
        else:
            return self.status(workflow_id, notice="nothing_pending")
        error = self._invoke(workflow_id, Command(resume=payload), "decline")
        return self.status(workflow_id, error=error)

    def revise(
        self,
        workflow_id: str,
        *,
        profile: ContentRef | None = None,
        inputs: ContentRef | None = None,
    ) -> dict[str, Any]:
        snap = self._require(workflow_id)
        if self._pending(snap) not in ("confirmation", "blocked"):
            # 已经提交的流程绑定一个 E1 运行，不能改条件；要换条件请开新流程。
            return self.status(workflow_id, notice="nothing_pending")
        if (profile and profile.kind != "confirmed_profile") or (
            inputs and inputs.kind != "run_inputs"
        ):
            raise WorkflowRefusedError("invalid_request")
        payload = {
            "action": "revise",
            "profile": profile.as_state() if profile else None,
            "inputs": inputs.as_state() if inputs else None,
        }
        error = self._invoke(workflow_id, Command(resume=payload), "revise")
        return self.status(workflow_id, error=error)

    def resolve(self, workflow_id: str, action: str) -> dict[str, Any]:
        if action not in ("retry", "abandon"):
            raise WorkflowRefusedError("invalid_request")
        snap = self._require(workflow_id)
        if self._pending(snap) != "attention":
            return self.status(workflow_id, notice="nothing_pending")
        error = self._invoke(workflow_id, Command(resume={"action": action}), "resolve")
        return self.status(workflow_id, error=error)

    def resume(self, workflow_id: str) -> dict[str, Any]:
        """进程中断后接着跑。停在等待事项上或已经结束时什么都不做，只返回公开视图。"""
        snap = self._require(workflow_id)
        if not snap.next or self._pending(snap) is not None:
            return self.status(workflow_id)
        error = self._invoke(workflow_id, None, "resume")
        return self.status(workflow_id, error=error)

    # ---- 公开视图 ----
    def status(
        self,
        workflow_id: str,
        *,
        error: dict[str, str] | None = None,
        notice: str | None = None,
    ) -> dict[str, Any]:
        snap = self._require(workflow_id)
        v = snap.values
        stage = v.get("stage", "checking")
        pending = self._pending(snap)
        view: dict[str, Any] = {
            "workflow_id": workflow_id,
            "flow_version": v.get("flow_version"),
            "services": self.services.label,
            "stage": stage,
            "stage_zh": STAGE_ZH.get(stage, stage),
            "waiting_for": {
                "confirmation": "confirmation",
                "blocked": "revision",
                "attention": "resolution",
            }.get(pending or ""),
            "finished": stage in TERMINAL_STAGES,
            "run_id": v.get("run_id"),
            "preflight": None,
            "confirm_with": None,
            "progress": None,
            "result": None,
            "attention": None,
            "notice": None,
            "error": error or self._last_error(workflow_id, snap),
        }
        problems: list[dict[str, str]] = []
        if v.get("preflight") and stage in ("awaiting_confirmation", "blocked", "checking"):
            ref = ContentRef.from_state(v["preflight"])
            view["preflight"] = {
                "fingerprint": ref.fingerprint,
                "runnable": bool(v.get("preflight_runnable")),
                "block": _code_view(v.get("preflight_block")),
                "public": self._safe(
                    lambda: self.services.preflight.describe(ref), workflow_id, "describe", problems
                ),
            }
            if pending == "confirmation":
                view["confirm_with"] = ref.fingerprint
        if v.get("run_id"):
            view["progress"] = self._safe(
                lambda: self.services.projection.progress(v["run_id"]),
                workflow_id,
                "progress",
                problems,
            )
        if stage == "delivered" and v.get("result"):
            view["result"] = self._safe(
                lambda: self._delivered_result(v), workflow_id, "result", problems
            )
        if v.get("attention"):
            view["attention"] = {
                **(_code_view(v["attention"].get("code")) or {}),
                "audit_ref": v["attention"].get("audit_ref") or None,
            }
        view["notice"] = _code_view(notice or v.get("notice"))
        if problems and view["error"] is None:
            view["error"] = problems[0]
        return view

    def _delivered_result(self, v: dict[str, Any]) -> dict[str, Any]:
        result = self.services.projection.result(v["run_id"])
        if result.ref.fingerprint != v["result"]["fingerprint"]:
            raise AttentionRequiredError("archive_conflict")
        return result.public

    # ---- 内部 ----
    def _config(self, workflow_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": workflow_id}}

    def _snapshot(self, workflow_id: str):
        snap = self._graph.get_state(self._config(workflow_id))
        return snap if snap.values else None

    def _require(self, workflow_id: str):
        snap = self._snapshot(workflow_id)
        if snap is None:
            raise WorkflowRefusedError("workflow_not_found")
        self._check_version(snap.values)
        return snap

    @staticmethod
    def _check_version(values: dict[str, Any]) -> None:
        if values.get("flow_version") != FLOW_VERSION:
            raise WorkflowRefusedError("flow_version_mismatch")

    @staticmethod
    def _pending(snap) -> str | None:
        for item in snap.interrupts:
            value = item.value
            if isinstance(value, dict) and value.get("kind") in (
                "confirmation",
                "blocked",
                "attention",
            ):
                return value["kind"]
        return None

    def _invoke(self, workflow_id: str, payload: Any, operation: str) -> dict[str, str] | None:
        try:
            self._graph.invoke(payload, self._config(workflow_id), durability="sync")
            return None
        except NotWiredError as exc:
            return self._record(workflow_id, operation, exc, "not_wired")
        except Exception as exc:  # 程序错误：原文只进本地诊断，对外只给公开代码与审计编号
            return self._record(workflow_id, operation, exc, "internal_error")

    def _safe(self, fn, workflow_id: str, operation: str, problems: list[dict[str, str]]):
        try:
            return fn()
        except AttentionRequiredError as exc:
            problems.append({**(_code_view(exc.code) or {}), "audit_ref": exc.audit_ref})
        except NotWiredError as exc:
            problems.append(self._record(workflow_id, operation, exc, "not_wired"))
        except Exception as exc:
            problems.append(self._record(workflow_id, operation, exc, "internal_error"))
        return None

    def _record(
        self, workflow_id: str, operation: str, exc: BaseException, code: str
    ) -> dict[str, str]:
        snap = self._graph.get_state(self._config(workflow_id))
        checkpoint_id = (snap.config or {}).get("configurable", {}).get("checkpoint_id")
        at = _now()
        audit_ref = (
            "wf-diag-"
            + fingerprint(
                {"wf": workflow_id, "op": operation, "at": at, "ns": time.perf_counter_ns()}
            )[:16]
        )
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "audit_ref": audit_ref,
            "workflow_id": workflow_id,
            "operation": operation,
            "code": code,
            "checkpoint_id": checkpoint_id,
            "at": at,
            "services": self.services.label,
            "exception_type": type(exc).__name__,
            "traceback": "".join(traceback.format_exception(exc)),
        }
        (self.diagnostics_dir / f"{audit_ref}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )
        with (self.diagnostics_dir / "index.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(
                json.dumps(
                    {
                        "audit_ref": audit_ref,
                        "workflow_id": workflow_id,
                        "code": code,
                        "checkpoint_id": checkpoint_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return {**(_code_view(code) or {}), "audit_ref": audit_ref}

    def _last_error(self, workflow_id: str, snap) -> dict[str, str] | None:
        """最近一次失败如果发生在当前 checkpoint 上（之后没有进展），公开视图继续显示它的代码与审计编号。"""
        index = self.diagnostics_dir / "index.jsonl"
        if not index.is_file():
            return None
        checkpoint_id = (snap.config or {}).get("configurable", {}).get("checkpoint_id")
        last = None
        for line in index.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("workflow_id") == workflow_id and row.get("checkpoint_id") == checkpoint_id:
                last = row
        if last is None:
            return None
        return {**(_code_view(last["code"]) or {}), "audit_ref": last["audit_ref"]}


def _code_view(code: str | None) -> dict[str, str] | None:
    if not code:
        return None
    return {"code": code, "message_zh": public_text(code) or ""}


def refuse_external_tracing() -> None:
    """本原型不把流程记录上传到外部追踪平台。环境里开着 LangSmith / LangChain 追踪就拒绝启动，不替用户悄悄关掉。"""
    for name in TRACING_ENV:
        if os.environ.get(name, "").strip().lower() in _TRUTHY:
            raise WorkflowRefusedError("external_tracing_enabled")
