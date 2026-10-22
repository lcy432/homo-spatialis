"""报告：覆盖矩阵、来源统计、映射诊断（与来源分布对照）、菜单诊断。

每一项都只报告，不设通过线；数字是测出来的，偏差与口径写在旁边。“测试通过”“映射合理”“现实有效”是三件事：这里的对照只能说明
映射复现了来源的分布、与独立发布的数字一致到什么程度，不能说明城市评价已经标定。
"""

from __future__ import annotations

import gzip
import json
import statistics
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.intake_contracts import Menu, MenuPolicy, Opportunity, StdRecord
from product.realdata import mapping_b, menus

REPORT_VERSION = "intake-reports-v1"


def _jsonl(path: Path):
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _wq(values: list[float], weights: list[float], q: float) -> float | None:
    if not values:
        return None
    return mapping_b.wquantile(np.array(values, float), np.array(weights, float), q)


def build_reports(config, std_dir: Path, map_dir: Path, catalog_dir: Path) -> dict[str, Any]:
    opps = [Opportunity.model_validate(d) for d in _jsonl(map_dir / "opportunities.jsonl.gz")]
    tables = {t["table_id"]: t for t in _jsonl(map_dir / "tables.jsonl.gz")}
    menu_docs = [Menu.model_validate(d) for d in _jsonl(catalog_dir / "menus.jsonl.gz")]
    return {
        "coverage": coverage(config, std_dir, opps),
        "sources": source_stats(config, std_dir),
        "mapping_housing": housing_diagnostics(config, std_dir, opps, tables),
        "mapping_jobs": jobs_diagnostics(config, opps, tables),
        "mapping_food": food_diagnostics(config, opps),
        "activities_training": activity_training_diagnostics(config, opps),
        "menus": menu_diagnostics(config, opps, menu_docs, tables),
        "capabilities": capability_summary(opps),
    }


# --------------------------------------------------------------------------- 覆盖矩阵


def coverage(config, std_dir: Path, opps: list[Opportunity]) -> dict[str, Any]:
    rec_counts: dict[tuple[str, str], int] = Counter()
    rec_national: Counter[str] = Counter()
    for p in sorted(std_dir.glob("records_*.jsonl.gz")):
        for d in _jsonl(p):
            cbsa = d["geo"].get("cbsa")
            if cbsa:
                rec_counts[(cbsa, d["sector"])] += 1
            else:
                rec_national[d["sector"]] += 1
    cells: dict[str, dict[str, Any]] = {}
    for m in config.metros:
        row: dict[str, Any] = {}
        for sector in ("housing", "jobs", "training", "food", "goods", "activities", "background"):
            mine = [o for o in opps if o.sector == sector and (o.cbsa == m.cbsa or o.cbsa is None)]
            row[sector] = {
                "records_in_metro": rec_counts.get((m.cbsa, sector), 0),
                "records_national_applicable": rec_national.get(sector, 0),
                "opportunities": len(mine),
                "by_origin": dict(Counter(o.origin for o in mine)),
                "data_complete": sum(o.data_status == "complete" for o in mine),
                "data_partial": sum(o.data_status == "partial" for o in mine),
                "data_insufficient": sum(o.data_status == "insufficient" for o in mine),
                "estimated_or_assumed": sum(
                    o.weakest_kind in ("estimated", "assumption") for o in mine
                ),
                "compilable": sum(o.compile.status == "compilable" for o in mine),
                "needs_e1_extension": sum(o.compile.status == "needs_e1_extension" for o in mine),
                "not_compilable": sum(o.compile.status == "not_compilable" for o in mine),
                "planning_reference": sum(o.capabilities.planning_reference for o in mine),
                "executable": sum(o.capabilities.executable for o in mine),
                "scorable": sum(o.capabilities.scorable for o in mine),
            }
        cells[m.cbsa] = {"label": m.label, "label_zh": m.label_zh, "sectors": row}
    return {
        "note_zh": (
            "records_in_metro = 这座都会区里实际取得的真实记录；records_national_applicable = 全国性的记录（例如全国认证的家电型号、全国均价），"
            "适用于每座城但不是本地观测；opportunities 里 mapping_a / mapping_b 是映射生成的候选，不是真实样本量。"
            "estimated_or_assumed = 机会里最弱的来源是估计或假设；needs_e1_extension / not_compilable = 当前引擎不能执行。"
        ),
        "metros": cells,
    }


def source_stats(config, std_dir: Path) -> dict[str, Any]:
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for p in sorted(std_dir.glob("records_*.jsonl.gz")):
        for d in _jsonl(p):
            by_source[d["source"]["source_id"]][d["record_type"]] += 1
    specs = {s.source_id: s for s in config.sources}
    return {
        sid: {
            "publisher": specs[sid].publisher if sid in specs else None,
            "access": specs[sid].access if sid in specs else None,
            "records": dict(c),
            "total": sum(c.values()),
        }
        for sid, c in sorted(by_source.items())
    } | {
        sid: {
            "publisher": s.publisher,
            "access": s.access,
            "records": {},
            "total": 0,
            "note": s.note,
        }
        for sid, s in specs.items()
        if sid not in by_source
    }


# --------------------------------------------------------------------------- 住房


def housing_diagnostics(
    config, std_dir: Path, opps: list[Opportunity], tables: dict[str, dict]
) -> dict[str, Any]:
    bg = {
        d["record_id"]: StdRecord.model_validate(d)
        for d in _jsonl(std_dir / "records_background.jsonl.gz")
    }
    out: dict[str, Any] = {}
    for m in config.metros:
        path = std_dir / f"housing_{m.cbsa}.parquet"
        if not path.exists():
            continue
        hdf = pd.read_parquet(path)
        stock = mapping_b.stock_median_gross_by_bed(hdf)
        pub = bg.get(f"background.acs_rent.{m.cbsa}")
        published = {}
        if pub is not None:
            for key, lab in (
                ("all", "all"),
                ("0", "0br"),
                ("1", "1br"),
                ("2", "2br"),
                ("3", "3br"),
                ("4", "4br"),
                ("5", "5plus_br"),
            ):
                f = pub.field(f"median_gross_rent_{lab}")
                published[key] = None if f is None or f.value is None else float(f.value)
        validation = [
            {
                "bedrooms": k,
                "pums_stock_median_gross": stock.get(k),
                "published_b25031": published.get(k),
                "difference": None
                if stock.get(k) is None or published.get(k) is None
                else round(stock[k] - published[k], 2),
            }
            for k in ("all", "0", "1", "2", "3", "4", "5")
        ]
        market_vs_stock = []
        for bed in ("0", "1", "2", "3plus"):
            mk = tables.get(f"b.housing.{m.cbsa}.metro_bed.{bed}")
            st = tables.get(f"b.housing.{m.cbsa}.metro_bed_stock.{bed}")
            market_vs_stock.append(
                {
                    "bedrooms": bed,
                    "market_proxy_median_contract": mk["quantiles"].get("0.50") if mk else None,
                    "market_n": mk["n"] if mk else 0,
                    "market_se_median": mk["se_median"] if mk else None,
                    "stock_median_contract": st["quantiles"].get("0.50") if st else None,
                    "stock_n": st["n"] if st else 0,
                }
            )
        market = hdf[hdf["full_puma"] & hdf["segment"].isin(mapping_b.MARKET)]
        b = [o for o in opps if o.cbsa == m.cbsa and o.kind == "rental_listing_candidate"]
        comparison = []
        for bed in ("0", "1", "2", "3plus"):
            src = market[market["bed_class"] == bed]
            cand = [
                o
                for o in b
                if o.stratum == bed
                and o.fact("monthly_rent")
                and o.fact("monthly_rent").status == "present"
            ]
            cv = [float(o.fact("monthly_rent").value) for o in cand]  # type: ignore[union-attr]
            cw = [float(o.target_weight or 0) for o in cand]
            row = {"bedrooms": bed, "source_n": len(src), "candidates": len(cand)}
            for q in (0.25, 0.5, 0.75):
                row[f"source_q{q}"] = _wq(src["rent_contract"].tolist(), src["WGTP"].tolist(), q)
                row[f"candidates_q{q}"] = _wq(cv, cw, q)
            comparison.append(row)
        levels = Counter(str(o.attributes.get("price_level_used")) for o in b)
        a = [o for o in opps if o.cbsa == m.cbsa and o.kind == "coverage_grid_candidate"]
        out[m.cbsa] = {
            "validation_vs_published": validation,
            "market_vs_stock": market_vs_stock,
            "candidates_vs_source_distribution": comparison,
            "b_price_level_used": dict(levels),
            "a_grid": {
                "generated": len(a),
                "zero_weight": sum(
                    1 for o in a if o.target_weight is not None and float(o.target_weight) == 0
                ),
                "levels": dict(Counter(str(o.attributes.get("price_level_used")) for o in a)),
            },
            "partial_pumas_excluded": int((~hdf["full_puma"]).sum()),
        }
    return {
        "note_zh": (
            "validation_vs_published：用同一套筛选与权重从 PUMS 算出的存量毛租金中位数，对照 ACS 独立发布的 B25031（发布表在分箱内插值，"
            "差几美元正常）；它检验的是权重与筛选，不检验新租市场代理。market_vs_stock：新租市场代理比存量高多少——这就是不能拿存量当报价的原因。"
            "candidates_vs_source_distribution：按目标权重加权的候选租金分布对照来源样本分布；回退层级越粗，差异越大。"
        ),
        "metros": out,
    }


# --------------------------------------------------------------------------- 岗位、食品、活动与培训


def jobs_diagnostics(config, opps: list[Opportunity], tables: dict[str, dict]) -> dict[str, Any]:
    out = {}
    for m in config.metros:
        js = [o for o in opps if o.cbsa == m.cbsa and o.sector == "jobs"]
        med = [o for o in js if o.attributes.get("wage_position") == "median"]
        wage = med and [o.fact("annual_wage") for o in med]
        out[m.cbsa] = {
            "occupations": len(med),
            "wage_present": sum(1 for f in (wage or []) if f and f.status == "present"),
            "wage_suppressed_topcoded": sum(
                1
                for f in (wage or [])
                if f and f.status == "suppressed" and "顶码" in (f.reason or "")
            ),
            "wage_not_published": sum(
                1
                for f in (wage or [])
                if f and f.status == "suppressed" and "顶码" not in (f.reason or "")
            ),
            "employment_suppressed": sum(1 for o in med if o.target_weight is None),
            "hours_level": dict(
                Counter(
                    t["level"]
                    for t in tables.values()
                    if t["table_id"].startswith(f"b.jobs.{m.cbsa}.")
                )
            ),
        }
    return {
        "note_zh": "工资是 OEWS 的观测分位数（换到 2024 年平均价格是估计）；工时与在家工作比例按 SOC 大类，样本不足回退到六城合并。",
        "metros": out,
    }


def food_diagnostics(config, opps: list[Opportunity]) -> dict[str, Any]:
    out = {}
    for m in config.metros:
        items = [o for o in opps if o.cbsa == m.cbsa and o.kind == "food_item"]
        ratios = []
        for o in items:
            est, reg = o.fact("price"), o.fact("regional_observed_price")
            if est and reg and est.status == "present" and reg.status == "present":
                ratios.append(float(est.value) / float(reg.value))  # type: ignore[arg-type]
        plans = {
            o.opportunity_id.rsplit(".", 1)[1]: (
                o.fact("monthly_food_cost").value if o.fact("monthly_food_cost") else None
            )
            for o in opps
            if o.cbsa == m.cbsa and o.kind == "food_plan"
        }
        venues = [o for o in opps if o.cbsa == m.cbsa and o.kind == "restaurant_venue"]
        out[m.cbsa] = {
            "items": len(items),
            "estimate_over_regional_observed": {
                "n": len(ratios),
                "median": round(statistics.median(ratios), 4) if ratios else None,
                "min": round(min(ratios), 4) if ratios else None,
                "max": round(max(ratios), 4) if ratios else None,
            },
            "plan_monthly_cost": plans,
            "restaurants": len(venues),
            "channel_known": {
                k: sum(1 for o in venues if o.attributes.get(k) not in (None, "unknown"))
                for k in ("takeaway", "delivery", "outdoor_seating")
            },
            "cuisine_known": sum(1 for o in venues if o.attributes.get("cuisine")),
        }
    return {
        "note_zh": (
            "都会区食材价格是估计（全国均价 × 本都会区商品类 RPP ÷ 全国）；estimate_over_regional_observed 是估计价与本大区观测均价之比，"
            "接近 1 说明空间换算与大区观测大体一致，偏离说明商品类 RPP 与食品价格差异不同步。方案费用里的数量是登记的假设。"
            "渠道（自取、配送）只有 OSM 写了才算知道；没写的是未知，不是不提供。"
        ),
        "metros": out,
    }


def activity_training_diagnostics(config, opps: list[Opportunity]) -> dict[str, Any]:
    out = {}
    for m in config.metros:
        progs = [o for o in opps if o.cbsa == m.cbsa and o.kind == "activity_program"]
        fees = [
            float(o.fact("program_fee").value)
            for o in progs
            if o.fact("program_fee") and o.fact("program_fee").status == "present"
        ]  # type: ignore[union-attr,arg-type]
        venues = [o for o in opps if o.cbsa == m.cbsa and o.kind == "activity_venue"]
        train = [o for o in opps if o.cbsa == m.cbsa and o.kind == "training_program"]
        tuition = [
            float(o.fact("tuition_fees").value)
            for o in train
            if o.fact("tuition_fees") and o.fact("tuition_fees").status == "present"
        ]  # type: ignore[union-attr,arg-type]
        out[m.cbsa] = {
            "programs": len(progs),
            "programs_free": sum(1 for f in fees if f == 0),
            "programs_fee_median": statistics.median(fees) if fees else None,
            "programs_single_session_compilable": sum(
                1 for o in progs if o.compile.status == "compilable"
            ),
            "venues": len(venues),
            "venues_with_opening_hours": sum(
                1 for o in venues if o.calendar is not None and o.calendar.known
            ),
            "venue_types": dict(
                Counter(str(o.attributes.get("venue_type")) for o in venues).most_common(8)
            ),
            "training_programs": len(train),
            "training_tuition_median": statistics.median(tuition) if tuition else None,
            "training_related_occupations_mapped": sum(
                1
                for o in train
                if o.fact("related_occupations")
                and o.fact("related_occupations").status == "present"
            ),
        }
    return {
        "note_zh": "只有芝加哥有带价格与日历的城市开放资料；其他城市的活动只有场所（价格未知）。这是来源覆盖的差异，不是城市之间的真实差异。",
        "metros": out,
    }


def capability_summary(opps: list[Opportunity]) -> dict[str, Any]:
    out: dict[str, dict[str, int]] = {}
    for sector in sorted({o.sector for o in opps}):
        mine = [o for o in opps if o.sector == sector]
        out[sector] = {
            "total": len(mine),
            "collected": sum(o.capabilities.collected for o in mine),
            "planning_reference": sum(o.capabilities.planning_reference for o in mine),
            "executable": sum(o.capabilities.executable for o in mine),
            "scorable": sum(o.capabilities.scorable for o in mine),
        }
    return {
        "note_zh": "已采集 = 机会本身是真实记录；能供规划参考 = 必需事实齐全；可执行 = 能编进当前引擎、供给已知、报价已知；可评分 = 能对应现有评分项。四者分开数。",
        "sectors": out,
    }


# --------------------------------------------------------------------------- 菜单


def menu_diagnostics(
    config, opps: list[Opportunity], menu_docs: list[Menu], tables: dict[str, dict]
) -> dict[str, Any]:
    diag = config.menus.size_diagnostic
    ks = [int(k) for k in diag.get("housing_per_stratum", [1, 2, 3, 5, 8])]
    reps = int(diag.get("repeats", 20))
    build_id = menu_docs[0].build_id if menu_docs else "0" * 64
    size_rows: dict[str, list[dict[str, Any]]] = {}
    inclusion: dict[str, Any] = {}
    for m in config.metros:
        pool = [o for o in opps if o.cbsa == m.cbsa and o.sector == "housing"]
        medians = {
            bed: float(t["quantiles"]["0.50"])
            for bed in ("0", "1", "2", "3plus")
            if (t := tables.get(f"b.housing.{m.cbsa}.metro_bed.{bed}"))
            and t["quantiles"].get("0.50")
        }
        rows = []
        for k in ks:
            policy = MenuPolicy(
                sector="housing",
                kind="annual_exposure_sample",
                strata_key="bedrooms",
                per_stratum=k,
            )
            best_ratio, short_commute = [], []
            for r in range(reps):
                mnu = menus.build_menu(
                    build_id=build_id,
                    cbsa=m.cbsa,
                    year=1,
                    sector="housing",
                    env_repeat=100 + r,
                    policy=policy,
                    opportunities=pool,
                    global_seed=config.build.seed,
                )
                chosen = [o for o in pool if o.opportunity_id in set(mnu.selected)]
                for bed, med in medians.items():
                    rents = [
                        float(o.fact("monthly_rent").value)
                        for o in chosen
                        if o.stratum == bed
                        and o.fact("monthly_rent")
                        and o.fact("monthly_rent").status == "present"
                    ]  # type: ignore[union-attr,arg-type]
                    if rents:
                        best_ratio.append(min(rents) / med)
                rt = [
                    int(o.fact("commute_roundtrip_minutes").value)
                    for o in chosen
                    if o.fact("commute_roundtrip_minutes")
                    and o.fact("commute_roundtrip_minutes").status == "present"
                ]  # type: ignore[union-attr,arg-type]
                if rt:
                    short_commute.append(sum(1 for x in rt if x <= 60) / len(rt))
            rows.append(
                {
                    "per_stratum": k,
                    "mean_cheapest_over_market_median": round(statistics.mean(best_ratio), 4)
                    if best_ratio
                    else None,
                    "mean_share_roundtrip_le_60min": round(statistics.mean(short_commute), 4)
                    if short_commute
                    else None,
                }
            )
        size_rows[m.cbsa] = rows
        inclusion[m.cbsa] = _inclusion_check(pool, "1", 3, reps=400)
    shared = _shared_menu_check(menu_docs)
    return {
        "note_zh": (
            "菜单大小诊断：同一城市用不同的每层名额 k 各抽 20 次（环境重复 100–119），看最便宜的一套相对新租市场中位数、往返 ≤ 60 分钟的比例怎样变。"
            "k 越大，最便宜的那套越便宜——所以各城必须用同一个 k，不能靠给某座城多抽来制造优势。inclusion：无放回加权抽样里，权重最大的候选的"
            "实际入选频率（400 次模拟）与“k × 权重份额”的对照；权重不是入选概率。"
        ),
        "size_diagnostic": size_rows,
        "inclusion": inclusion,
        "menus_per_policy": dict(Counter(mnu.policy for mnu in menu_docs)),
        "shared_menus": shared,
    }


def _inclusion_check(pool: list[Opportunity], stratum: str, k: int, *, reps: int) -> dict[str, Any]:
    cand = [
        o
        for o in pool
        if o.origin == "mapping_b"
        and o.stratum == stratum
        and o.target_weight
        and float(o.target_weight) > 0
    ]
    if len(cand) <= k:
        return {"candidates": len(cand), "note": "候选不多于名额，全部入选"}
    w = np.array([float(o.target_weight) for o in cand])  # type: ignore[arg-type]
    rng = np.random.default_rng(20261022)
    hits = np.zeros(len(cand))
    for _ in range(reps):
        keys = np.log(rng.random(len(cand))) / w
        hits[np.argsort(-keys)[:k]] += 1
    top = np.argsort(-w)[:5]
    share = w / w.sum()
    return {
        "candidates": len(cand),
        "k": k,
        "top5": [
            {
                "opportunity_id": cand[i].opportunity_id,
                "weight_share": round(float(share[i]), 5),
                "k_times_share": round(float(min(1.0, k * share[i])), 5),
                "simulated_inclusion": round(float(hits[i] / reps), 5),
            }
            for i in top
        ],
    }


def _shared_menu_check(menu_docs: list[Menu]) -> dict[str, Any]:
    """同城、同年、同板块、同环境重复只有一份菜单（分身共享）；不同环境重复的种子不同。"""
    keys = Counter((m.cbsa, m.year, m.sector, m.env_repeat) for m in menu_docs)
    seeds = defaultdict(set)
    for m in menu_docs:
        seeds[(m.cbsa, m.year, m.sector)].add(m.seed)
    return {
        "duplicate_menus": sum(1 for v in keys.values() if v > 1),
        "distinct_seed_per_env_repeat": all(
            len(v) == len({m.env_repeat for m in menu_docs if (m.cbsa, m.year, m.sector) == key})
            for key, v in seeds.items()
        ),
        "seed_inputs": [
            "build_id",
            "cbsa",
            "year",
            "sector",
            "env_repeat",
            "algorithm",
            "config_seed",
        ],
        "note_zh": "种子不含分身编号：同一环境重复下的分身拿到同一份菜单；恢复与重新查询读冻结的菜单，不重抽。",
    }


def _frac(x: str) -> float:
    return float(Fraction(x))
