"""E3B 物品与职业一侧的输入切片：从 intake-02 冻结库与原始文件缓存里只读出编译需要的那部分，存成一份规范 JSON。

编译（``life_items``、``occupations``）只读这份切片，不直接碰数据库或网络；测试用同样结构的小样本切片。切片里每个数都保留原文
文字（十进制字符串）与它来自哪条记录；原始文件只按获取记录里的缓存键读，SHA-256 对不上就拒绝（与 intake 同一套
``fetch.read_cached``）。

读的东西：

* 冻结库：构建身份；每个都会区的食材估计价（``food.item.<cbsa>.<代码>``）与商品类空间换算系数、intake 的三个篮子方案月费（对照）、
  CE 家庭在外饮食支出、RPP、OSM 餐馆菜系计数（只数 ``amenity=restaurant``）与活动场所计数、所需职业的 OEWS 工资分位与 EP 入职要求、
  工时表、芝加哥公园活动、IPEDS 培训项目；美国市场的 ENERGY STAR 型号；
* 原始缓存：CPI-U（CE 两年平均 → 2024 年平均的换算）、USDA FoodData Central SR Legacy（食材能量与份量克重）、BLS ATUS 表 1
  （做饭时间的量级核对，摘句逐字核对）、芝加哥公园活动原始 JSON（intake 记录只存了日期说明的前 200 个字符，数上课次数要完整说明；
  文件指纹必须与 intake 记录的相同）。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from product.realdata.daily_support_data import (
    CE_DETAIL_REQUEST,
    CE_SIZE_REQUEST,
    CPI_COMMUNICATION_REQUEST,
    FCC_METHOD_REQUEST,
    FCC_URS_REQUEST,
    ce_title,
    read_ce_means,
    read_cpi_annual,
    read_fcc_urs,
)
from product.realdata.fetch import DEFAULT_CACHE, Request, read_cached
from product.realdata.prices import months, parse_cpi
from product.realdata.sources import chicago_activities_request, cpi_request
from product.realdata.tax_rules import page_text

SLICE_SCHEMA = "e3b-items-slice-v2"
FDC_REQUEST = Request(
    source_id="usda_fdc_sr_legacy",
    url="https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip",
    expect="zip",
    timeout=180,
)
ATUS_REQUEST = Request(
    source_id="bls_atus", url="https://www.bls.gov/news.release/atus.t01.htm", timeout=60
)
#: ATUS 表 1 里核对的两行（逐字出现在网页正文里才用）：活动名后面依次是全体、男、女的每天平均小时，参与比例（全体、男、女），
#: 参与者每天平均小时（全体、男、女）
ATUS_ROWS = {
    "food_prep": "Food preparation and cleanup",
    "consumer_goods": "Consumer goods purchases",
}
FDC_ENERGY_NUTRIENT = "1008"  # Energy, KCAL
_PREFIX = "FoodData_Central_sr_legacy_food_csv_2018-04/"
_FACTOR = re.compile(r"×([0-9.]+)")


class SliceError(ValueError):
    pass


def _json(conn: sqlite3.Connection, sql: str, *args: object) -> dict[str, Any] | None:
    row = conn.execute(sql, args).fetchone()
    return None if row is None else json.loads(row[0])


def _fields(d: dict[str, Any], key: str = "fields") -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in d[key]}


def _soc_major(soc: str) -> str:
    return soc.split("-")[0]


# --------------------------------------------------------------------------- 原始缓存


def read_fdc(content: bytes, item_energy: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """SR Legacy → 每种 AP 食材的能量（千卡 / 100 克）与需要的份量克重。只读配置里点名的条目。"""
    z = zipfile.ZipFile(io.BytesIO(content))
    wanted = {str(v["fdc_id"]) for v in item_energy.values() if v.get("fdc_id")}

    def rows(name: str):
        return csv.DictReader(io.TextIOWrapper(z.open(_PREFIX + name), encoding="utf-8"))

    desc = {r["fdc_id"]: r["description"] for r in rows("food.csv") if r["fdc_id"] in wanted}
    energy = {
        r["fdc_id"]: r["amount"]
        for r in rows("food_nutrient.csv")
        if r["fdc_id"] in wanted and r["nutrient_id"] == FDC_ENERGY_NUTRIENT
    }
    portions: dict[str, dict[str, str]] = {}
    for r in rows("food_portion.csv"):
        # SR Legacy 的份量名写在 modifier 栏（“large”“quart”），portion_description 是空的
        name = r["modifier"] or r["portion_description"]
        if r["fdc_id"] in wanted and r["amount"] == "1" and name:
            portions.setdefault(r["fdc_id"], {})[name] = r["gram_weight"]
    out: dict[str, Any] = {}
    for fid in sorted(wanted):
        if fid not in desc or fid not in energy:
            raise SliceError(f"SR Legacy 里没有条目 {fid} 或它的能量")
        out[fid] = {
            "description": desc[fid],
            "kcal_per_100g": energy[fid],
            "portions": portions.get(fid, {}),
        }
    return out


def read_atus(content: bytes) -> dict[str, Any]:
    """ATUS 表 1：核对两行原文逐字在网页正文里，读出参与者每天平均小时（全体）。"""
    text = page_text(content)
    title = text[: text.find(" - ")] if " - " in text else text[:160]
    out: dict[str, Any] = {"title": title, "rows": {}}
    for key, label in ATUS_ROWS.items():
        m = re.search(re.escape(label) + r"((?: [0-9.]+){9})", text)
        if m is None:
            raise SliceError(f"ATUS 表里找不到“{label}”这一行")
        nums = m.group(1).split()
        out["rows"][key] = {
            "quote": label + m.group(1),
            "avg_hours_per_day_all": nums[0],
            "share_engaged_all": nums[3],
            "hours_per_day_engaged_all": nums[6],
        }
    return out


def _raw(req: Request, cache: Path) -> tuple[bytes, str]:
    got = read_cached(req, cache)
    if not got.ok or got.content is None:
        raise SliceError(f"原始缓存里没有 {req.source_id}：{got.record.note or got.record.status}")
    assert got.record.raw_sha256 is not None
    return got.content, got.record.raw_sha256


def _ce_lines(items_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """日常支持与消费篮子要读的全部 CE 条目（一人表的条目与详细表里拆分用的大类、子项）。"""
    ds, cb = items_cfg["daily_support"], items_cfg["consumption_basket"]
    return [
        ds["personal_communication"]["line"],
        ds["home_connectivity"]["ce_fallback_line"],
        *ds["household_supplies"]["lines"],
        *cb["lines"],
    ]


def _daily_support(items_cfg: dict[str, Any], cache: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """BLS CE 2024 全国表、FCC URS 2024 与 CPI 互联网服务：编译日常支持与消费篮子要用的数（只解析，不定价）。

    CE 两张表必须有。FCC 与 CPI 可以缺：缓存里没有（未获取，或获取时被拒、明确不可得）就在切片里标 ``unavailable``，编译时
    家庭宽带回退到 CE 预算代理（``ce_internet_budget_proxy_v1``）；文件在缓存里但指纹不符、损坏或格式不对就拒绝构建，不当作
    资料缺失。三份都在时输出与原来逐字节相同。"""

    def optional(req: Request) -> tuple[bytes, str] | None:
        got = read_cached(req, cache)
        if got.record.status == "not_cached":
            return None
        if not got.ok or got.content is None:
            raise SliceError(
                f"{req.source_id} 的原始文件在缓存里但不能用（{got.record.note or got.record.status}）：拒绝构建，不当作资料缺失回退"
            )
        assert got.record.raw_sha256 is not None
        return got.content, got.record.raw_sha256

    ds = items_cfg["daily_support"]
    hc = ds["home_connectivity"]
    size_b, size_sha = _raw(CE_SIZE_REQUEST, cache)
    det_b, det_sha = _raw(CE_DETAIL_REQUEST, cache)
    fcc_got, method_got, cpi_got = (
        optional(r) for r in (FCC_URS_REQUEST, FCC_METHOD_REQUEST, CPI_COMMUNICATION_REQUEST)
    )
    lines = _ce_lines(items_cfg)
    size_labels = sorted({x["size_label"] for x in lines})
    detail_labels = sorted(
        {x["detail_parent"] for x in lines if x.get("detail_items")}
        | {i for x in lines for i in x.get("detail_items", ())}
    )
    years = (hc["cpi_from_year"], hc["cpi_to_year"])
    out = {
        "ce": {
            "size_table": ce_title(size_b),
            "detail_table": ce_title(det_b),
            "one_person": read_ce_means(size_b, size_labels, ds["ce_size_column_one_person"]),
            "all_units_size_table": read_ce_means(size_b, size_labels, ds["ce_size_column_all"]),
            "detail_all_units": read_ce_means(det_b, detail_labels, ds["ce_detail_column_all"]),
            "unit": "USD per consumer unit per year (CE 2024 calendar year)",
        },
        # 文件在、但内容解析不出（缺列、缺年份……）时解析函数报错，同样拒绝构建
        "fcc": read_fcc_urs(fcc_got[0], hc["fcc_spec"], hc["regions"])
        if fcc_got
        else {"status": "unavailable", "reason_zh": "FCC URS 2024 原始文件未获取或明确不可得"},
        "cpi_internet": {
            "series": hc["cpi_series"],
            "annual": read_cpi_annual(cpi_got[0], hc["cpi_series"], years),
        }
        if cpi_got
        else {
            "series": hc["cpi_series"],
            "status": "unavailable",
            "reason_zh": "CPI 互联网服务原始文件未获取，2023 年调查价换不到 2024 年",
        },
    }
    raw = {
        "bls_ce_national_2024_size": size_sha,
        "bls_ce_national_2024_detail": det_sha,
        **{
            key: got[1]
            for key, got in (
                ("fcc_urs_2024_broadband", fcc_got),
                ("fcc_urs_2024_methodology", method_got),
                ("bls_cpi_u_education_communication", cpi_got),
            )
            if got is not None
        },
    }
    return out, raw


def _internet(conn: sqlite3.Connection, cbsa: str) -> dict[str, Any] | None:
    """ACS 接入字段的描述（intake ``household_internet_access@1``，租住户）：只用于描述与核对，不当运营商可得率。"""
    row = conn.execute(
        "select json from tables where method = 'household_internet_access@1' and cbsa = ?",
        (cbsa,),
    ).fetchone()
    if row is None:
        return None
    t = json.loads(row[0])
    return {
        "table_id": t["table_id"],
        "population": t["population"],
        "n": t["n"],
        "variables": t["variables"],
    }


# --------------------------------------------------------------------------- 冻结库


def _food(conn: sqlite3.Connection, cbsa: str) -> dict[str, Any]:
    items: dict[str, Any] = {}
    factor: str | None = None
    for oid, j in conn.execute(
        "select opportunity_id, json from opportunities where kind = 'food_item' and cbsa = ?",
        (cbsa,),
    ):
        d = json.loads(j)
        price = _fields(d, "facts")["price"]
        if price["status"] != "present":
            continue
        items[oid.rsplit(".", 1)[1].upper()] = {
            "opportunity_id": oid,
            "price": price["value"],
            "unit_text": d["attributes"]["unit_text"],
            "lineage": d["lineage"],
        }
        m = _FACTOR.search(price.get("method") or "")
        if m and factor is None:
            factor = m.group(1)
    plans = {}
    for oid, j in conn.execute(
        "select opportunity_id, json from opportunities where kind = 'food_plan' and cbsa = ?",
        (cbsa,),
    ):
        d = json.loads(j)
        plans[oid.rsplit(".", 1)[1]] = _fields(d, "facts")["monthly_food_cost"]["value"]
    return {"items": items, "goods_factor": factor, "intake_plans": plans}


def _background(conn: sqlite3.Connection, cbsa: str) -> dict[str, Any]:
    ce = _json(
        conn, "select json from records where record_id = ?", f"background.food_spending.{cbsa}"
    )
    rpp = _json(
        conn, "select json from records where record_id = ?", f"background.price_parities.{cbsa}"
    )
    if ce is None or rpp is None:
        raise SliceError(f"{cbsa}: 冻结库里没有 CE 食品支出或 RPP")
    cf, rf = _fields(ce), _fields(rpp)
    return {
        "ce": {
            "record_id": ce["record_id"],
            "food_away_per_cu_year": cf["food_away_per_cu_year"]["value"],
            "food_at_home_per_cu_year": cf["food_at_home_per_cu_year"]["value"],
            "food_away_numeraire": cf["food_away_per_cu_year"]["numeraire"],
            "persons_per_cu": cf["persons_per_cu"]["value"],
            "period": ce["period"]["label"],
        },
        "rpp": {
            "record_id": rpp["record_id"],
            **{k: rf[k]["value"] for k in ("rpp_all", "rpp_goods", "rpp_other")},
        },
    }


def _restaurants(conn: sqlite3.Connection, cbsa: str) -> dict[str, Any]:
    """OSM ``amenity=restaurant`` 的菜系标签组合与家数（一家写了几个标签只算一家；没写菜系的另计）。"""
    sets: Counter[tuple[str, ...]] = Counter()
    snapshot = None
    for (j,) in conn.execute(
        "select json from records where record_type = 'restaurant_venue' and cbsa = ?", (cbsa,)
    ):
        d = json.loads(j)
        a = d["attributes"]
        if a.get("amenity") != "restaurant":
            continue
        snapshot = snapshot or d["period"]["label"]
        tags = {x.strip().lower() for x in (a.get("cuisine") or "").split(";") if x.strip()}
        sets[tuple(sorted(tags))] += 1
    return {
        "restaurant_total": sum(sets.values()),
        "tag_sets": [{"tags": list(k), "count": n} for k, n in sorted(sets.items())],
        "snapshot": snapshot,
    }


def _venues(conn: sqlite3.Connection, cbsa: str) -> dict[str, Any]:
    rows = []
    city = None
    for (j,) in conn.execute(
        "select json from records where record_type = 'activity_venue' and cbsa = ?", (cbsa,)
    ):
        d = json.loads(j)
        a = d["attributes"]
        city = city or d["geo"]["label"]
        rows.append(
            {
                "venue_type": a["venue_type"],
                "named": bool(a.get("named")),
                "access": a.get("access"),
                "sports": sorted(
                    {s.strip() for s in (a.get("sport") or "").split(";") if s.strip()}
                ),
            }
        )
    # 只存计数：同一组属性合成一行（场所名单本身在冻结库里，这里不需要）
    agg: Counter[str] = Counter(json.dumps(r, sort_keys=True) for r in rows)
    return {
        "within": city,
        "groups": [{**json.loads(k), "count": n} for k, n in sorted(agg.items())],
    }


def _wages(conn: sqlite3.Connection, cbsa: str, socs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for soc in socs:
        rec = _json(
            conn, "select json from records where record_id = ?", f"jobs.oews2024.{cbsa}.{soc}"
        )
        if rec is None:
            continue
        f = _fields(rec)
        out[soc] = {
            "record_id": rec["record_id"],
            "title": rec["attributes"]["occupation_title"],
            "wage_basis": rec["attributes"]["wage_basis"],
            "period": rec["period"]["label"],
            **{
                k: (f[k]["value"] if k in f and f[k]["status"] == "present" else None)
                for k in (
                    "employment",
                    "annual_wage_p10",
                    "annual_wage_p25",
                    "annual_wage_median",
                    "annual_wage_p75",
                    "annual_wage_p90",
                )
            },
            "conversion": (f.get("annual_wage_median") or {}).get("method"),
        }
    return out


def _hours(conn: sqlite3.Connection, cbsa: str, majors: list[str]) -> dict[str, Any]:
    out = {}
    for mj in majors:
        t = _json(conn, "select json from tables where table_id = ?", f"b.jobs.{cbsa}.{mj}")
        if t is not None:
            out[mj] = {
                "table_id": t["table_id"],
                "level": t["level"],
                "n": t["n"],
                "median_weekly_hours": t["median_weekly_hours"],
                "wfh_share": t["wfh_share"],
            }
    return out


def _ep(conn: sqlite3.Connection, socs: list[str], cbsas: list[str]) -> dict[str, Any]:
    """EP 全国入职要求（intake 记录 ``jobs.ep.<soc>``）。只收至少一个都会区有 OEWS 记录的职业；EP 没有这个职业时报错，不补。"""
    out: dict[str, Any] = {}
    for soc in socs:
        if not any(
            conn.execute(
                "select 1 from records where record_id = ?", (f"jobs.oews2024.{cbsa}.{soc}",)
            ).fetchone()
            for cbsa in cbsas
        ):
            continue
        rec = _json(conn, "select json from records where record_id = ?", f"jobs.ep.{soc}")
        if rec is None:
            raise SliceError(
                f"{soc}: 有 OEWS 工资记录，但冻结库里没有 EP 入职要求（jobs.ep.{soc}）；"
                "EP 没有分配这个职业，或者这是删除三档岗位候选之前的旧 intake 构建（要用现行代码重建）"
            )
        f = _fields(rec)
        out[soc] = {
            k: f[k]["value"]
            for k in (
                "typical_entry_education",
                "related_work_experience",
                "typical_on_the_job_training",
            )
        }
    return out


def chicago_full_notes(content: bytes) -> dict[str, str]:
    """芝加哥公园活动原始 JSON → 活动编号 → 完整的日期说明（intake 的记录只存了前 200 个字符）。"""
    from product.realdata.activities import _text

    return {
        str(r["activity_id"]): _text(r.get("date_notes")) or ""
        for r in json.loads(content.decode("utf-8"))
        if r.get("activity_id") is not None
    }


def _programs(
    conn: sqlite3.Connection, cbsa: str, full_notes: tuple[dict[str, str], str] | None
) -> list[dict[str, Any]]:
    out = []
    for (j,) in conn.execute(
        "select json from records where record_type = 'activity_program' and cbsa = ? order by record_id",
        (cbsa,),
    ):
        d = json.loads(j)
        a, f = d["attributes"], _fields(d)
        fee, hours = f["program_fee"], f["session_hours"]
        notes = a.get("date_notes")
        if full_notes is not None:
            # 原始文件必须就是 intake 读的那一份（记录里记着它的 SHA-256），不然完整说明可能对不上
            if d["source"]["raw_sha256"] != full_notes[1]:
                raise SliceError(
                    f"{d['record_id']}: 原始缓存的芝加哥活动文件与 intake 记录的指纹不同"
                )
            notes = full_notes[0].get(d["record_id"].rsplit(".", 1)[1], notes)
        out.append(
            {
                "record_id": d["record_id"],
                "label": d["label"],
                "category": a.get("category"),
                "activity_type": a.get("activity_type"),
                "age_range": a.get("age_range"),
                "season": a.get("season"),
                "facility": a.get("facility"),
                "days": a.get("days"),
                "date_notes": notes,
                "start": d["period"]["start"],
                "end": d["period"]["end"],
                "cancelled": a.get("cancelled"),
                "fee": fee["value"] if fee["status"] == "present" else None,
                "fee_raw": fee.get("raw"),
                "fee_method": fee.get("method"),
                "session_hours": hours["value"] if hours["status"] == "present" else None,
            }
        )
    return out


def _training(conn: sqlite3.Connection, cbsa: str) -> list[dict[str, Any]]:
    out = []
    for (j,) in conn.execute(
        "select json from records where record_type = 'training_program' and cbsa = ? order by record_id",
        (cbsa,),
    ):
        d = json.loads(j)
        a, f = d["attributes"], _fields(d)
        tu, mo, rel = f["tuition_fees"], f["months_to_complete"], f["related_occupations"]
        out.append(
            {
                "record_id": d["record_id"],
                "institution": a.get("institution"),
                "city": a.get("city"),
                "cip": a.get("cip"),
                "tuition": tu["value"] if tu["status"] == "present" else None,
                "tuition_method": tu.get("method"),
                "months": mo["value"] if mo["status"] == "present" else None,
                "related_socs": [s for s in (rel.get("value") or "").split(";") if s],
            }
        )
    return out


def _appliances(
    conn: sqlite3.Connection, markets: list[str], categories: set[str]
) -> list[dict[str, Any]]:
    out = []
    for (j,) in conn.execute(
        "select json from records where record_type = 'appliance_model' order by record_id"
    ):
        d = json.loads(j)
        a, f = d["attributes"], _fields(d)
        if a["market_scope"] not in markets or a["category"] not in categories:
            continue
        energy = f.get("annual_energy") or {}
        out.append(
            {
                "record_id": d["record_id"],
                "category": a["category"],
                "type": a.get("type"),
                "brand": a.get("brand"),
                "model_number": a.get("model_number"),
                "market_scope": a["market_scope"],
                "annual_energy_kwh": energy.get("value")
                if energy.get("status") == "present"
                else None,
                "capacity": (f.get("capacity") or {}).get("value"),
            }
        )
    return out


def build_slice(
    db_path: Path,
    items_cfg: dict[str, Any],
    occ_cfg: dict[str, Any],
    *,
    cache: Path = DEFAULT_CACHE,
) -> dict[str, Any]:
    """读冻结库与原始缓存，生成编译用的切片。构建身份与配置写的不同就拒绝。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    build_id = conn.execute("select value from meta where key = 'build_id'").fetchone()[0]
    for name, cfg in (("物品", items_cfg), ("职业", occ_cfg)):
        if cfg["intake_build_id"] != build_id:
            raise SliceError(
                f"{name}配置写的 intake 构建是 {cfg['intake_build_id'][:16]}，冻结库是 {build_id[:16]}"
            )
    cbsas = [m["cbsa"] for m in items_cfg["metros"]]
    scope = [s["soc"] for s in occ_cfg["supported"]]
    service_socs = sorted(
        {s["soc"] for s in items_cfg["services"]["chores"]}
        | {t["soc"] for t in items_cfg["activities"]["templates"] if t.get("soc")}
        | {items_cfg["food"]["away_cost"][k] for k in ("cook_soc", "delivery_soc")}
    )
    socs = sorted(set(scope) | set(service_socs) | set(occ_cfg["excluded"]))
    majors = sorted({_soc_major(s) for s in socs})
    categories = {p["category"] for o in items_cfg["devices"]["offers"] for p in o["parts"]}

    cpi_bytes, cpi_sha = _raw(cpi_request(), cache)
    cpi = parse_cpi(cpi_bytes)
    ce_factor = cpi.factor(months("2023-01", "2024-12"))
    fdc_bytes, fdc_sha = _raw(FDC_REQUEST, cache)
    atus_bytes, atus_sha = _raw(ATUS_REQUEST, cache)
    chi_bytes, chi_sha = _raw(chicago_activities_request(), cache)
    full_notes = (chicago_full_notes(chi_bytes), chi_sha)
    daily, daily_raw = _daily_support(items_cfg, cache)

    metros: dict[str, Any] = {}
    for cbsa in cbsas:
        metros[cbsa] = {
            "food": _food(conn, cbsa),
            **_background(conn, cbsa),
            "restaurants": _restaurants(conn, cbsa),
            "venues": _venues(conn, cbsa),
            "wages": _wages(conn, cbsa, socs),
            "hours": _hours(conn, cbsa, majors),
            "programs": _programs(conn, cbsa, full_notes),
            "training": _training(conn, cbsa),
            "internet_access": _internet(conn, cbsa),
        }
    return {
        "schema": SLICE_SCHEMA,
        "intake_build_id": build_id,
        "raw": {
            "bls_cpi_u": cpi_sha,
            "usda_fdc_sr_legacy": fdc_sha,
            "bls_atus": atus_sha,
            "chicago_park_activities": chi_sha,
            **daily_raw,
        },
        "cpi": {
            "series": "CUUR0000SA0",
            "ce_2023_2024_to_2024": {
                "numerator": ce_factor.numerator,
                "denominator": ce_factor.denominator,
                "from": "2023-01..2024-12 平均",
                "to": "2024 年年平均",
            },
        },
        "fdc": read_fdc(fdc_bytes, items_cfg["food"]["item_energy"]),
        "atus": read_atus(atus_bytes),
        "ep": _ep(conn, socs, cbsas),
        "appliances": _appliances(conn, items_cfg["devices"]["markets"], categories),
        "daily_support": daily,
        "metros": metros,
    }
