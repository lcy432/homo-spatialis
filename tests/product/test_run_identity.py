"""E3-A 收尾 F1：六维产品画像的转换代码进了运行身份。

* 产品运行的运行清单另记 ``source_set`` 与 ``source:common/life_product_profile.py``；旧协议的运行清单一个字段都不多。
* 规则没变：中断、恢复、零调用重放照常。
* 规则变了（只在隔离的源码副本里交换物质与安全的读取）：源码指纹照记、确实不同，但开发期（所有者 2026-10-18）不再因此拒绝
  续跑——改了代码，旧运行仍可接着跑；配置、规则、事实等内容身份照旧核对。
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
from orchestration.life_runner import (
    PRODUCT_SOURCE_SET,
    preflight,
    replay_run,
    resume_run,
    start_run,
)
from product import scenarios as sc

REPO = Path(__file__).resolve().parents[2]
PROFILE_SOURCE = "source:common/life_product_profile.py"
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


def test_product_runs_record_the_product_rule_sources_and_old_protocols_do_not(compiled):
    ids = compiled.pre.world.identities
    assert ids["source_set"] == PRODUCT_SOURCE_SET and PROFILE_SOURCE in ids
    with contextlib.chdir(REPO):
        old = preflight(Path("configs/life/run_e1_convenience_mock.yaml"))
    assert "source_set" not in old.identities and PROFILE_SOURCE not in old.identities


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

import pytest
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
print(json.dumps({"weights": weights, "identity": world.identities["source:common/life_product_profile.py"], **out}, ensure_ascii=False))
"""


def test_a_changed_profile_rule_is_recorded_but_does_not_block_resume(compiled, tmp_path):
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
    assert out["identity"] != world.identities[PROFILE_SOURCE]
    assert out["refused"] is None  # 开发期：源码不同不拦续跑
    assert _digest(archive) != before  # 续跑确实提交了年度
