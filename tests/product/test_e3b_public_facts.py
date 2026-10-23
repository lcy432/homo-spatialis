"""E3B 的公开事实进产品消息（DECISIONS/0042）：结果里的住处、通勤、用车权、设备故障与税；逐城的资料性质；预检查的范围说明。

* 真实资料包的部分用旧金山与休斯敦的预检查（不过生活，快）；生活事实在真实资料包上的完整链路在 ``test_e3b_product_flow.py`` 的
  闭环里逐字段核对 E1 的记录。
* 设备故障与“买了用车权”用合成样例 K51 上的受控计划（写好的计划，不是规划器输出）：真实资料包里的设备都是模拟期内不随机坏；
  mock 只在不用车到不了岗位或满足不了硬要求时才买用车权（旧金山的情形见 ``test_mock_car.py``）。手算口径同 ``tests/life/test_e3b_engine.py``：开车远处 40 分钟、每次往返油费 5.00、文员每月到岗 20 次。
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.life_contracts import CityLifeCatalog, DeviceFailureSource, ReplacementRule, YearAction
from product import scenarios as sc

E3B = Path("configs/product/e3b_us_v1_pack.yaml")
HOU, SF, SEA = "US:CBSA2023:26420", "US:CBSA2023:41860", "US:CBSA2023:42660"
K51, CLONE_K = "US:SYNTH2026:K51", "u301.k51.r0"
PACK = "k51.durable.convenience_pack"
#: 公开事实里绝不能出现的内部字段：评分参数、按片区的背景量、故障抽签与种子。
INTERNAL_KEYS = {
    "eta",
    "tau",
    "cap",
    "base",
    "area_bases",
    "efficiencies",
    "uniform",
    "monthly_hazard",
    "key_fingerprint",
    "seed",
}


def _keys(obj: object) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


# --------------------------------------------------------------------------- 真实资料包的预检查


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    saved = sc.PACK_PATH
    sc.PACK_PATH = E3B
    try:
        return sc.preflight_only(
            tmp_path_factory.mktemp("e3b_sf"),
            "e3b_sf",
            abilities=("edu_at_least_hs",),
            cities=(SF, HOU),
        )
    finally:
        sc.PACK_PATH = saved


def _city(s, ref):
    return next(c for c in s.pre.preflight.cities if c.city_ref == ref)


def test_sf_without_transit_is_a_warning_not_zero_minutes(real):
    pre = real.pre.preflight
    assert pre.status == "ready"
    (gap,) = [g for g in _city(real, SF).data_gaps if g.code == "commute_mode_missing"]
    assert gap.severity == "warning" and "公共交通" in gap.message_zh
    assert not [g for g in _city(real, HOU).data_gaps if g.code == "commute_mode_missing"]
    modes = {m.mode: m for m in _city(real, SF).data_nature.commute_modes}
    assert not modes["transit"].in_relation and modes["transit"].lines == 0
    assert modes["drive"].in_relation and "car_access" in modes["drive"].condition_zh
    # 可评分不受影响：缺的方式是执行上的限制，不是正权重资料未知
    assert _city(real, SF).scorable.status == "yes"


def test_data_nature_counts_come_from_the_catalog_and_the_pack_report(real):
    world = real.pre.world
    report = json.loads(
        (real.work / "data/frozen/life/us-v1/pack/report.json").read_text(encoding="utf-8")
    )
    for ref, key in ((SF, "sf"), (HOU, "hou")):
        dn = _city(real, ref).data_nature
        cat = world.bundle.catalog_for(ref)
        assert dn.pack_status == "real_data_frozen"
        kinds = {k.kind: k.count for k in dn.totals}
        for kind in ("observed", "derived", "estimated", "synthetic"):
            assert kinds[kind] == sum(1 for o in cat.opportunities if o.source.kind == kind)
        assert sum(kinds.values()) == len(cat.opportunities)
        assert sum(c.total for c in dn.opportunities) == len(cat.opportunities)
        assert dn.home_areas + dn.work_areas == len(cat.areas)
        counts = report["cities"][key]["counts"]
        build = {b.key: b.count for b in dn.build}
        assert dn.build_status == "from_pack_report"
        for k in ("areas", "commute_legs", "housing_in_catalog", "jobs_placed", "menus"):
            assert build[k] == counts[k]
        assert build["items_by_domain.food"] == counts["items_by_domain"]["food"]
        for k, n in report["cities"][key]["excluded"].items():
            assert build[f"excluded.{k}"] == n
        assert build["housing_in_catalog"] == next(
            c.total for c in dn.opportunities if c.category == "housing"
        )
        assert {(m.category, m.years, m.env_repeats) for m in dn.menus} == {
            ("housing", 3, 2),
            ("job", 3, 2),
        }


def test_common_priors_named_assumptions_treatments_and_model_activities(real):
    dn = _city(real, HOU).data_nature
    # 主线 2026-10-23：安全改成各都会区的犯罪资料映射（各城不同，不是共同先验）；只剩休闲背景是六城共同设定
    assert {p.fact_id for p in dn.common_priors} == {"leisure_index"}
    assert all("共同先验" in p.label for p in dn.common_priors)
    names = " ".join(a.note_zh for a in dn.named_assumptions)
    for name in ("device_category_v1", "equipment_category_v1", "activity_template_v1"):
        assert name in names
    assert sum(a.count for a in dn.named_assumptions) == next(
        k.count for k in dn.totals if k.kind == "synthetic"
    )
    (treat,) = dn.treatment_assumptions_zh
    assert "共同" in treat
    cat = real.pre.world.bundle.catalog_for(HOU)
    assert set(dn.model_plan_activities) == {
        o.label
        for o in cat.opportunities
        if o.activity is not None and o.source.kind == "synthetic"
    }
    assert "在家练吉他（一次 1 小时）" in dn.model_plan_activities


def test_tax_scope_and_scope_notes(real):
    pre = real.pre.preflight
    tax = {c.city_ref: c.data_nature.tax for c in pre.cities}
    assert (tax[SF].policy_id, tax[HOU].policy_id) == ("tax.us2024.ca", "tax.us2024.tx")
    assert all(t.status == "official_2024_rules" and t.not_modeled_zh for t in tax.values())
    assert "联邦个人所得税" in tax[HOU].rules_zh
    topics = [n.topic for n in pre.scope_notes]
    for t in ("occupations", "tax", "commute", "menus", "unsupported"):
        assert t in topics
    occ = next(n for n in pre.scope_notes if n.topic == "occupations")
    assert f"支持 {len(real.pack.body.occupations)} 种职业" in occ.text_zh
    assert "不换成别的职业" in occ.text_zh
    taxes = {n.cities: n for n in pre.scope_notes if n.topic == "tax"}
    assert set(taxes) == {(SF,), (HOU,)}
    assert "单身、只有工资收入" in taxes[(HOU,)].text_zh
    assert [n.cities for n in pre.scope_notes if n.title_zh == "缺少的通勤方式"] == [(SF,)]
    menus = next(n for n in pre.scope_notes if n.topic == "menus")
    assert "接触机会，不是成交或录用" in menus.text_zh
    unsupported = [n.text_zh for n in pre.scope_notes if n.topic == "unsupported"]
    assert unsupported == list(real.pack.body.unsupported)
    assert any("芝加哥公园管理局" in u for u in unsupported)
    assert any("旧金山的公交票价" in u for u in unsupported)
    assert pre.preflight_fingerprint == pre.expected_fingerprint()


def test_public_facts_carry_no_scoring_parameters_or_draws(real):
    data = real.pre.preflight.model_dump(mode="json")
    found = _keys([c["data_nature"] for c in data["cities"]]) | _keys(data["scope_notes"])
    assert not found & INTERNAL_KEYS


def test_missing_or_mismatched_pack_report_makes_build_counts_unknown(real, tmp_path):
    from product.pack_facts import city_data_nature

    life = Path(real.pack.body.e1_base.life_input)
    (tmp_path / life.parent).mkdir(parents=True)
    shutil.copyfile(real.work / life, tmp_path / life)
    with contextlib.chdir(tmp_path):
        dn = city_data_nature(real.pack, real.pre.world, HOU)
        assert (dn.build_status, dn.build) == ("not_available", ())
        report = json.loads((real.work / life.parent / "report.json").read_text(encoding="utf-8"))
        report["files"][life.name] = "0" * 64  # 报告记下的生活输入不是这一份
        (life.parent / "report.json").write_text(json.dumps(report), encoding="utf-8")
        dn = city_data_nature(real.pack, real.pre.world, HOU)
        assert (dn.build_status, dn.build) == ("not_available", ())
        # 来源性质的计数照样来自目录（不依赖构建报告）
        assert sum(k.count for k in dn.totals) > 0


def test_real_pack_capabilities_mark_the_cities_as_real_data(tmp_path):
    saved = sc.PACK_PATH
    sc.PACK_PATH = E3B
    try:
        caps = sc.capabilities(tmp_path)
    finally:
        sc.PACK_PATH = saved
    assert caps.meta.data_pack.status == "real_data_frozen"
    assert {c.data_status for c in caps.cities} == {"real_data"}
    assert caps.occupation.concept_count == 40


# --------------------------------------------------------------------------- 合成资料包：含义不变


def test_synthetic_pack_keeps_its_meaning(tmp_path):
    s = sc.preflight_only(tmp_path, "syn")
    pre = s.pre.preflight
    assert pre.status == "ready" and pre.scope_notes == ()
    assert "scope_notes" not in pre.model_dump(mode="json")
    for c in pre.cities:
        assert not [g for g in c.data_gaps if g.code == "commute_mode_missing"]
        dn = c.data_nature
        assert dn.pack_status == "synthetic_fixture_only" and "合成资料包" in dn.summary_zh
        assert dn.build_status == "not_available"  # 合成样例没有构建报告：计数未知，不写 0
        kinds = {k.kind: k.count for k in dn.totals}
        assert kinds["synthetic"] == sum(kinds.values())
        assert dn.tax is not None and dn.tax.status == "synthetic_test_double"


# --------------------------------------------------------------------------- 受控的一年：用车权与设备故障


def _catalog(bundle, change) -> CityLifeCatalog:
    data = bundle.catalog_for(K51).model_dump(mode="json")
    change(data)
    return CityLifeCatalog.model_validate(data)


def _opp(d: dict, oid: str) -> dict:
    return next(o for o in d["opportunities"] if o["opportunity_id"] == oid)


def _with_car(d: dict) -> None:
    """同 tests/life/test_e3b_engine.py：远处到就业中心加一条开车（40 分钟、油费 5.00），加一项每月 300 的用车权。"""
    rel = d["commute"]
    src = rel["modes"][0]["source"]
    rel["modes"].append(
        {
            "mode": "drive",
            "requires_function": "car_access",
            "note": "测试：要有用车权",
            "source": src,
        }
    )
    far = dict(rel["legs"][1])
    rel["legs"].append(
        {
            **far,
            "mode": "drive",
            "roundtrip_minutes": 40,
            "cost_per_roundtrip": {"amount": 5.0, "numeraire": "USD_2024_LOCAL"},
        }
    )
    car = dict(_opp(d, "k51.service.chores"))
    car.update(
        opportunity_id="k51.service.car_access",
        label="用车权（测试）",
        quality="按月付费的用车权：车辆、保险与登记",
        price={
            "amount": {"amount": 300, "numeraire": "USD_2024_LOCAL"},
            "billing": "per_month",
            "per_unit": "service-month",
        },
        functions=["car_access"],
        resource={
            "basis": "per_service_month",
            "hours": 0,
            "burden_per_hour": 0,
            "quantity_scales": False,
            "note": "不占时间",
        },
    )
    d["opportunities"].append(car)


def _with_failure(d: dict) -> None:
    o = _opp(d, PACK)
    o["failure"] = {
        "kind": "annual_hazard",
        "annual_probability": 0.999999,
        "note": "测试用的高故障概率",
        "source": o["source"],
    }


@pytest.fixture(scope="module")
def synth():
    from ledger.life_catalog import load_life_bundle
    from ledger.life_scoring_config import load_scoring_config

    bundle, _ = load_life_bundle(
        Path("configs/life/synthetic_product.yaml"), require_settlement_ready=True
    )
    scoring, _ = load_scoring_config(Path("configs/life/scoring_product.yaml"), bundle)
    return bundle, scoring


def _live(bundle, scoring, catalog, extra, *, protocol=None, replacements=()):
    from econ.life_settlement import binding_from_policy
    from econ.life_year import ScoringInputs, run_life_year

    state = bundle.initial_state(CLONE_K)
    whole = [
        {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
        for oid in ("k51.housing.far", "k51.food.basic", "k51.job.clerk")
    ]
    action = YearAction.model_validate(
        {
            "person_id": state.person_id,
            "clone_id": state.clone_id,
            "year_index": state.year_index,
            "items": [*whole, *extra],
            "minimum_fallback": ["housing", "food"],
            "emergency": {"minor": "decline", "major": "decline", "pause_order": []},
            "rationale": "测试：预先写好的计划",
        }
    ).model_copy(update={"replacements": tuple(ReplacementRule(**r) for r in replacements)})
    p = bundle.product_profiles[0]
    return run_life_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=action,
        catalog=catalog,
        tax=binding_from_policy(bundle.tax_policy(catalog.tax_policy_id)),
        scoring=ScoringInputs(
            spec=scoring.spec,
            conditions=scoring.conditions_for(K51),
            preference_input=p,
            hard_requirements=p.hard_requirements,
        ),
    )


def _world(catalog):
    """``year_facts`` 只读目录（标签、片区与通勤关系）：给它一个只装这份目录的最小运行世界。"""
    return SimpleNamespace(bundle=SimpleNamespace(catalog_for=lambda city: catalog))


def test_car_access_bought_shows_in_the_commute_and_its_own_line(synth):
    from product.results import year_facts

    bundle, scoring = synth
    cat = _catalog(bundle, _with_car)
    car = {"opportunity_id": "k51.service.car_access", "quantity": 1, "start_month": 1}
    y = _live(bundle, scoring, cat, [{**car, "end_month": 12}])
    facts = year_facts(_world(cat), K51, y)
    assert facts.car_access is not None and facts.car_access.status == "bought"
    (line,) = facts.car_access.lines
    assert line.months_covered == tuple(range(1, 13)) and line.months_not_obtained == ()
    (g,) = facts.commute  # 12 个月完全相同：合成一行
    assert g.months == tuple(range(1, 13)) and g.status == "computed"
    (trip,) = g.trips
    assert (trip.mode, trip.mode_zh, trip.roundtrip_minutes, trip.onsite_visits) == (
        "drive",
        "开车",
        40,
        20,
    )
    assert g.monthly_paid.amount_minor == 20 * 500  # 20 次 × 5.00，手算
    (home,) = facts.homes
    area = next(a for a in cat.areas if a.area_id == cat.opportunity("k51.housing.far").area)
    assert (home.opportunity_id, home.months, home.area_id) == (
        "k51.housing.far",
        tuple(range(1, 13)),
        area.area_id,
    )
    assert (home.area_label, home.area_typical_roundtrip_minutes) == (
        area.label,
        area.typical_roundtrip_minutes,
    )
    assert facts.tax.policy_id == cat.tax_policy_id and facts.tax.status == "synthetic_test_double"

    # 不买用车权：仍有这一栏（这座城开车要用车权），状态是没有买；通勤回到公共交通 70 分钟、票价 0
    y = _live(bundle, scoring, cat, [])
    facts = year_facts(_world(cat), K51, y)
    assert facts.car_access.status == "not_bought" and facts.car_access.lines == ()
    (g,) = facts.commute
    assert [(t.mode, t.roundtrip_minutes) for t in g.trips] == [("transit", 70)]
    assert g.monthly_paid.amount_minor == 0


def test_synthetic_city_without_a_drive_condition_has_no_car_access_line(synth):
    from product.results import year_facts

    bundle, scoring = synth
    cat = bundle.catalog_for(K51)
    facts = year_facts(_world(cat), K51, _live(bundle, scoring, cat, []))
    assert facts.car_access is None and facts.device_failures == ()
    assert "car_access" not in facts.model_dump(mode="json")
    assert "device_failures" not in facts.model_dump(mode="json")


def test_a_device_failure_and_its_replacement_are_reported_without_the_draw(synth):
    from product.results import year_facts

    bundle, scoring = synth
    cat = _catalog(bundle, _with_failure)
    src = DeviceFailureSource(mode="random", pairing_id="test.pairing", seed=7, note="测试")
    y = _live(
        bundle,
        scoring,
        cat,
        [{"opportunity_id": PACK, "quantity": 1, "month": 1}],
        protocol=bundle.protocol.model_copy(update={"device_failures": src}),
        replacements=({"held": PACK, "replacement": PACK},),
    )
    e1 = y.settlement.outcome.device_failures[0]
    facts = year_facts(_world(cat), K51, y)
    f = facts.device_failures[0]
    assert len(facts.device_failures) == len(y.settlement.outcome.device_failures)
    assert (f.opportunity_id, f.month, f.units_failed) == (PACK, e1.month, e1.units_failed)
    assert f.label == cat.opportunity(PACK).label
    assert (f.replacement_id, f.replacement_status, f.replacement_month) == (
        PACK,
        "bought",
        e1.month + 1,
    )
    assert f.replacement_label == f.label and "下个月" in f.replacement_status_zh
    assert not _keys(facts.model_dump(mode="json")) & INTERNAL_KEYS


# --------------------------------------------------------------------------- 治安资料的逐城说明（审核 F6）

NYC, CHI = "US:CBSA2023:35620", "US:CBSA2023:16980"
#: 安全背景的内部换算：权重、全国参照锚点、换算公式与中间量都不进公开消息与样例
SAFETY_SECRETS = (
    "fbi_relative_rate",
    "w_violent",
    "w_property",
    "national_index",
    "全国平均水平",
    "6/r",
    "min(10",
    "safety_r",
)


def test_every_city_gets_a_safety_coverage_note_without_internal_parameters(tmp_path):
    saved = sc.PACK_PATH
    sc.PACK_PATH = E3B
    try:
        s = sc.preflight_only(
            tmp_path, "safety", abilities=("edu_at_least_hs",), cities=(NYC, CHI, HOU)
        )
    finally:
        sc.PACK_PATH = saved
    notes = [n for n in s.pre.preflight.scope_notes if n.topic == "data"]
    (general,) = [n for n in notes if not n.cities]
    assert "统计代理" in general.text_zh and "不是个人遭遇犯罪" in general.text_zh
    assert "休闲背景是六城共同" in general.text_zh
    by_city = {n.cities[0]: n.text_zh for n in notes if n.cities}
    assert set(by_city) == {NYC, CHI, HOU}
    # 纽约只覆盖都会区的纽约州部分；64.5% 是县的人口占比，不是报案完整率
    assert "纽约州部分" in by_city[NYC] and "64.5%" in by_city[NYC]
    assert "不是报案完整率" in by_city[NYC] and "新泽西" in by_city[NYC]
    # 芝加哥的资料含模拟居住范围以外的印第安纳州县
    assert "Indiana" in by_city[CHI] and "不在模拟居住范围" in by_city[CHI]
    assert "整个都会区" in by_city[HOU] and "资料范围与模拟居住范围相同" in by_city[HOU]
    text = json.dumps([n.model_dump(mode="json") for n in notes], ensure_ascii=False)
    for secret in SAFETY_SECRETS:
        assert secret not in text


def test_public_samples_carry_no_internal_safety_parameters():
    for p in sorted(Path("contracts/product/v3/samples").glob("*.json")):
        text = p.read_text(encoding="utf-8")
        for secret in SAFETY_SECRETS:
            assert secret not in text, (p.name, secret)
