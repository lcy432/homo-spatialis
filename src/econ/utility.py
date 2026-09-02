"""M2 的纯数学层。对国家、对城市、对原始数据列一无所知。

架构规则一的落点：本模块的函数只吃**已经标准化好的标量**——净收入、价格
指数、标度到 [0,1] 的舒适度值、以及一组指数。它们不知道这些数是从 OEWS
还是从别国统计局来的，不知道对应哪座城市，甚至不知道现在是哪一年。

规则二的落点：本模块的任何函数都**不接受 UtilityGraph 参数**。单次计入
记的是"哪个原始列进了哪个项"，而原始列只在装配层（econ.core 的 _income /
_price_index / _amenities）被读取。纯函数这一层已经没有列了，只有数，
在这里记账等于记流水账。

--------------------------------------------------------------------------
数学，一次写清楚
--------------------------------------------------------------------------

直接效用（README §7.1）：

    U = C^α · H^β · Π_k A_k^{θ_k},     α + β + Σ_k θ_k = 1

其中只有 C 与 H 是花钱买的，A_k 不可购买。所以预算约束里只有 α 与 β 这
两个指数参与分配，而它们的和是 w₁ = α + β < 1。一阶条件给出：

    C* = [α/(α+β)] · Y / p_C
    H* = [β/(α+β)] · Y / p_H

**分母是 (α+β) 而不是 1。** 这是本模块最容易写错的一行。写成 αY/p_C 会让
(1−w₁)·Y 这部分钱凭空消失——预算恒等式 p_C·C* + p_H·H* = Y 当场不成立，
而且错误是静默的：效用排序看上去仍然合理，但 §7.5 的储蓄轨迹会被污染，
因为"没花掉的钱"被错误地当成了储蓄。有一条断言专门盯着这个。

代回直接效用，得到间接效用：

    V = κ · (Y / P)^{w₁} · Π_k A_k^{θ_k}
    κ = (α/w₁)^α · (β/w₁)^β

而 P 正是 §5.2 那个三分项加权几何平均。代数上：

    p_C^α · p_H^β = P^{w₁}    当 α = w₁·(s_goods+s_other), β = w₁·s_rent

这就是 §7.2 两通道映射与 §5.2 价格指数严丝合缝的地方，也是住房问题的
答案：**房租只在 P 里出现一次，住房数量 H 被最优化掉了**，效用函数里
根本没有一个独立的住房项可以跟它重复计入。

κ 只依赖 (α, β)，也就是只依赖 persona，**跨城市恒定**。它在排序里会
约掉，所以排序测试发现不了 κ 算错；但等价变差是水平量的比较，κ 一旦
跨城市漂移，EV 就全错。有一条断言专门盯着这个。

--------------------------------------------------------------------------
对数空间
--------------------------------------------------------------------------

    ln V = ln κ + w₁·(ln Y − ln P) + Σ_k θ_k · ln A_k

而 ln κ 本身也可以化成 w₁ 的线性函数：把 α = w₁·s_C、β = w₁·s_rent 代入，

    ln κ = w₁ · (s_C·ln s_C + s_rent·ln s_rent)

括号里只依赖份额，是 persona 常数。于是 **ln V 对参数向量
(w₁, θ_1..θ_6) 是线性的**，这正是 §11.7 的条件 logit 要求的设定。这条
不是形式检查：它若不成立，logit 就是误设定的，回收出来的"权重"不对应
任何东西。有一条断言用数值方法验证它。

全程在对数空间计算还有一个务实的理由：P 是幂的连乘，直接算会在极端份额
下损失精度；而 ln P = Σ s_k·ln(rpp_k) 是一个加权和，数值上稳得多。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from econ.types import PersonaParams

# 特征列的命名约定。M5 按这些名字取列，改名等于改接口。
FEATURE_LN_Y = "ln_y_net"
FEATURE_LN_P = "ln_p"
AMENITY_FEATURE_PREFIX = "ln_a_"

# 舒适度标度值下界。A_k = 0 会让 ln A_k = -inf，整个效用塌成零。
# §5.3 的标度函数值域是 [0,1] 的闭区间，所以 0 是可能取到的，必须夹住。
# 这个下界是一个建模选择，不是数值 hack：它等价于说"最差的城市在这一维上
# 也不是绝对的零"，并且它的大小会影响 argmax，所以写在这里而不是藏在某个
# 计算的中间。
MIN_AMENITY = 1e-6

_TOL = 1e-9


def amenity_feature_name(dimension: str) -> str:
    return f"{AMENITY_FEATURE_PREFIX}{dimension}"


@dataclass(frozen=True, slots=True)
class UtilityDecomposition:
    """ln V 的完整分解。这是 M2 交给 M5 的东西，M5 不再自己算一遍。

    恒等式（由 __post_init__ 断言）：

        ln_utility == ln_kappa + Σ_col coefficients[col] · features[col]

    ``ln_kappa`` **不放进 features**，虽然它确实是 ln V 的一部分。原因是
    它跨城市恒定：在一个人的备选集里它是一列常数，放进设计矩阵会造成
    零方差列，直接把条件 logit 的设计矩阵搞成秩亏。它在 logit 里本来
    就会被差分掉，所以正确的做法是把它单独拎出来记账，而不是塞进列里。

    ``coefficients`` 存的是**真值系数**：ln_y_net 上是 +w₁，ln_p 上是
    −w₁，各 ln_a_k 上是 θ_k。M5 估出来的 ŵ 与这里的 w 直接相减就是回收
    误差，两者在同一尺度上，不需要任何换算。
    """

    ln_kappa: float
    features: Mapping[str, float]
    coefficients: Mapping[str, float]
    ln_utility: float

    def __post_init__(self) -> None:
        if set(self.features) != set(self.coefficients):
            raise ValueError(
                f"features 与 coefficients 的列必须一一对应："
                f"{sorted(set(self.features) ^ set(self.coefficients))} 只出现在一侧"
            )
        recomposed = self.ln_kappa + sum(
            self.coefficients[k] * self.features[k] for k in self.features
        )
        if not math.isclose(recomposed, self.ln_utility, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(
                f"分解不自洽：ln_kappa + Σβx = {recomposed!r} 但 ln_utility = {self.ln_utility!r}"
            )

    @property
    def utility(self) -> float:
        return math.exp(self.ln_utility)

    @property
    def money_illusion_residual(self) -> float:
        """|coef_Y + coef_P|。真值下恒为零，因为两者等值反号。

        这个量对真值没有意义（它就是 0），它的用处在 M5：把估出来的系数
        代进来，得到的就是货币幻觉的连续度量。§11.3 第四档因此从"选对了
        没有"升级成一个标量——模型有多大程度上在跟着名义工资走。
        """
        return abs(self.coefficients[FEATURE_LN_Y] + self.coefficients[FEATURE_LN_P])


def ln_kappa(params: PersonaParams) -> float:
    """κ 的对数。只依赖 persona，跨城市恒定。

    κ = (α/w₁)^α · (β/w₁)^β，其中 α/w₁ 与 β/w₁ 就是市场篮子内部的两个
    支出份额。约定 0·ln0 = 0（极限值），因此某一维权重为零时不会出现
    -inf；§6.3 的下限（每维至少 3 分）本就是为了避免这种情况，这里只是
    不让它炸掉。
    """
    w1 = params.w_material
    if w1 <= 0.0:
        raise ValueError("w₁ = α + β 必须为正：没有物质维度的人无法定义预算问题")
    out = 0.0
    for exponent in (params.alpha, params.beta):
        if exponent > 0.0:
            out += exponent * math.log(exponent / w1)
    return out


def budget_allocation(
    y_net: float, p_c: float, p_h: float, params: PersonaParams
) -> tuple[float, float]:
    """最优的非住房消费量与住房服务量。

    注意分母是 w₁ = α + β，不是 1。这是本模块最容易写错的一行，见模块
    文档字符串。返回 (C*, H*)，两者都是**实物量**，不是支出额。
    """
    if y_net <= 0.0:
        raise ValueError(f"净收入必须为正，得到 {y_net!r}")
    if p_c <= 0.0 or p_h <= 0.0:
        raise ValueError(f"价格必须为正，得到 p_c={p_c!r}, p_h={p_h!r}")
    w1 = params.w_material
    c_star = (params.alpha / w1) * y_net / p_c
    h_star = (params.beta / w1) * y_net / p_h
    return c_star, h_star


def ln_indirect_utility(
    ln_y_net: float,
    ln_p: float,
    ln_a: Mapping[str, float],
    params: PersonaParams,
) -> UtilityDecomposition:
    """ln V 及其分解。这是 M2 的中心函数。

    参数全部是对数：``ln_y_net`` 是税后、扣除储蓄后的消费预算的对数，
    ``ln_p`` 是个人化价格指数的对数（由装配层按 Σ s_k·ln rpp_k 算好），
    ``ln_a`` 是各维标度值的对数。

    ln κ 被吸收进常数项而不是某一列，理由见 UtilityDecomposition 的文档。
    但注意 ln κ 仍然含有 w₁ 因子，所以它随参数变化——这正是 ln V 对参数
    保持线性所需要的：ln κ = w₁·c(s)，c(s) 是 persona 常数。
    """
    missing = set(params.theta) - set(ln_a)
    if missing:
        raise ValueError(f"缺少舒适度维度 {sorted(missing)} 的标度值")

    w1 = params.w_material
    features: dict[str, float] = {FEATURE_LN_Y: ln_y_net, FEATURE_LN_P: ln_p}
    coefficients: dict[str, float] = {FEATURE_LN_Y: w1, FEATURE_LN_P: -w1}

    for dim, theta in params.theta.items():
        features[amenity_feature_name(dim)] = ln_a[dim]
        coefficients[amenity_feature_name(dim)] = theta

    k = ln_kappa(params)
    total = k + sum(coefficients[col] * features[col] for col in features)
    return UtilityDecomposition(
        ln_kappa=k, features=features, coefficients=coefficients, ln_utility=total
    )


def indirect_utility(
    y_net: float, p: float, a: Mapping[str, float], params: PersonaParams
) -> float:
    """水平空间的间接效用 V。

    这是架构规则一点名的那个签名。它是 ln_indirect_utility 的薄封装：
    真正的计算全在对数空间做，这里只取一次 exp。反过来写（水平空间算，
    需要时取 log）会在极端份额下丢精度，而且会让分解与效用值有两条独立
    的计算路径，也就有两个可以分叉的地方。
    """
    if p <= 0.0:
        raise ValueError(f"价格指数必须为正，得到 {p!r}")
    ln_a = {k: math.log(max(v, MIN_AMENITY)) for k, v in a.items()}
    return ln_indirect_utility(math.log(y_net), math.log(p), ln_a, params).utility


def solve_ln_amenity_for_tie(
    target_ln_utility: float,
    ln_y_net: float,
    ln_p: float,
    ln_a: Mapping[str, float],
    params: PersonaParams,
    dimension: str,
) -> float:
    """反解某一维的 ln A_k，使这座城市的效用**精确等于** target。

    §11.3 第三档要的就是这个。在对数空间里它是一次代数变形，不是数值
    求根——ln V 对每个 ln A_k 都是线性的，所以：

        ln A_k = [ln V* − ln κ − w₁(ln Y − ln P) − Σ_{j≠k} θ_j·ln A_j] / θ_k

    残差因此是浮点噪声（1e-15 量级），不是求解器容差。这一点是 §11.6
    σ 无关性的前提：并列必须真的是并列，"约等于"不够——近似并列时参考熵
    会随 σ 在几乎整个 [0, ln n] 区间上摆动。

    选择反解舒适度而不是反解工资，是因为工资那条路要反转累进税函数
    （净收入 → 毛收入）。分段线性的税表是可以逐段反转的，但那不再是
    "一次代数变形"，而且会把税制细节渗进世界生成器。留到需要时再说。
    """
    theta_k = params.theta.get(dimension)
    if theta_k is None:
        raise ValueError(f"{dimension!r} 不是这个 persona 的舒适度维度")
    if theta_k <= 0.0:
        raise ValueError(
            f"{dimension!r} 的权重为零，这一维对该 persona 不影响效用，无法用它构造并列"
        )
    others = sum(params.theta[d] * ln_a[d] for d in params.theta if d != dimension)
    material = params.w_material * (ln_y_net - ln_p)
    return (target_ln_utility - ln_kappa(params) - material - others) / theta_k
