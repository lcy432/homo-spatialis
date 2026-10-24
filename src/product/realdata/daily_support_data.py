"""日常支持与抽象消费篮子的公开资料（E3B 映射修正 §5、§6）：BLS CE 2024 全国详细表与“消费单位人数”表、FCC 2024 城市费率调查
（固定宽带）、BLS 类别 CPI。只读原始缓存（``fetch`` 命令显式联网获取），解析出编译要用的数，不在这里定价。

* **CE**：Table 1400（消费单位人数，一人消费单位一列）与 Table 2500（全部消费单位的详细条目）。每个条目名之后一行是 ``Mean``；
  条目名在表里必须恰好出现一次，值必须是数——“d/”“b/”之类的符号是资料不足，报错，不当 0。
* **FCC URS 2024**：每行是一家宽带供应商在一个普查区对一个服务档（下行 / 上行带宽与流量额度）的报价；``Weight`` 是调查权重
  （含该供应商在该区覆盖的住户数）；``Total Charge`` = 月费 + 其他强制收费 + 附加费（不含税）。同规格 = 下行在
  [下限, 上界) 之间、上行不低于下限、不限流量；参照价 = 这些报价按权重的中位数（累计权重第一次达到一半的那个价）。
* **CPI**：BLS 平面文件里某个序列的年平均（M13），只用于时期换算。
"""

from __future__ import annotations

import io
import warnings
from collections.abc import Iterable, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Any

from product.realdata.fetch import Request

CE_SIZE_REQUEST = Request(
    "bls_ce_national_2024",
    "https://www.bls.gov/cex/tables/calendar-year/mean-item-share-average-standard-error/cu-size-2024.xlsx",
    expect="xlsx",
    timeout=120,
)
CE_DETAIL_REQUEST = Request(
    "bls_ce_national_2024",
    "https://www.bls.gov/cex/tables/calendar-year/mean/cu-all-detail-2024.xlsx",
    expect="xlsx",
    timeout=120,
)
FCC_URS_REQUEST = Request(
    "fcc_urs_2024",
    "https://www.fcc.gov/sites/default/files/2024_urs_broadband_website_data 2023-12-26.xlsx",
    expect="xlsx",
    timeout=180,
)
FCC_METHOD_REQUEST = Request(
    "fcc_urs_2024",
    "https://www.fcc.gov/sites/default/files/2024 Urban Rate Survey Broadband Methodology Report.pdf",
    timeout=180,
)
CPI_COMMUNICATION_REQUEST = Request(
    "bls_cpi_u",
    "https://download.bls.gov/pub/time.series/cu/cu.data.17.USEducationAndCommunication",
    expect="text",
    timeout=300,
)
REQUESTS = (
    CE_SIZE_REQUEST,
    CE_DETAIL_REQUEST,
    FCC_URS_REQUEST,
    FCC_METHOD_REQUEST,
    CPI_COMMUNICATION_REQUEST,
)


class DataError(ValueError):
    pass


def _xlsx_rows(content: bytes, sheet: str | None = None) -> list[list[Any]]:
    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # BLS 的工作簿没有默认样式：openpyxl 只是提醒
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _dec(v: Any) -> Decimal:
    return Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v))


def read_ce_means(content: bytes, labels: Iterable[str], column: int) -> dict[str, str]:
    """一张 CE 表里这些条目的年均支出（美元 / 每个消费单位）。``column`` 是列号（Table 1400 的一人消费单位是 2，Table 2500 的
    全部消费单位是 1）。"""
    rows = _xlsx_rows(content)
    out: dict[str, str] = {}
    for label in labels:
        hits = [i for i, r in enumerate(rows) if isinstance(r[0], str) and r[0].strip() == label]
        if len(hits) != 1:
            raise DataError(f"CE 表里条目“{label}”出现 {len(hits)} 次（要求恰好一次）")
        i = hits[0]
        if i + 1 >= len(rows) or rows[i + 1][0] != "Mean":
            raise DataError(f"CE 表里条目“{label}”下一行不是 Mean")
        v = rows[i + 1][column] if column < len(rows[i + 1]) else None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise DataError(f"CE 表里条目“{label}”的均值是 {v!r}：资料不足，不当 0")
        out[label] = str(_dec(v))
    return out


def ce_title(content: bytes) -> str:
    rows = _xlsx_rows(content)
    return str(rows[0][0])[:300] if rows and rows[0] and rows[0][0] else ""


def weighted_median(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    """按权重的中位数：按值从小到大累加权重，第一次达到总权重一半的那个值（下中位数）。没有正权重时是 None。"""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    total = sum((w for _, w in pairs), Decimal(0))
    if total <= 0:
        return None
    acc = Decimal(0)
    for v, w in pairs:
        acc += w
        if acc * 2 >= total:
            return v
    return pairs[-1][0]


def read_fcc_urs(
    content: bytes, spec: dict[str, Any], regions: dict[str, Sequence[str]]
) -> dict[str, Any]:
    """FCC URS 2024 固定宽带：同规格报价按大区与全国的加权中位数总月费（2023 年调查价，不含税）。"""
    rows = _xlsx_rows(content, "Data")
    head = [str(h) for h in rows[0]]
    need = (
        "State",
        "Weight",
        "Download Bandwidth Mbps",
        "Upload Bandwidth Mbps",
        "Usage Allowance GB",
        "Total Charge",
    )
    missing = [h for h in need if h not in head]
    if missing:
        raise DataError(f"FCC URS 数据表缺列 {missing}")
    col = {h: head.index(h) for h in need}
    lo, hi, up = (
        Decimal(str(spec[k]))
        for k in ("download_min_mbps", "download_below_mbps", "upload_min_mbps")
    )
    by_state: dict[str, list[tuple[Decimal, Decimal]]] = {}
    total_rows = 0
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        total_rows += 1
        down, upl = _dec(r[col["Download Bandwidth Mbps"]]), _dec(r[col["Upload Bandwidth Mbps"]])
        unlimited = str(r[col["Usage Allowance GB"]]).strip() == "Inf"
        if lo <= down < hi and upl >= up and (unlimited or spec["usage"] != "unlimited"):
            by_state.setdefault(str(r[col["State"]]).strip().upper(), []).append(
                (_dec(r[col["Total Charge"]]), _dec(r[col["Weight"]]))
            )

    def summary(pairs: list[tuple[Decimal, Decimal]]) -> dict[str, Any]:
        m = weighted_median(pairs)
        return {
            "offers": len(pairs),
            "weight_sum": str(sum((w for _, w in pairs), Decimal(0))),
            "weighted_median_total_charge": None if m is None else str(m),
        }

    out: dict[str, Any] = {
        "rows": total_rows,
        "spec": spec,
        "by_region": {},
        "national": summary([p for v in by_state.values() for p in v]),
    }
    for region, states in regions.items():
        pairs = [p for s in states for p in by_state.get(s.upper(), [])]
        out["by_region"][region] = summary(pairs)
    return out


def read_cpi_annual(content: bytes, series_id: str, years: Iterable[int]) -> dict[str, str]:
    """BLS CPI 平面文件里一个序列的年平均（period M13）。"""
    want = {str(y) for y in years}
    out: dict[str, str] = {}
    for line in content.decode("utf-8").splitlines()[1:]:
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 4 and parts[0] == series_id and parts[1] in want and parts[2] == "M13":
            out[parts[1]] = parts[3]
    absent = sorted(want - set(out))
    if absent:
        raise DataError(f"CPI 序列 {series_id} 没有 {absent} 的年平均")
    return out


def allocated_annual(
    one_person: dict[str, str], detail: dict[str, str], line: dict[str, Any]
) -> tuple[Fraction, str]:
    """一条篮子或支持项的一人消费单位年支出：直接读一人表的条目；要拆分时 = 一人表的大类 × 全国详细表里所选子项占这一大类的
    比例（分配估计）。返回（年支出，口径）。"""
    total = Fraction(Decimal(one_person[line["size_label"]]))
    items = line.get("detail_items")
    if not items:
        return total, "one_person_table"
    parent = Fraction(Decimal(detail[line["detail_parent"]]))
    part = sum((Fraction(Decimal(detail[i])) for i in items), Fraction(0))
    if parent <= 0:
        raise DataError(f"全国详细表里“{line['detail_parent']}”是 0：不能按比例拆分")
    return total * part / parent, "allocated_by_national_detail_share"
