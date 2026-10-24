"""报告：覆盖矩阵、来源统计、映射诊断（与来源分布对照）、菜单诊断。

每一项都只报告，不设通过线；数字是测出来的，偏差与口径写在旁边。“测试通过”“映射合理”“现实有效”是三件事：这里的对照只能说明
映射复现了来源的分布、与独立发布的数字一致到什么程度，不能说明城市评价已经标定。
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.intake_contracts import Menu, Opportunity, StdRecord, applies_to
from product.realdata import mapping_b

REPORT_VERSION = "intake-reports-v3"


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
        "gaps": gap_summary(opps),
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
            elif d["geo"]["country"] == config.build.country:
                rec_national[d["sector"]] += 1
    cells: dict[str, dict[str, Any]] = {}
    for m in config.metros:
        row: dict[str, Any] = {}
        for sector in ("housing", "jobs", "training", "food", "goods", "activities", "background"):
            mine = [
                o
                for o in opps
                if o.sector == sector and applies_to(o, m.cbsa, config.build.country)
            ]
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
                "needs_data_only": sum(o.compile.status == "needs_data" for o in mine),
                "needs_e1_only": sum(o.compile.status == "needs_e1_extension" for o in mine),
                "needs_data_and_e1": sum(
                    o.compile.status == "needs_data_and_e1_extension" for o in mine
                ),
                "reference_only": sum(o.compile.status == "reference_only" for o in mine),
                "any_data_gap": sum(bool(o.compile.data_gaps) for o in mine),
                "any_e1_gap": sum(bool(o.compile.e1_gaps) for o in mine),
                "planning_reference": sum(o.capabilities.planning_reference for o in mine),
                "executable": sum(o.capabilities.executable for o in mine),
                "scorable": sum(o.capabilities.scorable for o in mine),
            }
        cells[m.cbsa] = {"label": m.label, "label_zh": m.label_zh, "sectors": row}
    return {
        "note_zh": (
            "records_in_metro = 这座都会区里实际取得的真实记录；records_national_applicable = 全国性的记录（例如全国认证的家电型号、全国均价），"
            "适用于同一国家的每座城但不是本地观测；只在加拿大销售的家电型号不算。opportunities 里 derived 是由真实记录经确定换算得到的"
            "（例如全国食材均价按地区价格水平换到本都会区），mapping_a / mapping_b 是映射生成的候选，都不是本地真实观测。"
            "estimated_or_assumed = 机会里最弱的来源是估计或假设。编译状态五选一、互不重叠、相加等于 opportunities："
            "compilable（现在就能编进 E1）、needs_data_only（只缺资料）、needs_e1_only（只缺 E1 表达能力）、needs_data_and_e1"
            "（两样都缺）、reference_only（城市背景这类参考事实）。any_data_gap / any_e1_gap 是“至少有一项资料缺口 / E1 缺口”的"
            "条数，两者重叠，分母都是 opportunities，不能相加当总数。"
        ),
        "goods_markets": goods_markets(opps, config.build.country),
        "metros": cells,
    }


def goods_markets(opps: list[Opportunity], country: str) -> dict[str, Any]:
    goods = [o for o in opps if o.sector == "goods"]
    by_scope = Counter(str(o.attributes.get("market_scope")) for o in goods)
    return {
        "total_models": len(goods),
        "by_market_scope": dict(sorted(by_scope.items())),
        "in_us_catalog": sum(1 for o in goods if o.geo.country == country),
        "note_zh": (
            "us = 只在美国、us_ca = 美加共同市场：进美国目录；ca = 只在加拿大销售：留在资料库（地理是加拿大），不进美国目录、"
            "不计入美国覆盖；unknown = 来源没写市场：进美国目录但标为市场未知（地理是代理），不当成确定可得，也不当成确定不可得。"
        ),
    }


def gap_summary(opps: list[Opportunity]) -> dict[str, Any]:
    """注册表里每项机会（不按城市重复计）的编译缺口：五种状态的分项（互不重叠）与各原因的条数（重叠，分母是板块总数）。"""
    out: dict[str, Any] = {}
    for sector in sorted({o.sector for o in opps}):
        mine = [o for o in opps if o.sector == sector]
        out[sector] = {
            "total": len(mine),
            "status": dict(sorted(Counter(o.compile.status for o in mine).items())),
            "any_data_gap": sum(bool(o.compile.data_gaps) for o in mine),
            "any_e1_gap": sum(bool(o.compile.e1_gaps) for o in mine),
            "data_gap_reasons": dict(
                Counter(r for o in mine for r in o.compile.data_gaps).most_common()
            ),
            "e1_gap_reasons": dict(
                Counter(r for o in mine for r in o.compile.e1_gaps).most_common()
            ),
        }
    return {
        "note_zh": (
            "每项机会只数一次（全国性的不按城市重复）。status 五种互不重叠、相加等于 total；any_data_gap、any_e1_gap 与各原因的"
            "条数互相重叠（一条机会可以同时缺价格、缺日历，还要 E1 适配），分母都是 total，不能相加。只缺 E1 表达的才是“改 E1 就能执行”；"
            "缺资料的改 E1 也执行不了。"
        ),
        "sectors": out,
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
                    "market_median_step": mk["median_step"] if mk else None,
                    "market_se_median_step": mk["se_median_step"] if mk else None,
                    "market_median_interpolated": mk["median_interpolated"] if mk else None,
                    "market_se_median_interpolated": mk["se_median_interpolated"] if mk else None,
                    "stock_median_contract": st["quantiles"].get("0.50") if st else None,
                    "stock_n": st["n"] if st else 0,
                }
            )
        market = hdf[hdf["full_puma"] & hdf["segment"].isin(mapping_b.MARKET)]
        b = [o for o in opps if o.cbsa == m.cbsa and o.kind == "rental_support_cell"]
        comparison = []
        for bed in ("0", "1", "2", "3plus"):
            src = market[market["bed_class"] == bed]
            cells = [o for o in b if o.stratum == bed and o.attributes.get("support_table")]
            # 展开后的逻辑候选：目标格总权重 × 支持点权重 ÷ 支持表总权重；只用合同租金已知的支持点（顶码不进分位数）
            cv: list[float] = []
            cw: list[float] = []
            for o in cells:
                tab = tables[str(o.attributes["support_table"])]
                scale = float(o.target_weight or 0) / max(tab["weight_sum"], 1)
                for pt in tab["points"]:
                    if pt["contract_rent"] is not None and pt["price_status"] != "censored":
                        cv.append(float(pt["contract_rent"]))
                        cw.append(pt["weight"] * scale)
            row = {"bedrooms": bed, "source_n": len(src), "target_cells": len(cells)}
            cens = src["censored"].to_numpy(bool)
            limit = (
                float(np.nanmin(src["censor_bound"].to_numpy(float)[cens]))
                if cens.any()
                else math.inf
            )
            for q in (0.25, 0.5, 0.75):
                row[f"source_q{q}"] = (
                    mapping_b.step_quantile(
                        src["rent_contract"].to_numpy(float),
                        src["WGTP"].to_numpy(float),
                        q,
                        limit,
                    )
                    if len(src)
                    else None
                )
                row[f"support_q{q}"] = _wq(cv, cw, q)
            comparison.append(row)
        levels = Counter(str(o.attributes.get("price_level_used")) for o in b)
        a = [o for o in opps if o.cbsa == m.cbsa and o.kind == "coverage_grid_candidate"]
        out[m.cbsa] = {
            "validation_vs_published": validation,
            "market_vs_stock": market_vs_stock,
            "support_vs_source_distribution": comparison,
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
            "support_vs_source_distribution：展开后的完整支持分布（目标格总权重 × 支持点份额，合同租金已知的部分）对照来源样本；"
            "目标格自己的样本够时两者一致，回退格借上层的形状，回退越粗差异越大。"
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
            "programs_compilable": sum(1 for o in progs if o.compile.status == "compilable"),
            "programs_dated": sum(
                1 for o in progs if o.calendar is not None and o.calendar.known and o.calendar.start
            ),
            "programs_single_day": sum(
                1
                for o in progs
                if o.calendar and o.calendar.start and o.calendar.start == o.calendar.end
            ),
            "programs_age": dict(
                sorted(
                    Counter(
                        "unknown"
                        if o.attributes.get("age_restricted") is None
                        else ("restricted" if o.attributes.get("age_restricted") else "all_ages")
                        for o in progs
                    ).items()
                )
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
        "note_zh": (
            "只有芝加哥有带价格与日历的城市开放资料；其他城市的活动只有场所（价格未知）。这是来源覆盖的差异，不是城市之间的真实差异。"
            "芝加哥的活动都有具体日期，不少还有年龄限制：E1 的体验没有日期窗口、资格标签不求值，模拟年份与真实日期的对应也没定义，"
            "所以现在一项都不能原样编进 E1（programs_compilable）；原始日历与年龄限制照存（programs_dated、programs_age）。"
        ),
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
    shared = _shared_menu_check(menu_docs)
    return {
        "note_zh": (
            "住房的候选池是完整加权经验支持表（每个目标格一条，intake 里只列出、不抽样）；年度接触菜单在 E3B 资料包里从完整可执行池"
            "按同一个名额抽，30、60、120 的菜单诊断也在那里（docs/design/e3b-life-pack.md）。这里只核对共享菜单与日历菜单。"
        ),
        "menus_per_policy": dict(Counter(mnu.policy for mnu in menu_docs)),
        "calendar_menus": {
            "filter": dict(
                Counter(mnu.calendar_filter for mnu in menu_docs if mnu.policy == "calendar_open")
            ),
            "year1_env0": {
                f"{mnu.cbsa}:{mnu.sector}": {
                    "listed": mnu.n_eligible,
                    "calendar_known": mnu.n_calendar_known,
                    "calendar_unknown": mnu.n_calendar_unknown,
                }
                for mnu in menu_docs
                if mnu.policy == "calendar_open" and mnu.year == 1 and mnu.env_repeat == 0
            },
            "note_zh": "按日历开放的菜单现在没有按日历筛选（pending_mapping：模拟年份与真实日期的对应还没定义），只列出观测到的项目及原始日历；日历已知与未知分开数。",
        },
        "shared_menus": shared,
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
