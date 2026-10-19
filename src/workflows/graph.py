"""外层流程的 StateGraph。

    START → preflight ─┬─ 能运行 → await_confirmation ─┬─ 确认（身份对得上）→ revalidate ─┬─ 未变 → submit → run_life → deliver → END
                       │                               ├─ 确认的不是最新检查 → await_confirmation（提示 confirmation_stale）
                       │                               ├─ 修改条件 → preflight
                       │                               └─ 拒绝 → END（declined）
                       └─ 不能运行 → blocked ─┬─ 修改条件 → preflight
                                             └─ 取消 → END（declined）
    revalidate：确认之后、登记之前再查一次；条件变了就回到等待确认（或 blocked），旧确认作废。
    submit / run_life / deliver 遇到需要人处理的情况 → await_resolution ─┬─ 重试 → 回到出问题的节点
                                                                       └─ 放弃 → END（abandoned）

设计约束（每条都对应一个测试）：

1. 等待节点（await_confirmation、blocked、await_resolution）第一句就是 ``interrupt``，前面没有任何写操作——LangGraph 恢复时
   会从头重跑被中断的节点。
2. 有副作用的节点（submit、run_life）在任何位置崩溃后都可能被整段重跑，所以它们先查询、再决定：submit 依赖提交服务的幂等键；
   run_life 先 ``inspect``，已完成就只读，停在支持的边界才调用 E1 原有的恢复入口，未开始才调用原有的启动入口。
3. 不给任何节点配 LangGraph 的自动重试（``retry_policy``）。模型服务重试、计划修订、预算都由 E1 管；流程遇到需要人处理的
   情况只停下，重试必须由人显式发起，而且重试也只是再问一次 E1，E1 的停止规则照样生效。
4. 运行编号登记之后不能换：任何服务返回了不同的运行编号，都停在 ``run_binding_conflict``。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from workflows.ports import AttentionRequiredError, RunInspection, WorkflowServices
from workflows.state import ATTENTION_RESUME_NODES, ContentRef, WorkflowState


def _ref(state: WorkflowState, key: str) -> ContentRef:
    raw = state.get(key)
    if not raw:
        raise ValueError(f"流程状态缺少 {key}")
    return ContentRef.from_state(raw)  # type: ignore[arg-type]


def _attention(code: str, audit_ref: str | None, resume_node: str) -> Command:
    if resume_node not in ATTENTION_RESUME_NODES:
        raise ValueError(f"不能从 {resume_node} 进入待处理")
    return Command(
        update={
            "stage": "needs_attention",
            "attention": {"code": code, "audit_ref": audit_ref or "", "resume_node": resume_node},
        },
        goto="await_resolution",
    )


def _revision(answer: dict[str, Any]) -> dict[str, Any]:
    """用户修改画像或输入：换引用、清掉旧检查与旧确认，回到预检查。"""
    update: dict[str, Any] = {
        "stage": "checking",
        "preflight": None,
        "preflight_runnable": False,
        "preflight_block": None,
        "confirmation": None,
        "notice": None,
    }
    # 引用没变也照样重新检查：底层内容可能已经变了，以重新检查的结果为准。
    for key in ("profile", "inputs"):
        if answer.get(key) is not None:
            update[key] = ContentRef.from_state(answer[key]).as_state()
    return update


def build_graph(services: WorkflowServices) -> StateGraph:
    def preflight(state: WorkflowState) -> Command[Literal["await_confirmation", "blocked"]]:
        outcome = services.preflight.check(_ref(state, "profile"), _ref(state, "inputs"))
        update = {
            "preflight": outcome.ref.as_state(),
            "preflight_runnable": outcome.runnable,
            "preflight_block": outcome.block_code,
            "confirmation": None,
        }
        if outcome.runnable:
            return Command(
                update={**update, "stage": "awaiting_confirmation"}, goto="await_confirmation"
            )
        return Command(update={**update, "stage": "blocked"}, goto="blocked")

    def await_confirmation(
        state: WorkflowState,
    ) -> Command[Literal["revalidate", "await_confirmation", "preflight", "__end__"]]:
        answer = interrupt({"kind": "confirmation", "preflight": state["preflight"]})
        action = answer.get("action") if isinstance(answer, dict) else None
        if action == "accept":
            if answer.get("preflight_fingerprint") != _ref(state, "preflight").fingerprint:
                return Command(update={"notice": "confirmation_stale"}, goto="await_confirmation")
            confirmation = {
                "preflight_fingerprint": answer["preflight_fingerprint"],
                "at": str(answer.get("at", "")),
            }
            return Command(
                update={"stage": "submitting", "confirmation": confirmation, "notice": None},
                goto="revalidate",
            )
        if action == "decline":
            return Command(
                update={"stage": "declined", "confirmation": None, "notice": None}, goto=END
            )
        if action == "revise":
            return Command(update=_revision(answer), goto="preflight")
        raise ValueError(f"等待确认时收到不认识的操作：{action!r}")

    def blocked(state: WorkflowState) -> Command[Literal["preflight", "__end__"]]:
        answer = interrupt({"kind": "blocked", "code": state.get("preflight_block")})
        action = answer.get("action") if isinstance(answer, dict) else None
        if action == "revise":
            return Command(update=_revision(answer), goto="preflight")
        if action == "cancel":
            return Command(update={"stage": "declined", "notice": None}, goto=END)
        raise ValueError(f"检查未通过时收到不认识的操作：{action!r}")

    def revalidate(
        state: WorkflowState,
    ) -> Command[Literal["submit", "await_confirmation", "blocked"]]:
        """确认之后、登记之前再查一次（只读）。确认绑定的是那一份预检查；条件变了，旧确认作废。"""
        outcome = services.preflight.check(_ref(state, "profile"), _ref(state, "inputs"))
        confirmed = (state.get("confirmation") or {}).get("preflight_fingerprint")
        if outcome.runnable and outcome.ref.fingerprint == confirmed:
            return Command(update={"stage": "submitting"}, goto="submit")
        update = {
            "preflight": outcome.ref.as_state(),
            "preflight_runnable": outcome.runnable,
            "preflight_block": outcome.block_code,
            "confirmation": None,
            "notice": "inputs_changed",
        }
        if outcome.runnable:
            return Command(
                update={**update, "stage": "awaiting_confirmation"}, goto="await_confirmation"
            )
        return Command(update={**update, "stage": "blocked"}, goto="blocked")

    def submit(state: WorkflowState) -> Command[Literal["run_life", "await_resolution"]]:
        preflight_ref = _ref(state, "preflight")
        try:
            receipt = services.submission.submit(
                idempotency_key=state["idempotency_key"],
                preflight=preflight_ref,
                confirmation=dict(state.get("confirmation") or {}),
            )
        except AttentionRequiredError as e:
            return _attention(e.code, e.audit_ref, "submit")
        if receipt.preflight_fingerprint != preflight_ref.fingerprint:
            return _attention("identity_conflict", None, "submit")
        bound = state.get("run_id")
        if bound and bound != receipt.run_id:
            return _attention("run_binding_conflict", None, "submit")
        return Command(
            update={"run_id": receipt.run_id, "stage": "running", "attention": None},
            goto="run_life",
        )

    def run_life(state: WorkflowState) -> Command[Literal["deliver", "await_resolution"]]:
        run_id = state.get("run_id")
        if not run_id:
            raise ValueError("run_life 之前必须已经登记运行")
        try:
            seen = services.life.inspect(run_id)
            _same_run(seen, run_id)
            if seen.kind == "not_started":
                seen = services.life.start(run_id)
            elif seen.kind == "resumable":
                seen = services.life.resume(run_id)
            _same_run(seen, run_id)
        except AttentionRequiredError as e:
            return _attention(e.code, e.audit_ref, "run_life")
        except _BindingConflictError:
            return _attention("run_binding_conflict", None, "run_life")
        if seen.kind == "completed":
            stored = state.get("archive")
            if stored and seen.archive and seen.archive.fingerprint != stored["fingerprint"]:
                return _attention("archive_conflict", None, "run_life")
            return Command(
                update={
                    "archive": seen.archive.as_state() if seen.archive else stored,
                    "attention": None,
                },
                goto="deliver",
            )
        if seen.kind == "needs_attention":
            return _attention(seen.code or "internal_error", seen.audit_ref, "run_life")
        if seen.kind == "resumable":
            # E1 自己按规则停在了支持恢复的边界（例如服务失败）。流程不自动再试，等人决定何时恢复。
            return _attention("service_unavailable", seen.audit_ref, "run_life")
        raise RuntimeError(f"E1 启动或恢复之后仍报告 {seen.kind}")

    def deliver(state: WorkflowState) -> Command[Literal["__end__", "await_resolution"]]:
        run_id = state["run_id"]
        try:
            view = services.projection.result(run_id)  # type: ignore[arg-type]
        except AttentionRequiredError as e:
            return _attention(e.code, e.audit_ref, "deliver")
        stored = state.get("result")
        if stored and view.ref.fingerprint != stored["fingerprint"]:
            return _attention("archive_conflict", None, "deliver")
        return Command(
            update={"stage": "delivered", "result": view.ref.as_state(), "attention": None},
            goto=END,
        )

    def await_resolution(
        state: WorkflowState,
    ) -> Command[Literal["submit", "run_life", "deliver", "__end__"]]:
        attention = state.get("attention") or {}
        answer = interrupt({"kind": "attention", "code": attention.get("code")})
        action = answer.get("action") if isinstance(answer, dict) else None
        if action == "retry":
            node = attention.get("resume_node", "run_life")
            stage = "submitting" if node == "submit" else "running"
            return Command(update={"stage": stage, "attention": None, "notice": None}, goto=node)
        if action == "abandon":
            return Command(update={"stage": "abandoned", "notice": None}, goto=END)
        raise ValueError(f"待处理时收到不认识的操作：{action!r}")

    builder = StateGraph(WorkflowState)
    builder.add_node("preflight", preflight)
    builder.add_node("await_confirmation", await_confirmation)
    builder.add_node("blocked", blocked)
    builder.add_node("revalidate", revalidate)
    builder.add_node("submit", submit)
    builder.add_node("run_life", run_life)
    builder.add_node("deliver", deliver)
    builder.add_node("await_resolution", await_resolution)
    builder.add_edge(START, "preflight")
    return builder


class _BindingConflictError(Exception):
    pass


def _same_run(seen: RunInspection, run_id: str) -> None:
    if seen.run_id != run_id:
        raise _BindingConflictError(run_id)


NODES_WITH_SIDE_EFFECTS = ("submit", "run_life")
WAITING_NODES = ("await_confirmation", "blocked", "await_resolution")
