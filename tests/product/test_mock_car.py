"""mock 的用车选择（独立审核 F5，DECISIONS/0042）：不用车到不了岗位或满足不了硬要求时，按公开事实考虑用车。

审核时的探针：旧金山一个失败的计划加上已有的用车服务就能合法完成，往返也满足 60 分钟——mock 原来明确不买用车权，把“mock 不考虑
用车”和“城市没有合法生活方案”混在一起。规则 M1b 只读请求里的通勤表、报价与功能（不读评分参数）。受控计划：买得起、买不起、
开车也到不了、不用车就行；另用真实资料包跑旧金山一个分身的离线闭环。
"""

from __future__ import annotations

from pathlib import Path

from agentlayer.life_mock import ProductMockPlanner, _View

MODES = [
    {"mode": "walk_bike"},
    {"mode": "transit"},
    {"mode": "drive", "requires_function": "car_access"},
]
NUM = "USD_2024_LOCAL"


def _money(x: float) -> dict:
    return {"amount": x, "numeraire": NUM}


def _house(oid: str, area: str, price: float) -> dict:
    return {
        "opportunity_id": oid,
        "category": "housing",
        "availability": "available",
        "area": area,
        "price": {"amount": _money(price), "billing": "per_month"},
    }


def _view(
    *,
    pay: float,
    cash: float,
    houses: list[dict],
    legs: list[dict],
    cap: float | None,
    car: bool = True,
):
    job = {
        "opportunity_id": "x.job.a",
        "category": "job",
        "availability": "available",
        "area": "x.area.w.1",
        "compensation": {"amount": _money(pay), "billing": "per_month"},
        "time_energy": {"job": {"onsite_days_per_month": 20}},
    }
    food = {
        "opportunity_id": "x.food.basic",
        "category": "food",
        "availability": "available",
        "price": {"amount": _money(300), "billing": "per_month"},
    }
    opps = [job, food, *houses]
    if car:
        opps.append(
            {
                "opportunity_id": "x.service.car_access",
                "category": "service",
                "availability": "available",
                "functions": ["car_access"],
                "price": {"amount": _money(601.5), "billing": "per_month"},
                "resource": {"basis": "per_service_month", "hours": 0},
            }
        )
    cheapest = min(houses, key=lambda h: h["price"]["amount"]["amount"])["opportunity_id"]
    hard = (
        []
        if cap is None
        else [
            {
                "need": "commute_limit",
                "requirement": {"category": "housing", "max_commute_roundtrip_minutes": cap},
            }
        ]
    )
    obs = {
        "opportunities": opps,
        "profile": {
            "six_dimension_points": {"material": "50", "opportunity": "10"},
            "material_allocation": {},
            "hard_requirements": hard,
        },
        "cash": _money(cash),
        "minimum_living": [
            {
                "need": "housing",
                "baseline_opportunity_id": cheapest,
                "qualifying_opportunity_ids": [h["opportunity_id"] for h in houses],
            },
            {
                "need": "food",
                "baseline_opportunity_id": "x.food.basic",
                "qualifying_opportunity_ids": ["x.food.basic"],
            },
        ],
        "employment": {"current": [{"opportunity_id": "x.job.a"}]},
        "commute": {"modes": MODES, "legs": legs},
        "training": [],
        "resources": {"household_tasks": []},
        "holdings": [],
        "abilities": [],
    }
    return _View({"observation": obs, "clone_id": "t.r0", "year_index": 1})


def _leg(home: str, mode: str, minutes: int, cost: float) -> dict:
    return {
        "home_area": home,
        "work_area": "x.area.w.1",
        "mode": mode,
        "roundtrip_minutes": minutes,
        "cost_per_roundtrip": _money(cost),
    }


def _decide(v: _View) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    action = ProductMockPlanner()._decide(v, notes)
    return [i["opportunity_id"] for i in action["items"]], notes


HOUSES = [
    _house("x.housing.far", "x.area.h.far", 900),
    _house("x.housing.near", "x.area.h.near", 1500),
]


def test_buys_the_car_when_only_driving_reaches_the_job_and_it_is_affordable():
    # 远处只有开车能到（往返 40 分钟，油费 3.47）；近处也只能开车（30 分钟）。月薪 5,000
    v = _view(
        pay=5000,
        cash=3000,
        houses=HOUSES,
        legs=[_leg("x.area.h.far", "drive", 40, 3.47), _leg("x.area.h.near", "drive", 30, 2.6)],
        cap=60,
    )
    items, notes = _decide(v)
    assert "x.service.car_access" in items and "x.housing.far" in items  # 开车能到岗里最便宜的
    # 年末现金 = 3,000 + 12 × (5,000 − 900 − 300 − 20 × 3.47 − 601.50) = 40,549.20 ≥ 一个月最低生活费 1,200
    assert any("M1b 用车" in n and "40,549" in n for n in notes)


def test_does_not_buy_a_car_it_cannot_afford():
    v = _view(
        pay=1500,
        cash=0,
        houses=HOUSES,
        legs=[_leg("x.area.h.far", "drive", 40, 3.47), _leg("x.area.h.near", "drive", 30, 2.6)],
        cap=60,
    )
    items, notes = _decide(v)
    assert "x.service.car_access" not in items
    # 12 × (1,500 − 900 − 300 − 69.40 − 601.50) < 0：买不起
    assert any("买不起" in n for n in notes)


def test_does_not_buy_a_car_that_still_cannot_reach():
    v = _view(pay=5000, cash=3000, houses=HOUSES, legs=[], cap=60)
    items, notes = _decide(v)
    assert "x.service.car_access" not in items
    assert any("开车也不行" in n for n in notes)


def test_keeps_living_without_a_car_when_a_home_already_works():
    v = _view(
        pay=5000,
        cash=3000,
        houses=HOUSES,
        legs=[_leg("x.area.h.near", "transit", 50, 5.0), _leg("x.area.h.far", "drive", 40, 3.47)],
        cap=60,
    )
    items, _ = _decide(v)
    assert "x.service.car_access" not in items and "x.housing.near" in items


def test_buys_the_car_to_meet_the_commute_limit():
    # 不用车能到，但公交往返 90 分钟超过硬要求 60；开车 40 分钟
    v = _view(
        pay=5000,
        cash=3000,
        houses=[_house("x.housing.far", "x.area.h.far", 900)],
        legs=[_leg("x.area.h.far", "transit", 90, 5.0), _leg("x.area.h.far", "drive", 40, 3.47)],
        cap=60,
    )
    items, _ = _decide(v)
    assert "x.service.car_access" in items


def test_without_a_public_car_offer_nothing_is_invented():
    v = _view(
        pay=5000,
        cash=3000,
        houses=HOUSES,
        legs=[_leg("x.area.h.far", "drive", 40, 3.47)],
        cap=60,
        car=False,
    )
    items, _ = _decide(v)
    assert all("car_access" not in i for i in items)


def test_a_san_francisco_clone_lives_legally_with_a_car(tmp_path, monkeypatch):
    """真实资料包：旧金山没有公交资料。mock 开车上班，三年都满足往返 60 分钟的硬要求，评分与估值完整。新问卷不再收集硬要求：
    这里的通勤上限来自带旧硬要求的历史画像（受控夹具），核验的是 E1 内部的硬要求机制。"""
    from product import scenarios as sc

    sf = "US:CBSA2023:41860"
    monkeypatch.setattr(sc, "PACK_PATH", Path("configs/product/e3b_us_v1_pack.yaml"))
    s = sc.closed_loop(
        tmp_path,
        "sf_car",
        draft_kwargs={"abilities": ("edu_at_least_hs",), "cities": (sf,)},
        repeat_indexes=(0, 1),
        legacy_hard_requirements=sc.LEGACY_COMMUTE_60,
    )
    assert s.result is not None
    for clone in s.result.clones:
        assert (clone.category, clone.missing_reason) == ("C", None), clone.explanation
        assert all(y.status == "completed" for y in clone.years)
        (req,) = clone.hard_requirements
        assert req.summary == "satisfied_every_month"
        for y in clone.years:  # 开车上班的年份就是买了用车权的年份；不用车能到岗的年份不买
            drives = any(t.mode == "drive" for g in y.commute for t in g.trips)
            assert drives == (y.car_access.status != "not_bought")
    assert any(
        y.car_access.status != "not_bought" for c in s.result.clones for y in c.years
    )  # 至少有一年的菜单上没有不用车就能到岗的住房，用车解决
