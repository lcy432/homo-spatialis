"""ACS 批量文件的读取：PUMS 州文件（住房与人员）、表格摘要文件与数据字典。

只读、只筛选，不做估计：筛出验证都会区里的 PUMA，住房只留租住（TEN = 3）与空置待租（VACS = 1）并有月租的记录，人员只留在职
（ESR = 1、2）。权重原样保留：住房的 WGTP 与 80 个重复权重 WGTP1–WGTP80（标准误用），人员的 PWGTP。
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from common.intake_contracts import MethodSpec

REP_WEIGHTS = tuple(f"WGTP{i}" for i in range(1, 81))


def _csv_member(content: bytes, prefix: str) -> tuple[zipfile.ZipFile, str]:
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(
        n for n in z.namelist() if n.lower().startswith(prefix) and n.lower().endswith(".csv")
    )
    return z, name


def read_pums_housing(content: bytes, pumas: set[str], columns: tuple[str, ...]) -> pd.DataFrame:
    """一个州的住房文件 → 验证都会区 PUMA 里、租住或空置待租且有月租的记录（含 80 个重复权重）。"""
    z, name = _csv_member(content, "psam_h")
    cols = list(dict.fromkeys([*columns, *REP_WEIGHTS]))
    with z.open(name) as f:
        df = pd.read_csv(
            f, usecols=cols, dtype={"SERIALNO": str, "STATE": str, "PUMA": str, "BLD": str}
        )
    df["STATE"] = df["STATE"].str.zfill(2)
    df["PUMA"] = df["PUMA"].str.zfill(5)
    df = df[df["PUMA"].isin(pumas)]
    rent = pd.to_numeric(df["RNTP"], errors="coerce")
    keep = ((df["TEN"] == 3) | (df["VACS"] == 1)) & (rent > 0)
    return df[keep].reset_index(drop=True)


def read_pums_persons(content: bytes, pumas: set[str], columns: tuple[str, ...]) -> pd.DataFrame:
    """一个州的人员文件 → 验证都会区 PUMA 里在职（ESR = 1、2）的人。"""
    z, name = _csv_member(content, "psam_p")
    with z.open(name) as f:
        df = pd.read_csv(
            f,
            usecols=list(columns),
            dtype={"SERIALNO": str, "STATE": str, "PUMA": str, "SOCP": str},
        )
    df["STATE"] = df["STATE"].str.zfill(2)
    df["PUMA"] = df["PUMA"].str.zfill(5)
    df = df[df["PUMA"].isin(pumas) & df["ESR"].isin([1, 2])]
    return df.reset_index(drop=True)


def read_summary_table(content: bytes, geo_ids: set[str]) -> dict[str, dict[str, str]]:
    """表格摘要文件（竖线分隔）→ GEO_ID → {列名 → 原样文字}。只保留需要的地理。"""
    text = content.decode("latin-1")
    rows = text.splitlines()
    header = rows[0].split("|")
    out = {}
    for ln in rows[1:]:
        p = ln.split("|")
        if p[0] in geo_ids:
            out[p[0]] = dict(zip(header, p, strict=False))
    return out


def dictionary_labels(content: bytes) -> dict[str, dict[str, str]]:
    """PUMS 数据字典 → 变量 → {代码 → 官方标签}（只取单值代码，不取区间）。"""
    out: dict[str, dict[str, str]] = {}
    for row in csv.reader(io.StringIO(content.decode("utf-8", "replace"))):
        if len(row) >= 7 and row[0] == "VAL" and row[4] == row[5]:
            out.setdefault(row[1], {})[row[4]] = row[6]
    return out


# --------------------------------------------------------------------------- 官方顶码表

TOPCODE_METHOD = MethodSpec(
    method_id="acs_official_topcodes",
    version="1",
    family="classification",
    summary_zh=(
        "按 Census 官方 2024 PUMS 顶码表识别合同租金顶码：州内超过阈值（RNTTPCT）的租金被换成同一个替代值（T_RNT，"
        "超过阈值的那些租金的平均）。替代值不是每条记录的真实值，只知道真实值高于本州阈值。官方表里没有的州：只把整州文件里"
        "的最大值标为“是否顶码未知”，它的真实值只知道高于整州第二大的租金。"
    ),
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=("跨州混合的格子里，可识别的分位数上限取格内顶码记录所在州的最低阈值",),
)


@dataclass(frozen=True)
class TopcodeRule:
    """一个州的合同租金顶码规则。``official``：官方阈值与替代值；``missing``：官方表里没有这个州，只有整州文件的最大与第二大值。"""

    state: str
    status: Literal["official", "missing"]
    threshold: float | None = None
    replacement: float | None = None
    file_max: float | None = None
    file_second: float | None = None

    def classify(self, rntp: float) -> tuple[str, float | None]:
        """一条记录 → （顶码状态，真实值的下界）。状态：``topcoded`` / ``unknown`` / ``not_topcoded``。"""
        if self.status == "official":
            assert self.threshold is not None and self.replacement is not None
            if rntp == self.replacement and self.replacement > self.threshold:
                return "topcoded", self.threshold
            if (
                rntp > self.threshold
            ):  # 官方说超过阈值的都被替换了；出现别的值说明对不上，按未知处理
                return "unknown", self.threshold
            return "not_topcoded", None
        if self.file_max is not None and rntp == self.file_max:
            return "unknown", self.file_second if self.file_second is not None else 0.0
        return "not_topcoded", None


def parse_topcodes(content: bytes) -> dict[str, TopcodeRule]:
    """官方顶码表（每州一行；合同租金是 RNTTPCT = 阈值、T_RNT = 替代值）→ 州代码 → 规则。"""
    out: dict[str, TopcodeRule] = {}
    for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig"))):
        st = str(row.get("STATE", "")).strip().zfill(2)
        try:
            t, r = float(row["RNTTPCT"]), float(row["T_RNT"])
        except (KeyError, TypeError, ValueError):
            continue
        out[st] = TopcodeRule(state=st, status="official", threshold=t, replacement=r)
    return out


#: 毛租金的组成部分也会顶码（官方表同一行）：电费、燃气费是月费，水费、其他燃料费是年费。列名是（阈值，替代值）。
UTILITY_TOPCODE_COLUMNS = {
    "ELEP": ("ELETPCT", "T_ELE"),
    "GASP": ("GASTPCT", "T_GAS"),
    "WATP": ("WATTPCT", "T_WAT"),
    "FULP": ("FULTPCT", "T_FUL"),
}


def parse_utility_topcodes(content: bytes) -> dict[str, dict[str, tuple[float, float]]]:
    """官方顶码表里水电燃料的阈值与替代值 → 州代码 → {变量: (阈值, 替代值)}。"""
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig"))):
        st = str(row.get("STATE", "")).strip().zfill(2)
        rules: dict[str, tuple[float, float]] = {}
        for var, (t, r) in UTILITY_TOPCODE_COLUMNS.items():
            try:
                rules[var] = (float(row[t]), float(row[r]))
            except (KeyError, TypeError, ValueError):
                continue
        if rules:
            out[st] = rules
    return out


def utility_topcoded(value: float, rule: tuple[float, float] | None) -> bool:
    """一个水电燃料金额受不受顶码影响：等于替代值（且替代值高于阈值）是顶码；高于阈值却不等于替代值说明对不上，也当受影响。
    没有这个州的规则时不能判断：当受影响（不拿可能是替代值的数当真实费用）。"""
    if rule is None:
        return True
    threshold, replacement = rule
    return value > threshold or (value == replacement and replacement > threshold)


def rent_top_values(content: bytes) -> tuple[float | None, float | None]:
    """整州住房文件里合同租金的最大值与第二大的不同值（只在官方表缺这个州时使用）。"""
    z, name = _csv_member(content, "psam_h")
    with z.open(name) as f:
        rent = pd.read_csv(f, usecols=["RNTP"])["RNTP"]
    vals = sorted({float(v) for v in rent.dropna() if v > 0})
    return (vals[-1] if vals else None), (vals[-2] if len(vals) > 1 else None)
