"""E1-3 增补的四类边界修复（`DECISIONS/0019` 第十二节，2026-10-08）：永久回归。

* F1 覆盖必须追踪评分真正读到的资料：容量缺失、功能资料未采集、参数来源与事实来源分开；
* F2 资料单位必须与评分读取单位一致：冲突、未登记、空单位拒绝；登记过的别名与有记录的换算通过；
* F3 存在型功能不能靠件数、批次或换一种商品刷分；容量型照样按实际容量递减地增加；
* F4 内部评分对象与它们原样序列化后的字典，在加载、可见投影、最终 payload 三处都被拒绝；
  正常的品牌、菜系、准备方式、住房规格、通勤分钟照样可见。

另有一条兼容核对：默认合法样例的账目与逐月评分与 2026-10-07 的归档演示逐项相同。

每条测试都核对拒绝的**原因**或"结果明确变成未知"，不只看"抛了异常"。数字来自合成样例，可以手算：
储物 z = 0.01 × 升、τ = 2（200 升 → 0.5，400 升 → 4/6）；存在型叶子 3/(3+1) = 0.75（这是这组合成
参数的演示结果，不是现实的需求满足比例）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentlayer.life_views import (
    FORBIDDEN_PAYLOAD_KEYS,
    AgentObservation,
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    visible_feedback,
)
from common.life_contracts import LifeContractError
from common.life_scoring_contracts import (
    RESERVED_SCORING_KEYS,
    CoverageReport,
    internal_scoring_models,
)
from common.money import Money
from econ.life_coverage import coverage_report
from econ.life_scoring import source_table
from ledger.life_catalog import cross_validate
from ledger.life_scoring_config import check_scoring_config, parse_scoring_config

from .conftest import catalog, opportunity
from .score_support import (
    APPROX,
    component,
    profile_of,
    research_pref,
    s_series,
    score_of,
    settle_year_of,
)
from .settle_support import bundle_from, plan_of, revise, state_of, with_items
from .test_views import RESEARCH

REPO = Path(__file__).resolve().parents[2]
USD = "USD_2024_LOCAL"
A41, B42 = "US:SYNTH2026:A41", "US:SYNTH2026:B42"
PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-3-draft",
)
BASE = (
    {"opportunity_id": "a41.housing.near", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a41.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a41.job.office", "quantity": 1, "start_month": 1, "end_month": 12},
)


def _condition(raw_scoring: dict, code: str, component_id: str) -> dict:
    cc = next(c for c in raw_scoring["city_conditions"] if c["city"]["code"] == code)
    return next(x for x in cc["components"] if x["component_id"] == component_id)


def _spec_component(raw_scoring: dict, component_id: str) -> dict:
    return next(c for c in raw_scoring["spec"]["components"] if c["component_id"] == component_id)


def _supply(raw: dict, code: str, function: str) -> dict:
    return next(f for f in catalog(raw, code)["function_supply"] if f["function"] == function)


def _coverage(bundle, config, person: str = "u11"):
    return coverage_report(
        spec=config.spec,
        group=config.comparison_group,
        conditions=config.city_conditions,
        catalogs=bundle.catalogs,
        preference=research_pref(bundle, person),
    )


def _leaf(report, leaf: str):
    return next(x for x in report.leaves if x.leaf == leaf)


def _rich(bundle, clone: str, cash: float = 30000.0):
    return revise(state_of(bundle, clone), cash=Money(cash, USD))


def _years(bundle, clone: str, *extras, cash: float = 30000.0):
    """逐年结算：第 k 年 = BASE + extras[k-1]（A41 的分身）。返回 [(年初状态, 结算结果), ...]。"""
    out, state = [], _rich(bundle, clone, cash)
    for year, extra in enumerate(extras, start=1):
        plan = with_items(plan_of(bundle, clone, 1), *BASE, *extra, year_index=year)
        s, r = settle_year_of(bundle, clone, year=year, plan=plan, state=state)
        out.append((s, r))
        state = r.next_state
    return out


def _drop_b42_laundry(raw: dict) -> None:
    """删除 B42 的全部洗衣机会，以及计划里对它们的购买（审核反例 undeclared_function 的情形）。"""
    b42 = catalog(raw, "B42")
    gone = {
        o["opportunity_id"] for o in b42["opportunities"] if "laundry" in o.get("functions", [])
    }
    b42["opportunities"] = [o for o in b42["opportunities"] if o["opportunity_id"] not in gone]
    for plan in raw["plans"]:
        plan["items"] = [i for i in plan["items"] if i["opportunity_id"] not in gone]


# ==========================================================================
# F1 覆盖追踪实际资料
# ==========================================================================


def test_missing_capacity_makes_coverage_and_score_agree(raw3s, scoring3s):
    """B42 的柜子没写容量：评分在用到它的月份报未知；覆盖也不能再说储物已支持。"""
    del opportunity(raw3s, "b42.durable.cabinet")["spec"]["storage_liters"]
    b = bundle_from(raw3s)
    assert cross_validate(b).contract_valid
    assert check_scoring_config(scoring3s, b).exploration_scoring_ready  # 缺口报告，不是入口错误
    rep = _coverage(b, scoring3s)
    storage = _leaf(rep, "storage")
    assert (storage.support, storage.city_data, storage.cities_unknown) == (
        "unknown_in_some_city",
        "gap",
        (B42,),
    )
    assert any("b42.durable.cabinet" in g and "storage_liters" in g for g in storage.data_gaps)
    assert rep.common_weight == pytest.approx(0.9 - 0.35 * 0.05, **APPROX)
    s, r = settle_year_of(b, "u11.b42.c0")
    st = component(score_of(b, scoring3s, s, r), 5, "storage")
    assert (st.status, st.s) == ("unknown", None)
    assert any("storage_liters" in x for x in st.reasons)
    # 一个无关字段缺失不让别的需求失效：通信、洗衣照样有完整资料
    assert _leaf(rep, "communication").support == _leaf(rep, "laundry").support == "supported"


def test_estimated_parameters_do_not_turn_synthetic_facts_into_data(raw_scoring3s, bundle3s):
    raw_scoring3s["comparison_group"]["allowed_source_kinds"] = ["synthetic", "estimated"]
    estimated = {
        "kind": "estimated",
        "observed_on": "2026-10-08",
        "geography_scope": "synthetic city (no real geography)",
        "mapping_version": "test",
        "inputs": ["survey wave 1"],
        "rule": "small-area estimate",
    }
    for cc in raw_scoring3s["city_conditions"]:
        for cond in cc["components"]:
            cond["source"] = dict(estimated)
            if cond.get("nature"):
                cond["nature"]["source"] = dict(estimated)
    config = parse_scoring_config(raw_scoring3s)
    assert check_scoring_config(config, bundle3s).exploration_scoring_ready
    rep = _coverage(bundle3s, config)
    assert rep.coverage_label == "mixed_coverage"
    storage = _leaf(rep, "storage")
    assert (storage.fact_evidence, storage.parameter_evidence, storage.evidence) == (
        "synthetic_only",
        "estimated",
        "mixed",
    )
    assert rep.parameter_weight_by_evidence == pytest.approx({"estimated": 0.9}, **APPROX)
    assert any("生活事实仍含合成设定" in n for n in rep.notes)


def test_data_coverage_needs_non_synthetic_facts_as_well(raw3s, raw_scoring3s):
    """反过来：事实与参数都不是合成设定时，标签才可能是 data_coverage（标签不是永远够不着）。"""
    estimated = {
        "kind": "estimated",
        "inputs": ["survey wave 1"],
        "rule": "small-area estimate",
    }
    shared = [
        opportunity(raw3s, "a41.housing.near")["source"],
        _supply(raw3s, "A41", "laundry")["source"],
    ]
    shared += [_condition(raw_scoring3s, "A41", "safety")["source"]]
    shared += [
        _condition(raw_scoring3s, code, "environment")["nature"]["source"]
        for code in ("A41", "B42")
    ]
    for src in shared:  # YAML 锚点：同一份字典被全部机会 / 供给声明 / 评分条件共用
        src.pop("synthetic_note", None)
        src.update(estimated)
    raw_scoring3s["comparison_group"]["allowed_source_kinds"] = ["estimated"]
    b = bundle_from(raw3s)
    assert {o.source.kind for c in b.catalogs for o in c.opportunities} == {"estimated"}
    config = parse_scoring_config(raw_scoring3s)
    assert check_scoring_config(config, b).exploration_scoring_ready
    rep = _coverage(b, config)
    assert rep.coverage_label == "data_coverage"
    assert rep.fact_weight_by_evidence == pytest.approx({"estimated": 0.9}, **APPROX)


def test_laundry_not_bought_unavailable_unknown_and_undeclared_stay_distinct(
    bundle3s, scoring3s, raw3s_once
):
    """同一个人在 B42 没有洗衣条件，四种原因不能混成一个"确定的 0"。"""
    # ① 有可得方案、只是还没买：u11 第 4 月才买洗衣机，第 2 月是确定的 0
    s, r = settle_year_of(bundle3s, "u11.b42.c0")
    not_bought = component(score_of(bundle3s, scoring3s, s, r), 2, "laundry")
    assert (not_bought.status, not_bought.s, not_bought.terms) == ("known", 0.0, ())
    assert _leaf(_coverage(bundle3s, scoring3s), "laundry").support == "supported"

    def variant(status: str | None):
        raw = copy.deepcopy(raw3s_once)
        _drop_b42_laundry(raw)
        supplies = catalog(raw, "B42")["function_supply"]
        if status is None:
            supplies[:] = [f for f in supplies if f["function"] != "laundry"]
        else:
            _supply(raw, "B42", "laundry")["status"] = status
        b = bundle_from(raw)
        s, r = settle_year_of(b, "u11.b42.c0")
        month5 = component(score_of(b, scoring3s, s, r), 5, "laundry")
        return b, cross_validate(b), check_scoring_config(scoring3s, b), month5

    # ② 确定不可得：合法，按既定规则评价，确定的 0
    b, life, config, m5 = variant("unavailable")
    assert life.contract_valid and config.exploration_scoring_ready
    assert (m5.status, m5.s) == ("known", 0.0)
    assert _leaf(_coverage(b, scoring3s), "laundry").support == "supported"

    # ③ 资料未知：合法声明，但不能当成没有——覆盖报缺口，评分报未知
    b, life, config, m5 = variant("unknown")
    assert life.contract_valid and config.exploration_scoring_ready
    assert m5.status == "unknown" and m5.s is None
    assert any("供给资料未知或没有声明" in x for x in m5.reasons)
    gap = _leaf(_coverage(b, scoring3s), "laundry")
    assert gap.support == "unknown_in_some_city"
    assert any("资料未知" in g for g in gap.data_gaps)

    # ④ 根本没声明：比较组在入口拒绝；评分器自己也不给确定的 0
    b, life, config, m5 = variant(None)
    assert life.contract_valid
    assert any("B42" in e and "laundry 没有供给声明" in e for e in config.errors)
    assert m5.status == "unknown"


@pytest.mark.parametrize(
    "change,message",
    [
        ("drop_but_available", "声明可得，但目录里没有一项可得的机会"),
        ("keep_but_unavailable", "声明确定不可得，但目录里有"),
        ("keep_but_unknown", "应声明 available 而不是 unknown"),
    ],
)
def test_supply_declarations_must_agree_with_the_catalog(raw3s, change, message):
    if change == "drop_but_available":
        _drop_b42_laundry(raw3s)
    else:
        _supply(raw3s, "B42", "laundry")["status"] = change.split("_")[-1]
    errors = cross_validate(bundle_from(raw3s)).errors
    assert any("B42" in e and "laundry" in e and message in e for e in errors), errors


def test_any_substitution_survives_a_supply_gap(raw3s, scoring3s):
    """A41 的外部工位资料未知：住在自带工作间的房子里，工作空间照样可评；住在没有工作间的房子里，才是未知。"""
    desk = opportunity(raw3s, "a41.service.cowork")
    desk.pop("price")
    desk["availability"] = "unknown"
    _supply(raw3s, "A41", "workspace")["status"] = "unknown"
    opportunity(raw3s, "a41.housing.near")["spec"]["home_office"] = True
    b = bundle_from(raw3s)
    assert cross_validate(b).contract_valid
    s, r = settle_year_of(b, "u11.a41.c0")  # 近处，自带工作间
    near = component(score_of(b, scoring3s, s, r), 1, "workspace")
    assert near.status == "known" and near.s == pytest.approx(0.75, **APPROX)
    s, r = settle_year_of(b, "u11.a41.c1")  # 远处，没有工作间
    far = component(score_of(b, scoring3s, s, r), 1, "workspace")
    assert far.status == "unknown"
    ws = _leaf(_coverage(b, scoring3s), "workspace")
    assert ws.support == "unknown_in_some_city" and any("any 替代" in x for x in ws.reasons)


def test_coverage_v2_reports_three_layers(bundle3s, scoring3s):
    rep = _coverage(bundle3s, scoring3s, "u12")
    assert rep.report_version == "coverage-v2" and rep.catalog_facts_checked
    cleaning = _leaf(rep, "home_cleaning")
    assert (cleaning.rule, cleaning.city_data, cleaning.action_scope) == (
        "no_component",
        "not_applicable",
        "not_applicable",
    )
    env = _leaf(rep, "environment")
    assert (env.rule, env.city_data, env.cities_unknown) == ("defined", "gap", (B42,))
    commute = _leaf(rep, "commute")
    assert (commute.city_data, commute.action_scope) == ("complete", "conditional")
    housing = _leaf(rep, "housing_space")
    assert (housing.city_data, housing.action_scope, housing.support) == (
        "complete",
        "all_arrangements",
        "supported",
    )
    # 默认样例的覆盖数值不因修复而变：u12 共同支持 0.86，家务 0.04 没有分项
    assert rep.common_weight == pytest.approx(0.86, **APPROX)
    assert rep.no_component_weight == pytest.approx(0.04, **APPROX)


def test_old_coverage_reports_stay_readable_but_are_marked_unchecked():
    old = json.loads(
        (REPO / "runs/2026-10-07-e1-3s-demo/coverage.json").read_text(encoding="utf-8")
    )
    reports = [
        CoverageReport.model_validate({k: v for k, v in e.items() if k != "config"}) for e in old
    ]
    assert reports and all(r.report_version == "coverage-v1" for r in reports)
    assert not any(r.catalog_facts_checked for r in reports)


# ==========================================================================
# F2 单位含义
# ==========================================================================


@pytest.mark.parametrize(
    "key,unit,message",
    [
        (
            "storage_liters",
            "立方英尺",
            "资料单位是 立方英尺，storage/storage_capacity 按 升 读取（同一量纲但单位不同）",
        ),
        ("storage_liters", "cubic feet", "同一量纲但单位不同"),
        ("commute_roundtrip_minutes", "小时", "资料单位是 小时，commute 的方案级通勤 按 分钟 读取"),
        ("commute_roundtrip_minutes", "hours per round trip", "没有登记——不按字段名猜单位"),
        ("home_office", "升", "量纲都不同"),
        ("work_mode", "分钟", "工作方式（类别）"),
        ("floor_area_sqft", "平方米", "没有登记"),
    ],
)
def test_conflicting_or_unregistered_data_units_are_refused(
    raw_scoring3s, bundle3s, key, unit, message
):
    raw_scoring3s["comparison_group"]["units"][key] = unit
    report = check_scoring_config(parse_scoring_config(raw_scoring3s), bundle3s)
    assert not report.exploration_scoring_ready
    assert any(key in e and message in e for e in report.errors), report.errors


def test_an_empty_unit_is_refused(raw_scoring3s):
    raw_scoring3s["comparison_group"]["units"]["storage_liters"] = ""
    with pytest.raises(LifeContractError, match=r"units.storage_liters"):
        parse_scoring_config(raw_scoring3s)


def test_registered_aliases_and_recorded_conversions_pass(raw3s, raw_scoring3s, bundle3s):
    # 登记过的别名：同一个规范单位
    raw_scoring3s["comparison_group"]["units"]["storage_liters"] = "liters"
    raw_scoring3s["comparison_group"]["units"]["commute_roundtrip_minutes"] = "minutes"
    raw_scoring3s["comparison_group"]["allowed_source_kinds"] = ["synthetic", "derived"]
    # 资料整理时把立方英尺换算成升：数值已是升，来源标为 derived，写明输入与规则
    cab = opportunity(raw3s, "a41.durable.cabinet")
    cab["source"] = {
        "kind": "derived",
        "observed_on": "2026-10-08",
        "geography_scope": "synthetic city (no real geography)",
        "mapping_version": "test",
        "inputs": ["原始标注 7.0629 立方英尺（合成）"],
        "rule": "立方英尺 × 28.316846592 = 升，取整到 200",
    }
    b = bundle_from(raw3s)
    config = parse_scoring_config(raw_scoring3s)
    report = check_scoring_config(config, b)
    assert report.exploration_scoring_ready, report.errors
    assert _leaf(_coverage(b, config), "storage").fact_evidence == "mixed"
    # 读取单位到分项单位的换算写进来源表
    rows = {(r["component"], r["source"]): r["unit"] for r in source_table(config.spec)}
    assert rows[("storage", "storage_capacity")] == "升 → 百升（1 升 = 0.01 百升）"
    assert (
        rows[("housing_space", "dwelling")]
        == "平方英尺 → 百平方英尺（1 平方英尺 = 0.01 百平方英尺）"
    )


@pytest.mark.parametrize(
    "change,message",
    [
        (
            lambda d: _spec_component(d, "storage").update(unit="百升储物容量"),
            "分项单位 '百升储物容量' 没有登记",
        ),
        (
            lambda d: _spec_component(d, "storage")["sources"][0].update(unit="分钟"),
            "不是同一量纲",
        ),
        (lambda d: _spec_component(d, "commute").update(unit="小时"), "两者必须是同一个单位"),
        (
            lambda d: _spec_component(d, "communication")["sources"][0].update(unit="升"),
            "单位应是布尔量纲",
        ),
    ],
)
def test_spec_units_must_be_registered_and_consistent(raw_scoring3s, change, message):
    change(raw_scoring3s)
    with pytest.raises(LifeContractError, match=message):
        parse_scoring_config(raw_scoring3s)


def test_the_adr0018_version_keeps_its_free_text_units(scoring3):
    """旧版本照旧可读：单位是说明文字，不按规范单位检查。"""
    assert scoring3.spec.follows_adr0018
    assert scoring3.spec.component("durables").unit == "在用耐用品件数"


# ==========================================================================
# F3 存在型与容量型
# ==========================================================================


def _communication(raw_scoring: dict, **source_changes) -> dict:
    comp = _spec_component(raw_scoring, "communication")
    comp["sources"][0].update(source_changes)
    return comp


def _to_efficiencies(raw_scoring: dict, component_id: str, source_id: str) -> None:
    for cc in raw_scoring["city_conditions"]:
        cond = next(c for c in cc["components"] if c["component_id"] == component_id)
        cond["efficiencies"] = [{"source_id": source_id, "eta": cond.pop("eta_any")}]


@pytest.mark.parametrize(
    "combine,changes,message",
    [
        ("max", {"value": "quantity"}, "存在型只看有没有，件数不能进入"),  # 审核复现的组合
        ("max", {"value": "quantity", "measure": "count"}, "只能是存在型或容量型"),
        ("max", {"value": "quantity", "measure": None}, "必须声明数量语义"),
        ("sum", {}, "按功能过滤的来源只取“存在”或数量时不能相加"),
        (
            "max",
            {"value": "spec_number", "spec_key": "screen_inch", "per_unit": True},
            "presence 来源不能取 spec_number",
        ),
        (
            "max",
            {"value": "spec_number", "spec_key": "screen_inch", "measure": "capacity"},
            "必须声明 per_unit",
        ),
    ],
)
def test_presence_functions_cannot_count_units_in_any_form(
    raw_scoring3s, combine, changes, message
):
    comp = _communication(raw_scoring3s, **changes)
    comp["combine"] = combine
    if combine != "any":
        _to_efficiencies(raw_scoring3s, "communication", "communication_item")
    with pytest.raises(LifeContractError, match=message):
        parse_scoring_config(raw_scoring3s)


def test_presence_without_a_function_filter_cannot_be_summed_either(raw_scoring3s):
    """不按功能过滤的存在型来源（住宅自带工作间，规格是/否）也不能相加：sum 会把多处满足数成多份。"""
    comp = _spec_component(raw_scoring3s, "workspace")
    comp["combine"] = "sum"
    comp["sources"] = [s for s in comp["sources"] if s["source_id"] == "home_office"]
    for cc in raw_scoring3s["city_conditions"]:
        cond = next(c for c in cc["components"] if c["component_id"] == "workspace")
        cond["efficiencies"] = [{"source_id": "home_office", "eta": cond.pop("eta_any")}]
    with pytest.raises(LifeContractError, match="presence 来源不能用 sum 合并"):
        parse_scoring_config(raw_scoring3s)


def test_dropping_the_function_filter_does_not_reopen_counting(raw_scoring3s):
    """等价绕过：不按功能、改按类别数耐用品件数——新版本里 count 只用于一次体验。"""
    _communication(
        raw_scoring3s, functions=[], categories=["durable"], value="quantity", measure="count"
    )
    _spec_component(raw_scoring3s, "communication")["combine"] = "sum"
    _to_efficiencies(raw_scoring3s, "communication", "communication_item")
    with pytest.raises(LifeContractError, match="count 只用于一次体验"):
        parse_scoring_config(raw_scoring3s)


def test_a_presence_function_cannot_be_relabelled_as_capacity(raw3s, raw_scoring3s):
    """等价绕过：给手机编一个"线路数"规格、按容量计——生活输入声明了通信是存在型，比较组拒绝。"""
    for oid in (
        "a41.durable.phone",
        "a41.durable.phone_premium",
        "b42.durable.phone",
        "b42.durable.phone_premium",
    ):
        opportunity(raw3s, oid).setdefault("spec", {})["lines"] = 1
    comp = _communication(
        raw_scoring3s,
        value="spec_number",
        spec_key="lines",
        per_unit=True,
        measure="capacity",
        unit="次/月",
    )
    comp["combine"] = "sum"
    comp["unit"] = "次/月"
    _to_efficiencies(raw_scoring3s, "communication", "communication_item")
    raw_scoring3s["comparison_group"]["units"]["lines"] = "次/月"
    report = check_scoring_config(parse_scoring_config(raw_scoring3s), bundle_from(raw3s))
    assert any(
        "communication 声明为 presence，评分来源却按 capacity 计" in e for e in report.errors
    )


def test_repeated_communication_never_adds_up(raw3s, scoring3s, bundle3s):
    """同批三部、分批多部、不同商品（手机与通信套餐）、跨年继续持有：通信都只是一份 0.75。"""
    # 同一条记录买三部
    s = _rich(bundle3s, "u11.a41.c0")
    plan = plan_of(bundle3s, "u11.a41.c0")
    items = [
        {**i, "quantity": 3} if i["opportunity_id"] == "a41.durable.phone" else i
        for i in plan.model_dump(mode="json")["items"]
    ]
    _, r = settle_year_of(bundle3s, "u11.a41.c0", plan=with_items(plan, *items), state=s)
    assert s_series(score_of(bundle3s, scoring3s, s, r), "communication") == pytest.approx(
        [0.75] * 12, **APPROX
    )
    # 不同商品提供同一功能：按月的通信套餐 + 第 3 月买的高端手机
    raw3s["catalogs"][0]["opportunities"].append(
        {
            **copy.deepcopy(opportunity(raw3s, "a41.service.cleaning")),
            "opportunity_id": "a41.service.phone_plan",
            "label": "手机通信套餐",
            "spec": {"workspace": False},
            "functions": ["communication"],
            "quality": "按月付的通信套餐（合成）",
            "price": {
                "amount": {"amount": 30, "numeraire": USD},
                "billing": "per_month",
                "per_unit": "month",
            },
        }
    )
    b = bundle_from(raw3s)
    plan_item = {
        "opportunity_id": "a41.service.phone_plan",
        "quantity": 1,
        "start_month": 1,
        "end_month": 12,
    }
    premium = {"opportunity_id": "a41.durable.phone_premium", "quantity": 1, "month": 3}
    ((s1, r1),) = _years(b, "u11.a41.c0", (plan_item, premium))
    sc = score_of(b, scoring3s, s1, r1)
    assert s_series(sc, "communication") == pytest.approx([0.75] * 12, **APPROX)
    assert [t.counted for t in component(sc, 3, "communication").terms] == [True, False]
    # 跨年：第 1 年的手机继续服务，第 2 年又买高端手机
    phone = {"opportunity_id": "a41.durable.phone", "quantity": 1, "month": 1}
    (_, _), (s2, r2) = _years(b, "u11.a41.c0", (phone,), (premium,))
    assert s_series(score_of(b, scoring3s, s2, r2), "communication") == pytest.approx(
        [0.75] * 12, **APPROX
    )


def test_capacity_still_grows_with_real_capacity(raw3s, scoring3s):
    """容量型：同一条记录 1/2/3 个柜子按 200/400/600 升递减增长；跨年加一种 100 升的置物架也照算。"""
    raw3s["catalogs"][0]["opportunities"].append(
        {
            **copy.deepcopy(opportunity(raw3s, "a41.durable.cabinet")),
            "opportunity_id": "a41.durable.shelf",
            "label": "置物架（100 升）",
            "spec": {"storage_liters": 100},
            "quality": "容量型功能（合成）",
            "price": {
                "amount": {"amount": 80, "numeraire": USD},
                "billing": "one_time",
                "per_unit": "shelf",
            },
        }
    )
    b = bundle_from(raw3s)
    got = []
    for n in (1, 2, 3):
        ((s, r),) = _years(
            b, "u11.a41.c0", ({"opportunity_id": "a41.durable.cabinet", "quantity": n, "month": 1},)
        )
        got.append(component(score_of(b, scoring3s, s, r), 1, "storage").s)
    assert got == pytest.approx([2 / 4, 4 / 6, 6 / 8], **APPROX)
    cabinet = {"opportunity_id": "a41.durable.cabinet", "quantity": 1, "month": 1}
    shelves = {"opportunity_id": "a41.durable.shelf", "quantity": 2, "month": 3}
    (_, _), (s2, r2) = _years(b, "u11.a41.c0", (cabinet,), (shelves,))
    assert s_series(score_of(b, scoring3s, s2, r2), "storage") == pytest.approx(
        [0.5] * 2 + [4 / 6] * 10, **APPROX
    )


# ==========================================================================
# F4 内部参数与它们的原样序列化
# ==========================================================================


def _internal_instances(bundle3s, scoring3s) -> list:
    spec = scoring3s.spec
    env, commute = spec.dimension_component("environment"), spec.dimension_component("commute")
    storage = spec.component("storage")
    a41 = scoring3s.conditions_for(A41)
    s, r = settle_year_of(bundle3s, "u11.a41.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    month = score.months[4]
    comps = {c.component_id: c for c in month.components}
    rep = _coverage(bundle3s, scoring3s)
    return [
        spec,
        storage,
        storage.sources[0],
        env.landscape,
        commute.plan_commute,
        spec.omissions[0],
        a41.condition("storage").efficiencies[0],
        a41.condition("environment").nature,
        a41.condition("storage"),
        a41,
        scoring3s.comparison_group,
        research_pref(bundle3s, "u11"),
        score,
        month,
        month.dimensions[0],
        comps["storage"],
        month.dimensions[0].material_terms[0],
        comps["storage"].terms[0],
        comps["environment"].landscape,
        comps["commute"].commute,
        score.hard_requirements[0].evidence[0],
        score.hard_requirements[0],
        rep.leaves[0],
        rep,
        *_growth_transport_instances(),
        *_health_instances(),
        *_convenience_instances(),
    ]


def _growth_transport_instances() -> list:
    """E1-5A 补充（`DECISIONS/0024`）新登记的五个内部评分类型。

    接口适配（2026-10-09）：E1-3 增补样例里没有学习与广义交通，产生不出这些对象，这里直接构造合成实例。
    上面"每个登记的内部评分类型都被检查"的断言原样保留，不改成子集比较。
    """
    from common.life_scoring_contracts import (
        AbilityProgressStep,
        GrowthSpec,
        GrowthTerm,
        TransportSpec,
        TransportTerm,
    )

    step = AbilityProgressStep(
        ability_id="accounting_cert",
        before=0.0,
        after=1 / 12,
        delta=1 / 12,
        via=("a71.training.accounting_course",),
    )
    return [
        GrowthSpec(
            eta=12,
            progress_rule="ability_progress_increment_v1",
            unit="能力进度/月",
            definition="合成：当月实际新增的能力进度",
        ),
        TransportSpec(
            minutes_key="commute_roundtrip_minutes",
            rho=0.25,
            reference_visits=20,
            unit="分钟/工作日往返",
            visits_unit="次/月",
            definition="合成：交通便捷程度及出行对生活的影响",
        ),
        step,
        GrowthTerm(g=1 / 12, eta=12, contribution=1.0, steps=(step,)),
        TransportTerm(
            housing_opportunity_id="a41.housing.near",
            minutes=30,
            onsite_visits=10,
            reference_visits=20,
            rho=0.25,
            visit_factor=0.625,
            equivalent_minutes=18.75,
            tau_minutes=60,
        ),
    ]


def _health_instances() -> list:
    """E1-5C（`DECISIONS/0027`）新登记的四个内部评分类型。

    接口适配（2026-10-11）：E1-3 增补样例里没有健康评分版本，产生不出这些对象，这里直接构造合成实例。
    上面"每个登记的内部评分类型都被检查"的断言原样保留，不改成子集比较。
    """
    from common.life_scoring_contracts import (
        HealthScoringSpec,
        HealthStateValues,
        HealthTerm,
        HealthWeighting,
    )

    values = HealthStateValues(healthy=1.0, minor=0.5, major=0.0)
    return [
        values,
        HealthScoringSpec(
            gamma=0.2, state_values=values, rule="month_start_state", definition="合成：健康项"
        ),
        HealthTerm(
            state="minor",
            value=0.5,
            gamma=0.2,
            contribution=0.1,
            deduction=0.1,
            daily_life_share=0.8,
        ),
        HealthWeighting(
            raw_dimension_weights={"material": 0.3, "healthcare": 0.1},
            healthcare_semantics="health_risk_coping_v1",
            six_dimension_sum=0.9,
            effective_weights={"material": 1 / 3},
            gamma=0.2,
            state_values=values,
            formula="合成：S = (1 − γ)·B + γ·H",
        ),
    ]


def _convenience_instances() -> list:
    """E1 补充"日常生活便利"（`DECISIONS/0033`）新登记的三个内部评分类型。

    接口适配（2026-10-18）：E1-3 增补样例里没有便利评分版本，产生不出这些对象，这里直接构造合成实例。
    上面"每个登记的内部评分类型都被检查"的断言原样保留，不改成子集比较。
    """
    from common.life_contracts import ExactAmount
    from common.life_scoring_contracts import ConvenienceSpec, ConvenienceTerm, TaskRelief

    relief = TaskRelief(
        task="laundry",
        baseline_hours=ExactAmount.of(12),
        residual_hours=ExactAmount.of(4),
        relief_hours=ExactAmount.of(8),
        opportunity_id="k51.durable.convenience_pack",
    )
    return [
        ConvenienceSpec(
            tasks=("laundry", "cleaning"),
            cap=0.9,
            rule="household_baseline_minus_residual_v1",
            unit="小时/月",
            definition="合成：这个月本人实际少做的家务小时",
        ),
        relief,
        ConvenienceTerm(tasks=(relief,), relief_hours=ExactAmount.of(8), tau_hours=8, cap=0.9),
    ]


def test_internal_objects_and_their_dumps_are_refused(bundle3s, scoring3s):
    objects = _internal_instances(bundle3s, scoring3s)
    assert {type(o).__name__ for o in objects} == {m.__name__ for m in internal_scoring_models()}
    for obj in objects:
        with pytest.raises(LifeContractError, match=f"^{type(obj).__name__} 出现在"):
            assert_payload_clean({"opportunities": [{"spec": {"deep": [obj]}}]})
        dump = obj.model_dump(mode="json")
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            assert_payload_clean({"notes": [{"nested": dump}]})


def _flat_dumps(bundle3s, scoring3s) -> dict[str, dict]:
    """能塞进机会属性（只许原始值）的那些内部对象：取它们原样序列化后的原始值字段。"""
    out = {}
    for obj in _internal_instances(bundle3s, scoring3s):
        dump = obj.model_dump(mode="json")
        model = type(obj)
        required = {n for n, f in model.model_fields.items() if f.is_required()}
        flat = {k: v for k, v in dump.items() if isinstance(v, (str, int, float, bool))}
        if required <= set(flat):
            out[model.__name__] = flat
    return out


def test_flat_parameter_dumps_cannot_ride_in_catalog_facts(raw3s_once, bundle3s, scoring3s):
    dumps = _flat_dumps(bundle3s, scoring3s)
    assert {
        "LandscapeSpec",
        "PlanCommuteSpec",
        "Omission",
        "SourceEfficiency",
        "CommuteTerm",
        "LeafCoverage",
    } <= set(dumps)
    clean = build_observation(
        permission=ObservationPermission(**RESEARCH),
        profile=profile_of(bundle3s, "u11"),
        state=state_of(bundle3s, "u11.a41.c0"),
        catalog=bundle3s.catalog_for(A41),
    )
    for name, flat in dumps.items():
        raw = copy.deepcopy(raw3s_once)
        food = opportunity(raw, "a41.food.fresh")  # 已有 prep_mode：内部字段混在正常属性里
        food["attributes"].update(flat)
        raw["attribute_keys"] = sorted(set(raw["attribute_keys"]) | set(flat))
        b = bundle_from(raw)
        # ① 正式加载
        errors = cross_validate(b).errors
        assert any(
            "a41.food.fresh" in e and ("评分侧的参数键" in e or name in e) for e in errors
        ), (name, errors)
        # ② 可见投影（绕过加载检查直接构造观察）
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            build_observation(
                permission=ObservationPermission(**RESEARCH),
                profile=profile_of(b, "u11"),
                state=state_of(b, "u11.a41.c0"),
                catalog=b.catalog_for(A41),
            )
        # ③ 最终 payload（绕过前两道，直接改已构造好的观察）
        opps = tuple(
            o.model_copy(update={"attributes": {**o.attributes, **flat}})
            if o.opportunity_id == "a41.food.fresh"
            else o
            for o in clean.opportunities
        )
        smuggled = clean.model_copy(update={"opportunities": opps})
        assert type(smuggled) is AgentObservation
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            observation_payload(smuggled)


def test_normal_facts_stay_visible(bundle3s):
    _, r = settle_year_of(bundle3s, "u11.a41.c0")
    for permission in (RESEARCH, PRODUCT):
        obs = build_observation(
            permission=ObservationPermission(**permission),
            profile=profile_of(bundle3s, "u11"),
            state=r.next_state,
            catalog=bundle3s.catalog_for(A41),
            last_feedback=visible_feedback(r),
        )
        payload = observation_payload(obs)
        opps = {o["opportunity_id"]: o for o in payload["opportunities"]}
        assert opps["a41.durable.phone_premium"]["attributes"] == {
            "brand": "premium_phone",
            "tier": "premium",
        }
        assert opps["a41.food.ready_meals"]["attributes"] == {
            "prep_mode": "ready_made",
            "cuisine": "italian",
        }
        near = opps["a41.housing.near"]["spec"]
        assert (near["floor_area_sqft"], near["rooms"], near["commute_roundtrip_minutes"]) == (
            600,
            1,
            30,
        )
        assert opps["a41.job.office"]["spec"]["work_mode"] == "onsite"
        assert opps["a41.durable.cabinet"]["functions"] == ["storage"]


def test_reserved_keys_never_collide_with_visible_fields():
    from pydantic import BaseModel

    import agentlayer.life_views as views

    names: set[str] = set()
    seen: set[type] = set()

    def walk(model: type) -> None:
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            names.add(name)
            stack = [field.annotation]
            while stack:
                ann = stack.pop()
                stack.extend(getattr(ann, "__args__", ()))
                if isinstance(ann, type) and issubclass(ann, BaseModel):
                    walk(ann)

    walk(views.AgentObservation)
    walk(views.VisibleFeedback)
    assert not RESERVED_SCORING_KEYS & names
    assert RESERVED_SCORING_KEYS <= FORBIDDEN_PAYLOAD_KEYS


# ==========================================================================
# 兼容：默认合法样例的账目与评分不变
# ==========================================================================

ARCHIVED_DEMO = REPO / "runs/2026-10-07-e1-3s-demo/results.json"


def test_default_sample_accounts_and_scores_match_the_archived_demo(bundle3s, scoring3s):
    """边界修复只收紧检查：默认合法样例的月末现金、出局、持有数量与逐月评分与修复前逐项相同。"""
    archived = json.loads(ARCHIVED_DEMO.read_text(encoding="utf-8"))
    assert len(archived) == 6
    for entry in archived:
        s, r = settle_year_of(bundle3s, entry["clone_id"])
        o = r.outcome
        assert [m.cash_end.amount for m in o.months] == entry["month_end_cash"], entry["clone_id"]
        assert o.cash_end.amount == entry["cash_end"]
        assert (o.exit.model_dump(mode="json") if o.exit else None) == entry["exit"]
        assert [h.model_dump(mode="json") for h in o.holdings_service] == entry["holdings_service"]
        for source, old in entry["scores"].items():
            pref = (
                research_pref(bundle3s, entry["person_id"])
                if source == "research_synthetic_truth"
                else None
            )
            if pref is None:
                from econ.life_scoring import preference_from_stated_profile

                pref = preference_from_stated_profile(profile_of(bundle3s, entry["person_id"]))
            new = score_of(bundle3s, scoring3s, s, r, preference=pref).model_dump(mode="json")
            assert new == old, (entry["clone_id"], source)
