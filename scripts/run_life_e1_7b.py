"""生活规划器真实模型运行的入口（DECISIONS/0031、0032、0035）。全部经 ``orchestration.life_runner`` 的正式入口；不是第二个运行器。

**用哪个模型由运行配置决定**（``--config``）。不写时用非全量真实 LLM 测试的配置 ``configs/life/run_product_sonnet.yaml``：Claude Sonnet 5.5，
effort max，models.yaml 的 ``test_planner_sonnet`` 角色，经 Claude Agent SDK 单轮调用（所有者 2026-10-19）。全规模的产品流程测试不走这里，
用 ``scripts/run_product_full.py``（Qwen Flash）。Haiku 测试配置（``run_product_haiku.yaml``）与产品样例的 Flash 配置
（``run_product_flash.yaml``）可以用 ``--config`` 显式选。找不到配置里写的模型角色就报错，不回退到别的角色或别的模型。真实运行必须写
``--run-id``（新的编号）。``resume`` 与 ``replay`` 只按归档里冻结的配置与模型执行，不接受 ``--config``，写了就报错——不能用换配置的办法
让另一个模型接着跑旧归档。

离线探针与 mock 对照用的 mock 配置由 ``--mock-config`` 选（默认 ``configs/life/run_product_mock.yaml``）。``offline-check`` 核对 mock 配置与
真实模型配置用的是同一份名单、同一套协议、目录、评分、事实、许可、提示模板与记忆政策，对不上就拒绝。mock 规划器按 mock 配置声明的身份
构造（``agentlayer.life_mock.mock_planner_for``），不认识的规则版本直接报错。

    uv run python scripts/run_life_e1_7b.py offline-check --out <目录> [--config <运行配置>] [--mock-config <mock 配置>]
                                                                        开跑前检查与请求估计（零网络、零费用）
    uv run python scripts/run_life_e1_7b.py pilot --out <目录> --run-id <新编号> [--config …]  先导：名单第一项的第 1 年，提交之后在年度边界停下
    uv run python scripts/run_life_e1_7b.py main --out <目录> --run-id <新编号> [--config …]   整份名单（受配置里的调用上限与付费预算约束）
    uv run python scripts/run_life_e1_7b.py resume --out <目录>          所有者决定之后，从停下的地方恢复（尝试次数与预算不清零）
    uv run python scripts/run_life_e1_7b.py mock --out <目录> [--mock-config <mock 配置> --run-id <编号>]
                                                                        同一名单的 mock 对照（零网络）
    uv run python scripts/run_life_e1_7b.py replay --out <目录> [--report <文件>]
                                                                        只用归档的零调用重放（不需要密钥）；生活重放与付费对账分开报告
    uv run python scripts/run_life_e1_7b.py budget [--config …]          付费预算账本的汇总（读配置里写的那一本）

真实调用只在 pilot、main、resume 三个子命令里发生；凭证在调用时从环境变量或 .env 读取，从不打印、不写进任何文件（Claude 走订阅时
不要密钥，写 ``auth: api_key`` 时读 ``ANTHROPIC_API_KEY``；千问读 ``DASHSCOPE_API_KEY``）。付费预算是按已记录报价执行的应用侧控制，不是供应商级的账单上限；运行配置也可以
显式写 ``unmetered_reason`` 不记账（DECISIONS/0035），这时只受全局调用上限约束。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_llm import (
    LlmPlanner,
    estimate_input_tokens,
    final_request_problems,
    make_transport,
)
from agentlayer.life_mock import mock_planner_for
from agentlayer.life_planning import PlannerCall, render_prompt
from common.config import ModelsConfig
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import (
    DEFAULT_MAX_WORKERS,
    RunOutcome,
    RunWorld,
    _forbidden_texts,
    budget_reconciliation,
    planner_payloads,
    preflight,
    preflight_from_snapshot,
    replay_run,
    resume_run,
    start_run,
)

#: 不写 --config 时的配置（所有者 2026-10-19）：非全量真实 LLM 测试用 Claude Sonnet 5.5 effort max（test_planner_sonnet 角色）
DEFAULT = Path("configs/life/run_product_sonnet.yaml")
#: 产品角色的 Qwen3.8 Flash 配置（DECISIONS/0032）：只在显式 ``--config`` 时使用
PRODUCT = Path("configs/life/run_product_flash.yaml")
MOCK = Path("configs/life/run_product_mock.yaml")
MOCK_RUN_ID = "product-mock"
#: 离线探针要求 mock 配置与真实模型配置相同的身份（同一份名单与资料；规划器、运行配置与预算当然不同）
PAIRED_IDENTITIES = (
    "roster",
    "protocol",
    "scoring_config",
    "scoring_content",
    "city_facts_visible",
    "city_facts_links",
    "permission",
    "prompt_template",
    "memory_policy",
    "illness_events",
)


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(obj) + "\n", encoding="utf-8", newline="\n")


def _live_planner(world: RunWorld) -> LlmPlanner:
    """按运行配置写的模型角色组装真实模型规划器。角色缺失就报错（``ConfigError``），不回退到 base_b 或别的模型。传输层按服务商选
    （千问：OpenAI 兼容端点；Claude：Agent SDK 单轮调用）；运行配置写了付费预算就带账本，写了 ``unmetered_reason`` 就不带。"""
    settings = world.config.planner.llm
    if settings is None:
        raise SystemExit("这份运行配置不是真实模型配置：不调用")
    spec = ModelsConfig.load().spec(settings.model_role)
    if (spec.provider, spec.base_url, spec.model) != (
        settings.provider,
        settings.base_url,
        settings.model,
    ):
        raise SystemExit("configs/models.yaml 的服务商、端点或模型与运行配置不同：不调用")
    if getattr(spec, "version_policy", "dated_snapshot") != settings.effective_version_policy:
        raise SystemExit("configs/models.yaml 的版本策略与运行配置不同：不调用")
    # 密钥只在这里读出，交给传输层；订阅认证不要密钥（用本机已登录的 Claude 订阅）
    key = None if settings.auth == "claude_subscription" else spec.resolve().api_key
    transport = make_transport(settings, api_key=key)
    paid = world.config.paid_budget
    return LlmPlanner(
        settings, transport=transport, budget=None if paid is None else RoundBudget(paid)
    )


def _mock_planner(world: RunWorld):
    """按 mock 配置声明的规划器设定（身份与受控行为）构造对应的 mock；不是 mock 配置或身份不认识就报错。"""
    if world.config.planner.identity.kind != "mock":
        raise SystemExit("mock 配置的规划器不是 mock：不运行")
    try:
        return mock_planner_for(world.config.planner)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


def pairing_problems(live: RunWorld, mock: RunWorld) -> list[str]:
    """离线探针的 mock 配置是否与真实模型配置用同一份名单与资料（逐项比较运行身份，不比较规划器、运行配置与预算）。"""
    out = []
    keys = sorted(
        k
        for k in set(live.identities) | set(mock.identities)
        if k in PAIRED_IDENTITIES or k.startswith(("catalog:", "tax:", "evaluation:"))
    )
    for k in keys:
        if live.identities.get(k) != mock.identities.get(k):
            out.append(f"{k}：真实模型配置与 mock 配置不同")
    return out


def _summary(outcome: RunOutcome, world: RunWorld) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "halt_kind": outcome.halt_kind,
        "pause_reason": outcome.pause_reason,
        "planning_calls": outcome.calls_used,
        "clones": [
            {
                "clone_id": r.clone_id,
                "status": r.status,
                "years_committed": r.years_committed,
                "attempts_by_year": list(r.attempts_by_year),
                "detail": r.detail,
            }
            for r in outcome.records
        ],
    }
    if world.config.paid_budget is not None:
        out["budget"] = RoundBudget(world.config.paid_budget).summary()
    return out


def cmd_offline_check(out: Path, config: Path, mock_config: Path) -> None:
    """开跑前：两份配置都通过运行前检查、用的是同一份名单与资料；用 mock 在同一名单上走一遍，按真实模型的请求体逐份做最终检查与
    输入估计。"""
    live = preflight(config)
    mock = preflight(mock_config)
    problems = pairing_problems(live, mock)
    if problems:
        raise SystemExit(
            f"离线探针的 mock 配置 {mock_config} 与真实模型配置 {config} 不是同一套资料与名单：\n  "
            + "\n  ".join(problems)
        )
    scratch = out / "mock_probe"
    outcome = start_run(mock, _mock_planner(mock), scratch, run_id="e1-7b-offline-probe")
    settings = live.config.planner.llm
    assert settings is not None
    body_of = LlmPlanner(settings, transport=None, budget=None).provider_body  # type: ignore[arg-type]
    forbidden = _forbidden_texts(live.bundle, live.scoring, live.specs)
    rows = []
    for p in planner_payloads(scratch):
        system, user = render_prompt(p)
        body = body_of(PlannerCall(p["request_id"], system, user, p))
        rows.append(
            {
                "request_id": p["request_id"],
                "chars": len(system) + len(user),
                "utf8_bytes": len((system + user).encode("utf-8")),
                "estimate": estimate_input_tokens(body["messages"]),
                "problems": final_request_problems(body, p, forbidden_texts=forbidden),
            }
        )
    estimates = [r["estimate"] for r in rows]
    report = {
        "live_config": config.as_posix(),
        "mock_config": mock_config.as_posix(),
        "live_preflight": "passed",
        "mock_preflight": "passed",
        "paired_identities": "same",
        "identities_live": live.identities,
        "mock_probe_status": outcome.status,
        "requests_checked": len(rows),
        "requests_with_problems": sum(1 for r in rows if r["problems"]),
        "estimate_rule": settings.token_estimate,
        "estimate_max": max(estimates),
        "estimate_min": min(estimates),
        "max_input_tokens": settings.max_input_tokens,
        "under_limit": max(estimates) <= settings.max_input_tokens,
        "rows": rows,
    }
    _write(out / "offline_check.json", report)
    print(_dump({k: v for k, v in report.items() if k not in ("rows", "identities_live")}))


def _workers(world: RunWorld, requested: int | None) -> int:
    """并行的分身数：不写时所有分身同时过（最多 ``DEFAULT_MAX_WORKERS`` 个）。"""
    return requested or min(len(world.order), DEFAULT_MAX_WORKERS)


def cmd_live(
    kind: str, out: Path, config: Path, run_id: str, *, workers: int | None = None
) -> None:
    world = preflight(config)
    planner = _live_planner(world)
    stop = 1 if kind == "pilot" else None
    # 先导只跑名单第一项的第 1 年：顺序执行，不让别的分身同时多花调用
    n = 1 if kind == "pilot" else _workers(world, workers)
    outcome = start_run(world, planner, out, run_id=run_id, stop_after_commits=stop, workers=n)
    summary = _summary(outcome, world) | {"requests_sent_this_process": planner.requests_sent}
    _write(out.parent / f"{out.name}.summary.json", summary)
    print(_dump(summary))


def cmd_resume(out: Path, stop_after_commits: int | None, *, workers: int | None = None) -> None:
    world = preflight_from_snapshot(out)
    planner = _live_planner(world)
    outcome = resume_run(
        world,
        planner,
        out,
        stop_after_commits=stop_after_commits,
        workers=1 if stop_after_commits else _workers(world, workers),
    )
    summary = _summary(outcome, world) | {"requests_sent_this_process": planner.requests_sent}
    _write(out.parent / f"{out.name}.resume.summary.json", summary)
    print(_dump(summary))


def cmd_mock(out: Path, mock_config: Path, run_id: str, *, workers: int | None = None) -> None:
    world = preflight(mock_config)
    outcome = start_run(
        world, _mock_planner(world), out, run_id=run_id, workers=_workers(world, workers)
    )
    summary = _summary(outcome, world)
    _write(out.parent / f"{out.name}.summary.json", summary)
    print(_dump(summary))


def cmd_replay(out: Path, report: Path | None) -> None:
    """零调用重放（不需要密钥）。生活重放与付费对账分开报告；对账只读账本，账本不存在就报"没有账本"，不创建。"""
    world = preflight_from_snapshot(out)
    result = {
        "life_replay": replay_run(world, out),
        "payment_reconciliation": budget_reconciliation(world, out),
    }
    _write(report or out.parent / f"{out.name}.replay.json", result)
    print(_dump(result))


def cmd_budget(config: Path) -> None:
    world = preflight(config)
    if world.config.paid_budget is None:
        print(_dump({"metered": False, "unmetered_reason": world.config.unmetered_reason}))
        return
    print(_dump(RoundBudget(world.config.paid_budget).summary()))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "command", choices=["offline-check", "pilot", "main", "resume", "mock", "replay", "budget"]
    )
    ap.add_argument("--out", type=Path)
    ap.add_argument("--stop-after-commits", type=int, default=None)
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"main / resume / mock 同时过生活的分身数（默认所有分身，最多 {DEFAULT_MAX_WORKERS}；pilot 固定 1）",
    )
    ap.add_argument(
        "--report", type=Path, default=None, help="replay：结果写到这里（默认写在归档旁边）"
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"offline-check / pilot / main / budget 的运行配置（默认测试配置 {DEFAULT}；Flash：{PRODUCT}）；"
        "resume / replay / mock 不接受",
    )
    ap.add_argument(
        "--mock-config",
        type=Path,
        default=None,
        help=f"offline-check / mock 用的 mock 运行配置（默认 {MOCK}）",
    )
    ap.add_argument(
        "--run-id",
        default=None,
        help="pilot / main：新的运行编号（必须写，不沿用旧编号）；mock 用非默认配置时也必须写",
    )
    args = ap.parse_args()
    if args.command != "budget" and args.out is None:
        ap.error("--out 是必须的")
    if args.command in ("pilot", "main") and not args.run_id:
        ap.error("pilot / main 必须写 --run-id（新的运行编号）")
    if args.command in ("resume", "replay", "mock") and args.config is not None:
        ap.error(f"{args.command} 只按归档里冻结（或 mock 自己）的配置与模型执行，不接受 --config")
    if args.command not in ("offline-check", "mock") and args.mock_config is not None:
        ap.error(f"{args.command} 不用 mock 配置，不接受 --mock-config")
    if args.command == "mock" and args.mock_config is not None and not args.run_id:
        ap.error("mock 用非默认的 mock 配置时必须写 --run-id（新的运行编号）")
    config = args.config or DEFAULT
    mock_config = args.mock_config or MOCK
    if args.command == "offline-check":
        cmd_offline_check(args.out, config, mock_config)
    elif args.command in ("pilot", "main"):
        cmd_live(args.command, args.out, config, args.run_id, workers=args.workers)
    elif args.command == "resume":
        cmd_resume(args.out, args.stop_after_commits, workers=args.workers)
    elif args.command == "mock":
        cmd_mock(args.out, mock_config, args.run_id or MOCK_RUN_ID, workers=args.workers)
    elif args.command == "replay":
        cmd_replay(args.out, args.report)
    else:
        cmd_budget(config)


if __name__ == "__main__":
    main()
