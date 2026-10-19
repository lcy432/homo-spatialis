"""E1-7B 离线测试的辅助：一个自带配置的临时"仓库"、可编排的离线传输替身与真实模型规划器的组装。零网络、零费用。

* ``make_repo``：把 E1-7B 的六份配置与 ``configs/models.yaml`` 复制进临时目录（可以先改），付费账本指向临时目录里的
  ``budget``——测试绝不碰真实试跑的账本。测试用 ``monkeypatch.chdir`` 进入这个目录，所有路径都是相对路径，输入冻结照常工作。
* ``ScriptedTransport``：按顺序执行脚本里的动作（服务错误、指定回复、或用 E1-7A 的 mock 规则从请求里读出的安全投影生成一份
  合法计划）；它只拿到真正要发给供应商的请求体，和真实传输一样。

不是第二个运行器：全部经 ``orchestration.life_runner`` 的正式入口。
"""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from agentlayer.life_llm import (
    QwenPlanner,
    TransportError,
    TransportResponse,
    estimate_input_tokens,
)
from agentlayer.life_mock import RuleBasedMockPlanner
from agentlayer.life_planning import PROMPT_USER_HEAD, PlannerCall
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import RunWorld, preflight

LIVE_CONFIG = Path("configs/life/run_e1_7b_live.yaml")
#: 产品默认（2026-10-13，DECISIONS/0032）：Qwen3.8 Flash；临时仓库里账本放在 ``budget-flash``
FLASH_CONFIG = Path("configs/life/run_e1_7b_flash.yaml")
FLASH_MODEL = "qwen3.8-flash"
MOCK_CONFIG = Path("configs/life/run_e1_7b_mock.yaml")
MODEL = "qwen3.7-max-2026-06-08"
FILES = (
    "configs/life/synthetic_e1_7b_pilot.yaml",
    "configs/life/scoring_e1_7b_pilot.yaml",
    "configs/life/evaluation_e1_7b.yaml",
    "configs/life/city_facts_e1_7b.yaml",
    "configs/life/roster_e1_7b.yaml",
    "configs/life/run_e1_7b_live.yaml",
    "configs/life/run_e1_7b_mock.yaml",
    "configs/life/run_e1_7b_flash.yaml",
    "configs/models.yaml",
)
REPO = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    # 先经 JSON 往返：拆开 YAML 锚点（改一处不连带改别处），日期写成字符串
    plain = json.loads(json.dumps(data, default=str))
    path.write_text(
        yaml.safe_dump(plain, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )


def make_repo(
    root: Path,
    *,
    life: Callable[[dict[str, Any]], None] | None = None,
    run: Callable[[dict[str, Any]], None] | None = None,
    flash: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """临时仓库：复制配置（可以改生活输入与运行配置），付费账本放在 ``budget``。返回目录（调用方再 chdir 进去）。"""
    for rel in FILES:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    run_path = root / LIVE_CONFIG
    data = load_yaml(run_path)
    data["run"]["paid_budget"]["ledger_dir"] = "budget"
    if run is not None:
        run(data["run"])
    write_yaml(run_path, data)
    flash_path = root / FLASH_CONFIG
    fdata = load_yaml(flash_path)
    fdata["run"]["paid_budget"]["ledger_dir"] = "budget-flash"
    if flash is not None:
        flash(fdata["run"])
    write_yaml(flash_path, fdata)
    if life is not None:
        life_path = root / FILES[0]
        doc = json.loads(json.dumps(load_yaml(life_path), default=str))
        life(doc)
        write_yaml(life_path, doc)
    return root


def live_world() -> RunWorld:
    return preflight(LIVE_CONFIG)


def flash_world() -> RunWorld:
    return preflight(FLASH_CONFIG)


def mock_world() -> RunWorld:
    return preflight(MOCK_CONFIG)


def payload_of(body: dict[str, Any]) -> dict[str, Any]:
    user = body["messages"][1]["content"]
    assert user.startswith(PROMPT_USER_HEAD + "```json\n") and user.endswith("\n```")
    return json.loads(user[len(PROMPT_USER_HEAD) + len("```json\n") : -len("\n```")])


def mock_text(body: dict[str, Any]) -> str:
    """E1-7A mock 规则读同一份安全投影给出的计划文字（离线替身用它当"模型"的合法回答）。"""
    payload = payload_of(body)
    call = PlannerCall(
        payload["request_id"],
        body["messages"][0]["content"],
        body["messages"][1]["content"],
        payload,
    )
    return RuleBasedMockPlanner().plan(call)


def reply(
    text: str,
    body: dict[str, Any],
    *,
    finish: str | None = "stop",
    model: str = MODEL,
    usage: bool = True,
) -> TransportResponse:
    est = estimate_input_tokens(body["messages"])
    return TransportResponse(
        text=text,
        finish_reason=finish,
        model=model,
        prompt_tokens=est // 3 if usage else None,
        completion_tokens=max(1, len(text) // 2) if usage else None,
    )


def timeout() -> TransportError:
    return TransportError("timeout", retryable=True)


class ScriptedTransport:
    """离线传输替身。脚本里每一步：``TransportError``（抛出）、``"mock"``（mock 规则的合法计划）、字符串（作为回复原文）、
    或 ``callable(body) -> TransportResponse``；脚本用完之后一律 ``"mock"``。记下每次收到的请求体与超时。"""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.bodies: list[dict[str, Any]] = []
        self.timeouts: list[float] = []

    def send(self, body: dict[str, Any], *, timeout: float) -> TransportResponse:
        self.bodies.append(copy.deepcopy(body))
        self.timeouts.append(timeout)
        step = self.script.pop(0) if self.script else "mock"
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(body)
        if step == "mock":  # 替身回显请求里的模型名（Max 测试里就是 MODEL，不变）
            return reply(mock_text(body), body, model=body["model"])
        return reply(str(step), body, model=body["model"])


def qwen(
    world: RunWorld,
    transport: Any,
    *,
    settings: Any = None,
    budget: Any = None,
    sleeps: list[float] | None = None,
) -> QwenPlanner:
    sleeps = [] if sleeps is None else sleeps
    return QwenPlanner(
        settings or world.config.planner.llm,
        transport=transport,
        budget=budget or RoundBudget(world.config.paid_budget),
        sleep=sleeps.append,
    )


from .planning_support import (  # noqa: E402,F401
    has_records,
    records,
    rewrite_records,
    write_records,
)


def jsonl(path: Path) -> list[dict[str, Any]]:
    """预算账本（ledger.jsonl，在运行目录之外）仍是 jsonl；运行归档的记录用 records()。"""
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
