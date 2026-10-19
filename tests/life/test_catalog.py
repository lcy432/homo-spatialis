"""加载与交叉校验（E1-1，`DECISIONS/0016`）。

拒绝类测试都从合法样例出发、只改坏一处，确认加载器**明确**拒绝、并说出是哪一处。
"""

from __future__ import annotations

import inspect

import pytest

import ledger.life_catalog as life_catalog
from common.life_contracts import LifeContractError
from ledger.life_catalog import cross_validate, dump_bundle, parse_bundle

from .conftest import catalog, opportunity


def _errors(raw: dict) -> tuple[str, ...]:
    return cross_validate(parse_bundle(raw)).errors


# --------------------------------------------------------------------------
# 合法样例：加载、稳定序列化、什么都不丢
# --------------------------------------------------------------------------


def test_the_fixture_loads_and_is_structurally_valid(bundle):
    r = cross_validate(bundle)
    assert r.contract_valid, r.errors
    assert r.scoring_ready is False
    assert r.protocol_status == "synthetic_fixture_only"
    assert set(r.summary["countries"]) == {"US", "ZZ"}


def test_serialisation_is_stable_and_round_trips(bundle):
    once = dump_bundle(bundle)
    again = dump_bundle(parse_bundle(once))
    assert once == again
    assert parse_bundle(once) == bundle


def test_country_units_prices_and_availability_survive_the_round_trip(bundle):
    back = parse_bundle(dump_bundle(bundle))
    zz = back.catalog_for("ZZ:SYNTHGEO:A01")
    assert zz is not None and zz.city.country == "ZZ"
    assert zz.money_basis.numeraire_id == "XTS_2024_LOCAL"
    flat = zz.opportunity("zz.housing.flat")
    assert flat.price.amount.amount == 31000 and flat.price.amount.numeraire == "XTS_2024_LOCAL"
    assert flat.unit == "dwelling" and flat.price.billing == "per_month"
    a01 = back.catalog_for("US:SYNTH2026:A01")
    b02 = back.catalog_for("US:SYNTH2026:B02")
    assert a01.opportunity("a01.food.meal_kit").availability == "unavailable"
    assert b02.opportunity("b02.durable.washer").availability == "unknown"
    assert b02.opportunity("b02.durable.washer").price is None


# --------------------------------------------------------------------------
# 异质性被完整保留——但不假装已经验证排序
# --------------------------------------------------------------------------


def test_two_cities_keep_their_different_relative_prices(bundle):
    """A01 住房便宜食品贵、B02 反过来。结构必须原样保留这层差异。"""

    def monthly(city, oid):
        return bundle.catalog_for(city).opportunity(oid).price.amount.amount

    a_ratio = monthly("US:SYNTH2026:A01", "a01.housing.two_bed") / monthly(
        "US:SYNTH2026:A01", "a01.food.fresh_groceries"
    )
    b_ratio = monthly("US:SYNTH2026:B02", "b02.housing.two_bed") / monthly(
        "US:SYNTH2026:B02", "b02.food.fresh_groceries"
    )
    assert b_ratio > a_ratio * 2, "两城的住房/食品相对价格必须真的不同"


def test_two_people_keep_their_different_preferences(bundle):
    by = {p.person_id: p for p in bundle.stated_profiles}
    u01 = {s.need: s.stated_strength for s in by["u01"].soft_preferences}
    u02 = {s.need: s.stated_strength for s in by["u02"].soft_preferences}
    assert u01["housing_space"] == "high" and u02["housing_space"] == "low"
    assert u01["fresh_food"] == "low" and u02["fresh_food"] == "high"
    truths = {t.person_id: t for t in bundle.internal_truths}
    assert truths["u01"].material_subweights != truths["u02"].material_subweights


def test_the_loader_produces_no_score_or_ranking(bundle):
    """本轮不评分：报告里不能出现任何得分或排序字段。"""
    # 查**结构**而不是全文：报告里出现 "score_mapping" 这个未定政策的名字是正常的，
    # 全文搜索会把它误判成得分字段。
    from ledger.life_catalog import ContractReport

    r = cross_validate(bundle)
    fields = set(ContractReport.model_fields) | set(r.summary)
    for word in ("rank", "score", "best", "utility", "welfare"):
        hits = {f for f in fields if word in f and f != "scoring_ready"}
        assert not hits, f"报告里出现了得分/排序字段 {hits}"


# --------------------------------------------------------------------------
# 明确拒绝
# --------------------------------------------------------------------------


def test_an_unknown_top_level_field_is_rejected(raw):
    raw["winning_city"] = "US:SYNTH2026:A01"
    with pytest.raises(LifeContractError, match="winning_city"):
        parse_bundle(raw)


def test_a_duplicate_opportunity_id_is_rejected(raw):
    a01 = catalog(raw, "A01")
    a01["opportunities"].append(dict(a01["opportunities"][0]))
    with pytest.raises(LifeContractError, match="重复"):
        parse_bundle(raw)


def test_an_undeclared_category_is_rejected(raw):
    opportunity(raw, "a01.food.convenience")["category"] = "luxury"
    assert any("luxury" in e for e in _errors(raw))


def test_a_state_in_a_missing_city_is_rejected(raw):
    raw["initial_states"][0]["city"]["code"] = "Q99"
    assert any("不存在的城市" in e for e in _errors(raw))


def test_an_action_on_a_missing_opportunity_is_rejected(raw):
    raw["example_actions"][0]["items"][0]["opportunity_id"] = "a01.housing.penthouse"
    assert any("不存在的机会" in e for e in _errors(raw))


def test_an_action_outside_the_allowed_arrangement_is_rejected(raw):
    raw["example_actions"][0]["items"][0]["arrangement"] = {"lease_months": 36}
    assert any("不在允许值" in e for e in _errors(raw))
    raw["example_actions"][0]["items"][0]["arrangement"] = {"rent_discount": 0.5}
    assert any("不在该机会允许的选项" in e for e in _errors(raw))


def test_a_holding_longer_than_the_item_lasts_is_rejected(raw):
    s = next(s for s in raw["initial_states"] if s["clone_id"] == "u02.a01.c0")
    s["holdings"][0]["remaining_years"] = 12
    assert any("超过物品期限" in e for e in _errors(raw))


def test_a_one_time_experience_cannot_be_carried_across_years(raw):
    s = next(s for s in raw["initial_states"] if s["clone_id"] == "u01.a01.c0")
    s["holdings"] = [
        {"opportunity_id": "a01.experience.festival", "quantity": 1, "acquired_year": 0}
    ]
    assert any("一次体验" in e for e in _errors(raw))


def test_an_account_in_the_wrong_basis_for_its_city_is_rejected(raw):
    """美国城市里的分身不能用 XTS 记账——口径跟城市走。"""
    s = next(s for s in raw["initial_states"] if s["clone_id"] == "u01.a01.c0")
    s["money_basis_id"] = "XTS_2024_LOCAL"
    s["cash"]["numeraire"] = "XTS_2024_LOCAL"
    assert any("账户口径" in e for e in _errors(raw))


def test_a_quote_in_a_different_basis_from_its_catalog_is_rejected(raw):
    opportunity(raw, "a01.food.convenience")["price"]["amount"]["numeraire"] = "XTS_2024_LOCAL"
    with pytest.raises(LifeContractError, match="不同口径的钱不能进同一本账"):
        parse_bundle(raw)


def test_a_person_with_only_one_side_is_rejected(raw):
    raw["internal_truths"] = [t for t in raw["internal_truths"] if t["person_id"] != "u02"]
    assert any("只在陈述与真值的一边" in e for e in _errors(raw))


def test_a_strict_load_raises_on_cross_errors(tmp_path, raw):
    import yaml

    raw["example_actions"][0]["items"][0]["opportunity_id"] = "a01.nope"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(LifeContractError, match="交叉校验失败"):
        life_catalog.load_life_bundle(p)
    _, report = life_catalog.load_life_bundle(p, strict=False)
    assert not report.contract_valid


def test_an_unknown_item_is_never_turned_into_a_zero_price(raw):
    opportunity(raw, "b02.durable.washer")["price"] = {
        "amount": {"amount": 0, "numeraire": "USD_2024_LOCAL"},
        "billing": "one_time",
        "per_unit": "appliance",
    }
    with pytest.raises(LifeContractError, match="未知不是零价"):
        parse_bundle(raw)


def test_dropping_a_known_unavailable_item_is_visible(bundle, raw):
    """确定缺货被删掉，可得状态的统计就会变——这条限制不会被悄悄抹掉。"""
    before = cross_validate(bundle).summary["availability"]
    a01 = catalog(raw, "A01")
    a01["opportunities"] = [o for o in a01["opportunities"] if o["availability"] != "unavailable"]
    after = cross_validate(parse_bundle(raw)).summary["availability"]
    assert before.get("unavailable") == 1 and after.get("unavailable") is None


# --------------------------------------------------------------------------
# 第二国家：同一加载入口，没有国家分支
# --------------------------------------------------------------------------


def test_the_second_country_goes_through_the_same_loader(bundle):
    zz = bundle.catalog_for("ZZ:SYNTHGEO:A01")
    assert zz.jurisdiction.country == "ZZ"
    assert zz.money_basis.currency == "XTS"
    clone = next(s for s in bundle.initial_states if s.clone_id == "u01.zz.c0")
    assert clone.money_basis_id == "XTS_2024_LOCAL"


def test_the_loader_has_no_country_name_branch():
    """加载器不认识任何国家的名字。出现 "US"/"ZZ" 字面量就说明有人开始写分支了。"""
    src = inspect.getsource(life_catalog)
    for literal in ('"US"', "'US'", '"ZZ"', "'ZZ'", '"XTS"', '"USD"'):
        assert literal not in src, f"加载器里出现了国家/币种字面量 {literal}"


def test_city_identity_is_the_full_triple_not_the_code(bundle):
    """US 与 ZZ 各有一个 code 为 A01 的城市；按完整身份区分，不撞号。"""
    a = bundle.catalog_for("US:SYNTH2026:A01")
    z = bundle.catalog_for("ZZ:SYNTHGEO:A01")
    assert a is not None and z is not None and a is not z
    assert a.city.code == z.city.code == "A01"
    assert any("code 'A01'" in n for n in cross_validate(bundle).notes)


def test_two_bases_are_readable_but_flagged_as_not_comparable(bundle):
    r = cross_validate(bundle)
    assert len(r.summary["money_bases"]) == 2
    assert any("不能直接比较" in n for n in r.notes)


def test_a_comparability_grade_is_not_a_permanent_item_property():
    """A/B/C 是对一次比较集合的判断，不能挂在商品上。"""
    from common.life_contracts import CityLifeCatalog, LifeOpportunity

    for model in (LifeOpportunity, CityLifeCatalog):
        assert not any("grade" in f for f in model.model_fields), model.__name__


def test_a_training_longer_than_the_horizon_is_rejected_by_the_loader(raw):
    """培训年数"不超过 N"依赖协议，E1-2 起从字段校验移到加载器（字段上只剩防呆上限）。

    这不是放宽：N = 3 时 5 年的培训照样被拒；N 改成 5，结论跟着变。
    """
    opportunity(raw, "a01.training.bookkeeping")["duration"]["completion_years"] = 5
    assert any("超出协议的 N = 3" in e for e in _errors(raw))
    raw["protocol"]["simulation_years"] = 5
    assert not any("超出协议的 N" in e for e in _errors(raw))
