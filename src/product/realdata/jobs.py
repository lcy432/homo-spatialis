"""职业的来源事实：BLS OEWS 2024 年 5 月（都会区 × 详细职业的工资分布）+ BLS 就业预测的教育与培训要求。

* 一条 OEWS 记录 = 一个都会区里一个详细职业（SOC 2018）的工资分布与就业人数。OEWS 的标记照实保留：``#`` 是顶码（年薪 ≥ 239,200 美元
  或时薪 ≥ 115 美元，只知道不低于）；``*`` 是未发布；``**`` 是就业人数未发布。不补数。
* 金额：OEWS 是 2024 年 5 月名义美元；用 CPI-U（2024 年平均 ÷ 2024 年 5 月）换到 ``USD_2024_LOCAL``——估计，原始金额照留。
* 一条 EP 记录 = 一个详细职业的全国入职要求（表 5.4 的“入职通常需要的教育、相关工作经验、在职培训”）。这是全国口径的通常要求，
  不是某个岗位的硬性条件；文字照表里读到的原样保存。
* intake 只保存这些来源事实与工时表（ACS 在职者，``mapping_b.job_hours``）。岗位候选池与年度岗位菜单在 E3B 生活资料包里
  （岗位模板 × 20 个年薪支持点 × 工时与就业片区的联合分布），intake 不再生成岗位候选。
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from collections.abc import Iterator
from fractions import Fraction
from typing import Any

import pandas as pd

from common.intake_contracts import (
    FieldValue,
    GeoRef,
    Period,
    SourceRef,
    StdRecord,
    missing,
)
from product.realdata.prices import METHOD as CPI_METHOD
from product.realdata.prices import to_cents_text

PERIOD = Period(label="OEWS May 2024", start=_dt.date(2024, 5, 1), end=_dt.date(2024, 5, 31))
WAGE_COLUMNS = {
    "annual_wage_p10": "A_PCT10",
    "annual_wage_p25": "A_PCT25",
    "annual_wage_median": "A_MEDIAN",
    "annual_wage_p75": "A_PCT75",
    "annual_wage_p90": "A_PCT90",
    "annual_wage_mean": "A_MEAN",
}
NATION = GeoRef(
    country="US",
    scheme="Nation",
    code="US",
    level="national",
    label="United States",
    cbsa=None,
    relation="contains",
)
#: EP 表 5.4 的三列 → 记录字段名（与原先岗位候选上的事实名相同，下游按这些名字读）。
EP_FIELDS = {
    "education": "typical_entry_education",
    "experience": "related_work_experience",
    "ojt": "typical_on_the_job_training",
}


def parse_oews(content: bytes, cbsas: set[str]) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if n.endswith("MSA_M2024_dl.xlsx"))
    with z.open(name) as f:
        from product.realdata import quiet_xlsx

        with quiet_xlsx():
            df = pd.read_excel(f, dtype=str)
    return df[df["AREA"].isin(cbsas) & (df["O_GROUP"] == "detailed")].reset_index(drop=True)


def parse_ep_education(content: bytes) -> dict[str, Any]:
    """BLS 就业预测表 5.4 → {表题, SOC → {职业名、入职教育、相关经验、在职培训}}。"""
    from product.realdata import quiet_xlsx

    with quiet_xlsx():
        # keep_default_na=False：表里的 "None"（不需要）是原文，不能被 pandas 读成缺失再写成 "nan"
        raw = pd.read_excel(
            io.BytesIO(content),
            sheet_name="Table 5.4",
            header=None,
            dtype=str,
            keep_default_na=False,
        )
    header_row = next(i for i in range(10) if any("code" in str(x).lower() for x in raw.iloc[i]))
    header = [str(x) for x in raw.iloc[header_row]]
    data = raw.iloc[header_row + 1 :]

    def col(word: str) -> int:
        return next(i for i, h in enumerate(header) if word in h.lower())

    i_code, i_title = col("code"), col("title")
    i_edu, i_exp, i_ojt = col("education"), col("experience"), col("training")
    out = {}
    for _, r in data.iterrows():
        code = str(r.iloc[i_code]).strip()
        if len(code) == 7 and code[2] == "-":
            out[code] = {
                "title": str(r.iloc[i_title]).strip(),
                "education": str(r.iloc[i_edu]).strip(),
                "experience": str(r.iloc[i_exp]).strip(),
                "ojt": str(r.iloc[i_ojt]).strip(),
            }
    return {"table_title": str(raw.iloc[0, 0]).strip(), "occupations": out}


def ep_records(ep: dict[str, Any], raw_sha: str | None) -> Iterator[StdRecord]:
    """EP 表 5.4 的每个详细职业 → 一条全国记录（``jobs.ep.<soc>``）。文字照解析结果原样存，不归类、不改写。"""
    m = re.search(r"(\d{4})\s*$", ep["table_title"])
    if m is None:
        raise ValueError(f"EP 表 5.4 的表题里读不到年份：{ep['table_title']!r}")
    year = int(m.group(1))
    period = Period(
        label=f"EP 表 5.4（{year} 年分配）", start=_dt.date(year, 1, 1), end=_dt.date(year, 12, 31)
    )
    for soc, q in sorted(ep["occupations"].items()):
        yield StdRecord(
            record_id=f"jobs.ep.{soc.lower()}",
            sector="jobs",
            record_type="occupation_entry_requirements",
            label=f"{q['title']}（入职要求，全国）",
            geo=NATION,
            period=period,
            source=SourceRef(
                source_id="bls_ep_education",
                locator=f"education.xlsx Table 5.4 code={soc}",
                raw_sha256=raw_sha,
            ),
            fields=tuple(
                FieldValue(name=name, status="present", value=q[key], raw=q[key], kind="observed")
                for key, name in EP_FIELDS.items()
            ),
            attributes={
                "occupation_scheme": "SOC2018",
                "occupation_code": soc,
                "occupation_title": q["title"],
                "qualification_scope": "national (BLS EP)",
            },
        )


def _wage(name: str, raw: str, factor: Fraction) -> FieldValue:
    raw = str(raw).strip()
    if raw == "#":
        return missing(name, "suppressed", "OEWS 顶码：年薪 ≥ 239,200 美元，只知道不低于")
    if raw in ("*", "nan", "", "None"):
        return missing(name, "suppressed", "OEWS 未发布这项估计")
    value = Fraction(raw.replace(",", "")) * factor
    return FieldValue(
        name=name,
        status="present",
        raw=raw,
        raw_unit="USD/year（OEWS 2024 年 5 月名义值）",
        value=to_cents_text(value),
        unit="USD/year",
        numeraire="USD_2024_LOCAL",
        kind="estimated",
        method=f"{CPI_METHOD.ref}（×{float(factor):.6f}）",
    )


def records(
    df: pd.DataFrame, factor: Fraction, raw_sha: str | None, titles: dict[str, str]
) -> Iterator[StdRecord]:
    for r in df.itertuples(index=False):
        emp = str(r.TOT_EMP).strip()
        fields = [
            (
                FieldValue(
                    name="employment",
                    status="present",
                    raw=emp,
                    raw_unit="jobs",
                    value=emp.replace(",", ""),
                    unit="jobs",
                )
                if emp not in ("**", "*", "nan")
                else missing("employment", "suppressed", "OEWS 未发布就业人数")
            ),
            *(_wage(k, getattr(r, col), factor) for k, col in WAGE_COLUMNS.items()),
        ]
        basis = (
            "annual_only"
            if str(r.ANNUAL).upper() == "TRUE"
            else ("hourly_only" if str(r.HOURLY).upper() == "TRUE" else "annual_and_hourly")
        )
        yield StdRecord(
            record_id=f"jobs.oews2024.{r.AREA}.{r.OCC_CODE.lower()}",
            sector="jobs",
            record_type="occupation_wage_distribution",
            label=f"{r.OCC_TITLE}（{titles.get(r.AREA, r.AREA_TITLE)}）",
            geo=GeoRef(
                country="US",
                scheme="CBSA2023",
                code=r.AREA,
                level="cbsa",
                label=r.AREA_TITLE,
                cbsa=r.AREA,
                relation="exact",
            ),
            period=PERIOD,
            source=SourceRef(
                source_id="bls_oews_may2024",
                locator=f"oesm24ma/MSA_M2024_dl.xlsx AREA={r.AREA} OCC_CODE={r.OCC_CODE}",
                raw_sha256=raw_sha,
            ),
            fields=tuple(fields),
            attributes={
                "occupation_scheme": "SOC2018",
                "occupation_code": r.OCC_CODE,
                "occupation_title": r.OCC_TITLE,
                "wage_basis": basis,
            },
        )
