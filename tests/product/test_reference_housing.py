"""期末参考生活的住房（独立审核 F3、F4，DECISIONS/0042）：按参考岗位与通勤关系从完整候选池绑定，与年度菜单无关。

审核时的问题：参考住房从菜单裁剪后的目录里挑、不看参考岗位在哪——西雅图的固定住房从岗位片区出发超过通勤上限，三年都正常
生活的分身全部无法估值；菜单一换，参考集合也跟着换。规则 ``reference_housing_by_job_reach_v1`` 见
``product.realdata.reference_housing``。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from product.realdata.life_pack import load_pack_config
from product.realdata.reference_housing import choose_reference_housing

PACK = Path("data/frozen/life/us-v1/pack")
FROZEN = Path("data/frozen/life/us-v1")


def home(oid: str, area: str, price: float, bedrooms: int = 1, quality: int = 2) -> dict:
    return {
        "opportunity_id": oid,
        "area": area,
        "spec": {"bedrooms": bedrooms, "quality_level": quality},
        "price": {"amount": {"amount": price, "numeraire": "USD_2024_LOCAL"}},
    }


def leg(h: str, w: str, mode: str, minutes: int) -> dict:
    return {"home_area": h, "work_area": w, "mode": mode, "roundtrip_minutes": minutes}


MODES = [
    {"mode": "walk_bike"},
    {"mode": "transit"},
    {"mode": "drive", "requires_function": "car_access"},
]
JOB_W = [{"opportunity_id": "x.job.a", "area": "w1", "availability": "available"}]


def test_basic_is_the_cheapest_home_that_reaches_every_reference_job():
    housing = [
        home("x.housing.far", "h_far", 500, bedrooms=0, quality=1),  # 最便宜，但没有一条线到岗位
        home("x.housing.ok", "h_ok", 900, bedrooms=0, quality=1),
        home("x.housing.mid", "h_near", 1500),
    ]
    rel = {
        "modes": MODES,
        "legs": [leg("h_ok", "w1", "transit", 80), leg("h_near", "w1", "walk_bike", 20)],
    }
    ref = choose_reference_housing(housing, JOB_W, rel)
    assert ref.job_areas == ("w1",)
    assert ref.slots["housing_basic"] == "x.housing.ok"  # 不是到不了岗位的 500
    assert ref.worst_minutes["housing_basic"] == 80
    assert ref.slots["housing_comfort"] == "x.housing.mid"
    assert ref.slots["housing_basic_car"] is None and "不设用车" in ref.reasons["housing_basic_car"]


def test_comfort_is_the_one_bedroom_mid_tier_with_the_shortest_worst_commute():
    housing = [
        home("x.housing.a", "h_a", 1400),
        home("x.housing.b", "h_b", 1600),
        home("x.housing.c", "h_c", 1300, bedrooms=2),  # 两卧：不是“一卧中档”
    ]
    rel = {
        "modes": MODES,
        "legs": [
            leg("h_a", "w1", "transit", 70),
            leg("h_b", "w1", "transit", 30),
            leg("h_c", "w1", "walk_bike", 10),
        ],
    }
    ref = choose_reference_housing(housing, JOB_W, rel)
    assert (
        ref.slots["housing_comfort"] == "x.housing.b" and ref.worst_minutes["housing_comfort"] == 30
    )
    assert ref.slots["housing_basic"] == "x.housing.c"  # 基本住房只看合法与价格


def test_every_reference_job_area_must_be_reachable():
    jobs = [*JOB_W, {"opportunity_id": "x.job.b", "area": "w2", "availability": "available"}]
    housing = [home("x.housing.a", "h_a", 1000), home("x.housing.b", "h_b", 1100)]
    rel = {
        "modes": MODES,
        "legs": [
            leg("h_a", "w1", "walk_bike", 20),  # 到不了 w2
            leg("h_b", "w1", "transit", 40),
            leg("h_b", "w2", "transit", 50),
        ],
    }
    ref = choose_reference_housing(housing, jobs, rel)
    assert ref.slots["housing_basic"] == "x.housing.b" and ref.worst_minutes["housing_basic"] == 50


def test_a_car_reference_life_only_when_no_home_reaches_without_a_car():
    jobs = [*JOB_W, {"opportunity_id": "x.job.b", "area": "w2", "availability": "available"}]
    housing = [home("x.housing.a", "h_a", 1000), home("x.housing.b", "h_b", 1200)]
    rel = {
        "modes": MODES,
        "legs": [
            leg("h_a", "w1", "walk_bike", 20),
            leg("h_a", "w2", "drive", 40),
            leg("h_b", "w1", "drive", 30),
            leg("h_b", "w2", "drive", 30),
        ],
    }
    ref = choose_reference_housing(housing, jobs, rel)
    assert ref.slots["housing_basic"] is None and ref.slots["housing_comfort"] is None
    assert "没有不用车" in ref.reasons["housing_basic"]
    assert ref.slots["housing_basic_car"] == "x.housing.a"  # 开车能到两个片区里最便宜的
    assert ref.slots["housing_comfort_car"] == "x.housing.b"  # 最长往返 30 < 40
    assert ref.worst_minutes["housing_comfort_car"] == 30


def test_no_home_reaches_even_by_car_is_reported_not_relaxed():
    housing = [home("x.housing.a", "h_a", 1000)]
    rel = {"modes": MODES, "legs": []}
    ref = choose_reference_housing(housing, JOB_W, rel)
    assert ref.bound() == []
    assert "开车也没有" in ref.reasons["housing_basic_car"]


# --------------------------------------------------------------------------- 组装：参考集合与菜单无关


def _cand(puma: str, bed: str, pos: str, rent: str) -> dict:
    return {
        "opportunity_id": f"housing.b.x.{puma}.{bed}.{pos}",
        "stratum": bed,
        "supply_status": "offered",
        "target_weight": "10",
        "geo": {"code": puma, "label": f"Somewhere {puma} PUMA; Texas"},
        "attributes": {
            "price_position": pos,
            "utilities_median_month": "120.00",
            "price_level_used": "tier_bed",
        },
        "facts": [{"name": "monthly_rent", "status": "present", "value": rent}],
    }


def _hou_inputs():
    """休斯敦的真实通勤与生活机会资料（冻结、进仓库），加手写的住房候选：离就业中心近的片区几处、远的片区很多便宜的。"""
    commute = json.loads((FROZEN / "commute" / "hou.json").read_text(encoding="utf-8"))
    items = json.loads((FROZEN / "items" / "26420.json").read_text(encoding="utf-8"))
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
    cands = []
    for h in far:  # 远处很多便宜的：菜单常常抽到它们
        puma = h.rsplit(".", 1)[1]
        for bed in ("0", "1", "2", "3plus"):
            for pos in ("1/6", "1/2", "5/6"):
                cands.append(_cand(puma, bed, pos, "600.00"))
    for i, h in enumerate(near):
        puma = h.rsplit(".", 1)[1]
        for bed in ("0", "1", "2"):
            for pos in ("1/6", "1/2", "5/6"):
                cands.append(_cand(puma, bed, pos, f"{1500 + 50 * i}.00"))
    counties = {h.rsplit(".", 1)[1]: ("48201",) for h in near + far}
    return commute, items, cands, counties


def _build(seed: str):
    from product.realdata.life_assembly import assemble_city
    from product.realdata.pack_inputs import menu_settings_identity

    cfg = copy.deepcopy(load_pack_config())
    cfg["menus"]["seed"] = seed
    hou = next(c for c in cfg["cities"] if c["key"] == "hou")
    commute, items, cands, counties = _hou_inputs()
    return assemble_city(
        hou,
        cfg,
        candidates=cands,
        puma_counties=counties,
        commute=commute,
        items=items,
        settings_identity=menu_settings_identity(cfg),
    )


def test_changing_only_the_menu_seed_keeps_the_reference_housing():
    a, b = _build("seed-one"), _build("seed-two")
    menus = lambda cb: {  # noqa: E731
        m["menu_id"]: m["opportunity_ids"]
        for m in cb.catalog["menus"]
        if m["category"] == "housing"
    }
    assert menus(a) != menus(b)  # 菜单确实换了
    assert a.reference.slots == b.reference.slots  # 参考住房没变
    for cb in (a, b):
        ids = {o["opportunity_id"] for o in cb.catalog["opportunities"]}
        assert set(cb.reference.bound()) <= ids  # 参考住房一定在目录里（菜单没抽到也在）
        on_menu = {i for v in menus(cb).values() for i in v}
        assert cb.reference.slots["housing_basic"] is not None
        # 参考专用项不因此进菜单：没被抽中就不在任何一份菜单上
        extra = set(cb.reference.bound()) - on_menu
        assert all(i not in on_menu for i in extra)


# --------------------------------------------------------------------------- 冻结的资料包


@pytest.fixture(scope="module")
def pack():
    from ledger.life_catalog import load_life_bundle
    from ledger.life_evaluation_config import load_evaluation_config
    from ledger.life_scoring_config import load_scoring_config

    bundle, _ = load_life_bundle(PACK / "life_input.json", require_settlement_ready=True)
    scoring, _ = load_scoring_config(PACK / "scoring.json", bundle)
    ev = load_evaluation_config(PACK / "evaluation.json", bundle, scoring)
    return bundle, ev


def _job_areas(cat) -> set[str]:
    return {
        o.area for o in cat.opportunities if o.category == "job" and o.availability == "available"
    }


def _reach(cat, home_area: str, work_area: str, car: bool) -> int | None:
    req = {m.mode: m.requires_function for m in cat.commute.modes}
    mins = [
        g.roundtrip_minutes
        for g in cat.commute.legs
        if g.home_area == home_area and g.work_area == work_area and (car or req[g.mode] is None)
    ]
    return min(mins) if mins else None


def test_every_bound_reference_home_reaches_every_reference_job(pack):
    bundle, ev = pack
    for b in ev.spec.city_bindings:
        cat = bundle.catalog_for(str(b.city))
        areas = _job_areas(cat)
        for slot, car in (
            ("housing_basic", False),
            ("housing_comfort", False),
            ("housing_basic_car", True),
            ("housing_comfort_car", True),
        ):
            t = b.target(slot)
            if t.status != "opportunity":
                assert t.status == "unavailable" and t.reason
                continue
            h = cat.opportunity(t.opportunity_id)
            for w in areas:
                assert _reach(cat, h.area, w, car) is not None, (b.city, slot, w)
        car_slot = b.target("car_access")
        assert car_slot.status == "opportunity" and car_slot.opportunity_id.endswith(
            ".service.car_access"
        )


def test_the_switch_job_templates_check_their_target_job(pack):
    bundle, ev = pack
    for b in ev.spec.city_bindings:
        q = b.target("qualified_job")
        if q.status != "opportunity":
            continue
        cat = bundle.catalog_for(str(b.city))
        area = cat.opportunity(q.opportunity_id).area
        for slot in ("housing_basic", "housing_comfort"):
            t = b.target(slot)
            assert t.status == "opportunity"
            assert _reach(cat, cat.opportunity(t.opportunity_id).area, area, False) is not None


def test_car_reference_lives_only_where_needed(pack):
    _, ev = pack
    for b in ev.spec.city_bindings:
        code = str(b.city).rsplit(":", 1)[1]
        basic, car = b.target("housing_basic"), b.target("housing_basic_car")
        if code == "41860":  # 旧金山：没有公交资料，岗位分在两个就业片区，不用车到不了全部参考岗位
            assert basic.status == "unavailable" and car.status == "opportunity"
        else:
            assert basic.status == "opportunity" and car.status == "unavailable"
    templates = {p.template_id: p for p in ev.spec.life_templates}
    assert "car_access" in templates["p1_basic_car"].service_slots
    assert "car_access" not in templates["p1_basic"].service_slots


def test_reference_only_homes_stay_out_of_the_agents_menu(pack):
    bundle, ev = pack
    for b in ev.spec.city_bindings:
        cat = bundle.catalog_for(str(b.city))
        on_menu = {i for m in cat.menus if m.category == "housing" for i in m.opportunity_ids}
        bound = {
            t.opportunity_id
            for t in b.targets
            if t.status == "opportunity" and cat.opportunity(t.opportunity_id).category == "housing"
        }
        baseline = {m.baseline_opportunity_id for m in cat.minimum_living}
        for oid in bound - on_menu - baseline:
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
    bound = {t.opportunity_id for t in spec.binding_for(hou).targets if t.opportunity_id}
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
    assert value(other) == first


def test_reference_only_homes_are_not_in_the_agents_observation(tmp_path, monkeypatch):
    """估值专用的参考住房在目录里、不在菜单上：观察的机会与最低生活合格名单里都没有它们（合格名单只列这一年能写进计划的）。"""
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
        t.opportunity_id
        for t in w.specs[next(iter(w.specs))].binding_for(sf).targets
        if t.opportunity_id and cat.opportunity(t.opportunity_id).category == "housing"
    }
    hidden = ref - offered
    assert hidden  # 旧金山的用车参考住房这一年不在菜单上
    for oid in hidden:
        assert oid not in text
    for need in obs.minimum_living:
        assert set(need.qualifying_opportunity_ids) <= offered
