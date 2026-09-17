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

from dataclasses import dataclass, field, replace

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


# ---------------------------------------------------------------------------
# 舒适度的标度：绝对锚定优先，分位兜底（§5.3）
# ---------------------------------------------------------------------------
#
# §5.3 说"能用绝对的就用绝对"，而我们此前一个绝对锚定都没实现——六维全是
# 分位等价物（高斯 copula 给均匀边际）。补上它有两个收益，第二个比第一个
# 更要紧：
#
# 一、跨国可比。绝对锚定的变量不需要参照总体：µg/m³ 在哪儿都是 µg/m³，
#    分钟在哪儿都是分钟。§5.3 明写这一点，而 §5.8 的跨国分级正靠它。
#
# 二、**不双重计入强度。** 分位法把每一维都拉伸填满 [0,1]，无论那一维在
#    物理上到底变化多少。于是"人有多在乎这一维"被烤进了标度，而 θ 又在
#    问卷里量了一遍同一件事。绝对标度把强度整个赶进 θ——标度只说"物理上
#    差多少"，θ 说"这个人多在乎"。这正是分工该有的样子。
#
# 第二条同时解释了 10.8×：分位法把 ln A 的跨城 sd 抻到 0.855，而工资是
# 0.186、房租是 0.229。锚定之后（PM2.5 给 0.282、日照 0.180）三者同量级，
# 不是靠挑一个 floor 凑出来的。
#
# **只锚定真有外生标准的。** 治安、医疗、机会、休闲没有——"多少犯罪率算
# 好"没有世卫指导值那样的东西，硬给一个参照点就是把任意标度换个说法。
# 它们继续走分位，但分位的离散度改成**显式参数**（见 quantile_ln_sd），
# 不再是 (0,1) 均匀映射的意外副产品。


@dataclass(frozen=True)
class AbsoluteAnchor:
    """一个外生标准，加上把物理量映到 [0,1] 的变换。

    ``reference`` 必须是外生的：世卫指导值、天文上限、物理零点。若它是
    "样本里最好的那个"或者"我们觉得合理的数"，那就不是绝对锚定，是换了
    个名字的分位法。
    """

    variable: str
    reference: float
    source: str
    kind: str  # "ratio_to_standard" | "share_of_budget"

    def scale(self, raw: float) -> float:
        if self.kind == "ratio_to_standard":
            # 越小越好的量（污染物浓度）：达到或优于标准记 1，之后按比例衰减。
            return float(min(1.0, self.reference / max(raw, 1e-12)))
        if self.kind == "share_of_budget":
            # 越小越好的量，且有一个外生的总量（清醒时间）。
            return float(max(0.0, 1.0 - raw / self.reference))
        raise ValueError(f"未知的锚定形式 {self.kind!r}")


# 每天清醒分钟数。用于把通勤时长变成"没被通勤吃掉的清醒时间占比"。
# 两端都外生：零通勤是理想，一整天是上限。
WAKING_MINUTES_PER_DAY = 960.0

AMENITY_ANCHORS: dict[str, AbsoluteAnchor] = {
    # 年均 PM2.5，µg/m³。世卫 2021 年空气质量指导值的年均值是 5。
    "environment": AbsoluteAnchor(
        variable="pm25_annual_ug_m3",
        reference=5.0,
        source="WHO Global Air Quality Guidelines 2021, annual PM2.5",
        kind="ratio_to_standard",
    ),
    # 单程通勤分钟数，换算成往返占清醒时间的比例。
    #
    # 注意这一维锚定之后的 ln A 离散度只有 0.008——按清醒时间占比算，美国
    # 都会区之间的通勤差异**在物理上确实很小**。若人对通勤的厌恶远超这个
    # 比例，那应当体现在 θ（他在量表上给通勤多少分），不在标度里。这正是
    # 绝对锚定该有的效果：强度归 θ，物理差异归标度。
    "commute": AbsoluteAnchor(
        variable="commute_minutes_one_way",
        reference=WAKING_MINUTES_PER_DAY / 2.0,
        source="往返通勤占清醒时间的比例；零通勤与整日两端均外生",
        kind="share_of_budget",
    ),
}

# 没有外生标准的四维，走分位。
QUANTILE_DIMENSIONS: tuple[str, ...] = tuple(
    d for d in AMENITY_DIMENSIONS if d not in AMENITY_ANCHORS
)


# 锚定维度的物理量边际分布。参数取自美国都会区的实际范围，是**标定**，
# 与锚定标准本身分开：标准是外生的（世卫指导值、清醒时间），分布是我们
# 对"美国都会区之间差多少"的经验描述，M1 拿到真实数据后替换。
PHYSICAL_MARGINALS: dict[str, tuple[float, float, float, float]] = {
    # (对数均值 或 均值, 尺度, 下截断, 上截断)
    "environment": (8.0, 0.30, 3.0, 20.0),  # PM2.5 µg/m³，对数正态
    "commute": (26.0, 4.0, 15.0, 45.0),  # 单程分钟，正态
}


def _physical_value(dim: str, z: float) -> float:
    """把隐变量映到物理量。**符号翻转**：z 大表示宜居因子高，而 PM2.5 与
    通勤时长都是"越小越好"，所以用 −z。

    这个负号是锚定维度与分位维度唯一的实现差异，也是最容易写反的一处：
    写正了不会报错，只会让空气差的城市看起来宜居。有测试盯着它。
    """
    import math

    loc, scale, lo, hi = PHYSICAL_MARGINALS[dim]
    # PM2.5 走对数正态（浓度是正的、右偏），通勤时长走正态。
    lognormal = dim == "environment"
    raw = math.exp(math.log(loc) + scale * (-z)) if lognormal else loc + scale * (-z)
    return min(hi, max(lo, raw))


def scale_amenity(dim: str, z: float, quantile_ln_sd: float) -> float:
    """隐变量 → [0,1] 的标度值。锚定维走物理量，其余走显式离散度的对数正态。

    分位维用 `A = exp(σ·(z − z_cap))`，`z_cap = 3`：
      · `z ≤ 3` 时 `A ≤ 1`，满足 §5.3 的值域；
      · `ln A` 的跨城标准差**恰好是 σ**，因此那是一个可以直接指定、直接
        扫描的参数，而不是 (0,1) 均匀映射的意外副产品（那个副产品是 0.855，
        而工资是 0.186、房租是 0.229）。
    """
    import math

    anchor = AMENITY_ANCHORS.get(dim)
    if anchor is not None:
        return anchor.scale(_physical_value(dim, z))
    return math.exp(quantile_ln_sd * (min(z, 3.0) - 3.0))


def commute_minutes_for_world(z_commute, calibration) -> np.ndarray:
    """一个世界里全部城市的**城市层面代表性单程通勤分钟**。

    关闭长通勤臂（默认）时，它逐城退化为 ``_physical_value("commute", z)``，
    与旧实现逐位一致——旧配置因此仍然完全可复现。

    --------------------------------------------------------------------
    为什么这条规则必须看整个世界，而不能放进 ``scale_amenity``
    --------------------------------------------------------------------

    长通勤档是按**世界内排序**指派的：要保证同一道选择题里同时出现短通勤和
    长通勤城市。逐城独立抽样做不到这一点——它只会把不同世界的整体通勤水平
    抬高抬低，而条件 logit 看不见世界之间的差异，只看得见选择集**内部**的
    差异（`DECISIONS/0011` 第四节）。

    --------------------------------------------------------------------
    指派规则：同一个隐变量，换一个更宽的单调映射
    --------------------------------------------------------------------

    取 ``z_commute`` 最低的 k 座（原映射下通勤最长的那几座）升到长通勤档，
    最低的拿最长的那一档。于是 ``分钟数`` 仍然是 ``z_commute`` 的**非增函数**，
    因子结构（通勤与宜居因子的相关）因此原样保留——它只是同一个隐变量的另一
    个单调变换，不是新加的一层随机性。

    这一条同时挡住两个陷阱：

      · **不是按真值权重指派。** 世界在人设之前生成，这里只看世界自己的隐
        变量，两条轴保持正交（§11.9）。
      · **不是"短通勤城市在别的维度上全面占优"。** 每个舒适度维在因子模型里
        都带自己的特异噪声，所以长通勤城市在治安、医疗等维度上有高有低，
        权衡是真的权衡。

    **长通勤档是人为设定的情境水平，不代表现实发生频率。** 口径没有变：仍然
    是城市层面的代表性单程通勤分钟，不是某个人的通勤。60 分钟接近美国都会区
    的现实上沿，90 与 120 已经超出，它们的作用是把这一维的组内变异撑开到可
    识别，而不是描述美国。
    """
    import numpy as np

    z = np.asarray(z_commute, dtype=float)
    base = np.array([_physical_value("commute", float(v)) for v in z])

    levels = tuple(float(v) for v in calibration.commute_long_minutes)
    k = min(int(calibration.commute_long_cities), len(base))
    if not levels or k <= 0:
        return base

    ceiling = PHYSICAL_MARGINALS["commute"][3]
    if min(levels) <= ceiling:
        raise ValueError(
            f"长通勤档 {levels} 必须全部高于短通勤段的上截断 {ceiling}，"
            "否则分钟数不再是 z_commute 的单调函数，因子结构会被打乱"
        )

    ordered = np.argsort(z, kind="stable")  # z 升序：通勤最长的在前
    longest_first = sorted(levels, reverse=True)
    out = base.copy()
    for rank in range(k):
        out[ordered[rank]] = longest_first[rank * len(longest_first) // k]
    return out


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

    # --- 分位维度的 ln A 跨城标准差。**显式参数，不是映射的副产品。** ---
    #
    # 默认 0.22 不是挑出来凑比值的：它落在两个锚定维实测给出的区间里
    # （PM2.5 0.282、日照 0.180），也落在经济变量的量级上（ln wage 0.186、
    # ln rpp_rent 0.229）。所以它表达的假设是"没有外生标准的舒适度，其
    # 跨城变异与有标准的那些差不多大"——一个写下来的、可检验的假设，
    # 而不是 (0,1) 均匀映射的意外结果（那个结果是 0.855）。
    #
    # 研究模式下它是**扫描参数**：舒适度与物质两个通道的汇率没人知道，
    # 正确的反应不是挑一个，是扫过去、把结论报成它的函数。
    quantile_ln_sd: float = 0.22

    # 税制与宜居因子的相关。现实里高税辖区提供更多公共服务（六个舒适度维
    # 里五个主要靠税收支撑），而此前税制是独立随机分配的，实测
    # corr(税负, 舒适度) = +0.020——世界里根本不存在"高税高服务"的城市，
    # 第四档因此测不到最现实的那种货币幻觉：只看到手工资，看不见税买了什么。
    #
    # **这是国别参数。** 同一国内部它应当明显为正；跨国合并则不一定，因为
    # 国家能力与经济实力不同，高税不一定换来高服务。所以它属于 CountryPack
    # 的标定，不是一个全球常数（§5.8 的分级同理）。
    tax_amenity_corr: float = 0.55

    # --- 长通勤实验臂（默认关闭，关闭时与旧实现逐位一致）---
    #
    # `DECISIONS/0011` 第四节：绝对锚定之后 commute 的 ln A 绝对跨度只有
    # 0.0667，因此**任何**选择集的标准差上界是 0.0334，而其余五维中位是
    # 0.2088——这一维的权重在数据上不可识别，且这个上界来自 A = 1 − t/480
    # 与截断 [15, 45] 本身，选世界、重试都够不着。
    #
    # 这里不动 `A(t) = 1 − t/480`，只在世界里**加入长通勤城市**：把 t 的取值
    # 范围从"美国都会区经验区间"扩到包含人为设定的长通勤情境。见
    # `commute_minutes_for_world` 的指派规则与口径说明。
    #
    # 空元组 / 零座 = 关闭 = 旧基线。
    commute_long_minutes: tuple[float, ...] = ()
    commute_long_cities: int = 0

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

#: 长通勤实验臂。**显式启用**：``WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)``。
#:
#: 12 座城市里 3 座走长通勤档（每档一座：60 / 90 / 120 分钟单程），其余 9 座
#: 仍走原来的 15–45 分钟经验区间。三座的选取由 ``commute_long_cities`` 与
#: ``commute_minutes_for_world`` 的排序规则决定，与人设真值权重无关。
#:
#: 3 是最小值：三个档位各一座，既保证同一道题里短长通勤同时在场，又尽量少
#: 改动世界。要更强的对比就调 ``commute_long_cities``，规则会按档位均分。
LONG_COMMUTE_ARM_2024 = replace(
    PROVISIONAL_US_2024,
    commute_long_minutes=(60.0, 90.0, 120.0),
    commute_long_cities=3,
)
"""V1 的标定。**手工设定，不是从数据估的。**

文件名与常量名都带 provisional，是为了让任何一个读到这里的人（包括三个月
后的自己）知道这些数还没有被真实数据支持过。M1 落地后新增一个从 OEWS 与
RPP 估出来的实例，把这个留在原地作对照。
"""


#: 按名字取标定。配置文件里写名字、产出里记名字，比记一个对象的 repr 稳。
#: **换名字就是换实验臂**，因此 P8 的设计指纹与门一的配置哈希都要吃到它。
CALIBRATIONS: dict[str, AttributeCalibration] = {
    "LONG_COMMUTE_ARM_2024": LONG_COMMUTE_ARM_2024,
    "PROVISIONAL_US_2024": PROVISIONAL_US_2024,
}

#: 没写就是它——与 2026-09-11 跑过的门一逐位一致的那一个。
DEFAULT_CALIBRATION_NAME = "PROVISIONAL_US_2024"
