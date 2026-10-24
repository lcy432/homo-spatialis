"""E3B 菜单诊断（E3B 收尾第 5 节）的命令行入口：离线、零模型调用、不写资料包。

    uv run python scripts/life_pack_menu_diagnostics.py --out <目录>                 # 冻结资料包 + 完整池基准（约 35 秒组装 + 多进程 E1 检查）
    uv run python scripts/life_pack_menu_diagnostics.py --pack <资料包目录> --out <目录> --no-full-pool

只报告首月 / 空间诊断，不是长期成功率；不用来筛选机会或调整菜单大小。输出 ``menu_diagnostics.json`` 与 ``menu_diagnostics.md``。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import _bootstrap  # noqa: F401


def main() -> int:
    from product.realdata.menu_diagnostics import diagnose, write

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pack", default="data/frozen/life/us-v1/pack")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--no-full-pool",
        action="store_true",
        help="不重新组装完整合格池（不给完整池基准与 30 / 60 / 120 的质量补充）",
    )
    ap.add_argument(
        "--workers", type=int, default=None, help="E1 检查的进程数（默认 min(16, CPU)）"
    )
    args = ap.parse_args()
    t0 = time.perf_counter()
    d = diagnose(Path(args.pack), full_pool=not args.no_full_pool, workers=args.workers)
    write(d, Path(args.out))
    print(f"menu diagnostics written in {time.perf_counter() - t0:.1f}s")
    for key, c in d["cities"].items():
        ov = c["housing_first_month_overview"]
        print(
            key,
            {cash: (v["min"], v["median"], v["max"]) for cash, v in ov.items()},
            c["full_pool"].get("housing_weight_share_either", c["full_pool"].get("status")),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
