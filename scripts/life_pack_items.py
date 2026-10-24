"""E3B（DECISIONS/0042）六城真实生活资料包的物品与职业一侧：完整食品方案、服务、设备、装备、活动、岗位与培训。

只有 ``fetch`` 会联网（显式命令；通用 User-Agent，不带密钥、不注册、不付费；被拒的来源照实记录，不绕过）。其余命令都离线：
``slice`` 只读 intake-02 的冻结库与原始文件缓存，``build`` 只读切片。

    uv run python scripts/life_pack_items.py fetch [--mode cache_first|refresh]      # USDA SR Legacy 与 BLS ATUS 表 1
    uv run python scripts/life_pack_items.py slice --db <intake.sqlite> --out runs/life_items/slice.json
    uv run python scripts/life_pack_items.py build --slice runs/life_items/slice.json   # → data/frozen/life/us-v1/items/
    uv run python scripts/life_pack_items.py start-job --cbsa 16980 --soc 43-9061      # 读冻结输出
    uv run python scripts/life_pack_items.py cut-fixture --slice runs/life_items/slice.json   # 测试用的小样本切片
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401
from product.realdata.fetch import DEFAULT_CACHE, fetch
from product.realdata.life_freeze import freeze
from product.realdata.life_inputs import ATUS_REQUEST, FDC_REQUEST, build_slice
from product.realdata.occupations import start_job

ITEMS_CONFIG = Path("configs/intake/life_items_us_v1.yaml")
OCC_CONFIG = Path("configs/intake/occupations_us_v1.yaml")
INTAKE_DB = Path("runs/intake/us-v1/work/freeze/35667f4101a71c0e/intake.sqlite")
SLICE = Path("runs/life_items/slice.json")
OUT = Path("data/frozen/life/us-v1/items")
FIXTURE = Path("tests/fixtures/life_items/slice_sample.json")


def _yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _baskets(items_cfg: dict[str, Any]) -> dict[str, Any]:
    return _yaml(Path(items_cfg["food"]["intake_config"]))["mapping"]["a_food"]["baskets"]


def _write(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    bad = 0
    for req in (FDC_REQUEST, ATUS_REQUEST):
        got = fetch(req, Path(args.cache), mode=args.mode)
        r = got.record
        print(f"{r.status:10} {r.bytes or '':>9} {req.source_id}  {r.url}  {r.note or ''}")
        bad += 0 if got.ok else 1
    return 1 if bad else 0


def cmd_slice(args: argparse.Namespace) -> int:
    sl = build_slice(
        Path(args.db), _yaml(args.items_config), _yaml(args.occ_config), cache=Path(args.cache)
    )
    _write(Path(args.out), sl)
    print(f"切片已写出：{args.out}（intake 构建 {sl['intake_build_id'][:16]}）")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    sl = json.loads(Path(args.slice).read_text(encoding="utf-8"))
    items_cfg = _yaml(args.items_config)
    m = freeze(sl, items_cfg, _yaml(args.occ_config), _baskets(items_cfg), Path(args.out))
    print(f"冻结输出：{args.out}（内容身份 {m['content_identity'][:16]}）")
    for cbsa, c in m["cities"].items():
        s = c["counts"]["by_status"]
        print(
            f"  {cbsa}: 可执行 {s['executable']}、待补就业片区 {s['needs_area']}、需要 E1 扩展 {s['needs_e1_extension']}、"
            f"价格未知 {s['data_unknown']}；未生成 {c['counts']['not_generated']}"
        )
    return 0


def cmd_start_job(args: argparse.Namespace) -> int:
    city = json.loads((Path(args.items_dir) / f"{args.cbsa}.json").read_text(encoding="utf-8"))
    r = start_job(city["start_jobs"], _yaml(args.occ_config), args.soc)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["status"] == "ok" else 2


def cut_fixture(sl: dict[str, Any], items_cfg: dict[str, Any]) -> dict[str, Any]:
    """测试用的小样本：两座城（纽约、芝加哥）+ 旧金山（没有公共泳池的情形）；餐馆标签只留配置里点名的菜系（其余合成一行，
    总家数不变）；场所只留规则用得到的组；芝加哥活动与培训项目各挑几条覆盖每种判定；设备每（类别 × 类型）留 5 个型号。"""
    keep = ("35620", "16980", "41860")
    tags = {t for v in items_cfg["food"]["dining"]["osm_cuisine_tags"].values() for t in v}
    rules = items_cfg["activities"]["venue_rules"].values()
    types = {t for r in rules for t in r.get("venue_types", ())}
    sports = {s for r in rules for s in r.get("sports", ())}
    out = {k: v for k, v in sl.items() if k not in ("metros", "appliances")}
    out["metros"] = {}
    for cbsa in keep:
        m = json.loads(json.dumps(sl["metros"][cbsa]))
        sets, other = [], 0
        for s in m["restaurants"]["tag_sets"]:
            if set(s["tags"]) & tags:
                sets.append(s)
            else:
                other += s["count"]
        m["restaurants"]["tag_sets"] = [*sets, {"tags": ["_other"], "count": other}]
        groups, other = [], 0
        for g in m["venues"]["groups"]:
            if g["venue_type"] in types or set(g["sports"]) & sports:
                groups.append(g)
            else:
                other += g["count"]
        m["venues"]["groups"] = [
            *groups,
            {"venue_type": "_other", "named": False, "access": None, "sports": [], "count": other},
        ]
        m["programs"] = _pick_programs(m["programs"], items_cfg)
        m["training"] = _pick_training(m["training"])
        out["metros"][cbsa] = m
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for a in sl["appliances"]:
        by.setdefault((a["category"], a["type"]), []).append(a)
    out["appliances"] = [a for k in sorted(by, key=str) for a in by[k][:5]]
    out["fixture_note"] = (
        "从真实切片切出的小样本（scripts/life_pack_items.py cut-fixture）：只供离线测试，分布不代表现实；"
        f"来源切片的 intake 构建 {sl['intake_build_id'][:16]}"
    )
    return out


def _pick_programs(
    programs: list[dict[str, Any]], items_cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    cats = {c for v in items_cfg["activities"]["chicago"]["categories"].values() for c in v}
    want = [
        lambda p: (
            p["age_range"] == "Adult"
            and p["category"] == "Sports - Tennis"
            and p["fee"] in ("0", "0.00")
        ),
        lambda p: (
            p["age_range"] == "Adult"
            and p["category"] == "Yoga"
            and p["fee"] not in (None, "0", "0.00")
        ),
        lambda p: p["age_range"] == "All Ages" and p["category"] in cats,
        lambda p: p["age_range"] == "Senior" and p["category"] in cats,
        lambda p: p["age_range"] == "Youth",
        lambda p: p["age_range"] == "Adult" and p["category"] == "Sports - Basketball",
        lambda p: p["activity_type"] == "Camp",
        lambda p: (
            p["age_range"] == "Adult" and p["category"] in cats and p["session_hours"] is None
        ),
        lambda p: (
            p["age_range"] == "Adult"
            and p["category"] in cats
            and "Except" in (p["date_notes"] or "")
        ),
        lambda p: p["age_range"] == "Adult" and p["category"] == "Music",
    ]
    out: list[dict[str, Any]] = []
    for test in want:
        hit = next((p for p in programs if test(p) and p not in out), None)
        if hit is not None:
            out.append(hit)
    return out


def _pick_training(training: list[dict[str, Any]]) -> list[dict[str, Any]]:
    award = {"49-3023", "49-2022", "27-4011"}
    want = [
        lambda p: set(p["related_socs"]) & award and p["tuition"] and p["months"] in ("12", "24"),
        lambda p: (
            set(p["related_socs"]) & award and p["tuition"] and p["months"] not in ("12", "24")
        ),
        lambda p: not set(p["related_socs"]) & award,
        lambda p: set(p["related_socs"]) & award and not p["tuition"],
    ]
    out: list[dict[str, Any]] = []
    for test in want:
        hit = next((p for p in training if test(p) and p not in out), None)
        if hit is not None:
            out.append(hit)
    return out


def cmd_cut_fixture(args: argparse.Namespace) -> int:
    sl = json.loads(Path(args.slice).read_text(encoding="utf-8"))
    _write(Path(args.out), cut_fixture(sl, _yaml(args.items_config)))
    print(f"测试样本已写出：{args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--items-config", default=str(ITEMS_CONFIG))
    p.add_argument("--occ-config", default=str(OCC_CONFIG))
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--cache", default=str(DEFAULT_CACHE))
    f.add_argument("--mode", choices=("cache_first", "refresh"), default="cache_first")
    f.set_defaults(func=cmd_fetch)
    s = sub.add_parser("slice")
    s.add_argument("--db", default=str(INTAKE_DB))
    s.add_argument("--cache", default=str(DEFAULT_CACHE))
    s.add_argument("--out", default=str(SLICE))
    s.set_defaults(func=cmd_slice)
    b = sub.add_parser("build")
    b.add_argument("--slice", default=str(SLICE))
    b.add_argument("--out", default=str(OUT))
    b.set_defaults(func=cmd_build)
    j = sub.add_parser("start-job")
    j.add_argument("--cbsa", required=True)
    j.add_argument("--soc", required=True)
    j.add_argument("--items-dir", default=str(OUT))
    j.set_defaults(func=cmd_start_job)
    c = sub.add_parser("cut-fixture")
    c.add_argument("--slice", default=str(SLICE))
    c.add_argument("--out", default=str(FIXTURE))
    c.set_defaults(func=cmd_cut_fixture)
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
