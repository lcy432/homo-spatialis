"""地理显示清单（``configs/product/display/geography/<pack_id>.json``，见 ``product.display.geography``）。

用法（仓库根目录）::

    uv run python scripts/product_geography.py build
        按合成与 E3B 两个资料包生成清单。真实城市只读本机缓存 ``data/raw/intake`` 里的 Census Gazetteer 与 2023 年 7 月 CBSA
        划定文件，并要求就是核对过的那两份；不联网、不调用模型。生成之后由显示资源的 ``freeze`` 冻结进新的资源版本。
    uv run python scripts/product_geography.py check
        重新生成并与仓库里的文件逐字比较，不同就退出码 1（同样要本机缓存）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PACKS = ("configs/product/e3a_synthetic_pack.yaml", "configs/product/e3b_us_v1_pack.yaml")
OUT = REPO / "configs/product/display/geography"
CACHE = REPO / "data/raw/intake"


def rendered() -> list[tuple[Path, dict[str, Any], str]]:
    from product.display.geography import build_bundle, dump, load_life_input, read_sources
    from product.pack import load_pack

    out = []
    sources = None
    for rel in PACKS:
        pack = load_pack(REPO / rel)
        points = count = None
        if pack.body.status == "real_data_frozen":
            sources = sources or read_sources(CACHE)
            points, count = sources
        bundle = build_bundle(pack, load_life_input(pack, REPO), points, count)
        out.append((OUT / f"{pack.body.pack_id}.json", bundle, dump(bundle)))
    return out


def cmd_build(_: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for path, bundle, text in rendered():
        path.write_text(text, encoding="utf-8", newline="\n")
        anchors = sum(1 for c in bundle["cities"] if c["anchor"] is not None)
        print(
            f"{path.relative_to(REPO).as_posix()}：{len(bundle['cities'])} 城，{anchors} 个锚点，{bundle['status']}"
        )
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    stale = [
        path
        for path, _bundle, text in rendered()
        if not path.is_file() or path.read_bytes() != text.encode("utf-8")
    ]
    for path in stale:
        print("✗ 与生成结果不同：", path.relative_to(REPO).as_posix())
    print(f"地理显示清单核对：{len(stale)} 个文件需要重新生成")
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    from product.display.catalog import DisplayResourceError

    try:
        return {"build": cmd_build, "check": cmd_check}[args.cmd](args)
    except DisplayResourceError as exc:
        print("✗", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
