"""把当前引擎已经能表达的机会导出成 E1 机会（``LifeOpportunity``），并做合法性检查。

只是**独立导出与合法性验证**：不写进任何 E1 运行配置，不接产品链，不宣称完整产品运行已接真实资料。

目前能表达的只有一类：**单次活动**（芝加哥公园里开始与结束是同一天、有收费、有唯一明确时段的项目）→ E1 的一次体验
（``one_time_experience``，按次收费，``per_occurrence`` 小时）。每小时负担在 E1 里本来就是引擎层面的合成设定（E1 自己的样例也是），
这里沿用 E1 产品样例里活动的设定 1.0，登记为假设 ``e1_activity_burden_per_hour``，并写进导出机会的来源规则里。

其余板块为什么还不能导出，按原因代码汇总（见 E1 扩展声明）。
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

from common.intake_contracts import AssumptionSpec, MethodSpec, Opportunity
from common.life_contracts import LifeOpportunity

EXPORT_VERSION = "e1-export-v2"
EXPORT_METHOD = MethodSpec(
    method_id="e1_export_single_session_experience",
    version="1",
    family="conversion",
    summary_zh="单次活动 → E1 一次体验：价格 = 收费（2024 年当地价估计），按次计费；资源 per_occurrence = 本次时段小时；每小时负担用登记的 E1 合成设定。",
    inputs=("chicago_park_activities", "bls_cpi_u"),
)
BURDEN = AssumptionSpec(
    assumption_id="e1_activity_burden_per_hour",
    kind="assumption",
    statement_zh="导出成 E1 一次体验时，每小时负担取 E1 产品样例里活动的合成设定 1.0（负担点 / 小时）。",
    applies_to=("e1_export_single_session_experience@1",),
    why_not_measured_zh="负担是 E1 引擎层面的合成尺度，不是可以从资料里测到的量。",
)
BURDEN_VALUE = 1.0


def _two_decimals(text: str) -> bool:
    return (Fraction(text) * 100).denominator == 1


def to_life_opportunity(o: Opportunity) -> dict[str, Any]:
    """只导出 E1 能原样表达的单次活动。带日期或年龄限制的活动不导出：E1 的体验没有日期窗口、资格标签不求值，
    导出就会变成任意月份、任意年龄都能参加的普通体验。"""
    if o.calendar is not None and o.calendar.known and (o.calendar.start or o.calendar.end):
        raise ValueError("dated_calendar_not_expressible")
    if o.attributes.get("age_restricted") is not False:
        raise ValueError("age_eligibility_not_expressible_or_unknown")
    fee = o.fact("program_fee")
    hours = o.fact("session_hours")
    assert (
        fee is not None and fee.value is not None and hours is not None and hours.value is not None
    )
    if not _two_decimals(hours.value):
        raise ValueError("session_hours_more_than_two_decimals")
    activity = o.opportunity_id.rsplit(".", 1)[1]
    return {
        "opportunity_id": f"chi.activity.{activity}"[:64],
        "category": "experience",
        "label": o.label[:120],
        "unit": "registration",
        "quality": f"{o.attributes.get('category') or '活动'}；{o.attributes.get('facility') or ''}；{o.calendar.start if o.calendar else ''} 当天"[
            :280
        ],
        "availability": "available",
        "price": {
            "amount": {"amount": float(fee.value), "numeraire": "USD_2024_LOCAL"},
            "billing": "one_time",
            "per_unit": "registration",
        },
        "duration": {"kind": "one_time_experience", "lasts_years": 0},
        "resource": {
            "basis": "per_occurrence",
            "hours": float(hours.value),
            "burden_per_hour": BURDEN_VALUE,
            "quantity_scales": True,
            "note": "一次活动的时段；负担是登记的 E1 合成设定"[:280],
        },
        "attributes": {
            k: v
            for k, v in (
                ("activity_category", o.attributes.get("category")),
                ("age_range", o.attributes.get("age_range")),
            )
            if isinstance(v, str)
        },
        "source": {
            "kind": "estimated",
            "observed_on": str(
                o.calendar.start if o.calendar and o.calendar.start else "2026-01-01"
            ),
            "geography_scope": "City of Chicago parks (within CBSA 16980)",
            "mapping_version": EXPORT_METHOD.ref,
            "inputs": list(o.lineage),
            "rule": f"收费按 CPI-U 换到 2024 年当地价（{fee.method}）；每小时负担 = {BURDEN_VALUE}（假设 {BURDEN.assumption_id}）"[
                :400
            ],
        },
    }


def export(map_dir: Path) -> dict[str, Any]:
    compiled: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    by_status: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    by_sector: dict[str, Counter[str]] = {}
    with gzip.open(map_dir / "opportunities.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            o = Opportunity.model_validate(json.loads(line))
            by_status[o.compile.status] += 1
            by_sector.setdefault(o.sector, Counter())[o.compile.status] += 1
            for r in o.compile.reasons:
                reasons[f"{o.sector}:{r}"] += 1
            if o.compile.status != "compilable":
                continue
            try:
                doc = to_life_opportunity(o)
                LifeOpportunity.model_validate(doc)
            except (ValueError, AssertionError) as exc:
                failed.append({"opportunity_id": o.opportunity_id, "reason": str(exc)[:200]})
                continue
            compiled.append(doc)
    return {
        "version": EXPORT_VERSION,
        "summary": {"compiled": len(compiled), "failed": len(failed), "by_status": dict(by_status)},
        "by_sector": {k: dict(v) for k, v in sorted(by_sector.items())},
        "reasons": dict(reasons.most_common()),
        "failed": failed[:50],
        "sample": compiled[:10],
        "note_zh": "独立导出与合法性检查（LifeOpportunity 校验）；没有写进任何运行配置，不代表产品链已接真实资料。",
    }
