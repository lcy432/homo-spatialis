"""依赖方向与外部上传：LangGraph 只在 workflows 包里出现；外部追踪开着就拒绝启动。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from workflows.runtime import TRACING_ENV, WorkflowRefusedError, WorkflowRuntime
from workflows.synthetic import SyntheticWorld

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = ("langgraph", "langchain", "langchain_core", "langsmith", "workflows")
GUARDED = (
    "common",
    "econ",
    "ledger",
    "agentlayer",
    "product",
    "orchestration",
    "worlds",
    "eval",
    "data",
    "ui",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_core_packages_do_not_depend_on_langgraph_or_the_workflow():
    offenders = []
    for package in GUARDED:
        for path in (SRC / package).rglob("*.py"):
            bad = _imports(path) & set(FORBIDDEN)
            if bad:
                offenders.append(f"{path.relative_to(SRC).as_posix()}: {sorted(bad)}")
    assert offenders == []


def test_the_workflow_package_does_not_reach_into_econ():
    for path in (SRC / "workflows").rglob("*.py"):
        assert "econ" not in _imports(path), path.name


@pytest.mark.parametrize("name", TRACING_ENV)
def test_external_tracing_must_be_off(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "true")
    with pytest.raises(WorkflowRefusedError) as e:
        WorkflowRuntime(tmp_path / "wf.db", SyntheticWorld(tmp_path / "world").services())
    assert e.value.code == "external_tracing_enabled"
    assert not (tmp_path / "wf.db").exists()
