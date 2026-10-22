"""职业与岗位：BLS OEWS 2024 年 5 月（都会区 × 详细职业的工资分布）+ BLS 就业预测的教育与培训要求 + ACS 在职者工时（B）。

* 一条记录 = 一个都会区里一个详细职业（SOC 2018）的工资分布与就业人数。OEWS 的标记照实保留：``#`` 是顶码（年薪 ≥ 239,200 美元
  或时薪 ≥ 115 美元，只知道不低于）；``*`` 是未发布；``**`` 是就业人数未发布。不补数。
* 金额：OEWS 是 2024 年 5 月名义美元；用 CPI-U（2024 年平均 ÷ 2024 年 5 月）换到 ``USD_2024_LOCAL``——估计，原始金额照留。
* 岗位候选：每个职业三档工资（25 分位、中位数、75 分位）。三档是工资分布上的位置，不是三个真实招聘广告。目标权重 = 就业人数 ÷ 3，
  **探索性代理设定**：就业存量份额不是招聘机会频率，更不是录用概率。
* 工时与在家工作比例来自 ACS 在职者（B 映射，按 SOC 大类）；单个岗位的到岗安排、工作日与雇主资料里都没有，记为缺失。
* 资格依据：BLS 就业预测（全国）的“入职通常需要的教育、相关工作经验、在职培训”。这是全国口径的通常要求，不是某个岗位的硬性条件。
"""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from collections.abc import Iterator
from fractions import Fraction

import pandas as pd

from common.intake_contracts import (
    AssumptionSpec,
    CompileInfo,
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
POSITIONS = (
    ("p25", "annual_wage_p25"),
    ("median", "annual_wage_median"),
    ("p75", "annual_wage_p75"),
)
POSITION_ZH = {
    "p25": "工资在本职业第 25 百分位",
    "median": "工资在本职业中位数",
    "p75": "工资在本职业第 75 百分位",
}
EMPLOYMENT_PROXY = AssumptionSpec(
    assumption_id="jobs_employment_share_exposure",
    kind="exploratory_proxy",
    statement_zh="一年里接触到某个职业岗位的机会，暂按这个职业在本都会区的就业人数份额设定。",
    applies_to=("menus.jobs",),
    why_not_measured_zh="没有公开的都会区 × 职业招聘或空缺频率资料；就业存量份额不是招聘频率，也不是录用概率。",
)
JOB_COMPILE_REASONS = (
    "job_arrangement_not_observed",
    "workdays_and_onsite_days_not_observed",
    "employer_not_identified",
)


def parse_oews(content: bytes, cbsas: set[str]) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if n.endswith("MSA_M2024_dl.xlsx"))
    with z.open(name) as f:
        from product.realdata import quiet_xlsx

        with quiet_xlsx():
            df = pd.read_excel(f, dtype=str)
    return df[df["AREA"].isin(cbsas) & (df["O_GROUP"] == "detailed")].reset_index(drop=True)


def parse_ep_education(content: bytes) -> dict[str, dict[str, str]]:
    """BLS 就业预测表 5.4 → SOC → {入职教育、相关经验、在职培训}。"""
    from product.realdata import quiet_xlsx

    with quiet_xlsx():
        raw = pd.read_excel(io.BytesIO(content), sheet_name="Table 5.4", header=None, dtype=str)
    header_row = next(i for i in range(10) if any("code" in str(x).lower() for x in raw.iloc[i]))
    header = [str(x) for x in raw.iloc[header_row]]
    data = raw.iloc[header_row + 1 :]

    def col(word: str) -> int:
        return next(i for i, h in enumerate(header) if word in h.lower())

    i_code = col("code")
    i_edu, i_exp, i_ojt = col("education"), col("experience"), col("training")
    out = {}
    for _, r in data.iterrows():
        code = str(r.iloc[i_code]).strip()
        if len(code) == 7 and code[2] == "-":
            out[code] = {
                "education": str(r.iloc[i_edu]).strip(),
                "experience": str(r.iloc[i_exp]).strip(),
                "ojt": str(r.iloc[i_ojt]).strip(),
            }
    return out


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


def opportunities(rec: StdRecord, hours: dict, ep: dict[str, dict[str, str]]) -> list:
    from product.realdata.mapping_b import JOBS_METHOD
    from product.realdata.registry import make_opportunity, weight_text

    soc = str(rec.attributes["occupation_code"])
    group = hours.get(soc[:2])
    emp = rec.field("employment")
    q = ep.get(soc)
    qual = (
        [
            FieldValue(
                name="typical_entry_education",
                status="present",
                value=q["education"],
                raw=q["education"],
                kind="observed",
            ),
            FieldValue(
                name="related_work_experience",
                status="present",
                value=q["experience"],
                raw=q["experience"],
                kind="observed",
            ),
            FieldValue(
                name="typical_on_the_job_training",
                status="present",
                value=q["ojt"],
                raw=q["ojt"],
                kind="observed",
            ),
        ]
        if q
        else [
            missing(
                "typical_entry_education", "missing", "BLS 就业预测没有这个职业的教育与培训分配"
            )
        ]
    )
    hours_facts = (
        [
            FieldValue(
                name="usual_weekly_hours",
                status="present",
                value=f"{group.median_weekly_hours:g}",
                unit="hours/week",
                kind="estimated",
                method=JOBS_METHOD.ref,
                raw=f"{group.level} n={group.n}",
                raw_unit="ACS 在职者通常每周工时的加权中位数",
            ),
            FieldValue(
                name="group_wfh_share",
                status="present",
                value=group.wfh_share,
                unit="share",
                kind="estimated",
                method=JOBS_METHOD.ref,
                raw=f"{group.level} n={group.n}",
                raw_unit="SOC 大类在职者上周在家工作的比例",
            ),
        ]
        if group and group.median_weekly_hours is not None
        else [missing("usual_weekly_hours", "missing", "ACS 这一职业大类的在职者样本不足")]
    )
    out = []
    for key, wage_name in POSITIONS:
        wage = rec.field(wage_name)
        facts = (
            FieldValue(**{**wage.model_dump(), "name": "annual_wage"})
            if wage and wage.status == "present"
            else missing(
                "annual_wage",
                wage.status if wage else "missing",
                wage.reason if wage and wage.reason else "没有这一档工资",
            ),
            emp,
            *hours_facts,
            *qual,
            missing(
                "work_arrangement", "missing", "单个岗位的到岗安排（到岗天数、远程）资料里没有"
            ),
            missing("employer", "missing", "OEWS 只有职业 × 地区的汇总，雇主不可识别"),
        )
        weight = (
            weight_text(Fraction(emp.value) / len(POSITIONS))
            if emp is not None and emp.status == "present"
            else None
        )
        out.append(
            make_opportunity(
                opportunity_id=f"jobs.{rec.geo.code}.{soc.lower()}.{key}",
                sector="jobs",
                kind="job_opening_candidate",
                label=f"{rec.attributes['occupation_title']}·{POSITION_ZH[key]}",
                cbsa=rec.geo.code,
                geo=rec.geo,
                origin="mapping_b",
                method=JOBS_METHOD.ref,
                # 工时表只在这一职业大类有足够样本时才生成；没有就不引用（工时事实记为缺失）。
                lineage=(rec.record_id, *([f"b.jobs.{rec.geo.code}.{soc[:2]}"] if group else [])),
                facts=facts,
                required=("annual_wage",),
                supply_status="offered"
                if emp is not None and emp.status == "present"
                else "unknown",
                menu_policy="annual_exposure_sample",
                compile=CompileInfo(
                    status="needs_e1_extension", reasons=JOB_COMPILE_REASONS, e1_category="job"
                ),
                attributes={
                    "occupation_scheme": "SOC2018",
                    "occupation_code": soc,
                    "soc_major": soc[:2],
                    "wage_position": key,
                    "wage_basis": rec.attributes.get("wage_basis"),
                    "qualification_scope": "national (BLS EP)",
                },
                target_weight=weight,
                weight_basis="exploratory_proxy:employment_stock_share — 本职业在本都会区的就业人数 ÷ 3；不是招聘或录用概率"
                if weight
                else None,
                stratum=soc[:2],
                price_fact="annual_wage",
            )
        )
    return out
