"""E3-A 模块 D：便利资料候选 → 合法 E1 机会；寿命、效果、报价缺失各有明确状态；排除之后仍保留分母；选项目录覆盖与轨迹能否评分分开。"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
import yaml

from common.life_contracts import LifeOpportunity
from product import scenarios as sc
from product.catalog import compile_catalog, inventory_coverage
from product.fixtures import K51, M52, prepare_workspace
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    work = prepare_workspace(tmp_path_factory.mktemp("ws"), REPO)
    with contextlib.chdir(work):
        pack = load_pack()
        life = yaml.safe_load(Path(pack.body.e1_base.life_input).read_text(encoding="utf-8"))
        comp, by_city = compile_catalog(pack, life["protocol"])
        return pack, comp, by_city, inventory_coverage(pack, comp)


def _opp(by_city, city, oid) -> LifeOpportunity:
    return LifeOpportunity.model_validate(
        next(o for o in by_city[city] if o["opportunity_id"] == oid)
    )


def test_legal_device_and_service_enter_e1_with_their_facts(compiled):
    _, comp, by_city, _ = compiled
    pack_k51 = _opp(by_city, K51, "k51.durable.convenience_pack")
    assert pack_k51.availability == "available" and pack_k51.price.amount.amount == 1440
    assert pack_k51.duration.kind == "durable_good" and pack_k51.duration.lasts_years == 5
    assert {(s.task, s.residual_hours_per_month) for s in pack_k51.resource.substitutes} == {
        ("laundry", 4),
        ("cleaning", 6),
    }
    chores = _opp(by_city, M52, "m52.service.chores")
    assert chores.price.billing == "per_month" and chores.duration.kind == "ongoing_service"
    assert chores.resource.basis == "per_service_month" and chores.resource.hours == 0
    rec = next(c for c in comp.compiled if c.candidate_id == "cand.k51.device.convenience-pack")
    assert rec.field_sources == {
        "price": ("f.k51.pack.price",),
        "lifetime": ("f.pack.life",),
        "effect": ("f.pack.effect",),
    }
    assert rec.weakest_source_kind == "synthetic" and pack_k51.source.kind == "synthetic"


def test_unknown_lifetime_is_not_compiled_and_supply_is_not_rewritten(compiled):
    _, comp, by_city, _inv = compiled
    robot = next(
        u for u in comp.uncompiled if u.candidate_id == "cand.k51.device.robot-lifetime-unknown"
    )
    assert [r.code for r in robot.reasons] == ["lifetime_unknown"]
    assert robot.market_status == "offered"  # 有卖但寿命未知：不改写成确定买不到
    assert all(o["opportunity_id"] != "k51.durable.robot" for o in by_city[K51])


def test_unknown_quote_compiles_as_unknown_availability_without_a_price(compiled):
    _, comp, by_city, _ = compiled
    dryer = _opp(by_city, K51, "k51.durable.dryer")
    assert dryer.availability == "unknown" and dryer.price is None
    assert dryer.duration.lasts_years == 8  # 其余结构照样要齐（E1 对 unknown 也不放松）
    assert (
        next(c for c in comp.compiled if c.opportunity_id == "k51.durable.dryer").availability
        == "unknown"
    )


def test_unknown_effect_is_not_disguised_as_no_effect(compiled):
    _, comp, by_city, _ = compiled
    cleaning = next(
        u for u in comp.uncompiled if u.candidate_id == "cand.m52.service.cleaning-effect-unknown"
    )
    assert [r.code for r in cleaning.reasons] == ["effect_unknown"]
    assert all(o["opportunity_id"] != "m52.service.cleaning" for o in by_city[M52])


def test_inventory_keeps_the_denominator_and_reports_partial(compiled):
    _, _, _, inv = compiled
    cities = {c.city_ref: c for c in inv.cities}
    k51 = cities[K51]
    assert (k51.surveyed, k51.resolved, k51.unresolved, k51.excluded) == (5, 2, 2, 1)
    assert k51.status == "partial"
    outcome = {i.candidate_id: i.outcome for i in k51.items}
    assert outcome["cand.k51.device.cooker-out-of-scope"] == "excluded"
    m52 = cities[M52]
    assert (m52.surveyed, m52.resolved, m52.unresolved, m52.excluded) == (
        3,
        2,
        1,
        0,
    ) and m52.status == "partial"
    assert "不保证穷尽" in inv.not_exhaustive_zh


def test_boundary_candidates_mixed_sources_fractional_lifetime_and_per_visit_billing(tmp_path):
    comp, _inv = sc.catalog_boundary(tmp_path)
    washer = next(
        c for c in comp.compiled if c.candidate_id == "cand.m52.device.washer-mixed-sources"
    )
    assert washer.weakest_source_kind == "synthetic"  # 观测形状的报价不会把假设效果变成观测
    assert washer.field_sources["price"] == ("f.m52.washer.price.observed-fixture",)
    reasons = {u.candidate_id: [r.code for r in u.reasons] for u in comp.uncompiled}
    assert reasons["cand.m52.device.robot-half-year"] == ["lifetime_not_integer"]
    assert set(reasons["cand.k51.service.laundry-per-visit"]) == {
        "billing_unit_unsupported",
        "effect_unknown",
    }
    assert comp.meta.sample.origin == "controlled_fixture"


def test_trajectory_scorability_is_independent_of_the_catalog_gaps(tmp_path):
    """目录不完整（partial）不影响已发生轨迹的评分：不买任何便利方案、资源账完整时，E1 照样给出确定的 q = 0。"""
    s = sc.closed_loop(
        tmp_path,
        "no_purchase",
        person="u602",
        draft_kwargs={
            "material": {"housing_space": "50", "fresh_food": "50", "daily_convenience": "0"},
            "hard_requirements": (),
        },
    )
    assert s.pre.inventory.cities[0].status == "partial"
    assert {c.category for c in s.result.clones} == {"C"}
    for clone in s.result.clones:
        for y in clone.years:
            assert y.devices == () and y.services == ()
            hours = {h.task: set(h.monthly_hours) for h in y.household}
            assert hours == {
                "laundry": {"12"},
                "cleaning": {"10"},
            }  # 本人全做：基准小时，便利 q = 0
    for life in s.outcome.lives.values():
        for r in life:
            q = {
                next(
                    c for c in m.components if c.component_id == "daily_convenience"
                ).convenience.relief_hours.exact
                for m in r.score.months
            }
            assert q == {"0"}


def test_a_positive_weight_with_unknown_city_data_is_reported_not_renormalized(tmp_path):
    """受控夹具：M52 的安全资料未知、用户给安全 20 分。预检查明确报告这一缺口（M52 的“可评分”只是 partial），覆盖摘要按城计算；
    确认画像里的 20 分原样保留，不删掉、不把其余权重重新归一化。"""
    s = sc.positive_weight_data_unknown(tmp_path)
    pre = s.pre.preflight
    assert pre.status == "ready" and pre.scorable.status == "partial"
    cities = {c.city_ref: c for c in pre.cities}
    k51, m52 = cities[K51], cities[M52]
    assert (k51.scorable.status, m52.scorable.status) == ("yes", "partial")
    gap = [g for g in m52.data_gaps if g.code == "positive_weight_data_unknown"]
    assert len(gap) == 1 and gap[0].field == "preferences.safety" and "安全" in gap[0].message_zh
    assert k51.data_gaps == ()
    # 覆盖摘要：资料未知按城算（0.8 × 0.2 = 0.16 只在 M52），共同覆盖是比较组层面的（两城相同）
    assert (k51.coverage.unknown_weight.display, m52.coverage.unknown_weight.display) == (
        "0.000",
        "0.160",
    )
    assert k51.coverage.common_weight == m52.coverage.common_weight
    assert (k51.coverage.unsupported_positive_leaves, m52.coverage.unsupported_positive_leaves) == (
        (),
        ("safety",),
    )
    assert s.confirmed.six_dimensions.points["safety"] == "20"


# ---------------------------------------------------------------------- E3-A 收尾 F2


def _variant(mutate):
    """资料包副本（只在内存里改）：改一个候选，重新编译与出覆盖报告，不碰磁盘上的资料包。"""
    import copy

    from product.pack import DataPack

    pack = load_pack()
    raw = copy.deepcopy(pack.raw)
    mutate(raw["pack"])
    variant = DataPack(raw, pack.path)
    life = yaml.safe_load(Path(variant.body.e1_base.life_input).read_text(encoding="utf-8"))
    comp, by_city = compile_catalog(variant, life["protocol"])
    return comp, by_city, inventory_coverage(variant, comp)


def _cand(body, cid):
    return next(c for c in body["candidates"] if c["candidate_id"] == cid)


@pytest.fixture()
def in_ws(tmp_path):
    with contextlib.chdir(prepare_workspace(tmp_path / "ws", REPO)):
        yield


def test_known_fields_without_sources_are_not_compiled_or_called_observed(in_ws):
    """只有报价有（观测形状的）来源、寿命与效果有数值没来源：不编入，不把整项标成 observed / 资料齐全；全部来源为空同样不编入，
    也不自动生成合成来源说明。其余候选照常编译，分母不变。"""
    cid = "cand.k51.device.convenience-pack"

    def observed_price_only(body):
        c = _cand(body, cid)
        c["lifetime"]["facts"] = []
        c["effect"]["facts"] = []
        for f in body["facts"]:
            if f["fact_id"] in c["price"]["facts"]:
                f["kind"], f["locator"] = "observed", "https://example.invalid/offline-fixture"
                f.pop("assumption", None)

    def no_sources(body):
        c = _cand(body, cid)
        for name in ("price", "lifetime", "effect"):
            c[name]["facts"] = []

    for mutate, fields in (
        (observed_price_only, {"lifetime", "effect"}),
        (no_sources, {"price", "lifetime", "effect"}),
    ):
        comp, by_city, inv = _variant(mutate)
        assert cid not in {c.candidate_id for c in comp.compiled}
        reasons = next(u.reasons for u in comp.uncompiled if u.candidate_id == cid)
        assert {r.code for r in reasons} == {"source_missing"}
        assert {r.field.split(".")[-2] for r in reasons} == fields
        assert "k51.durable.convenience_pack" not in {o["opportunity_id"] for o in by_city[K51]}
        assert "cand.k51.service.chores" in {c.candidate_id for c in comp.compiled}
        k51 = next(c for c in inv.cities if c.city_ref == K51)
        item = next(i for i in k51.items if i.candidate_id == cid)
        assert (item.outcome, item.data_status, item.compile_status) == (
            "unresolved",
            "incomplete",
            "not_compiled",
        )
        assert k51.surveyed == 5  # 分母没有因为删掉问题候选而变小


def test_registered_mixed_sources_compile_at_the_weakest_kind(in_ws):
    """观测报价 + 登记过的合成寿命与效果：编入，整条按最弱来源标 synthetic，不升级为 observed。"""
    cid = "cand.k51.device.convenience-pack"

    def observed_price(body):
        c = _cand(body, cid)
        for f in body["facts"]:
            if f["fact_id"] in c["price"]["facts"]:
                f["kind"], f["locator"] = "observed", "https://example.invalid/offline-fixture"
                f.pop("assumption", None)

    comp, by_city, _ = _variant(observed_price)
    rec = next(c for c in comp.compiled if c.candidate_id == cid)
    assert rec.availability == "available" and rec.weakest_source_kind == "synthetic"
    opp = next(o for o in by_city[K51] if o["opportunity_id"] == "k51.durable.convenience_pack")
    assert opp["source"]["kind"] == "synthetic" and opp["unit"] == "appliance-set"


def test_missing_units_bad_numbers_and_wrong_sources_are_reported_per_candidate(tmp_path):
    """缺计价单位、参与小时为负或不是数、报价引用了效果事实：逐候选结构化原因，整批不抛异常，其余候选照常编译。"""
    comp, inv = sc.catalog_statuses(tmp_path)
    reasons = {u.candidate_id: [r.code for r in u.reasons] for u in comp.uncompiled}
    assert reasons["cand.m52.device.pack-no-unit"] == ["price_unit_missing"]
    assert reasons["cand.k51.service.helper-negative-participation"] == ["participation_invalid"]
    assert reasons["cand.m52.service.chores-wrong-field-source"] == ["source_field_mismatch"]
    assert reasons["cand.k51.device.washer-observed-price-only"] == ["source_missing"] * 2
    compiled = {c.candidate_id for c in comp.compiled}
    assert {"cand.k51.device.convenience-pack", "cand.m52.service.chores"} <= compiled
    surveyed = {c.city_ref: c.surveyed for c in inv.cities}
    assert surveyed == {K51: 8, M52: 6}  # 原有 5 + 3、3 + 3：没有一个被过滤掉

    def garbage(body):
        _cand(body, "cand.k51.service.chores")["participation"]["hours_text"] = "garbage"

    with contextlib.chdir(prepare_workspace(tmp_path / "ws", REPO)):
        comp2, _, _ = _variant(garbage)
    bad = next(u for u in comp2.uncompiled if u.candidate_id == "cand.k51.service.chores")
    assert [r.code for r in bad.reasons] == ["participation_invalid"]
    assert "cand.m52.service.chores" in {c.candidate_id for c in comp2.compiled}


def test_not_offered_supply_unknown_and_price_unknown_are_told_apart(tmp_path):
    comp, inv = sc.catalog_statuses(tmp_path)
    items = {i.candidate_id: i for city in inv.cities for i in city.items}
    nope = items["cand.k51.service.ironing-not-offered"]
    assert (nope.outcome, nope.supply_status, nope.data_status, nope.compile_status) == (
        "resolved",
        "not_offered",
        "not_needed",
        "not_compiled",
    )
    assert "不是可购买的方案" in nope.reason_zh  # 已确定的调查结果，但不能买
    assert "cand.k51.service.ironing-not-offered" not in {c.candidate_id for c in comp.compiled}
    supply = items["cand.m52.service.chores-supply-unknown"]
    rec = next(
        c for c in comp.compiled if c.candidate_id == "cand.m52.service.chores-supply-unknown"
    )
    assert rec.availability == "unknown" and rec.unknown_reason == "supply_unknown"
    assert (supply.outcome, supply.supply_status, supply.data_status) == (
        "unresolved",
        "unknown",
        "complete",
    )
    assert "供给未知" in supply.reason_zh and "报价" not in supply.reason_zh
    price = items["cand.k51.device.dryer-quote-unknown"]
    dryer = next(
        c for c in comp.compiled if c.candidate_id == "cand.k51.device.dryer-quote-unknown"
    )
    assert dryer.unknown_reason == "price_unknown" and price.data_status == "price_unknown"
    assert "报价未知" in price.reason_zh


# ---------------------------------------------------------------------- 报价单位、重复任务、低于最小单位的报价


def _price_fact(body, cand_id):
    ref = _cand(body, cand_id)["price"]["facts"][0]
    return next(f for f in body["facts"] if f["fact_id"] == ref)


def _codes(comp, cid):
    return [r.code for r in next(u for u in comp.uncompiled if u.candidate_id == cid).reasons]


def test_quote_source_must_match_the_billing_period_and_the_priced_object(in_ws):
    """来源“216 美元/次”不能支持候选“216 美元/月”；按台的来源不能支持按套的候选；口径（币种、基年）不同、计价对象没登记的
    同样拒绝。每次只坏一个候选：它进覆盖报告的未解决、资料不完整，其余候选照常编译；原样的同单位报价照常通过。"""
    chores, pack_k51 = "cand.k51.service.chores", "cand.k51.device.convenience-pack"
    comp, by_city, _ = _variant(lambda body: None)
    assert {chores, pack_k51} <= {c.candidate_id for c in comp.compiled}  # 月对月、套对套：通过
    chores_opp = next(o for o in by_city[K51] if o["opportunity_id"] == "k51.service.chores")
    assert chores_opp["price"]["billing"] == "per_month" and chores_opp["unit"] == "service-month"

    def unit(cid, value):
        def mutate(body):
            _price_fact(body, cid)["unit"] = value

        return mutate

    def per_unit(body):
        _cand(body, pack_k51)["price"]["per_unit"] = "vacuum"

    for mutate, cid, code in (
        (unit(chores, "USD_2024_LOCAL/次"), chores, "source_billing_mismatch"),
        (unit(pack_k51, "USD_2024_LOCAL/台"), pack_k51, "source_object_mismatch"),
        (unit(pack_k51, "USD_2024_LOCAL/月"), pack_k51, "source_billing_mismatch"),
        (unit(chores, "USD_2023_LOCAL/月"), chores, "source_unit_mismatch"),
        (unit(chores, "CNY_2024_LOCAL/月"), chores, "source_unit_mismatch"),
        (unit(chores, "USD_2024_LOCAL/周"), chores, "source_unit_unregistered"),
        (per_unit, pack_k51, "price_unit_unregistered"),
    ):
        comp, by_city, inv = _variant(mutate)
        assert _codes(comp, cid) == [code], (cid, code)
        other = chores if cid == pack_k51 else pack_k51
        assert other in {c.candidate_id for c in comp.compiled}
        item = next(i for city in inv.cities for i in city.items if i.candidate_id == cid)
        assert (item.outcome, item.data_status, item.compile_status) == (
            "unresolved",
            "incomplete",
            "not_compiled",
        )
    comp, _, _ = _variant(unit(chores, "USD_2024_LOCAL/次"))
    reason = next(u for u in comp.uncompiled if u.candidate_id == chores).reasons[0]
    assert "按次" in reason.message_zh and "按月" in reason.message_zh


def test_duplicate_tasks_and_sub_cent_quotes_are_candidate_errors(in_ws):
    """同一项家务写了两条残余（内容完全相同也算）、报价“1e-3”或“0.005”（细于 1 美分）：都在候选阶段拒绝，不交给 E1 抛异常；
    其余候选照常编译。“1e3”是 1,000 美元，可以精确记账，照常通过。"""
    pack_k51, chores = "cand.k51.device.convenience-pack", "cand.k51.service.chores"

    def duplicate(same: bool):
        def mutate(body):
            residuals = _cand(body, pack_k51)["effect"]["residuals"]
            first = dict(residuals[0])
            residuals.append(first if same else {**first, "residual_hours_per_month": "3"})

        return mutate

    def quote(text):
        def mutate(body):
            _cand(body, chores)["price"]["amount_text"] = text
            _price_fact(body, chores)["raw_value"] = text

        return mutate

    for mutate, cid, code in (
        (duplicate(True), pack_k51, "residual_task_duplicated"),
        (duplicate(False), pack_k51, "residual_task_duplicated"),
        (quote("1e-3"), chores, "price_too_precise"),
        (quote("0.005"), chores, "price_too_precise"),
    ):
        comp, by_city, _ = _variant(mutate)
        assert _codes(comp, cid) == [code], (cid, code)
        other = chores if cid == pack_k51 else pack_k51
        assert other in {c.candidate_id for c in comp.compiled}
    comp, _, _ = _variant(duplicate(True))
    reason = next(u for u in comp.uncompiled if u.candidate_id == pack_k51).reasons[0]
    assert reason.field.endswith(".effect.laundry") and "2 次" in reason.message_zh
    comp, by_city, _ = _variant(quote("1e3"))
    opp = next(o for o in by_city[K51] if o["opportunity_id"] == "k51.service.chores")
    assert opp["price"]["amount"]["amount"] == 1000


def test_bad_candidates_do_not_break_the_preflight(tmp_path):
    """在合法候选旁边加两个坏候选——重复任务的设备、“1e-3”报价的服务：预检查不抛异常、照常可以启动，两项按候选拒绝并留在
    覆盖报告里，原有候选照常编进 E1。（坏的若恰好是参考估值模板绑定的那项方案，E1 会结构化地拒绝整次比较，不是异常。）"""
    import copy

    def mutate(body):
        dev = copy.deepcopy(_cand(body, "cand.k51.device.convenience-pack"))
        dev["candidate_id"], dev["target_opportunity_id"] = "cand.k51.device.dup", "k51.durable.dup"
        dev["effect"]["residuals"].append(dict(dev["effect"]["residuals"][0]))
        svc = copy.deepcopy(_cand(body, "cand.k51.service.chores"))
        svc["candidate_id"], svc["target_opportunity_id"] = (
            "cand.k51.service.tiny",
            "k51.service.tiny",
        )
        svc["price"]["amount_text"] = "1e-3"
        fact = copy.deepcopy(_price_fact(body, "cand.k51.service.chores"))
        fact["fact_id"], fact["raw_value"] = "f.k51.tiny.price", "1e-3"
        svc["price"]["facts"] = ["f.k51.tiny.price"]
        body["facts"].append(fact)
        body["candidates"] += [dev, svc]

    s = sc.preflight_only(
        tmp_path, "bad_candidates", mutate_pack=mutate, origin="controlled_fixture"
    )
    assert s.pre.preflight.status == "ready"
    comp = s.pre.compiled.catalog
    assert _codes(comp, "cand.k51.device.dup") == ["residual_task_duplicated"]
    assert _codes(comp, "cand.k51.service.tiny") == ["price_too_precise"]
    k51 = {o.opportunity_id for o in s.pre.world.bundle.catalog_for(K51).opportunities}
    assert {"k51.durable.convenience_pack", "k51.service.chores", "k51.durable.dryer"} <= k51
    assert not {"k51.durable.dup", "k51.service.tiny"} & k51
    items = {i.candidate_id: i for c in s.pre.inventory.cities for i in c.items}
    assert items["cand.k51.device.dup"].data_status == "incomplete"
    assert (
        next(c for c in s.pre.inventory.cities if c.city_ref == K51).surveyed == 7
    )  # 5 + 2，分母不变
