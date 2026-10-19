"""流程测试的公共工具：合成世界、同进程运行入口、跨进程调用演示入口。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from workflows.runtime import WorkflowRuntime
from workflows.synthetic import SyntheticWorld

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "scripts" / "product_workflow.py"

PROFILE = {"person_id": "u301", "note": "合成画像占位"}
BASE = {
    "years": 3,
    "cities": ["K51", "M52"],
    "outcomes": {"K51": "C", "M52": "M"},
    "max_paid_calls": 12,
}


def seed(world: SyntheticWorld, name: str = "normal", **overrides: Any):
    profile = world.write_input("profile", PROFILE, kind="confirmed_profile")
    inputs = world.write_input(name, {**BASE, **overrides}, kind="run_inputs")
    return profile, inputs


def runtime(tmp: Path, world: SyntheticWorld, db: str = "wf.db") -> WorkflowRuntime:
    return WorkflowRuntime(tmp / db, world.services())


def cli(
    tmp: Path, *args: str, fault: str | None = None, db: str = "wf.db"
) -> tuple[int, dict[str, Any] | None]:
    """在一个新进程里运行演示入口。返回退出码与公开视图（进程被故障注入杀掉时没有输出）。"""
    env = {k: v for k, v in os.environ.items() if k != "HS_WF_FAULT"}
    env["PYTHONIOENCODING"] = "utf-8"
    if fault:
        env["HS_WF_FAULT"] = fault
    cmd = [
        sys.executable,
        str(CLI),
        "--db",
        str(tmp / db),
        "--synthetic-root",
        str(tmp / "world"),
        *args,
    ]
    proc = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None)


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def years_executed(world: SyntheticWorld, run_id: str) -> list[int]:
    return [e["year"] for e in world.executions(run_id)]
