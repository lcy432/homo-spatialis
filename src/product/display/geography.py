"""地理显示清单的生成：``configs/product/display/geography/<pack_id>.json``，即 ``GeographyDisplayBundle`` 去掉服务加的
``display_schema_version`` / ``resource_version`` / ``pack_fingerprint`` 三个字段。由 ``scripts/product_geography.py build`` 生成，
不要手改（``check`` 逐字核对）。

* 真实城市的锚点是 Census 2024 Gazetteer CBSA 文件里的都会区内部点（``INTPTLAT`` / ``INTPTLONG``，``metro_internal_point``）。
  只读本机缓存，并要求它就是 E3B 资料包登记、AB 与 C 核对过的那一份（SHA-256）。它是都会区的代表点，不是住处、主城市中心或
  都会区边界。
* 基准：Gazetteer 的记录格式没有另写基准。2026-10-24 核对了官方 TIGER/Line 2024 CBSA 文件（``tl_2024_us_cbsa.zip``，SHA-256 见
  ``TIGER_CBSA_SHA256``）：六城的 ``INTPTLAT`` / ``INTPTLON`` 属性（七位小数）四舍五入到六位就是 Gazetteer 的值，那份文件的
  ``.prj`` 是 ``GCS_North_American_1983``，TIGER/Line 2024 技术文档 3.3.3 节也写明是 NAD83——所以原始基准是 NAD83。发布为
  EPSG:4326 用 EPSG 登记的 1188 号转换（三个平移量都是 0，精度 4 米），数值原样不变。
* 边界版本：2024 Gazetteer 的全部 CBSA 与资料包所用的 2023 年 7 月划定文件编号、名称逐条一致才生成。
* 支持范围（居住、工作）按资料包的税务范围人工写定中英两种文字；居住片区个数与就业片区的县名从资料包的生活输入读，不手抄。
  清单不授予运行资格：能选哪些城市由能力清单与服务规则决定。
* 合成城市没有现实位置：``not_applicable``、锚点为空。
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from common.product_contracts import GeographyDisplayBundle
from product.display.catalog import DISPLAY_SCHEMA_VERSION, RESOURCE_PREFIX, DisplayResourceError
from product.display.text import has_cjk
from product.realdata.fetch import DEFAULT_CACHE, read_cached
from product.realdata.geo import (
    DELINEATION_URL,
    GAZETTEER_URL,
    delineation_request,
    gazetteer_request,
    parse_delineation,
)

if TYPE_CHECKING:
    from product.pack import CityDecl, DataPack

#: E3B 资料包登记的两份原始文件（``data/frozen/intake/us-v1/manifest.json``）；Gazetteer 这份的六城记录 AB 与 C 都核对过
GAZETTEER_SHA256 = "0ec21c5d83982467903f1500fbbae87311edc81f09bed8ec1bbded80bce7eec3"
DELINEATION_SHA256 = "952c4b1e78acbb54e6ec9412434b7602fedacbf021736351a63c181bdb753629"

GAZETTEER = "census.gazetteer.2024.cbsa"
GAZETTEER_LAYOUT = "census.gazetteer.2024.layout"
TIGER_DATUM = "census.tiger.2024.datum"
TIGER_CBSA = "census.tiger.2024.cbsa"
#: 官方 TIGER/Line 2024 CBSA 文件（2026-10-24 下载核对，不放进仓库）：六城内部点与 Gazetteer 一致，``.prj`` 是 NAD83
TIGER_CBSA_SHA256 = "ea2f40fcd9ecf46663da798e2e524766d37d6a802d760cd50c6629cfb861e54e"
DELINEATION = "census.cbsa.delineation.2023-07"
EPSG_1188 = "epsg.1188"
#: 锚点的依据：坐标本身、原始基准（同一内部点的 TIGER/Line 属性与它的坐标系、技术文档）、登记的转换
ANCHOR_SOURCES = (GAZETTEER, TIGER_CBSA, TIGER_DATUM, EPSG_1188)

REAL_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "source_ref": GAZETTEER,
        "publisher": "U.S. Census Bureau",
        "dataset": "2024 Gazetteer Files: Core Based Statistical Areas (2024_Gaz_cbsa_national.zip); "
        "fields NAME, CBSA_TYPE, INTPTLAT, INTPTLONG",
        "version": "2024",
        "public_url": GAZETTEER_URL,
        "content_sha256": GAZETTEER_SHA256,
    },
    {
        "source_ref": GAZETTEER_LAYOUT,
        "publisher": "U.S. Census Bureau",
        "dataset": "2024 Gazetteer Files record layouts: INTPTLAT / INTPTLONG are the latitude and "
        "longitude of the internal point in decimal degrees (no datum restated)",
        "version": "2024",
        "public_url": "https://www.census.gov/programs-surveys/geography/technical-documentation/"
        "records-layout/gaz-record-layouts.2024.html",
        "content_sha256": None,
    },
    {
        "source_ref": TIGER_CBSA,
        "publisher": "U.S. Census Bureau",
        "dataset": "2024 TIGER/Line Shapefiles: Core Based Statistical Areas (tl_2024_us_cbsa.zip); "
        "INTPTLAT / INTPTLON of the six CBSAs equal the Gazetteer values (rounded to 6 decimals); "
        ".prj GCS_North_American_1983",
        "version": "2024",
        "public_url": "https://www2.census.gov/geo/tiger/TIGER2024/CBSA/tl_2024_us_cbsa.zip",
        "content_sha256": TIGER_CBSA_SHA256,
    },
    {
        "source_ref": TIGER_DATUM,
        "publisher": "U.S. Census Bureau",
        "dataset": "2024 TIGER/Line Shapefiles Technical Documentation, chapter 3, section 3.3.3 Datum: "
        "all Census Bureau generated TIGER/Line Shapefiles are in GCS NAD83",
        "version": "2024",
        "public_url": "https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2024/"
        "TGRSHP2024_TechDoc_Ch3.pdf",
        "content_sha256": None,
    },
    {
        "source_ref": DELINEATION,
        "publisher": "U.S. Census Bureau",
        "dataset": "List 1: Core Based Statistical Areas, Metropolitan Divisions and Combined "
        "Statistical Areas, July 2023 (list1_2023.xlsx; based on OMB Bulletin No. 23-01)",
        "version": "July 2023",
        "public_url": DELINEATION_URL,
        "content_sha256": DELINEATION_SHA256,
    },
    {
        "source_ref": EPSG_1188,
        "publisher": "IOGP, EPSG Geodetic Parameter Dataset",
        "dataset": "Transformation 1188, NAD83 to WGS 84 (1): NAD83 (EPSG:4269) to WGS 84 (EPSG:4326), "
        "geocentric translations 0, 0, 0 m, accuracy 4 m, extent North America - Canada and USA "
        "(CONUS, Alaska mainland)",
        "version": "EPSG Dataset v13.106 (record revised 2020-03-14)",
        "public_url": "https://epsg.org/transformation_1188/NAD83-to-WGS-84-1.html",
        "content_sha256": None,
    },
)


def _l(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _join(*parts: Mapping[str, str]) -> dict[str, str]:
    return _l("".join(p["zh"] for p in parts), " ".join(p["en"] for p in parts))


COUNTRY_NAMES = {"US": _l("美国", "United States")}

DATUM_NOTE = _l(
    "锚点坐标取自 Census 2024 Gazetteer CBSA 文件的 INTPTLAT / INTPTLONG（十进制度）。Gazetteer 的记录格式没有另写基准；同一批"
    "内部点在官方 TIGER/Line 2024 CBSA 文件里也有（七位小数，四舍五入到六位与 Gazetteer 相同），那份文件的坐标系是 NAD83"
    "（.prj 为 GCS_North_American_1983，技术文档 3.3.3 节也这样写），所以原始基准是 NAD83。"
    "发布为 WGS 84（EPSG:4326）时用 EPSG 登记的 1188 号转换“NAD83 to WGS 84 (1)”：三个平移量都是 0，精度约 4 米，所以数值"
    "原样不变；这样的误差对都会区代表点没有影响。",
    "Anchor coordinates are INTPTLAT / INTPTLONG (decimal degrees) from the Census 2024 Gazetteer CBSA "
    "file. The Gazetteer record layout does not restate a datum; the same internal points appear in the "
    "official 2024 TIGER/Line CBSA file (7 decimals, equal to the Gazetteer values when rounded to 6), "
    "whose coordinate system is NAD83 (.prj GCS_North_American_1983, also stated in section 3.3.3 of "
    "the TIGER/Line technical documentation), so the original datum is NAD83. They are published "
    'as WGS 84 (EPSG:4326) through EPSG transformation 1188, "NAD83 to WGS 84 (1)": all three '
    "translations are zero and its accuracy is about 4 m, so the numbers are unchanged. An error of "
    "that size does not matter for a metro-area representative point.",
)
POINT_NOTE = _l(
    "锚点是 Census 为整个都会区计算的内部点（代表点），不是住处、主城市中心或都会区边界；都会区范围大，点可能离主城市市区较远。"
    "首版不提供住宅、街道或 PUMA 多边形。",
    "The anchor is the internal point (a representative point) that Census computes for the whole "
    "metro area. It is not a home, the principal city's centre or a boundary, and in a large metro area "
    "it can be far from the main city. This version has no home, street or PUMA polygons.",
)
ELIGIBILITY_NOTE = _l(
    "这份清单不授予运行资格：能选哪些城市由能力清单与服务规则决定；没有锚点的城市仍可从城市列表访问。",
    "This list does not grant run eligibility: the capabilities list and service rules decide which "
    "cities can be chosen, and a city without an anchor can still be opened from the city list.",
)
SYNTHETIC_NOTE = _l(
    "合成城市不对应真实地点，没有地图位置（锚点为空，not_applicable），只能从城市列表访问。",
    "Synthetic cities are not real places and have no map position (anchor null, not_applicable); "
    "open them from the city list.",
)
COVERAGE_NOTE = _l(
    "各项资料的覆盖范围不一定与都会区或支持范围相同，以每次运行的资料性质与范围说明为准。",
    "Individual data sources may cover a different area than the metro area or the supported scope; "
    "see each run's data nature and scope notes.",
)


def _vintage_note(cbsa_count: int) -> dict[str, str]:
    return _l(
        f"城市地理按 CBSA 2023（OMB 2023 年 7 月划定，Bulletin No. 23-01）。2024 Gazetteer CBSA 文件的 {cbsa_count} 个 CBSA "
        "与资料包所用的 2023 年 7 月划定文件编号、名称逐条一致。",
        "City geography follows the CBSA 2023 delineation (OMB, July 2023, Bulletin No. 23-01). All "
        f"{cbsa_count} CBSAs in the 2024 Gazetteer CBSA file have the same codes and titles as the July "
        "2023 delineation file used by the pack.",
    )


@dataclass(frozen=True)
class _RealCity:
    """一座真实城市人工写定的部分：中文正式名（Census 名称的译名）与支持范围、覆盖说明的开头（按资料包的税务范围）。"""

    tax_policy: str
    zh_official: str
    residence: dict[str, str]
    work: dict[str, str]
    note: dict[str, str]
    coverage: dict[str, str]


REAL_CITIES = {
    "US:CBSA2023:35620": _RealCity(
        tax_policy="tax.us2024.nyc",
        zh_official="纽约-纽瓦克-泽西城都会区（纽约州、新泽西州）",
        residence=_l(
            "只支持纽约市五个区的居民：Bronx、Kings、New York、Queens、Richmond 县；都会区的新泽西州部分与纽约州其他县的居民不支持。",
            "Only residents of the five New York City boroughs are supported: Bronx, Kings, New York, "
            "Queens and Richmond counties. Residents of the New Jersey part of the metro area or of "
            "other New York State counties are not supported.",
        ),
        work=_l(
            "工作地只在纽约州境内，新泽西、宾州的就业片区不支持。",
            "Places of work must be in New York State; work areas in New Jersey or Pennsylvania are "
            "not supported.",
        ),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.nyc 裁剪：跨州税与非纽约市居民的规则没有建模。",
            "The scope follows the pack's tax rules (tax.us2024.nyc): multi-state taxes and rules for "
            "non-NYC residents are not modelled.",
        ),
        coverage=_l(
            "地图点按整个纽约都会区计算（含新泽西州部分），不是纽约市五区的中心。",
            "The map point is computed for the whole New York metro area, including its New Jersey "
            "part; it is not the centre of the five boroughs.",
        ),
    ),
    "US:CBSA2023:31080": _RealCity(
        tax_policy="tax.us2024.ca",
        zh_official="洛杉矶-长滩-阿纳海姆都会区（加利福尼亚州）",
        residence=_l(
            "支持都会区内的加州居民（都会区全部在加州）。",
            "California residents of the metro area are supported (the metro area is entirely in "
            "California).",
        ),
        work=_l("工作地在加州。", "Places of work are in California."),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.ca：加州居民、工作地在加州。",
            "The scope follows the pack's tax rules (tax.us2024.ca): California residents working in "
            "California.",
        ),
        coverage=_l(
            "地图点按整个洛杉矶都会区计算，不是洛杉矶市中心。",
            "The map point is computed for the whole Los Angeles metro area; it is not downtown Los "
            "Angeles.",
        ),
    ),
    "US:CBSA2023:41860": _RealCity(
        tax_policy="tax.us2024.ca",
        zh_official="旧金山-奥克兰-弗里蒙特都会区（加利福尼亚州）",
        residence=_l(
            "支持都会区内的加州居民（都会区的 5 个县全部在加州）。圣何塞不在这个都会区里：它属于另一个都会区"
            "（San Jose-Sunnyvale-Santa Clara，CBSA 41940），不支持。",
            "California residents of the metro area are supported (its five counties are all in "
            "California). San Jose is not in this metro area: it belongs to a separate one (San "
            "Jose-Sunnyvale-Santa Clara, CBSA 41940) that is not supported.",
        ),
        work=_l(
            "工作地在加州；圣何塞所在的 Santa Clara County 不在就业片区里。",
            "Places of work are in California; Santa Clara County, where San Jose is, is not one of "
            "the work areas.",
        ),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.ca：加州居民、工作地在加州。这里的“旧金山”指这个都会区，不是整个湾区。",
            "The scope follows the pack's tax rules (tax.us2024.ca): California residents working in "
            'California. "San Francisco" here means this metro area, not the whole Bay Area.',
        ),
        coverage=_l(
            "地图点按整个旧金山都会区计算，不是旧金山市中心；都会区不含圣何塞。",
            "The map point is computed for the whole San Francisco metro area; it is not downtown San "
            "Francisco, and the metro area does not include San Jose.",
        ),
    ),
    "US:CBSA2023:16980": _RealCity(
        tax_policy="tax.us2024.il",
        zh_official="芝加哥-内珀维尔-埃尔金都会区（伊利诺伊州、印第安纳州）",
        residence=_l(
            "只支持都会区的伊利诺伊州部分；都会区里印第安纳州的 4 个县不支持。",
            "Only the Illinois part of the metro area is supported; its four Indiana counties are not.",
        ),
        work=_l("工作地也只在伊利诺伊州。", "Places of work must also be in Illinois."),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.il 裁剪：居住地与工作地都在伊利诺伊州。",
            "The scope follows the pack's tax rules (tax.us2024.il): home and work are both in "
            "Illinois.",
        ),
        coverage=_l(
            "地图点按整个芝加哥都会区计算（含印第安纳州的县），不是芝加哥市中心。",
            "The map point is computed for the whole Chicago metro area, including its Indiana "
            "counties; it is not downtown Chicago.",
        ),
    ),
    "US:CBSA2023:26420": _RealCity(
        tax_policy="tax.us2024.tx",
        zh_official="休斯敦-帕萨迪纳-伍德兰兹都会区（得克萨斯州）",
        residence=_l(
            "支持都会区内的德州居民（都会区全部在德州）。",
            "Texas residents of the metro area are supported (the metro area is entirely in Texas).",
        ),
        work=_l("工作地在德州。", "Places of work are in Texas."),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.tx：德州居民、工作地在德州。",
            "The scope follows the pack's tax rules (tax.us2024.tx): Texas residents working in Texas.",
        ),
        coverage=_l(
            "地图点按整个休斯敦都会区计算，不是休斯敦市中心。",
            "The map point is computed for the whole Houston metro area; it is not downtown Houston.",
        ),
    ),
    "US:CBSA2023:42660": _RealCity(
        tax_policy="tax.us2024.wa",
        zh_official="西雅图-塔科马-贝尔维尤都会区（华盛顿州）",
        residence=_l(
            "支持都会区内的华盛顿州居民（都会区全部在华盛顿州）。",
            "Washington State residents of the metro area are supported (the metro area is entirely "
            "in Washington State).",
        ),
        work=_l("工作地在华盛顿州。", "Places of work are in Washington State."),
        note=_l(
            "范围按资料包的税务规则 tax.us2024.wa：华盛顿州居民、工作地在华盛顿州。",
            "The scope follows the pack's tax rules (tax.us2024.wa): Washington State residents "
            "working in Washington State.",
        ),
        coverage=_l(
            "地图点按整个西雅图都会区计算，不是西雅图市中心；都会区面积大，点可能离市区较远。",
            "The map point is computed for the whole Seattle metro area; it is not downtown Seattle, "
            "and because the metro area is large the point can be far from the city.",
        ),
    ),
}


# ---------------------------------------------------------------------- Census 原始文件


@dataclass(frozen=True)
class GazetteerPoint:
    """Gazetteer CBSA 文件的一行：名称、类型（1 都会区，2 小都会区）与内部点（度；原始基准 NAD83）。"""

    geoid: str
    name: str
    cbsa_type: str
    latitude: float
    longitude: float

    @property
    def short(self) -> str:
        """名称里的第一个主城市（``New York-Newark-Jersey City, NY-NJ Metro Area`` → ``New York``）。"""
        return self.name.split(",")[0].split("-")[0]


_FIELDS = ("GEOID", "NAME", "CBSA_TYPE", "INTPTLAT", "INTPTLONG")
_SUFFIX = {"1": " Metro Area", "2": " Micro Area"}


def parse_points(content: bytes) -> dict[str, GazetteerPoint]:
    """Gazetteer CBSA 文件（zip）→ GEOID → 名称、类型与内部点。每个点都要是有限数、在经纬度范围内，否则拒绝。"""
    z = zipfile.ZipFile(io.BytesIO(content))
    member = next((n for n in z.namelist() if n.endswith(".txt")), None)
    if member is None:
        raise DisplayResourceError("Gazetteer 压缩包里没有 .txt 表")
    lines = z.read(member).decode("utf-8-sig").splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    missing = [f for f in _FIELDS if f not in header]
    if missing:
        raise DisplayResourceError(f"Gazetteer 表头缺少 {missing}")
    i = {f: header.index(f) for f in _FIELDS}
    out: dict[str, GazetteerPoint] = {}
    for ln in lines[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if len(p) < len(header):
            continue
        geoid = p[i["GEOID"]]
        try:
            lat, lon = float(p[i["INTPTLAT"]]), float(p[i["INTPTLONG"]])
        except ValueError as exc:
            raise DisplayResourceError(f"CBSA {geoid} 的内部点不是数：{exc}") from exc
        if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
            raise DisplayResourceError(f"CBSA {geoid} 的内部点不在经纬度范围内：{lat}, {lon}")
        out[geoid] = GazetteerPoint(geoid, p[i["NAME"]], p[i["CBSA_TYPE"]], lat, lon)
    return out


def check_vintage(points: Mapping[str, GazetteerPoint], titles: Mapping[str, str]) -> int:
    """Gazetteer 与划定文件的 CBSA 逐条一致（编号相同；名称是划定文件的名称加上都会 / 小都会后缀）→ 个数；否则拒绝。"""
    only = sorted(set(points) ^ set(titles))
    if only:
        raise DisplayResourceError(f"Gazetteer 与 2023 年 7 月划定文件的 CBSA 不同：{only[:10]}")
    bad = sorted(
        code
        for code, p in points.items()
        if p.cbsa_type not in _SUFFIX or p.name != titles[code] + _SUFFIX[p.cbsa_type]
    )
    if bad:
        raise DisplayResourceError(f"Gazetteer 与 2023 年 7 月划定文件的名称或类型不同：{bad[:10]}")
    return len(points)


def read_sources(cache: Path = DEFAULT_CACHE) -> tuple[dict[str, GazetteerPoint], int]:
    """读本机缓存里的 Gazetteer 与 2023 年 7 月划定文件（不联网），要求就是核对过的那两份；返回内部点与逐条一致的 CBSA 个数。"""
    content: dict[str, bytes] = {}
    for req, want in (
        (gazetteer_request(), GAZETTEER_SHA256),
        (delineation_request(), DELINEATION_SHA256),
    ):
        got = read_cached(req, cache)
        if not got.ok or got.content is None:
            raise DisplayResourceError(f"{req.source_id}：{got.record.note}")
        if got.record.raw_sha256 != want:
            raise DisplayResourceError(
                f"{req.source_id} 的缓存不是核对过的那一份（SHA-256 {got.record.raw_sha256}，应为 {want}）"
            )
        content[req.source_id] = got.content
    points = parse_points(content["census_gazetteer_cbsa_2024"])
    metros = parse_delineation(content["census_cbsa_2023"])
    return points, check_vintage(points, {code: geo.title for code, geo in metros.items()})


def load_life_input(pack: DataPack, root: Path) -> Any:
    """资料包的 E1 生活输入（E3B 是冻结的 JSON，合成包是 YAML），用来读城市目录的税务范围与片区。"""
    path = root / pack.body.e1_base.life_input
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


# ---------------------------------------------------------------------- 清单


def build_bundle(
    pack: DataPack,
    life_input: Mapping[str, Any],
    points: Mapping[str, GazetteerPoint] | None = None,
    cbsa_count: int | None = None,
) -> dict[str, Any]:
    """一个资料包的地理显示清单（纯函数）。真实资料包必须给 ``read_sources`` 的结果：没有核对过的坐标就拒绝，不猜。"""
    body = pack.body
    if body.status == "synthetic_fixture_only":
        bundle = {
            "status": "complete",
            "cities": [
                _synthetic_city(c, _catalog(life_input, c), body.pack_id) for c in body.cities
            ],
            "sources": [],
            "notes": [SYNTHETIC_NOTE, ELIGIBILITY_NOTE],
        }
    else:
        if points is None or cbsa_count is None:
            raise DisplayResourceError(
                "真实资料包要用核对过的 Gazetteer 内部点与 CBSA 版本核对结果"
            )
        bundle = {
            "status": "complete",
            "cities": [
                _real_city(c, _catalog(life_input, c), points.get(c.code), body.pack_id)
                for c in body.cities
            ],
            "sources": [dict(s) for s in REAL_SOURCES],
            "notes": [DATUM_NOTE, _vintage_note(cbsa_count), POINT_NOTE, ELIGIBILITY_NOTE],
        }
    _check(bundle, pack.fingerprint)
    return bundle


def dump(bundle: Mapping[str, Any]) -> str:
    """写进仓库的文字（UTF-8、缩进 1、末尾换行；写文件时用 LF）。"""
    return json.dumps(bundle, ensure_ascii=False, indent=1) + "\n"


def _identity(city: CityDecl) -> dict[str, Any]:
    names = COUNTRY_NAMES.get(city.country)
    if names is None:
        raise DisplayResourceError(f"没有国家 {city.country} 的中英名称")
    return {
        "city_ref": city.city_ref,
        "country": city.country,
        "scheme": city.scheme,
        "code": city.code,
        "geography_version": city.geography_version,
        "country_names": dict(names),
    }


def _catalog(life_input: Mapping[str, Any], city: CityDecl) -> Mapping[str, Any]:
    want = (city.country, city.scheme, city.code)
    for catalog in life_input["catalogs"]:
        key = catalog["city"]
        if (key["country"], key["scheme"], key["code"]) == want:
            return catalog
    raise DisplayResourceError(f"生活输入里没有 {city.city_ref} 的城市目录")


def _areas(catalog: Mapping[str, Any], kind: str) -> list[str]:
    return [a["label"] for a in catalog["areas"] if a["kind"] == kind]


_PUMA = re.compile(r"（PUMA \d{7}）$")


def _home_pumas(catalog: Mapping[str, Any]) -> int:
    homes = _areas(catalog, "home")
    if not homes or not all(_PUMA.search(h) for h in homes):
        raise DisplayResourceError(
            f"{catalog['label']} 的居住片区不全是 2020 年 PUMA，范围说明要改写"
        )
    return len(homes)


def _work_counties(catalog: Mapping[str, Any]) -> list[str]:
    """就业片区的县名（资料包里是英文专名）；“其余工作地”一片拆成它包含的县。"""
    out: list[str] = []
    for label in _areas(catalog, "work"):
        name = label.removeprefix("就业片区·")
        if name.startswith("其余工作地（") and name.endswith("）"):
            out += name.removeprefix("其余工作地（").removesuffix("）").split("、")
        else:
            out.append(name)
    if not out or any(has_cjk(n) for n in out):
        raise DisplayResourceError(f"{catalog['label']} 的就业片区名称认不出来：{out}")
    return out


def _real_city(
    city: CityDecl, catalog: Mapping[str, Any], point: GazetteerPoint | None, pack_id: str
) -> dict[str, Any]:
    spec = REAL_CITIES.get(city.city_ref)
    if spec is None:
        raise DisplayResourceError(f"{city.city_ref} 还没有写定的中文正式名与支持范围说明")
    if catalog["tax_policy_id"] != spec.tax_policy:
        raise DisplayResourceError(
            f"{city.city_ref} 的税务范围是 {catalog['tax_policy_id']}，说明是按 {spec.tax_policy} 写的"
        )
    if point is None or point.cbsa_type != "1":
        raise DisplayResourceError(f"Gazetteer 里没有都会区 {city.code}")
    homes, work = _home_pumas(catalog), _work_counties(catalog)
    return {
        **_identity(city),
        "names": {
            "zh": {"official": spec.zh_official, "short": city.label.split("（")[0]},
            "en": {"official": point.name, "short": point.short},
        },
        "model_kind": "real",
        "anchor_status": "available",
        "anchor": {
            "latitude": point.latitude,
            "longitude": point.longitude,
            "crs": "EPSG:4326",
            "kind": "metro_internal_point",
            "source_refs": list(ANCHOR_SOURCES),
        },
        "support_scope": {
            "ref": spec.tax_policy,
            "version": pack_id,
            "residence": _join(
                spec.residence,
                _l(
                    f"居住片区是其中的 {homes} 个 2020 年 PUMA（资料的地理单位，不是街区）。",
                    f"Home areas are {homes} PUMAs (2020 definitions) within this scope; they are "
                    "data units, not neighbourhoods.",
                ),
            ),
            "work": _join(
                spec.work, _l(f"就业片区：{'、'.join(work)}。", f"Work areas: {', '.join(work)}.")
            ),
            "note": spec.note,
        },
        "coverage_note": _join(spec.coverage, COVERAGE_NOTE),
    }


def _synthetic_city(city: CityDecl, catalog: Mapping[str, Any], pack_id: str) -> dict[str, Any]:
    homes, works = len(_areas(catalog, "home")), len(_areas(catalog, "work"))
    tax = catalog["tax_policy_id"]
    return {
        **_identity(city),
        "names": {
            "zh": {"official": city.label, "short": city.label},
            "en": {"official": f"Synthetic city {city.code}", "short": f"Synthetic {city.code}"},
        },
        "model_kind": "synthetic",
        "anchor_status": "not_applicable",
        "anchor": None,
        "support_scope": {
            "ref": tax,
            "version": pack_id,
            "residence": _l(
                f"合成城市，不对应真实地点；居住地是它的 {homes} 个合成居住片区。",
                f"A synthetic city, not a real place; homes are in its {homes} synthetic home "
                f"{_plural(homes)}.",
            ),
            "work": _l(
                f"工作地是同一座合成城市的 {works} 个合成就业片区。",
                f"Work is in the same synthetic city's {works} synthetic work {_plural(works)}.",
            ),
            "note": _l(
                f"合成资料包的模型设定（税务设定 {tax}），不代表任何真实城市。",
                f"Model settings of the synthetic pack (tax setting {tax}); it does not represent "
                "any real city.",
            ),
        },
        "coverage_note": _l(
            "全部数值是手工合成的模型设定或测试资料，没有地图位置，只能从城市列表访问。",
            "All values are hand-made model settings or test data; there is no map position, so "
            "open it from the city list.",
        ),
    }


def _plural(n: int) -> str:
    return "area" if n == 1 else "areas"


def _check(bundle: Mapping[str, Any], pack_fingerprint: str) -> None:
    """按公共模型校验（补上服务加的三个字段），锚点只引用登记的来源，英文里没有中文和尖括号。"""
    GeographyDisplayBundle.model_validate(
        {
            **bundle,
            "display_schema_version": DISPLAY_SCHEMA_VERSION,
            "resource_version": RESOURCE_PREFIX + "0" * 16,
            "pack_fingerprint": pack_fingerprint,
        }
    )
    refs = {s["source_ref"] for s in bundle["sources"]}
    for city in bundle["cities"]:
        unknown = set((city["anchor"] or {}).get("source_refs", ())) - refs
        if unknown:
            raise DisplayResourceError(
                f"{city['city_ref']} 的锚点引用了没有登记的来源 {sorted(unknown)}"
            )
    for text in _english(bundle):
        if has_cjk(text) or "<" in text or ">" in text:
            raise DisplayResourceError(f"英文里有中文或尖括号：{text[:60]}")


def _english(node: Any, inside: bool = False) -> Iterator[str]:
    """清单里所有英文文字（``en`` 下面的字符串）。"""
    if isinstance(node, str):
        if inside:
            yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _english(value, inside or key == "en")
    elif isinstance(node, list):
        for value in node:
            yield from _english(value, inside)
