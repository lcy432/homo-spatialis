"""E3B 通勤资料（DECISIONS/0042 决定 4）：片区、往返格的回退、票价与油费、用车权、冻结文件。

测试不联网、不读 ``data/raw`` 与 ``runs/``：票价网页是把配置里的摘句写成的假网页（放进临时缓存），地理、代码表、PUMS 人员与
intake 的事实都是手写的小样本。手算值：

* 一座合成的“纽约”：曼哈顿两个居住 PUMA（A = 3604101、D = 3604102，通勤层 core）、布鲁克林一个（B = 3604301，middle）；
  新泽西的 PUMA 不在税的支持范围，3603101 只有一半普查区在都会区内。
* 工作地流入（权重）：曼哈顿 600、布鲁克林 300、拿骚 60、韦斯切斯特 40（合计 1000）→ 份额 0.6 / 0.3 / 0.06 各成一个片区，
  0.04 的韦斯切斯特并进“其余工作地”。
* A→曼哈顿：公交 30 人单程 20 分钟（住处格，往返 40，票价 2 × 2.90 = 5.80）；步行 30 人 15 分钟（往返 30）；开车只有 5 人
  （30 分钟）→ 同一通勤层合并格 = A 的 5 人 + D 的 30 人（40 分钟），35 人的加权中位数 40 → 往返 80，油费
  80/60 × 25 ÷ 23.4 × 3.351 = 4.7735… → 4.77。
* B→布鲁克林开车 3 人（20 分钟）：middle 层只有 B 自己 → 全城合并格 = B 的 3 人 + D 的 30 人（25 分钟）→ 往返 50，油费 2.98。
* A→布鲁克林步行 5 人：步行不用合并格 → 缺；这条线没有公交与开车的记录 → 整条线没有任何方式。
* 居民平时的往返：A 84 人的中位数 20 → 40；B 36 人 45 → 90；D 60 人 25 → 50。
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3
import zipfile
import zlib
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from common.intake_contracts import FetchRecord
from common.life_contracts import AreaDecl, CommuteRelation, LifeOpportunity
from econ.life_commute import choose_leg
from product.realdata import geo
from product.realdata.build import RawChangedError
from product.realdata.commute import (
    COMMUTE_CONFIG,
    FROZEN_DIR,
    GasFact,
    IntakeFacts,
    PowLookup,
    RawInputs,
    bound_raw,
    build_city,
    compile_all,
    compile_costs,
    cost_requests,
    define_work_areas,
    frozen_payload_problems,
    fuel_per_roundtrip,
    load_commute_config,
    parse_powpuma_lookup,
    read_commuters,
    read_intake,
    select_home_pumas,
    write_frozen,
)
from product.realdata.fetch import Request, _store

CFG = load_commute_config(COMMUTE_CONFIG)
NYC = next(c for c in CFG["cities"] if c["key"] == "nyc")
A, D, B = "3604101", "3604102", "3604301"
M, K, N = "nyc.area.w.3604100", "nyc.area.w.3604300", "nyc.area.w.3603200"
OTHER = "nyc.area.w.other"


def fake_pdf(text: str) -> bytes:
    """一份最小的 PDF：一个 Flate 压缩的内容流，正文是一个字面字符串（括号与反斜杠按 PDF 规则转义）。"""
    lit = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = zlib.compress(b"BT (" + lit.encode("cp1252") + b") Tj ET")
    head = f"%PDF-1.7\n1 0 obj << /Length {len(stream)} /Filter /FlateDecode >>\nstream\n"
    return head.encode("ascii") + stream + b"\nendstream\nendobj\n%EOF\n"


def fake_cost_cache(tmp: Path, cfg: dict, *, drop: str | None = None) -> Path:
    """每份来源一页：正文是配置里引用它的全部摘句（``drop`` 那一句不写进去）。被拒的来源不放进缓存；``format: pdf`` 的来源
    存成 PDF。"""
    quotes: dict[str, list[str]] = {sid: [] for sid in cfg["sources"]}
    car = cfg["drive"]["car_access"]
    specs = [cfg["drive"]["mpg"], car["annual"], car["annual_miles"], car["fuel_cents_per_mile"]]
    for c in cfg["cities"]:
        t = c["transit"]
        specs += [s for s in (t.get("fare"), t.get("monthly_pass")) if s]
    for s in specs:
        for c in s["cite"]:
            quotes[c["source"]].append(c["quote"])
    for sid, req in cost_requests(cfg).items():
        if not quotes[sid]:
            continue
        kept = [q for q in quotes[sid] if q != drop]
        if cfg["sources"][sid].get("format") == "pdf":
            _store(tmp, req, fake_pdf(" ".join(kept)), 200, "application/pdf")
            continue
        body = " <p> ".join(kept)
        _store(tmp, req, f"<html><body>{body}</body></html>".encode(), 200, "text/html")
    return tmp


@pytest.fixture(scope="module")
def costs(tmp_path_factory):
    return compile_costs(CFG, fake_cost_cache(tmp_path_factory.mktemp("costs"), CFG))


# --------------------------------------------------------------------------- 合成的城市


def county(fips: str) -> geo.County:
    return geo.County(fips[:2], fips[2:], fips, "", True, None, None)


def puma(
    code: str, counties: tuple[str, ...], inside: int = 10, total: int = 10
) -> geo.PumaMembership:
    return geo.PumaMembership(code[:2], code[2:], inside, total, counties, f"Synthetic {code} PUMA")


def metro() -> geo.MetroGeo:
    return geo.MetroGeo(
        cbsa="35620",
        title="Synthetic New York",
        counties=[county(f) for f in ("36061", "36047", "36059", "36119", "34017")],
        pumas=[
            puma(A, ("36061",)),
            puma(D, ("36061",)),
            puma(B, ("36047",)),
            puma("3400601", ("34017",)),
            puma("3603102", ("36119",)),
            puma("3603101", ("36119", "36001"), inside=5, total=10),
        ],
    )


LOOKUP = PowLookup(
    counties={
        ("36", "04100"): frozenset({"36061"}),
        ("36", "04300"): frozenset({"36047"}),
        ("36", "03200"): frozenset({"36059"}),
        ("36", "03100"): frozenset({"36119"}),
        ("34", "00600"): frozenset({"34017"}),
        ("36", "09999"): frozenset({"36061", "36001"}),
        ("36", "05000"): frozenset({"36001"}),
    },
    county_names={
        "36061": "New York County",
        "36047": "Kings County",
        "36059": "Nassau County",
        "36119": "Westchester County",
        "34017": "Hudson County",
        "36001": "Albany County",
    },
)
POW = {
    "M": ("036", "04100"),
    "K": ("036", "04300"),
    "N": ("036", "03200"),
    "W": ("036", "03100"),
    "NJ": ("034", "00600"),
    "partial": ("036", "09999"),
    "albany": ("036", "05000"),
}


def rows(
    home: str,
    work: str,
    jwtrns: int,
    minutes: int,
    count: int,
    weight: int = 1,
    soc: str = "43",
) -> list[dict]:
    powsp, powpuma = POW[work]
    return [
        {
            "STATE": home[:2],
            "PUMA": home[2:],
            "PWGTP": weight,
            "JWMNP": minutes,
            "JWTRNS": jwtrns,
            "POWSP": powsp,
            "POWPUMA": powpuma,
            "SOC2": soc,
        }
    ] * count


def commuters() -> pd.DataFrame:
    """A→曼哈顿坐公交的 30 人是 SOC 15（计算机与数学），其余都是 43（办公室与行政支持）。"""
    car, bus, walk, taxi = 1, 2, 10, 7
    r = (
        rows(A, "M", bus, 20, 30, soc="15")
        + rows(A, "M", walk, 15, 30)
        + rows(A, "M", car, 30, 5)
        + rows(A, "M", taxi, 30, 4)
        + rows(A, "K", walk, 20, 5)
        + rows(A, "NJ", car, 50, 6)
        + rows(A, "partial", car, 60, 2)
        + rows(A, "albany", car, 70, 2)
        + rows(D, "M", car, 40, 30)
        + rows(D, "K", car, 25, 30)
        + rows(B, "K", car, 20, 3)
        + rows(B, "M", bus, 45, 30)
        + rows(B, "N", car, 60, 3)
    )
    df = pd.DataFrame(r)
    # 新泽西居民（不在居住片区里）补足工作地的流入：曼哈顿 600、布鲁克林 300、拿骚 60、韦斯切斯特 40
    inbound = df.assign(k=df["POWSP"] + df["POWPUMA"]).groupby("k")["PWGTP"].sum()
    fill = []
    for w, target in (("M", 600), ("K", 300), ("N", 60), ("W", 40)):
        have = int(inbound.get("".join(POW[w]), 0))
        fill += rows("3400601", w, 1, 30, 1, weight=target - have)
    return pd.concat([df, pd.DataFrame(fill)], ignore_index=True)


def intake_facts() -> IntakeFacts:
    return IntakeFacts(
        build_id=CFG["intake"]["build_id"],
        fetch_log={},
        tiers={"35620": {A: "core", D: "core", B: "middle"}},
        medians={"35620": {A: 20.0, D: 25.0, B: 45.0}},
        gasoline={
            "35620": GasFact(
                value=Decimal("3.351"),
                record_id="background.energy_prices.35620",
                locator="ap.data.0.Current area=S12A",
                raw_sha256="a" * 64,
            )
        },
    )


def raw_inputs() -> RawInputs:
    return RawInputs(
        geography={"35620": metro()},
        lookup=LOOKUP,
        commuters=commuters(),
        fingerprints={"pums_p:36": "b" * 64, "pums_p:34": "c" * 64, "codelist": "d" * 64},
    )


def nyc_only(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    out["cities"] = [c for c in out["cities"] if c["key"] == "nyc"]
    return out


@pytest.fixture(scope="module")
def city(costs):
    return build_city(CFG, NYC, raw_inputs(), intake_facts(), costs).payload


def legs_of(payload: dict) -> dict[tuple[str, str, str], dict]:
    return {
        (g["home_area"].rsplit(".", 1)[1], g["work_area"], g["mode"]): g
        for g in payload["commute"]["legs"]
    }


# --------------------------------------------------------------------------- 票价、油耗与用车费用的核对


def test_cost_quotes_pass_and_the_blocked_agency_is_reported(costs):
    assert sorted(costs.transit) == ["chi", "hou", "la", "nyc", "sea"]
    assert sorted(costs.transit_blocked) == ["sf"]
    assert any("403" in w for w in costs.transit_blocked["sf"])
    assert costs.drive_blocked == []
    nyc = costs.transit["nyc"]
    assert nyc.fare.value == Decimal("2.9") and nyc.monthly_pass.value == Decimal("132")
    assert costs.transit["la"].monthly_pass is None
    assert costs.mpg.value == Decimal("23.4") and costs.car_annual.value == Decimal("8886")
    assert costs.car_miles.value == Decimal("15000") and costs.car_fuel_cents.value == Decimal(
        "11.12"
    )
    for c in nyc.fare.citations:
        assert c["url"].startswith("https://") and len(c["raw_sha256"]) == 64


def test_a_quote_missing_from_its_page_blocks_only_that_city(tmp_path):
    quote = NYC["transit"]["monthly_pass"]["cite"][0]["quote"]
    b = compile_costs(CFG, fake_cost_cache(tmp_path, CFG, drop=quote))
    assert "nyc" not in b.transit and "chi" in b.transit
    assert any("没有逐字出现" in w for w in b.transit_blocked["nyc"])


def test_a_number_that_is_not_in_its_quotes_blocks(tmp_path):
    cfg = copy.deepcopy(CFG)
    next(c for c in cfg["cities"] if c["key"] == "nyc")["transit"]["fare"]["value"] = 3.00
    b = compile_costs(cfg, fake_cost_cache(tmp_path, cfg))
    assert any("没有出现在摘句里" in w for w in b.transit_blocked["nyc"])


def test_drive_costs_are_blocked_without_their_quote(tmp_path):
    quote = CFG["drive"]["mpg"]["cite"][0]["quote"]
    b = compile_costs(CFG, fake_cost_cache(tmp_path, CFG, drop=quote))
    assert b.mpg is None and any("每加仑英里数" in w for w in b.drive_blocked)


def test_fuel_is_rounded_to_cents():
    # 60 分钟 = 25 英里 ÷ 23.4 = 1.0684 加仑 × 3.351 = 3.5801 → 3.58
    assert fuel_per_roundtrip(60, Decimal(25), Decimal("23.4"), Decimal("3.351")) == Decimal("3.58")
    assert fuel_per_roundtrip(0, Decimal(25), Decimal("23.4"), Decimal("3.351")) == Decimal("0.00")


# --------------------------------------------------------------------------- 片区


def test_home_areas_are_full_pumas_inside_the_tax_scope():
    keep, excluded = select_home_pumas(NYC, metro())
    assert [p.code for p in keep] == [A, D, B]
    assert [x["puma"] for x in excluded["partial_in_metro"]] == ["3603101"]
    assert sorted(x["puma"] for x in excluded["outside_tax_scope"]) == ["3400601", "3603102"]
    whole = {"home_scope": {}}
    assert len(select_home_pumas(whole, metro())[0]) == 5


def test_work_areas_follow_the_inbound_share_rule():
    areas, diag = define_work_areas(
        "nyc", commuters(), LOOKUP, {c.fips for c in metro().counties}, {"36"}, 0.05
    )
    assert [a.area_id for a in areas] == [M, K, N, OTHER]
    assert [str(a.share) for a in areas] == ["0.6", "0.3", "0.06", "0.04"]
    assert areas[-1].powpumas == (("36", "03100"),)
    assert [x["powpuma"] for x in diag["excluded_partial_in_metro"]] == ["36-09999"]
    assert [x["powpuma"] for x in diag["excluded_state_not_supported"]] == ["34-00600"]


def test_area_declarations(city):
    areas = {a["area_id"]: AreaDecl.model_validate(a) for a in city["areas"]}
    home = areas[f"nyc.area.h.{A}"]
    assert home.kind == "home" and home.typical_roundtrip_minutes == 40
    assert home.typical_source.kind == "estimated"
    assert areas[f"nyc.area.h.{B}"].typical_roundtrip_minutes == 90
    assert areas[f"nyc.area.h.{D}"].typical_roundtrip_minutes == 50
    assert areas[M].kind == "work" and areas[M].typical_roundtrip_minutes is None
    assert city["diagnostics"]["typical"]["differs_from_intake_commute_table"] == []


# --------------------------------------------------------------------------- 往返格


def test_cells_use_the_declared_fallback_levels(city):
    legs = legs_of(city)
    cases = {
        (A, M, "transit"): (40, "puma 格 n=30（这条线 30）"),
        (A, M, "walk_bike"): (30, "puma 格 n=30（这条线 30）"),
        (A, M, "drive"): (80, "tier 格 n=35（这条线 5）"),
        (D, M, "drive"): (80, "puma 格 n=30（这条线 30）"),
        (B, K, "drive"): (50, "city 格 n=33（这条线 3）"),
        (B, M, "transit"): (90, "puma 格 n=30（这条线 30）"),
    }
    for cell, (minutes, level) in cases.items():
        g = legs[cell]
        assert g["roundtrip_minutes"] == minutes, cell
        assert g["source"]["inputs"][0].endswith(level), cell
    # 步行不用合并格；这条线没有任何一种方式；合并格也不够
    assert (A, K, "walk_bike") not in legs
    assert (B, N, "drive") not in legs
    leg_diag = city["diagnostics"]["legs"]
    assert f"{A}→{K}" in leg_diag["lines_without_any_mode"]
    assert leg_diag["cells_by_mode_and_level"]["drive"]["city"] == 1
    assert leg_diag["missing_reasons"]["drive: 合并格也不足 30 条"] == 1


def test_costs_on_each_leg(city):
    legs = legs_of(city)
    amount = {k: Decimal(repr(g["cost_per_roundtrip"]["amount"])) for k, g in legs.items()}
    assert amount[(A, M, "transit")] == Decimal("5.8")
    assert amount[(A, M, "walk_bike")] == 0
    assert amount[(A, M, "drive")] == Decimal("4.77")  # 往返 80 分钟
    assert amount[(B, K, "drive")] == Decimal("2.98")  # 往返 50 分钟
    assert {g["cost_per_roundtrip"]["numeraire"] for g in legs.values()} == {"USD_2024_LOCAL"}
    rel = CommuteRelation.model_validate(city["commute"])
    transit, drive = rel.mode_rule("transit"), rel.mode_rule("drive")
    assert transit.monthly_cap.amount == 132.0 and transit.requires_function is None
    assert transit.source.kind == "observed" and transit.source.provenance is not None
    assert drive.requires_function == "car_access" and drive.monthly_cap is None


def test_the_engine_rule_drives_only_with_car_access(city):
    rel = CommuteRelation.model_validate(city["commute"])
    line = [g for g in rel.legs if g.home_area.endswith(A) and g.work_area == M]
    assert choose_leg(line, 2).mode == "walk_bike"  # 30 < 40 < 80
    by_b = [g for g in rel.legs if g.home_area.endswith(B) and g.work_area == M]
    assert [g.mode for g in by_b] == ["transit"]
    by_d = [g for g in rel.legs if g.home_area.endswith(D) and g.work_area == K]
    assert [g.mode for g in by_d] == ["drive"]  # 没有用车权就没有可用方式（由结算报计划不可行）


def test_car_access_is_one_monthly_service(city):
    car = LifeOpportunity.model_validate(city["car_access"])
    assert car.opportunity_id == "nyc.service.car_access"
    assert car.functions == ("car_access",) and car.duration.kind == "ongoing_service"
    # 月费只取非燃油部分：(8,886 − 15,000 × 0.1112) ÷ 12 = 601.50；油费按每次开车往返另付（独立审核 F1）
    assert car.price.amount.amount == 601.5 and car.price.billing == "per_month"
    assert car.resource.basis == "per_service_month" and car.resource.hours == 0


def test_ids_follow_the_shared_life_pack_convention(city):
    ids = [a["area_id"] for a in city["areas"]]
    assert ids == [f"nyc.area.h.{A}", f"nyc.area.h.{D}", f"nyc.area.h.{B}", M, K, N, OTHER]
    assert city["car_access"]["opportunity_id"] == "nyc.service.car_access"
    assert city["home_pumas"] == {
        "included": [A, D, B],
        "excluded_outside_tax_scope": ["3400601", "3603102"],
        "excluded_partial_in_metro": ["3603101"],
    }


def test_work_area_job_shares_by_soc_major_group(city):
    # 全部：600 / 300 / 60 / 40；SOC 15 只有 A→曼哈顿的 30 人；SOC 43 = 570 / 300 / 60 / 40（合计 970）
    s = city["work_area_job_shares"]
    assert s["all"]["weight"] == 1000
    assert s["all"]["shares"] == {M: "0.6000", K: "0.3000", N: "0.0600", OTHER: "0.0400"}
    assert s["by_soc_major"]["15"]["shares"] == {
        M: "1.0000",
        K: "0.0000",
        N: "0.0000",
        OTHER: "0.0000",
    }
    assert s["by_soc_major"]["43"]["weight"] == 970
    assert s["by_soc_major"]["43"]["shares"] == {
        M: "0.5876",
        K: "0.3093",
        N: "0.0619",
        OTHER: "0.0412",
    }


def test_resident_diagnostics(city):
    d = city["diagnostics"]["resident_commuters"]
    assert d["outside_work_areas_weight_by_reason"] == {
        "outside_metro": 2,
        "partial_in_metro": 2,
        "work_state_not_supported": 6,
    }
    assert d["not_modeled_modes_weight"] == {"出租车或网约车": 4}
    assert city["status"] == "complete" and city["modes"] == ["walk_bike", "transit", "drive"]


def test_blocked_modes_are_left_out_not_replaced(costs):
    sf_like = copy.deepcopy(NYC)
    sf_like["key"] = "sf"  # 配置里旧金山的公交是阻断的
    p = build_city(CFG, sf_like, raw_inputs(), intake_facts(), costs).payload
    assert p["status"] == "partial" and "transit" in p["blocked"]
    assert {g["mode"] for g in p["commute"]["legs"]} == {"walk_bike", "drive"}
    no_gas = intake_facts()
    no_gas.gasoline.clear()
    p = build_city(CFG, NYC, raw_inputs(), no_gas, costs).payload
    assert p["car_access"] is None and "drive" in p["blocked"]
    assert "drive" not in {g["mode"] for g in p["commute"]["legs"]}


# --------------------------------------------------------------------------- 冻结文件


def test_frozen_files_round_trip_and_carry_a_content_identity(tmp_path, costs):
    results = compile_all(nyc_only(CFG), raw_inputs(), intake_facts(), costs)
    index = write_frozen(results, tmp_path)
    back = json.loads((tmp_path / "nyc.json").read_text(encoding="utf-8"))
    assert back == results[0].payload
    assert frozen_payload_problems(back) == []
    assert index["cities"]["nyc"]["content_identity"] == back["content_identity"]
    back["commute"]["legs"][0]["roundtrip_minutes"] += 2
    assert frozen_payload_problems(back) == ["内容身份对不上"]


def test_a_different_intake_build_is_refused(costs):
    other = intake_facts()
    other.build_id = "0" * 64
    with pytest.raises(RawChangedError):
        compile_all(nyc_only(CFG), raw_inputs(), other, costs)


def test_committed_frozen_files_are_consistent():
    index = json.loads((FROZEN_DIR / "index.json").read_text(encoding="utf-8"))
    assert sorted(index["cities"]) == sorted(c["key"] for c in CFG["cities"])
    for key, entry in index["cities"].items():
        payload = json.loads((FROZEN_DIR / f"{key}.json").read_text(encoding="utf-8"))
        assert frozen_payload_problems(payload) == [], key
        assert payload["content_identity"] == entry["content_identity"]
        assert payload["inputs"]["intake_build_id"] == CFG["intake"]["build_id"]


# --------------------------------------------------------------------------- 原始文件的读取


def test_read_commuters_keeps_employed_non_wfh_commuters():
    header = ",".join(
        [
            "SERIALNO",
            "STATE",
            "PUMA",
            "PWGTP",
            "ESR",
            "JWMNP",
            "JWTRNS",
            "POWSP",
            "POWPUMA",
            "SOCP",
        ]
    )
    lines = [
        header,
        "1,36,4101,12,1,25,3,036,4100,151252",  # 留下：在职、坐地铁
        "2,36,4101,12,1,,11,036,4100,151252",  # 在家工作
        "3,36,4101,12,3,30,1,036,4100,",  # 失业
        "4,36,4101,12,2,,,,,4110XX",  # 有工作但上周没上班
        "5,36,4101,12,1,40,1,034,600,",  # 留下：去新泽西开车（没有职业代码）
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("psam_p36.csv", "\n".join(lines) + "\n")
    df = read_commuters(buf.getvalue(), [1, 2])
    assert df["JWMNP"].tolist() == [25, 40]
    assert df["PUMA"].tolist() == ["04101", "04101"]
    assert df["POWSP"].tolist() == ["036", "034"] and df["POWPUMA"].tolist() == ["04100", "00600"]
    assert df["SOC2"].tolist() == ["15", ""]


def test_parse_powpuma_lookup():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Geography Lookup List"
    ws.append(["Table with row headers"])
    ws.append(["State FIPS Code (2-digit)", "County", "Name", "State", "PUMA", "POWSP", "POWPUMA"])
    ws.append(["36", "061", "New York County", "36", "04101", "036", "04100"])
    ws.append(["36", "061", "New York County", "36", "04102", "036", "04100"])
    ws.append(["36", "001", "Albany County", "36", "01900", "036", "01900"])
    ws.append(["36", "083", "Rensselaer County", "36", "01900", "036", "01900"])
    buf = io.BytesIO()
    wb.save(buf)
    lk = parse_powpuma_lookup(buf.getvalue(), "Geography Lookup List")
    assert lk.counties[("36", "04100")] == {"36061"}
    assert lk.counties[("36", "01900")] == {"36001", "36083"}
    assert lk.label(("36", "01900")) == "Albany County、Rensselaer County"


def test_read_intake(tmp_path):
    db = tmp_path / "intake.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        "create table meta (key text, value text);"
        "create table fetches (name text, json text);"
        "create table tables (table_id text, method text, cbsa text, json text);"
        "create table records (record_id text, sector text, record_type text, cbsa text, label text, json text);"
    )
    con.execute("insert into meta values ('build_id', ?)", ("e" * 64,))
    rec = FetchRecord(
        source_id="census_acs1_pums_2024",
        request_key="f" * 64,
        url="u",
        status="ok",
        raw_sha256="1" * 64,
    )
    con.execute("insert into fetches values ('pums_p:36', ?)", (rec.model_dump_json(),))
    for key, tier, med in ((A, "core", 20.0), (B, "middle", None)):
        t = {"level": "puma", "key": key, "tier": tier, "median_oneway": med}
        con.execute(
            "insert into tables values (?, 'commute_by_residence_puma@1', '35620', ?)",
            (key, json.dumps(t)),
        )
    con.execute(
        "insert into tables values ('t', 'commute_by_residence_puma@1', '35620', ?)",
        (json.dumps({"level": "tier", "key": "core", "tier": "core", "median_oneway": 25.0}),),
    )
    energy = {
        "fields": [
            {
                "name": "gasoline_regular_per_gallon",
                "status": "present",
                "value": "3.351",
                "numeraire": "USD_2024_LOCAL",
            }
        ],
        "source": {"locator": "ap area=S12A", "raw_sha256": "2" * 64},
    }
    con.execute(
        "insert into records values ('background.energy_prices.35620', 'background', 'city_fact_table', '35620', 'x', ?)",
        (json.dumps(energy),),
    )
    con.commit()
    con.close()
    f = read_intake(db)
    assert f.build_id == "e" * 64
    assert f.fetch_log["pums_p:36"].raw_sha256 == "1" * 64
    assert f.tiers == {"35620": {A: "core", B: "middle"}}
    assert f.medians == {"35620": {A: 20.0}}
    assert f.gasoline["35620"].value == Decimal("3.351")


def test_raw_files_are_read_only_with_the_recorded_fingerprint(tmp_path):
    req = Request("census_acs1_pums_2024", "https://example.invalid/csv_pny.zip", expect="any")
    rec = _store(tmp_path, req, b"original", 200, "application/zip")
    log = {"pums_p:36": rec}
    assert bound_raw("pums_p:36", req, tmp_path, log) == b"original"
    _store(tmp_path, req, b"refetched", 200, "application/zip")
    with pytest.raises(RawChangedError):
        bound_raw("pums_p:36", req, tmp_path, log)
    other = Request("census_acs1_pums_2024", "https://example.invalid/other.zip", expect="any")
    with pytest.raises(RawChangedError):
        bound_raw("pums_p:36", other, tmp_path, log)
