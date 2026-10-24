"""规划请求的可逆紧凑表示（E3B 映射修正 §7）：decode(encode(payload)) == payload；缺失键与 null 不混；只省重复，不删不改。"""

from __future__ import annotations

import json

import pytest

from agentlayer.compact_payload import ABSENT, TABLE, CompactError, decode, encode, encode_payload


def canon(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cells(t: dict, i: int) -> dict:
    """表里第 i 行：列名 → 值（两种写法都还原成列对齐的样子，缺失写 ABSENT）。"""
    if "sparse_rows" in t:
        row = t["sparse_rows"][i]
        return {c: row.get(str(k), ABSENT) for k, c in enumerate(t["columns"])}
    return dict(zip(t["columns"], t["rows"][i], strict=True))


OPPS = [
    {
        "opportunity_id": "x.housing.1701.1.s0001",
        "category": "housing",
        "price": {
            "amount": {"amount": 1234.56, "numeraire": "USD_2024_LOCAL"},
            "billing": "per_month",
        },
        "area": "x.area.h.1701",
        "attributes": {},
        "note": None,
    },
    {
        "opportunity_id": "x.job.43-9061.w03.h40.w.1703100",
        "category": "job",
        "compensation": {"amount": {"amount": 40000.0, "numeraire": "USD_2024_LOCAL"}},
        "area": None,  # 键存在、值为空
        "attributes": {"hours_area_level": "soc_major_43", "employment": 1200},
        "food_plan": {
            "parts": [
                {"mode": "home_cooked", "meals_per_week": 7},
                {"mode": "delivery", "meals_per_week": 14.0},
            ]
        },
    },
]


def test_round_trip_keeps_nested_values_missing_keys_nulls_amounts_and_ids():
    payload = {
        "observation": {"opportunities": OPPS, "cash": {"amount": 0.0}, "flags": [True, 1, 1.0]}
    }
    enc = encode_payload(payload)
    back = decode(enc)
    assert canon(back) == canon(payload)
    t = enc["observation"]["opportunities"][TABLE]
    # 每行都相同的值只写一次；嵌套字段按路径展开
    assert "category" in t["columns"] and "opportunity_id" in t["columns"]
    assert "compensation.amount.numeraire" in t["columns"]  # 只有第二行有：不是每行都相同
    row0 = cells(t, 0)
    # 第一行没有 compensation：写 $absent；第二行的 area 是 null：照写 null
    assert row0["compensation.amount.amount"] == ABSENT
    row1 = cells(t, 1)
    assert row1["area"] is None and row1["price.amount.amount"] == ABSENT
    # 嵌套的对象列表递归成表；空对象 {} 原样保留
    assert TABLE in row1["food_plan.parts"]
    assert back["observation"]["opportunities"][0]["attributes"] == {}
    assert (
        back["observation"]["opportunities"][1]["food_plan"]["parts"][1]["meals_per_week"] == 14.0
    )


def test_values_that_only_look_equal_are_not_merged():
    rows = [{"k": 1, "same": "a"}, {"k": 1.0, "same": "a"}, {"k": True, "same": "a"}]
    enc = encode(rows)
    t = enc[TABLE]
    assert t["common"] == {"same": "a"} and t["columns"] == ["k"]
    assert canon(decode(enc)) == canon(rows)  # 1、1.0、true 各自保留


def test_reserved_keys_short_lists_and_scalar_lists_are_left_alone():
    with pytest.raises(CompactError, match="保留键"):
        encode_payload({"x": [{"$absent": 1}, {"a": 2}]})
    assert encode({"one": [{"a": 1}], "nums": [1, 2], "empty": []}) == {
        "one": [{"a": 1}],
        "nums": [1, 2],
        "empty": [],
    }
    dotted = [{"a.b": 1}, {"a.b": 2}]
    t = encode(dotted)[TABLE]
    assert t["flat"] is False and canon(decode(encode(dotted))) == canon(dotted)


def test_a_real_request_round_trips_and_shrinks(bundle, scoring):
    from agentlayer.life_llm import _payload_from_body
    from agentlayer.life_planning import render_prompt

    from .test_life_extension import request_for

    payload = request_for(bundle, scoring)
    system, user = render_prompt(payload)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert len(user) < 0.9 * len(plain)  # 合成样例的机会种类杂：省一成多；同类对象越多省得越多
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    assert canon(_payload_from_body(body)) == canon(payload)
    assert "$table" in system  # 系统提示说明了怎样读这种表示
