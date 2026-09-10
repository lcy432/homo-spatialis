"""世界生成器：抽出一批虚构城市，检查它是否可用，然后冻结。

"可用"不是"生成成功"。一个世界可以完全合法、数值正常，却对研究一文不
值——如果城市属性高度共线，反解出来的权重会极不稳定，而**那种不稳定是
世界的缺陷，不是模型的缺陷**（§10.3）。不检查就跑实验，会得到一堆方差
巨大的回收误差，然后误以为是模型不行。

所以生成器有一道硬验收：条件数。它算的就是 M5 将来实际看到的那个设计
矩阵——直接取 M2 的 GroundTruth.features，不另起一套特征表示，理由与
§11.6 要求并列在特征空间也精确是同一条：两套表示迟早会分叉。

--------------------------------------------------------------------------
两处随机化（§10.2）
--------------------------------------------------------------------------

**标签随机化**在这里。属性抽出来之后，哪一组属性挂到 City 1、哪一组挂到
City 7，每个世界实例重新洗一次，置换记录在 World.label_permutation 里。
不做这件事，跑一批世界之后模型可以学到"City 1 通常便宜"这种批次内相关，
而那与偏好推断毫无关系。

**排列随机化**不在这里，它在代理层：每个代理看到的城市顺序单独打乱。
两者是不同的东西——标签随机化防跨世界的标签-属性相关，排列随机化防
"总选第一个"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from common.ids import CityId, Jurisdiction
from econ.kernel import GrowthRates
from econ.types import CityRecord
from worlds.attributes import (
    LATENT_ORDER,
    QUANTILES,
    AttributeCalibration,
    scale_amenity,
)
from worlds.dimensions import AMENITY_DIMENSIONS
from worlds.taxes import StateRegime, SyntheticProgressiveTax

# 合成城市用的地理方案。虚构，且明显虚构——不复用 CBSA，免得有人把它
# 当成真实都会区编码去 join 真实数据。
SYNTH_SCHEME = "SYNTH"

# 州的名字也用中性标签。用真实州名会把真实税制的联想带进来。
SYNTH_STATES: tuple[str, ...] = ("S1", "S2", "S3", "S4")

STATE_REGIMES: dict[str, StateRegime] = {
    "S1": StateRegime.NONE,
    "S2": StateRegime.FLAT,
    "S3": StateRegime.PROGRESSIVE,
    "S4": StateRegime.PROGRESSIVE_LOCAL,
}


class WorldRejectedError(RuntimeError):
    """生成出的世界没通过验收。带上原因，因为调用方要据此调参数而不是重试。"""


@dataclass(frozen=True)
class World:
    """一个冻结的世界实例。

    ``label_permutation`` 记录属性向量被挂到哪个标签上。它跟人设的
    dimension_order 一样，记录不是为了隐藏，是为了让"标签里没有信息"
    这件事可被检验。
    """

    world_id: str
    seed: int
    cities: dict[str, CityRecord]
    growth: dict[str, GrowthRates]
    label_permutation: tuple[int, ...]
    condition_number: float
    calibration: AttributeCalibration

    @property
    def city_ids(self) -> tuple[CityId, ...]:
        return tuple(rec.city for rec in self.cities.values())

    def __len__(self) -> int:
        return len(self.cities)


def _sample_latent(
    rng: np.random.Generator, calibration: AttributeCalibration, n_cities: int
) -> np.ndarray:
    """按相关矩阵抽多元标准正态。行是城市，列按 LATENT_ORDER。"""
    corr = calibration.correlation_matrix()
    chol = np.linalg.cholesky(corr)
    z = rng.standard_normal((n_cities, len(LATENT_ORDER)))
    return z @ chol.T


def _standard_normal_cdf(x: np.ndarray) -> np.ndarray:
    """Φ(x)。用 erf 而不是引 scipy，这一处不值得多一个依赖。"""
    from math import erf, sqrt

    return np.vectorize(lambda v: 0.5 * (1.0 + erf(v / sqrt(2.0))))(x)


def _assign_states(
    latent: np.ndarray, calibration: AttributeCalibration, rng: np.random.Generator
) -> list[str]:
    """按"税负与宜居正相关"分配州税制原型。

    现实里六个舒适度维中有五个（治安、医疗、机会、休闲、通勤）主要靠税收
    与公共转移支付支撑，所以高税辖区通常提供更多公共服务。此前州制度是
    **独立随机**分配的，实测 corr(税负, 舒适度) = +0.020——世界里根本不
    存在"高税高服务"的城市。后果不只是不现实：

      · 装置连表达"税收换来的服务是否值那些税"这个机制的能力都没有；
      · §11.3 第四档（货币幻觉）因此只能测"高名义工资但房租贵"，测不到
        最现实的那一种——**只看到手工资，看不见税买了什么**。

    做法是把宜居因子与一个独立噪声按 ``tax_amenity_corr`` 混合成一个排序
    分数，再按分数的分位把四种税制原型（无州税、平税、累进、累进加地方
    附加）铺上去。相关强度可调，因为它是**国别参数**：同一国内部应当明显
    为正，跨国合并则不一定——国家能力与经济实力不同，高税不必然换来高服务。

    **这不泄露任何东西。** 税率与舒适度都是 L0/L1 可观测量，代理本来就
    看得见两者；改变的只是它们在世界里的联合分布。真值仍然只在 score()
    那一侧，AgentInput 的边界一动没动。
    """
    idx = {name: i for i, name in enumerate(LATENT_ORDER)}
    amenity_score = latent[:, [idx[d] for d in AMENITY_DIMENSIONS]].mean(axis=1)
    rho = float(np.clip(calibration.tax_amenity_corr, -1.0, 1.0))
    noise = rng.standard_normal(len(amenity_score))
    blended = rho * amenity_score + np.sqrt(max(0.0, 1.0 - rho**2)) * noise

    order = np.argsort(blended)  # 分数低 -> 低税
    states = [""] * len(order)
    for rank, city in enumerate(order):
        bucket = min(len(SYNTH_STATES) - 1, rank * len(SYNTH_STATES) // len(order))
        states[city] = SYNTH_STATES[bucket]
    return states


def _build_records(
    latent: np.ndarray,
    calibration: AttributeCalibration,
    states: list[str],
) -> list[CityRecord]:
    """把隐变量映射到各自的边际分布，装成 CityRecord。

    价格与工资走对数正态（乘性、正数、右偏），舒适度走 Φ 得到 [0,1] 上
    均匀分布的标度值——正好是 §5.3 要求的值域，且不需要再选一个分布形状。
    """
    col = {name: i for i, name in enumerate(LATENT_ORDER)}
    records: list[CityRecord] = []

    for i in range(latent.shape[0]):
        z = latent[i]
        rpp = {
            "rent": float(np.exp(z[col["rpp_rent"]] * calibration.ln_rent_sd)),
            "goods": float(np.exp(z[col["rpp_goods"]] * calibration.ln_goods_sd)),
            "other": float(np.exp(z[col["rpp_other"]] * calibration.ln_other_sd)),
        }
        median = calibration.median_wage * float(np.exp(z[col["wage"]] * calibration.ln_wage_sd))
        wages = {q: median * calibration.quantile_ratios[q] for q in QUANTILES}

        # 绝对锚定优先、分位兜底（§5.3）。见 attributes.scale_amenity：
        # 锚定维走物理量与外生标准，分位维走显式离散度而不是 (0,1) 均匀。
        amenities = {
            d: float(
                np.clip(scale_amenity(d, float(z[col[d]]), calibration.quantile_ln_sd), 1e-6, 1.0)
            )
            for d in AMENITY_DIMENSIONS
        }

        records.append(
            CityRecord(
                city=CityId("US", SYNTH_SCHEME, f"C{i + 1}"),
                rpp=rpp,
                wage_by_quantile=wages,
                amenities_scaled=amenities,
                jurisdiction=Jurisdiction(("US", states[i])),
            )
        )
    return records


def design_matrix(core, persona, cities: list[CityId]) -> np.ndarray:
    """M5 将来实际看到的设计矩阵：行是备选城市，列是 ln V 的特征。

    直接取 M2 的 GroundTruth.features，不另起一套特征表示。理由与 §11.6
    要求"并列在特征空间也精确"是同一条：两套表示迟早分叉，分叉之后回收
    误差这个核心因变量就没有意义了。

    **列要按备选集中心化**。条件 logit 差分掉了备选集内的公共部分，所以
    决定可识别性的是中心化之后的矩阵。不中心化会把一个共同的大截距算进
    条件数里，得到一个虚高的、与实际估计无关的数。
    """
    truths = [core.score(persona, c, 2024) for c in cities]
    columns = sorted(truths[0].features)
    m = np.array([[t.features[c] for c in columns] for t in truths], dtype=float)
    return m - m.mean(axis=0, keepdims=True)


def condition_number(core, persona, cities: list[CityId]) -> float:
    """中心化设计矩阵的条件数（最大奇异值除以最小）。

    数值越大越病态。属性高度共线时，"模型在意房租"与"模型在意治安"
    在数据上分不开，反解出的权重方差巨大——而这与模型的缺陷无关。
    """
    m = design_matrix(core, persona, cities)
    # 中心化恰好消耗一个秩：n×k 的中心化矩阵秩至多 n−1。所以列满秩要求
    # n_cities − 1 ≥ n_features，也就是城市数至少比特征数多一。少于这个
    # 数时不是"病态"而是"不可识别"，返回 inf 而不是一个大数。
    if m.shape[0] - 1 < m.shape[1]:
        return float("inf")

    # **列必须先标准化到单位长度。** 条件数大于 30 意味着共线性严重，这条
    # 经验法则（Belsley-Kuh-Welsch）针对的是列已经等尺度化的矩阵。不做这
    # 一步，条件数量的主要是**列的尺度差异**而不是共线性：这里 ln_a 的跨
    # 城波动在 3 的量级（舒适度均匀分布于 (0,1)，取对数后左尾很长），而
    # ln_p 只有 0.07 的量级，光这个比值就能把条件数推到几百，与属性之间
    # 相不相关毫无关系。
    #
    # 这个错误的危险之处在于它是**保守方向**的：它让好世界看起来病态，
    # 于是你会去调一组本来没问题的相关系数，或者把城市数堆到没必要的多。
    norms = np.linalg.norm(m, axis=0)
    if np.any(norms <= 0):
        return float("inf")  # 某一列在备选集内完全没有变异
    m = m / norms

    sv = np.linalg.svd(m, compute_uv=False)
    return float("inf") if sv[-1] <= 0 else float(sv[0] / sv[-1])


class WorldGenerator:
    """按配置抽样、验收、冻结。

    ``max_condition_number`` 是硬验收线。30 是 Belsley-Kuh-Welsch 的经验
    阈值：列等尺度化之后条件数超过约 30，共线性已经严重到影响系数估计。
    它可配，因为放宽它是一个**要被看见的决定**，不该藏在某个默认值里。

    ``n_cities`` 的默认值 12 是量出来的，不是拍的。用参照人设各抽 30 个
    世界，阈值 30：

        城市数   条件数中位数   30 次里最差   通过率
          9         36.3          367.5       13/30    （下界，贴着不可识别）
         10         14.8           79.5       25/30
         12          8.3           17.5       30/30    ← 默认
         16          5.5            9.1       30/30
         20          4.9            9.9       30/30

    9 是数学下界（特征数加一），但贴着下界时最小奇异值紧邻零，条件数会
    炸。12 是第一个稳定全过的规模，再往上收益递减而 prompt 变长——输入
    token 在这类实验里是主导成本（§11.9）。
    """

    def __init__(
        self,
        calibration: AttributeCalibration | None = None,
        *,
        n_cities: int = 12,
        max_condition_number: float = 30.0,
        max_attempts: int = 200,
    ) -> None:
        from worlds.attributes import PROVISIONAL_US_2024
        from worlds.dimensions import N_AMENITIES

        # 特征列 = ln_y_net + ln_p + 每个舒适度维一列。
        n_features = 2 + N_AMENITIES
        minimum = n_features + 1
        if n_cities < minimum:
            raise ValueError(
                f"城市数 {n_cities} 少于 {minimum}。设计矩阵有 {n_features} 列，"
                "而按备选集中心化之后秩至多是城市数减一，所以城市数必须比特征数多一，"
                "否则不是病态而是**不可识别**。默认的 16 留了余量：刚好取到下界时"
                "最小奇异值贴着零，条件数仍然会大到没法用。"
            )

        self.calibration = calibration or PROVISIONAL_US_2024
        self.n_cities = n_cities
        self.max_condition_number = max_condition_number
        self.max_attempts = max_attempts

    def tax(self) -> SyntheticProgressiveTax:
        return SyntheticProgressiveTax(regimes=dict(STATE_REGIMES))

    def _attempt(self, seed: int, world_id: str) -> World:
        rng = np.random.default_rng(seed)
        latent = _sample_latent(rng, self.calibration, self.n_cities)

        # 标签随机化（§10.2）：属性向量与标签的挂接每个世界重洗一次。
        permutation = rng.permutation(self.n_cities)
        latent = latent[permutation]

        states = _assign_states(latent, self.calibration, rng)
        records = _build_records(latent, self.calibration, states)

        growth = {
            rec.city.code: GrowthRates(
                real_wage=float(
                    rng.normal(
                        self.calibration.real_wage_growth_mean,
                        self.calibration.real_wage_growth_sd,
                    )
                ),
                real_rent=float(
                    rng.normal(
                        self.calibration.real_rent_growth_mean,
                        self.calibration.real_rent_growth_sd,
                    )
                ),
                real_interest=self.calibration.real_interest,
            )
            for rec in records
        }

        return World(
            world_id=world_id,
            seed=seed,
            cities={rec.city.code: rec for rec in records},
            growth=growth,
            label_permutation=tuple(int(i) for i in permutation),
            condition_number=float("nan"),  # 由 generate 填，需要一个参照人设
            calibration=self.calibration,
        )

    def generate(self, seed: int, reference_persona, *, world_id: str | None = None) -> World:
        """抽样直到通过条件数验收。

        用一个**参照人设**来算条件数，因为设计矩阵依赖份额（ln P 是三个
        rpp 的份额加权）与能力分位（决定工资）。参照人设应当取份额与分位
        都居中的那种；边缘人设的条件数会更差，所以这道闸是必要非充分的。
        """
        from econ.core import WelfareCore

        last: float | None = None
        for attempt in range(self.max_attempts):
            wid = world_id or f"w{seed:06d}"
            world = self._attempt(seed + attempt * 1_000_003, wid)
            core = WelfareCore(
                world.cities,
                self.tax(),
                numeraire="USD_2024_US100",
                reference_id="SYNTH_2024",
            )
            cond = condition_number(core, reference_persona, list(world.city_ids))
            last = cond
            if cond <= self.max_condition_number:
                return World(
                    world_id=wid,
                    seed=world.seed,
                    cities=world.cities,
                    growth=world.growth,
                    label_permutation=world.label_permutation,
                    condition_number=cond,
                    calibration=self.calibration,
                )

        raise WorldRejectedError(
            f"{self.max_attempts} 次抽样都没能把条件数压到 {self.max_condition_number} 以下"
            f"（最后一次 {last:.1f}）。这通常不是运气问题：要么城市数太少"
            f"（现在 {self.n_cities}，特征数是 2 + 舒适度维数），要么标定里的相关系数"
            "设得太强。调参数，不要靠重试。"
        )

    def generate_many(self, n: int, base_seed: int, reference_persona) -> list[World]:
        """一批相互独立的世界。人设投放到哪个世界由调用方决定，两轴正交。"""
        return [
            self.generate(base_seed + i, reference_persona, world_id=f"w{base_seed + i:06d}")
            for i in range(n)
        ]
