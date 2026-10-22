"""职业培训：IPEDS 2023–24 年“按项目报告”的院校的最大几个项目（学费、书本用品费、项目长度、全日制完成月数）+ CIP→SOC 对照。

* 院校与所在都会区取自 HD2023（含 CBSA 代码）；只保留六个验证都会区里的院校。项目取自 IC2023_PY（每校最多 6 个项目）。IPEDS 的
  补值标记（A = 不适用、R = 报告值……）原样当作“有没有值”的依据：不是报告值就不当数。
* 学费是整个项目的公布学费与杂费；2023–24 学年名义美元，用 2023 年 7 月–2024 年 6 月的平均 CPI-U 换到 ``USD_2024_LOCAL``（估计）。
* **授予的能力**：CIP 专业代码经 NCES/BLS 的 CIP 2020 → SOC 2018 对照，得到“相关职业”。这是专业与职业的对照关系，不是录用保证，
  也不代表完成课程就满足某个岗位的资格。
* 开课日期、每周上课小时数资料里没有，记为缺失：日历未知（不当成随时可报），本人参与时间未知（不当 0）。
"""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from fractions import Fraction

import pandas as pd

from common.intake_contracts import (
    Calendar,
    FieldValue,
    GeoRef,
    MethodSpec,
    Period,
    SourceRef,
    StdRecord,
    missing,
)
from product.realdata.prices import METHOD as CPI_METHOD
from product.realdata.prices import Cpi, months, to_cents_text

PERIOD = Period(
    label="IPEDS academic year 2023-24", start=_dt.date(2023, 7, 1), end=_dt.date(2024, 6, 30)
)
MEASURE = {"1": "credit hours", "2": "clock hours", "3": "weeks"}
CIP_SOC = MethodSpec(
    method_id="cip_soc_related_occupations",
    version="1",
    family="classification",
    summary_zh="项目的 CIP 2020 专业代码经 NCES/BLS 官方对照表得到相关的 SOC 2018 职业；只说明专业与职业相关，不代表资格或录用。",
    inputs=("nces_cip_soc_2020",),
)


def _read(content: bytes) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    df = pd.read_csv(z.open(name), dtype=str, encoding="latin-1", index_col=False)
    df.columns = [c.strip().lstrip("﻿").lstrip("ï»¿") for c in df.columns]
    return df


def parse_institutions(content: bytes, cbsas: set[str]) -> pd.DataFrame:
    df = _read(content)
    return df[df["CBSA"].isin(cbsas)].reset_index(drop=True)


def parse_programs(content: bytes) -> pd.DataFrame:
    return _read(content)


def parse_cip_soc(content: bytes) -> dict[str, list[tuple[str, str]]]:
    df = pd.read_excel(io.BytesIO(content), sheet_name="CIP-SOC", dtype=str)
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for _, r in df.iterrows():
        cip, soc = str(r["CIP2020Code"]).strip(), str(r["SOC2018Code"]).strip()
        if soc and soc != "99-9999" and soc != "nan":
            out[cip].append((soc, str(r["SOC2018Title"]).strip()))
    return dict(out)


def _reported(flag: str | None, value: str | None) -> str | None:
    """IPEDS 的补值标记：只有标为报告值（R）或没有标记且有数字时才当数。"""
    if value is None or str(value).strip() in ("", "nan", "."):
        return None
    if flag is not None and str(flag).strip() not in ("R", "", "nan"):
        return None
    return str(value).strip()


def records(
    insts: pd.DataFrame,
    programs: pd.DataFrame,
    cpi: Cpi,
    cip_soc: dict,
    sha_hd: str | None,
    sha_py: str | None,
) -> Iterator[StdRecord]:
    factor = cpi.factor(months("2023-07", "2024-06"))
    by_id = {r["UNITID"]: r for _, r in insts.iterrows()}
    for _, p in programs.iterrows():
        inst = by_id.get(p["UNITID"])
        if inst is None:
            continue
        for k in range(1, 7):
            cip = _reported(None, p.get(f"CIPCODE{k}"))
            if cip is None or cip in ("A", "R"):
                continue
            tuition = _reported(p.get(f"XCIPTUI{k}"), p.get(f"CIPTUIT{k}"))
            supplies = _reported(p.get(f"XCIPSUP{k}"), p.get(f"CIPSUPP{k}"))
            length = _reported(p.get(f"XCIPLGT{k}"), p.get(f"CIPLGTH{k}"))
            months_cmp = _reported(p.get(f"XMTHCMP{k}"), p.get(f"MTHCMP{k}"))
            measure = MEASURE.get(str(p.get(f"PRGMSR{k}")).strip())

            def money(name: str, raw: str | None) -> FieldValue:
                if raw is None:
                    return missing(name, "missing", "IPEDS 没有这项报告值")
                return FieldValue(
                    name=name,
                    status="present",
                    raw=raw,
                    raw_unit="USD per program（2023–24 学年名义值）",
                    value=to_cents_text(Fraction(raw) * factor),
                    unit="USD/program",
                    numeraire="USD_2024_LOCAL",
                    kind="estimated",
                    method=f"{CPI_METHOD.ref}（2023-07–2024-06 平均 → 2024 年平均，×{float(factor):.6f}）",
                )

            related = cip_soc.get(cip, [])
            fields = (
                money("tuition_fees", tuition),
                money("books_supplies", supplies),
                FieldValue(
                    name="program_length",
                    status="present",
                    raw=length,
                    raw_unit=measure or "unknown measure",
                    value=length,
                    unit=measure or "unknown",
                )
                if length is not None
                else missing("program_length", "missing", "IPEDS 没有项目长度"),
                FieldValue(
                    name="months_to_complete",
                    status="present",
                    raw=months_cmp,
                    raw_unit="months (normal full-time)",
                    value=months_cmp,
                    unit="months",
                )
                if months_cmp is not None
                else missing("months_to_complete", "missing", "IPEDS 没有全日制完成月数"),
                missing("start_dates", "missing", "IPEDS 不记开课日期"),
                missing("weekly_hours", "missing", "IPEDS 不记每周上课小时数"),
                FieldValue(
                    name="related_occupations",
                    status="present",
                    value=";".join(s for s, _ in related)[:200],
                    kind="derived",
                    method=CIP_SOC.ref,
                    raw=cip,
                )
                if related
                else missing(
                    "related_occupations", "unmappable", "CIP→SOC 对照表里这个专业没有对应职业"
                ),
            )
            yield StdRecord(
                record_id=f"training.ipeds2023.{p['UNITID']}.{k}",
                sector="training",
                record_type="training_program",
                label=f"CIP {cip} 项目 · {inst['INSTNM']}"[:200],
                geo=GeoRef(
                    country="US",
                    scheme="CBSA2023",
                    code=str(inst["CBSA"]),
                    level="point",
                    label=f"{inst['CITY']}, {inst['STABBR']}",
                    cbsa=str(inst["CBSA"]),
                    relation="within",
                ),
                period=PERIOD,
                source=SourceRef(
                    source_id="ipeds_2023",
                    locator=f"IC2023_PY UNITID={p['UNITID']} program={k}; HD2023",
                    raw_sha256=sha_py,
                ),
                fields=fields,
                attributes={
                    "institution": inst["INSTNM"],
                    "unitid": p["UNITID"],
                    "cip": cip,
                    "related_soc_titles": "; ".join(t for _, t in related[:5]) or None,
                    "sector_code": inst.get("SECTOR"),
                    "city": inst.get("CITY"),
                },
                terms={"books_supplies_in_tuition": "excluded"},
            )


def opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    months_f = rec.field("months_to_complete")
    data_gaps: list[str] = []
    e1_gaps: list[str] = []
    if months_f is not None and months_f.status == "present":
        try:
            m = Fraction(months_f.value)  # type: ignore[arg-type]
            if m.denominator != 1 or m % 12 != 0:
                e1_gaps.append("length_not_whole_years")  # E1 的培训按整年计
        except (ValueError, ZeroDivisionError):
            data_gaps.append("length_unreadable")
    return make_opportunity(
        opportunity_id=rec.record_id.replace(
            "training.ipeds2023.", f"training.program.{rec.geo.cbsa}.", 1
        ),
        sector="training",
        kind="training_program",
        label=rec.label,
        cbsa=rec.geo.cbsa,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=("tuition_fees",),
        supply_status="offered",
        menu_policy="calendar_open",
        e1_category="training",
        e1_needs=("weekly_hours", "start_dates", "months_to_complete"),
        data_gaps=("calendar_unknown", *data_gaps),
        e1_gaps=tuple(e1_gaps),
        attributes=dict(rec.attributes),
        terms=dict(rec.terms),
        calendar=Calendar(known=False, reason="IPEDS 不记开课日期"),
        price_fact="tuition_fees",
    )
