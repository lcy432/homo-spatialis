"""把冻结库写成可以提交的小文件（``data/frozen/intake/<构建名>/``）：清单、覆盖矩阵、映射诊断、菜单索引与复现命令。

冻结库本身（SQLite，几百 MB）只留在本机，由原始文件 + 配置 + 代码按复现命令重建。清单记着每份原始文件的 SHA-256 与每个阶段的内容
身份，重建后构建身份应当相同。获取时间只放在清单的 ``run_metadata`` 里，不进任何内容身份。

菜单索引：抽样菜单（住房、新工作）逐条保存选中的机会编号；按日历开放与完整目录的菜单由规则决定，只保存合格集合与选中集合的内容
身份（重建后可以逐项核对），不在仓库里抄几万个编号。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from common.intake_contracts import content_identity
from product.realdata import store

SECTORS = ("housing", "jobs", "training", "food", "goods", "activities", "background")
SECTOR_ZH = {
    "housing": "住房",
    "jobs": "工作",
    "training": "培训",
    "food": "食品",
    "goods": "工业品",
    "activities": "活动",
    "background": "城市背景",
}


def _write(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _counts(conn: sqlite3.Connection) -> dict[str, Any]:
    def group(sql: str) -> dict[str, int]:
        return {"|".join(str(x) for x in row[:-1]): row[-1] for row in conn.execute(sql)}

    return {
        "records_by_sector": group("SELECT sector, COUNT(*) FROM records GROUP BY sector"),
        "tables": conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0],
        "opportunities_by_sector_origin": group(
            "SELECT sector, origin, COUNT(*) FROM opportunities GROUP BY sector, origin"
        ),
        "opportunities_by_compile_status": group(
            "SELECT compile_status, COUNT(*) FROM opportunities GROUP BY compile_status"
        ),
        "capabilities_by_sector": {
            row[0]: {
                "total": row[1],
                "collected": row[2],
                "planning_reference": row[3],
                "executable": row[4],
                "scorable": row[5],
            }
            for row in conn.execute(
                "SELECT sector, COUNT(*), SUM(collected), SUM(planning_reference), SUM(executable), "
                "SUM(scorable) FROM opportunities GROUP BY sector"
            )
        },
        "menus_by_policy": group("SELECT policy, COUNT(*) FROM menus GROUP BY policy"),
    }


def _fetches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for name, raw in conn.execute("SELECT name, json FROM fetches ORDER BY name"):
        f = json.loads(raw)
        out.append(
            {
                "name": name,
                "source_id": f["source_id"],
                "status": f["status"],
                "url": f["url"],
                "raw_sha256": f.get("raw_sha256"),
                "bytes": f.get("bytes"),
                "note": f.get("note") or "",
            }
        )
    return out


def menus_index(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for (raw,) in conn.execute("SELECT json FROM menus ORDER BY menu_id"):
        m = json.loads(raw)
        row = {k: m[k] for k in ("menu_id", "cbsa", "year", "sector", "env_repeat", "policy")}
        row |= {
            "algorithm": m["algorithm"],
            "seed": m["seed"],
            "n_eligible": m["n_eligible"],
            "eligible_identity": m["eligible_identity"],
            "selection": m["selection"],
            "n_selected": len(m["selected"]) if m["selection"] == "listed" else m["n_eligible"],
            "selected_identity": content_identity(list(m["selected"])),
        }
        if m["policy"] == "annual_exposure_sample":
            row["selected"] = m["selected"]
        out.append(row)
    return out


def coverage_markdown(coverage: dict[str, Any], fetches: list[dict[str, Any]]) -> str:
    lines = [
        "# 六个验证都会区 × 板块覆盖矩阵",
        "",
        "由 `scripts/intake_data.py export` 从冻结库生成，不要手改。数字的含义：",
        "",
        "* **记录**：真实取得的标准化记录（本都会区的 + 全国适用的，例如全国登记的家电型号、全国均价）。",
        "* **机会**：注册表里的候选；其中“映射生成”的是 A / B 映射按资料生成的候选，不是真实房源或真实岗位。",
        "* **机会（真实记录 / 派生 / 映射生成）**：派生是由真实记录经确定换算得到的（例如全国食材均价换到本都会区），映射生成是 A / B 映射的候选；都不是本地真实观测。",
        "* **资料完整 / 部分 / 不足**：候选必需的事实是否都在；不足的不进抽样（列表类菜单照样列出并标明），也不补数。",
        "* **估计或设定**：至少一项事实是估计值或设定值（例如租金是新租市场代理、食材价是地区价格水平调整后的估计）。",
        "* **编译状态**（互不重叠，相加等于机会数）：可编译 = 现在就能编进 E1；只缺资料 = 改 E1 也执行不了，要新资料；只缺 E1 = 资料够了，"
        "差 E1 的表达能力；两样都缺；参考事实 = 城市背景这类不是行动的事实。各原因的明细（互相重叠）见 `reports/gaps.json`。",
        "",
    ]
    for sector in SECTORS:
        lines += [
            f"## {SECTOR_ZH[sector]}（{sector}）",
            "",
            "| 都会区 | 记录（本地 / 全国适用） | 机会（真实记录 / 派生 / 映射生成） | 资料完整 / 部分 / 不足 | 估计或设定 | 可编译 / 只缺资料 / 只缺 E1 / 两样都缺 / 参考事实 | 能供规划参考 | 可执行 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for cbsa, m in coverage["metros"].items():
            v = m["sectors"].get(sector)
            if v is None:
                continue
            origin = v["by_origin"]
            generated = origin.get("mapping_a", 0) + origin.get("mapping_b", 0)
            lines.append(
                f"| {m['label_zh']}（{cbsa}） | {v['records_in_metro']} / {v['records_national_applicable']} "
                f"| {origin.get('record', 0)} / {origin.get('derived', 0)} / {generated} "
                f"| {v['data_complete']} / {v['data_partial']} / {v['data_insufficient']} "
                f"| {v['estimated_or_assumed']} "
                f"| {v['compilable']} / {v['needs_data_only']} / {v['needs_e1_only']} / {v['needs_data_and_e1']} / {v['reference_only']} "
                f"| {v['planning_reference']} | {v['executable']} |"
            )
        lines.append("")
    gm = coverage.get("goods_markets")
    if gm:
        lines += [
            "## 家电型号的适用市场",
            "",
            f"共 {gm['total_models']} 个型号；按来源的 markets 字段：{gm['by_market_scope']}；进美国目录的 {gm['in_us_catalog']} 个。{gm['note_zh']}",
            "",
        ]
    bad = [f for f in fetches if f["status"] != "ok"]
    lines += ["## 没有取得的来源请求", ""]
    if not bad:
        lines.append("（全部取得）")
    else:
        lines += ["| 请求 | 来源 | 状态 | 说明 |", "|---|---|---|---|"]
        lines += [f"| {f['name']} | {f['source_id']} | {f['status']} | {f['note']} |" for f in bad]
    return "\n".join(lines) + "\n"


def write_summary(db_path: Path, out_dir: Path, *, repro: list[str]) -> dict[str, Any]:
    conn = store.open_readonly(db_path)
    try:
        meta = store.meta(conn)
        fetches = _fetches(conn)
        reports = {
            name: json.loads(raw)
            for name, raw in conn.execute("SELECT name, json FROM reports ORDER BY name")
            if name != "problems"
        }
        manifest = {
            "build_id": meta["build_id"],
            "schema": meta["schema"],
            "config_identity": meta["config_identity"],
            "stages": {
                k[len("stage_") :]: v for k, v in sorted(meta.items()) if k.startswith("stage_")
            },
            "country": meta.get("country"),
            "counts": _counts(conn),
            "sources": fetches,
            "methods": [r for (r,) in conn.execute("SELECT ref FROM methods ORDER BY ref")],
            "assumptions": [
                a
                for (a,) in conn.execute(
                    "SELECT assumption_id FROM assumptions ORDER BY assumption_id"
                )
            ],
            "repro": repro,
            "run_metadata": {
                "retrieved_at": {
                    name: json.loads(raw).get("retrieved_at")
                    for name, raw in conn.execute("SELECT name, json FROM fetches ORDER BY name")
                },
                "note_zh": "获取时间是运行元数据，不进构建身份；同一份原始文件（SHA-256 相同）重新获取不改变构建身份。",
            },
        }
        index = menus_index(conn)
    finally:
        conn.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "manifest.json", manifest)
    for name, content in reports.items():
        _write(out_dir / "reports" / f"{name}.json", content)
    _write(out_dir / "menus_index.json", index)
    (out_dir / "coverage.md").write_text(
        coverage_markdown(reports["coverage"], fetches), encoding="utf-8", newline="\n"
    )
    return {
        "build_id": manifest["build_id"],
        "files": sorted(p.name for p in out_dir.rglob("*") if p.is_file()),
    }
