"""intake-02（E3-B）真实资料的命令行入口。

只有 ``fetch`` 会联网（显式命令；通用 User-Agent，不带密钥、不注册、不付费）；其余命令都只读缓存、工作目录与冻结库。

    uv run python scripts/intake_data.py fetch [--only 前缀 ...] [--skip 前缀 ...] [--mode cache_first|refresh]
    uv run python scripts/intake_data.py build [--stop-after 阶段] [--thread 流程编号]   # LangGraph：开始或接着上次停下的地方跑
    uv run python scripts/intake_data.py status [--thread 流程编号]
    uv run python scripts/intake_data.py query --sector food --cbsa 41860 [--kind ...] [--attr 键=值 ...] [--page 1 --per-page 20]
    uv run python scripts/intake_data.py menu --cbsa 41860 --year 1 --sector housing [--env-repeat 0] [--page 1]
    uv run python scripts/intake_data.py lineage <机会编号>
    uv run python scripts/intake_data.py export [--out data/frozen/intake/us-v1]

默认路径：原始文件缓存 ``data/raw/intake``（不进仓库）；阶段产物 ``runs/intake/us-v1/work``；流程 checkpoint
``runs/intake/us-v1/data_build.sqlite``；冻结库在工作目录的 ``freeze/<身份>/intake.sqlite``（只在本机）。``export`` 把冻结库的清单、
覆盖矩阵、映射诊断与菜单索引写到 ``data/frozen/intake/us-v1``（进仓库）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
from product.realdata import geo, publish, query, store
from product.realdata.build import STAGES, BuildContext, RawChangedError
from product.realdata.config import DEFAULT_CONFIG, BuildConfig, load_config
from product.realdata.fetch import DEFAULT_CACHE, fetch
from product.realdata.sources import all_requests
from workflows.data_build import DataBuildRefusedError, DataBuildRuntime

DEFAULT_WORK = Path("runs/intake/us-v1/work")
DEFAULT_CHECKPOINT = Path("runs/intake/us-v1/data_build.sqlite")
DEFAULT_EXPORT = Path("data/frozen/intake/us-v1")
REPRO = [
    "uv sync",
    "uv run python scripts/intake_data.py fetch            # 联网获取原始文件（显式；SF 的分类查询偶尔超时，可以单独重试）",
    "uv run python scripts/intake_data.py build            # 离线：标准化 → A/B 映射 → 目录与菜单 → 检查 → 冻结",
    "uv run python scripts/intake_data.py export           # 把清单与报告写到 data/frozen/intake/us-v1",
]


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _states(cache: Path, cfg: BuildConfig) -> list[str]:
    d = fetch(geo.delineation_request(), cache, "cache_first")
    if not d.ok:
        raise SystemExit(f"划定文件获取失败：{d.record.status} {d.record.note}")
    all_geo = geo.parse_delineation(d.content)  # type: ignore[arg-type]
    return sorted({c.state for m in cfg.metros for c in all_geo[m.cbsa].counties})


def cmd_fetch(args: argparse.Namespace) -> int:
    cache = Path(args.cache)
    cfg = load_config(args.config)
    reqs = all_requests(cfg, _states(cache, cfg))
    log: dict[str, dict] = {}
    for name, req in reqs.items():
        if args.only and not any(name.startswith(p) for p in args.only):
            continue
        if args.skip and any(name.startswith(p) for p in args.skip):
            continue
        r = fetch(req, cache, args.mode)
        log[name] = r.record.model_dump(mode="json") | {"attempted_at": _now()}
        print(f"{r.record.status:10} {r.record.bytes or ''!s:>10} {name}", flush=True)
    path = cache / "_fetch_log.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    old.update(log)
    path.write_text(
        json.dumps(old, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    bad = [n for n, v in log.items() if v["status"] != "ok"]
    print(f"完成：{len(log) - len(bad)} 个成功，{len(bad)} 个没有成功 {bad}")
    return 0


def _runtime(args: argparse.Namespace) -> DataBuildRuntime:
    ctx = BuildContext(
        config=load_config(args.config), work=Path(args.work), cache=Path(args.cache)
    )
    return DataBuildRuntime(Path(args.checkpoint), ctx)


def _short(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": status["thread_id"],
        "status": status["status"],
        "next": status["next"],
        "stages": [
            {k: v for k, v in s.items() if k in ("stage", "state", "identity", "summary")}
            for s in status["stages"]
        ],
    }


def cmd_build(args: argparse.Namespace) -> int:
    with _runtime(args) as rt:
        try:
            st = rt.run(args.thread, stop_after=args.stop_after)
        except DataBuildRefusedError as exc:
            print(f"拒绝：{exc.code} {exc.message_zh}", file=sys.stderr)
            return 3
        except RawChangedError as exc:
            print(f"拒绝：raw_changed {exc}", file=sys.stderr)
            return 3
        _print(_short(st))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with _runtime(args) as rt:
        _print(_short(rt.status(args.thread)))
    return 0


def _db(args: argparse.Namespace) -> Path:
    if args.db_file:
        return Path(args.db_file)
    with _runtime(args) as rt:
        return rt.frozen_db(args.thread)


def cmd_query(args: argparse.Namespace) -> int:
    attrs = dict(a.split("=", 1) for a in args.attr or [])
    conn = store.open_readonly(_db(args))
    try:
        page = query.opportunities(
            conn,
            sector=args.sector,
            cbsa=args.cbsa,
            kind=args.kind,
            attributes=attrs,
            text=args.text,
            page=args.page,
            per_page=args.per_page,
        )
    finally:
        conn.close()
    _print(
        {
            "total": page.total,
            "page": page.page,
            "per_page": page.per_page,
            "items": [
                {k: o[k] for k in ("opportunity_id", "label", "data_status", "supply_status")}
                | {"compile": o["compile"]["status"], "amount": _amount(o)}
                for o in page.items
            ],
        }
    )
    return 0


def _amount(o: dict[str, Any]) -> dict[str, Any] | None:
    """机会的金额（月租、年薪、活动收费、每月食品费用、单价、学费……）：按机会登记的金额事实读，保留含义、单位、计价口径与性质；
    缺失就显示状态与原因（零是有值，不是缺失）。没有金额的机会（例如城市背景）返回 None。"""
    name = o.get("price_fact")
    f = next((x for x in o["facts"] if x["name"] == name), None) if name else None
    if f is None:
        return None
    if f["status"] == "present":
        return {
            "fact": name,
            "value": f["value"],
            "unit": f.get("unit"),
            "numeraire": f.get("numeraire"),
            "kind": f.get("kind"),
        }
    return {"fact": name, "status": f["status"], "reason": f.get("reason")}


def cmd_menu(args: argparse.Namespace) -> int:
    conn = store.open_readonly(_db(args))
    try:
        doc = query.menu(conn, args.cbsa, args.year, args.sector, args.env_repeat)
        if doc is None:
            print("没有这份菜单", file=sys.stderr)
            return 1
        page = query.menu_items(conn, doc, args.page, args.per_page)
    finally:
        conn.close()
    head = {
        k: doc[k] for k in ("menu_id", "policy", "selection", "n_eligible", "seed", "algorithm")
    }
    _print(
        head
        | {
            "total": page.total,
            "page": page.page,
            "items": [
                {"opportunity_id": o["opportunity_id"], "label": o["label"], "amount": _amount(o)}
                for o in page.items
            ],
        }
    )
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    conn = store.open_readonly(_db(args))
    try:
        _print(query.lineage(conn, args.opportunity_id))
    finally:
        conn.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    _print(publish.write_summary(_db(args), Path(args.out), repro=REPRO))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    ap.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    ap.add_argument("--thread", default=None, help="流程编号（默认由配置身份决定）")
    ap.add_argument("--db-file", default=None, help="直接指定冻结库（默认取这个流程冻结的库）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="联网获取（显式命令）")
    f.add_argument("--only", nargs="*", default=None, help="只取名字以这些前缀开头的请求")
    f.add_argument("--skip", nargs="*", default=None, help="跳过名字以这些前缀开头的请求")
    f.add_argument("--mode", choices=["cache_first", "refresh"], default="cache_first")
    b = sub.add_parser("build", help="LangGraph 资料构建：开始，或接着上次停下的阶段跑（离线）")
    b.add_argument("--stop-after", choices=STAGES, default=None)
    sub.add_parser("status", help="流程状态")
    q = sub.add_parser("query", help="分页查询注册表")
    q.add_argument("--sector")
    q.add_argument("--cbsa")
    q.add_argument("--kind")
    q.add_argument("--text")
    q.add_argument("--attr", nargs="*", help="属性过滤，键=值")
    q.add_argument("--page", type=int, default=1)
    q.add_argument("--per-page", type=int, default=20)
    m = sub.add_parser("menu", help="查看一份菜单")
    m.add_argument("--cbsa", required=True)
    m.add_argument("--year", type=int, required=True)
    m.add_argument("--sector", required=True)
    m.add_argument("--env-repeat", type=int, default=0)
    m.add_argument("--page", type=int, default=1)
    m.add_argument("--per-page", type=int, default=20)
    lg = sub.add_parser("lineage", help="一项机会的来源链")
    lg.add_argument("opportunity_id")
    e = sub.add_parser("export", help="把冻结库的清单与报告写成可提交的小文件")
    e.add_argument("--out", default=str(DEFAULT_EXPORT))
    args = ap.parse_args(argv)
    handlers = {
        "fetch": cmd_fetch,
        "build": cmd_build,
        "status": cmd_status,
        "query": cmd_query,
        "menu": cmd_menu,
        "lineage": cmd_lineage,
        "export": cmd_export,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
