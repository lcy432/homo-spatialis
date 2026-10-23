"""E3B（DECISIONS/0042）六城真实生活资料包的命令行入口。

只有 ``fetch-*`` 会联网（显式命令；通用 User-Agent，不带密钥、不注册、不付费；被拒的来源照实记录，不绕过）。其余命令都离线，
只读原始文件缓存与 intake-02 的冻结库。

    uv run python scripts/life_pack_us.py fetch-tax [--mode cache_first|refresh]   # 获取官方税规则网页
    uv run python scripts/life_pack_us.py check-tax                                # 离线核对摘句与数，列出每城的税或阻断原因
    uv run python scripts/life_pack_us.py fetch-safety                             # 获取 FBI 2024 估计表、纽约州 DCJS 县合计与 PEP 县人口
    uv run python scripts/life_pack_us.py check-safety                             # 离线列出每城的犯罪资料、覆盖与安全背景
    uv run python scripts/life_pack_us.py build [--out data/frozen/life/us-v1/pack]  # 离线组装六城资料包（生活输入、评分、评价、城市事实）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

import _bootstrap  # noqa: F401
from product.realdata.fetch import DEFAULT_CACHE
from product.realdata.tax_rules import TAX_CONFIG, compile_tax


def cmd_fetch_tax(args: argparse.Namespace) -> int:
    cfg = yaml.safe_load(Path(args.tax_config).read_text(encoding="utf-8"))
    b = compile_tax(cfg, Path(args.cache), mode=args.mode)
    for sid, rec in sorted(b.sources.items()):
        print(f"{rec['status']:10} {rec.get('bytes') or '':>8} {sid}  {rec['url']}")
    return _report(b)


def cmd_check_tax(args: argparse.Namespace) -> int:
    cfg = yaml.safe_load(Path(args.tax_config).read_text(encoding="utf-8"))
    return _report(compile_tax(cfg, Path(args.cache)))


def cmd_fetch_safety(args: argparse.Namespace) -> int:
    from product.realdata.safety import fetch_safety_sources

    status = fetch_safety_sources(Path(args.cache))
    for sid, st in sorted(status.items()):
        print(f"{st:10} {sid}")
    return 0 if all(st == "ok" for st in status.values()) else 1


def cmd_check_safety(args: argparse.Namespace) -> int:
    from product.realdata.life_assembly import load_pack_config, metro_safety_inputs, safety_pair

    cfg = load_pack_config()
    inputs, national = metro_safety_inputs(cfg, Path(args.cache))
    print(
        f"全国 2024：人口 {national.population:,}，暴力 {national.violent_rate:.2f}，"
        f"财产 {national.property_rate:.2f}（每 10 万人）"
    )
    missing = 0
    for c in cfg["cities"]:
        s = inputs[c["cbsa"]]
        pair = safety_pair(cfg, s, national)
        if pair is None or s.count is None:
            missing += 1
            print(f"{c['key']:4} 未知：{s.missing}")
            continue
        index, b, r = pair
        print(
            f"{c['key']:4} 暴力 {s.count.violent_rate:8.2f} 财产 {s.count.property_rate:8.2f} "
            f"r {r:.4f} 指数 {index} b {b}  {s.scope}；{s.coverage}；{s.residence}"
        )
    return 0 if not missing else 1


def cmd_build(args: argparse.Namespace) -> int:
    from product.realdata.life_assembly import build_all

    rep = build_all(Path(args.out), intake_db=Path(args.intake_db), cache=Path(args.cache))
    for key, c in rep["cities"].items():
        print(
            key,
            json.dumps(c["counts"], ensure_ascii=False),
            json.dumps(c["excluded"], ensure_ascii=False),
        )
    print(
        "build", rep["build_identity"][:16], "files", json.dumps(rep["files"], ensure_ascii=False)
    )
    return 0 if not rep["tax_blocked"] else 1


def _report(b) -> int:
    for pid, p in sorted(b.policies.items()):
        print(
            f"通过   {pid}: {len(p.rules)} 条规则，{sum(len(r.citations) for r in p.rules)} 条摘句"
        )
    for pid, why in sorted(b.blocked.items()):
        print(f"阻断   {pid}:")
        for w in why:
            print(f"         {w}")
    print(
        json.dumps({"passed": sorted(b.policies), "blocked": sorted(b.blocked)}, ensure_ascii=False)
    )
    return 0 if not b.blocked else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--tax-config", default=str(TAX_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-tax", help="联网获取官方税规则网页（显式命令）")
    f.add_argument("--mode", choices=["cache_first", "refresh"], default="cache_first")
    sub.add_parser("check-tax", help="离线核对官方税规则")
    sub.add_parser("fetch-safety", help="联网获取犯罪统计与县人口（显式命令）")
    sub.add_parser("check-safety", help="离线列出每城的安全资料与背景值")
    b = sub.add_parser("build", help="离线组装六城资料包")
    b.add_argument("--out", default="data/frozen/life/us-v1/pack")
    b.add_argument(
        "--intake-db", default="runs/intake/us-v1/work/freeze/562c32339bbae67f/intake.sqlite"
    )
    args = ap.parse_args(argv)
    handlers = {
        "fetch-tax": cmd_fetch_tax,
        "check-tax": cmd_check_tax,
        "fetch-safety": cmd_fetch_safety,
        "check-safety": cmd_check_safety,
        "build": cmd_build,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
