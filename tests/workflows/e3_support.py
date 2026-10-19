"""E3 / E1 真实接入的测试与对照工具：同一组合成输入分别走 E3 的直接路径与 LangGraph 路径，逐项比较。

比较规则见 ``runs/2026-10-18-langgraph-e3-wiring/expected_differences.md``（比较之前写下）：只去掉 ``meta.sample`` 一个字段，归档目录名
不同但文件内容逐字节比较，只比较直接路径的第一次登记回执，流程自己的文件不参与比较。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from common.life_planning_contracts import MockScript
from orchestration.life_outcome import outcome_from_archive
from product import scenarios as sc
from workflows.e3_adapters import (
    _ILLNESS,
    EXAMPLES,
    E3Context,
    E3Workspace,
    e3_services,
    seed_example,
)
from workflows.runtime import WorkflowRuntime

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "scripts" / "product_workflow.py"


def key_for(example: str, name: str | None = None) -> str:
    """与 ``product.scenarios.closed_loop`` 同一个幂等键：``idem-<人>-<名字>``。"""
    return f"idem-{EXAMPLES[example]['person']}-{name or example}"


def direct(work: Path, example: str, name: str | None = None) -> sc.Scenario:
    """E3 自己的直接路径（离线闭环），输入与 ``seed_example`` 相同。"""
    spec = EXAMPLES[example]
    kw: dict[str, Any] = {"person": spec["person"]}
    if example != "normal":
        kw["origin"] = "controlled_fixture"
    if "draft" in spec:
        kw["draft_kwargs"] = dict(spec["draft"])
    if spec.get("repeat_indexes") is not None:
        kw["repeat_indexes"] = tuple(spec["repeat_indexes"])
    if "illness" in spec:
        kw["illness_events"] = {**_ILLNESS, "injected": copy.deepcopy(spec["illness"])}
    if "scripts" in spec:
        kw["scripts"] = tuple(MockScript(**s) for s in spec["scripts"])
    return sc.closed_loop(Path(work), name or example, **kw)


def via_workflow(
    work: Path,
    example: str,
    *,
    name: str | None = None,
    faults: set[str] | None = None,
    retry_service_halt: bool = True,
) -> tuple[E3Workspace, dict[str, Any]]:
    """LangGraph 路径（同一进程）：开始 → 确认 →（服务失败停下时人工重试一次）→ 交付。返回工作目录与最后的公开视图。"""
    ws = E3Workspace.prepare(Path(work) / "ws")
    profile, inputs = seed_example(ws, example, name=name)
    with WorkflowRuntime(Path(work) / "wf.db", e3_services(ws, faults=faults)) as rt:
        wid = f"wf-{name or example}"
        view = rt.start(
            workflow_id=wid, profile=profile, inputs=inputs, idempotency_key=key_for(example, name)
        )
        view = rt.confirm(wid, view["confirm_with"])
        if (
            retry_service_halt
            and (view.get("attention") or {}).get("code") == "service_unavailable"
        ):
            view = rt.resolve(wid, "retry")
    return ws, view


def cli_e3(work: Path, *args: str, fault: str | None = None) -> tuple[int, dict[str, Any] | None]:
    """在新进程里运行演示入口（真实接入）。"""
    env = {k: v for k, v in os.environ.items() if k not in ("HS_WF_E3_FAULT", "HS_WF_FAULT")}
    env["PYTHONIOENCODING"] = "utf-8"
    if fault:
        env["HS_WF_E3_FAULT"] = fault
    cmd = [
        sys.executable,
        str(CLI),
        "--services",
        "e3",
        "--e3-workspace",
        str(Path(work) / "ws"),
        "--db",
        str(Path(work) / "wf.db"),
        *args,
    ]
    proc = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None)


def strip_sample(obj: Any) -> Any:
    """去掉 E3 消息里唯一允许不同的字段 ``meta.sample``（深拷贝，不改原对象）。"""
    out = copy.deepcopy(obj)
    if isinstance(out, dict) and isinstance(out.get("meta"), dict):
        out["meta"].pop("sample", None)
    return out


def tree(root: Path) -> dict[str, str]:
    root = Path(root)
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _diff(a: dict[str, str], b: dict[str, str]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def rebuilt_lives(ws: E3Workspace, run_id: str) -> dict[str, list[dict[str, Any]]]:
    ctx = E3Context(ws)
    with ws.cwd():
        world, *_ = ctx.world(run_id)
        outcome = outcome_from_archive(world, Path("archive") / run_id)
    return {c: [r.model_dump(mode="json") for r in rs] for c, rs in outcome.lives.items()}


def compare(s: sc.Scenario, ws: E3Workspace, view: dict[str, Any]) -> dict[str, Any]:
    """逐项比较直接路径的场景与 LangGraph 路径的工作目录。返回每一项的结果与（不同时的）不同之处。"""
    name = s.name
    run_id = view["run_id"]
    root = ws.root
    out: dict[str, Any] = {
        "scenario": name,
        "run_id_direct": s.receipt.run_id if s.receipt else None,
        "run_id_workflow": run_id,
    }
    out["run_id_same"] = out["run_id_direct"] == run_id

    confirmed = json.loads(
        (root / "workflow/confirmed" / f"{s.confirmed.confirmed_id}.json").read_text(
            encoding="utf-8"
        )
    )
    out["confirmed_same"] = strip_sample(s.confirmed.model_dump(mode="json")) == strip_sample(
        confirmed
    )
    rec = json.loads(
        (root / "workflow/preflights" / f"{s.pre.preflight.preflight_fingerprint}.json").read_text(
            encoding="utf-8"
        )
    )
    out["preflight_same"] = strip_sample(s.pre.preflight.model_dump(mode="json")) == strip_sample(
        rec["preflight"]
    )
    receipts = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((root / "workflow/receipts").glob("*.json"))
    ]
    out["receipt_same"] = len(receipts) == 1 and strip_sample(
        s.receipt.model_dump(mode="json")
    ) == strip_sample(receipts[0])
    out["direct_second_submit"] = s.duplicate.status if s.duplicate else None
    out["progress_same"] = strip_sample(s.progress.model_dump(mode="json")) == strip_sample(
        view["progress"]
    )
    if s.result is not None and view.get("result") is not None:
        out["result_same"] = strip_sample(s.result.model_dump(mode="json")) == strip_sample(
            view["result"]
        )
        out["result_fingerprint_same"] = (
            s.result.result_fingerprint == view["result"]["result_fingerprint"]
        )
    else:
        out["result_same"] = s.result is None and view.get("result") is None
    out["registry_same"] = (s.work / "registry/submissions.json").read_bytes() == (
        root / "registry/submissions.json"
    ).read_bytes()
    comp_a, comp_b = tree(s.work / "compiled" / name), tree(root / "compiled" / name)
    out["compiled_files"] = len(comp_a)
    out["compiled_diff"] = _diff(comp_a, comp_b)
    arch_a, arch_b = tree(s.work / "archive" / name), tree(root / "archive" / run_id)
    out["archive_files"] = len(arch_a)
    out["archive_diff"] = _diff(arch_a, arch_b)
    direct_lives = {c: [r.model_dump(mode="json") for r in rs] for c, rs in s.outcome.lives.items()}
    out["life_years_compared"] = sum(len(v) for v in direct_lives.values())
    out["lives_same"] = direct_lives == rebuilt_lives(ws, run_id) if s.outcome.reports else None
    out["workflow_only_files"] = sorted(
        p.relative_to(root).as_posix() for p in (root / "workflow").rglob("*") if p.is_file()
    )
    out["all_same"] = all(
        [
            out["run_id_same"],
            out["confirmed_same"],
            out["preflight_same"],
            out["receipt_same"],
            out["progress_same"],
            out["result_same"],
            out.get("result_fingerprint_same", True),
            out["registry_same"],
            not out["compiled_diff"],
            not out["archive_diff"],
            out["lives_same"] is not False,
        ]
    )
    return out


@contextlib.contextmanager
def no_fault_env():
    saved = {k: os.environ.pop(k) for k in ("HS_WF_E3_FAULT", "HS_WF_FAULT") if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)
