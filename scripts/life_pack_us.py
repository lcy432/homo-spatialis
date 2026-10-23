"""E3B（DECISIONS/0042）六城真实生活资料包的命令行入口。

只有 ``fetch-*`` 会联网（显式命令；通用 User-Agent，不带密钥、不注册、不付费；被拒的来源照实记录，不绕过）。其余命令都离线，
只读原始文件缓存与 intake-02 的冻结库。

    uv run python scripts/life_pack_us.py fetch-tax [--mode cache_first|refresh]   # 获取官方税规则网页
    uv run python scripts/life_pack_us.py check-tax                                # 离线核对摘句与数，列出每城的税或阻断原因
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
    b = sub.add_parser("build", help="离线组装六城资料包")
    b.add_argument("--out", default="data/frozen/life/us-v1/pack")
    b.add_argument(
        "--intake-db", default="runs/intake/us-v1/work/freeze/562c32339bbae67f/intake.sqlite"
    )
    args = ap.parse_args(argv)
    handlers = {"fetch-tax": cmd_fetch_tax, "check-tax": cmd_check_tax, "build": cmd_build}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
