"""期末参考生活的住房候选（E3B 收尾，DECISIONS/0044）：全城就业覆盖 3/5，加上估值期初的实际通勤检查。

参考住房不再在编译时固定成四处，而是编译成一个**内部选择器**（``common.life_evaluation_contracts.HousingSelector``），
在估值期初每份参考模板的岗位确定之后由 E1 解析（``econ.life_terminal_value._select_housing``）。这里只做编译这一半：

* **全城就业选择面**：就业片区全集是完整岗位池与开局岗位用到的片区（与年度菜单无关）；权重读冻结通勤资料
  ``diagnostics.work_areas.areas[].inbound_weight``（ACS 流入就业人数），不用岗位个数、模板个数或取整的 share。
* **覆盖率(h)** = 有已知线路通达的就业片区权重 ÷ 全部就业片区权重。分子允许关系表里任一方式（只量住处的潜在地理
  适用面，不给用户免费的车，也不另加全城统一的 60 分钟条件）；一条线都没有、也没有明确不可达证据的片区算未知，
  留在分母里。已知覆盖 ≥ 3/5 → 合格；加上未知才可能 ≥ 3/5 → 资格未知；否则不进候选。阈值由所有者定，不按六城结果调。
  覆盖率不表示 60% 的工作一小时内可达，也不表示实际招聘概率或不用车就能到。
* **无损压缩**：每个居住片区最多两项——当地最便宜的一处（基本档候选）与当地最便宜的一卧中档（较舒适档候选），
  同价按编号。同片区的通勤关系相同，这对当前排序与通勤硬要求是精确简化；以后加面积、楼型之类的住房硬要求要重新核对。
* 候选从按普通住房代理（``ordinary_rental_access_p10_v1``）过滤后的完整池生成，作为内部评价资料展开进目录，不进任何
  菜单：分身在模拟年里看不到、也选不了参考专用的候选（同一编号本来就在当年菜单上时照常可见）。

正权重缺失或分母为零时报资料不足（参考住房槽位资料未知），不默认等权。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from common.life_evaluation_contracts import HOUSING_SELECTOR_RULE, coverage_status

REFERENCE_HOUSING_RULE = HOUSING_SELECTOR_RULE
COMFORT_SPEC = {"bedrooms": 1, "quality_level": 2}


@dataclass(frozen=True)
class ReferenceHousing:
    """一城的参考住房候选：两个档次的选择器（``basic`` / ``comfort``，可以直接写进槽位绑定），或者资料不足的原因；
    ``areas`` 是每个居住片区的覆盖（报告与证据用）。"""

    job_areas: tuple[str, ...]
    selectors: dict[str, dict[str, Any]] = field(default_factory=dict)
    reason: str | None = None
    areas: dict[str, dict[str, Any]] = field(default_factory=dict)

    def bound(self) -> list[str]:
        """要展开进目录的候选编号。"""
        return sorted(
            {c["opportunity_id"] for s in self.selectors.values() for c in s["candidates"]}
        )

    def summary(self) -> dict[str, Any]:
        """报告用的覆盖摘要：各资格的居住片区数、两档的候选数。"""
        by_status: dict[str, int] = {"eligible": 0, "unknown": 0, "excluded": 0}
        for a in self.areas.values():
            by_status[a["coverage"]] += 1
        return {
            "rule": REFERENCE_HOUSING_RULE,
            "coverage_threshold": "3/5",
            "job_areas": len(self.job_areas),
            "home_areas_by_coverage": by_status,
            "candidates": {k: len(s["candidates"]) for k, s in sorted(self.selectors.items())},
            **({"reason": self.reason} if self.reason else {}),
        }


def _rent(h: dict[str, Any]) -> Decimal:
    return Decimal(h["gross_rent"])


def build_reference_housing(
    housing: list[dict[str, Any]],
    job_areas: Iterable[str],
    commute: dict[str, Any],
) -> ReferenceHousing:
    """``housing`` 是合格住房池的轻量视图（编号、片区、月租 ``gross_rent``、卧室数、质量档），``job_areas`` 是全城就业
    片区全集，``commute`` 是冻结的通勤资料（取关系表、流入就业权重与内容身份）。"""
    areas = tuple(sorted(set(job_areas)))
    weights = {
        a["area_id"]: int(a["inbound_weight"])
        for a in commute["diagnostics"]["work_areas"]["areas"]
    }
    if not areas:
        return ReferenceHousing(areas, reason="本城没有可得岗位：全城就业选择面是空的")
    missing = [a for a in areas if weights.get(a, 0) <= 0]
    if missing:
        return ReferenceHousing(
            areas,
            reason=f"就业片区 {missing} 没有正的流入就业权重：覆盖率资料不足（不默认等权）",
        )
    total = sum(weights[a] for a in areas)
    pairs = {(g["home_area"], g["work_area"]) for g in commute["commute"]["legs"]}
    by_area: dict[str, list[dict[str, Any]]] = {}
    for h in housing:
        by_area.setdefault(h["area"], []).append(h)
    coverage: dict[str, dict[str, Any]] = {}
    basic: list[dict[str, Any]] = []
    comfort: list[dict[str, Any]] = []
    for home in sorted(by_area):
        covered = sum(weights[w] for w in areas if (home, w) in pairs)
        unknown = total - covered
        status = coverage_status(covered, unknown, total)
        coverage[home] = {"covered": covered, "unknown": unknown, "coverage": status}
        if status == "excluded":
            continue
        cand = {
            "area": home,
            "coverage": status,
            "covered_weight": covered,
            "unknown_weight": unknown,
        }
        homes = by_area[home]
        cheapest = min(homes, key=lambda h: (_rent(h), h["opportunity_id"]))
        basic.append({"opportunity_id": cheapest["opportunity_id"], **cand})
        mid1 = [
            h
            for h in homes
            if (h["bedrooms"], h["quality_level"])
            == (COMFORT_SPEC["bedrooms"], COMFORT_SPEC["quality_level"])
        ]
        if mid1:
            best = min(mid1, key=lambda h: (_rent(h), h["opportunity_id"]))
            comfort.append({"opportunity_id": best["opportunity_id"], **cand})
    excluded = sum(a["coverage"] == "excluded" for a in coverage.values())
    head = {
        "rule_id": REFERENCE_HOUSING_RULE,
        "coverage_threshold": "3/5",
        "commute_identity": commute["content_identity"],
        "work_areas": [{"area_id": a, "inbound_weight": weights[a]} for a in areas],
        "excluded_home_areas": excluded,
    }
    selectors = {
        "basic": {**head, "kind": "basic", "candidates": basic},
        "comfort": {**head, "kind": "comfort", "candidates": comfort},
    }
    return ReferenceHousing(areas, selectors, None, coverage)
