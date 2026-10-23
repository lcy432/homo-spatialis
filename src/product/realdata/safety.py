"""E3B 都会区安全背景的资料与换算（所有者 2026-10-23 的调整；DECISIONS/0042）。

每个都会区一个安全背景值，城内所有居住片区共用；不研究住宅、街区或交通方式与安全的关系。它表示这个都会区的治安情况对生活里
安全考虑的影响，**不是**个人遭遇犯罪或死亡的概率。

资料（全部免密钥的官方来源，原始文件进缓存并核对 SHA-256）：

* FBI《Crime in the United States 2024》估计表（CDE 下载 ``cius/2024/cius-estimations-2024.zip``）：表 6 按都会区给出人口、
  暴力犯罪与财产犯罪的数（报告不全的机构由 FBI 估算，“Estimated total”）；表 1 给出全国 2024 年的人口与两类犯罪数（全国参照）。
* 表 6 没有纽约都会区。纽约用纽约州刑事司法服务局（DCJS）的县合计（data.ny.gov ``sh5y-5av7``，2024 年）汇总都会区里纽约州的县，
  人口用人口普查局 2024 年县人口估计（PEP Vintage 2024）。新泽西与宾州部分没有可比的免密钥资料：覆盖范围照实写成“都会区的
  纽约州部分”，不冒称整个都会区。

汇总一律先加案件数与人口再算率，不平均各地的率。换算（登记的模型设定 ``fbi_relative_rate_v1``）：

    r = w_v × 暴力率 / 全国暴力率 + w_p × 财产率 / 全国财产率        （w_v = w_p = 0.5）
    安全指数 = min(10, 6 / r)，取到 0.001；评分条件里的背景 b = 指数 / 2

全国平均（r = 1）是指数 6、b = 3（与原来的共同先验相同）；犯罪率越高 r 越大、指数越低（不会因犯罪率升高变好）；每城只与固定的
全国参照比，改一城的资料不改别城的值。缺报不当零犯罪：资料缺失就报未知，不用六城平均填补。
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from product.realdata.fetch import DEFAULT_CACHE, Request, _store, read_cached

CIUS_KEY = "cius/2024/cius-estimations-2024.zip"
CIUS_REQUEST = Request(
    source_id="fbi_cius_2024",
    url=f"https://cde.ucr.cjis.gov/LATEST/s3/signedurl?key={CIUS_KEY}",
    expect="zip",
    timeout=120,
)
DCJS_REQUEST = Request(
    source_id="ny_dcjs_county_totals",
    url="https://data.ny.gov/api/views/sh5y-5av7/rows.csv?accessType=DOWNLOAD",
    expect="any",
    timeout=120,
)
PEP_REQUEST = Request(
    source_id="census_pep_county_2024",
    url="https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/co-est2024-alldata.csv",
    expect="any",
    timeout=120,
)
TABLE1 = "CIUS_Table_1_Crime_in_the_United_States_by_Volume_and_Rate_per_100000_Inhabitants_2005-2024.xlsx"
TABLE6 = "CIUS_Table_6_Crime_in_the_United_States_by_Metropolitan_Statistical_Area_2024.xlsx"
PER = Decimal(100000)


def fetch_safety_sources(cache: Path = DEFAULT_CACHE) -> dict[str, str]:
    """显式联网：取三份原始文件进缓存（已有就不取）。CDE 的文件要先要一个限时签名链接；缓存按固定的请求键存。"""
    import httpx

    from product.realdata.fetch import USER_AGENT, fetch

    out: dict[str, str] = {}
    hit = read_cached(CIUS_REQUEST, cache)
    if not hit.ok:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120) as c:
            signed = c.get(CIUS_REQUEST.url).json()[CIUS_KEY]
            r = c.get(signed)
            r.raise_for_status()
            _store(cache, CIUS_REQUEST, r.content, r.status_code, r.headers.get("content-type", ""))
    out["fbi_cius_2024"] = read_cached(CIUS_REQUEST, cache).record.status
    for req in (DCJS_REQUEST, PEP_REQUEST):
        out[req.source_id] = fetch(req, cache, "cache_first").record.status
    return out


def source_digests(cache: Path = DEFAULT_CACHE) -> dict[str, str | None]:
    """三份原始文件在缓存里的 SHA-256（进资料包的构建身份；缓存里没有或对不上是 None）。"""
    out: dict[str, str | None] = {}
    for req in (CIUS_REQUEST, DCJS_REQUEST, PEP_REQUEST):
        hit = read_cached(req, cache)
        out[req.source_id] = hit.record.raw_sha256 if hit.ok else None
    return out


@dataclass(frozen=True)
class CrimeCount:
    """一个地理范围 2024 年的两类犯罪数与人口（汇总后的数，不是率）。"""

    population: int
    violent: int
    property: int

    @property
    def violent_rate(self) -> Decimal:
        return Decimal(self.violent) * PER / Decimal(self.population)

    @property
    def property_rate(self) -> Decimal:
        return Decimal(self.property) * PER / Decimal(self.population)


def aggregate(parts: list[CrimeCount]) -> CrimeCount:
    """先合计案件数与人口，再算率（不平均各地的率）。"""
    return CrimeCount(
        population=sum(p.population for p in parts),
        violent=sum(p.violent for p in parts),
        property=sum(p.property for p in parts),
    )


@dataclass
class SafetyInput:
    cbsa: str
    count: CrimeCount | None
    scope: str
    nature: str
    coverage: str
    inputs: list[str] = field(default_factory=list)
    missing: str | None = None
    residence: str = "—"  # 资料范围与模拟居住范围的关系


def residence_relation(
    data: list[tuple[str, str]], home: list[tuple[str, str]], whole_metro: bool
) -> str:
    """资料覆盖的县与模拟居住的县（都是（FIPS，县名））的关系，照实写成一句话。"""
    d, h = {f for f, _ in data}, {f for f, _ in home}
    if not h <= d:
        out = sorted(n for f, n in home if f not in d)
        return f"模拟居住范围有 {len(out)} 个县不在资料里（{'、'.join(out)}）：背景值只代表资料覆盖的县"
    if d == h:
        return "资料范围与模拟居住范围相同" + ("（整个都会区）" if whole_metro else "")
    extra = sorted(n for f, n in data if f not in h)
    return (
        f"模拟居住范围（{len(h)} 个县）都在资料范围里；资料另含 {len(extra)} 个不在模拟居住范围里的县"
        f"（{'、'.join(extra)}），背景值是整个资料范围的综合水平"
    )


def relative_index(
    count: CrimeCount,
    national: CrimeCount,
    *,
    w_violent: Decimal,
    w_property: Decimal,
    national_index: Decimal,
    cap: Decimal,
) -> tuple[Decimal, Decimal]:
    """（r，安全指数）：r 是相对全国的综合犯罪水平，指数 = min(cap, national_index / r)，取到 0.001。"""
    r = w_violent * count.violent_rate / national.violent_rate + w_property * (
        count.property_rate / national.property_rate
    )
    index = min(cap, national_index / r).quantize(Decimal("0.001"))
    return r, index


def _cius(cache: Path) -> zipfile.ZipFile:
    hit = read_cached(CIUS_REQUEST, cache)
    if not hit.ok:
        raise FileNotFoundError(
            f"缓存里没有 FBI CIUS 2024 估计表（{hit.record.status}）：先运行 fetch-safety"
        )
    assert hit.content is not None
    return zipfile.ZipFile(io.BytesIO(hit.content))


def national_2024(cache: Path = DEFAULT_CACHE) -> CrimeCount:
    import pandas as pd

    t1 = pd.read_excel(_cius(cache).open(TABLE1), header=None)
    row = next(r for r in t1.itertuples(index=False) if str(r[0]).strip() == "2024")
    return CrimeCount(population=int(row[1]), violent=int(row[2]), property=int(row[14]))


def msa_rows(cache: Path = DEFAULT_CACHE) -> dict[str, dict[str, Any]]:
    """表 6：都会区名称 → 人口、全区（含 FBI 估算）两类犯罪数、实际报告覆盖、用的是哪一行。只读每个都会区开头的全区合计那一组。"""
    import pandas as pd

    t6 = pd.read_excel(_cius(cache).open(TABLE6), header=None)
    col0 = [str(v) for v in t6.iloc[:, 0].tolist()]
    heads = [i for i, v in enumerate(col0) if "M. S. A." in v]
    out: dict[str, dict[str, Any]] = {}
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(col0)
        name = col0[i].replace("M. S. A.", "").strip()
        reported = estimated = None
        for r in range(i + 1, end):
            label = str(t6.iloc[r, 1])
            if label.startswith("Total area actually reporting") and reported is None:
                reported = r
            elif label.startswith("Estimated total") and estimated is None:
                estimated = r
            elif label.startswith("Rate per 100,000"):
                break
        use = estimated if estimated is not None else reported
        if use is None:
            continue
        share = Decimal(str(t6.iloc[reported, 2])) if reported is not None else None
        out[name] = {
            "population": int(t6.iloc[i, 2]),
            "violent": int(t6.iloc[use, 3]),
            "property": int(t6.iloc[use, 8]),
            "reporting_share": share,
            "row": "Estimated total" if estimated is not None else "Total area actually reporting",
        }
    return out


def ny_state_part(
    counties: list[tuple[str, str]], cache: Path = DEFAULT_CACHE
) -> tuple[CrimeCount, list[str], int]:
    """纽约都会区的纽约州县：DCJS 2024 县合计 + PEP 2024 县人口。``counties`` 是（5 位 FIPS，“县名, 州名”）。返回（汇总，县名，都会区全部县人口）。"""
    dcjs = read_cached(DCJS_REQUEST, cache)
    pep = read_cached(PEP_REQUEST, cache)
    if not (dcjs.ok and pep.ok):
        raise FileNotFoundError("缓存里没有 DCJS 县合计或 PEP 县人口：先运行 fetch-safety")
    assert dcjs.content is not None and pep.content is not None
    rows = {
        r["County"].strip(): r
        for r in csv.DictReader(io.StringIO(dcjs.content.decode("utf-8-sig")))
        if r["Year"] == "2024" and r["Agency"] == "County Total"
    }
    pops = {
        r["STATE"] + r["COUNTY"]: int(r["POPESTIMATE2024"])
        for r in csv.DictReader(io.StringIO(pep.content.decode("latin-1")))
        if r["SUMLEV"] == "050"
    }
    parts, names = [], []
    for fips, name in counties:
        if not fips.startswith("36"):
            continue
        short = name.split(",")[0].replace(" County", "").strip()
        r = rows.get(short)
        if r is None:
            raise ValueError(f"DCJS 2024 县合计里没有 {short}：不当零犯罪")
        parts.append(
            CrimeCount(
                population=pops[fips],
                violent=int(r["Violent Total"]),
                property=int(r["Property Total"]),
            )
        )
        names.append(short)
    metro_pop = sum(pops[f] for f, _ in counties)
    return aggregate(parts), names, metro_pop


def home_counties(city: dict[str, Any], counties: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """模拟居住范围的县：配置写了 ``home_counties`` 就用它，否则取都会区里 ``home_states`` 的县。"""
    if city.get("home_counties"):
        return [(f, n) for f, n in counties if f in set(city["home_counties"])]
    return [(f, n) for f, n in counties if f[:2] in set(city.get("home_states", ()))]


def safety_inputs(
    cities: list[dict[str, Any]],
    county_lists: dict[str, list[tuple[str, str]]],
    titles: dict[str, str],
    cache: Path = DEFAULT_CACHE,
) -> tuple[dict[str, SafetyInput], CrimeCount]:
    """六城的安全输入与全国参照。``titles``：CBSA → 划定文件里的都会区名称（与表 6 的名称对上）。"""
    national = national_2024(cache)
    table = msa_rows(cache)
    out: dict[str, SafetyInput] = {}
    for c in cities:
        cbsa = c["cbsa"]
        home = home_counties(c, county_lists[cbsa])
        row = table.get(titles[cbsa])
        if row is not None:
            share = row["reporting_share"]
            out[cbsa] = SafetyInput(
                cbsa=cbsa,
                count=CrimeCount(row["population"], row["violent"], row["property"]),
                scope=f"整个都会区（{titles[cbsa]}，FBI 表 6）",
                nature="estimated" if row["row"] == "Estimated total" else "observed",
                coverage=(
                    f"FBI 估计总数：实际报告的机构覆盖 {share * 100:.1f}% 人口，其余由 FBI 估算"
                    if row["row"] == "Estimated total"
                    else "全部机构报告（100%），FBI 实际报告合计"
                ),
                inputs=[f"fbi_cius_2024:{TABLE6}:{titles[cbsa]}:{row['row']}"],
                residence=residence_relation(county_lists[cbsa], home, whole_metro=True),
            )
        elif cbsa == "35620":
            part, names, metro_pop = ny_state_part(county_lists[cbsa], cache)
            out[cbsa] = SafetyInput(
                cbsa=cbsa,
                count=part,
                scope=f"纽约都会区的纽约州部分（{len(names)} 个县：{'、'.join(names)}）",
                nature="observed",
                coverage=(
                    f"FBI 表 6 没有纽约都会区：DCJS 2024 县合计（各执法机构报告的汇总，缺报不补），覆盖都会区人口的 "
                    f"{Decimal(part.population) * 100 / Decimal(metro_pop):.1f}%；新泽西与宾州的县没有可比的免密钥资料，不在内"
                ),
                inputs=["ny_dcjs_county_totals:2024", "census_pep_county_2024:POPESTIMATE2024"],
                residence=residence_relation(
                    [(f, n) for f, n in county_lists[cbsa] if f.startswith("36")],
                    home,
                    whole_metro=False,
                ),
            )
        else:
            out[cbsa] = SafetyInput(
                cbsa=cbsa,
                count=None,
                scope="—",
                nature="unknown",
                coverage="—",
                missing="FBI 表 6 没有这个都会区，也没有可汇总的县资料：未知，不当零犯罪，也不用他城平均填补",
            )
    return out, national
