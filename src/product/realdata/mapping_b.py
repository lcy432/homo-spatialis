"""B 映射：由真实资料估计的条件关系。

**住房（``housing_rent_conditional@2``）**：在一个都会区里，按 卧室数 × 地点（PUMA 或通勤三分位层）× 楼型 估计月租的条件分布。

* 目标人群是“新租市场的代理”：空置待租房的要价 ∪ 过去 12 个月内迁入的租住户的合同租金（ACS 住房权重 WGTP 加权）。居民存量
  租金不直接当市场价：只有在市场代理样本不足时作为最后一层回退，并标明人群换了。
* 加权分位数取“累计权重第一次达到 q × 总权重”的那个**真实观测值**（阶梯函数，不插值），所以每个分位数都是某条样本的实际租金。
* 标准误用 ACS 的连续差分重复权重（WGTP1–WGTP80）：SE = √(4/80 × Σ(θ_r − θ)²)，θ 与 θ_r 用同一个估计方法。中位数成对报告：
  阶梯中位数 + 阶梯法的重复权重标准误（候选价格用阶梯法），插值中位数 + 插值法的重复权重标准误；两对不混写。
* 每格要求未加权样本数 ≥ ``min_unweighted_n``（默认 30）；不够就按层级回退：(PUMA, 卧室) → (通勤层, 卧室, 楼型) → (通勤层, 卧室)
  → (都会区, 卧室) → (都会区, 卧室, 存量人群) → 不支持。用了哪一层记在机会里，不悄悄抹掉。
* 顶码按官方州别阈值与替代值识别（``acs_official_topcodes@1``）：顶码记录的真实值只知道高于本州阈值，替代值不是下界的近似。
  分位数只有不高于格内顶码记录的最低阈值才可识别，否则不填数；格里没有顶码记录时，最大值就是普通观测值，照常使用。
* 房间数不是面积：ACS 没有面积，面积一律缺失，不从卧室数推。

**通勤（``commute_by_residence_puma@1``）**：按居住地 PUMA，在职且不在家工作的人（JWMNP > 0）单程通勤分钟的加权分布（PWGTP），
以及在家工作的比例。这是居民去**各自**工作地点的时间，不是去某个就业中心的时间。

**岗位工时（``job_hours_wfh_by_occupation_group@1``）**：按 都会区 × SOC 大类，在职者通常每周工时的加权中位数与上周在家工作的
比例。在职者不是新招聘岗位；在家工作比例不是某个岗位的工作安排。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd

from common.intake_contracts import AssumptionSpec, MethodSpec

HOUSING_METHOD = MethodSpec(
    method_id="housing_rent_conditional",
    version="2",
    family="mapping_b",
    summary_zh=(
        "按 卧室数 × 地点 × 楼型 估计月租（合同租金或要价）的条件分布：新租市场代理人群（空置待租 ∪ 12 个月内迁入）、WGTP 加权、"
        "阶梯加权分位数（取真实观测值）、中位数与重复权重标准误按同一估计方法成对（阶梯 / 插值）、官方州别顶码阈值、样本不足按层级回退。"
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
SUPPORT_METHOD = MethodSpec(
    method_id="housing_rent_support",
    version="1",
    family="mapping_b",
    summary_zh=(
        "每个条件格保存完整的加权经验支持点：同一条原始记录的合同租金、毛租金、卧室、房间、楼型、建成年代与厨卫条件一起保留，完全"
        "相同的特征合并（权重相加、保留来源记录）；回退格只借上层条件分布的形状，总权重仍取目标 PUMA × 卧室格；合法、未受顶码影响的"
        "毛租金同记录使用，缺毛租金的记录按本格条件水电中位数插补（供体合法、未顶码），没有供体保留未知；顶码记录保留权重与阈值、标为"
        "删失；品质三档 = 本格合同租金加权分布里的三分位位置。"
    ),
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=(
        "支持点是调查记录的报告值，不是房源广告；新租市场代理的偏差照旧（见 housing_rent_conditional@2）",
        "回退格的特征来自上层（通勤层或全都会区）的记录，位置换成目标 PUMA",
        "插补的水电燃料费是本格供体的中位数，不是这套房的真实费用",
    ),
)
UTILITIES_IMPUTATION = AssumptionSpec(
    assumption_id="housing_utilities_conditional_median_v1",
    kind="assumption",
    statement_zh=(
        "没有毛租金的记录（空置待租的要价、少数缺毛租金的租住户）：毛租金 = 合同租金 + 同一条件格里供体（合法、未受顶码影响、毛租金与"
        "合同租金都有的租住户）水电燃料月费的加权中位数；格里没有供体就保留未知，不补零。记录供体格与供体数。"
    ),
    applies_to=("housing_rent_support@1",),
    why_not_measured_zh="空置房只有要价，ACS 不问空置房的水电燃料费。",
)
INTERNET_METHOD = MethodSpec(
    method_id="household_internet_access",
    version="1",
    family="mapping_b",
    summary_zh=(
        "按都会区（完整在内的 PUMA）的租住户（WGTP 加权）：ACS 上网方式（ACCESSINET）、宽带（HISPEED）、手机数据套餐（BROADBND）、"
        "智能手机（SMARTPHONE）、电话服务（TEL）的分布。不适用（b）与 2024 年部分 PUMA 的抑制值（8）不进分母，单独计数。"
    ),
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=(
        "只用于描述与核对：接入率不是运营商可得率，不能据此让一部分分身买不到网络",
        "HISPEED、BROADBND 只问付费上网的住户，分母与 ACCESSINET 不同",
    ),
)
MARKET_PROXY = AssumptionSpec(
    assumption_id="housing_new_market_proxy",
    kind="exploratory_proxy",
    statement_zh="空置待租房的要价与过去 12 个月内迁入者的合同租金，近似一个新来的人在这座城会看到的租金分布。",
    applies_to=("housing_rent_conditional@2", "housing_rent_support@1", "housing_coverage_grid@1"),
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


def step_quantile(
    values: np.ndarray, weights: np.ndarray, q: float, limit: float = math.inf
) -> float | None:
    """有顶码时的阶梯分位数：按报告值算（顶码记录排在替代值处），结果不高于格内顶码记录真实值下界的最小值 ``limit`` 才可识别——
    这时所有顶码记录的真实值都在它上面，累计权重不受影响；否则 None。没有顶码记录时 ``limit`` 是无穷。"""
    v = wquantile(values, weights, q)
    return v if v <= limit else None


def interpolated_quantile(
    values: np.ndarray, weights: np.ndarray, q: float, limit: float = math.inf
) -> float | None:
    """有顶码时的插值分位数。插值区间的上端是这个报告值与**下一个**报告值的中点，所以除了结果不高于 ``limit``，下一个报告值也要
    不高于 ``limit``（是已知值）：否则一条顶码记录的真实值可能正好落在中间，改变区间与累计权重，结果不可识别。"""
    v = wquantile_smooth(values, weights, q)
    if math.isinf(limit):
        return v
    if v > limit:
        return None
    uniq, inv = np.unique(values, return_inverse=True)
    wsum = np.bincount(inv, weights=weights)
    cdf = np.concatenate([[0.0], np.cumsum(wsum) / wsum.sum()])
    j = max(int(np.searchsorted(cdf, q, side="left")) - 1, 0)
    if j + 1 >= len(uniq) or uniq[j + 1] > limit:
        return None
    return v


def replicate_se(
    values: np.ndarray,
    rep: np.ndarray,
    weights: np.ndarray,
    q: float,
    estimator=None,
    limit: float = math.inf,
) -> float | None:
    """ACS 连续差分重复权重的标准误：√(4/R × Σ(θ_r − θ)²)，R 是重复权重个数（ACS 是 80）。θ 与 θ_r 必须用**同一个**估计方法
    （``estimator``：``step_quantile`` 或 ``interpolated_quantile``，默认插值）——点估计与标准误成对，不混用。
    主估计或任何一个重复估计不可识别（顶码）就返回 ``None``，不给一个看似精确的标准误。"""
    est_fn = estimator or interpolated_quantile
    est = est_fn(values, weights, q, limit)
    thetas = [est_fn(values, np.clip(rep[:, r], 0, None), q, limit) for r in range(rep.shape[1])]
    if est is None or any(t is None for t in thetas):
        return None
    diffs = np.array(thetas, dtype=float) - est
    return float(np.sqrt(4.0 / rep.shape[1] * np.sum(diffs**2)))


def money_text(x: float) -> str:
    return f"{x:.2f}"


def step_quantile_text(
    values: np.ndarray, weights: np.ndarray, q: float, limit: float
) -> str | None:
    v = step_quantile(values, weights, q, limit)
    return None if v is None else money_text(v)


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
    #: 中位数的两种估计，各自配自己的重复权重标准误（不混写）：阶梯法 = 累计权重第一次过半的真实观测值（候选价格用它）；
    #: 插值法 = 把每个报告值当成到相邻值中点的均匀区间后线性插值。落在顶码下界以上（不可识别）时为 None。
    median_step: float | None
    se_median_step: float | None
    median_interpolated: float | None
    se_median_interpolated: float | None
    censored_share: float
    #: 格内顶码（或是否顶码未知）记录的真实值下界的最小值；分位数只有不高于它才可识别。没有这类记录时为 None。
    censor_bound_min: float | None
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
    censored = g["censored"].to_numpy(bool)
    limit = (
        float(np.nanmin(g["censor_bound"].to_numpy(float)[censored]))
        if censored.any()
        else math.inf
    )
    quantiles: dict[str, str | None] = {}
    for label, q in _quantile_keys(qs):
        quantiles[label] = step_quantile_text(vals, w, q, limit)
    m_step = step_quantile(vals, w, 0.5, limit)
    m_interp = interpolated_quantile(vals, w, 0.5, limit)
    rep = g[rep_cols].to_numpy(float) if rep_cols and len(g) > 1 else None

    def se_of(estimator) -> float | None:
        return None if rep is None else replicate_se(vals, rep, w, 0.5, estimator, limit)

    mix = g.groupby("struct_class")["WGTP"].sum()
    mix_share = {k: f"{v / mix.sum():.4f}" for k, v in sorted(mix.items())}
    util = (g["rent_gross"] - g["rent_contract"]).where(g["rent_gross"].notna())
    util_ok = util.notna() & (util >= 0) & ~g["censored"]
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
        median_step=m_step,
        se_median_step=se_of(step_quantile),
        median_interpolated=None if m_interp is None else round(m_interp, 2),
        se_median_interpolated=se_of(interpolated_quantile),
        censored_share=float(w[censored].sum() / w.sum()),
        censor_bound_min=None if math.isinf(limit) else limit,
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


#: A 覆盖网格（``housing_coverage_grid@1``）的价位：每一格租金分布三等分区间的中点。只给 A 网格用；B 的候选是完整的加权经验
#: 支持点（``housing_rent_support@1``），不再压成三个价位。要改价位就升 A 网格的方法版本，不只改配置。
GRID_POSITIONS = ("1/6", "1/2", "5/6")


def rent_model(df: pd.DataFrame, cbsa: str, tiers: dict[str, str], cfg: dict) -> RentModel:
    """估计一个都会区所有层级的条件租金格。只用完整在内的 PUMA 的样本（部分在内的 PUMA 在覆盖报告里单列）。"""
    min_n = int(cfg["min_unweighted_n"])
    qs = list(dict.fromkeys([*cfg["quantiles"], *GRID_POSITIONS]))
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


#: B 住房候选的回退顺序（``housing_rent_support@1``）：PUMA × 卧室 → 通勤层 × 卧室 → 都会区 × 卧室 → 存量 × 卧室。
SUPPORT_LEVELS = ("puma_bed", "tier_bed", "metro_bed", "metro_bed_stock")
PRICE_STATUS_ORDER = {"observed": 0, "imputed": 1, "censored": 2, "unknown": 3}


def quality_tiers(contract: np.ndarray, censored: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """每条记录在本格合同租金加权分布里的三分位位置（1、2、3）：并列的记录取同一个位置（并列组累计权重的中点）；顶码记录的真实
    值只知道高于阈值，排在最上面。这是旧的 1/6、1/2、5/6 三个价位的推广：每条记录落在哪个三分之一，就是哪一档。"""
    key = np.where(censored, np.inf, contract)
    order = np.argsort(key, kind="stable")
    total = float(weights.sum())
    tiers = np.empty(len(key), dtype=int)
    i, cum = 0, 0.0
    while i < len(order):
        j = i
        while j + 1 < len(order) and key[order[j + 1]] == key[order[i]]:
            j += 1
        grp = order[i : j + 1]
        gw = float(weights[grp].sum())
        mid = (cum + gw / 2) / total if total > 0 else 0.5
        tiers[grp] = 1 if mid < 1 / 3 else (2 if mid < 2 / 3 else 3)
        cum += gw
        i = j + 1
    return tiers


def support_table_id(cell: RentCell) -> str:
    return f"b.housing_support.{cell.cbsa}.{cell.level}." + ".".join(cell.key)


def _num(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def support_table(cell: RentCell, rows: pd.DataFrame) -> dict[str, Any]:
    """一格的完整加权经验支持点（``housing_rent_support@1``）。``rows`` 是这一格的全部样本记录（与 ``cell.record_ids`` 相同）。

    价格状态：``observed`` = 租住户合法、未受顶码影响的毛租金（同记录的 GRNTP）；``imputed`` = 没有毛租金（空置待租的要价、缺
    毛租金的租住户）：合同租金 + 本格供体水电燃料月费的加权中位数；``censored`` = 合同租金或水电燃料顶码，只知道下界；``unknown``
    = 要插补而格里没有供体。完全相同的特征（价格状态、毛租金、合同租金、下界、卧室、房间、楼型、建成年代、厨卫、品质档）合并。"""
    w = rows["WGTP"].to_numpy(float)
    renter = (rows["segment"] != "vacant_for_rent").to_numpy(bool)
    censored = rows["censored"].to_numpy(bool)
    contract = rows["rent_contract"].to_numpy(float)
    gross = pd.to_numeric(rows["rent_gross"], errors="coerce").to_numpy(float)
    affected = rows["gross_affected_by"].notna().to_numpy(bool)
    observed = renter & ~censored & ~affected & ~np.isnan(gross) & (gross > 0) & (gross >= contract)
    util = gross - contract
    util_med = wquantile(util[observed], w[observed], 0.5) if observed.any() else None
    tiers = quality_tiers(contract, censored, w)
    groups: dict[tuple, dict[str, Any]] = {}
    for i, r in enumerate(rows.itertuples(index=False)):
        if censored[i] or (renter[i] and affected[i]):
            status, price, cval = "censored", None, None if censored[i] else contract[i]
        elif observed[i]:
            status, price, cval = "observed", round(float(gross[i]), 2), contract[i]
        elif util_med is not None:
            status, price, cval = "imputed", round(float(contract[i] + util_med), 2), contract[i]
        else:
            status, price, cval = "unknown", None, contract[i]
        bound = _num(r.censor_bound) if censored[i] else None
        kit = {1: "complete", 2: "incomplete"}.get(int(_num(r.KIT) or 0), "unknown")
        plm = {1: "complete", 2: "incomplete"}.get(int(_num(r.PLM) or 0), "unknown")
        yrblt = _num(r.YRBLT)
        key = (
            status,
            price,
            None if cval is None else round(float(cval), 2),
            bound,
            int(r.BDSP),
            int(r.RMSP),
            str(r.BLD).zfill(2),
            None if yrblt is None else int(yrblt),
            kit,
            plm,
            int(tiers[i]),
            r.gross_affected_by if status == "censored" and not censored[i] else None,
        )
        g = groups.setdefault(key, {"weight": 0.0, "records": []})
        g["weight"] += float(w[i])
        g["records"].append(r.record_id)

    def order(k: tuple) -> tuple:
        return (PRICE_STATUS_ORDER[k[0]], -1 if k[1] is None else k[1], *[str(x) for x in k[2:]])

    points = []
    for n, k in enumerate(sorted(groups, key=order), start=1):
        g = groups[k]
        points.append(
            {
                "id": f"s{n:04d}",
                "weight": round(g["weight"]),
                "price_status": k[0],
                "gross_rent": None if k[1] is None else money_text(k[1]),
                "contract_rent": None if k[2] is None else money_text(k[2]),
                "censor_bound": k[3],
                "censored_by": "RNTP" if k[0] == "censored" and k[11] is None else k[11],
                "bedrooms": k[4],
                "rooms": k[5],
                "structure": k[6],
                "year_built": k[7],
                "kitchen": k[8],
                "plumbing": k[9],
                "quality_tier": k[10],
                "records": sorted(g["records"]),
            }
        )
    total = round(float(w.sum()))

    def mass(pred) -> int:
        return sum(p["weight"] for p in points if pred(p))

    complete = lambda p: p["kitchen"] == "complete" and p["plumbing"] == "complete"  # noqa: E731
    return {
        "table_id": support_table_id(cell),
        "method": SUPPORT_METHOD.ref,
        "cbsa": cell.cbsa,
        "level": cell.level,
        "key": list(cell.key),
        "population": cell.population,
        "cell_table": cell.table_id,
        "n": cell.n,
        "n_eff": round(cell.n_eff, 2),
        "weight_sum": total,
        "donors": {
            "n": int(observed.sum()),
            "utilities_median_month": None if util_med is None else money_text(util_med),
            "assumption": UTILITIES_IMPUTATION.assumption_id,
        },
        "masses": {
            "raw": total,
            "observed": mass(lambda p: p["price_status"] == "observed"),
            "imputed": mass(lambda p: p["price_status"] == "imputed"),
            "censored": mass(lambda p: p["price_status"] == "censored"),
            "unknown": mass(lambda p: p["price_status"] == "unknown"),
            "facilities_complete": mass(complete),
            "facilities_incomplete": mass(lambda p: "incomplete" in (p["kitchen"], p["plumbing"])),
            "facilities_unknown": mass(
                lambda p: not complete(p) and "incomplete" not in (p["kitchen"], p["plumbing"])
            ),
            "executable": mass(
                lambda p: p["price_status"] in ("observed", "imputed") and complete(p)
            ),
        },
        "points": points,
    }


INTERNET_VARIABLES = {
    "ACCESSINET": {1: "paid", 2: "without_paying", 3: "no_access"},
    "HISPEED": {1: "yes", 2: "no"},
    "BROADBND": {1: "yes", 2: "no"},
    "SMARTPHONE": {1: "yes", 2: "no"},
    "TEL": {1: "yes", 2: "no"},
}


def internet_access(df: pd.DataFrame, cbsa: str) -> dict[str, Any]:
    """都会区（完整在内的 PUMA）租住户的上网与通信设备分布（``household_internet_access@1``）。抑制值 8 与不适用（空值）不进分母，
    单独计数；不是运营商可得率。"""
    occ = df[df["full_puma"] & (df["segment"] != "vacant_for_rent")]
    w = occ["WGTP"].to_numpy(float)
    out: dict[str, Any] = {
        "table_id": f"b.internet.{cbsa}",
        "method": INTERNET_METHOD.ref,
        "cbsa": cbsa,
        "population": "renter_occupied_full_pumas",
        "n": len(occ),
        "weight_sum": int(w.sum()),
        "variables": {},
    }
    for var, labels in INTERNET_VARIABLES.items():
        col = pd.to_numeric(occ[var], errors="coerce") if var in occ else pd.Series(dtype=float)
        if col.empty:
            out["variables"][var] = {"status": "not_read"}
            continue
        codes = col.to_numpy(float)
        valid = np.isin(codes, list(labels))
        valid_w = float(w[valid].sum())
        out["variables"][var] = {
            "weight_valid": int(valid_w),
            "weight_suppressed": int(w[codes == 8].sum()),
            "weight_not_applicable": int(w[np.isnan(codes)].sum()),
            "shares_of_valid": {
                lab: None if valid_w == 0 else round(float(w[codes == c].sum()) / valid_w, 4)
                for c, lab in labels.items()
            },
        }
    return out


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
