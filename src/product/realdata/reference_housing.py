"""期末参考生活的住房绑定（独立审核 F3、F4，DECISIONS/0042）：按参考岗位与通勤关系，从完整候选池选。

参考生活的住房以前从按菜单裁剪后的目录里挑，而且不看参考岗位在哪：固定住房可能到不了岗位（通勤不合法），菜单一换参考集合就
跟着变。现在的规则（``reference_housing_by_job_reach_v1``，六城统一、在看结果之前写定）：

* **参考岗位集合**：现职模板的岗位是估值期初分身手上的岗位，事先不知道是哪一个，所以取本城目录里全部可得岗位的就业片区；
  换岗模板的目标岗位也在这个集合里（组装时另外核对）。
* **合法**：从住处片区到每个参考岗位片区都有一条可执行的通勤线——不用车的模板只认不需要用车权的方式（公交、步行骑车）；用车
  模板也认开车（用车权的月费与油费在估值结算里照付）。
* **基本住房**：完整候选池（不是菜单）里合法的住房中最便宜的一处；**较舒适的住房**：合法的一卧中档里，到参考岗位最长的往返
  分钟最短的一处（同分钟取便宜的）。
* **必要交通服务**：只在没有不用车就合法的住房时，才设用车的参考生活（同样的基本 / 较舒适含义，另加用车权）；有不用车就合法
  的住房时用车参考生活确定不设，不让一辆车抬高参考集合。
* 用车也到不了的：这一档确定没有，用到它的参考方案已知不可行——资料确实不足时估值如实报不完整，不放宽条件。

参考集合只由完整候选池、参考岗位与通勤关系决定，与年度菜单无关：只换菜单抽样种子不会改变参考住房。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

REFERENCE_HOUSING_RULE = "reference_housing_by_job_reach_v1"
COMFORT_SPEC = {"bedrooms": 1, "quality_level": 2}


@dataclass(frozen=True)
class ReferenceHousing:
    """一城的参考住房：四个槽位各绑定到哪一处（或确定没有及原因），以及参考岗位片区与每处的最长往返分钟。"""

    job_areas: tuple[str, ...]
    slots: dict[str, str | None]
    reasons: dict[str, str] = field(default_factory=dict)
    worst_minutes: dict[str, int] = field(default_factory=dict)

    def bound(self) -> list[str]:
        return [oid for oid in self.slots.values() if oid is not None]


def _legs(relation: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for g in relation["legs"]:
        out.setdefault((g["home_area"], g["work_area"]), []).append(g)
    return out


def worst_roundtrip(
    home_area: str | None,
    job_areas: Iterable[str],
    legs: dict[tuple[str, str], list[dict[str, Any]]],
    modes: set[str],
) -> int | None:
    """从住处片区到每个参考岗位片区、只用 ``modes`` 里的方式时最快一条线的往返分钟，再取最长的；任何一个岗位片区走不通是 None。"""
    if home_area is None:
        return None
    worst = 0
    for w in job_areas:
        cands = [g["roundtrip_minutes"] for g in legs.get((home_area, w), ()) if g["mode"] in modes]
        if not cands:
            return None
        worst = max(worst, min(cands))
    return worst


def _price(h: dict[str, Any]) -> float:
    return float(h["price"]["amount"]["amount"])


def _pick(
    housing: list[dict[str, Any]],
    job_areas: tuple[str, ...],
    legs: dict[tuple[str, str], list[dict[str, Any]]],
    modes: set[str],
) -> tuple[tuple[dict[str, Any], int] | None, tuple[dict[str, Any], int] | None]:
    reach = []
    for h in housing:
        w = worst_roundtrip(h.get("area"), job_areas, legs, modes)
        if w is not None:
            reach.append((h, w))
    basic = min(reach, key=lambda x: (_price(x[0]), x[0]["opportunity_id"]), default=None)
    mid1 = [
        (h, w)
        for h, w in reach
        if all((h.get("spec") or {}).get(k) == v for k, v in COMFORT_SPEC.items())
    ]
    comfort = min(mid1, key=lambda x: (x[1], _price(x[0]), x[0]["opportunity_id"]), default=None)
    return basic, comfort


def choose_reference_housing(
    housing: list[dict[str, Any]], jobs: list[dict[str, Any]], relation: dict[str, Any]
) -> ReferenceHousing:
    """``housing`` 是完整候选池（可执行的住房，未按菜单裁剪），``jobs`` 是本城目录里的岗位，``relation`` 是通勤关系表。"""
    job_areas = tuple(
        sorted({j["area"] for j in jobs if j.get("availability") == "available" and j.get("area")})
    )
    legs = _legs(relation)
    modes = {m["mode"]: m for m in relation["modes"]}
    free = {m for m, d in modes.items() if not d.get("requires_function")}
    slots: dict[str, str | None] = {}
    reasons: dict[str, str] = {}
    worst: dict[str, int] = {}
    if not job_areas:
        for s in ("housing_basic", "housing_comfort", "housing_basic_car", "housing_comfort_car"):
            slots[s] = None
            reasons[s] = "本城目录里没有可得岗位：参考岗位集合为空，没有可以核对通勤的参考住房"
        return ReferenceHousing(job_areas, slots, reasons, worst)
    no_car = dict(
        zip(
            ("housing_basic", "housing_comfort"), _pick(housing, job_areas, legs, free), strict=True
        )
    )
    with_car = dict(
        zip(
            ("housing_basic_car", "housing_comfort_car"),
            _pick(housing, job_areas, legs, set(modes)),
            strict=True,
        )
    )
    labels = {"housing_basic": "合法住房", "housing_comfort": "合法的一卧中档"}
    for slot, got in no_car.items():
        car_slot = f"{slot}_car"
        if got is not None:
            slots[slot], worst[slot] = got[0]["opportunity_id"], got[1]
            slots[car_slot] = None
            reasons[car_slot] = (
                f"不用车就有能到达全部参考岗位片区的{labels[slot]}：不设用车的参考生活（必要交通服务只在需要时加）"
            )
            continue
        slots[slot] = None
        reasons[slot] = (
            f"完整候选池里没有不用车就能到达全部参考岗位片区（{len(job_areas)} 个）的{labels[slot]}"
        )
        car = with_car[car_slot]
        if car is not None:
            slots[car_slot], worst[car_slot] = car[0]["opportunity_id"], car[1]
        else:
            slots[car_slot] = None
            reasons[car_slot] = (
                f"开车也没有能到达全部参考岗位片区的{labels[slot]}：这一档参考生活确定没有（资料不足，不放宽条件）"
            )
    return ReferenceHousing(job_areas, slots, reasons, worst)
