"""E3B 资料包的有限敏感性检查（DECISIONS/0042）：改一项登记的假设，重跑同一条六城 mock 产品链，比较各城的 C/E/M 与平均 J。

只改冻结资料包里的评分条件（与对应的城市事实），评分规格不变；生活由同一个 mock 规划器重新过（它的选择本身不读评分参数，
满足感只经由精力反馈影响下一年的额度）。跑完一律用 git 恢复冻结文件。零网络、零费用。

    uv run python scripts/life_pack_sensitivity.py --out runs/<日期>-e3b-sensitivity

变体：
* ``env_flat``：环境背景改成六城同一个值（固定尺度的 5 × 平均好天比例）——去掉唯一由资料驱动的城市背景差异；
* ``quality_off``：居住条件的条件档次效率 η 从 0.5 改成 0（只看空间等级）；
* ``quality_full``：η 改成 1.0（档次与空间同权）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from statistics import mean

import _bootstrap  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "data/frozen/life/us-v1/pack"


def _load(name: str) -> dict:
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def _save(name: str, obj: dict) -> None:
    (PACK / name).write_text(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def env_flat() -> None:
    scoring, facts = _load("scoring.json"), _load("city_facts.json")
    envs = [
        Decimal(str(c["base"]))
        for cc in scoring["city_conditions"]
        for c in cc["components"]
        if c["component_id"] == "environment"
    ]
    flat = (sum(envs) / len(envs)).quantize(Decimal("0.0005"))
    for cc in scoring["city_conditions"]:
        for c in cc["components"]:
            if c["component_id"] == "environment":
                c["base"] = float(flat)
                c["area_bases"] = {a: float(flat) for a in c["area_bases"]}
    for sheet in facts["city_facts"]["sheets"]:
        for f in sheet["facts"]:
            if f["fact_id"] == "environment_index":
                f["number"] = float(flat * 2)
    _save("scoring.json", scoring)
    _save("city_facts.json", facts)


def quality(eta: float) -> None:
    scoring = _load("scoring.json")
    for cc in scoring["city_conditions"]:
        for c in cc["components"]:
            if c["component_id"] == "housing_condition":
                for e in c["efficiencies"]:
                    if e["source_id"] == "quality":
                        e["eta"] = eta
    _save("scoring.json", scoring)


VARIANTS = {
    "env_flat": env_flat,
    "quality_off": lambda: quality(0.0),
    "quality_full": lambda: quality(1.0),
}


def summarize(run: Path) -> dict:
    evals = json.loads(
        (run / "ws/archive/full/evaluations/arm.main.json").read_text(encoding="utf-8")
    )
    out: dict[str, dict] = {}
    for e in evals:
        city = e["city"]["code"]
        row = out.setdefault(city, {"complete": 0, "j": []})
        comb = e.get("combined") or {}
        if comb.get("status") == "complete":
            row["complete"] += 1
            row["j"].append(comb["j"]["value"])
    return {
        c: {"complete": r["complete"], "mean_j": round(mean(r["j"]), 4) if r["j"] else None}
        for c, r in sorted(out.items())
    }


def run_mock(out: Path) -> None:
    cmd = [
        sys.executable,
        str(REPO / "scripts/run_product_full.py"),
        "--out",
        str(out),
        "--mock",
        "--pack",
        "configs/product/e3b_us_v1_pack.yaml",
        "--abilities",
        "edu_at_least_hs",
    ]
    subprocess.run(cmd, cwd=REPO, check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--baseline", type=Path, default=None, help="已有的基线 mock 运行目录（不给就重跑）"
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results: dict[str, dict] = {}
    base = args.baseline or args.out / "baseline"
    if args.baseline is None:
        run_mock(base)
    results["baseline"] = summarize(base)
    for name, apply in VARIANTS.items():
        try:
            apply()
            run_mock(args.out / name)
            results[name] = summarize(args.out / name)
        finally:
            subprocess.run(["git", "checkout", "--", str(PACK)], cwd=REPO, check=True)
    (args.out / "sensitivity.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
