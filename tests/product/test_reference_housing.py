"""期末参考住房候选（E3B 收尾，``reference_housing_coverage_commute_v3``）：全城就业覆盖 3/5 编译成选择器，与年度菜单无关。

编译这一半在 ``product.realdata.reference_housing``；估值期初按实际岗位、交通能力与通勤硬要求选的那一半在 E1，测试见
``tests/life/test_reference_selector.py``。手算口径：覆盖率 = 有已知线路（任一方式）通达的就业片区流入权重 ÷ 全部就业片区
流入权重；一条线都没有的片区算未知、留在分母里；每个居住片区只留最便宜的一处与最便宜的一卧中档。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from product.realdata.life_pack import load_pack_config
from product.realdata.reference_housing import build_reference_housing

PACK = Path("data/frozen/life/us-v1/pack")
FROZEN = Path("data/frozen/life/us-v1")
HOUSING_SLOTS = ("housing_basic", "housing_comfort", "housing_basic_car", "housing_comfort_car")


def home(oid: str, area: str, rent: str, bedrooms: int = 1, quality: int = 2) -> dict:
    return {
        "opportunity_id": oid,
        "area": area,
        "gross_rent": rent,
        "bedrooms": bedrooms,
        "quality_level": quality,
    }


def commute(legs: list[tuple[str, str]], weights: dict[str, int]) -> dict:
    return {
        "content_identity": "a" * 64,
        "commute": {"legs": [{"home_area": h, "work_area": w} for h, w in legs]},
        "diagnostics": {
            "work_areas": {
                "areas": [{"area_id": a, "inbound_weight": n} for a, n in weights.items()]
            }
        },
    }


W = {"w1": 60, "w2": 30, "w3": 10}


def test_coverage_counts_any_known_line_and_keeps_unknown_areas_in_the_denominator():
    housing = [
        home("x.h.a1", "ha", "900.00", bedrooms=0, quality=1),
        home("x.h.b1", "hb", "800.00"),
        home("x.h.c1", "hc", "500.00"),
    ]
    rel = commute([("ha", "w1"), ("hb", "w2"), ("hb", "w3"), ("hc", "w2")], W)
    ref = build_reference_housing(housing, ["w1", "w2", "w3"], rel)
    assert ref.reason is None
    # ha：60/100 = 3/5 恰好达标；hb：已知 40/100 不足，加上未知 60 可能达到 → 资格未知；hc 同理
    assert ref.areas["ha"] == {"covered": 60, "unknown": 40, "coverage": "eligible"}
    assert ref.areas["hb"] == {"covered": 40, "unknown": 60, "coverage": "unknown"}
    assert ref.areas["hc"]["coverage"] == "unknown"
    basic = {c["opportunity_id"]: c for c in ref.selectors["basic"]["candidates"]}
    assert basic["x.h.a1"]["covered_weight"] == 60 and basic["x.h.a1"]["coverage"] == "eligible"
    assert ref.selectors["basic"]["work_areas"] == [
        {"area_id": a, "inbound_weight": n} for a, n in sorted(W.items())
    ]
    assert ref.selectors["basic"]["commute_identity"] == "a" * 64


def test_without_unreachability_evidence_an_area_is_never_excluded_only_unknown():
    """冻结资料里没有“明确不可达”的记录：缺线一律算未知，所以已知覆盖再低也只是资格未知，不会被当成确定不合格。"""
    housing = [home("x.h.a", "ha", "500.00")]
    ref = build_reference_housing(housing, ["w1", "w2", "w3"], commute([("ha", "w3")], W))
    assert ref.areas["ha"] == {"covered": 10, "unknown": 90, "coverage": "unknown"}
    assert ref.selectors["basic"]["excluded_home_areas"] == 0


def test_each_home_area_keeps_its_cheapest_home_and_its_cheapest_one_bed_mid():
    housing = [
        home("x.h.2", "ha", "700.00", bedrooms=0, quality=1),
        home("x.h.1", "ha", "700.00", bedrooms=2, quality=3),  # 同价按编号：x.h.1 在前
        home("x.h.3", "ha", "1200.00"),
        home("x.h.4", "ha", "1100.00"),
        home("x.h.5", "ha", "1000.00", bedrooms=1, quality=3),  # 一卧但不是中档
    ]
    ref = build_reference_housing(housing, ["w1"], commute([("ha", "w1")], {"w1": 5}))
    assert [c["opportunity_id"] for c in ref.selectors["basic"]["candidates"]] == ["x.h.1"]
    assert [c["opportunity_id"] for c in ref.selectors["comfort"]["candidates"]] == ["x.h.4"]
    assert ref.bound() == ["x.h.1", "x.h.4"]


def test_a_job_area_without_a_positive_weight_is_insufficient_data_not_equal_weights():
    housing = [home("x.h.a", "ha", "500.00")]
    ref = build_reference_housing(housing, ["w1", "w9"], commute([("ha", "w1")], {"w1": 5}))
    assert ref.selectors == {} and "不默认等权" in ref.reason


# --------------------------------------------------------------------------- 组装：参考候选与菜单无关


def _point(pid: str, rent: str, bedrooms: int, tier: int) -> dict:
    return {
        "id": pid,
        "weight": 10,
        "price_status": "observed",
        "gross_rent": rent,
        "contract_rent": rent,
        "kitchen": "complete",
        "plumbing": "complete",
        "bedrooms": bedrooms,
        "rooms": bedrooms + 2,
        "structure": "05",
        "year_built": 1990,
        "quality_tier": tier,
        "censor_bound": None,
        "censored_by": None,
        "records": [f"housing.acs2024.{pid}"],
    }


def _hou_inputs():
    """休斯敦的真实通勤与岗位模板（冻结、进仓库），加手写的住房目标格与支持表：离就业中心近的片区几处、远的片区很多便宜的。"""
    commute = json.loads((FROZEN / "commute" / "hou.json").read_text(encoding="utf-8"))
    items = json.loads((FROZEN / "items" / "26420.json").read_text(encoding="utf-8"))
    rules = json.loads((FROZEN / "items" / "manifest.json").read_text(encoding="utf-8"))[
        "job_rules"
    ]
    rel = commute["commute"]
    free = {m["mode"] for m in rel["modes"] if not m.get("requires_function")}
    homes = sorted({a["area_id"] for a in commute["areas"] if a["kind"] == "home"})
    work = "hou.area.w.4804600"
    free_minutes = {
        h: min(
            (
                g["roundtrip_minutes"]
                for g in rel["legs"]
                if g["home_area"] == h and g["work_area"] == work and g["mode"] in free
            ),
            default=10**6,
        )
        for h in homes
    }
    # 近处：不用车往返最短的几个片区；远处：不用车到不了，或不用车往返最长的几个片区
    near = sorted(homes, key=lambda h: (free_minutes[h], h))[:3]
    far = sorted(homes, key=lambda h: (-free_minutes[h], h))[:3]
    assert near and far and not set(near) & set(far)
    cells, tables = [], {}
    for h, rent in [(h, "600.00") for h in far] + [
        (h, f"{1500 + 50 * i}.00") for i, h in enumerate(near)
    ]:
        puma = h.rsplit(".", 1)[1]
        for bed, nbed in (("0", 0), ("1", 1), ("2", 2)):
            tid = f"t.{puma}.{bed}"
            tables[tid] = {
                "table_id": tid,
                "level": "puma_bed",
                "weight_sum": 30,
                "donors": {"n": 3, "utilities_median_month": "100.00", "assumption": "x"},
                "points": [_point(f"{tid}.s{k}", rent, nbed, k) for k in (1, 2, 3)],
            }
            cells.append(
                {
                    "opportunity_id": f"housing.b.x.{puma}.{bed}",
                    "stratum": bed,
                    "supply_status": "offered",
                    "target_weight": "10" if h in far else "1",  # 远处权重大：菜单常常抽到它们
                    "geo": {"code": puma, "label": f"Somewhere {puma} PUMA; Texas"},
                    "attributes": {"support_table": tid},
                }
            )
    counties = {h.rsplit(".", 1)[1]: ("48201",) for h in near + far}
    return commute, items, cells, tables, counties, rules


def _build(seed: str):
    from product.realdata.life_assembly import assemble_city
    from product.realdata.pack_inputs import menu_settings_identity

    cfg = copy.deepcopy(load_pack_config())
    cfg["menus"]["seed"] = seed
    cfg["menus"]["housing"]["size"] = 4
    cfg["menu_size_diagnostic"] = {"sizes": [2, 4], "repeats": 2}
    hou = next(c for c in cfg["cities"] if c["key"] == "hou")
    commute, items, cells, tables, counties, rules = _hou_inputs()
    return assemble_city(
        hou,
        cfg,
        cells=cells,
        tables=tables,
        puma_counties=counties,
        commute=commute,
        items=items,
        job_rules=rules,
        settings_identity=menu_settings_identity(cfg),
    )


def test_changing_only_the_menu_seed_keeps_the_reference_candidates():
    a, b = _build("seed-one"), _build("seed-two")
    menus = lambda cb: {  # noqa: E731
        m["menu_id"]: m["opportunity_ids"]
        for m in cb.catalog["menus"]
        if m["category"] == "housing"
    }
    assert menus(a) != menus(b)  # 菜单确实换了
    assert a.reference.selectors == b.reference.selectors  # 参考候选没变
    for cb in (a, b):
        ids = {o["opportunity_id"] for o in cb.catalog["opportunities"]}
        assert cb.reference.bound() and set(cb.reference.bound()) <= ids  # 候选一定在目录里


# --------------------------------------------------------------------------- 冻结的资料包


@pytest.fixture(scope="module")
def pack():
    from ledger.life_catalog import load_life_bundle
    from ledger.life_evaluation_config import load_evaluation_config
    from ledger.life_scoring_config import load_scoring_config

    bundle, _ = load_life_bundle(PACK / "life_input.json", require_settlement_ready=True)
    scoring, _ = load_scoring_config(PACK / "scoring.json", bundle)
    ev = load_evaluation_config(PACK / "evaluation.json", bundle, scoring)  # 加载器按目录重算覆盖
    return bundle, ev


def test_every_housing_slot_is_a_selector_shared_by_car_and_no_car_templates(pack):
    _, ev = pack
    for b in ev.spec.city_bindings:
        sel = {s: b.target(s).housing_selector for s in HOUSING_SLOTS}
        assert all(v is not None for v in sel.values()), b.city
        assert sel["housing_basic"] == sel["housing_basic_car"]
        assert sel["housing_comfort"] == sel["housing_comfort_car"]
        assert sel["housing_basic"].kind == "basic" and sel["housing_comfort"].kind == "comfort"
        assert sel["housing_basic"].rule_id == "reference_housing_coverage_commute_v3"
    templates = {p.template_id: p for p in ev.spec.life_templates}
    assert len(templates) == 12
    assert "car_access" in templates["p1_basic_car"].service_slots
    assert "car_access" not in templates["p1_basic"].service_slots


def test_seattle_potential_coverage_is_not_a_sixty_minute_rule(pack):
    """已知反例的口径：一处住房到每个就业片区都有线（哪怕有的往返超过 60 分钟），潜在覆盖就是 100%。"""
    bundle, ev = pack
    sea = "US:CBSA2023:42660"
    cat = bundle.catalog_for(sea)
    sel = ev.spec.binding_for(sea).target("housing_comfort").housing_selector
    slow = [
        c
        for c in sel.candidates
        if c.covered_weight == sel.total_weight
        and any(g.roundtrip_minutes > 60 for g in cat.commute.legs if g.home_area == c.area)
    ]
    assert slow and all(c.coverage == "eligible" for c in slow)


def test_reference_only_candidates_stay_out_of_the_agents_menu(pack):
    bundle, ev = pack
    for b in ev.spec.city_bindings:
        cat = bundle.catalog_for(str(b.city))
        on_menu = {i for m in cat.menus if m.category == "housing" for i in m.opportunity_ids}
        cands = {
            c.opportunity_id for s in HOUSING_SLOTS for c in b.target(s).housing_selector.candidates
        }
        baseline = {m.baseline_opportunity_id for m in cat.minimum_living}
        assert cands - on_menu - baseline  # 确实有参考专用的候选
        for oid in cands - on_menu - baseline:
            for env in (1, 2):
                for year in (1, 2, 3):
                    assert oid not in _offered(cat, year, env)


def _offered(cat, year: int, env: int) -> frozenset[str]:
    from common.life_contracts import PersonYearState

    state = PersonYearState.model_construct(
        clone_id="t.r0", year_index=year, env_repeat=env, residence=None
    )
    object.__setattr__(state, "employment", type("E", (), {"current_position_ids": ()})())
    return cat.offered_ids(state)


def test_with_data_and_terminal_state_fixed_new_menus_do_not_rewrite_v(tmp_path, monkeypatch):
    """F4 的另一半：资料、终态与参考规则都固定，只把目录里的年度菜单换成另一批住房与岗位，期末估值逐项相同。"""
    from econ.life_evaluation import verify_trajectory
    from econ.life_settlement import binding_from_policy
    from econ.life_terminal_value import value_terminal
    from econ.life_year import ScoringInputs
    from product import scenarios as sc

    hou = "US:CBSA2023:26420"
    monkeypatch.setattr(sc, "PACK_PATH", Path("configs/product/e3b_us_v1_pack.yaml"))
    s = sc.closed_loop(
        tmp_path,
        "f4",
        draft_kwargs={"abilities": ("edu_at_least_hs",), "cities": (hou,)},
        repeat_indexes=(0,),
    )
    world = s.pre.world
    bundle = world.bundle
    (clone,) = s.result.clones
    results = s.outcome.lives[clone.clone_ref]
    cat = bundle.catalog_for(hou)
    profile = bundle.profile_for(clone.clone_ref.split(".")[0])
    inputs = ScoringInputs(
        spec=world.scoring.spec,
        conditions=world.scoring.conditions_for(hou),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )
    tax = binding_from_policy(bundle.tax_policy(cat.tax_policy_id))
    spec = next(iter(world.specs.values()))
    traj = verify_trajectory(
        protocol=bundle.protocol, catalog=cat, tax=tax, scoring=inputs, results=results
    )
    targets = spec.binding_for(hou).targets
    bound = {t.opportunity_id for t in targets if t.opportunity_id} | {
        c.opportunity_id
        for t in targets
        if t.housing_selector is not None
        for c in t.housing_selector.candidates
    }
    swapped = []
    for m in cat.menus:
        pool = sorted(
            o.opportunity_id
            for o in cat.opportunities
            if o.category == m.category
            and o.opportunity_id not in m.opportunity_ids
            and o.opportunity_id not in bound
        )
        swapped.append(
            m.model_copy(update={"opportunity_ids": tuple(pool[: len(m.opportunity_ids)])})
        )
    other = cat.model_copy(update={"menus": tuple(swapped)})
    assert other.menus != cat.menus

    def value(catalog):
        v = value_terminal(
            spec=spec,
            protocol=bundle.protocol,
            catalog=catalog,
            tax=tax,
            scoring=inputs,
            terminal=traj.terminal,
            last_year=traj.last_year,
            trajectory_status="complete",
        )
        return (
            v.status,
            v.selected_plan_id,
            v.v_reference,
            [(p.plan_id, p.status, p.v_plan) for p in v.plans],
        )

    first = value(cat)
    assert first[0] == "complete" and first[2] is not None
    # 每个模板的住房都是岗位确定之后按选择器选的，绑定记录进评价证据
    v = value_terminal(
        spec=spec,
        protocol=bundle.protocol,
        catalog=cat,
        tax=tax,
        scoring=inputs,
        terminal=traj.terminal,
        last_year=traj.last_year,
        trajectory_status="complete",
    )
    # 走到住房这一步的分支都有绑定记录（在岗位资格这一步就失败的分支没有）
    for p in v.plans:
        if any(i.slot_role == "housing" for i in p.items):
            assert p.housing_binding is not None and p.housing_binding.status == "selected"
    best = next(p for p in v.plans if p.plan_id == v.selected_plan_id)
    assert best.housing_binding.status == "selected"
    assert best.housing_binding.opportunity_id in {i.opportunity_id for i in best.items}
    assert value(other) == first


def test_reference_only_homes_are_not_in_the_agents_observation(tmp_path, monkeypatch):
    """估值专用的参考候选在目录里、不在菜单上：观察的机会与最低生活合格名单里都没有它们（合格名单只列这一年能写进计划的）。"""
    import contextlib

    from agentlayer.life_views import build_observation
    from product import scenarios as sc

    sf = "US:CBSA2023:41860"
    monkeypatch.setattr(sc, "PACK_PATH", Path("configs/product/e3b_us_v1_pack.yaml"))
    s = sc.preflight_only(tmp_path, "obs", abilities=("edu_at_least_hs",), cities=(sf,))
    w = s.pre.world
    p = w.bundle.protocol
    state = next(st for st in w.bundle.initial_states if str(st.city) == sf)
    cat = w.bundle.catalog_for(sf)
    with contextlib.chdir(s.work):
        obs = build_observation(
            permission=w.permission,
            profile=w.bundle.profile_for(state.person_id),
            state=state,
            catalog=cat,
            resource_rules=p.resource_rules,
            energy_feedback=p.energy_feedback,
            career_rules=p.career_rules,
            savings_rules=p.savings_rules,
            health_rules=p.health_rules,
            health_input=p.health_input,
        )
    text = obs.model_dump_json()
    offered = cat.offered_ids(state)
    ref = {
        c.opportunity_id
        for t in w.specs[next(iter(w.specs))].binding_for(sf).targets
        if t.housing_selector is not None
        for c in t.housing_selector.candidates
    }
    hidden = ref - offered
    assert hidden  # 旧金山的大部分参考候选这一年不在菜单上
    for oid in hidden:
        assert oid not in text
    for need in obs.minimum_living:
        assert set(need.qualifying_opportunity_ids) <= offered


def test_duplicated_job_candidates_do_not_change_the_job_weights():
    housing = [home("x.h.a", "ha", "500.00"), home("x.h.b", "hb", "700.00")]
    rel = commute([("ha", "w1"), ("hb", "w2")], W)
    once = build_reference_housing(housing, ["w1", "w2", "w3"], rel)
    many = build_reference_housing(housing, ["w1", "w1", "w1", "w2", "w3", "w3"], rel)
    assert once.selectors == many.selectors and once.areas == many.areas
