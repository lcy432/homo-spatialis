"""资料构建流程（intake-02）：来源配置 → 获取或读取缓存 → 标准化 → A/B 映射 → 生成目录与菜单 → 检查 → 冻结。

    START → configure → fetch → standardize → map → catalog → check → freeze → END

设计约束（每条都对应一个测试）：

1. 业务都在 ``product.realdata.build`` 的阶段函数里，不经过图也能跑、能测（``build.run_all``）。图只排顺序、存引用、负责恢复；
   同一份配置与原始文件，经过图与不经过图得到同一个构建身份。
2. checkpoint 只存配置身份、每个已完成阶段的引用（阶段名、内容身份、相对工作目录的路径）与状态；不存资料、不存摘要。
3. 阶段产物按内容身份放在工作目录（``<工作目录>/<阶段>/<身份前 16 位>/``），写完才有 ``done.json``。同一身份的产物已存在就复用：
   中断后恢复、换流程编号重跑，都不会重新获取、重新拟合或重新抽样。资料或方法变了，身份跟着变，产物进新目录，旧产物不动。
4. 冻结之后这个流程编号就结束了：再跑只返回冻结结果。配置变了不能接着旧流程编号跑（``config_changed``）。
5. 默认离线（只读缓存），联网获取要显式给 ``mode``。用户模拟流程不经过这里，每次模拟不会重新下载全国资料。
6. 外部追踪开着就拒绝启动（与用户模拟流程同一个守卫）。不配自动重试：阶段失败就停在那个阶段，修好后由人显式恢复。
"""

from __future__ import annotations

import sqlite3
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from product.realdata import build
from product.realdata.build import STAGES, BuildContext, StageRef
from workflows.runtime import refuse_external_tracing


class DataBuildState(TypedDict, total=False):
    config_identity: str
    refs: dict[str, dict[str, str]]
    stage: str
    status: Literal["running", "frozen"]


class DataBuildRefusedError(Exception):
    """操作被拒绝，流程状态没有改变。"""

    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(f"{code}: {message_zh}")
        self.code = code
        self.message_zh = message_zh


def _slim(ref: StageRef) -> dict[str, str]:
    return {"stage": ref.stage, "identity": ref.identity, "path": ref.path}


def build_data_graph(ctx: BuildContext) -> StateGraph:
    def need(state: DataBuildState, stage: str) -> StageRef:
        raw = (state.get("refs") or {}).get(stage)
        if not raw:
            raise ValueError(f"流程状态缺少 {stage} 阶段的产物引用")
        ref = build.load_ref(ctx, raw["stage"], raw["identity"])
        if ref is None:
            raise FileNotFoundError(
                f"{stage} 阶段的产物（{raw['path']}）不在工作目录里了：换一个流程编号重建"
            )
        return ref

    def done(state: DataBuildState, ref: StageRef, **extra: Any) -> dict[str, Any]:
        return {
            "refs": {**(state.get("refs") or {}), ref.stage: _slim(ref)},
            "stage": ref.stage,
            **extra,
        }

    def configure(state: DataBuildState) -> dict[str, Any]:
        if state.get("config_identity") != ctx.config.identity:
            raise ValueError("流程状态里的配置身份与当前配置不同")
        return done(state, build.stage_configure(ctx))

    def fetch(state: DataBuildState) -> dict[str, Any]:
        return done(state, build.stage_fetch(ctx, need(state, "configure")))

    def standardize(state: DataBuildState) -> dict[str, Any]:
        return done(state, build.stage_standardize(ctx, need(state, "fetch")))

    def map_(state: DataBuildState) -> dict[str, Any]:
        return done(state, build.stage_map(ctx, need(state, "standardize")))

    def catalog(state: DataBuildState) -> dict[str, Any]:
        return done(state, build.stage_catalog(ctx, need(state, "map")))

    def check(state: DataBuildState) -> dict[str, Any]:
        refs = [need(state, s) for s in ("standardize", "map", "catalog")]
        return done(state, build.stage_check(ctx, *refs))

    def freeze(state: DataBuildState) -> dict[str, Any]:
        refs = {s: need(state, s) for s in STAGES[:-1]}
        return done(state, build.stage_freeze(ctx, refs), status="frozen")

    nodes = {
        "configure": configure,
        "fetch": fetch,
        "standardize": standardize,
        "map": map_,
        "catalog": catalog,
        "check": check,
        "freeze": freeze,
    }
    g = StateGraph(DataBuildState)
    for name in STAGES:
        g.add_node(name, nodes[name])
    g.add_edge(START, STAGES[0])
    for a, b in pairwise(STAGES):
        g.add_edge(a, b)
    g.add_edge(STAGES[-1], END)
    return g


class DataBuildRuntime:
    """带 SQLite checkpoint 的资料构建入口：开始或恢复（``run``）、查询（``status``）。"""

    def __init__(self, checkpoint_db: Path | str, ctx: BuildContext) -> None:
        refuse_external_tracing()
        self.ctx = ctx
        self.checkpoint_db = Path(checkpoint_db)
        self.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.checkpoint_db), check_same_thread=False)
        self._graph = build_data_graph(ctx).compile(checkpointer=SqliteSaver(self._conn))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DataBuildRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def graph(self):  # 测试与诊断用
        return self._graph

    def default_thread(self) -> str:
        """默认流程编号由配置与代码版本决定：资料方法或版本变了就是新流程，旧流程的冻结结果不动。"""
        return (
            f"intake-{self.ctx.config.build.name}-{build.pipeline_identity(self.ctx.config)[:16]}"
        )

    def _values(self, thread_id: str) -> tuple[dict[str, Any], tuple[str, ...]]:
        snap = self._graph.get_state({"configurable": {"thread_id": thread_id}})
        return dict(snap.values or {}), tuple(snap.next)

    def run(self, thread_id: str | None = None, *, stop_after: str | None = None) -> dict[str, Any]:
        """开始一个新流程，或从上次停下（中断、失败或 ``stop_after``）的阶段接着跑；已冻结的只返回结果。"""
        if stop_after is not None and stop_after not in STAGES:
            raise ValueError(f"没有阶段 {stop_after}；阶段是 {STAGES}")
        tid = thread_id or self.default_thread()
        values, _ = self._values(tid)
        if values:
            if values.get("config_identity") != self.ctx.config.identity:
                raise DataBuildRefusedError(
                    "config_changed", "这个流程编号是用另一份配置建的：配置变了就用新的流程编号"
                )
            if values.get("status") == "frozen":
                return self.status(tid)
            payload: dict[str, Any] | None = None
        else:
            payload = {
                "config_identity": self.ctx.config.identity,
                "refs": {},
                "stage": "",
                "status": "running",
            }
        self._graph.invoke(
            payload,
            {"configurable": {"thread_id": tid}},
            interrupt_after=[stop_after] if stop_after else None,
            durability="sync",
        )
        return self.status(tid)

    def status(self, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or self.default_thread()
        values, nxt = self._values(tid)
        refs = values.get("refs") or {}
        stages = []
        for s in STAGES:
            raw = refs.get(s)
            if raw is None:
                stages.append({"stage": s, "state": "pending"})
                continue
            hit = build.load_ref(self.ctx, s, raw["identity"])
            stages.append(
                {
                    "stage": s,
                    "state": "done" if hit is not None else "artifact_missing",
                    "identity": raw["identity"],
                    "path": raw["path"],
                    "summary": hit.summary if hit is not None else None,
                }
            )
        if not values:
            status = "not_started"
        elif values.get("status") == "frozen":
            status = "frozen"
        else:
            status = "paused"  # 停在某个阶段之后，或某个阶段失败：可以接着跑
        return {
            "thread_id": tid,
            "status": status,
            "next": list(nxt),
            "config_identity": values.get("config_identity"),
            "stages": stages,
        }

    def frozen_db(self, thread_id: str | None = None) -> Path:
        st = self.status(thread_id)
        if st["status"] != "frozen":
            raise DataBuildRefusedError("not_frozen", "这个流程还没有冻结")
        ref = next(s for s in st["stages"] if s["stage"] == "freeze")
        return self.ctx.work / ref["path"] / "intake.sqlite"
