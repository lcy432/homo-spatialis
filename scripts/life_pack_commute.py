"""E3B（DECISIONS/0042 决定 4）六城通勤资料的命令行入口。

只有 ``fetch-costs`` 会联网（显式命令；通用 User-Agent，不带密钥、不注册、不付费；被拒的来源照实记录，不绕过）。其余命令都
离线，只读原始文件缓存与 intake-02 的冻结库。

    uv run python scripts/life_pack_commute.py fetch-costs [--mode cache_first|refresh]   # 票价、油耗、用车费用网页与 PUMS 代码表
    uv run python scripts/life_pack_commute.py check-costs                                # 离线核对摘句与数
    uv run python scripts/life_pack_commute.py build [--intake-db 路径] [--out 目录]        # 离线编译并写出每城的冻结 JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from product.realdata.commute import (
    COMMUTE_CONFIG,
    FROZEN_DIR,
    CostBuild,
    codelist_request,
    compile_all,
    compile_costs,
    frozen_payload_problems,
    load_commute_config,
    load_raw,
    read_intake,
    write_frozen,
)
from product.realdata.fetch import DEFAULT_CACHE, fetch


def cmd_fetch_costs(args: argparse.Namespace) -> int:
    cfg = load_commute_config(args.config)
    cl = fetch(codelist_request(cfg), Path(args.cache), args.mode)
    print(f"{cl.record.status:10} {cl.record.bytes or '':>8} codelist  {cl.record.url}")
    if cl.ok and cl.record.raw_sha256 != cfg["codelist"]["sha256"]:
        print(f"           代码表的 SHA-256 {cl.record.raw_sha256} 不是配置里钉住的那一份")
    b = compile_costs(cfg, Path(args.cache), mode=args.mode)
    for sid, rec in sorted(b.sources.items()):
        print(f"{rec['status']:10} {rec.get('bytes') or '':>8} {sid}  {rec['url']}")
    return _report(b)


def cmd_check_costs(args: argparse.Namespace) -> int:
    return _report(compile_costs(load_commute_config(args.config), Path(args.cache)))


def _report(b: CostBuild) -> int:
    for key, f in sorted(b.transit.items()):
        cap = f"，月票 {f.monthly_pass.value}" if f.monthly_pass else "，没有月票"
        print(f"通过   {key} 公交：{f.agency} 单程 {f.fare.value}{cap}")
    for key, why in sorted(b.transit_blocked.items()):
        print(f"阻断   {key} 公交：")
        for w in why:
            print(f"         {w}")
    if b.mpg and b.car_annual:
        print(f"通过   开车：{b.mpg.value} mpg，用车权年费用 {b.car_annual.value}")
    for w in b.drive_blocked:
        print(f"阻断   开车：{w}")
    print(
        json.dumps(
            {
                "transit": sorted(b.transit),
                "transit_blocked": sorted(b.transit_blocked),
                "drive_blocked": len(b.drive_blocked),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not b.drive_blocked else 1


def cmd_build(args: argparse.Namespace) -> int:
    cfg = load_commute_config(args.config)
    cache = Path(args.cache)
    intake = read_intake(Path(args.intake_db or cfg["intake"]["default_db"]))
    raw = load_raw(cfg, intake, cache)
    costs = compile_costs(cfg, cache)
    results = compile_all(cfg, raw, intake, costs)
    bad = {r.key: frozen_payload_problems(r.payload) for r in results}
    if any(bad.values()):
        print(json.dumps(bad, ensure_ascii=False, indent=1))
        return 1
    index = write_frozen(results, Path(args.out))
    for key, c in index["cities"].items():
        print(
            f"{key:4} {c['status']:8} 方式 {','.join(c['modes']):24} 居住 {c['home_areas']:3} 就业 {c['work_areas']} "
            f"往返格 {c['legs']:5} 阻断 {c['blocked'] or '无'}"
        )
    print(f"写出 {args.out}（索引内容身份 {index['content_identity'][:16]}…）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--config", default=str(COMMUTE_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser(
        "fetch-costs", help="联网获取票价、油耗、用车费用网页与 PUMS 代码表（显式命令）"
    )
    f.add_argument("--mode", choices=["cache_first", "refresh"], default="cache_first")
    sub.add_parser("check-costs", help="离线核对票价、油耗与用车费用")
    b = sub.add_parser("build", help="离线编译六城通勤资料并写出冻结 JSON")
    b.add_argument("--intake-db", default=None, help="intake-02 冻结库（默认按配置）")
    b.add_argument("--out", default=str(FROZEN_DIR))
    args = ap.parse_args(argv)
    return {"fetch-costs": cmd_fetch_costs, "check-costs": cmd_check_costs, "build": cmd_build}[
        args.cmd
    ](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
