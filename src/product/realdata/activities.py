"""爱好与活动：芝加哥公园管理局的活动（真实价格、日期、时段、地点）+ OpenStreetMap 的活动场所（城市行政边界之内）。

* 芝加哥公园活动：一条记录 = 一个项目或活动（标题、类别、活动类型、年龄段、地点、收费、起止日期、星期与时段、报名日期）。收费是
  整个项目一次报名的价格（不是每次）；按项目开始月份的 CPI-U 换到 ``USD_2024_LOCAL``（估计），原价原样保留；项目开始在 CPI 已发布
  月份之后的，用最近一个已发布月份（标在方法里）。设备要求资料里没有，记为缺失。
* OSM 场所：健身房、运动中心、游泳池、攀岩、电影院、剧院、图书馆、社区中心、博物馆等。开放时间是 OSM 的原文；``fee`` 只说收不收费，
  不是价格，价格一律缺失。
* 只有芝加哥有带价格与日历的城市开放资料；其他五个城市的活动只有场所。这是来源覆盖的差异，不是城市之间的真实差异。
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from collections.abc import Iterator
from fractions import Fraction

from common.intake_contracts import (
    Calendar,
    CompileInfo,
    FieldValue,
    GeoRef,
    MethodSpec,
    Period,
    SourceRef,
    StdRecord,
    missing,
)
from product.realdata.config import Metro
from product.realdata.prices import METHOD as CPI_METHOD
from product.realdata.prices import Cpi, to_cents_text

PARSE_NOTES = MethodSpec(
    method_id="parse_date_notes",
    version="1",
    family="conversion",
    summary_zh="从芝加哥公园活动的日期说明里读出唯一一段“from h to h”的时段，换成小时；多段或看不懂就不推算。",
    inputs=("chicago_park_activities",),
)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PRIVATE_ACCESS = frozenset({"private", "no", "residents"})
VENUE_ZH = {
    "fitness_centre": "健身房",
    "sports_centre": "运动中心",
    "swimming_pool": "游泳池",
    "ice_rink": "溜冰场",
    "climbing": "攀岩场",
    "bowling_alley": "保龄球馆",
    "golf_course": "高尔夫球场",
    "dance": "舞蹈场地",
    "cinema": "电影院",
    "theatre": "剧院",
    "library": "图书馆",
    "community_centre": "社区中心",
    "arts_centre": "艺术中心",
    "museum": "博物馆",
    "gallery": "画廊",
    "zoo": "动物园",
    "aquarium": "水族馆",
}


def _text(value: object) -> str | None:
    """芝加哥资料的文字字段带 HTML 实体（例如 “Fun &amp; Games”）：还原成字符；空值仍是空值。"""
    if value is None:
        return None
    s = html.unescape(str(value)).strip()
    return s or None


def _dt_or_none(text: str | None) -> _dt.datetime | None:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _session_hours(notes: str | None) -> Fraction | None:
    """“from 5pm to 7:30pm” → 2.5 小时（只认一段明确的时段；多段或看不懂就不推算）。"""
    if not notes:
        return None
    spans = re.findall(
        r"from (\d{1,2})(?::(\d{2}))?\s*(am|pm) to (\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        notes,
        flags=re.I,
    )
    if len({s for s in spans}) != 1:
        return None
    h1, m1, a1, h2, m2, a2 = spans[0]

    def minutes(h: str, m: str, ap: str) -> int:
        hh = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
        return hh * 60 + int(m or 0)

    d = minutes(h2, m2, a2) - minutes(h1, m1, a1)
    return Fraction(d, 60) if d > 0 else None


def chicago_records(
    rows: list[dict], metro: Metro, cpi: Cpi, raw_sha: str | None
) -> Iterator[StdRecord]:
    latest = max(k for k in cpi.values if not k.endswith("annual"))
    for r in rows:
        start, end = _dt_or_none(r.get("start_date")), _dt_or_none(r.get("end_date"))
        if start is None:
            continue
        end = end or start
        notes = _text(r.get("date_notes"))
        days = tuple(d for d in DAYS if notes and d in notes)
        fee_raw = r.get("fee")
        if fee_raw is None or str(fee_raw).strip() == "":
            fee = missing("program_fee", "missing", "没有写收费")
        else:
            month = min(f"{start.year}-{start.month:02d}", latest)
            factor = cpi.factor([month])
            fee = FieldValue(
                name="program_fee",
                status="present",
                raw=str(fee_raw),
                raw_unit=f"USD per program registration（{start.year} 年名义值）",
                value=to_cents_text(Fraction(str(fee_raw)) * factor),
                unit="USD/registration",
                numeraire="USD_2024_LOCAL",
                kind="estimated",
                method=f"{CPI_METHOD.ref}（{month} → 2024 年平均，×{float(factor):.6f}）",
            )
        hours = _session_hours(notes)
        fields = (
            fee,
            FieldValue(
                name="session_hours",
                status="present",
                value=str(float(hours)),
                unit="hours/session",
                kind="derived",
                method=PARSE_NOTES.ref,
                raw=notes[:200] if notes else None,
            )
            if hours is not None
            else missing("session_hours", "missing", "日期说明里没有唯一明确的时段"),
            missing("equipment_requirements", "missing", "芝加哥公园活动资料没有设备要求"),
        )
        yield StdRecord(
            record_id=f"activities.chicago_parks.{str(r['activity_id']).lower()}",
            sector="activities",
            record_type="activity_program",
            label=(_text(r.get("title")) or "活动")[:200],
            geo=GeoRef(
                country="US",
                scheme="Point",
                code=f"{r.get('latitude')},{r.get('longitude')}",
                level="point",
                label=_text(r.get("location_facility")) or "Chicago Park District",
                cbsa=metro.cbsa,
                relation="within",
            ),
            period=Period(
                label="program dates", start=start.date(), end=max(end.date(), start.date())
            ),
            source=SourceRef(
                source_id="chicago_park_activities",
                locator=f"tn7v-6rnw activity_id={r['activity_id']}",
                raw_sha256=raw_sha,
            ),
            fields=fields,
            attributes={
                "type": _text(r.get("type")),
                "category": _text(r.get("category")),
                "activity_type": _text(r.get("activity_type")),
                "age_range": _text(r.get("age_range")),
                "season": _text(r.get("season")),
                "facility": _text(r.get("location_facility")),
                "zip": r.get("zip"),
                "days": ",".join(days) or None,
                "registration_opens": (r.get("registration_date") or "")[:10] or None,
                "cancelled": r.get("event_cancelled") == "Y",
                "date_notes": (notes or "")[:200] or None,
            },
            terms={"sales_tax": "unknown"},
        )


def program_opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    days = rec.attributes.get("days")
    cal = Calendar(
        known=True,
        start=rec.period.start,
        end=rec.period.end,
        days=tuple(str(days).split(",")) if days else (),
        times=str(rec.attributes.get("date_notes"))[:80]
        if rec.attributes.get("date_notes")
        else None,
        season=str(rec.attributes.get("season"))[:40] if rec.attributes.get("season") else None,
    )
    cancelled = bool(rec.attributes.get("cancelled"))
    fee, hours = rec.field("program_fee"), rec.field("session_hours")
    single = (
        rec.period.start == rec.period.end
        and fee is not None
        and fee.status == "present"
        and hours is not None
        and hours.status == "present"
        and (Fraction(hours.value) * 100).denominator == 1  # type: ignore[arg-type]
    )
    compile_info = (
        CompileInfo(status="compilable", e1_category="experience")
        if single
        else CompileInfo(
            status="needs_e1_extension",
            reasons=("calendar_bound_repeated_sessions_not_expressible",),
            e1_category="experience",
        )
    )
    return make_opportunity(
        opportunity_id=rec.record_id.replace(
            "activities.chicago_parks.", "activities.program.16980.", 1
        ),
        sector="activities",
        kind="activity_program",
        label=rec.label,
        cbsa=rec.geo.cbsa,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=("program_fee",),
        supply_status="not_offered" if cancelled else "offered",
        menu_policy="calendar_open",
        compile=compile_info,
        attributes=dict(rec.attributes),
        terms=dict(rec.terms),
        calendar=cal,
        price_fact="program_fee",
    )


def venue_records(
    elements: list[dict], metro: Metro, snapshot: str | None, raw_sha: str | None
) -> Iterator[StdRecord]:
    day = (snapshot or "1970-01-01")[:10]
    period = Period(
        label=f"OSM snapshot {day}",
        start=_dt.date.fromisoformat(day),
        end=_dt.date.fromisoformat(day),
    )
    for el in elements:
        tags = el.get("tags") or {}
        kind = tags.get("leisure") or tags.get("amenity") or tags.get("tourism")
        if kind not in VENUE_ZH:
            continue
        if tags.get("access") in PRIVATE_ACCESS:
            continue  # 私人场所（例如自家后院的泳池）不是可以去的活动场所
        oid = f"{el['type']}/{el['id']}"
        hours = tags.get("opening_hours")
        fee = tags.get("fee")
        fields = (
            missing("price", "missing", "OSM 不记录价格（fee 只说收不收费）"),
            FieldValue(name="opening_hours", status="present", raw=hours[:200], value=hours[:200])
            if hours
            else missing("opening_hours", "missing", "OSM 没有开放时间"),
        )
        yield StdRecord(
            record_id=f"activities.osm.{metro.cbsa}.{el['type']}.{el['id']}",
            sector="activities",
            record_type="activity_venue",
            label=(tags.get("name") or f"（未命名的{VENUE_ZH[kind]}）")[:200],
            geo=GeoRef(
                country="US",
                scheme="OSM",
                code=oid,
                level="point",
                label=metro.principal_city.name,
                cbsa=metro.cbsa,
                relation="within",
            ),
            period=period,
            source=SourceRef(
                source_id="osm_overpass", locator=f"overpass {kind} {oid}", raw_sha256=raw_sha
            ),
            fields=fields,
            attributes={
                "venue_type": kind,
                "sport": tags.get("sport"),
                "fee_tag": fee,
                "access": tags.get("access"),
                "named": "name" in tags,
            },
        )


def venue_opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    hours = rec.field("opening_hours")
    cal = (
        Calendar(known=True, times=hours.value[:80] if hours and hours.value else None)
        if hours is not None and hours.status == "present"
        else Calendar(known=False, reason="OSM 没有开放时间")
    )
    return make_opportunity(
        opportunity_id=rec.record_id.replace("activities.osm.", "activities.venue.", 1),
        sector="activities",
        kind="activity_venue",
        label=rec.label,
        cbsa=rec.geo.cbsa,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=(),
        supply_status="offered",
        menu_policy="calendar_open",
        compile=CompileInfo(
            status="needs_e1_extension",
            reasons=("price_unknown", "e1_has_no_venue_visit_choice"),
            e1_category="experience",
        ),
        attributes=dict(rec.attributes),
        calendar=cal,
        price_fact="price",
    )
