"""全规模的产品流程测试（所有者 2026-10-19）：走完整的产品链，用 Qwen 最新的 Flash（configs/models.yaml 的 product_planner）规划。

全规模指：资料包里所有可用城市、每座城不少于 4 个分身（重复序号 0 … N−1）、完整的模拟年限（默认按协议，可以用 --years 加长）。
非全量的真实 LLM 测试不走这里，用 ``scripts/run_life_e1_7b.py``（默认 Sonnet 5.5 effort max）。

产品链：资料包 → 答案草稿 → 审阅 → 确认 → 编译 → E1 预检查 → 运行 → E1 评价与汇总，与 ``product.scenarios`` 的离线闭环同一条路径；
只把编译出来的 mock 规划器换成 ``configs/life/run_product_flash.yaml`` 里的 Flash 规划器设定（模型调用设定与报价都从那一份读，不在这里
另写一套）。付费：每次运行在运行目录里开一本新账本，次数上限 = 年度数 × 3 份候选 × 2 次服务请求，费用上限由 ``--max-cost-cny`` 给
（按已记录的报价估算，应用侧控制，不是供应商账单上限）。密钥从环境变量或 .env 读 ``DASHSCOPE_API_KEY``，不打印、不写进任何文件。

    uv run python scripts/run_product_full.py --out runs/<日期>-product-full                 真实运行（付费）
    uv run python scripts/run_product_full.py --out runs/<日期>-product-full --repeats 6     每城 6 个分身
    uv run python scripts/run_product_full.py --out runs/<日期>-product-full --years 5       模拟 5 年
    uv run python scripts/run_product_full.py --out <目录> --mock                            同一条产品链，mock 规划器（零网络、零费用）

每个分身各自并行地过（``--workers``，默认所有分身，最多 16 个；1 是顺序）：分身的生活只取决于自己的计划，结果与顺序运行相同。
运行中每 15 秒报一次进度与用时；结束时写 ``<out>/summary.json``。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_llm import LlmPlanner, llm_identity, make_transport
from agentlayer.life_mock import mock_planner_for
from common.config import ModelsConfig
from common.life_planning_contracts import LlmSettings
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import (
    DEFAULT_MAX_WORKERS,
    RunOutcome,
    RunWorld,
    preflight,
    start_run,
)
from product import scenarios as sc
from product.pack import DataPack

REPO = Path(__file__).resolve().parent.parent
FLASH_CONFIG = REPO / "configs/life/run_product_flash.yaml"
#: 全规模的下限：每座城不少于这么多分身（"每个城市超过 3 个"）
MIN_REPEATS = 4
POLL_SECONDS = 15


def _with_repeats(n: int) -> None:
    """每座城 n 个重复分身：改资料包的 run.repeat_indexes，与 ``product.scenarios.closed_loop`` 的做法相同。"""
    original = sc.load_pack

    def load(*args: Any) -> DataPack:
        pack = original(*args)
        raw = json.loads(json.dumps(pack.raw, default=str))
        raw["pack"]["run"]["repeat_indexes"] = list(range(n))
        return DataPack(raw, pack.path)

    sc.load_pack = load


def _with_years(years: int) -> None:
    """模拟年限：工作区复制好资料以后，改工作区里那份生活输入的 protocol.simulation_years（仓库里的样例不动）。"""
    original = sc.workspace

    def workspace(work: Path) -> Path:
        w = original(work)
        pack = yaml.safe_load((w / sc.PACK_PATH).read_text(encoding="utf-8"))
        path = w / pack["pack"]["e1_base"]["life_input"]
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["protocol"]["simulation_years"] = years
        text = (
            json.dumps(data, ensure_ascii=False, default=str)
            if path.suffix == ".json"
            else yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
        )
        path.write_text(text, encoding="utf-8", newline="\n")
        return w

    sc.workspace = workspace


def compile_full(
    work: Path, *, repeats: int, years: int | None, abilities: tuple[str, ...] = ()
) -> sc.Scenario:
    """产品链编译到 E1 预检查（mock 规划器的运行配置）。"""
    _with_repeats(repeats)
    if years is not None:
        _with_years(years)
    s = sc.preflight_only(work, "full", **({"abilities": abilities} if abilities else {}))
    if s.pre.preflight.status != "ready":
        raise SystemExit(f"E1 预检查没有通过：{s.pre.preflight.status}")
    return s


def flash_run_config(compiled: Path, *, years_total: int, max_cost_cny: float) -> Path:
    """把编译出的运行配置里的 mock 规划器换成 Flash；调用设定与报价从 run_product_flash.yaml 读。"""
    # 运行前检查要按 configs/models.yaml 核对模型角色；产品工作区里没有这一份，从仓库复制
    ws = compiled.parents[1]
    (ws / "configs").mkdir(exist_ok=True)
    shutil.copyfile(REPO / "configs/models.yaml", ws / "configs/models.yaml")
    flash = yaml.safe_load(FLASH_CONFIG.read_text(encoding="utf-8"))["run"]
    data = yaml.safe_load((compiled / "run.yaml").read_text(encoding="utf-8"))
    run = data["run"]
    llm = flash["planner"]["llm"]
    run["run_config_id"] = f"{run['run_config_id']}.flash-full"
    run["version"] = flash["version"]
    run["planning"]["global_call_limit"] = years_total * 3
    run["planning"]["transport_retries"] = flash["planning"]["transport_retries"]
    run["planning"]["note"] = (
        "全规模真实模型：每年一次规划、最多两次修订；每份候选最多再试 1 次服务请求；顺序调度"
    )
    run["planner"] = {
        "identity": llm_identity(LlmSettings.model_validate(llm)).model_dump(mode="json"),
        "llm": llm,
    }
    run["paid_budget"] = {
        "ledger_dir": "budget",
        "max_requests": years_total * 3 * 2,
        "max_cost_cny": max_cost_cny,
        "price": flash["paid_budget"]["price"],
    }
    run["note"] = f"全规模的产品流程测试（{llm['model']}）"
    path = compiled / "run_flash_full.yaml"
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return path


def flash_planner(world: RunWorld) -> LlmPlanner:
    settings = world.config.planner.llm
    assert settings is not None
    spec = ModelsConfig.load(REPO / "configs/models.yaml").spec(settings.model_role)
    if (spec.provider, spec.base_url, spec.model) != (
        settings.provider,
        settings.base_url,
        settings.model,
    ):
        raise SystemExit("configs/models.yaml 的服务商、端点或模型与运行配置不同：不调用")
    transport = make_transport(settings, api_key=spec.resolve().api_key)
    return LlmPlanner(settings, transport=transport, budget=RoundBudget(world.config.paid_budget))


def progress(db: Path, planned: int) -> str:
    if not db.is_file():
        return "还没有记录"
    with sqlite3.connect(db) as con:

        def one(sql: str) -> tuple[Any, ...] | None:
            try:
                return con.execute(sql).fetchone()
            except sqlite3.OperationalError:
                return None

        acc = one("SELECT count(*) FROM accepted") or (0,)
        out = one(
            "SELECT count(*), sum(json_extract(body,'$.prompt_tokens')),"
            " sum(json_extract(body,'$.completion_tokens')),"
            " sum(CASE WHEN json_extract(body,'$.status') = 'error' THEN 1 ELSE 0 END),"
            " sum(CAST(json_extract(body,'$.cost_cny') AS REAL))"
            " FROM provider_calls WHERE json_extract(body,'$.event') = 'outcome'"
        ) or (0, 0, 0, 0, None)
        calls = one("SELECT count(*) FROM calls") or (0,)
    n, pin, pout, err, cost = out
    money = f"，按报价估算 {cost:.4f} 元" if cost else ""
    return (
        f"已接受 {acc[0]}/{planned} 个年度，规划调用 {calls[0]} 次，服务请求 {n} 次（失败 {err or 0}），"
        f"token 输入 {pin or 0:,} / 输出 {pout or 0:,}{money}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录（已存在就拒绝）")
    ap.add_argument(
        "--repeats", type=int, default=MIN_REPEATS, help=f"每座城的分身数（≥ {MIN_REPEATS}）"
    )
    ap.add_argument("--years", type=int, default=None, help="模拟年限（默认按协议）")
    ap.add_argument(
        "--max-cost-cny", type=float, default=10.0, help="这次运行的费用上限（元，按报价估算）"
    )
    ap.add_argument(
        "--mock", action="store_true", help="同一条产品链，mock 规划器（零网络、零费用）"
    )
    ap.add_argument("--run-id", default=None)
    ap.add_argument(
        "--abilities",
        nargs="*",
        default=[],
        help="草稿里明确确认的能力（例如 edu_at_least_hs）；系统不从职业称呼自动授予",
    )
    ap.add_argument(
        "--pack",
        default=None,
        help="资料包（默认 E3-A 合成资料包；E3B 六城真实资料包是 configs/product/e3b_us_v1_pack.yaml）",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"同时过生活的分身数（默认所有分身，最多 {DEFAULT_MAX_WORKERS}；1 是顺序）",
    )
    args = ap.parse_args()
    if args.repeats < MIN_REPEATS:
        raise SystemExit(
            f"全规模要求每座城不少于 {MIN_REPEATS} 个分身；小规模测试用 scripts/run_life_e1_7b.py"
        )
    if args.out.exists():
        raise SystemExit(f"{args.out} 已存在：换一个新目录")
    work = args.out.resolve()
    if args.pack:
        sc.PACK_PATH = Path(args.pack)
    if not args.mock:
        # 运行目录可以在仓库之外：密钥的后备从仓库根目录的 .env 读（进程环境优先，不覆盖），不打印
        load_dotenv(REPO / ".env", override=False)
    s = compile_full(
        work / "ws", repeats=args.repeats, years=args.years, abilities=tuple(args.abilities)
    )
    ws = s.work
    world = s.pre.world
    assert world is not None
    years = world.bundle.protocol.simulation_years
    planned = len(world.order) * years
    cities = sorted({str(e.city) for e in world.roster.entries})
    workers = args.workers or min(len(world.order), DEFAULT_MAX_WORKERS)
    print(
        f"名单：{len(cities)} 座城 × 每城 {args.repeats} 个分身 × {years} 年 = {planned} 个年度；"
        f"{workers} 个分身同时过",
        flush=True,
    )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"product-full-{'mock' if args.mock else 'flash'}-{stamp}"
    archive = ws / "archive" / "full"
    with contextlib.chdir(ws):
        if args.mock:
            planner: Any = mock_planner_for(world.config.planner)
        else:
            cfg = flash_run_config(
                ws / "compiled/full", years_total=planned, max_cost_cny=args.max_cost_cny
            )
            world = preflight(cfg.relative_to(ws))
            planner = flash_planner(world)
            print(
                f"Flash：{world.config.planner.llm.model}；账本 {ws / 'budget'}，"
                f"上限 {planned * 6} 次请求、{args.max_cost_cny} 元（按报价估算）",
                flush=True,
            )
        t0 = time.monotonic()
        done = threading.Event()

        def report() -> None:
            while not done.wait(POLL_SECONDS):
                mins, secs = divmod(int(time.monotonic() - t0), 60)
                print(
                    f"[{mins:02d}:{secs:02d}] {progress(archive / 'archive.sqlite', planned)}",
                    flush=True,
                )

        threading.Thread(target=report, daemon=True).start()
        try:
            outcome: RunOutcome = start_run(world, planner, archive, run_id=run_id, workers=workers)
        finally:
            done.set()
    total = int(time.monotonic() - t0)
    clones = [
        {"clone_id": r.clone_id, "status": r.status, "years": r.years_committed, "detail": r.detail}
        for r in outcome.records
    ]
    summary = {
        "run_id": run_id,
        "planner": "mock" if args.mock else world.config.planner.llm.model,
        "cities": cities,
        "repeats": args.repeats,
        "years": years,
        "planned_years": planned,
        "status": outcome.status,
        "workers": workers,
        "seconds": total,
        "progress": progress(archive / "archive.sqlite", planned),
        "clones": clones,
    }
    (work / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"结束：{outcome.status}，用时 {total // 60} 分 {total % 60} 秒", flush=True)
    print("最终：" + summary["progress"], flush=True)
    for c in clones:
        print(f"  {c['clone_id']}  {c['status']}  {c['years']} 年  {c['detail'] or ''}", flush=True)


if __name__ == "__main__":
    main()
