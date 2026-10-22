"""地理：OMB 2023 年 CBSA 划定（都会区 → 县 → 都会分区）与 2020 年 PUMA 归属。

* 都会区的成员县、都会分区（Metropolitan Division）与中心 / 外围县都取自划定文件，不按城市名猜。
* PUMA 归属用官方的 2020 普查区 → PUMA 对应文件：一个 PUMA 的普查区全部落在都会区的县里，记为“完整在内”；只有一部分落在里面，
  记为“部分在内”并保存比例（按普查区个数，不是人口——没有人口时不假装有）。部分在内的 PUMA 的样本只在明确允许时使用，
  并在覆盖报告里单列。
* PUMA 名称来自 ACS 1 年期表格摘要文件的地理对照（Census 数据 API 现在要密钥，用公开批量文件代替）。
"""

from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction

from product.realdata.config import Metro
from product.realdata.fetch import Request

DELINEATION_URL = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/"
    "delineation-files/list1_2023.xlsx"
)
TRACT_PUMA_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt"
)
GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_cbsa_national.zip"


ACS_SF_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF"


def delineation_request() -> Request:
    return Request("census_cbsa_2023", DELINEATION_URL, expect="xlsx")


def tract_puma_request() -> Request:
    return Request("census_tract_puma_2020", TRACT_PUMA_URL, expect="text")


def gazetteer_request() -> Request:
    return Request("census_gazetteer_cbsa_2024", GAZETTEER_URL, expect="zip")


def acs_geos_request(year: int) -> Request:
    """ACS 1 年期表格摘要文件的地理对照（含 PUMA 与 CBSA 的名称）。Census 数据 API 现在要密钥，这里用公开的批量文件。"""
    return Request(
        "census_acs1_tables_2024",
        f"{ACS_SF_BASE.format(year=year)}/documentation/Geos{year}1YR.txt",
        expect="text",
        timeout=300,
    )


def parse_acs_geos(content: bytes) -> dict[str, dict[str, str]]:
    """Geos 文件 → GEO_ID → {sumlevel, state, puma, cbsa, name}。只保留 PUMA（795）与 CBSA（310）两级。"""
    text = content.decode("latin-1")
    lines = text.splitlines()
    header = lines[0].split("|")
    idx = {h: i for i, h in enumerate(header)}
    out: dict[str, dict[str, str]] = {}
    for ln in lines[1:]:
        p = ln.split("|")
        if len(p) < len(header):
            continue
        sl = p[idx["SUMLEVEL"]]
        if sl not in ("795", "310") or p[idx["COMPONENT"]] != "00":
            continue
        out[p[idx["GEO_ID"]]] = {
            "sumlevel": sl,
            "state": p[idx["STATE"]],
            "puma": p[idx["PUMA5"]],
            "cbsa": p[idx["CBSA"]],
            "name": p[idx["NAME"]],
        }
    return out


def puma_names_from_geos(geos: dict[str, dict[str, str]]) -> dict[tuple[str, str], str]:
    return {(g["state"], g["puma"]): g["name"] for g in geos.values() if g["sumlevel"] == "795"}


@dataclass(frozen=True)
class County:
    state: str
    county: str
    name: str
    state_name: str
    central: bool
    division: str | None
    division_title: str | None

    @property
    def fips(self) -> str:
        return self.state + self.county


@dataclass(frozen=True)
class PumaMembership:
    state: str
    puma: str
    tracts_in_metro: int
    tracts_total: int
    counties: tuple[str, ...]
    name: str | None = None

    @property
    def share(self) -> Fraction:
        return Fraction(self.tracts_in_metro, self.tracts_total)

    @property
    def full(self) -> bool:
        return self.tracts_in_metro == self.tracts_total

    @property
    def code(self) -> str:
        return self.state + self.puma


@dataclass
class MetroGeo:
    cbsa: str
    title: str
    counties: list[County]
    pumas: list[PumaMembership] = field(default_factory=list)
    land_area_sqmi: str | None = None

    @property
    def states(self) -> list[str]:
        return sorted({c.state for c in self.counties})

    @property
    def divisions(self) -> list[tuple[str, str]]:
        seen = {(c.division, c.division_title) for c in self.counties if c.division}
        return sorted(seen)  # type: ignore[arg-type]

    def full_pumas(self, state: str) -> list[str]:
        return sorted(p.puma for p in self.pumas if p.state == state and p.full)

    def puma(self, state: str, puma: str) -> PumaMembership | None:
        return next((p for p in self.pumas if p.state == state and p.puma == puma), None)


def parse_delineation(content: bytes) -> dict[str, MetroGeo]:
    """划定文件 → 每个 CBSA 的成员县与都会分区。"""
    import pandas as pd

    df = pd.read_excel(io.BytesIO(content), header=2, dtype=str)
    df = df[df["CBSA Code"].notna() & df["FIPS County Code"].notna()]
    out: dict[str, MetroGeo] = {}
    for code, g in df.groupby("CBSA Code"):
        counties = [
            County(
                state=r["FIPS State Code"],
                county=r["FIPS County Code"],
                name=r["County/County Equivalent"],
                state_name=r["State Name"],
                central=r["Central/Outlying County"] == "Central",
                division=r["Metropolitan Division Code"]
                if isinstance(r["Metropolitan Division Code"], str)
                else None,
                division_title=r["Metropolitan Division Title"]
                if isinstance(r["Metropolitan Division Title"], str)
                else None,
            )
            for _, r in g.iterrows()
        ]
        out[str(code)] = MetroGeo(cbsa=str(code), title=g["CBSA Title"].iloc[0], counties=counties)
    return out


def parse_tract_puma(content: bytes) -> dict[tuple[str, str], dict[str, int]]:
    """普查区 → PUMA 对应文件 → 每个 (州, PUMA) 的成员县及其普查区个数。"""
    text = content.decode("utf-8-sig")
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split(",")]
    i_st, i_co, i_puma = header.index("STATEFP"), header.index("COUNTYFP"), header.index("PUMA5CE")
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ln in lines[1:]:
        if not ln.strip():
            continue
        p = ln.split(",")
        out[(p[i_st], p[i_puma])][p[i_st] + p[i_co]] += 1
    return {k: dict(v) for k, v in out.items()}


def attach_pumas(geo: MetroGeo, tract_puma: dict[tuple[str, str], dict[str, int]]) -> MetroGeo:
    metro_fips = {c.fips for c in geo.counties}
    members: list[PumaMembership] = []
    for (state, puma), counties in sorted(tract_puma.items()):
        inside = sum(n for fips, n in counties.items() if fips in metro_fips)
        if inside == 0:
            continue
        members.append(
            PumaMembership(
                state=state,
                puma=puma,
                tracts_in_metro=inside,
                tracts_total=sum(counties.values()),
                counties=tuple(sorted(counties)),
            )
        )
    geo.pumas = members
    return geo


def name_pumas(geo: MetroGeo, names: dict[tuple[str, str], str]) -> MetroGeo:
    geo.pumas = [
        PumaMembership(
            state=p.state,
            puma=p.puma,
            tracts_in_metro=p.tracts_in_metro,
            tracts_total=p.tracts_total,
            counties=p.counties,
            name=names.get((p.state, p.puma)),
        )
        for p in geo.pumas
    ]
    return geo


def parse_gazetteer(content: bytes) -> dict[str, str]:
    """Gazetteer CBSA 文件 → 陆地面积（平方英里，原样文字）。"""
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if n.endswith(".txt"))
    text = z.read(name).decode("utf-8-sig")
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    i_geoid, i_land = header.index("GEOID"), header.index("ALAND_SQMI")
    out = {}
    for ln in lines[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if len(p) > i_land:
            out[p[i_geoid]] = p[i_land]
    return out


def metro_geography(
    metros: tuple[Metro, ...],
    delineation: bytes,
    tract_puma: bytes,
    puma_names: dict[tuple[str, str], str],
    gazetteer: bytes | None,
) -> dict[str, MetroGeo]:
    all_geo = parse_delineation(delineation)
    tp = parse_tract_puma(tract_puma)
    land = parse_gazetteer(gazetteer) if gazetteer else {}
    out: dict[str, MetroGeo] = {}
    for m in metros:
        if m.cbsa not in all_geo:
            raise KeyError(f"CBSA {m.cbsa} 不在 2023 年划定文件里")
        geo = attach_pumas(all_geo[m.cbsa], tp)
        geo = name_pumas(geo, puma_names)
        geo.land_area_sqmi = land.get(m.cbsa)
        out[m.cbsa] = geo
    return out


def geo_summary(geo: MetroGeo) -> dict[str, object]:
    """给报告与冻结产物用的摘要（普通 JSON）。"""
    return {
        "cbsa": geo.cbsa,
        "title": geo.title,
        "states": geo.states,
        "counties": [
            {
                "fips": c.fips,
                "name": c.name,
                "state": c.state_name,
                "central": c.central,
                "division": c.division,
            }
            for c in geo.counties
        ],
        "divisions": [{"code": d[0], "title": d[1]} for d in geo.divisions],
        "pumas": [
            {
                "code": p.code,
                "full": p.full,
                "name": p.name,
                "share_tracts": f"{p.tracts_in_metro}/{p.tracts_total}",
            }
            for p in geo.pumas
        ],
        "pumas_full": sum(1 for p in geo.pumas if p.full),
        "pumas_partial": [
            {"puma": p.code, "share_tracts": f"{p.tracts_in_metro}/{p.tracts_total}"}
            for p in geo.pumas
            if not p.full
        ],
        "land_area_sqmi": geo.land_area_sqmi,
    }
