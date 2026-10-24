"""规划请求的可逆紧凑表示（E3B 映射修正 §7、E3B 收尾 §6）：按板块分表、食品组合展开、文字模板、查表与编号引用。

都必须 decode(encode(x)) == x（类型敏感；缺键与 null、0 与 null、1 与 1.0 与 true 各不相同），只省重复，不删不改，也不带进
载荷以外的任何内容；同一份载荷不论键的先后都渲染出逐字相同的文字。"""

from __future__ import annotations

import copy
import json
import random
import re

import pytest

from agentlayer.compact_payload import (
    GRID,
    IDS,
    SECTIONS,
    TABLE,
    CompactError,
    decode,
    encode,
    encode_payload,
)
from agentlayer.life_planning import PROMPT_SYSTEM, payload_from_user_prompt, render_prompt


def canon(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for k, v in x.items() for s in (k, *strings(v))]
    if isinstance(x, list):
        return [s for v in x for s in strings(v)]
    return []


# ---------------------------------------------------------------------- 食品网格（与资料包同一生成关系的合成样子）

BASKETS = {"staples": "主食为主", "varied": "荤素搭配", "fresh": "鲜肉蔬果较多"}
MODES = {"dine_out": "外食", "delivery": "外送", "half": "外食外送各半"}
CUISINES = {
    "chinese": "中餐",
    "italian": "意式",
    "mexican": "墨西哥菜",
    "japanese": "日料",
    "indian": "印度菜",
    "thai": "泰国菜",
    "korean": "韩国菜",
    "vietnamese": "越南菜",
    "mediterranean": "地中海菜",
}
OSM = dict(zip(CUISINES, (128, 124, 90, 77, 40, 35, 0, 35, 12), strict=True))


def food_rows(city: str = "chi") -> list[dict]:
    """3 个篮子 ×（0 / 3 / 7 / 14 顿在家 × 3 种外面的方式 × 9 个菜系 + 全在家 1 个）= 327，生成顺序与资料包相同。"""
    rows = []
    for b, (bk, blabel) in enumerate(BASKETS.items()):
        for h in (0, 3, 7, 14):
            for m, (mk, mlabel) in enumerate(MODES.items()):
                for ck, clabel in CUISINES.items():
                    away = 21 - h
                    parts = []
                    if h:
                        parts.append(
                            {
                                "part_id": "home",
                                "mode": "home_cooked",
                                "meals_per_week": float(h),
                                "cuisine": "home_style",
                            }
                        )
                    for pid, mode, share in (
                        ("out", "dine_out", 1.0),
                        ("delivery", "delivery", 1.0),
                    ):
                        if mk in (mode, "half"):
                            meals = away * (0.5 if mk == "half" else share)
                            parts.append(
                                {
                                    "part_id": pid,
                                    "mode": mode,
                                    "meals_per_week": meals,
                                    "cuisine": ck,
                                }
                            )
                    rows.append(
                        {
                            "opportunity_id": f"{city}.food.{bk}.h{h:02d}.{mk}.{ck}",
                            "category": "food",
                            "label": f"自己做 {h} 顿·{blabel} + {mlabel} {away} 顿（{clabel}）",
                            "quality": f"每周 {h} 顿自己做（{blabel}），另外 {away} 顿{clabel}{mlabel}；外面的钱含在月费里，不另收",
                            "price": {
                                "kind": "actual_quote",
                                "amount": {
                                    "amount": round(300 + 40 * b + 30 * away + 7 * m, 2),
                                    "numeraire": "USD_2024_LOCAL",
                                },
                                "billing": "per_month",
                            },
                            "attributes": {
                                "basket": f"{bk}_home",
                                "away_mode": mk,
                                "home_meals_per_week": h,
                                "osm_restaurants_with_cuisine": OSM[ck],
                                "cuisine_supply_evidence": "osm_presence",
                            },
                            "food_plan": {
                                "covers_basic_month": True,
                                "meals_per_week": 21.0,
                                "parts": parts,
                            },
                        }
                    )
        rows.append(
            {
                "opportunity_id": f"{city}.food.{bk}.h21",
                "category": "food",
                "label": f"自己做·{blabel}",
                "quality": f"{blabel}的家常做法，按热量补齐一周",
                "price": {
                    "kind": "actual_quote",
                    "amount": {"amount": 250.0 + b, "numeraire": "USD_2024_LOCAL"},
                    "billing": "per_month",
                },
                "attributes": {"basket": f"{bk}_home", "home_meals_per_week": 21},
                "food_plan": {
                    "covers_basic_month": True,
                    "meals_per_week": 21.0,
                    "parts": [
                        {
                            "part_id": "home",
                            "mode": "home_cooked",
                            "meals_per_week": 21.0,
                            "cuisine": "home_style",
                        }
                    ],
                },
            }
        )
    return rows


def expanded_ids(enc: dict) -> list[str]:
    return [r["opportunity_id"] for r in decode(enc)]


def test_food_is_factorized_into_base_combos_and_cuisines_and_expands_exactly():
    rows = food_rows()
    assert len(rows) == 327
    enc = encode(rows)
    assert set(enc) == {GRID}
    g = enc[GRID]
    assert g["axis"] == "cuisine"
    values = decode(g["values"])
    assert [v["key"] for v in values] == list(CUISINES)
    assert [v["label"] for v in values] == list(CUISINES.values())
    # 菜系的证据只写一次（每个菜系一行），不在 36 个基础组合里重复
    assert [v["osm_restaurants_with_cuisine"] for v in values] == list(OSM.values())
    base, single = decode(g["rows"]), decode(g["single"])
    assert len(base) == 36 and len(single) == 3
    assert base[0]["opportunity_id"] == "chi.food.staples.h00.dine_out.{cuisine.key}"
    assert [r["opportunity_id"] for r in single] == [f"chi.food.{b}.h21" for b in BASKETS]
    # 合法组合精确展开回原来的 327 项，顺序与取值（含类型：餐数 3.0 与 3、餐馆数 0）都不变
    assert canon(decode(enc)) == canon(rows)
    assert expanded_ids(enc) == [r["opportunity_id"] for r in rows]
    # 原文里的菜系名、编号与数字由维度表逐项填回
    korean = next(
        r for r in decode(enc) if r["opportunity_id"] == "chi.food.varied.h07.half.korean"
    )
    assert korean["label"] == "自己做 7 顿·荤素搭配 + 外食外送各半 14 顿（韩国菜）"
    assert korean["attributes"]["osm_restaurants_with_cuisine"] == 0
    assert [p["cuisine"] for p in korean["food_plan"]["parts"]] == [
        "home_style",
        "korean",
        "korean",
    ]


def test_a_combo_updated_by_its_own_quote_is_kept_as_an_exception():
    rows = food_rows()
    i = next(
        k for k, r in enumerate(rows) if r["opportunity_id"] == "chi.food.fresh.h03.delivery.thai"
    )
    rows[i]["price"]["amount"]["amount"] = 999.99  # 这一个组合有了自己的本地报价
    del rows[i]["attributes"]["cuisine_supply_evidence"]  # 缺键也逐项保存
    enc = encode(rows)
    g = enc[GRID]
    assert set(g["overrides"]) == {"chi.food.fresh.h03.delivery.thai"}
    over = g["overrides"]["chi.food.fresh.h03.delivery.thai"]
    assert set(over) == {"attributes", "price"}
    back = decode(enc)
    assert canon(back) == canon(rows)
    assert back[i]["price"]["amount"]["amount"] == 999.99
    assert "cuisine_supply_evidence" not in back[i]["attributes"]
    # 同一基础组合的其他菜系不受影响
    assert back[i - 1]["price"]["amount"]["amount"] != 999.99


def test_combos_without_a_full_cartesian_product_are_stored_explicitly():
    rows = [r for r in food_rows() if r["opportunity_id"] != "chi.food.varied.h14.delivery.indian"]
    enc = encode(rows)
    back = decode(enc)
    assert canon(back) == canon(rows)
    ids = [r["opportunity_id"] for r in back]
    assert "chi.food.varied.h14.delivery.indian" not in ids  # 不补出不存在的选项
    assert len(ids) == 326
    # 缺了一个菜系的那个基础组合整组写成单项，其余照样展开
    single = [r["opportunity_id"] for r in decode(enc[GRID]["single"])]
    assert "chi.food.varied.h14.delivery.thai" in single


def test_braces_in_the_original_text_disable_placeholders():
    rows = food_rows()
    rows[5]["quality"] = "原文里本来就有 {cuisine.label} 这几个字"
    enc = encode(rows)
    assert GRID not in enc  # 不用占位，免得与原文混淆
    assert canon(decode(enc)) == canon(rows)


# ---------------------------------------------------------------------- 行表：模板、查表、相同列、分组


def job_rows() -> list[dict]:
    occ = [
        ("13-1071", "Human Resources Specialists", "人力资源专员", "edu_at_least_bachelor"),
        ("35-3023", "Fast Food and Counter Workers", "快餐店员", None),
    ]
    out = []
    for n, (code, title, zh, need) in enumerate(occ * 4):
        hours = (35, 40, 45, 50)[n % 4]
        row = {
            "opportunity_id": f"chi.job.{code}.w{n:02d}.h{hours}.w.17031{n:02d}",
            "category": "job",
            "label": f"{zh}·工资约在本职业 {10 * n + 5} 分位",
            "quality": f"{title}；每周约 {hours} 小时、每月 22 个工作日"
            + (f"；要求 {need}" if need else ""),
            "occupation": {"scheme": "SOC2018", "code": code},
            "attributes": {"soc_title": title, "employment": 24080 if need else 104270},
            "compensation": {
                "amount": {"amount": 40000.0 + 1000 * n, "numeraire": "USD_2024_LOCAL"}
            },
            "area": f"chi.area.w.17031{n % 3:02d}",
            "price": None,
        }
        if need:
            row["eligibility"] = [f"ability:{need}"]
        out.append(row)
    return out


def table_of(enc):
    return enc[TABLE]


def test_text_templates_lookups_and_equal_columns_round_trip():
    rows = job_rows()
    enc = encode(rows)
    t = table_of(enc)
    assert canon(decode(enc)) == canon(rows)
    # 共同的文案写一次（模板里的 {0}、{1}… 由同一行的“列#0”“列#1”…填回）
    assert "label" in t["format"] and "工资约在本职业" in t["format"]["label"]
    assert "label" not in t["columns"] or any("label#" in c for c in t["columns"])
    # 职业名称由职业代码决定：查表，每个代码只写一次
    lookups = t["lookup"]
    assert any(lk["key"] == "occupation.code" for lk in lookups)
    # 每行都相同的值进 common；price 是 null（键存在）照样保留
    assert t["common"]["price"] is None and t["common"]["occupation.scheme"] == "SOC2018"
    # 没有 eligibility 的岗位还原后仍然没有这个键（不是 null，也不是空列表）
    back = decode(enc)
    assert "eligibility" not in back[1] and back[0]["eligibility"] == [
        "ability:edu_at_least_bachelor"
    ]


def test_values_that_only_look_equal_are_not_merged():
    rows = [
        {"k": 1, "same": "a"},
        {"k": 1.0, "same": "a"},
        {"k": True, "same": "a"},
        {"k": 0, "same": "a"},
        {"k": None, "same": "a"},
    ]
    enc = encode(rows)
    t = enc[TABLE]
    assert t["common"] == {"same": "a"}
    back = decode(enc)
    assert canon(back) == canon(rows)  # 1、1.0、true、0、null 各自保留
    assert [type(r["k"]).__name__ for r in back] == ["int", "float", "bool", "int", "NoneType"]


def test_missing_key_null_zero_and_empty_containers_stay_distinct():
    rows = [
        {"id": "a", "x": None, "y": 0, "z": {}, "w": []},
        {"id": "b", "y": 0.0, "z": {"q": None}},
        {"id": "c", "x": 0, "w": [0]},
    ]
    payload = {"observation": {"rows": rows}}
    assert canon(decode(encode_payload(payload))) == canon(payload)


def test_contiguous_rows_are_grouped_by_one_column():
    legs = [
        {
            "home_area": f"x.area.h.{h}",
            "work_area": f"x.area.w.{w}",
            "mode": m,
            "roundtrip_minutes": 30 + h + w,
            "cost_per_roundtrip": {"amount": 2.5, "numeraire": "USD_2024_LOCAL"},
        }
        for h in range(6)
        for w in range(5)
        for m in ("transit", "drive")
    ]
    enc = encode(legs)
    t = enc[TABLE]
    assert "group" in t and t["group"].startswith("home_area")
    assert canon(decode(enc)) == canon(legs)


def test_nested_object_lists_share_one_sub_table():
    rows = food_rows()[:20]
    enc = encode(rows)
    # 食品前 20 项不够成组合展开时也照样还原；嵌套的 parts 共用一份子表头
    assert canon(decode(enc)) == canon(rows)


# ---------------------------------------------------------------------- 分板块、编号引用、整份请求


def observation() -> dict:
    housing = [
        {
            "opportunity_id": f"chi.housing.17031{i:02d}.1.s{i:04d}",
            "category": "housing",
            "label": f"1 卧整套租住（Place {i % 4}，中档）",
            "price": {"amount": {"amount": 1000.0 + i, "numeraire": "USD_2024_LOCAL"}},
            "area": f"chi.area.h.17031{i % 4:02d}",
        }
        for i in range(8)
    ]
    opps = housing[:4] + job_rows() + food_rows() + housing[4:]
    return {
        "year_index": 1,
        "cash": {"amount": 0.0, "numeraire": "USD_2024_LOCAL"},
        "opportunities": opps,
        "minimum_living": [
            {
                "need": "housing",
                "qualifying_opportunity_ids": sorted(o["opportunity_id"] for o in housing),
            },
            {
                "need": "food",
                "qualifying_opportunity_ids": sorted(
                    o["opportunity_id"] for o in opps if o["category"] == "food"
                ),
            },
            {
                "need": "partial",
                "qualifying_opportunity_ids": [
                    housing[0]["opportunity_id"],
                    housing[1]["opportunity_id"],
                ],
            },
        ],
        "last_feedback": None,
    }


def test_heterogeneous_opportunities_get_one_table_per_section_and_keep_their_order():
    payload = {"request_id": "run-e3a-0000000000000000/c1/y1/a1", "observation": observation()}
    enc = encode_payload(payload)
    s = enc["observation"]["opportunities"][SECTIONS]
    assert s["key"] == "category"
    assert s["order"] == [["housing", 4], ["job", 8], ["food", 327], ["housing", 4]]
    assert set(s["parts"]) == {"housing", "job", "food"}
    assert GRID in s["parts"]["food"]
    # 各板块各用自己的列：住房表里没有食品或岗位的字段
    housing_cols = set(s["parts"]["housing"][TABLE]["columns"]) | set(
        s["parts"]["housing"][TABLE]["common"]
    )
    assert not any(c.startswith(("food_plan", "occupation", "compensation")) for c in housing_cols)
    assert canon(decode(enc)) == canon(payload)


def test_lists_of_all_ids_in_a_section_are_written_as_references():
    payload = {"observation": observation()}
    enc = encode_payload(payload)
    text = canon(enc["observation"]["minimum_living"])
    assert canon({IDS: "food"}) in text and canon({IDS: "housing"}) in text
    # 只是部分编号的列表照原样写
    assert "chi.housing.1703100.1.s0000" in text
    assert canon(decode(enc)) == canon(payload)


def test_rendering_is_canonical_and_deterministic():
    payload = {"request_id": "r", "observation": observation()}
    shuffled = json.loads(json.dumps(payload))
    rng = random.Random(7)

    def shuffle(x):
        if isinstance(x, dict):
            items = list(x.items())
            rng.shuffle(items)
            return {k: shuffle(v) for k, v in items}
        if isinstance(x, list):
            return [shuffle(v) for v in x]
        return x

    a = render_prompt(payload)
    b = render_prompt(shuffle(shuffled))
    assert a == b
    assert canon(payload_from_user_prompt(a[1])) == canon(payload)


def test_compact_form_carries_nothing_from_outside_the_payload():
    """压缩字典里只有载荷自己的内容：每段文字去掉占位以后，都是载荷里某段文字、键名或字段路径的一部分，加上少数结构词。"""
    payload = {"observation": observation()}
    enc = encode_payload(payload)
    source = strings(payload)
    structural = {
        "$table",
        "$sections",
        "$grid",
        "$ids",
        "$absent",
        "flat",
        "columns",
        "rows",
        "sparse_rows",
        "common",
        "format",
        "same",
        "lookup",
        "nested",
        "key",
        "values",
        "group",
        "groups",
        "parts",
        "order",
        "axis",
        "single",
        "overrides",
        "label",
    }
    pieces_re = re.compile(r"\{(?:\d+|cuisine\.[A-Za-z0-9_.]+)\}|#\d+")
    for s in strings(enc):
        if s in structural or s.isdigit():
            continue
        for piece in pieces_re.split(s):
            for part in piece.split("."):
                if part:
                    assert any(part in x for x in source), (s, part)


def test_reserved_keys_are_refused():
    with pytest.raises(CompactError, match="保留键"):
        encode_payload({"x": [{"$absent": 1}, {"a": 2}]})


def test_the_prompt_explains_every_construct():
    for word in (
        "$table",
        "$sections",
        "$grid",
        "$ids",
        "$absent",
        "format",
        "lookup",
        "same",
        "nested",
        "group",
        "overrides",
        "single",
    ):
        assert word in PROMPT_SYSTEM


def test_a_real_request_round_trips_and_shrinks(bundle, scoring):
    from agentlayer.life_llm import _payload_from_body

    from .test_life_extension import request_for

    payload = request_for(bundle, scoring)
    system, user = render_prompt(payload)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert len(user) < 0.9 * len(plain)
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    assert canon(_payload_from_body(body)) == canon(payload)
    edited = copy.deepcopy(payload)
    edited["observation"]["cash"] = None  # 任何改动都改变渲染出来的文字
    assert render_prompt(edited)[1] != user
