"""对冻结库的只读查询：分页、筛选、菜单读取、溯源。

* 查询不重新拟合、不重新抽样：菜单读的是冻结时生成好的那一份。
* 分页只是“这一页”：总数照实给出；检索截断不能说成当地买不到（``total`` 与 ``returned`` 分开）。
* 属性筛选用 JSON 字段精确匹配（例如 ``cuisine=chinese``、``dine_in=yes``）；没有写的属性等于“未知”，不等于“没有”。
* 岗位：intake 只保存来源事实（OEWS 工资分布、EP 入职要求，用 ``records`` 查）与工时表，不再有岗位候选与岗位菜单。查旧的
  ``job_opening_candidate`` 或岗位板块的机会 / 菜单会报 ``RetiredJobQueryError`` 并指向 E3B 生活资料包，不返回一个空结果冒充“没有岗位”。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from product.realdata import store

#: 旧的三档岗位候选（每个职业 × 都会区的 25 分位 / 中位数 / 75 分位）的机会种类；现行构建不再生成。
RETIRED_JOB_KIND = "job_opening_candidate"
JOB_MIGRATION_ZH = (
    "intake 不再生成岗位候选与岗位菜单（旧的 job_opening_candidate 三档工资候选已删除）。岗位候选池"
    "（岗位模板 × 20 个年薪支持点 × 工时与就业片区的联合分布）与每年的岗位菜单在 E3B 生活资料包里："
    "data/frozen/life/us-v1/pack/（由 scripts/life_pack_us.py build 生成）。intake 里的来源事实仍可查："
    "records --sector jobs（OEWS 工资分布 occupation_wage_distribution、EP 入职要求 occupation_entry_requirements）。"
)


class RetiredJobQueryError(LookupError):
    """查询已删除的旧岗位候选或岗位菜单：不按新语义解读，给出迁移提示。"""

    def __init__(self) -> None:
        super().__init__(JOB_MIGRATION_ZH)


def _refuse_retired_jobs(sector: str | None, kind: str | None) -> None:
    if kind == RETIRED_JOB_KIND or sector == "jobs":
        raise RetiredJobQueryError()


@dataclass(frozen=True)
class Page:
    total: int
    page: int
    per_page: int
    items: list[dict[str, Any]]

    @property
    def returned(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "returned": self.returned,
            "note_zh": "total 是库里符合条件的全部数量；本页只返回其中一部分，没返回的不等于不存在",
            "items": self.items,
        }


def opportunities(
    conn: sqlite3.Connection,
    *,
    sector: str | None = None,
    cbsa: str | None = None,
    kind: str | None = None,
    attributes: dict[str, str] | None = None,
    text: str | None = None,
    executable: bool | None = None,
    offered_only: bool = False,
    page: int = 1,
    per_page: int = 20,
    include_national: bool = True,
) -> Page:
    _refuse_retired_jobs(sector, kind)
    where, args = [], []
    if sector:
        where.append("sector = ?")
        args.append(sector)
    if cbsa:
        if include_national:
            # 全国性的机会只算进同一国家的都会区（只在加拿大销售的家电型号不进美国都会区），与目录、菜单同一条规则
            where.append("(cbsa = ? OR (cbsa IS NULL AND country = ?))")
            args += [cbsa, store.meta(conn)["country"]]
        else:
            where.append("cbsa = ?")
            args.append(cbsa)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if executable is not None:
        where.append("executable = ?")
        args.append(int(executable))
    if offered_only:
        where.append("supply_status != 'not_offered'")
    if text:
        where.append("label LIKE ?")
        args.append(f"%{text}%")
    for key, value in (attributes or {}).items():
        where.append("json_extract(json, ?) = ?")
        args += [f"$.attributes.{key}", value]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM opportunities{clause}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT json FROM opportunities{clause} ORDER BY opportunity_id LIMIT ? OFFSET ?",
        [*args, per_page, (page - 1) * per_page],
    ).fetchall()
    return Page(total=total, page=page, per_page=per_page, items=[json.loads(r[0]) for r in rows])


def menu(
    conn: sqlite3.Connection, cbsa: str, year: int, sector: str, env_repeat: int = 0
) -> dict[str, Any] | None:
    _refuse_retired_jobs(sector, None)
    row = conn.execute(
        "SELECT json FROM menus WHERE cbsa = ? AND year = ? AND sector = ? AND env_repeat = ?",
        (cbsa, year, sector, env_repeat),
    ).fetchone()
    return json.loads(row[0]) if row else None


def menu_items(
    conn: sqlite3.Connection, menu_doc: dict[str, Any], page: int = 1, per_page: int = 20
) -> Page:
    """菜单里的机会：列出的菜单按顺序分页；完整目录按条件分页（同一板块、同一城市及全国性的，与合格规则一样去掉明确不供应的）。"""
    _refuse_retired_jobs(menu_doc["sector"], None)
    if menu_doc["selection"] == "listed":
        ids = menu_doc["selected"]
        chunk = ids[(page - 1) * per_page : page * per_page]
        items = []
        for oid in chunk:
            row = conn.execute(
                "SELECT json FROM opportunities WHERE opportunity_id = ?", (oid,)
            ).fetchone()
            items.append(json.loads(row[0]))
        return Page(total=len(ids), page=page, per_page=per_page, items=items)
    return opportunities(
        conn,
        sector=menu_doc["sector"],
        cbsa=menu_doc["cbsa"],
        offered_only=True,
        page=page,
        per_page=per_page,
    )


def records(
    conn: sqlite3.Connection,
    *,
    sector: str | None = None,
    cbsa: str | None = None,
    record_type: str | None = None,
    text: str | None = None,
    page: int = 1,
    per_page: int = 20,
    include_national: bool = True,
) -> Page:
    """标准化的来源记录（原始事实）：与机会一样分页；``cbsa`` 默认连同同一国家的全国性记录一起给（例如 EP 的全国入职要求）。"""
    where, args = [], []
    if sector:
        where.append("sector = ?")
        args.append(sector)
    if cbsa:
        if include_national:
            where.append("(cbsa = ? OR (cbsa IS NULL AND json_extract(json, '$.geo.country') = ?))")
            args += [cbsa, store.meta(conn)["country"]]
        else:
            where.append("cbsa = ?")
            args.append(cbsa)
    if record_type:
        where.append("record_type = ?")
        args.append(record_type)
    if text:
        where.append("label LIKE ?")
        args.append(f"%{text}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM records{clause}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT json FROM records{clause} ORDER BY record_id LIMIT ? OFFSET ?",
        [*args, per_page, (page - 1) * per_page],
    ).fetchall()
    return Page(total=total, page=page, per_page=per_page, items=[json.loads(r[0]) for r in rows])


def lineage(conn: sqlite3.Connection, opportunity_id: str) -> dict[str, Any]:
    """一项机会 → 它用到的条件表与记录（含来源与原始记录定位）。"""
    row = conn.execute(
        "SELECT json FROM opportunities WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    if row is None:
        if opportunity_id.startswith("jobs."):
            raise RetiredJobQueryError()
        raise KeyError(opportunity_id)
    opp = json.loads(row[0])
    out: dict[str, Any] = {"opportunity": opp, "tables": [], "records": []}
    for ref in opp["lineage"]:
        t = conn.execute("SELECT json FROM tables WHERE table_id = ?", (ref,)).fetchone()
        if t is not None:
            table = json.loads(t[0])
            n = conn.execute(
                "SELECT COUNT(*) FROM table_records WHERE table_id = ?", (ref,)
            ).fetchone()[0]
            sample = [
                r[0]
                for r in conn.execute(
                    "SELECT record_id FROM table_records WHERE table_id = ? ORDER BY record_id LIMIT 5",
                    (ref,),
                )
            ]
            out["tables"].append({**table, "records_total": n, "records_sample": sample})
            continue
        r = conn.execute("SELECT json FROM records WHERE record_id = ?", (ref,)).fetchone()
        if r is not None:
            out["records"].append(json.loads(r[0]))
    return out
