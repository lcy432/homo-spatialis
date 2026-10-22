"""每个来源的请求清单：获取命令与离线构建用**同一份**请求定义，所以缓存键一定对得上。

只有这里写 URL 与查询。Census 的数据 API 现在要密钥（2026-10 实测：返回“Missing Key”页面），所以 ACS 用公开的批量文件
（PUMS 州文件、表格摘要文件）；HUD 公平市场租金文件返回反爬挑战（HTTP 202、空内容），照实记录、不绕过；FBI 的 API 要密钥，
不请求。
"""

from __future__ import annotations

from product.realdata.config import BuildConfig, Metro
from product.realdata.fetch import Request

STATE_ABBR = {
    "06": "ca",
    "17": "il",
    "18": "in",
    "34": "nj",
    "36": "ny",
    "48": "tx",
    "53": "wa",
}
PUMS_BASE = "https://www2.census.gov/programs-surveys/acs/data/pums/{year}/1-Year"
ACS_SF = "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF"
ACS_TABLES = ("b25031", "b25064", "b08303", "b08301", "b01003", "b19013")
CE_BASE = "https://www.bls.gov/cex/tables/geographic/mean"
ENERGYSTAR = {
    "clothes_washer": "bghd-e2wd",
    "clothes_dryer": "t9u7-4d2j",
    "dishwasher": "q8py-6w3f",
    "combo_washer_dryer": "9jai-gs6t",
}
OVERPASS = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"


def pums_request(year: int, state: str, record: str) -> Request:
    abbr = STATE_ABBR[state]
    return Request(
        "census_acs1_pums_2024",
        f"{PUMS_BASE.format(year=year)}/csv_{record}{abbr}.zip",
        expect="zip",
        timeout=900,
    )


def pums_dictionary_request(year: int) -> Request:
    return Request(
        "census_acs1_pums_2024",
        f"https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/PUMS_Data_Dictionary_{year}.csv",
        expect="text",
    )


def acs_table_request(year: int, table: str) -> Request:
    return Request(
        "census_acs1_tables_2024",
        f"{ACS_SF.format(year=year)}/data/1YRData/acsdt1y{year}-{table}.dat",
        expect="text",
        timeout=300,
    )


def oews_request() -> Request:
    return Request(
        "bls_oews_may2024",
        "https://www.bls.gov/oes/special-requests/oesm24ma.zip",
        expect="zip",
        timeout=600,
    )


def ep_education_request() -> Request:
    return Request(
        "bls_ep_education", "https://www.bls.gov/emp/ind-occ-matrix/education.xlsx", expect="xlsx"
    )


def cpi_request() -> Request:
    return Request(
        "bls_cpi_u",
        "https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems",
        expect="text",
        timeout=300,
    )


def ap_requests() -> dict[str, Request]:
    base = "https://download.bls.gov/pub/time.series/ap/"
    return {
        name: Request("bls_ap", base + name, expect="text", timeout=300)
        for name in ("ap.series", "ap.item", "ap.area", "ap.data.0.Current")
    }


def ce_request(region_file: str) -> Request:
    return Request(
        "bls_ce_msa_2023_2024",
        f"{CE_BASE}/cu-msa-{region_file}-2-year-average-2023-2024.xlsx",
        expect="xlsx",
    )


def cip_soc_request() -> Request:
    return Request(
        "nces_cip_soc_2020",
        "https://nces.ed.gov/ipeds/cipcode/Files/CIP2020_SOC2018_Crosswalk.xlsx",
        expect="xlsx",
    )


def ipeds_request(name: str) -> Request:
    return Request(
        "ipeds_2023",
        f"https://nces.ed.gov/ipeds/datacenter/data/{name}.zip",
        expect="zip",
        timeout=300,
    )


def aqi_request(year: int = 2025) -> Request:
    return Request(
        "epa_aqi_cbsa_2025",
        f"https://aqs.epa.gov/aqsweb/airdata/annual_aqi_by_cbsa_{year}.zip",
        expect="zip",
    )


def energystar_request(category: str) -> Request:
    return Request(
        "energystar_products",
        f"https://data.energystar.gov/resource/{ENERGYSTAR[category]}.json",
        params=(("$limit", "50000"), ("$order", ":id")),
        expect="json",
        timeout=300,
    )


def chicago_activities_request() -> Request:
    return Request(
        "chicago_park_activities",
        "https://data.cityofchicago.org/resource/tn7v-6rnw.json",
        params=(("$limit", "50000"), ("$order", "activity_id")),
        expect="json",
        timeout=300,
    )


def bea_rpp_request() -> Request:
    return Request("bea_rpp_msa", "https://apps.bea.gov/regional/zip/MARPP.zip", expect="zip")


def hud_fmr_request() -> Request:
    return Request(
        "hud_fmr_fy2026",
        "https://www.huduser.gov/portal/datasets/fmr/fmr2026/FY26_FMRs.xlsx",
        expect="xlsx",
    )


FOOD_QUERY = (
    '[out:json][timeout:300];area["wikidata"="{wd}"]->.a;'
    'nwr["amenity"~"^(restaurant|fast_food|cafe)$"](area.a);out center tags;'
)
LEISURE_QUERY = (
    '[out:json][timeout:300];area["wikidata"="{wd}"]->.a;('
    'nwr["leisure"~"^(fitness_centre|sports_centre|swimming_pool|ice_rink|climbing|bowling_alley|golf_course|dance)$"](area.a);'
    'nwr["amenity"~"^(cinema|theatre|library|community_centre|arts_centre)$"](area.a);'
    'nwr["tourism"~"^(museum|gallery|zoo|aquarium)$"](area.a);'
    ");out center tags;"
)


SPLIT_FOOD_QUERY = (
    '[out:json][timeout:300];area["wikidata"="{wd}"]->.a;'
    'nwr["amenity"="{amenity}"](area.a);out center tags;'
)
FOOD_AMENITIES = ("restaurant", "fast_food", "cafe")


def osm_request(metro: Metro, kind: str) -> Request:
    template = FOOD_QUERY if kind == "food" else LEISURE_QUERY
    return Request(
        "osm_overpass",
        OVERPASS,
        form=(("data", template.format(wd=metro.principal_city.wikidata)),),
        expect="json",
        timeout=360,
    )


def osm_split_food_requests(metro: Metro) -> dict[str, Request]:
    """合并查询在镜像上超时的城市：餐馆、快餐、咖啡分三次查（内容与合并查询相同，只是分开取）。"""
    return {
        amenity: Request(
            "osm_overpass",
            OVERPASS,
            form=(
                (
                    "data",
                    SPLIT_FOOD_QUERY.format(wd=metro.principal_city.wikidata, amenity=amenity),
                ),
            ),
            expect="json",
            timeout=360,
        )
        for amenity in FOOD_AMENITIES
    }


def all_requests(config: BuildConfig, states: list[str]) -> dict[str, Request]:
    """构建要用的全部请求（按名字）。``states`` 是验证都会区涉及的州（来自划定文件）。"""
    from product.realdata import geo

    year = config.pums.year
    reqs: dict[str, Request] = {
        "cbsa_delineation": geo.delineation_request(),
        "tract_puma": geo.tract_puma_request(),
        "gazetteer": geo.gazetteer_request(),
        "acs_geos": geo.acs_geos_request(year),
        "pums_dictionary": pums_dictionary_request(year),
        "oews": oews_request(),
        "ep_education": ep_education_request(),
        "cpi": cpi_request(),
        "cip_soc": cip_soc_request(),
        "ipeds_hd": ipeds_request("HD2023"),
        "ipeds_ic_py": ipeds_request("IC2023_PY"),
        "aqi": aqi_request(2025),
        "aqi_2024": aqi_request(2024),
        "chicago_activities": chicago_activities_request(),
        "hud_fmr": hud_fmr_request(),
        "bea_rpp": bea_rpp_request(),
    }
    for name, req in ap_requests().items():
        reqs[f"ap:{name}"] = req
    for st in states:
        reqs[f"pums_h:{st}"] = pums_request(year, st, "h")
        reqs[f"pums_p:{st}"] = pums_request(year, st, "p")
    for t in ACS_TABLES:
        reqs[f"acs_table:{t}"] = acs_table_request(year, t)
    for region in sorted({m.ce_region_file for m in config.metros}):
        reqs[f"ce:{region}"] = ce_request(region)
    for cat in ENERGYSTAR:
        reqs[f"energystar:{cat}"] = energystar_request(cat)
    split = set(config.mapping.get("osm", {}).get("split_food_queries", []))
    for m in config.metros:
        reqs[f"osm_food:{m.cbsa}"] = osm_request(m, "food")
        reqs[f"osm_leisure:{m.cbsa}"] = osm_request(m, "leisure")
        if m.cbsa in split:
            for amenity, req in osm_split_food_requests(m).items():
                reqs[f"osm_food_split:{m.cbsa}:{amenity}"] = req
    return reqs
