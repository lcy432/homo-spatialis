"""冻结的资料库（SQLite）：来源、获取记录、方法与假设、标准化记录、条件表、机会注册表、菜单、覆盖报告。

* 内容进库时都是规范 JSON；内容身份（``build_id``）只由内容决定：获取时间、是否命中缓存、本机路径都在 ``fetches`` 表里，不参与。
* 冻结之后只读：查询不重新拟合、不重新抽样；菜单是冻结时生成好的。
* 库文件只在本机（几十到上百 MB）；仓库里提交的是注册表、菜单与报告的规范化导出（见 ``build.export_summary``）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from common.intake_contracts import (
    AssumptionSpec,
    FetchRecord,
    Menu,
    MethodSpec,
    Opportunity,
    SourceSpec,
    StdRecord,
    canonical_json,
)

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (source_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE fetches (name TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE methods (ref TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE assumptions (assumption_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE records (
    record_id TEXT PRIMARY KEY, sector TEXT NOT NULL, record_type TEXT NOT NULL,
    cbsa TEXT, label TEXT NOT NULL, json TEXT NOT NULL);
CREATE INDEX records_sector ON records (sector, cbsa, record_type);
CREATE TABLE tables (table_id TEXT PRIMARY KEY, method TEXT NOT NULL, cbsa TEXT, json TEXT NOT NULL);
CREATE TABLE table_records (table_id TEXT NOT NULL, record_id TEXT NOT NULL);
CREATE INDEX table_records_table ON table_records (table_id);
CREATE TABLE opportunities (
    opportunity_id TEXT PRIMARY KEY, sector TEXT NOT NULL, kind TEXT NOT NULL, cbsa TEXT,
    origin TEXT NOT NULL, stratum TEXT, menu_policy TEXT NOT NULL, supply_status TEXT NOT NULL,
    data_status TEXT NOT NULL, compile_status TEXT NOT NULL, collected INTEGER NOT NULL,
    planning_reference INTEGER NOT NULL, executable INTEGER NOT NULL, scorable INTEGER NOT NULL,
    label TEXT NOT NULL, json TEXT NOT NULL);
CREATE INDEX opportunities_sector ON opportunities (sector, cbsa, kind);
CREATE TABLE menus (
    menu_id TEXT PRIMARY KEY, cbsa TEXT NOT NULL, year INTEGER NOT NULL, sector TEXT NOT NULL,
    env_repeat INTEGER NOT NULL, policy TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE reports (name TEXT PRIMARY KEY, json TEXT NOT NULL);
"""


def create(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def open_readonly(path: Path) -> sqlite3.Connection:
    if not Path(path).exists():
        raise FileNotFoundError(f"冻结库不存在：{path}（先运行 build）")
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _dump(model) -> str:
    return canonical_json(model.model_dump(mode="json"))


def put_meta(conn: sqlite3.Connection, items: dict[str, str]) -> None:
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", sorted(items.items()))


def put_sources(conn: sqlite3.Connection, sources: Iterable[SourceSpec]) -> None:
    conn.executemany(
        "INSERT INTO sources VALUES (?, ?)", [(s.source_id, _dump(s)) for s in sources]
    )


def put_fetches(conn: sqlite3.Connection, fetches: dict[str, FetchRecord]) -> None:
    conn.executemany(
        "INSERT INTO fetches VALUES (?, ?)", [(k, _dump(v)) for k, v in sorted(fetches.items())]
    )


def put_methods(
    conn: sqlite3.Connection, methods: Iterable[MethodSpec], assumptions: Iterable[AssumptionSpec]
) -> None:
    conn.executemany("INSERT INTO methods VALUES (?, ?)", [(m.ref, _dump(m)) for m in methods])
    conn.executemany(
        "INSERT INTO assumptions VALUES (?, ?)", [(a.assumption_id, _dump(a)) for a in assumptions]
    )


def put_records(conn: sqlite3.Connection, records: Iterable[StdRecord]) -> int:
    rows = [(r.record_id, r.sector, r.record_type, r.geo.cbsa, r.label, _dump(r)) for r in records]
    conn.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def put_tables(conn: sqlite3.Connection, tables: Iterable[dict]) -> int:
    n = 0
    for t in tables:
        record_ids = t.pop("record_ids", ())
        conn.execute(
            "INSERT INTO tables VALUES (?, ?, ?, ?)",
            (t["table_id"], t["method"], t.get("cbsa"), canonical_json(t)),
        )
        conn.executemany(
            "INSERT INTO table_records VALUES (?, ?)", [(t["table_id"], rid) for rid in record_ids]
        )
        n += 1
    return n


def put_opportunities(conn: sqlite3.Connection, opps: Iterable[Opportunity]) -> int:
    rows = [
        (
            o.opportunity_id,
            o.sector,
            o.kind,
            o.cbsa,
            o.origin,
            o.stratum,
            o.menu_policy,
            o.supply_status,
            o.data_status,
            o.compile.status,
            int(o.capabilities.collected),
            int(o.capabilities.planning_reference),
            int(o.capabilities.executable),
            int(o.capabilities.scorable),
            o.label,
            _dump(o),
        )
        for o in opps
    ]
    conn.executemany("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def put_menus(conn: sqlite3.Connection, menus: Iterable[Menu]) -> int:
    rows = [(m.menu_id, m.cbsa, m.year, m.sector, m.env_repeat, m.policy, _dump(m)) for m in menus]
    conn.executemany("INSERT INTO menus VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def put_report(conn: sqlite3.Connection, name: str, content: object) -> None:
    conn.execute("INSERT OR REPLACE INTO reports VALUES (?, ?)", (name, canonical_json(content)))


def meta(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def report(conn: sqlite3.Connection, name: str) -> object:
    row = conn.execute("SELECT json FROM reports WHERE name = ?", (name,)).fetchone()
    return json.loads(row[0]) if row else None
