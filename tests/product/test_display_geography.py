"""地理显示清单（``product.display.geography``、``configs/product/display/geography/``）。

* 仓库里的两份清单就是生成器的输出（不手改），按公共模型校验通过，城市与资料包一一对应；
* E3B 六城的锚点等于 Census 2024 Gazetteer 原文里的内部点（下面按原文写出，可以手工核对），在经纬度范围内、轴顺序没有写反；
  原始文件的 SHA-256 与 E3B 冻结清单登记的一致；合成城市没有位置；
* 纽约、芝加哥、旧金山的支持限制与基准、代表点、运行资格三条说明中英都有；
* 生成器拒绝不在范围内的坐标、对不上的边界版本与没核对过的缓存。

测试不联网、不读 ``data/raw``：真实缓存的逐字核对由 ``scripts/product_geography.py check`` 在本机做。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import pytest

from common.product_contracts import GeographyDisplayBundle
from product.display.catalog import DisplayResourceError
from product.display.geography import (
    DELINEATION_SHA256,
    GAZETTEER_SHA256,
    GazetteerPoint,
    build_bundle,
    check_vintage,
    dump,
    load_life_input,
    parse_points,
    read_sources,
)
from product.pack import load_pack
from product.realdata.geo import delineation_request, gazetteer_request

REPO = Path(__file__).resolve().parents[2]
GEO = REPO / "configs/product/display/geography"

#: Census 2024 Gazetteer CBSA 文件（2024_Gaz_cbsa_national.txt）里六城的原文：GEOID → NAME、INTPTLAT、INTPTLONG
CENSUS = {
    "35620": ("New York-Newark-Jersey City, NY-NJ Metro Area", "40.735425", "-73.80773"),
    "31080": ("Los Angeles-Long Beach-Anaheim, CA Metro Area", "34.108703", "-118.182753"),
    "41860": ("San Francisco-Oakland-Fremont, CA Metro Area", "37.773718", "-122.274432"),
    "16980": ("Chicago-Naperville-Elgin, IL-IN Metro Area", "41.75878", "-87.841887"),
    "26420": ("Houston-Pasadena-The Woodlands, TX Metro Area", "29.801005", "-95.34262"),
    "42660": ("Seattle-Tacoma-Bellevue, WA Metro Area", "47.490552", "-121.833977"),
}
#: 2024 Gazetteer 与 2023 年 7 月划定文件逐条一致的 CBSA 个数（本机 check 时由真实文件算出）
CBSA_COUNT = 935


@pytest.fixture(scope="module")
def e3b():
    pack = load_pack(REPO / "configs/product/e3b_us_v1_pack.yaml")
    return pack, load_life_input(pack, REPO)


@pytest.fixture(scope="module")
def synthetic():
    pack = load_pack(REPO / "configs/product/e3a_synthetic_pack.yaml")
    return pack, load_life_input(pack, REPO)


def _points() -> dict[str, GazetteerPoint]:
    return {
        code: GazetteerPoint(code, name, "1", float(lat), float(lon))
        for code, (name, lat, lon) in CENSUS.items()
    }


def _bundle(pack) -> GeographyDisplayBundle:
    body = json.loads((GEO / f"{pack.body.pack_id}.json").read_text(encoding="utf-8"))
    return GeographyDisplayBundle.model_validate(
        {
            **body,
            "display_schema_version": "e3-display-v1",
            "resource_version": "e3-display-res-v1-" + "0" * 16,
            "pack_fingerprint": pack.fingerprint,
        }
    )


def _gazetteer(*rows: tuple[str, str, str, str, str]) -> bytes:
    """一份 Gazetteer CBSA 格式的小压缩包：每行 (GEOID, NAME, CBSA_TYPE, INTPTLAT, INTPTLONG)。"""
    header = "CSAFP\tGEOID\tNAME\tCBSA_TYPE\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG   "
    lines = [header] + [
        "\t".join(["", g, n, t, "1", "0", "0.4", "0", la, lo]) for g, n, t, la, lo in rows
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2024_Gaz_cbsa_national.txt", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_committed_files_are_exactly_the_generator_output(e3b, synthetic):
    for (pack, life_input), points, count in (
        (e3b, _points(), CBSA_COUNT),
        (synthetic, None, None),
    ):
        text = dump(build_bundle(pack, life_input, points, count))
        assert (GEO / f"{pack.body.pack_id}.json").read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("which", ["e3b", "synthetic"])
def test_bundles_validate_and_list_the_pack_cities_in_order(which, request):
    pack, _ = request.getfixturevalue(which)
    bundle = _bundle(pack)
    assert bundle.status == "complete"
    ident = [(c.city_ref, c.country, c.scheme, c.code, c.geography_version) for c in bundle.cities]
    assert ident == [
        (c.city_ref, c.country, c.scheme, c.code, c.geography_version) for c in pack.body.cities
    ]


def test_e3b_anchors_are_the_census_internal_points(e3b):
    bundle = _bundle(e3b[0])
    refs = {s.source_ref for s in bundle.sources}
    for city in bundle.cities:
        name, lat, lon = CENSUS[city.code]
        a = city.anchor
        assert (city.model_kind, city.anchor_status) == ("real", "available") and a is not None
        assert (a.latitude, a.longitude) == (float(lat), float(lon))
        assert (a.crs, a.kind) == ("EPSG:4326", "metro_internal_point")
        assert math.isfinite(a.latitude) and math.isfinite(a.longitude)
        # 美国本土：纬度 24–50、经度 −125…−66，轴顺序写反就过不了
        assert 24 < a.latitude < 50 and -125 < a.longitude < -66
        assert set(a.source_refs) <= refs
        assert city.names.en is not None and city.names.en.official == name


def test_raw_file_hashes_are_the_ones_the_pack_registered(e3b):
    manifest = json.loads(
        (REPO / "data/frozen/intake/us-v1/manifest.json").read_text(encoding="utf-8")
    )
    raw = {s["source_id"]: s["raw_sha256"] for s in manifest["sources"]}
    assert raw["census_gazetteer_cbsa_2024"] == GAZETTEER_SHA256
    assert GAZETTEER_SHA256 == "0ec21c5d83982467903f1500fbbae87311edc81f09bed8ec1bbded80bce7eec3"
    assert raw["census_cbsa_2023"] == DELINEATION_SHA256
    published = {s.public_url: s.content_sha256 for s in _bundle(e3b[0]).sources}
    assert published[gazetteer_request().url] == GAZETTEER_SHA256
    assert published[delineation_request().url] == DELINEATION_SHA256


def test_synthetic_cities_have_no_location(synthetic):
    bundle = _bundle(synthetic[0])
    assert bundle.cities and bundle.sources == ()
    for city in bundle.cities:
        assert (city.model_kind, city.anchor_status, city.anchor) == (
            "synthetic",
            "not_applicable",
            None,
        )


def test_scope_states_the_nyc_chicago_and_san_francisco_limits(e3b):
    pack = e3b[0]
    scope = {c.code: c.support_scope for c in _bundle(pack).cities}
    nyc, chi, sf = scope["35620"], scope["16980"], scope["41860"]
    assert "纽约市五个区" in nyc.residence.zh and "five New York City boroughs" in nyc.residence.en
    assert "纽约州" in nyc.work.zh and "New York State" in nyc.work.en
    assert "新泽西" in nyc.work.zh and "New Jersey" in nyc.work.en
    assert "伊利诺伊州部分" in chi.residence.zh and "Illinois part" in chi.residence.en
    assert "印第安纳" in chi.residence.zh and "Indiana" in chi.residence.en
    assert "圣何塞" in sf.residence.zh and "San Jose" in sf.residence.en
    assert "41940" in sf.residence.zh and "41940" in sf.residence.en
    for s in scope.values():
        assert s.ref.startswith("tax.us2024.") and s.version == pack.body.pack_id


def test_notes_cover_datum_representative_point_and_eligibility(e3b, synthetic):
    notes = _bundle(e3b[0]).notes
    zh, en = "".join(n.zh for n in notes), " ".join(n.en or "" for n in notes)
    for word in ("NAD83", "EPSG:4326", "1188", "不是住处", "不授予运行资格", "935"):
        assert word in zh
    for word in (
        "NAD83",
        "EPSG:4326",
        "1188",
        "4 m",
        "not a home",
        "does not grant run eligibility",
    ):
        assert word in en
    assert any("不授予运行资格" in n.zh for n in _bundle(synthetic[0]).notes)


def test_real_cities_need_verified_points(e3b):
    pack, life_input = e3b
    with pytest.raises(DisplayResourceError):
        build_bundle(pack, life_input)
    points = _points()
    del points["41860"]
    with pytest.raises(DisplayResourceError, match="41860"):
        build_bundle(pack, life_input, points, CBSA_COUNT)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [("nan", "-87.8"), ("91", "-87.8"), ("41.7", "-180.5"), ("41.7", "inf"), ("", "1")],
)
def test_points_must_be_finite_and_within_range(lat, lon):
    with pytest.raises(DisplayResourceError):
        parse_points(
            _gazetteer(("16980", "Chicago-Naperville-Elgin, IL-IN Metro Area", "1", lat, lon))
        )


def test_vintage_check_needs_the_same_codes_and_titles():
    points = parse_points(
        _gazetteer(
            ("16980", "Chicago-Naperville-Elgin, IL-IN Metro Area", "1", "41.75878", "-87.841887"),
            ("10100", "Aberdeen, SD Micro Area", "2", "45.4", "-98.7"),
        )
    )
    assert points["16980"].latitude == 41.75878 and points["16980"].short == "Chicago"
    titles = {"16980": "Chicago-Naperville-Elgin, IL-IN", "10100": "Aberdeen, SD"}
    assert check_vintage(points, titles) == 2
    with pytest.raises(DisplayResourceError):  # 2020 年划定的旧名称（当时含威斯康星的基诺沙县）
        check_vintage(points, {**titles, "16980": "Chicago-Naperville-Elgin, IL-IN-WI"})
    with pytest.raises(DisplayResourceError):  # 划定文件里少了一个 CBSA
        check_vintage(points, {"16980": titles["16980"]})


def test_sources_must_be_the_verified_cached_files(tmp_path):
    with pytest.raises(DisplayResourceError, match="缓存里没有"):
        read_sources(tmp_path)
    req = gazetteer_request()
    content = _gazetteer(
        ("16980", "Chicago-Naperville-Elgin, IL-IN Metro Area", "1", "41.7", "-87.8")
    )
    folder = tmp_path / req.source_id
    folder.mkdir()
    (folder / f"{req.key[:16]}.bin").write_bytes(content)
    meta = {"sha256": hashlib.sha256(content).hexdigest()}
    (folder / f"{req.key[:16]}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(DisplayResourceError, match="不是核对过的那一份"):
        read_sources(tmp_path)
