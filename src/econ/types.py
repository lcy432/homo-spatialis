"""The type contract of the welfare model: what is visible, what is the answer.

CLAUDE.md hard rule 3 covers this package. These are declarations with no
arithmetic in them -- the utility function, the scale functions and the
transition kernel are M2's own first version and are deliberately absent. What
is here is the boundary of README §3.2, expressed so that the type system holds
it rather than anyone's memory.

Two parallel splits, for the same reason:

    Observable   / GroundTruth    what the environment computes / what is the answer
    PersonaBrief / Persona        what the agent reads          / what was injected

The second split matters as much as the first. README §11.5: if the persona text
states the weights, the task degenerates into reading comprehension. So the
numeric weights live in Persona, the prose lives in PersonaBrief, and only the
brief can reach a prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from common.ids import CityId, Jurisdiction, OccupationId
from common.money import Money, WelfareLevel
from common.providers import ExpenditureShares

_EXPONENT_TOL = 1e-9

# rpp 的合法量级。归一到全美=1 之后，合成世界的 rent 分项大约落在 0.6–1.6，
# 第三档求解器可能把某一维推到边缘，所以留余量。它守的是"口径搞错了"这种
# 量级错误（100 制的值典型 85–130，会立刻撞上界），不是"数值有点偏"。
RPP_MIN = 0.3
RPP_MAX = 3.0


class ObservableTier(Enum):
    """README §11.4. How much accounting the environment has already done."""

    O1 = "O1"  # L0 + L1: raw statistical yearbook
    O2 = "O2"  # plus L2: an assistant that can do the arithmetic
    O3 = "O3"  # plus representative-basket real income: a cost-of-living calculator


class LiteralnessTier(Enum):
    """README §11.5. How directly the persona text states its own weights."""

    IMPLICIT = "implicit"  # "I hate driving and cannot take winters"
    MIXED = "mixed"
    EXPLICIT = "explicit"  # "commute weight high, climate weight high"


class BoundaryViolationError(AssertionError):
    """Raised when an answer-side object is found somewhere agent-visible."""


@dataclass(frozen=True, slots=True)
class PersonaParams:
    """Cobb-Douglas exponents. Answer side.

    The mapping from the seven constant-sum weights to (alpha, beta, theta) is
    README §7.2's two-channel formula and belongs to M2, not here. This type only
    holds the result and enforces the constraint §7.1 puts on it.
    """

    alpha: float  # non-housing consumption
    beta: float  # housing
    theta: Mapping[str, float]  # amenity name -> exponent

    def __post_init__(self) -> None:
        if self.alpha < 0 or self.beta < 0:
            raise ValueError("alpha and beta must be non-negative")
        if any(v < 0 for v in self.theta.values()):
            raise ValueError("amenity exponents must be non-negative")
        total = self.alpha + self.beta + sum(self.theta.values())
        if abs(total - 1.0) > _EXPONENT_TOL:
            raise ValueError(
                f"exponents must sum to 1 (README §7.1 constant returns), got {total!r}"
            )

    @property
    def w_material(self) -> float:
        """The exponent on real income in the indirect form: w_1 = alpha + beta."""
        return self.alpha + self.beta

    @property
    def zero_exponents(self) -> tuple[str, ...]:
        """Dimensions a Cobb-Douglas would annihilate (README §6.3, the zero problem)."""
        names = [k for k, v in self.theta.items() if v == 0.0]
        if self.alpha == 0.0:
            names.append("alpha")
        if self.beta == 0.0:
            names.append("beta")
        return tuple(sorted(names))


@dataclass(frozen=True, slots=True)
class PersonaBrief:
    """代理唯一被允许读到的关于"人"的信息。

    ``dimension_order`` 记录七个维度在散文里的叙述顺序。它存在是为了堵一条
    数值扫描完全抓不到的泄漏通道：如果人设散文总是按权重从高到低铺陈维度，
    那么秩序信息已经泄漏了——模型只要读出叙述次序就能答对权衡档，一个数字
    都不用看。这与 §10.2 已有的城市排列随机化是同一手法：随机化，并记录
    置换，然后检验置换与真值之间没有系统关联（见 common.leakcheck 的
    narration_order_leakage）。

    注意这个字段本身是"可见"的：它就是散文的顺序，代理读散文时自然知道。
    记录它不是为了隐藏，而是为了让"顺序里没有信息"这件事可被检验。
    """

    persona_id: str
    literalness: LiteralnessTier
    narrative: str
    dimension_order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Persona:
    """环境所知道的"这个人"。答案侧，永不进入 prompt。

    ``shares`` 是 Persona 的字段，**不是 (persona, city) 的函数**，这一点是
    硬约束而非风格。若按各城市的税后收入去查 CEX 档位，份额会跨城市波动，
    随之三件事同时坏掉：

      (a) P 不再是价格指数。指数数论要求权重固定，权重随价格变动的加权
          几何平均不是任何东西的价格指数；
      (b) κ 跨城市变动，于是它不再能从排序里约掉，等价变差失效；
      (c) ln V 的系数随备选项变化，§11.7 的条件 logit 变成误设定——
          它假定同一个人在所有备选项上用同一组系数。

    更根本的理由是函数形式本身：Cobb-Douglas 位似、替代弹性为一，支出份额
    在理论上就不随价格和收入变化。让 s 随城市变，是在跟自己选的效用函数
    打架。

    因此查 CEX 用的是一个**与城市无关的参照收入**（产品模式取用户当前收入，
    研究模式取人设指定收入），在 Persona 构造时算一次并冻结。装配层不得
    持有 ExpenditureShareProvider 的引用——有一条测试盯着这件事。

    ``variety`` 是 §6.4 的可贸易品三题（常吃菜系、汽车品牌倾向、手机品牌
    倾向）。V1 全美同值，因此这三列方差为零，白送一个泄漏对照：若模型的
    选择与零方差列相关，说明估计层在读噪声。
    """

    persona_id: str
    params: PersonaParams
    shares: ExpenditureShares
    occupation: OccupationId
    ability_quantile: int  # one of the OEWS quantiles: 10, 25, 50, 75, 90
    brief: PersonaBrief
    variety: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ability_quantile not in (10, 25, 50, 75, 90):
            raise ValueError(
                "ability_quantile must be an OEWS quantile (10/25/50/75/90); "
                "README §7.5 forbids defaulting everyone to the median"
            )
        if self.brief.persona_id != self.persona_id:
            raise ValueError("persona_id must match between Persona and its brief")


@dataclass(frozen=True, slots=True)
class Observable:
    """L0-L2. The only city information an agent ever sees.

    ``o3_representative_real`` is environment, not answer, despite deflating
    income: it uses the *representative* basket, so it does not depend on this
    person's shares and therefore cannot be A(a; theta) in disguise.
    """

    tier: ObservableTier
    city: CityId
    year: int
    l0_amenities: Mapping[str, float]
    l1_economics: Mapping[str, float]
    l2_accounting: Mapping[str, float] | None = None
    o3_representative_real: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.l0_amenities or not self.l1_economics:
            raise ValueError(
                "every tier retains L0 and L1. README §11.4: the collapsed quantities are "
                "additional, never a replacement, or the design matrix loses rank and the "
                "conditional logit cannot recover the injected weights"
            )
        if self.tier is ObservableTier.O1 and (
            self.l2_accounting is not None or self.o3_representative_real is not None
        ):
            raise ValueError("O1 carries L0 and L1 only")
        if self.tier in (ObservableTier.O2, ObservableTier.O3) and self.l2_accounting is None:
            raise ValueError(f"{self.tier.value} must carry L2 accounting")
        if self.tier is ObservableTier.O3 and self.o3_representative_real is None:
            raise ValueError("O3 must carry representative-basket real income")
        if self.tier is ObservableTier.O2 and self.o3_representative_real is not None:
            raise ValueError("O2 must not carry the O3 deflated quantities")


@dataclass(frozen=True, slots=True)
class CityRecord:
    """M2 眼中的一座城市：已经被 ledger 标准化过的量，没有一列原始数据。

    这是"M2 对国家无知"这条架构规则的落点。M2 里不允许出现
    ``if country == ...``；它拿到的就是这个结构，不知道 rpp 是 BEA 发的还是
    别国统计局发的，也不知道 amenities 是用绝对锚定还是分位表标度出来的。
    所有国别知识都在上游的 CountryPack 里被消化掉了。若 M2 里冒出了需要
    分国家讨论的东西，那是上游抽象漏了，修上游，不修 M2。

    字段约定：

      rpp                 三个分项，**已归一到全美=1**（不是 BEA 发布的
                          全美=100）。归一在 ledger 层做，见
                          ledger.normalization。这样 ln P 落在 0 附近，
                          条件 logit 的数值条件明显更好。
      wage_by_quantile    OEWS 的五个分位。§7.5 明确禁止一律取中位数：
                          能力分位到工资分位的映射是不能省的一项。
      amenities_scaled    已标度到 [0,1]，语义是"越大越好"。标度函数是
                          自由参数（§5.3），它属于 ledger，不属于这里。
      variety_ln_price    可贸易品的品种调整项，对数空间，按类别给出。
                          V1 全美同值（通常全零），V2 由标志商品机制填。
    """

    city: CityId
    rpp: Mapping[str, float]  # 键为 "rent" / "goods" / "other"
    wage_by_quantile: Mapping[int, float]
    amenities_scaled: Mapping[str, float]
    jurisdiction: Jurisdiction
    variety_ln_price: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = {"rent", "goods", "other"} - set(self.rpp)
        if missing:
            raise ValueError(f"CityRecord.rpp 缺少分项 {sorted(missing)}；§5.2 不使用 rpp_all")
        for name, value in self.rpp.items():
            if not RPP_MIN <= value <= RPP_MAX:
                raise ValueError(
                    f"rpp_{name} = {value}，不在 [{RPP_MIN}, {RPP_MAX}]。"
                    "RPP 以全美=100 发布，进 CityRecord 之前必须由 ledger.normalization "
                    "归一到全美=1。忘了归一化不会算错，只会让 ln P 落在 4.6 而不是 0 附近，"
                    "logit 数值条件变差、系数不好解释——而且不报错。这条护栏就是为了让它报错"
                )
        for name, value in self.amenities_scaled.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"舒适度 {name} 的标度值 {value} 不在 [0,1]，标度应在 ledger 完成")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """L3-L4。永不进入 prompt（CLAUDE.md 硬规则 1）。

    ``features`` 与 ``coefficients`` 是 ln V 的分解，M5 直接消费，**不自行
    重算**。这么做的理由不是浮点漂移（那是 1e-15 量级，可忽略），而是两条：

      一、单一真值来源。特征表示若有两份实现，它们迟早会分叉，而分叉之后
          "回收误差"这个核心因变量就没有意义了。
      二、§11.3 的精确并列求解器与 M5 必须用同一套特征表示。并列是在
          ln V 上求解出来的；如果 M5 眼里的特征不是同一组，那个"精确"在
          M5 看来就不精确，§11.6 赖以成立的 σ 无关性随之失效。

    ``ln_money_metric`` 是 §9.1.1 美元口径的载体，**存对数不存美元**。理由见
    econ.utility.ln_money_metric：1/w₁ 在指数上，而 Dirichlet(α=0.5) 会抽出
    w₁ 极小的人设，直接存美元会让约 3% 的人设静默下溢成 $0——而"没有损失"
    与"美元口径对这个人没有意义"在报表上会长得一模一样。

    ``equivalent_variation`` 是它的美元形式，低于语义地板时为 None 而不是 0。
    **命名提醒**：这个字段装的是货币度量 e(V) 本身，不是等价变差；两座城市
    的 e 之差才是等价变差。只应经 econ.utility.welfare_loss 消费。

    物质项**拆成 ln_y_net 与 ln_p 两列**，而不是合成一列 ln(Y/P)。模型说
    这两个系数应当等值反号（都是 ±w₁），拆开之后这个约束从"我们的假设"
    变成"可检验的假设"，而 ``|coef_Y + coef_P|`` 直接就是货币幻觉的连续
    度量——它把 §11.3 第四档从一个二元的对错判断升级成了一个标量。
    """

    city: CityId
    year: int
    amenity_index: float  # A(a; theta)，L3
    profile: Mapping[str, float]  # 分维剖面，L3
    utility: WelfareLevel  # L4
    ln_utility: float  # L4，对数空间的同一个数
    ln_kappa: float  # 跨城不变的常数项，见 econ.utility.ln_kappa
    features: Mapping[str, float]  # ln V 的各列，M5 消费
    coefficients: Mapping[str, float]  # 各列对应的真值系数
    ln_money_metric: float  # ln e(V)，见 econ.utility.ln_money_metric
    equivalent_variation: Money | None = None  # e(V) 的美元值，低于语义地板时为 None


@dataclass(frozen=True, slots=True)
class ChoiceSetTruth:
    """选择集层面的真值。键 = persona × 选择集 × year。

    **为什么与 GroundTruth 分开**：福利间隔、离散度、全距都是"这个选择集"
    的属性，不是"这座城市"的属性——同一座城市放进不同的选择集，这三个数
    就不同。挂在单城市的 GroundTruth 上会让它们看起来像城市属性，而那类
    错位不会报错，只会让某个下游算出对不上的数。顺带也避免了每座城市都
    把全集合算一遍的 N 倍重复。见 DECISIONS/0004。

    **只存一个向量，其余全是派生量。** 三个福利量都是同一组 ln e 的函数，
    分别存下来就有三个可以各自漂移的地方；存一份、其余用 property 算，
    是把"它们必须自洽"这件事变成结构性的而不是纪律性的。
    """

    persona_id: str
    year: int
    cities: tuple[CityId, ...]
    ln_money_metric: Mapping[str, float]  # 城市码 -> ln e(V)
    argmax_city: CityId
    numeraire: str
    reference: str  # 货币度量的参照点，见 econ.utility.MONEY_METRIC_REFERENCE

    def __post_init__(self) -> None:
        if len(self.cities) < 2:
            raise ValueError("选择集至少要有两座城市，否则没有可比较的东西")
        codes = {c.code for c in self.cities}
        if set(self.ln_money_metric) != codes:
            raise ValueError(
                f"ln_money_metric 的城市与 cities 不一致："
                f"{sorted(codes ^ set(self.ln_money_metric))} 只出现在一侧"
            )
        if self.argmax_city not in self.cities:
            raise ValueError(f"argmax_city {self.argmax_city} 不在选择集里")

    @property
    def _sorted_ln(self) -> list[float]:
        return sorted(self.ln_money_metric.values(), reverse=True)

    @property
    def ln_welfare_gap(self) -> float:
        """最优与次优的 ln e 之差。无量纲，永远有限，不受语义地板影响。

        分箱、分组、按难度排序都该用这个，不要用美元版——美元版在下尾是
        None，会把样本悄悄削掉一块。
        """
        top = self._sorted_ln
        return top[0] - top[1]

    @property
    def welfare_gap(self) -> float | None:
        """最优与次优的美元差。低于语义地板时为 None。"""
        from econ.utility import welfare_loss

        top = self._sorted_ln
        return welfare_loss(top[0], top[1])

    @property
    def welfare_range(self) -> float | None:
        """最优与最劣的美元差。§1.2.5 归一化福利损失的分母。"""
        from econ.utility import welfare_loss

        top = self._sorted_ln
        return welfare_loss(top[0], top[-1])

    @property
    def welfare_dispersion(self) -> float | None:
        """全部城市美元货币度量的标准差。适用边界阈值 τ 与信噪比分组用它。

        与 welfare_range **必须分开**（MIGRATION B1）：全距是归一化的分母，
        标准差是分组的依据，两者在城市数不同的选择集之间行为不同。
        """
        from econ.utility import money_metric_dollars

        values = [money_metric_dollars(v) for v in self.ln_money_metric.values()]
        if any(v is None for v in values):
            return None
        mean = sum(values) / len(values)
        return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

    @property
    def n_below_money_floor(self) -> int:
        """有多少座城市的货币度量落在语义地板之下。

        它必须进结果元数据：美元口径的样本量与总样本量不同，而差多少是
        一个要被看见的数，不是一个可以四舍五入掉的细节。
        """
        from econ.utility import MONEY_METRIC_LN_FLOOR

        return sum(1 for v in self.ln_money_metric.values() if v < MONEY_METRIC_LN_FLOOR)


def _reject_answer_side(value: object, forbidden: tuple[type, ...], path: str) -> None:
    if isinstance(value, forbidden):
        raise BoundaryViolationError(
            f"{type(value).__name__} reached agent-visible field {path!r}; "
            "CLAUDE.md hard rule 1 forbids GroundTruth in any prompt"
        )
    if isinstance(value, Mapping):
        for k, v in value.items():
            _reject_answer_side(v, forbidden, f"{path}[{k!r}]")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for i, v in enumerate(value):
            _reject_answer_side(v, forbidden, f"{path}[{i}]")


@dataclass(frozen=True, slots=True)
class AgentInput:
    """The complete, and only, payload an agent adapter may accept.

    Constructing one is the choke point for hard rule 1: it walks itself and
    refuses anything from the answer side, so a leak has to get past a
    constructor rather than past a reviewer.
    """

    brief: PersonaBrief
    options: tuple[Observable, ...]
    permutation: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        forbidden = (GroundTruth, Persona, PersonaParams, WelfareLevel)
        for name in ("brief", "options", "permutation"):
            _reject_answer_side(getattr(self, name), forbidden, path=name)
        if self.permutation and sorted(self.permutation) != list(range(len(self.options))):
            raise ValueError(
                "permutation must be a permutation of the option indices; README §10.2 "
                "requires the order to be randomised and the permutation recorded"
            )


@runtime_checkable
class EconomicCore(Protocol):
    """One implementation, two exits (README §3.2).

    Both must call the same underlying functions. Writing the ground-truth
    generator and the offloaded arithmetic separately lets the tax function
    drift between them, and then a discrepancy cannot be attributed to either.
    """

    def observe(
        self, persona: Persona, city: CityId, year: int, tier: ObservableTier
    ) -> Observable: ...

    def score(self, persona: Persona, city: CityId, year: int) -> GroundTruth: ...
