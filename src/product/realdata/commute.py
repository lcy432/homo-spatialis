"""E3B 通勤资料（DECISIONS/0042 决定 4）：六城的片区、通勤关系表与用车权。

只做资料与编译，生成引擎已有的契约对象（``AreaDecl``、``CommuteLeg``、``CommuteModeRule``、``CommuteRelation`` 与一项
``LifeOpportunity``），不改引擎。``configs/intake/commute_us_v1.yaml`` 写方法参数、每城的范围与票价摘句。

* **居住片区**：2020 PUMA，完整位于都会区内（按普查区，与 intake 的住房同一规则），并且在税的支持范围内（纽约只要五个区、
  芝加哥只要伊利诺伊）。居民平时的往返分钟 = 居民（在职、不在家工作）单程通勤分钟的加权中位数 × 2。
* **就业片区**：2020 年的工作地 PUMA（官方代码表给出每个工作地 PUMA 由哪些县组成；这六个都会区里都是一县或几县一个）。
  只要全部所在县都在都会区内、州在税的支持范围内的；按流入在职者的加权人数排序，份额不低于 ``own_area_min_share`` 的各成
  一个片区，其余合并成“其余工作地”（``powpuma_share_v1``）。
* **往返分钟**：住处 PUMA × 就业片区 × 方式（JWTRNS 归成步行骑车 / 公交 / 开车）里实际通勤者单程分钟（JWMNP）的 PWGTP 加权
  阶梯中位数 × 2。每格至少 ``min_unweighted_n`` 条未加权记录；不够依次回退到同一通勤层的居民合并格、本城全部居住片区的合并格，
  再不够就是缺（这条线没有这种方式，不当 0 分钟）。合并格只给 ``pooled_fallback_modes``，并且这条线本身至少要有
  ``min_line_evidence`` 条用这种方式的记录。每格记下用了哪一层与未加权条数。
* **花费**：公交 = 两次单程的官方票价，月票当作月度封顶；开车 = 里程（分钟 × 登记速度）÷ 每加仑英里数 × 本都会区 2024 年汽油
  均价（intake 的 BLS AP），四舍五入到分；开车要有在用的用车权（一项按月付费的普通机会）。步行骑车 0。票价、油耗与用车费用
  按 ``tax_rules`` 的同一套离线核对：摘句逐字在缓存的原始网页里、每个数都在摘句里；不过就阻断这一城的这种方式。

原始文件只读缓存：intake 的来源（PUMS、划定、普查区→PUMA、PUMA 名称）按冻结库记下的请求键与 SHA-256 读，对不上就拒绝
（``RawChangedError``）；新的代码表按配置里钉住的 SHA-256 读。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import sqlite3
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from common.intake_contracts import FetchRecord, content_identity
from common.life_contracts import (
    COMMUTE_MODES,
    AreaDecl,
    CommuteLeg,
    CommuteModeRule,
    CommuteRelation,
    LifeOpportunity,
    SourceRecord,
)
from common.money import Money
from common.provenance import Provenance
from product.realdata import geo
from product.realdata.build import RawChangedError
from product.realdata.config import DEFAULT_CONFIG, load_config
from product.realdata.fetch import DEFAULT_CACHE, Fetched, Mode, Request, fetch
from product.realdata.mapping_b import wquantile
from product.realdata.sources import all_requests
from product.realdata.tax_rules import numbers_in, page_text

COMMUTE_CONFIG = Path("configs/intake/commute_us_v1.yaml")
FROZEN_DIR = Path("data/frozen/life/us-v1/commute")
SCHEMA = "e3b-commute-city-v1"
LEVELS = ("puma", "tier", "city")
LEVEL_LABEL = {
    "puma": "住处 PUMA 格",
    "tier": "同一通勤层的居民合并格",
    "city": "本城全部居住片区合并格",
}
#: PUMS 2024 是全年调查；来源记录的日期取观测期的最后一天。
OBSERVED_2024 = _dt.date(2024, 12, 31)
CENT = Decimal("0.01")
PERSON_COLUMNS = ("STATE", "PUMA", "PWGTP", "ESR", "JWMNP", "JWTRNS", "POWSP", "POWPUMA", "SOCP")


def load_commute_config(path: Path | str = COMMUTE_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _dec(x: Any) -> Decimal:
    return Decimal(repr(float(x))) if isinstance(x, float) else Decimal(str(x))


def _money(d: Decimal, numeraire: str) -> Money:
    return Money(float(d), numeraire)


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- 请求


def codelist_request(cfg: dict[str, Any]) -> Request:
    c = cfg["codelist"]
    return Request(c["cache_source"], c["url"], expect="xlsx", timeout=300)


def cost_requests(cfg: dict[str, Any]) -> dict[str, Request]:
    """票价、油耗与用车费用的网页（按配置里的来源编号）。获取命令与离线构建用同一份请求，缓存键一定对得上。"""
    return {
        sid: Request(source_id=s["cache_source"], url=s["url"], expect="any", timeout=60)
        for sid, s in cfg["sources"].items()
    }


# --------------------------------------------------------------------------- 摘句核对（与 tax_rules 同一套）


@dataclass(frozen=True)
class Cited:
    """通过核对的一个数与它的摘句。"""

    value: Decimal
    citations: tuple[dict[str, Any], ...]

    @property
    def first(self) -> dict[str, Any]:
        return self.citations[0]


@dataclass(frozen=True)
class TransitFare:
    agency: str
    fare: Cited
    monthly_pass: Cited | None
    period_note: str
    not_modeled: tuple[str, ...]


@dataclass
class CostBuild:
    """票价与开车费用的核对结果：每城的公交票价或阻断原因、油耗与用车费用或阻断原因、每份网页的获取记录。"""

    transit: dict[str, TransitFare] = field(default_factory=dict)
    transit_blocked: dict[str, list[str]] = field(default_factory=dict)
    mpg: Cited | None = None
    car_annual: Cited | None = None
    drive_blocked: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def verify_cited(
    spec: dict[str, Any],
    cfg: dict[str, Any],
    texts: dict[str, str],
    fetched: dict[str, Fetched],
    sources: dict[str, dict[str, Any]],
) -> tuple[Cited | None, list[str]]:
    """核对一个数：每条摘句逐字在那份网页的正文里，这个数出现在某条摘句里。"""
    problems: list[str] = []
    cites: list[dict[str, Any]] = []
    for c in spec["cite"]:
        sid = c["source"]
        if sid not in texts:
            problems.append(f"来源 {sid} 没有可用的原始网页（{sources[sid]['status']}）")
            continue
        if c["quote"] not in texts[sid]:
            problems.append(
                f"摘句没有逐字出现在 {cfg['sources'][sid]['url']} 的正文里：{c['quote'][:80]!r}"
            )
            continue
        rec = fetched[sid].record
        assert rec.raw_sha256 is not None
        cites.append(
            {
                "source": sid,
                "publisher": cfg["sources"][sid]["publisher"],
                "url": cfg["sources"][sid]["url"],
                "retrieved_on": str(rec.retrieved_at or "1970-01-01")[:10],
                "raw_sha256": rec.raw_sha256,
                "quote": c["quote"],
            }
        )
    value = _dec(spec["value"])
    if not problems:
        numbers = set().union(*(numbers_in(c["quote"]) for c in cites))
        if value not in numbers:
            problems.append(f"{value} 没有出现在摘句里")
    if problems:
        return None, problems
    return Cited(value=value, citations=tuple(cites)), []


def compile_costs(
    cfg: dict[str, Any] | None = None, cache: Path = DEFAULT_CACHE, *, mode: Mode = "offline"
) -> CostBuild:
    """离线核对全部票价、油耗与用车费用。``mode`` 只给显式的获取命令用；构建与测试一律离线。"""
    if cfg is None:
        cfg = load_commute_config()
    out = CostBuild()
    fetched: dict[str, Fetched] = {}
    texts: dict[str, str] = {}
    for sid, req in cost_requests(cfg).items():
        f = fetch(req, cache, mode)
        fetched[sid] = f
        out.sources[sid] = f.record.model_dump(mode="json")
        if f.ok:
            assert f.content is not None
            texts[sid] = page_text(f.content)

    for city in cfg["cities"]:
        key, t = city["key"], city["transit"]
        if t.get("blocked"):
            reasons = [t["blocked"]]
            reasons += [
                f"来源 {sid}：{out.sources[sid]['status']} {out.sources[sid].get('note', '')}".strip()
                for sid in t.get("sources", ())
            ]
            out.transit_blocked[key] = reasons
            continue
        fare, why = verify_cited(t["fare"], cfg, texts, fetched, out.sources)
        problems = [f"单程票价：{w}" for w in why]
        cap = None
        if t.get("monthly_pass") is not None:
            cap, why = verify_cited(t["monthly_pass"], cfg, texts, fetched, out.sources)
            problems += [f"月票：{w}" for w in why]
        if problems:
            out.transit_blocked[key] = problems
            continue
        assert fare is not None
        out.transit[key] = TransitFare(
            agency=t["agency"],
            fare=fare,
            monthly_pass=cap,
            period_note=t["period_note"],
            not_modeled=tuple(t.get("not_modeled", ())),
        )

    d = cfg["drive"]
    out.mpg, why = verify_cited(d["mpg"], cfg, texts, fetched, out.sources)
    out.drive_blocked += [f"每加仑英里数：{w}" for w in why]
    out.car_annual, why = verify_cited(d["car_access"]["annual"], cfg, texts, fetched, out.sources)
    out.drive_blocked += [f"用车权年费用：{w}" for w in why]
    return out


# --------------------------------------------------------------------------- intake 冻结库


@dataclass(frozen=True)
class GasFact:
    """一个都会区 2024 年的汽油均价（intake 的 BLS AP 都会区序列，``ap_annual_mean@1``）。"""

    value: Decimal
    record_id: str
    locator: str
    raw_sha256: str


@dataclass
class IntakeFacts:
    build_id: str
    fetch_log: dict[str, FetchRecord]
    tiers: dict[str, dict[str, str]]
    medians: dict[str, dict[str, float]]
    gasoline: dict[str, GasFact]


def read_intake(db: Path) -> IntakeFacts:
    """从 intake-02 的冻结库读：构建身份、获取记录（原始文件指纹）、每个 PUMA 的通勤层与居民单程中位数、各都会区汽油均价。"""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("select key, value from meta"))
        log = {
            n: FetchRecord.model_validate_json(j)
            for n, j in con.execute("select name, json from fetches")
        }
        tiers: dict[str, dict[str, str]] = {}
        medians: dict[str, dict[str, float]] = {}
        for cbsa, j in con.execute(
            "select cbsa, json from tables where method = 'commute_by_residence_puma@1'"
        ):
            t = json.loads(j)
            if t.get("level") != "puma":
                continue
            if t.get("tier"):
                tiers.setdefault(cbsa, {})[t["key"]] = t["tier"]
            if t.get("median_oneway") is not None:
                medians.setdefault(cbsa, {})[t["key"]] = float(t["median_oneway"])
        gasoline: dict[str, GasFact] = {}
        for rid, cbsa, j in con.execute(
            "select record_id, cbsa, json from records where record_id like 'background.energy_prices.%'"
        ):
            r = json.loads(j)
            f = next(
                (
                    x
                    for x in r["fields"]
                    if x["name"] == "gasoline_regular_per_gallon" and x["status"] == "present"
                ),
                None,
            )
            if f is None or f.get("numeraire") != "USD_2024_LOCAL":
                continue
            gasoline[cbsa] = GasFact(
                value=Decimal(f["value"]),
                record_id=rid,
                locator=r["source"]["locator"],
                raw_sha256=r["source"]["raw_sha256"],
            )
    finally:
        con.close()
    return IntakeFacts(
        build_id=meta["build_id"], fetch_log=log, tiers=tiers, medians=medians, gasoline=gasoline
    )


def bound_raw(name: str, req: Request, cache: Path, log: dict[str, FetchRecord]) -> bytes:
    """按冻结库记下的指纹读原始文件：请求键与 SHA-256 都要对上，否则拒绝（不悄悄读到另一份内容）。"""
    rec = log.get(name)
    if rec is None or rec.status != "ok" or rec.raw_sha256 is None:
        raise RawChangedError(f"{name} 在 intake 的获取阶段没有取得，不能读")
    if req.key != rec.request_key:
        raise RawChangedError(f"{name}：请求与 intake 获取阶段记下的不同")
    return pinned_raw(name, req, cache, rec.raw_sha256)


def pinned_raw(name: str, req: Request, cache: Path, sha256: str) -> bytes:
    r = fetch(req, cache, "offline")
    if not r.ok:
        raise RawChangedError(f"{name}：缓存里读不到这份原始文件（{r.record.status}）")
    if r.record.raw_sha256 != sha256:
        raise RawChangedError(
            f"{name}：缓存里的内容（SHA-256 {str(r.record.raw_sha256)[:12]}…）不是记下的那一份（{sha256[:12]}…）"
        )
    assert r.content is not None
    return r.content


# --------------------------------------------------------------------------- 原始文件的读取


@dataclass(frozen=True)
class PowLookup:
    """官方代码表：工作地 PUMA（州两位 + 五位代码）由哪些县组成，县名。"""

    counties: dict[tuple[str, str], frozenset[str]]
    county_names: dict[str, str]

    def label(self, key: tuple[str, str]) -> str:
        return "、".join(self.county_names.get(c, c) for c in sorted(self.counties.get(key, ())))


def parse_powpuma_lookup(content: bytes, sheet: str) -> PowLookup:
    """“Geography Lookup List”表（州、县、县名、州、PUMA、工作地州三位、工作地 PUMA）→ 工作地 PUMA 的县组成。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    rows = wb[sheet].iter_rows(values_only=True)
    counties: dict[tuple[str, str], set[str]] = {}
    names: dict[str, str] = {}
    started = False
    for r in rows:
        if not started:
            started = isinstance(r[0], str) and r[0].startswith("State FIPS Code")
            continue
        if r[0] is None or r[6] is None:
            continue
        st, co, name, powsp, pow_ = str(r[0]), str(r[1]), str(r[2]), str(r[5]), str(r[6])
        fips = st.zfill(2) + co.zfill(3)
        counties.setdefault((powsp.zfill(3)[-2:], pow_.zfill(5)), set()).add(fips)
        names[fips] = name
    if not counties:
        raise ValueError(f"代码表 {sheet} 里没有读到县 → 工作地 PUMA 的对应")
    return PowLookup({k: frozenset(v) for k, v in counties.items()}, names)


def read_commuters(content: bytes, employed_esr: list[int]) -> pd.DataFrame:
    """一个州的 PUMS 人员文件 → 在职、上周不在家工作、有通勤分钟的人（居住与工作地、方式、分钟、权重、职业大类）。"""
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(
        n for n in z.namelist() if n.lower().startswith("psam_p") and n.lower().endswith(".csv")
    )
    with z.open(name) as f:
        df = pd.read_csv(
            f,
            usecols=list(PERSON_COLUMNS),
            dtype={"STATE": str, "PUMA": str, "POWSP": str, "POWPUMA": str, "SOCP": str},
        )
    df = df[df["ESR"].isin(employed_esr) & (df["JWMNP"] > 0) & df["JWTRNS"].notna()]
    df = df[df["JWTRNS"] != 11].copy()
    df["STATE"] = df["STATE"].str.zfill(2)
    df["PUMA"] = df["PUMA"].str.zfill(5)
    df["POWSP"] = df["POWSP"].fillna("").str.zfill(3)
    df["POWPUMA"] = df["POWPUMA"].fillna("").str.zfill(5)
    df["JWTRNS"] = df["JWTRNS"].astype(int)
    df["JWMNP"] = df["JWMNP"].astype(int)
    df["PWGTP"] = df["PWGTP"].astype(int)
    #: SOC 2018 大类（SOCP 的前两位，例如 15 = 计算机与数学）；没有职业代码的是空串
    df["SOC2"] = df["SOCP"].fillna("").str.strip().str[:2]
    return df[
        ["STATE", "PUMA", "PWGTP", "JWMNP", "JWTRNS", "POWSP", "POWPUMA", "SOC2"]
    ].reset_index(drop=True)


@dataclass
class RawInputs:
    geography: dict[str, geo.MetroGeo]
    lookup: PowLookup
    commuters: pd.DataFrame
    fingerprints: dict[str, str]


def load_raw(
    cfg: dict[str, Any],
    intake: IntakeFacts,
    cache: Path = DEFAULT_CACHE,
    intake_config: Path = DEFAULT_CONFIG,
) -> RawInputs:
    """按指纹读全部原始文件。PUMS 用 intake 获取阶段请求过的全部州（流入在职者也算邻州居民）。"""
    bcfg = load_config(intake_config)
    states = sorted(n.split(":")[1] for n in intake.fetch_log if n.startswith("pums_p:"))
    reqs = all_requests(bcfg, states)
    log = intake.fetch_log

    def raw(name: str) -> bytes:
        return bound_raw(name, reqs[name], cache, log)

    names = geo.puma_names_from_geos(geo.parse_acs_geos(raw("acs_geos")))
    geography = geo.metro_geography(
        bcfg.metros, raw("cbsa_delineation"), raw("tract_puma"), names, None
    )
    lookup = parse_powpuma_lookup(
        pinned_raw("codelist", codelist_request(cfg), cache, cfg["codelist"]["sha256"]),
        cfg["codelist"]["sheet"],
    )
    frames = [read_commuters(raw(f"pums_p:{st}"), cfg["method"]["employed_esr"]) for st in states]
    fingerprints = {
        n: log[n].raw_sha256 or ""
        for n in ("acs_geos", "cbsa_delineation", "tract_puma", *(f"pums_p:{s}" for s in states))
    }
    fingerprints["codelist"] = cfg["codelist"]["sha256"]
    return RawInputs(geography, lookup, pd.concat(frames, ignore_index=True), fingerprints)


# --------------------------------------------------------------------------- 片区


def select_home_pumas(
    city: dict[str, Any], mg: geo.MetroGeo
) -> tuple[list[geo.PumaMembership], dict[str, list[dict[str, Any]]]]:
    """居住片区：完整位于都会区内、并且在税的支持范围内的 PUMA。部分在内的与范围外的分开列出。"""
    scope = city.get("home_scope") or {}
    states, counties = set(scope.get("states", ())), set(scope.get("counties", ()))
    keep: list[geo.PumaMembership] = []
    excluded: dict[str, list[dict[str, Any]]] = {"partial_in_metro": [], "outside_tax_scope": []}
    for p in sorted(mg.pumas, key=lambda p: p.code):
        if not p.full:
            excluded["partial_in_metro"].append(
                {"puma": p.code, "share_tracts": f"{p.tracts_in_metro}/{p.tracts_total}"}
            )
        elif (states and p.state not in states) or (counties and not set(p.counties) <= counties):
            excluded["outside_tax_scope"].append({"puma": p.code, "counties": list(p.counties)})
        else:
            keep.append(p)
    return keep, excluded


@dataclass(frozen=True)
class WorkArea:
    area_id: str
    label: str
    powpumas: tuple[tuple[str, str], ...]
    counties: tuple[str, ...]
    weight: int
    share: Decimal


def _pow_key(df: pd.DataFrame) -> pd.Series:
    return df["POWSP"].str[-2:] + ":" + df["POWPUMA"]


def define_work_areas(
    key: str,
    commuters: pd.DataFrame,
    lookup: PowLookup,
    metro_counties: set[str],
    work_states: set[str],
    min_share: float,
) -> tuple[list[WorkArea], dict[str, Any]]:
    """``powpuma_share_v1``：可支持的工作地 PUMA 按流入在职者的加权人数排序；份额不低于 ``min_share`` 的各成一个片区，其余合并。"""
    supported, partial, other_state = [], [], []
    for k, cs in sorted(lookup.counties.items()):
        inside = cs & metro_counties
        if not inside:
            continue
        if k[0] not in work_states:
            other_state.append(k)
        elif cs <= metro_counties:
            supported.append(k)
        else:
            partial.append(k)
    inbound = commuters.groupby(_pow_key(commuters))["PWGTP"].sum()
    w = {k: int(inbound.get(f"{k[0]}:{k[1]}", 0)) for k in supported}
    total = sum(w.values())
    if total == 0:
        raise ValueError(f"{key}: 可支持的工作地没有观测到任何流入在职者")
    ranked = sorted((k for k in supported if w[k] > 0), key=lambda k: (-w[k], k))
    share = {k: Decimal(w[k]) / Decimal(total) for k in ranked}
    own = [k for k in ranked if share[k] >= _dec(min_share)]
    rest = [k for k in ranked if k not in own]
    areas = [
        WorkArea(
            area_id=f"{key}.area.w.{k[0]}{k[1]}",
            label=_cut(f"就业片区·{lookup.label(k)}", 120),
            powpumas=(k,),
            counties=tuple(sorted(lookup.counties[k])),
            weight=w[k],
            share=share[k],
        )
        for k in own
    ]
    if rest:
        areas.append(
            WorkArea(
                area_id=f"{key}.area.w.other",
                label=_cut(
                    "就业片区·其余工作地（" + "、".join(lookup.label(k) for k in rest) + "）", 120
                ),
                powpumas=tuple(rest),
                counties=tuple(sorted(set().union(*(lookup.counties[k] for k in rest)))),
                weight=sum(w[k] for k in rest),
                share=sum((share[k] for k in rest), Decimal(0)),
            )
        )

    def weights(keys: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "powpuma": f"{k[0]}-{k[1]}",
                "counties": lookup.label(k),
                "inbound_weight": int(inbound.get(f"{k[0]}:{k[1]}", 0)),
            }
            for k in keys
        ]

    diag = {
        "rule": "powpuma_share_v1",
        "own_area_min_share": str(min_share),
        "supported_inbound_weight": total,
        "areas": [
            {
                "area_id": a.area_id,
                "powpumas": [f"{k[0]}-{k[1]}" for k in a.powpumas],
                "counties": [lookup.label(k) for k in a.powpumas],
                "inbound_weight": a.weight,
                "share": f"{a.share:.4f}",
            }
            for a in areas
        ],
        "excluded_partial_in_metro": weights(partial),
        "excluded_state_not_supported": weights(other_state),
        "supported_without_inbound": [f"{k[0]}-{k[1]}" for k in supported if w[k] == 0],
    }
    return areas, diag


def work_area_job_shares(
    commuters: pd.DataFrame, area_of: dict[str, str], area_ids: list[str]
) -> dict[str, Any]:
    """每个就业片区占本城可支持工作地岗位的份额：全部，以及按 SOC 2018 大类（SOCP 前两位）。

    岗位 = 到这些工作地上班、不在家工作的在职者（PWGTP 加权，缓存里各州的全部居民）。这是存量在职者的分布，不是招聘；
    份额四舍五入到 4 位，加起来不一定正好是 1。只用来说明把岗位放进就业片区的规则，不进评分。"""
    df = commuters.assign(area=_pow_key(commuters).map(area_of))
    df = df[df["area"].notna()]

    def shares(g: pd.DataFrame) -> dict[str, Any]:
        w = g.groupby("area")["PWGTP"].sum()
        total = int(w.sum())
        return {
            "n": len(g),
            "weight": total,
            "shares": {a: f"{Decimal(int(w.get(a, 0))) / Decimal(total):.4f}" for a in area_ids},
        }

    return {
        "rule": "到可支持工作地上班、不在家工作的在职者（PWGTP）在各就业片区的分布；SOC 大类 = SOCP 前两位",
        "all": shares(df),
        "by_soc_major": {
            str(soc): shares(g) for soc, g in df[df["SOC2"] != ""].groupby("SOC2", sort=True)
        },
    }


# --------------------------------------------------------------------------- 通勤格


@dataclass(frozen=True)
class LegCell:
    home: str
    work: str
    mode: str
    level: str
    n: int
    n_line: int
    oneway: int


def _cells(df: pd.DataFrame, keys: list[str]) -> dict[tuple[str, ...], tuple[int, int]]:
    """每格（未加权条数，单程分钟的 PWGTP 加权阶梯中位数）。"""
    out: dict[tuple[str, ...], tuple[int, int]] = {}
    for k, g in df.groupby(keys, sort=True):
        kk = k if isinstance(k, tuple) else (k,)
        v, w = g["JWMNP"].to_numpy(float), g["PWGTP"].to_numpy(float)
        out[tuple(str(x) for x in kk)] = (len(g), int(wquantile(v, w, 0.5)))
    return out


def estimate_legs(
    res: pd.DataFrame,
    homes: list[str],
    works: list[str],
    tiers: dict[str, str],
    method: dict[str, Any],
) -> tuple[list[LegCell], dict[str, Any]]:
    """住处 × 就业片区 × 方式的往返分钟，按格回退（见模块说明）。``res`` 已带 ``home``、``work``、``mode`` 三列。"""
    min_n, evidence = int(method["min_unweighted_n"]), int(method["min_line_evidence"])
    pooled = set(method["pooled_fallback_modes"])
    res = res.assign(tier=res["home"].map(tiers).fillna(""))
    by_line = _cells(res, ["home", "work", "mode"])
    by_tier = _cells(res[res["tier"] != ""], ["tier", "work", "mode"])
    by_city = _cells(res, ["work", "mode"])
    cells: list[LegCell] = []
    counts: Counter[str] = Counter()
    missing_why: Counter[str] = Counter()
    #: 用了合并格、而这条线本身至少有 10 条记录的格：合并格的中位数与这条线自己的（小样本）中位数差多少分钟（单程）。
    pooled_gap: list[int] = []
    for h in homes:
        for wk in works:
            for m in COMMUTE_MODES:
                n_line, med = by_line.get((h, wk, m), (0, 0))
                level = None
                if n_line >= min_n:
                    level, n, oneway = "puma", n_line, med
                elif m in pooled and n_line >= evidence:
                    for lv, cell in (
                        ("tier", by_tier.get((tiers.get(h, ""), wk, m))),
                        ("city", by_city.get((wk, m))),
                    ):
                        if lv == "tier" and h not in tiers:
                            continue
                        if cell is not None and cell[0] >= min_n:
                            level, (n, oneway) = lv, cell
                            break
                    if level is None:
                        missing_why[f"{m}: 合并格也不足 {min_n} 条"] += 1
                    elif n_line >= 10:
                        pooled_gap.append(oneway - med)
                elif m not in pooled:
                    missing_why[f"{m}: 住处 PUMA 格不足 {min_n} 条（这种方式不用合并格）"] += 1
                else:
                    missing_why[f"{m}: 这条线没有观测到用这种方式的人"] += 1
                if level is None:
                    counts[f"{m}:missing"] += 1
                    continue
                counts[f"{m}:{level}"] += 1
                cells.append(LegCell(h, wk, m, level, n, n_line, oneway))
    reachable = {(c.home, c.work) for c in cells}
    diag = {
        "cells_by_mode_and_level": {
            m: {lv: counts.get(f"{m}:{lv}", 0) for lv in (*LEVELS, "missing")}
            for m in COMMUTE_MODES
        },
        "missing_reasons": dict(sorted(missing_why.items())),
        "lines_without_any_mode": [
            f"{h}→{wk}" for h in homes for wk in works if (h, wk) not in reachable
        ],
        "pooled_vs_own_line": {
            "cells_with_own_line_n_ge_10": len(pooled_gap),
            "pooled_shorter_by_10min_or_more": sum(1 for x in pooled_gap if x <= -10),
            "pooled_longer_by_10min_or_more": sum(1 for x in pooled_gap if x >= 10),
            "note": "单程分钟：合并格中位数 − 这条线自己的小样本中位数；合并格不看到就业片区的远近",
        },
    }
    return cells, diag


def typical_oneway(
    res_all: pd.DataFrame, homes: list[str], tiers: dict[str, str], min_n: int
) -> dict[str, tuple[int, str, int]]:
    """居民平时的单程分钟（全部去向、全部方式）：住处 PUMA ≥ min_n 条用本 PUMA，否则同一通勤层合并，再否则全部居住片区合并。"""
    res_all = res_all.assign(tier=res_all["home"].map(tiers).fillna(""))
    by_home = _cells(res_all, ["home"])
    by_tier = _cells(res_all[res_all["tier"] != ""], ["tier"])
    v, w = res_all["JWMNP"].to_numpy(float), res_all["PWGTP"].to_numpy(float)
    city = (len(res_all), int(wquantile(v, w, 0.5))) if len(res_all) else (0, 0)
    out: dict[str, tuple[int, str, int]] = {}
    for h in homes:
        for lv, cell in (
            ("puma", by_home.get((h,))),
            ("tier", by_tier.get((tiers[h],)) if h in tiers else None),
            ("city", city),
        ):
            if cell is not None and cell[0] >= min_n:
                out[h] = (cell[1], lv, cell[0])
                break
    return out


def fuel_per_roundtrip(minutes: int, speed_mph: Decimal, mpg: Decimal, price: Decimal) -> Decimal:
    """油费 = 往返分钟 / 60 × 速度 ÷ 每加仑英里数 × 每加仑价格，四舍五入到分。"""
    return (Decimal(minutes) / 60 * speed_mph / mpg * price).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- 一座城


@dataclass
class CityResult:
    key: str
    payload: dict[str, Any]


def _home_label(p: geo.PumaMembership) -> str:
    name = (p.name or "").removesuffix(" PUMA").split(";")[0]
    return _cut(f"居住片区·{name}（PUMA {p.code}）" if name else f"居住片区·PUMA {p.code}", 120)


def _pums_input(state: str, fingerprints: dict[str, str]) -> str:
    return f"PUMS 2024 psam_p{state}.csv@{fingerprints.get(f'pums_p:{state}', '')[:12]}"


@dataclass(frozen=True)
class DrivePrice:
    """开车每次往返的油费依据：登记速度、每加仑英里数（FHWA）、本都会区汽油均价（BLS AP）。"""

    speed_mph: Decimal
    mpg: Decimal
    gasoline: GasFact

    def fuel(self, minutes: int) -> Decimal:
        return fuel_per_roundtrip(minutes, self.speed_mph, self.mpg, self.gasoline.value)


def resident_commuters(
    commuters: pd.DataFrame, home_codes: list[str], area_of: dict[str, str], method: dict[str, Any]
) -> pd.DataFrame:
    """居住片区的居民通勤者，带住处（``home``）、就业片区（``work``，不在任何就业片区为空）与方式（``mode``，没建模的方式为空）。"""
    res = commuters[(commuters["STATE"] + commuters["PUMA"]).isin(home_codes)].copy()
    res["home"] = res["STATE"] + res["PUMA"]
    res["work"] = _pow_key(res).map(area_of)
    res["mode"] = res["JWTRNS"].astype(str).map(method["jwtrns_modes"])
    return res


def _area_decls(
    cfg: dict[str, Any],
    key: str,
    cbsa: str,
    homes: list[geo.PumaMembership],
    tiers: dict[str, str],
    typical: dict[str, tuple[int, str, int]],
    work_areas: list[WorkArea],
    fingerprints: dict[str, str],
) -> list[AreaDecl]:
    factor = int(cfg["method"]["roundtrip_factor"])
    out: list[AreaDecl] = []
    for p in homes:
        kw: dict[str, Any] = {}
        if p.code in typical:
            oneway, lv, n = typical[p.code]
            kw = {
                "typical_roundtrip_minutes": oneway * factor,
                "typical_source": SourceRecord(
                    kind="estimated",
                    observed_on=OBSERVED_2024,
                    geography_scope=f"CBSA {cbsa} PUMA {p.code} 的居民",
                    mapping_version=cfg["version"],
                    inputs=(
                        _pums_input(p.state, fingerprints),
                        f"{LEVEL_LABEL[lv]}，未加权 {n} 条",
                    ),
                    rule=(
                        f"居民（在职、上周不在家工作）去各自工作地点的单程分钟 JWMNP 的 PWGTP 加权中位数 {oneway} × {factor}"
                        "（假设 commute_roundtrip_is_twice_oneway）"
                    ),
                ),
            }
        out.append(
            AreaDecl(
                area_id=f"{key}.area.h.{p.code}",
                label=_home_label(p),
                kind="home",
                note=_cut(
                    f"2020 PUMA {p.code}，完整位于 CBSA {cbsa} 内（按普查区）；资料的地理单位，不是街区；"
                    f"通勤层 {tiers.get(p.code, '未分层')}",
                    280,
                ),
                **kw,
            )
        )
    for a in work_areas:
        codes = "、".join(f"{k[0]}-{k[1]}" for k in a.powpumas)
        out.append(
            AreaDecl(
                area_id=a.area_id,
                label=a.label,
                kind="work",
                note=_cut(
                    f"2020 工作地 PUMA {codes}；占本城可支持工作地流入在职者的 {a.share:.1%}（ACS 2024 PUMS 加权，"
                    "按 powpuma_share_v1 划分）；只到县，不是某个就业中心",
                    280,
                ),
            )
        )
    return out


def _mode_rules(
    cfg: dict[str, Any], cbsa: str, fare: TransitFare | None, drive: DrivePrice | None
) -> list[CommuteModeRule]:
    numeraire, version = cfg["money_basis_id"], cfg["version"]
    rules = [
        CommuteModeRule(
            mode="walk_bike",
            note="步行或骑车：没有使用条件、不封顶；每次往返记 0（不计自行车、共享单车与鞋的费用）",
            source=SourceRecord(
                kind="derived",
                observed_on=OBSERVED_2024,
                geography_scope=f"CBSA {cbsa}",
                mapping_version=version,
                inputs=("ACS PUMS JWTRNS 09 Bicycle、10 Walked",),
                rule="步行骑车没有票价：每次往返的花费记 0",
            ),
        )
    ]
    if fare is not None:
        cite = (fare.monthly_pass or fare.fare).first
        cap = fare.monthly_pass.value if fare.monthly_pass is not None else None
        day = _dt.date.fromisoformat(cite["retrieved_on"])
        rules.append(
            CommuteModeRule(
                mode="transit",
                monthly_cap=None if cap is None else _money(cap, numeraire),
                note=_cut(
                    f"{fare.agency}：单程 {fare.fare.value}，每次往返两次单程 {fare.fare.value * 2}；"
                    + (f"月票 {cap} 当作每月封顶；" if cap is not None else "没有月票，不封顶；")
                    + fare.period_note,
                    400,
                ),
                source=SourceRecord(
                    kind="observed",
                    observed_on=day,
                    geography_scope=f"{fare.agency}的服务范围（用于 CBSA {cbsa} 全部片区）",
                    mapping_version=version,
                    provenance=Provenance(
                        source_url=cite["url"],
                        source_table=f"{cite['publisher']} 官方网页（SHA-256 {cite['raw_sha256'][:12]}）",
                        source_cell=cite["quote"],
                        retrieved_date=day,
                    ),
                ),
            )
        )
    if drive is not None:
        d = cfg["drive"]
        rules.append(
            CommuteModeRule(
                mode="drive",
                requires_function=d["requires_function"],
                note=_cut(
                    f"开车要有在用的用车权（{d['requires_function']}，一项按月付费的机会）；每次往返另付油费 = 往返分钟 / 60 × "
                    f"{drive.speed_mph} 英里每小时 ÷ {drive.mpg} 英里每加仑 × 汽油 {drive.gasoline.value} 美元每加仑；"
                    "不封顶；停车与过路费没有建模",
                    400,
                ),
                source=SourceRecord(
                    kind="estimated",
                    observed_on=OBSERVED_2024,
                    geography_scope=f"CBSA {cbsa}（汽油价是 BLS 都会区序列，油耗与速度全美统一）",
                    mapping_version=version,
                    inputs=(
                        f"BLS AP 2024 年汽油均价 {drive.gasoline.value}（{drive.gasoline.record_id}，{drive.gasoline.locator}）",
                        f"FHWA VM-1 2024 全部轻型车 {drive.mpg} mpg",
                        f"速度 {drive.speed_mph} mph（假设 {d['speed_mph']['assumption_id']}）",
                    ),
                    rule="油费 = 往返分钟 / 60 × 速度 ÷ mpg × 汽油价，四舍五入到分；开车要有在用的用车权（DECISIONS/0042）",
                ),
            )
        )
    return rules


def _leg(
    cfg: dict[str, Any],
    key: str,
    cbsa: str,
    cell: LegCell,
    fare: TransitFare | None,
    drive: DrivePrice | None,
    fingerprints: dict[str, str],
) -> CommuteLeg:
    factor = int(cfg["method"]["roundtrip_factor"])
    minutes = cell.oneway * factor
    if cell.mode == "transit":
        assert fare is not None
        cost = fare.fare.value * 2
        cost_text = f"花费 = 2 × 单程 {fare.fare.value}"
    elif cell.mode == "drive":
        assert drive is not None
        cost = drive.fuel(minutes)
        cost_text = (
            f"油费 {minutes}/60×{drive.speed_mph}÷{drive.mpg}×{drive.gasoline.value} = {cost}"
        )
    else:
        cost, cost_text = Decimal(0), "花费 0"
    return CommuteLeg(
        home_area=f"{key}.area.h.{cell.home}",
        work_area=cell.work,
        mode=cell.mode,  # type: ignore[arg-type]
        roundtrip_minutes=minutes,
        cost_per_roundtrip=_money(cost, cfg["money_basis_id"]),
        source=SourceRecord(
            kind="estimated",
            observed_on=OBSERVED_2024,
            geography_scope=f"CBSA {cbsa}",
            mapping_version=cfg["version"],
            inputs=(
                f"{_pums_input(cell.home[:2], fingerprints)}；{cell.level} 格 n={cell.n}（这条线 {cell.n_line}）",
            ),
            rule=f"单程 JWMNP 加权中位数 {cell.oneway} × {factor}；{cost_text}",
        ),
    )


def car_access_item(cfg: dict[str, Any], key: str, annual: Cited) -> LifeOpportunity:
    """用车权：一项按月付费的普通机会（``ongoing_service``，提供 ``car_access`` 功能），月费 = 年度费用 ÷ 12。"""
    d = cfg["drive"]
    monthly = (annual.value / 12).quantize(CENT, rounding=ROUND_HALF_UP)
    cite = annual.citations[-1]
    return LifeOpportunity(
        opportunity_id=f"{key}.{d['car_access']['opportunity_suffix']}",
        category="service",
        label="用车权（一辆小型轿车，按月）",
        unit="service-month",
        quality=_cut(
            f"按月付费的用车权：一辆小型轿车的折旧、贷款利息、保险、登记与保养（AAA 2024 年全美平均，年 {annual.value} "
            "美元 ÷ 12）；开车通勤每次另付油费",
            280,
        ),
        availability="available",
        price={
            "amount": _money(monthly, cfg["money_basis_id"]),
            "billing": "per_month",
            "per_unit": "service-month",
        },
        duration={"kind": "ongoing_service"},
        functions=(d["requires_function"],),
        resource={
            "basis": "per_service_month",
            "hours": 0,
            "burden_per_hour": 0,
            "quantity_scales": False,
            "note": "不占时间：开车的时间记在每月的通勤里",
        },
        source=SourceRecord(
            kind="derived",
            observed_on=_dt.date.fromisoformat(cite["retrieved_on"]),
            geography_scope="美国全国平均（不分城市）",
            mapping_version=cfg["version"],
            inputs=(
                f"{cite['publisher']} 2024 Your Driving Costs：小型轿车年度总费用 {annual.value}（摘句见 inputs.drive_costs）",
                _cut(f"{cite['url']}@{cite['raw_sha256'][:12]}", 200),
            ),
            rule=f"{d['car_access']['derivation']}（假设 {d['car_access']['assumption_id']}；年度费用已含每年 15,000 英里的油费）",
        ),
    )


def _cited_json(c: Cited | None) -> dict[str, Any] | None:
    return None if c is None else {"value": str(c.value), "citations": list(c.citations)}


def build_city(
    cfg: dict[str, Any],
    city: dict[str, Any],
    raw: RawInputs,
    intake: IntakeFacts,
    costs: CostBuild,
) -> CityResult:
    """一座城的片区、通勤关系表、用车权与诊断（普通 JSON，契约对象都已校验）。"""
    key, cbsa = city["key"], city["cbsa"]
    method, numeraire = cfg["method"], cfg["money_basis_id"]
    mg = raw.geography[cbsa]
    metro_counties = {c.fips for c in mg.counties}
    work_states = set(city["work_states"])
    homes, home_excluded = select_home_pumas(city, mg)
    home_codes = [p.code for p in homes]
    tiers = {h: t for h, t in intake.tiers.get(cbsa, {}).items() if h in home_codes}
    work_areas, work_diag = define_work_areas(
        key,
        raw.commuters,
        raw.lookup,
        metro_counties,
        work_states,
        float(method["work_areas"]["own_area_min_share"]),
    )
    area_of = {f"{k[0]}:{k[1]}": a.area_id for a in work_areas for k in a.powpumas}
    res = resident_commuters(raw.commuters, home_codes, area_of, method)
    cells, leg_diag = estimate_legs(
        res[res["work"].notna() & res["mode"].notna()],
        home_codes,
        [a.area_id for a in work_areas],
        tiers,
        method,
    )
    typical = typical_oneway(res, home_codes, tiers, int(method["min_unweighted_n"]))
    # 居民平时的分钟与 intake 的 commute_by_residence_puma@1 是同一筛选与同一估计：逐个对照
    intake_med = intake.medians.get(cbsa, {})
    differ = [h for h, (m, lv, _) in typical.items() if lv == "puma" and intake_med.get(h, m) != m]

    fare = costs.transit.get(key)
    gas = intake.gasoline.get(cbsa)
    drive = (
        DrivePrice(_dec(cfg["drive"]["speed_mph"]["value"]), costs.mpg.value, gas)
        if costs.mpg is not None and costs.car_annual is not None and gas is not None
        else None
    )
    blocked: dict[str, list[str]] = {}
    if fare is None:
        blocked["transit"] = costs.transit_blocked.get(key, ["没有票价"])
    if drive is None:
        blocked["drive"] = list(costs.drive_blocked) + (
            [] if gas is not None else [f"intake 里没有 CBSA {cbsa} 的 2024 年汽油均价"]
        )
    areas = _area_decls(cfg, key, cbsa, homes, tiers, typical, work_areas, raw.fingerprints)
    modes = _mode_rules(cfg, cbsa, fare, drive)
    usable = {m.mode for m in modes}
    legs = [
        _leg(cfg, key, cbsa, c, fare, drive, raw.fingerprints) for c in cells if c.mode in usable
    ]
    relation = CommuteRelation(
        modes=tuple(modes),
        legs=tuple(legs),
        note=_cut(
            "真实资料（E3B）：居住片区是 2020 PUMA，就业片区是按县划的工作地 PUMA；往返分钟是 ACS 2024 PUMS 里实际用这种方式的"
            "通勤者单程分钟的加权中位数 × 2，样本不足按格回退（每格记下层级与条数）；公交按主要机构 2024 年的官方票价，开车油费按"
            "分钟、速度、油耗与本都会区汽油均价估计；停车、过路费与按距离计价的票价没有建模。",
            400,
        ),
    )
    car = None
    if drive is not None:
        assert costs.car_annual is not None
        car = car_access_item(cfg, key, costs.car_annual)

    not_modeled = res[res["work"].notna() & res["mode"].isna()]
    diag = {
        "home_areas": {
            "included": len(homes),
            "excluded_partial_in_metro": home_excluded["partial_in_metro"],
            "excluded_outside_tax_scope": home_excluded["outside_tax_scope"],
            "tiers": dict(sorted(Counter(tiers.get(h, "未分层") for h in home_codes).items())),
        },
        "work_areas": work_diag,
        "resident_commuters": {
            "unweighted": len(res),
            "weight": int(res["PWGTP"].sum()),
            "to_work_areas_weight": int(res.loc[res["work"].notna(), "PWGTP"].sum()),
            "outside_work_areas_weight_by_reason": _classify_outside(
                res[res["work"].isna()], raw.lookup, metro_counties, work_states
            ),
            "not_modeled_modes_weight": {
                method["jwtrns_not_modeled"].get(str(k), str(k)): int(v)
                for k, v in not_modeled.groupby("JWTRNS")["PWGTP"].sum().items()
            },
        },
        "legs": {
            **leg_diag,
            "emitted": len(legs),
            "emitted_by_mode": dict(Counter(g.mode for g in legs)),
        },
        "typical": {
            "by_level": dict(Counter(lv for _, lv, _ in typical.values())),
            "unknown": [h for h in home_codes if h not in typical],
            "differs_from_intake_commute_table": differ,
        },
    }
    states = {p.state for p in homes}
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "version": cfg["version"],
        "city": {
            "cbsa": cbsa,
            "key": key,
            "label_zh": city["label_zh"],
            "scope": city["scope_note"],
        },
        "money_basis_id": numeraire,
        "status": "complete" if not blocked else "partial",
        "modes": [m.mode for m in modes],
        "blocked": blocked,
        "areas": [a.model_dump(mode="json", exclude_none=True) for a in areas],
        "commute": relation.model_dump(mode="json", exclude_none=True),
        "car_access": None if car is None else car.model_dump(mode="json", exclude_none=True),
        # 住房按 PUMA 生成时对照用：哪些 PUMA 有居住片区，哪些因税的支持范围或只部分在都会区内而没有
        "home_pumas": {
            "included": home_codes,
            "excluded_outside_tax_scope": [x["puma"] for x in home_excluded["outside_tax_scope"]],
            "excluded_partial_in_metro": [x["puma"] for x in home_excluded["partial_in_metro"]],
        },
        "work_area_job_shares": work_area_job_shares(
            raw.commuters, area_of, [a.area_id for a in work_areas]
        ),
        "transit_fare": None
        if fare is None
        else {
            "agency": fare.agency,
            "fare": _cited_json(fare.fare),
            "monthly_pass": _cited_json(fare.monthly_pass),
            "period_note": fare.period_note,
        },
        "assumptions": _assumptions(cfg),
        "not_modeled": list(cfg["not_modeled"])
        + list(fare.not_modeled if fare else city["transit"].get("not_modeled", ())),
        "inputs": {
            "intake_build_id": intake.build_id,
            "raw_sha256": {
                n: s
                for n, s in sorted(raw.fingerprints.items())
                if not n.startswith("pums_p:") or n.split(":")[1] in states
            },
            "gasoline": None
            if gas is None
            else {
                "value": str(gas.value),
                "record_id": gas.record_id,
                "raw_sha256": gas.raw_sha256,
            },
            "drive_costs": {
                "mpg": _cited_json(costs.mpg),
                "car_annual": _cited_json(costs.car_annual),
                "speed_mph": str(cfg["drive"]["speed_mph"]["value"]),
            },
        },
        "diagnostics": diag,
    }
    payload["content_identity"] = content_identity(payload)
    return CityResult(key=key, payload=payload)


def _classify_outside(
    df: pd.DataFrame, lookup: PowLookup, metro_counties: set[str], work_states: set[str]
) -> dict[str, int]:
    """居民通勤者里去向不在就业片区的：州不在支持范围、在都会区外、部分在都会区内、代码表里没有。"""
    out: Counter[str] = Counter()
    for (st, pw), w in df.groupby([df["POWSP"].str[-2:], df["POWPUMA"]])["PWGTP"].sum().items():
        cs = lookup.counties.get((st, pw))
        if st not in work_states:
            why = "work_state_not_supported"
        elif cs is None:
            why = "powpuma_not_in_codelist"
        elif not cs & metro_counties:
            why = "outside_metro"
        elif not cs <= metro_counties:
            why = "partial_in_metro"
        else:
            why = "supported_but_no_area"
        out[why] += int(w)
    return dict(sorted(out.items()))


def _assumptions(cfg: dict[str, Any]) -> list[dict[str, str]]:
    d, m = cfg["drive"], cfg["method"]
    return [
        {
            "id": "commute_roundtrip_is_twice_oneway",
            "statement": "往返通勤分钟 = 单程 × 2（去回对称；intake 已登记）",
        },
        {
            "id": "commute_cell_fallback_v1",
            "statement": (
                f"每格至少 {m['min_unweighted_n']} 条未加权记录；不够依次回退到同一通勤层的居民合并格、本城全部居住片区合并格；"
                f"合并格只给 {'、'.join(m['pooled_fallback_modes'])}，且这条线至少有 {m['min_line_evidence']} 条用这种方式的记录"
            ),
        },
        {
            "id": "work_area_powpuma_share_v1",
            "statement": f"可支持的工作地 PUMA 中流入在职者份额不低于 {m['work_areas']['own_area_min_share']} 的各成一个就业片区，其余合并",
        },
        {
            "id": "commute_minutes_observed_users",
            "statement": "每格的分钟是实际选了这种方式的人的分钟（自选择），不是任何人改用这种方式的分钟",
        },
        {
            "id": "transit_fare_in_effect_2024",
            "statement": "引用网页写明的票价在 2024 年全年有效（各城依据见 period_note）；没有逐日核对年内调价",
        },
        {
            "id": "transit_monthly_pass_as_cap",
            "statement": "主要机构的月票（或 30 天票）价当作公交每月花费的封顶；没有月票的城市不封顶",
        },
        {"id": d["speed_mph"]["assumption_id"], "statement": d["speed_mph"]["statement"]},
        {"id": d["car_access"]["assumption_id"], "statement": d["car_access"]["statement"]},
    ]


# --------------------------------------------------------------------------- 全部城市与冻结


def compile_all(
    cfg: dict[str, Any], raw: RawInputs, intake: IntakeFacts, costs: CostBuild
) -> list[CityResult]:
    if intake.build_id != cfg["intake"]["build_id"]:
        raise RawChangedError(
            f"intake 冻结库的构建身份 {intake.build_id[:16]}… 不是配置里的 {cfg['intake']['build_id'][:16]}…"
        )
    return [build_city(cfg, city, raw, intake, costs) for city in cfg["cities"]]


def payload_text(payload: dict[str, Any]) -> str:
    """一城的冻结 JSON：顶层每个键一行，片区与往返格每条一行（文件小、差异按行看得清）；``json.loads`` 读回的内容不变。"""

    def one(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    def rows(items: list[Any]) -> str:
        return "[\n" + ",\n".join(one(x) for x in items) + "\n]"

    parts = []
    for k, v in payload.items():
        if k == "areas":
            text = rows(v)
        elif k == "commute":
            head = {x: y for x, y in v.items() if x != "legs"}
            text = one(head)[:-1] + ',"legs":' + rows(v["legs"]) + "}"
        else:
            text = one(v)
        parts.append(f"{one(k)}:{text}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def write_frozen(results: list[CityResult], out_dir: Path) -> dict[str, Any]:
    """每城一份 JSON（``<key>.json``）与一份索引（``index.json``：每城的内容身份、状态与可用方式）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"schema": SCHEMA, "cities": {}}
    for r in results:
        (out_dir / f"{r.key}.json").write_text(
            payload_text(r.payload), encoding="utf-8", newline="\n"
        )
        p = r.payload
        index["cities"][r.key] = {
            "cbsa": p["city"]["cbsa"],
            "content_identity": p["content_identity"],
            "status": p["status"],
            "modes": p["modes"],
            "blocked": sorted(p["blocked"]),
            "home_areas": p["diagnostics"]["home_areas"]["included"],
            "work_areas": len(p["diagnostics"]["work_areas"]["areas"]),
            "legs": p["diagnostics"]["legs"]["emitted"],
        }
    index["content_identity"] = content_identity(index["cities"])
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return index


def frozen_payload_problems(payload: dict[str, Any]) -> list[str]:
    """冻结文件自检：内容身份对得上，片区、关系表与用车权都能按契约读回，关系表的片区都声明过、种类对。"""
    problems: list[str] = []
    body = {k: v for k, v in payload.items() if k != "content_identity"}
    if content_identity(body) != payload.get("content_identity"):
        problems.append("内容身份对不上")
    areas = [AreaDecl.model_validate(a) for a in payload["areas"]]
    kinds = {a.area_id: a.kind for a in areas}
    problems += [
        f"{a.area_id}：居住片区没有居民平时的往返分钟"
        for a in areas
        if a.kind == "home" and a.typical_roundtrip_minutes is None
    ]
    rel = CommuteRelation.model_validate(payload["commute"])
    for g in rel.legs:
        if kinds.get(g.home_area) != "home" or kinds.get(g.work_area) != "work":
            problems.append(f"{g.home_area}→{g.work_area}：片区没有声明或种类不对")
        if g.cost_per_roundtrip.numeraire != payload["money_basis_id"]:
            problems.append(f"{g.home_area}→{g.work_area}：花费口径不对")
    if payload.get("car_access") is not None:
        LifeOpportunity.model_validate(payload["car_access"])
    return problems
