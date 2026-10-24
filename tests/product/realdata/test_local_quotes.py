"""E3B 收尾 §4：A 的本地报价接线与家庭宽带的回退。

* 报价走完整构建：真实小样本切片 + 临时报价清单 → 冻结 → 机会价格 → E1 年度结算，扣的是零售修正后的最终价；
* 报价清单的校验与去重（同编号同内容只算一次、同编号不同内容拒绝、负价拒绝、口径不同不混、观测时期按真实日历）；
* 基础价口径逐规格核实：仓库里没有核实过任何规格，任何报价都拒绝启用、价格不变；能更新的测试用口径写明的受控夹具
  （``CONTROLLED``，不是对真实基础价的核实）；
* FCC 原始资料缺失才回退到 CE 预算代理，可见文字跟着回退；文件在但坏了拒绝构建。

只读 ``tests/fixtures/life_items/`` 与临时目录，不联网、不读本机原始缓存。
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from fractions import Fraction
from pathlib import Path

import openpyxl
import pytest
import yaml

from common.life_contracts import CityLifeCatalog, YearAction, exact_minor_units
from product.realdata.a_blocks import QuoteError, base_basis_of, load_local_quotes
from product.realdata.daily_support_data import (
    CE_DETAIL_REQUEST,
    CE_SIZE_REQUEST,
    CPI_COMMUNICATION_REQUEST,
    FCC_METHOD_REQUEST,
    FCC_URS_REQUEST,
    DataError,
)
from product.realdata.fetch import _paths
from product.realdata.life_freeze import FreezeError, freeze
from product.realdata.life_inputs import SliceError, _ce_lines, _daily_support

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests/fixtures/life_items/slice_sample.json"
CHI, NYC = "16980", "35620"
MOBILE = "service.mobile_basic"
NET = "service.home_internet_basic"
#: 受控夹具：只在测试里把手机基础价的口径写明为“未含销售税、单独购买”，让同口径的报价能走完更新链。它不是对 CE 基础价的
#: 真实核实（CE 支出含销售税），仓库里的报价清单不登记任何规格。
CONTROLLED = {
    MOBILE: {
        "tax_basis": "before_sales_tax",
        "bundle": "standalone",
        "verified_by": "测试夹具：受控口径（tests/product/realdata/test_local_quotes.py），不是真实核实",
    }
}


def _yaml(rel: str) -> dict:
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfgs():
    items = _yaml("configs/intake/life_items_us_v1.yaml")
    occ = _yaml("configs/intake/occupations_us_v1.yaml")
    baskets = _yaml("configs/intake/us_v1.yaml")["mapping"]["a_food"]["baskets"]
    return items, occ, baskets


@pytest.fixture(scope="module")
def sl():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def quote(quote_id: str, amount: str, supplier: str = "carrier-a", **change) -> dict:
    return {
        "city": CHI,
        "quote_id": quote_id,
        "supplier": supplier,
        "spec": MOBILE,
        "amount": amount,
        "currency": "USD",
        "base_year": 2024,
        "period": "2024-06",
        "billing": "per_month",
        "per_unit": "service-month",
        "tax_basis": "before_sales_tax",
        "bundle": "standalone",
        "source": "测试用报价（不是真实运营商）",
        "locator": "tests/product/realdata/test_local_quotes.py",
        **change,
    }


def quotes_cfg(*quotes: dict, basis: dict | None = None) -> dict:
    """仓库的报价清单换上测试报价；``basis`` 给出基础价口径登记（默认是受控夹具，``{}`` = 仓库现状：没有核实过任何规格）。"""
    cfg = _yaml("configs/intake/local_quotes_us_v1.yaml")
    return {**cfg, "base_basis": CONTROLLED if basis is None else basis, "quotes": list(quotes)}


def frozen_city(out: Path, cbsa: str) -> dict:
    return json.loads((out / f"{cbsa}.json").read_text(encoding="utf-8"))


def item(body: dict, spec: str) -> dict:
    key = body["key"]
    return next(i for i in body["items"] if i["opportunity"]["opportunity_id"] == f"{key}.{spec}")


def support_family(body: dict) -> dict:
    block = next(b for b in body["a_blocks"] if b["block"] == "daily_support")
    return block["families"][0]["retail_correction"]


@pytest.fixture(scope="module")
def plain(tmp_path_factory, sl, cfgs):
    """没有报价（仓库里的空清单）时的冻结结果。"""
    out = tmp_path_factory.mktemp("plain")
    return out, freeze(sl, *cfgs, out)


@pytest.fixture(scope="module")
def quoted(tmp_path_factory, sl, cfgs):
    """芝加哥手机一家同规格报价 91.62 的冻结结果（基础价口径用受控夹具写明）。"""
    out = tmp_path_factory.mktemp("quoted")
    return out, freeze(sl, *cfgs, out, quotes=quotes_cfg(quote("chi-mobile-001", "91.62")))


# --------------------------------------------------------------------------- 报价穿过构建到 E1 账目


def test_the_repository_list_is_empty_and_enters_the_build_identity(plain, quoted):
    repo = _yaml("configs/intake/local_quotes_us_v1.yaml")
    assert (
        repo["quotes"] == [] and repo["base_basis"] == {}
    )  # 没有报价，也没有核实过任何规格的基础价口径
    assert plain[1]["identities"]["local_quotes"] != quoted[1]["identities"]["local_quotes"]
    assert plain[1]["content_identity"] != quoted[1]["content_identity"]
    # 只有芝加哥的内容变了；没有报价的城市逐字节不变（除了记录输入身份的 identities）
    assert plain[1]["cities"][NYC] == quoted[1]["cities"][NYC]
    assert (
        plain[1]["cities"][CHI]["content_identity"] != quoted[1]["cities"][CHI]["content_identity"]
    )
    old, new = frozen_city(plain[0], NYC), frozen_city(quoted[0], NYC)
    assert {k: v for k, v in old.items() if k != "identities"} == {
        k: v for k, v in new.items() if k != "identities"
    }
    assert support_family(old)["note_zh"] == "未发生本地报价更新"
    assert "spec_updates" not in support_family(old)


def test_one_quote_moves_the_frozen_price_by_the_registered_formula(plain, quoted):
    """手算：基础价 61.08，一家报 91.62 → δ = log 1.5；一家时观测方差 = 先验方差 = log(1.5)²，后验均值 = log 1.5 ÷ 2，
    执行价 = 61.08 × √1.5 = 74.807… → 74.81。"""
    base = item(frozen_city(plain[0], CHI), MOBILE)["opportunity"]
    assert base["price"]["amount"]["amount"] == 61.08
    body = frozen_city(quoted[0], CHI)
    o = item(body, MOBILE)["opportunity"]
    assert o["price"]["amount"] == {"amount": 74.81, "numeraire": "USD_2024_LOCAL"}
    assert round(61.08 * math.sqrt(1.5), 2) == 74.81
    assert o["attributes"]["cost_basis"] == "local_quote_retail_correction"
    assert o["attributes"]["base_cost_basis"] == "ce_one_person_budget_proxy"
    assert o["attributes"]["retail_base_price"] == "61.08"
    assert o["attributes"]["local_quote_suppliers"] == 1
    assert o["attributes"]["observed_unit_price"] is False
    assert "61.08 → 74.81" in o["quality"] and len(o["quality"]) <= 280
    assert (
        o["source"]["kind"] == "estimated" and "local_quote:chi-mobile-001" in o["source"]["inputs"]
    )
    assert o["source"]["rule"].startswith("retail_correction_lognormal_v1")
    assert {"retail_correction_lognormal_v1", "local_quote_base_basis_v1"} <= set(
        item(body, MOBILE)["assumptions"]
    )
    # 方案族汇总的记录就是写进机会的那一次修正：基础价、合格报价、先验与后验、最终价
    fam = support_family(body)
    assert fam["by_status"] == {"no_local_quotes": 2, "updated": 1}
    r = fam["spec_updates"][MOBILE]
    assert (r["p_base"], r["execution_price"], r["final_price"]) == ("61.08", "74.81", "74.81")
    assert r["status"] == "updated" and r["suppliers"] == 1
    assert r["comparable_quote_ids"] == ["chi-mobile-001"] and r["not_comparable"] == []
    assert (
        r["basis"]["tax_basis"] == "before_sales_tax" and "测试夹具" in r["base_basis_verified_by"]
    )
    assert r["posterior"]["mean"] == pytest.approx(math.log(1.5) / 2)
    assert r["prior"]["sd"] == pytest.approx(math.log(1.5))
    # 其余两项没有报价：价格不变
    for spec in (NET, "service.household_supplies_basic"):
        assert item(body, spec) == item(frozen_city(plain[0], CHI), spec)


def test_e1_settles_the_frozen_final_price(quoted):
    """用冻结出来的芝加哥手机机会（只换成样例城市 K51 的编号）跑一年 E1 结算：每月扣 74.81，全年 897.72。"""
    from econ.life_settlement import binding_from_policy, settle_year
    from ledger.life_catalog import load_life_bundle

    bundle, _ = load_life_bundle(
        REPO / "configs/life/synthetic_product.yaml", require_settlement_ready=True
    )
    k51 = "US:SYNTH2026:K51"
    frozen = item(frozen_city(quoted[0], CHI), MOBILE)["opportunity"]
    data = bundle.catalog_for(k51).model_dump(mode="json")
    data["opportunities"] = [
        {**frozen, "opportunity_id": "k51.service.mobile_basic"}
        if o["opportunity_id"] == "k51.service.mobile_basic"
        else o
        for o in data["opportunities"]
    ]
    catalog = CityLifeCatalog.model_validate(data)
    state = bundle.initial_state("u301.k51.r0")

    def settle(extra: list[dict]):
        whole = [
            {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
            for oid in ("k51.housing.far", "k51.food.basic", "k51.job.clerk", *extra)
        ]
        action = YearAction.model_validate(
            {
                "person_id": state.person_id,
                "clone_id": state.clone_id,
                "year_index": state.year_index,
                "items": whole,
                "minimum_fallback": ["housing", "food"],
                "emergency": {"minor": "decline", "major": "decline", "pause_order": []},
                "rationale": "测试：预先写好的计划",
            }
        )
        return settle_year(
            protocol=bundle.protocol,
            state=state,
            action=action,
            catalog=catalog,
            tax=binding_from_policy(bundle.tax_policy(catalog.tax_policy_id)),
        )

    with_phone, without = settle(["k51.service.mobile_basic"]), settle([])
    assert with_phone.status == without.status == "completed"
    paid = next(
        i for i in with_phone.outcome.items if i.opportunity_id == "k51.service.mobile_basic"
    )
    assert exact_minor_units(paid.amount_paid.amount, 2) == 12 * 7481
    for a, b in zip(with_phone.outcome.months, without.outcome.months, strict=True):
        assert exact_minor_units(a.spent.amount, 2) - exact_minor_units(b.spent.amount, 2) == 7481


# --------------------------------------------------------------------------- 去重、同一供应商、冲突、口径


def _mobile_record(sl, cfgs, tmp_path, *quotes) -> tuple[dict, dict]:
    freeze(sl, *cfgs, tmp_path, quotes=quotes_cfg(*quotes))
    body = frozen_city(tmp_path, CHI)
    return item(body, MOBILE)["opportunity"], support_family(body)


def test_repeating_the_same_quote_changes_nothing(sl, cfgs, quoted, tmp_path):
    o, fam = _mobile_record(
        sl, cfgs, tmp_path, quote("chi-mobile-001", "91.62"), quote("chi-mobile-001", "91.62")
    )
    once = frozen_city(quoted[0], CHI)
    assert o == item(once, MOBILE)["opportunity"]
    assert fam == support_family(once)
    assert load_local_quotes(
        quotes_cfg(quote("chi-mobile-001", "91.62"), quote("chi-mobile-001", "91.62"))
    ) == load_local_quotes(quotes_cfg(quote("chi-mobile-001", "91.62")))


def test_several_real_quotes_from_one_supplier_are_one_observation(sl, cfgs, tmp_path):
    """同一家两条真实报价（不同编号）：每家只给一个对数比值（两条取平均），仍按一家的宽方差更新。
    手算：δ = (log(80/61.08) + log(103.86/61.08)) ÷ 2，后验均值 = δ ÷ 2。"""
    o, fam = _mobile_record(
        sl, cfgs, tmp_path, quote("chi-mobile-001", "80.00"), quote("chi-mobile-002", "103.86")
    )
    r = fam["spec_updates"][MOBILE]
    assert r["suppliers"] == 1 and r["comparable_quote_ids"] == ["chi-mobile-001", "chi-mobile-002"]
    delta = (math.log(80 / 61.08) + math.log(103.86 / 61.08)) / 2
    assert r["observation"]["variance"] == pytest.approx(math.log(1.5) ** 2)
    assert r["posterior"]["mean"] == pytest.approx(delta / 2)
    want = round(61.08 * math.exp(delta / 2), 2)
    assert o["price"]["amount"]["amount"] == want == float(r["final_price"])


def test_a_conflicting_quote_id_negative_price_or_bad_field_is_refused(sl, cfgs):
    with pytest.raises(QuoteError, match="出现两次且内容不同"):
        load_local_quotes(quotes_cfg(quote("q1", "91.62"), quote("q1", "91.63")))
    with pytest.raises(FreezeError, match="出现两次且内容不同"):
        freeze(sl, *cfgs, quotes=quotes_cfg(quote("q1", "91.62"), quote("q1", "91.63")))
    with pytest.raises(QuoteError, match="负价"):
        load_local_quotes(quotes_cfg(quote("q1", "-1.00")))
    with pytest.raises(QuoteError, match="精确到分"):
        load_local_quotes(quotes_cfg(quote("q1", "91.625")))
    with pytest.raises(QuoteError, match="不认识的字段"):
        load_local_quotes(quotes_cfg({**quote("q1", "91.62"), "discount": "10%"}))
    with pytest.raises(QuoteError, match="缺字段"):
        load_local_quotes(
            quotes_cfg({k: v for k, v in quote("q1", "91.62").items() if k != "locator"})
        )
    with pytest.raises(QuoteError, match="年份要等于 base_year"):
        load_local_quotes(quotes_cfg(quote("q1", "91.62", period="2025-01")))
    with pytest.raises(QuoteError, match="不在登记的取值里"):
        load_local_quotes(quotes_cfg(quote("q1", "91.62", tax_basis="maybe")))
    # 同一个编号在不同城市是两条报价
    two = load_local_quotes(quotes_cfg(quote("q1", "91.62"), quote("q1", "91.62", city=NYC)))
    assert set(two) == {CHI, NYC}


def test_quotes_that_cannot_attach_to_a_correctable_opportunity_are_refused(sl, cfgs):
    with pytest.raises(FreezeError, match="本城没有这个机会"):
        freeze(sl, *cfgs, quotes=quotes_cfg(quote("q1", "91.62", spec="service.mobile_premium")))
    with pytest.raises(FreezeError, match="不在这次构建里的城市"):
        freeze(sl, *cfgs, quotes=quotes_cfg(quote("q1", "91.62", city="26420")))


def test_a_park_program_keeps_its_direct_quote(plain, sl, cfgs):
    """芝加哥公园整期报名费本身就是直接报价：原样保留，不接受零售修正。"""
    programs = json.loads((plain[0] / f"{CHI}.programs.json").read_text(encoding="utf-8"))["items"]
    oid = next(i["opportunity"]["opportunity_id"] for i in programs if i["opportunity"]["price"])
    spec = oid.split(".", 1)[1]
    p = next(i for i in programs if i["opportunity"]["opportunity_id"] == oid)["opportunity"][
        "price"
    ]
    with pytest.raises(FreezeError, match="本身就是直接报价"):
        freeze(
            sl,
            *cfgs,
            quotes=quotes_cfg(
                quote("q1", "10.00", spec=spec, billing=p["billing"], per_unit=p["per_unit"])
            ),
        )
    fam = next(
        f["retail_correction"]
        for b in frozen_city(plain[0], CHI)["a_blocks"]
        for f in b["families"]
        if f["retail_correction"]["by_status"].get("direct_quotes_kept")
    )
    assert fam["note_zh"] == "价格本身就是直接报价，原样保留，不做修正"


def test_other_bases_and_zero_prices_do_not_update(sl, cfgs, plain, tmp_path):
    """基础价口径登记为“未含销售税、单独购买”时：含税、捆绑、按年计费或别的计价口径的报价只记录哪一项不同、不参与；
    零价单独处理、不取对数。都不改价格。"""
    o, fam = _mobile_record(
        sl,
        cfgs,
        tmp_path,
        quote("tax", "91.62", tax_basis="including_sales_tax"),
        quote("bundle", "91.62", supplier="carrier-b", bundle="bundled"),
        quote("year", "900.00", supplier="carrier-c", billing="per_year"),
        quote("y25", "91.62", supplier="carrier-d", base_year=2025, period="2025-02"),
        quote("free", "0.00", supplier="carrier-e"),
    )
    assert o == item(frozen_city(plain[0], CHI), MOBILE)["opportunity"]
    r = fam["spec_updates"][MOBILE]
    assert r["status"] == "no_local_quotes" and r["final_price"] == r["p_base"] == "61.08"
    assert r["zero_price_quotes"] == 1 and r["comparable_quote_ids"] == ["free"]
    assert {x["quote_id"]: x["differs_in"] for x in r["not_comparable"]} == {
        "bundle": ["bundle"],
        "tax": ["tax_basis"],
        "y25": ["numeraire"],
        "year": ["billing"],
    }


def test_an_unverified_base_basis_refuses_the_quote_and_keeps_the_price(sl, cfgs, plain, tmp_path):
    """仓库现状：没有核实过任何规格的基础价口径。一条口径看起来“合适”的报价也拒绝启用，价格逐字节不变，记录写明原因。"""
    freeze(sl, *cfgs, tmp_path, quotes=quotes_cfg(quote("chi-mobile-001", "91.62"), basis={}))
    body = frozen_city(tmp_path, CHI)
    assert item(body, MOBILE) == item(
        frozen_city(plain[0], CHI), MOBILE
    )  # 价格、来源、设定登记都不变
    r = support_family(body)["spec_updates"][MOBILE]
    assert r["status"] == "base_basis_unverified" and r["refused_quote_ids"] == ["chi-mobile-001"]
    assert r["final_price"] == r["p_base"] == "61.08" and r["comparable_quote_ids"] == []
    assert "没有逐规格核实" in r["note_zh"] and "不猜税费" in r["note_zh"]
    assert r["basis_known"] == {
        "numeraire": "USD_2024_LOCAL",
        "billing": "per_month",
        "per_unit": "service-month",
    }


def test_a_registered_basis_that_differs_from_the_quote_is_not_used(sl, cfgs, plain, tmp_path):
    """登记的基础价是含税的，报价是未含税的：不做换算，只记录不同的那一项，价格不变。"""
    taxed = {MOBILE: {**CONTROLLED[MOBILE], "tax_basis": "including_sales_tax"}}
    freeze(sl, *cfgs, tmp_path, quotes=quotes_cfg(quote("chi-mobile-001", "91.62"), basis=taxed))
    body = frozen_city(tmp_path, CHI)
    assert (
        item(body, MOBILE)["opportunity"] == item(frozen_city(plain[0], CHI), MOBILE)["opportunity"]
    )
    r = support_family(body)["spec_updates"][MOBILE]
    assert r["status"] == "no_local_quotes" and r["final_price"] == "61.08"
    assert r["not_comparable"] == [{"quote_id": "chi-mobile-001", "differs_in": ["tax_basis"]}]


def test_the_base_basis_register_needs_registered_values_and_a_verification():
    ok = base_basis_of(quotes_cfg())
    assert ok == CONTROLLED
    with pytest.raises(QuoteError, match="恰好写"):
        base_basis_of(
            quotes_cfg(basis={MOBILE: {"tax_basis": "before_sales_tax", "bundle": "standalone"}})
        )
    with pytest.raises(QuoteError, match="不在登记的取值里"):
        base_basis_of(quotes_cfg(basis={MOBILE: {**CONTROLLED[MOBILE], "tax_basis": "probably"}}))
    with pytest.raises(QuoteError, match="核实依据"):
        base_basis_of(quotes_cfg(basis={MOBILE: {**CONTROLLED[MOBILE], "verified_by": " "}}))
    with pytest.raises(QuoteError, match="要是映射"):
        base_basis_of({**quotes_cfg(), "base_basis": None})


@pytest.mark.parametrize("period", ["2024", "2024-06", "2024-12-31", "2024-02-29"])
def test_real_calendar_periods_are_accepted(period):
    (q,) = load_local_quotes(quotes_cfg(quote("q1", "91.62", period=period)))[CHI]
    assert q.period == period


@pytest.mark.parametrize(
    ("period", "base_year", "message"),
    [
        ("2024-99-99", 2024, "不是真实的日历日期"),
        ("2024-02-30", 2024, "不是真实的日历日期"),
        ("2024-13", 2024, "不是真实的日历日期"),
        ("2024-00", 2024, "不是真实的日历日期"),
        ("2023-02-29", 2023, "不是真实的日历日期"),  # 2023 不是闰年
        ("2024-6", 2024, "YYYY、YYYY-MM 或 YYYY-MM-DD"),
        ("2024/06", 2024, "YYYY、YYYY-MM 或 YYYY-MM-DD"),
        ("2025-01-15", 2024, "年份要等于 base_year"),
    ],
)
def test_impossible_or_mismatched_periods_are_refused(period, base_year, message):
    with pytest.raises(QuoteError, match=message):
        load_local_quotes(quotes_cfg(quote("q1", "91.62", period=period, base_year=base_year)))


# --------------------------------------------------------------------------- 家庭宽带的回退


def _without(sl: dict, key: str) -> dict:
    out = copy.deepcopy(sl)
    block = out["daily_support"][key]
    out["daily_support"][key] = {
        **({"series": block["series"]} if key == "cpi_internet" else {}),
        "status": "unavailable",
        "reason_zh": "测试：原始文件未获取",
    }
    return out


@pytest.mark.parametrize("missing", ["fcc", "cpi_internet"])
def test_missing_fcc_or_cpi_falls_back_without_claiming_a_speed_tier(missing, sl, cfgs, tmp_path):
    """FCC 原始资料或换算用的 CPI 不可得：家庭宽带 = CE 一人“其他家庭开支”× 全国详细表互联网服务占比 ÷ 12，不乘 CPI；
    名称、说明、属性、来源与设定都换成回退这一支，不再写 100–199 Mbps、上行 20、不限流量。"""
    freeze(_without(sl, missing), *cfgs, tmp_path)
    ce = sl["daily_support"]["ce"]
    share = Fraction(
        ce["detail_all_units"]["Computer information services (internet) [I]"]
    ) / Fraction(ce["detail_all_units"]["Other household expenses"])
    want = round(float(Fraction(ce["one_person"]["Other household expenses"]) * share / 12), 2)
    for cbsa in (NYC, CHI, "41860"):
        body = frozen_city(tmp_path, cbsa)
        net = item(body, NET)
        o = net["opportunity"]
        assert o["price"]["amount"]["amount"] == want
        text = json.dumps(o, ensure_ascii=False)
        for claim in (
            "Mbps",
            "100–199",
            "上行",
            "不限流量",
            "FCC 2024 城市费率调查同规格",
            "fcc_urs",
        ):
            assert claim not in text.replace("FCC 同规格参照不能用", ""), claim
        assert o["label"] == "家庭宽带（按平均支出估计）"
        assert o["attributes"] == {
            "cost_basis": "ce_allocated_budget_proxy",
            "observed_unit_price": False,
            "price_geography": "national",
            "ce_annual_one_person": o["attributes"]["ce_annual_one_person"],
        }
        assert "不乘 CPI" in o["source"]["rule"] and not any(
            "cpi" in x.lower() for x in o["source"]["inputs"]
        )
        assert "ce_internet_budget_proxy_v1" in net["assumptions"]
        assert "fcc_broadband_basic_tier_v1" not in net["assumptions"]
        assert any(
            e["key"] == "home_connectivity" and "测试：原始文件未获取" in e["reason_zh"]
            for e in body["not_generated"]
        )


def test_with_fcc_and_cpi_the_regional_reference_is_unchanged(plain, sl):
    o = item(frozen_city(plain[0], NYC), NET)["opportunity"]
    assert o["attributes"]["cost_basis"] == "fcc_regional_reference"
    assert o["attributes"]["download_mbps"] == "100–199"
    assert o["label"] == "家庭宽带（基本档）"


# --------------------------------------------------------------------------- 切片阶段：缺失可以回退，坏文件拒绝


def _xlsx(rows: list[list], sheet: str | None = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    if sheet:
        ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _put(cache: Path, req, content: bytes, sha: str | None = None) -> None:
    data, meta = _paths(cache, req)
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_bytes(content)
    meta.write_text(
        json.dumps({"sha256": sha or hashlib.sha256(content).hexdigest()}), encoding="utf-8"
    )


FCC_HEAD = [
    "State",
    "Weight",
    "Download Bandwidth Mbps",
    "Upload Bandwidth Mbps",
    "Usage Allowance GB",
    "Total Charge",
]


@pytest.fixture
def ce_cache(tmp_path, cfgs):
    """只有 CE 两张表的临时缓存（数是测试用的，不是 CE 的真实值）。"""
    lines = _ce_lines(cfgs[0])
    size = sorted({x["size_label"] for x in lines})
    detail = sorted(
        {x["detail_parent"] for x in lines if x.get("detail_items")}
        | {i for x in lines for i in x.get("detail_items", ())}
    )
    _put(
        tmp_path,
        CE_SIZE_REQUEST,
        _xlsx([["Table 1400 (test)"], *(r for k in size for r in ([k], ["Mean", 1200, 600]))]),
    )
    _put(
        tmp_path,
        CE_DETAIL_REQUEST,
        _xlsx([["Table 2500 (test)"], *(r for k in detail for r in ([k], ["Mean", 300]))]),
    )
    return tmp_path


def _cpi(cache: Path) -> None:
    text = "series_id\tyear\tperiod\tvalue\nCUUR0000SEEE03\t2023\tM13\t100.0\nCUUR0000SEEE03\t2024\tM13\t102.0\n"
    _put(cache, CPI_COMMUNICATION_REQUEST, text.encode("utf-8"))


def test_absent_fcc_or_cpi_files_are_marked_unavailable(ce_cache, cfgs):
    out, raw = _daily_support(cfgs[0], ce_cache)
    assert out["fcc"]["status"] == out["cpi_internet"]["status"] == "unavailable"
    assert set(raw) == {"bls_ce_national_2024_size", "bls_ce_national_2024_detail"}
    _cpi(ce_cache)
    out, raw = _daily_support(cfgs[0], ce_cache)
    assert out["fcc"]["status"] == "unavailable"
    assert out["cpi_internet"]["annual"] == {"2023": "100.0", "2024": "102.0"}
    assert set(raw) == {
        "bls_ce_national_2024_size",
        "bls_ce_national_2024_detail",
        "bls_cpi_u_education_communication",
    }


def test_all_files_present_keep_the_old_slice_shape(ce_cache, cfgs):
    _cpi(ce_cache)
    rows = [FCC_HEAD, *(["ILLINOIS", 1.0, 150, 20, "Inf", 60 + i] for i in range(3))]
    _put(ce_cache, FCC_URS_REQUEST, _xlsx(rows, "Data"))
    _put(ce_cache, FCC_METHOD_REQUEST, b"%PDF-1.4 test")
    out, raw = _daily_support(cfgs[0], ce_cache)
    assert "status" not in out["fcc"] and out["fcc"]["by_region"]["Midwest"]["offers"] == 3
    assert "status" not in out["cpi_internet"]
    assert list(raw) == [
        "bls_ce_national_2024_size",
        "bls_ce_national_2024_detail",
        "fcc_urs_2024_broadband",
        "fcc_urs_2024_methodology",
        "bls_cpi_u_education_communication",
    ]


def test_present_but_broken_fcc_files_refuse_the_build(ce_cache, cfgs):
    _cpi(ce_cache)
    good = _xlsx([FCC_HEAD, ["ILLINOIS", 1.0, 150, 20, "Inf", 60]], "Data")
    # 指纹不符
    _put(ce_cache, FCC_URS_REQUEST, good, sha="0" * 64)
    with pytest.raises(SliceError, match="不当作资料缺失回退"):
        _daily_support(cfgs[0], ce_cache)
    # 不是 xlsx（例如一页网页）
    _put(ce_cache, FCC_URS_REQUEST, b"<html>blocked</html>")
    with pytest.raises(SliceError, match="不当作资料缺失回退"):
        _daily_support(cfgs[0], ce_cache)
    # 是 xlsx 但格式不对（缺列）
    _put(ce_cache, FCC_URS_REQUEST, _xlsx([FCC_HEAD[:3], ["ILLINOIS", 1.0, 150]], "Data"))
    with pytest.raises(DataError, match="缺列"):
        _daily_support(cfgs[0], ce_cache)
    # CPI 文件在但指纹不符：同样拒绝，不回退
    _put(ce_cache, FCC_URS_REQUEST, good)
    _put(ce_cache, CPI_COMMUNICATION_REQUEST, b"series_id\n", sha="1" * 64)
    with pytest.raises(SliceError, match="不当作资料缺失回退"):
        _daily_support(cfgs[0], ce_cache)
