"""接入层真实资料的跨对象检查（intake-02）。只做加载与检查，不做估计、不改内容；不依赖 ``product``（登记过的方法与假设由调用方传入）。

检查项（每条失败写成一条问题，构建在检查阶段停下）：

1. 记录引用的来源都在来源清单里；记录编号不重复；字段引用的方法与假设都登记过（方法写成 ``编号@版本``，可以带括号说明）。
2. 金额字段必须写计价口径，而且只能是登记过的口径（``USD_2024_LOCAL``、全国均价、人口普查大区均价）；非 2024 年时期的观测金额
   不能直接标成 ``USD_2024_LOCAL``（换口径要有方法）。
3. 机会的溯源（``lineage``）都能找到对应的记录或条件表；映射生成的机会写了登记过的方法。
4. 菜单只引用注册表里的机会；同一（都会区、年份、板块、环境重复）只有一份菜单；按同一条合格规则重算合格集合，数量与内容身份都要
   对得上；列出的菜单选中的都在合格集合里。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common.intake_contracts import Menu, Opportunity, StdRecord, canonical_json

ALLOWED_NUMERAIRES = frozenset({"USD_2024_LOCAL", "USD_2024_US_AVG", "USD_2024_CENSUS_REGION"})
#: 原样保留在原始时期的名义金额（没有换口径）：只能当事实展示，不能和 ``USD_2024_LOCAL`` 直接相加或比较。
NOMINAL = re.compile(r"^USD_\d{4}(_\d{4})?_NOMINAL$")
MAX_PROBLEMS = 200


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _method_id(ref: str) -> str:
    return ref.split("（", 1)[0].strip()


def eligible_identity(ids: Iterable[str]) -> str:
    """与菜单生成同一条规则：排序后的编号列表的规范 JSON 的 SHA-256。"""
    return hashlib.sha256(canonical_json(sorted(ids)).encode("utf-8")).hexdigest()


def check_fields(
    owner: str,
    fields: Iterable[dict[str, Any]],
    methods: set[str],
    assumptions: set[str],
    period_year: int | None,
) -> list[str]:
    problems = []
    for f in fields:
        m = f.get("method")
        if m and _method_id(m) not in methods:
            problems.append(f"{owner}.{f['name']}: 方法 {_method_id(m)} 没有登记")
        for a in f.get("assumptions") or ():
            if a not in assumptions:
                problems.append(f"{owner}.{f['name']}: 假设 {a} 没有登记")
        num = f.get("numeraire")
        if f.get("status") == "present":
            if num is not None and num not in ALLOWED_NUMERAIRES and not NOMINAL.match(num):
                problems.append(f"{owner}.{f['name']}: 计价口径 {num} 没有登记")
            if (
                num == "USD_2024_LOCAL"
                and f.get("kind") == "observed"
                and period_year not in (None, 2024)
            ):
                problems.append(
                    f"{owner}.{f['name']}: 非 2024 年的观测金额标成了 USD_2024_LOCAL（换口径要有方法）"
                )
            if (f.get("unit") or "").startswith("USD") and num is None:
                problems.append(f"{owner}.{f['name']}: 金额字段没有写计价口径")
    return problems


def _eligible(o: Opportunity, policy: str) -> bool:
    if policy == "annual_exposure_sample":
        return (
            o.supply_status == "offered"
            and o.target_weight is not None
            and float(o.target_weight) > 0
        )
    return o.supply_status != "not_offered"


def check_build(
    std_dir: Path,
    map_dir: Path,
    catalog_dir: Path,
    *,
    source_ids: set[str],
    metro_cbsas: list[str],
    methods: set[str],
    assumptions: set[str],
) -> list[str]:
    problems: list[str] = []
    record_ids: set[str] = set()
    for p in sorted(std_dir.glob("records_*.jsonl.gz")):
        for doc in _jsonl(p):
            rec = StdRecord.model_validate(doc)
            if rec.record_id in record_ids:
                problems.append(f"{rec.record_id}: 记录编号重复")
            record_ids.add(rec.record_id)
            if rec.source.source_id not in source_ids:
                problems.append(f"{rec.record_id}: 来源 {rec.source.source_id} 不在来源清单里")
            year = rec.period.start.year if rec.period.start.year == rec.period.end.year else None
            problems += check_fields(rec.record_id, doc["fields"], methods, assumptions, year)
            if len(problems) > MAX_PROBLEMS:
                return problems
    table_ids = {doc["table_id"] for doc in _jsonl(map_dir / "tables.jsonl.gz")}
    opps: dict[str, Opportunity] = {}
    by_city_sector: dict[tuple[str, str], list[str]] = {}
    for doc in _jsonl(map_dir / "opportunities.jsonl.gz"):
        o = Opportunity.model_validate(doc)
        opps[o.opportunity_id] = o
        if o.method and o.method not in methods:
            problems.append(f"{o.opportunity_id}: 方法 {o.method} 没有登记")
        for ref in o.lineage:
            if ref not in record_ids and ref not in table_ids:
                problems.append(f"{o.opportunity_id}: 溯源 {ref} 找不到对应的记录或条件表")
        problems += check_fields(o.opportunity_id, doc["facts"], methods, assumptions, None)
        for c in [o.cbsa] if o.cbsa else metro_cbsas:
            by_city_sector.setdefault((c, o.sector), []).append(o.opportunity_id)
        if len(problems) > MAX_PROBLEMS:
            return problems
    seen: Counter[tuple[str, int, str, int]] = Counter()
    for doc in _jsonl(catalog_dir / "menus.jsonl.gz"):
        mnu = Menu.model_validate(doc)
        seen[(mnu.cbsa, mnu.year, mnu.sector, mnu.env_repeat)] += 1
        missing = [i for i in mnu.selected if i not in opps]
        if missing:
            problems.append(f"{mnu.menu_id}: 引用了注册表里没有的机会 {missing[:3]}")
            continue
        pool = [
            i
            for i in by_city_sector.get((mnu.cbsa, mnu.sector), [])
            if _eligible(opps[i], mnu.policy)
        ]
        if len(pool) != mnu.n_eligible or eligible_identity(pool) != mnu.eligible_identity:
            problems.append(
                f"{mnu.menu_id}: 按同一规则重算的合格集合（{len(pool)} 项）与菜单记下的（{mnu.n_eligible} 项）对不上"
            )
        outside = set(mnu.selected) - set(pool)
        if outside:
            problems.append(f"{mnu.menu_id}: 选中了合格集合以外的机会 {sorted(outside)[:3]}")
    dup = [k for k, v in seen.items() if v > 1]
    if dup:
        problems.append(f"同一（都会区、年份、板块、环境重复）有多份菜单：{dup[:3]}")
    return problems
