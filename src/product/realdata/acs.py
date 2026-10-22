"""ACS 批量文件的读取：PUMS 州文件（住房与人员）、表格摘要文件与数据字典。

只读、只筛选，不做估计：筛出验证都会区里的 PUMA，住房只留租住（TEN = 3）与空置待租（VACS = 1）并有月租的记录，人员只留在职
（ESR = 1、2）。权重原样保留：住房的 WGTP 与 80 个重复权重 WGTP1–WGTP80（标准误用），人员的 PWGTP。
"""

from __future__ import annotations

import csv
import io
import zipfile

import pandas as pd

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
