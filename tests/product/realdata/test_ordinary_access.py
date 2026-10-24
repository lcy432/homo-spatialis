"""普通住房代理 ``ordinary_rental_access_p10_v1``（E3B 收尾）：都会区 × 卧室组的毛租金加权阶梯 P10，低于门槛的退出普通可执行池。

手算口径：权重 = 支持点在前置合格池里的份额（精确分数）；P10 = 累计权重第一次达到 1/10 总权重的那个租金；等于门槛的保留，
严格低于的退出；独立来源记录按编号去重计数，不到 30 条回退到同城全部卧室，仍不足就阻断冻结。
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from product.realdata.life_pack import (
    ACCESS_RULE,
    Candidate,
    Pool,
    apply_ordinary_access,
    weighted_step_quantile,
)


def home(oid: str, bed: str, rent: str, share: Fraction, records: list[str]) -> Candidate:
    return Candidate(
        opportunity_id=oid,
        weight=float(share),
        area="t.area.h1",
        data={
            "cell": {"stratum": bed},
            "point": {"gross_rent": rent, "records": records},
            "share": share,
        },
    )


def recs(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i}" for i in range(n)]


def test_weighted_step_p10_by_hand():
    pairs = [
        (Decimal("100"), Fraction(1)),
        (Decimal("500"), Fraction(1)),
        (Decimal("900"), Fraction(8)),
    ]
    # 总权重 10，1/10 = 1：第一个租金的累计权重 1 就达到了 → 100
    assert weighted_step_quantile(pairs, Fraction(1, 10)) == Decimal("100")
    pairs = [
        (Decimal("100"), Fraction(1, 2)),
        (Decimal("500"), Fraction(1, 2)),
        (Decimal("900"), Fraction(9)),
    ]
    # 累计 0.5 < 1，累计 1.0 = 1 → 500（阶梯分位，不插值）
    assert weighted_step_quantile(pairs, Fraction(1, 10)) == Decimal("500")


def pool_with(cands: list[Candidate]) -> Pool:
    p = Pool(candidates=list(cands))
    p.masses["prequalified"] = sum((c.data["share"] for c in cands), Fraction(0))
    return p


def test_low_tail_leaves_the_pool_ties_stay_and_prices_are_unchanged():
    one = [
        home("a", "1", "50.00", Fraction(1, 20), recs("a", 10)),
        home("b", "1", "800.00", Fraction(1, 20), recs("b", 10)),
        home("c", "1", "800.00", Fraction(1, 10), recs("c", 10)),
        home("d", "1", "1200.00", Fraction(4, 5), recs("d", 10)),
    ]
    p = pool_with(one)
    apply_ordinary_access(p, "t")
    # 总权重 1：累计 0.05（50）、0.10（800）→ P10 = 800；等于 800 的 b、c 都保留
    g = p.access["groups"]["1"]
    assert g["threshold"] == "800.00" and g["level"] == "bedroom_group"
    assert [c.opportunity_id for c in p.candidates] == ["b", "c", "d"]
    assert p.masses["access_unverified_low_tail"] == Fraction(1, 20)
    assert p.masses["executable"] == Fraction(19, 20)
    assert g["min_rent_before"] == "50.00" and g["min_rent_after"] == "800.00"
    assert p.access["rule"] == ACCESS_RULE
    # 原始租金不抬价：留下的候选资料原样
    assert (
        next(c for c in p.candidates if c.opportunity_id == "b").data["point"]["gross_rent"]
        == "800.00"
    )


def test_records_are_counted_once_and_a_thin_group_falls_back_to_the_whole_city():
    shared = recs("s", 20)  # 同一批记录出现在两个支持点上：去重后只算 20 条
    thin = [
        home("x1", "0", "100.00", Fraction(1, 10), shared),
        home("x2", "0", "300.00", Fraction(1, 10), shared),
    ]
    thick = [
        home("y1", "2", "200.00", Fraction(1, 10), recs("y", 40)),
        home("y2", "2", "900.00", Fraction(7, 10), recs("z", 40)),
    ]
    p = pool_with(thin + thick)
    apply_ordinary_access(p, "t")
    g0, g2 = p.access["groups"]["0"], p.access["groups"]["2"]
    assert g0["independent_records"] == 20 and g0["level"] == "all_bedrooms"
    # 全城：租金排序 100(0.1) 200(0.1) 300(0.1) 900(0.7)，1/10 → 100；开间组用全城门槛 100
    assert p.access["city_threshold"] == "100.00" and g0["threshold"] == "100.00"
    # 两卧组自己 80 条：累计 0.1/0.8 = 1/8 ≥ 1/10 → 200
    assert g2["threshold"] == "200.00" and g2["independent_records"] == 80
    assert sorted(c.opportunity_id for c in p.candidates) == ["x1", "x2", "y1", "y2"]


def test_too_few_records_in_the_whole_city_blocks_the_freeze():
    p = pool_with([home("a", "1", "500.00", Fraction(1), recs("a", 29))])
    with pytest.raises(ValueError, match="阻断"):
        apply_ordinary_access(p, "t")


def test_minimum_menu_and_reference_housing_all_clear_the_same_threshold():
    """冻结资料包里每一处住房（菜单、最低住房、参考候选）的月租都不低于它所在卧室组的门槛：三种资格读同一个过滤后的池。"""
    import json
    from pathlib import Path

    pack = Path("data/frozen/life/us-v1/pack")
    report = json.loads((pack / "report.json").read_text(encoding="utf-8"))
    life = json.loads((pack / "life_input.json").read_text(encoding="utf-8"))
    checked = 0
    for cat in life["catalogs"]:
        homes = [o for o in cat["opportunities"] if o["category"] == "housing"]
        key = homes[0]["opportunity_id"].split(".", 1)[0]  # 编号以城市键开头，例如 chi.housing…
        groups = report["cities"][key]["pools"]["housing"]["ordinary_access"]["groups"]
        for o in homes:
            beds = int(o["spec"]["bedrooms"])
            threshold = Decimal(groups["3plus" if beds >= 3 else str(beds)]["threshold"])
            assert Decimal(str(o["price"]["amount"]["amount"])) >= threshold, o["opportunity_id"]
            checked += 1
    assert checked
