"""B 映射：由真实资料估计的条件关系。

**住房（``housing_rent_conditional@1``）**：在一个都会区里，按 卧室数 × 地点（PUMA 或通勤三分位层）× 楼型 估计月租的条件分布。

* 目标人群是“新租市场的代理”：空置待租房的要价 ∪ 过去 12 个月内迁入的租住户的合同租金（ACS 住房权重 WGTP 加权）。居民存量
  租金不直接当市场价：只有在市场代理样本不足时作为最后一层回退，并标明人群换了。
* 加权分位数取“累计权重第一次达到 q × 总权重”的那个**真实观测值**（阶梯函数，不插值），所以每个分位数都是某条样本的实际租金。
* 标准误用 ACS 的连续差分重复权重（WGTP1–WGTP80）：SE = √(4/80 × Σ(θ_r − θ)²)。
* 每格要求未加权样本数 ≥ ``min_unweighted_n``（默认 30）；不够就按层级回退：(PUMA, 卧室) → (通勤层, 卧室, 楼型) → (通勤层, 卧室)
  → (都会区, 卧室) → (都会区, 卧室, 存量人群) → 不支持。用了哪一层记在机会里，不悄悄抹掉。
* 顶码：PUMS 把州内最高一段租金换成同一个值；分位数落在顶码值上就标为“截断”（只知道不低于它）。
* 房间数不是面积：ACS 没有面积，面积一律缺失，不从卧室数推。

**通勤（``commute_by_residence_puma@1``）**：按居住地 PUMA，在职且不在家工作的人（JWMNP > 0）单程通勤分钟的加权分布（PWGTP），
以及在家工作的比例。这是居民去**各自**工作地点的时间，不是去某个就业中心的时间。

**岗位工时（``job_hours_wfh_by_occupation_group@1``）**：按 都会区 × SOC 大类，在职者通常每周工时的加权中位数与上周在家工作的
比例。在职者不是新招聘岗位；在家工作比例不是某个岗位的工作安排。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np
import pandas as pd

from common.intake_contracts import AssumptionSpec, MethodSpec

HOUSING_METHOD = MethodSpec(
    method_id="housing_rent_conditional",
    version="1",
    family="mapping_b",
    summary_zh=(
        "按 卧室数 × 地点 × 楼型 估计月租（合同租金或要价）的条件分布：新租市场代理人群（空置待租 ∪ 12 个月内迁入）、WGTP 加权、"
        "阶梯加权分位数（取真实观测值）、重复权重标准误、样本不足按层级回退。"
    ),
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=(
        "新迁入者的合同可能含入住优惠，空置待租房偏向难租的房子：两者都只是新租市场的代理",
        "ACS 是年度调查的报告值，不是房源广告；同一格里的差异混合了面积、装修与具体位置，资料里没有面积",
        "回退到存量人群时，租金会偏低（老租户的合同更便宜）",
    ),
)
COMMUTE_METHOD = MethodSpec(
    method_id="commute_by_residence_puma",
    version="1",
    family="mapping_b",
    summary_zh="按居住地 PUMA：在职且不在家工作者单程通勤分钟的加权分位数（PWGTP），在家工作的加权比例。",
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=(
        "居民去各自工作地点的时间，不是去某个就业中心或某个岗位的时间",
        "往返时间按单程 × 2 估计（假设去回对称）",
    ),
)
JOBS_METHOD = MethodSpec(
    method_id="job_hours_wfh_by_occupation_group",
    version="1",
    family="mapping_b",
    summary_zh="按 都会区 × SOC 大类：在职者通常每周工时（WKHP）的加权中位数、上周在家工作（JWTRNS = 11）的加权比例；样本不足回退到六个都会区合并。",
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=(
        "在职者不是新招聘岗位：新岗位的工时与安排可能不同",
        "在家工作比例是这一大类在职者的比例，不是某个岗位的固定安排",
    ),
)
MARKET_PROXY = AssumptionSpec(
    assumption_id="housing_new_market_proxy",
    kind="exploratory_proxy",
    statement_zh="空置待租房的要价与过去 12 个月内迁入者的合同租金，近似一个新来的人在这座城会看到的租金分布。",
    applies_to=("housing_rent_conditional@1", "housing_coverage_grid@1"),
    why_not_measured_zh="没有可用的公开房源成交资料；HUD 公平市场租金文件访问被拒（反爬挑战）。",
)
ROUNDTRIP = AssumptionSpec(
    assumption_id="commute_roundtrip_is_twice_oneway",
    kind="assumption",
    statement_zh="往返通勤分钟 = 单程通勤分钟 × 2（去回对称）。",
    applies_to=("commute_by_residence_puma@1",),
    why_not_measured_zh="ACS 只问去工作地点的单程时间。",
)

LEVELS = ("puma_bed", "tier_bed_struct", "tier_bed", "metro_bed", "metro_bed_stock")
MARKET = ("vacant_for_rent", "recent_mover")


def wquantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """阶梯加权分位数：累计权重第一次达到 q × 总权重的那个观测值。"""
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    cw = np.cumsum(w)
    idx = int(np.searchsorted(cw, q * cw[-1], side="left"))
    return float(v[min(idx, len(v) - 1)])


def wquantile_smooth(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """插值加权分位数（只用于标准误）：报告值四舍五入、大量并列时，阶梯分位数在重复权重之间几乎不动，标准误会塌成 0。这里把
    每个不同的报告值当作它与相邻值中点之间的均匀区间，在累计分布上线性插值（与 Census 发布表的中位数插值同一思路）。"""
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    uniq, inv = np.unique(v, return_inverse=True)
    wsum = np.bincount(inv, weights=w)
    if len(uniq) == 1:
        return float(uniq[0])
    edges = np.concatenate(
        [
            [uniq[0] - (uniq[1] - uniq[0]) / 2],
            (uniq[:-1] + uniq[1:]) / 2,
            [uniq[-1] + (uniq[-1] - uniq[-2]) / 2],
        ]
    )
    cdf = np.concatenate([[0.0], np.cumsum(wsum) / wsum.sum()])
    return float(np.interp(q, cdf, edges))


def replicate_se(values: np.ndarray, rep: np.ndarray, weights: np.ndarray, q: float) -> float:
    """ACS 连续差分重复权重的标准误：√(4/80 × Σ(θ_r − θ)²)，θ 用插值分位数。``rep`` 是 n × 80 的重复权重。"""
    est = wquantile_smooth(values, weights, q)
    thetas = np.array(
        [wquantile_smooth(values, np.clip(rep[:, r], 0, None), q) for r in range(rep.shape[1])]
    )
    return float(np.sqrt(4.0 / rep.shape[1] * np.sum((thetas - est) ** 2)))


def money_text(x: float) -> str:
    return f"{x:.2f}"


@dataclass(frozen=True)
class PumaCommute:
    puma_code: str
    n_workers: int
    n_commuters: int
    wfh_share: str
    median_oneway: float | None
    p25: float | None
    p75: float | None
    supported: bool


def commute_by_puma(persons: pd.DataFrame, min_n: int) -> dict[str, PumaCommute]:
    out: dict[str, PumaCommute] = {}
    persons = persons.assign(puma_code=persons["STATE"] + persons["PUMA"])
    for code, g in persons.groupby("puma_code"):
        w_all = g["PWGTP"].to_numpy(float)
        wfh = (g["JWTRNS"] == 11).to_numpy()
        commuters = g[(g["JWMNP"] > 0) & (g["JWTRNS"] != 11)]
        n_c = len(commuters)
        share = float(w_all[wfh].sum() / w_all.sum()) if w_all.sum() > 0 else 0.0
        if n_c >= min_n:
            vals = commuters["JWMNP"].to_numpy(float)
            ws = commuters["PWGTP"].to_numpy(float)
            med, p25, p75 = (wquantile(vals, ws, q) for q in (0.5, 0.25, 0.75))
        else:
            med = p25 = p75 = None
        out[str(code)] = PumaCommute(
            puma_code=str(code),
            n_workers=len(g),
            n_commuters=n_c,
            wfh_share=f"{share:.4f}",
            median_oneway=med,
            p25=p25,
            p75=p75,
            supported=n_c >= min_n,
        )
    return out


def commute_by_tier(
    persons: pd.DataFrame, tiers: dict[str, str], min_n: int
) -> dict[str, PumaCommute]:
    """通勤层的单程通勤分布（层里所有 PUMA 的通勤者合并），给 A 网格用。"""
    p = persons.assign(puma_code=persons["STATE"] + persons["PUMA"])
    p = p[p["puma_code"].map(tiers).notna()].assign(puma_code=lambda x: x["puma_code"].map(tiers))
    return commute_by_puma(p.assign(STATE="", PUMA=p["puma_code"]), min_n)


def assign_tiers(
    commute: dict[str, PumaCommute], renter_weight: dict[str, float]
) -> dict[str, str]:
    """通勤三分位层：PUMA 按居民单程通勤中位数从短到长排，累计租住户权重的前三分之一为 core、中间为 middle、其余为 outer。
    层是都会区内部的相对位置（“通勤较短的居住区”），不是“市中心”。通勤资料不足的 PUMA 不分层。"""
    ranked = sorted(
        (c for c in commute.values() if c.supported and c.puma_code in renter_weight),
        key=lambda c: (c.median_oneway, c.puma_code),
    )
    total = sum(renter_weight[c.puma_code] for c in ranked)
    tiers: dict[str, str] = {}
    acc = 0.0
    for c in ranked:
        mid = acc + renter_weight[c.puma_code] / 2
        tiers[c.puma_code] = (
            "core" if mid <= total / 3 else ("middle" if mid <= 2 * total / 3 else "outer")
        )
        acc += renter_weight[c.puma_code]
    return tiers


@dataclass(frozen=True)
class RentCell:
    """一格条件分布的估计。``quantiles`` 的键是分位数（文字，例如 ``0.5``、``1/6``），值是月租（2024 年当地价，文字）或
    ``None``（落在顶码上，只知道不低于顶码值）。"""

    table_id: str
    cbsa: str
    level: str
    key: tuple[str, ...]
    population: str
    n: int
    weight_sum: int
    n_eff: float
    quantiles: dict[str, str | None]
    se_median: float | None
    censored_share: float
    structure_mix: dict[str, str]
    utilities_median: str | None
    record_ids: tuple[str, ...] = field(repr=False, default=())

    @property
    def supported(self) -> bool:
        return self.n > 0


def _quantile_keys(qs: list[str]) -> list[tuple[str, float]]:
    return [(q, float(Fraction(q))) for q in qs]


def estimate_cell(
    g: pd.DataFrame,
    table_id: str,
    cbsa: str,
    level: str,
    key: tuple[str, ...],
    population: str,
    qs: list[str],
    rep_cols: list[str],
) -> RentCell:
    vals = g["rent_contract"].to_numpy(float)
    w = g["WGTP"].to_numpy(float)
    topcoded = g["topcoded"].to_numpy(bool)
    cw_sorted = None
    quantiles: dict[str, str | None] = {}
    order = np.argsort(vals, kind="stable")
    cw_sorted = np.cumsum(w[order]) / w.sum()
    top_from = None
    if topcoded.any():
        first_top = np.argmax(topcoded[order])
        top_from = cw_sorted[first_top - 1] if first_top > 0 else 0.0
    for label, q in _quantile_keys(qs):
        v = wquantile(vals, w, q)
        quantiles[label] = None if (top_from is not None and q > top_from) else money_text(v)
    se = (
        replicate_se(vals, g[rep_cols].to_numpy(float), w, 0.5) if rep_cols and len(g) > 1 else None
    )
    mix = g.groupby("struct_class")["WGTP"].sum()
    mix_share = {k: f"{v / mix.sum():.4f}" for k, v in sorted(mix.items())}
    util = (g["rent_gross"] - g["rent_contract"]).where(g["rent_gross"].notna())
    util_ok = util.notna() & (util >= 0)
    util_med = (
        money_text(wquantile(util[util_ok].to_numpy(float), w[util_ok.to_numpy()], 0.5))
        if util_ok.sum() >= 1
        else None
    )
    return RentCell(
        table_id=table_id,
        cbsa=cbsa,
        level=level,
        key=key,
        population=population,
        n=len(g),
        weight_sum=int(w.sum()),
        n_eff=float(w.sum() ** 2 / (w**2).sum()),
        quantiles=quantiles,
        se_median=se,
        censored_share=float(w[topcoded].sum() / w.sum()),
        structure_mix=mix_share,
        utilities_median=util_med,
        record_ids=tuple(sorted(g["record_id"])),
    )


@dataclass
class RentModel:
    """一个都会区的条件租金表（所有层级）与回退查询。"""

    cbsa: str
    min_n: int
    cells: dict[tuple[str, tuple[str, ...]], RentCell]
    tiers: dict[str, str]

    def get(self, level: str, key: tuple[str, ...]) -> RentCell | None:
        return self.cells.get((level, key))

    def resolve(
        self, levels: list[tuple[str, tuple[str, ...]]]
    ) -> tuple[RentCell | None, list[str]]:
        """按顺序找第一格样本数达标的；返回（那一格，试过但不够的层级）。"""
        tried: list[str] = []
        for level, key in levels:
            cell = self.cells.get((level, key))
            if cell is not None and cell.n >= self.min_n:
                return cell, tried
            tried.append(level)
        return None, tried


#: 候选价位：每一格租金分布三等分区间的中点。它是 ``housing_rent_conditional@1`` 与 ``housing_coverage_grid@1`` 定义的一部分
#: （每个候选代表这一格三分之一的目标人群）；要改价位就升方法版本，不只改配置。
PRICE_POSITIONS = ("1/6", "1/2", "5/6")


def rent_model(df: pd.DataFrame, cbsa: str, tiers: dict[str, str], cfg: dict) -> RentModel:
    """估计一个都会区所有层级的条件租金格。只用完整在内的 PUMA 的样本（部分在内的 PUMA 在覆盖报告里单列）。"""
    min_n = int(cfg["min_unweighted_n"])
    qs = list(dict.fromkeys([*cfg["quantiles"], *PRICE_POSITIONS]))
    rep_cols = [c for c in df.columns if c.startswith("WGTP") and c[4:].isdigit()]
    rep_cols = rep_cols[: int(cfg.get("replicate_weights", 80))]
    d = df[df["full_puma"]].copy()
    d["tier"] = d["puma_code"].map(tiers)
    market = d[d["segment"].isin(MARKET)]
    cells: dict[tuple[str, tuple[str, ...]], RentCell] = {}

    def add(frame: pd.DataFrame, level: str, keys: list[str], population: str) -> None:
        for kvals, g in frame.groupby(keys, dropna=True):
            kv = tuple(str(x) for x in (kvals if isinstance(kvals, tuple) else (kvals,)))
            tid = f"b.housing.{cbsa}.{level}." + ".".join(kv)
            cells[(level, kv)] = estimate_cell(g, tid, cbsa, level, kv, population, qs, rep_cols)

    add(market, "puma_bed", ["puma_code", "bed_class"], "new_market_proxy")
    add(
        market.dropna(subset=["tier"]),
        "tier_bed_struct",
        ["tier", "bed_class", "struct_class"],
        "new_market_proxy",
    )
    add(market.dropna(subset=["tier"]), "tier_bed", ["tier", "bed_class"], "new_market_proxy")
    add(market, "metro_bed", ["bed_class"], "new_market_proxy")
    add(d[d["segment"] != "vacant_for_rent"], "metro_bed_stock", ["bed_class"], "renter_stock_all")
    return RentModel(cbsa=cbsa, min_n=min_n, cells=cells, tiers=tiers)


def stock_median_gross_by_bed(df: pd.DataFrame) -> dict[str, float]:
    """验证用：存量租住户（不含空置）的毛租金加权中位数，按卧室数（与 ACS 发布表 B25031 同口径：付现金租金的租住户）。"""
    occ = df[(df["segment"] != "vacant_for_rent") & df["rent_gross"].notna()]
    out = {}
    bed = occ["BDSP"].astype(int).clip(upper=5)
    for b, g in occ.groupby(bed):
        out[str(b)] = wquantile(g["rent_gross"].to_numpy(float), g["WGTP"].to_numpy(float), 0.5)
    out["all"] = wquantile(occ["rent_gross"].to_numpy(float), occ["WGTP"].to_numpy(float), 0.5)
    return out


@dataclass(frozen=True)
class JobGroupHours:
    cbsa: str
    soc_major: str
    level: str
    n: int
    median_weekly_hours: float | None
    wfh_share: str


def job_hours(
    persons: pd.DataFrame, cbsa: str, pooled: pd.DataFrame, min_n: int
) -> dict[str, JobGroupHours]:
    """按 SOC 大类（SOCP 前两位）：通常每周工时加权中位数与上周在家工作比例；样本不足回退到六个都会区合并。"""
    out: dict[str, JobGroupHours] = {}

    def summarize(g: pd.DataFrame) -> tuple[float | None, str]:
        h = g[g["WKHP"] > 0]
        med = (
            wquantile(h["WKHP"].to_numpy(float), h["PWGTP"].to_numpy(float), 0.5)
            if len(h)
            else None
        )
        w = g["PWGTP"].to_numpy(float)
        share = float(w[(g["JWTRNS"] == 11).to_numpy()].sum() / w.sum()) if w.sum() > 0 else 0.0
        return med, f"{share:.4f}"

    major = persons["SOCP"].astype(str).str[:2]
    pooled_major = pooled["SOCP"].astype(str).str[:2]
    for m in sorted(set(major) | set(pooled_major)):
        g = persons[major == m]
        if len(g) >= min_n:
            med, share = summarize(g)
            out[m] = JobGroupHours(cbsa, m, "metro_major", len(g), med, share)
        else:
            gp = pooled[pooled_major == m]
            if len(gp) >= min_n:
                med, share = summarize(gp)
                out[m] = JobGroupHours(cbsa, m, "pooled_major", len(gp), med, share)
    return out
