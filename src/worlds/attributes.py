"""合成城市的属性分布。M1 到位之前，这里是"真实世界长什么样"的唯一来源。

README §11.3：属性分布从真实 OEWS/RPP 标定，但实例化出的城市是虚构的。
这句话有两半，两半都要做到：

  标定的是**分布**。工资的离散度、租金的离散度、两者的相关性，都要像
  真实美国都会区。参数现在是手工设的（下面 PROVISIONAL_US_2024），M1
  拿到真实数据后换成估计值，接口不变。

  实例化出的是**虚构城市**。中性标签 City 1、City 7，不得写得像任何一座
  真实城市。否则测的是模型对真实城市的记忆，不是推理。

--------------------------------------------------------------------------
相关结构才是重点，不是边际分布
--------------------------------------------------------------------------

最容易做错的是把各个属性独立抽样。那样造出来的世界里会有"工资高、房租
低、什么都好"的城市，而现实中不存在这种地方——**因为空间均衡**。

Rosen-Roback 说：劳动力自由流动时，城市间的实际工资差异必须被舒适度差异
抵消。落到数据上就是三条相关：

    工资 ↔ 房租        强正相关。高工资城市房租也高，这是均衡条件本身。
    舒适度 ↔ 房租      正相关。好地方贵，这就是补偿性差异。
    舒适度 ↔ 工资      弱负相关。人们为了好地方接受低工资。

**第四档（对抗型）能不能存在，完全取决于第一条。** 如果工资和房租不相关，
那么名义工资最高的城市大概率也是实际福利最高的，货币幻觉就无从检验——
题目本身不成立。所以这个相关系数不是拟真的装饰，它是实验设计的一部分。

同时它也是**麻烦的来源**：属性越相关，设计矩阵越病态，反解出来的权重越
不稳定（§10.3）。而那种不稳定是世界的缺陷，不是模型的缺陷。所以相关强度
是一个要显式调、显式报告、并且要配合条件数检查的参数，不是拍脑袋填的。

--------------------------------------------------------------------------
抽样方法
--------------------------------------------------------------------------

高斯 copula：先按相关矩阵抽一个多元正态的隐变量，再把每一维分别映射到
它自己的边际分布。价格与工资走对数正态（乘性量，正数，右偏），舒适度走
标准正态的 CDF，得到 [0,1] 上均匀分布的标度值——正好是 §5.3 要求的值域。

这样相关结构与边际形状可以分开调，改一个不动另一个。

--------------------------------------------------------------------------
相关结构用因子模型给，不用两两手填
--------------------------------------------------------------------------

第一版是把相关系数两两填进矩阵的。它**不是半正定的**——有一个特征值是
-0.12，也就是说那组数不对应任何真实存在的联合分布。而且矛盾是有经济含义
的，不是填错了数：

    工资与房租 +0.75，舒适度与房租 +0.35
    ⟹ 舒适度与工资经由房租传递应当在 +0.26 左右
    但我们想要的是 -0.15（好地方工资低）

一个因子说明不了这件事。Roback (1982) 本来就需要**两个力**：

    生产率因子 f_prod   同时抬高工资和房租（集聚效应）
    宜居因子   f_amen   抬高房租、**压低**工资（补偿性差异）

补偿性差异的全部内容就是第二行那个负号：好地方的人愿意少拿钱。所以正确
的做法不是把两两相关调到勉强半正定，而是按这两个因子写载荷——相关系数
是载荷的乘积，**半正定由构造保证**，且每个参数都有经济解释。

代价是不能再直接指定"我要 wage-rent = 0.75"，只能通过载荷间接得到。这是
好事：能直接指定的那组数里，有一大半根本不存在。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from worlds.dimensions import AMENITY_DIMENSIONS

# 隐变量的顺序。相关矩阵按这个顺序排，改顺序等于改矩阵。
LATENT_ORDER: tuple[str, ...] = (
    "wage",
    "rpp_rent",
    "rpp_goods",
    "rpp_other",
    *AMENITY_DIMENSIONS,
)

# OEWS 的五个工资分位。§7.5 明确禁止一律取中位数。
QUANTILES: tuple[int, ...] = (10, 25, 50, 75, 90)


@dataclass(frozen=True)
class FactorLoadings:
    """Roback 两因子结构的载荷。相关矩阵由它生成，因此天然半正定。

    每个隐变量写成两个正交因子的线性组合加特异噪声，全部标准化到单位
    方差：

        wage      =  w_prod·f_prod − w_amen·f_amen + 噪声
        rpp_rent  =  r_prod·f_prod + r_amen·f_amen + 噪声
        rpp_other =  o_prod·f_prod + o_amen·f_amen + 噪声
        rpp_goods =  g_prod·f_prod + g_amen·f_amen + 噪声
        amenity_k =                  a_amen·f_amen + 噪声

    ``w_amen`` 前面的负号是硬编码的，不是可配的符号——它就是补偿性差异
    本身。要是哪天有人把它改成正的，Roback 结构就没了，第四档也跟着没了。

    载荷平方和必须小于一，否则特异噪声方差为负。有断言。
    """

    # 生产率因子上的载荷。工资与房租都高，这是集聚。
    w_prod: float = 0.90
    r_prod: float = 0.86
    o_prod: float = 0.78
    g_prod: float = 0.32  # 商品可贸易，几乎不随本地生产率走

    # 宜居因子上的载荷。房租正，工资负。
    w_amen: float = 0.20  # 会被取负号，见类文档
    r_amen: float = 0.42
    o_amen: float = 0.34
    g_amen: float = 0.08

    # 每个舒适度维在宜居因子上的载荷。平方即为舒适度之间的相关。
    a_amen: float = 0.45

    def __post_init__(self) -> None:
        checks = {
            "wage": self.w_prod**2 + self.w_amen**2,
            "rpp_rent": self.r_prod**2 + self.r_amen**2,
            "rpp_other": self.o_prod**2 + self.o_amen**2,
            "rpp_goods": self.g_prod**2 + self.g_amen**2,
            "amenity": self.a_amen**2,
        }
        for name, communality in checks.items():
            if communality >= 1.0:
                raise ValueError(
                    f"{name} 的载荷平方和为 {communality:.3f}，不小于一，特异噪声方差会是负的"
                )

    def loading_vector(self, name: str) -> tuple[float, float]:
        """(f_prod 载荷, f_amen 载荷)。"""
        if name == "wage":
            return self.w_prod, -self.w_amen  # 负号在此
        if name == "rpp_rent":
            return self.r_prod, self.r_amen
        if name == "rpp_other":
            return self.o_prod, self.o_amen
        if name == "rpp_goods":
            return self.g_prod, self.g_amen
        if name in AMENITY_DIMENSIONS:
            return 0.0, self.a_amen
        raise KeyError(f"未知的隐变量 {name!r}")


@dataclass(frozen=True)
class AttributeCalibration:
    """分布参数。**手工设定，M1 到位后替换为真实估计值。**

    每一个数下面都注了它的现实依据与量级理由。这不是文档洁癖：这些数
    直接决定真值 argmax，一个填错的离散度会让整档难度失去意义，而它不会
    报错。
    """

    # --- 价格。对数正态，全美均值 = 1（已在 ledger 归一，见 §5.2）---
    #
    # 租金离散度远大于商品，这是整个项目的核心机制（§5.2）：住房支出占
    # 40% 的人与占 20% 的人，面对同一组城市可以得到完全相反的价格排序。
    # 若把这两个 sd 设成相近，个人化价格指数就不再有区分力，项目的卖点
    # 当场消失。
    ln_rent_sd: float = 0.20  # 约当 rpp_rent 落在 0.70–1.45
    ln_goods_sd: float = 0.025  # 可贸易品，套利拉平，0.95–1.05
    ln_other_sd: float = 0.075  # 主要不可贸易，0.86–1.16

    # --- 工资。中位分位的对数正态 ---
    median_wage: float = 82_000.0
    ln_wage_sd: float = 0.18

    # 分位比。相对中位数的倍数，取自 OEWS 里专业职业的典型形状。
    quantile_ratios: dict[int, float] = field(
        default_factory=lambda: {10: 0.62, 25: 0.79, 50: 1.00, 75: 1.32, 90: 1.72}
    )

    # --- 三个实际增长率（§7.5，决定二：一律实际，不用名义）---
    real_wage_growth_mean: float = 0.010
    real_wage_growth_sd: float = 0.008
    real_rent_growth_mean: float = 0.012
    real_rent_growth_sd: float = 0.012
    real_interest: float = 0.015

    # --- 相关结构。见模块文档：因子模型，不是两两手填 ---
    loadings: FactorLoadings = field(default_factory=FactorLoadings)

    def correlation_matrix(self) -> np.ndarray:
        """由载荷生成。半正定由构造保证，不需要事后修补。"""
        names = LATENT_ORDER
        loadings = np.array([self.loadings.loading_vector(n) for n in names])
        m = loadings @ loadings.T
        np.fill_diagonal(m, 1.0)
        return m

    def implied_correlations(self) -> dict[str, float]:
        """几个关键相关的实现值。载荷是间接的，所以要能看见结果。"""
        m = self.correlation_matrix()
        idx = {n: i for i, n in enumerate(LATENT_ORDER)}
        a0, a1 = AMENITY_DIMENSIONS[0], AMENITY_DIMENSIONS[1]
        return {
            "wage~rent": float(m[idx["wage"], idx["rpp_rent"]]),
            "rent~other": float(m[idx["rpp_rent"], idx["rpp_other"]]),
            "rent~goods": float(m[idx["rpp_rent"], idx["rpp_goods"]]),
            "amenity~rent": float(m[idx[a0], idx["rpp_rent"]]),
            "amenity~wage": float(m[idx[a0], idx["wage"]]),
            "amenity~amenity": float(m[idx[a0], idx[a1]]),
        }


PROVISIONAL_US_2024 = AttributeCalibration()
"""V1 的标定。**手工设定，不是从数据估的。**

文件名与常量名都带 provisional，是为了让任何一个读到这里的人（包括三个月
后的自己）知道这些数还没有被真实数据支持过。M1 落地后新增一个从 OEWS 与
RPP 估出来的实例，把这个留在原地作对照。
"""
