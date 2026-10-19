"""E1-7A 测试的辅助：读运行配置、写变体配置、在临时目录里跑一个批次（mock 规划器，零网络）。

不是第二个运行器：全部经 ``orchestration.life_runner`` 的正式入口（``preflight`` / ``start_run`` / ``resume_run`` / ``replay_run``）。
变体配置只写在 pytest 的临时目录，不改仓库里的配置。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from agentlayer.life_mock import RuleBasedMockPlanner
from common.run_store import RunStore
from orchestration.life_runner import RunOutcome, RunWorld, preflight, start_run

RUN_CONFIG = Path("configs/life/run_e1_7a.yaml")
FACTS = Path("configs/life/city_facts_e1_7a.yaml")
RUN_ID = "test-e1-7a"
ARM = "arm.main"


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def run_config_variant(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], name: str = "run.yaml"
) -> Path:
    """在临时目录写一份改过的运行配置（名单、事实表等路径照旧指向仓库里的配置，除非 mutate 改了它们）。"""
    data = load_yaml(RUN_CONFIG)
    mutate(data["run"])
    return write_yaml(tmp_path / name, data)


def facts_variant(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """改过的城市事实表。YAML 锚点读进来是同一个对象（改 A95 会连带改 B96），先经 JSON 往返拆开。"""
    data = json.loads(json.dumps(load_yaml(FACTS), default=str))
    mutate(data["city_facts"])
    return write_yaml(tmp_path / "facts.yaml", data)


def batch(world: RunWorld, out: Path, **kw: Any) -> tuple[RunOutcome, RuleBasedMockPlanner]:
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    outcome = start_run(world, planner, out, run_id=RUN_ID, **kw)
    return outcome, planner


def default_world() -> RunWorld:
    return preflight(RUN_CONFIG)


def records(run_dir: Path, stream: str) -> list[dict[str, Any]]:
    """归档库（``archive.sqlite``）里一种记录，按写入顺序。"""
    return RunStore(run_dir).records(stream)


def has_records(run_dir: Path, stream: str) -> bool:
    return RunStore(run_dir).has(stream)


def write_records(run_dir: Path, stream: str, rows: list[dict[str, Any]]) -> None:
    """整表改写（模拟归档被改动）。"""
    RunStore(run_dir).replace(stream, rows)


def rewrite_records(run_dir: Path, stream: str, fn: Callable[[list[dict[str, Any]]], Any]) -> None:
    rows = records(run_dir, stream)
    fn(rows)
    write_records(run_dir, stream, rows)


def snapshot(root: Path) -> dict[str, bytes]:
    """一个运行目录里全部文件的字节（判断"拒绝后原归档保持不变"）。"""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def copy_run(src: Path, dst: Path) -> Path:
    for name, data in snapshot(src).items():
        (dst / name).parent.mkdir(parents=True, exist_ok=True)
        (dst / name).write_bytes(data)
    return dst


def request(out: Path, clone: str, year: int, attempt: int = 1) -> dict[str, Any]:
    return next(
        c["request"]
        for c in records(out, "calls")
        if (c["clone_id"], c["year_index"], c["attempt"]) == (clone, year, attempt)
    )
