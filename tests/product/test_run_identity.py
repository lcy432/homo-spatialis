"""运行清单与代码改动（开发期，所有者 2026-10-18）。

* 运行清单不记源码指纹（产品运行与旧协议都没有 ``source:*`` / ``source_set``）。
* 规则没变：中断、恢复、零调用重放照常。
* 改了代码（只在隔离的源码副本里交换物质与安全的读取）：旧运行照样可以续跑；配置、规则、事实等内容身份照旧核对。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentlayer.life_mock import mock_planner_for
from orchestration.life_runner import preflight, replay_run, resume_run, start_run
from product import scenarios as sc

REPO = Path(__file__).resolve().parents[2]
OLD = "k: Fraction(self.dimension_points[k])"
SWAPPED = (
    'k: Fraction(self.dimension_points[{"material": "safety", "safety": "material"}.get(k, k)])'
)


def _digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return sc.preflight_only(tmp_path_factory.mktemp("identity"), "normal")


def test_no_run_records_source_fingerprints(compiled):
    with contextlib.chdir(REPO):
        old = preflight(Path("configs/life/run_product_mock.yaml"))
    for ids in (compiled.pre.world.identities, old.identities):
        assert not [k for k in ids if k.startswith("source:") or k == "source_set"]


def test_unchanged_rules_interrupt_resume_and_replay_normally(compiled, tmp_path):
    world = compiled.pre.world
    archive = tmp_path / "archive"
    with contextlib.chdir(compiled.work):
        first = start_run(
            world, mock_planner_for(world.config.planner), archive, stop_after_calls=2
        )
        assert first.status == "interrupted" and first.calls_used == 2
        done = resume_run(world, mock_planner_for(world.config.planner), archive)
        assert done.status == "completed" and done.calls_used == 6
        replay = replay_run(world, archive)
    assert replay["planner_calls"] == 0 and replay["bindings_identical"]
    assert all(replay["evaluations_identical"].values())


CHILD = r"""
import contextlib, json, sys
from pathlib import Path
src, workspace, archive = sys.argv[1:4]
sys.path.insert(0, str(Path(src).resolve()))
from orchestration.life_runner import preflight, resume_run, RunRefusedError
from agentlayer.life_mock import mock_planner_for
from econ.life_scoring import preference_from_confirmed_profile
with contextlib.chdir(workspace):
    world = preflight(Path("compiled/normal/run.yaml"))
    weights = preference_from_confirmed_profile(world.bundle.product_profiles[0]).dimension_weights
    try:
        resume_run(world, mock_planner_for(world.config.planner), Path(archive), stop_after_commits=1)
        out = {"refused": None}
    except RunRefusedError as exc:
        out = {"refused": str(exc)}
print(json.dumps({"weights": weights, **out}, ensure_ascii=False))
"""


def test_changed_code_does_not_block_resume(compiled, tmp_path):
    mutated = tmp_path / "isolated-src"
    shutil.copytree(REPO / "src", mutated, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = mutated / "common/life_product_profile.py"
    text = target.read_text(encoding="utf-8")
    assert text.count(OLD) == 1
    target.write_text(text.replace(OLD, SWAPPED), encoding="utf-8", newline="\n")
    world = compiled.pre.world
    archive = tmp_path / "archive"
    with contextlib.chdir(compiled.work):
        start_run(world, mock_planner_for(world.config.planner), archive, stop_after_calls=0)
    before = _digest(archive)
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [sys.executable, "-B", str(script), str(mutated), str(compiled.work), str(archive)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=True,
    )
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["weights"]["material"] == 0.2 and out["weights"]["safety"] == 0.3  # 副本确实换了规则
    assert out["refused"] is None
    assert _digest(archive) != before  # 续跑确实提交了年度
