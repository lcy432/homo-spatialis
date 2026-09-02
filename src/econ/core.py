"""M2 的装配层：把标准化过的城市记录拼成 Observable 与 GroundTruth。

这一层是 README §3.2 说的"一份实现、两个出口"。observe() 与 score() 调用
同一批底层函数，绝不各算一遍——写两份会让税函数漂移，而漂移之后，一个
差异出现时你无法判断是模型错了还是尺子错了。

三条架构规则在这里的落点：

  一、对国家无知。本文件里不存在 ``if country == ...``。国别知识全部
      在上游被消化成 CityRecord 与注入的 TaxFunction。如果有一天这里
      冒出了需要分国家讨论的东西，那是上游抽象漏了，修上游不修这里。
      验收标准是 FAKE_COUNTRY fixture 跑通时本文件一行不改。

  二、UtilityGraph 的 g 只穿装配层。_income / _price_index / _amenities
      三个方法接受 g，因为原始列只在它们里面被读取。纯函数那一层
      （econ.utility）没有列，只有数，不接受 g。

  三、装配层不持有 ExpenditureShareProvider。份额是 Persona 的字段，在
      构造 persona 时用一个与城市无关的参照收入查一次并冻结。理由写在
      types.Persona 的文档字符串里，有一条测试盯着这个类的属性。

--------------------------------------------------------------------------
泄漏防护在这一层是怎么落地的
--------------------------------------------------------------------------

observe() 只读 CityRecord 与 persona 的**非偏好字段**（职业、能力分位、
份额），score() 才读 params。两者返回不同的类型，而 AgentInput 的构造
函数会拒收 GroundTruth 一系。所以泄漏要发生，得同时骗过：类型边界、
AgentInput 的递归检查、以及 common.leakcheck 的数值扫描。

一个容易被忽略的点：O2/O3 档给出的会计量是**用 persona 的份额算的**
（税后收入、储蓄轨迹），这没问题，因为份额来自行为而非陈述偏好，不是
答案的一部分。但 O3 的实际收入必须用**代表性篮子**平减，不能用这个人
自己的份额——否则它就是 A(a; θ) 的一半，题目当场没了。这一条在
_representative_real 里，注释标了。
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from common.ids import CityId
from common.money import Money, WelfareLevel
from common.providers import ExpenditureShares, TaxFunction
from econ.graph import Term, UtilityGraph
from econ.types import (
    CityRecord,
    GroundTruth,
    Observable,
    ObservableTier,
    Persona,
)
from econ.utility import (
    MIN_AMENITY,
    UtilityDecomposition,
    indirect_utility,
    ln_indirect_utility,
    solve_ln_amenity_for_tie,
)
from econ.variety import NULL_VARIETY, VarietyAdjustment

# §5.2 的三个分项与 ExpenditureShares 三个字段的对应。这张表是口径映射的
# 唯一落点：CEX 的支出分类与 RPP 的三类划分口径不同（§5.7），映射本身是
# ledger 的产物，这里只负责把已经映射好的份额接到正确的分项上。
_SHARE_OF_CATEGORY = {
    "rent": "s_rent",
    "goods": "s_goods",
    "other": "s_other",
}

# 代表性篮子。O3 用它平减，因此 O3 仍然是环境而不是答案：它不依赖
# 任何一个人的偏好参数。数值是占位的等分，接真实 CEX 后由 ledger 提供。
REPRESENTATIVE_BASKET = ExpenditureShares(s_rent=0.33, s_goods=0.33, s_other=0.34)

_SHARE_TOL = 1e-9


class WelfareCore:
    """真值的唯一来源。实现 econ.types.EconomicCore 协议。

    构造参数里刻意**没有** ExpenditureShareProvider（架构修正一）。份额
    从 persona 上读，不在这里查表。

    ``tax`` 是注入的，来自上游 CountryPack。注入而非内建，是"对国家无知"
    的实现方式：本类不知道它拿到的是美国的联邦加州税还是别国的税制，
    只知道它是一个 (Money, Jurisdiction, year) -> Money 的函数。
    """

    def __init__(
        self,
        cities: Mapping[str, CityRecord],
        tax: TaxFunction,
        *,
        numeraire: str,
        reference_id: str,
        variety: VarietyAdjustment = NULL_VARIETY,
    ) -> None:
        self.cities = dict(cities)
        self.tax = tax
        self.numeraire = numeraire
        self.reference_id = reference_id
        self.variety = variety

    # ------------------------------------------------------------------
    # 装配层：以下三个方法读取原始列，因此它们（也只有它们）接受 g
    # ------------------------------------------------------------------

    def record(self, city: CityId) -> CityRecord:
        try:
            return self.cities[city.code]
        except KeyError:
            raise KeyError(f"世界里没有城市 {city}；已知：{sorted(self.cities)}") from None

    def _income(
        self,
        persona: Persona,
        city: CityId,
        g: UtilityGraph | None = None,
        *,
        savings_rate: float = 0.0,
    ) -> dict[str, float]:
        """毛收入 → 税 → 税后 → 扣除储蓄后的消费预算。全部是 L2 会计量。

        L2 必须归环境（§3.2）：累进税与复利是纯算术，让代理自己算，测到的
        是它的算术能力而非偏好理解能力，而且算术误差会污染回收误差——
        也就是整个研究模式的核心因变量。

        能力分位到工资分位的映射在这里，不是在别处取中位数（§7.5 明确
        点名这一项不能省）。
        """
        rec = self.record(city)
        if g is not None:
            g.contribute("wage", Term.BUDGET)
            g.contribute("tax", Term.BUDGET)

        gross = rec.wage_by_quantile[persona.ability_quantile]
        tax_owed = self.tax.tax(Money(gross, self.numeraire), rec.jurisdiction, 0).amount
        net = gross - tax_owed
        if net <= 0.0:
            raise ValueError(f"{city} 的税后收入非正（{net}），税表或工资表有问题")

        saving = net * savings_rate
        budget = net - saving
        return {
            "gross_income": gross,
            "tax": tax_owed,
            "net_income": net,
            "saving": saving,
            "consumption_budget": budget,
        }

    def _price_index(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        """返回 **ln P**，不是 P（架构修正三）。

            ln P = Σ_k s_k · (ln rpp_k + 品种调整_k)

        份额只从 persona 读，绝不按城市查表。份额不和为一则 P 不是价格的
        一次齐次函数，也就不是任何东西的价格指数，所以这里断言而不是
        归一化——静默归一化会掩盖上游的错误。

        rpp 已在 ledger 层归一到全美=1（不是 BEA 发布的全美=100），所以
        ln P 落在 0 附近，条件 logit 的数值条件明显更好。
        """
        rec = self.record(city)
        shares = persona.shares
        total = shares.s_rent + shares.s_goods + shares.s_other
        if abs(total - 1.0) > _SHARE_TOL:
            raise ValueError(f"支出份额和为 {total!r}，不为一则 P 不是价格指数（不满足一次齐次）")

        ln_p = 0.0
        for category, share_field in _SHARE_OF_CATEGORY.items():
            if g is not None:
                g.contribute(f"rpp_{category}", Term.PRICE_INDEX)
            share = getattr(shares, share_field)
            adjustment = self.variety.ln_adjustment(category, city, persona.variety)
            if adjustment != 0.0 and g is not None:
                # 品种调整用的是另外的原始列（标志商品价格），与 rpp 不同列，
                # 所以同归 PRICE_INDEX 不构成重复计入。
                g.contribute(f"variety_{category}", Term.PRICE_INDEX)
            ln_p += share * (math.log(rec.rpp[category]) + adjustment)
        return ln_p

    def _amenities(
        self, persona: Persona, city: CityId, g: UtilityGraph | None = None
    ) -> dict[str, float]:
        """返回各维的 **ln A_k**。标度已在 ledger 完成，这里只取对数。

        夹一个下界：标度函数值域是闭区间 [0,1]，取到 0 会让 ln A 变成
        -inf，整个效用塌成零。见 econ.utility.MIN_AMENITY 的说明——那是
        一个建模选择，写在明处。
        """
        rec = self.record(city)
        out: dict[str, float] = {}
        for dim in persona.params.theta:
            try:
                raw = rec.amenities_scaled[dim]
            except KeyError:
                raise KeyError(f"{city} 缺少舒适度维度 {dim!r} 的标度值") from None
            if g is not None:
                g.contribute(f"amenity_{dim}", Term.AMENITY, dimension=dim)
            out[dim] = math.log(max(raw, MIN_AMENITY))
        return out

    # ------------------------------------------------------------------
    # 两个出口
    # ------------------------------------------------------------------

    def decompose(
        self,
        persona: Persona,
        city: CityId,
        g: UtilityGraph | None = None,
        *,
        savings_rate: float = 0.0,
    ) -> UtilityDecomposition:
        """ln V 及其分解。observe 与 score 都走这条路，所以只有一份实现。"""
        acc = self._income(persona, city, g, savings_rate=savings_rate)
        ln_p = self._price_index(persona, city, g)
        ln_a = self._amenities(persona, city, g)
        return ln_indirect_utility(math.log(acc["consumption_budget"]), ln_p, ln_a, persona.params)

    def graph_for(self, persona: Persona, city: CityId) -> UtilityGraph:
        """遍历效用计算图，供 §10.1 的单次计入断言使用。"""
        g = UtilityGraph()
        self.decompose(persona, city, g)
        return g

    def _representative_real(self, persona: Persona, city: CityId) -> float:
        """用**代表性篮子**平减的实际收入，O3 档专用。

        用代表性篮子而不是这个人自己的份额，是 O3 仍然属于环境的全部
        理由：它不依赖任何偏好参数，因此不是 A(a; θ) 的一部分。若这里
        误用 persona.shares，O3 就直接把答案的一半递给了代理。
        """
        rec = self.record(city)
        rep = REPRESENTATIVE_BASKET
        ln_p_rep = (
            rep.s_rent * math.log(rec.rpp["rent"])
            + rep.s_goods * math.log(rec.rpp["goods"])
            + rep.s_other * math.log(rec.rpp["other"])
        )
        return self._income(persona, city)["net_income"] / math.exp(ln_p_rep)

    def observe(
        self, persona: Persona, city: CityId, year: int, tier: ObservableTier
    ) -> Observable:
        """L0–L2。代理可见的一切。

        每一档都保留 L0 与 L1（§11.4 的秩条件）：折叠量是附加，不是替代。
        若 O2 把若干属性折叠成"三年储蓄"一个数而抽掉原始列，设计矩阵秩
        亏，条件 logit 在数学上就恢复不了那些权重。Observable 的构造函数
        会拒绝违反这一点的组合。
        """
        rec = self.record(city)
        l1 = {
            "wage": rec.wage_by_quantile[persona.ability_quantile],
            "rpp_rent": rec.rpp["rent"],
            "rpp_goods": rec.rpp["goods"],
            "rpp_other": rec.rpp["other"],
        }
        l0 = dict(rec.amenities_scaled)

        l2 = None
        o3 = None
        if tier in (ObservableTier.O2, ObservableTier.O3):
            acc = self._income(persona, city)
            l2 = {
                "tax": acc["tax"],
                "net_income": acc["net_income"],
                # 住房负担比是诊断量（§9.1），进 Observable 没问题——它是
                # L2 会计量。它不进效用函数，那由 graph.py 的断言守。
                "housing_burden_ratio": persona.shares.s_rent,
            }
        if tier is ObservableTier.O3:
            o3 = {"real_income_representative": self._representative_real(persona, city)}

        return Observable(
            tier=tier,
            city=city,
            year=year,
            l0_amenities=l0,
            l1_economics=l1,
            l2_accounting=l2,
            o3_representative_real=o3,
        )

    def score(
        self, persona: Persona, city: CityId, year: int, *, savings_rate: float = 0.0
    ) -> GroundTruth:
        """L3–L4。代理永不可见。

        features 与 coefficients 直接从分解里带出来交给 M5，M5 不重算
        （架构修正二）。这样精确并列求解器与 M5 用的是同一套特征表示，
        §11.6 的 σ 无关性才成立。
        """
        d = self.decompose(persona, city, savings_rate=savings_rate)
        rec = self.record(city)
        amenity_index = math.exp(
            sum(
                persona.params.theta[dim] * ln_a
                for dim, ln_a in self._amenities(persona, city).items()
            )
        )
        return GroundTruth(
            city=city,
            year=year,
            amenity_index=amenity_index,
            profile={dim: rec.amenities_scaled[dim] for dim in persona.params.theta},
            utility=WelfareLevel(d.utility, self.numeraire, self.reference_id),
            ln_utility=d.ln_utility,
            ln_kappa=d.ln_kappa,
            features=d.features,
            coefficients=d.coefficients,
        )

    # ------------------------------------------------------------------
    # §11.3 第三档：精确并列的构造
    # ------------------------------------------------------------------

    def solve_amenity_for_tie(
        self, persona: Persona, target: CityId, city: CityId, dimension: str
    ) -> float:
        """反解 ``city`` 在 ``dimension`` 上的标度值，使其效用精确等于 ``target``。

        返回的是标度值本身（[0,1] 区间内），可以直接写回 CityRecord。
        落在区间外意味着这一对城市在这一维上没法拉平——通常是差距太大，
        换一维或换一座城市，不要把值夹回边界，那样得到的是"接近"而不是
        "精确"，而 §11.6 整个论证都建立在"精确"之上。

        并列是**对某一个人**而言的。同一对城市，换一个 persona 就要重新
        求解，所以第三档是按人设构造的，不是每个世界构造一次。
        """
        target_ln_u = self.decompose(persona, target).ln_utility
        acc = self._income(persona, city)
        ln_p = self._price_index(persona, city)
        ln_a = self._amenities(persona, city)
        ln_needed = solve_ln_amenity_for_tie(
            target_ln_u,
            math.log(acc["consumption_budget"]),
            ln_p,
            ln_a,
            persona.params,
            dimension,
        )
        value = math.exp(ln_needed)
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"要在 {dimension!r} 上与 {target} 拉平，{city} 需要标度值 {value:.6g}，"
                "超出 [0,1]。换一维或换一座城市；不要夹回边界，那会把精确并列变成近似并列"
            )
        return value

    def with_amenity(self, city: CityId, dimension: str, value: float) -> WelfareCore:
        """返回一个新核，其中某座城市的某一维被替换。世界生成器用它落子。

        返回新对象而不是就地修改：真值必须可复现，而一个会被人改来改去的
        世界对象，在出问题时无法回溯到当时的状态。
        """
        rec = self.record(city)
        amenities = dict(rec.amenities_scaled)
        amenities[dimension] = value
        updated = CityRecord(
            city=rec.city,
            rpp=rec.rpp,
            wage_by_quantile=rec.wage_by_quantile,
            amenities_scaled=amenities,
            jurisdiction=rec.jurisdiction,
            variety_ln_price=rec.variety_ln_price,
        )
        cities = dict(self.cities)
        cities[city.code] = updated
        return WelfareCore(
            cities,
            self.tax,
            numeraire=self.numeraire,
            reference_id=self.reference_id,
            variety=self.variety,
        )

    def with_record(self, city: CityId, record: CityRecord) -> WelfareCore:
        """返回一个替换了某座城市记录的新核。转移核用它把世界推进一年。

        与 with_amenity 一样返回新对象而不是就地改：一条三年轨迹如果是靠
        反复改同一个世界对象跑出来的，出问题时无法回溯到第二年当时的状态。
        """
        cities = dict(self.cities)
        cities[city.code] = record
        return WelfareCore(
            cities,
            self.tax,
            numeraire=self.numeraire,
            reference_id=self.reference_id,
            variety=self.variety,
        )

    def utility_value(self, persona: Persona, city: CityId) -> float:
        """水平空间的 V，走 econ.utility.indirect_utility 那条薄封装。"""
        acc = self._income(persona, city)
        ln_p = self._price_index(persona, city)
        rec = self.record(city)
        a = {dim: rec.amenities_scaled[dim] for dim in persona.params.theta}
        return indirect_utility(acc["consumption_budget"], math.exp(ln_p), a, persona.params)


def build_core(
    cities: Mapping[str, CityRecord],
    tax: TaxFunction,
    *,
    numeraire: str,
    reference_id: str,
    variety: VarietyAdjustment = NULL_VARIETY,
) -> WelfareCore:
    """tests/econ/test_contract.py 找的就是这个名字。"""
    return WelfareCore(cities, tax, numeraire=numeraire, reference_id=reference_id, variety=variety)
