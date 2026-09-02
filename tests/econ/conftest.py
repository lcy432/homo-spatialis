"""M2 harness 的 fixtures。

与上一版的关键差别：**不再有玩具核**。故意写坏的核直接继承真正的
WelfareCore，只重写一个方法。这样单次计入断言咬的是生产代码，而不是
一个长得像生产代码的替身。

依然保留"每个检查都要有一个能被它抓到的坏核"这条原则：一个从未见过
违规的断言等于装饰。
"""

from __future__ import annotations

import math

import pytest

from common.ids import CityId, Jurisdiction, OccupationId
from common.money import USD_2024_US100, Money
from common.providers import ExpenditureShares
from econ.core import WelfareCore
from econ.graph import Term, UtilityGraph
from econ.kernel import GrowthRates, TransitionKernel
from econ.types import (
    CityRecord,
    LiteralnessTier,
    Observable,
    Persona,
    PersonaBrief,
    PersonaParams,
)

REFERENCE_ID = "US_MSA_2024"
AMENITY_DIMS = ("safety", "sunshine", "commute")


class ProgressiveTax:
    """两档累进税加一个按辖区深度的地方附加。

    累进是必须的，不是装饰：Albouy (2009) 的效应——名义工资高的城市，
    同等条件劳动者多交约 30% 联邦税——只有在累进税下才存在。平税会让
    §11.3 第四档（对抗型，检验货币幻觉）失去一半的构造空间。

    地方附加依赖 Jurisdiction 路径的**深度**，这样美国式的 (国, 州) 与
    只有 (国,) 的国家走同一个签名，M2 不需要知道区别。
    """

    def __init__(
        self, brackets: tuple[tuple[float, float], ...] = ((50_000.0, 0.12), (float("inf"), 0.32))
    ):
        self.brackets = brackets

    def tax(self, gross: Money, jurisdiction: Jurisdiction, year: int) -> Money:
        owed = 0.0
        lower = 0.0
        for upper, rate in self.brackets:
            if gross.amount <= lower:
                break
            taxable = min(gross.amount, upper) - lower
            owed += taxable * rate
            lower = upper
        local = 0.02 * (len(jurisdiction.path) - 1)
        return Money(owed + gross.amount * local, gross.numeraire)


US_JURISDICTION = Jurisdiction(("US", "MA"))

# 三座合成城市。中性标签（§11.3：不得写得像任何一座真实城市，否则测的是
# 模型对真实城市的记忆而非推理）。rpp 已归一到全美=1。
CITY_RECORDS = {
    "C1": CityRecord(
        city=CityId("US", "SYNTH", "C1"),
        rpp={"rent": 1.31, "goods": 1.02, "other": 1.11},
        wage_by_quantile={10: 58_000.0, 25: 71_000.0, 50: 92_000.0, 75: 118_000.0, 90: 149_000.0},
        amenities_scaled={"safety": 0.62, "sunshine": 0.58, "commute": 0.41},
        jurisdiction=US_JURISDICTION,
    ),
    "C7": CityRecord(
        city=CityId("US", "SYNTH", "C7"),
        rpp={"rent": 0.88, "goods": 0.97, "other": 0.94},
        wage_by_quantile={10: 44_000.0, 25: 54_000.0, 50: 71_000.0, 75: 89_000.0, 90: 112_000.0},
        amenities_scaled={"safety": 0.79, "sunshine": 0.83, "commute": 0.76},
        jurisdiction=US_JURISDICTION,
    ),
    "C4": CityRecord(
        city=CityId("US", "SYNTH", "C4"),
        rpp={"rent": 1.04, "goods": 1.00, "other": 1.02},
        wage_by_quantile={10: 50_000.0, 25: 62_000.0, 50: 80_500.0, 75: 101_000.0, 90: 127_000.0},
        amenities_scaled={"safety": 0.71, "sunshine": 0.66, "commute": 0.58},
        jurisdiction=US_JURISDICTION,
    ),
}


class DoubleCountingCore(WelfareCore):
    """故意写坏：房租既进价格指数又进舒适度向量。

    这正是 §7.4 点名的那个错误，也是运行时看上去完全健康、却会摧毁
    识别的那一个。
    """

    def _amenities(self, persona, city, g=None):
        out = super()._amenities(persona, city, g)
        if g is not None:
            g.contribute("rpp_rent", Term.AMENITY, dimension="housing_niceness")
        return out


class DiagnosticInUtilityCore(WelfareCore):
    """故意写坏：住房负担比被当成舒适度。

    负担比是租金除以收入，两者都已经计过了，再加一次是各计了三遍。
    §9.1 把它留作诊断量。
    """

    def _amenities(self, persona, city, g=None):
        out = super()._amenities(persona, city, g)
        if g is not None:
            g.contribute("housing_burden_ratio", Term.AMENITY, dimension="affordability")
        return out


class CityVaryingSharesCore(WelfareCore):
    """故意写坏：份额按城市变（架构修正一禁止的那件事）。

    留着它是为了让"κ 跨城不变"那条断言有东西可抓。真核里份额只从
    persona 读，所以正确实现下这个错误无法发生；但假如有一天有人把
    CEX provider 塞回装配层，κ 就会开始跨城漂移，而排序测试发现不了。
    """

    def _price_index(self, persona, city, g=None):
        rec = self.record(city)
        # 用城市的租金水平去"调整"份额 —— 正是被禁止的做法
        tilt = min(0.55, 0.34 * rec.rpp["rent"])
        skewed = ExpenditureShares.from_raw(tilt, 0.29, 0.37)
        clone = Persona(
            persona_id=persona.persona_id,
            params=persona.params,
            shares=skewed,
            occupation=persona.occupation,
            ability_quantile=persona.ability_quantile,
            brief=persona.brief,
        )
        return super()._price_index(clone, city, g)

    def kappa_of(self, persona, city):
        """这个坏核下 κ 会随城市变；真核下不会。"""
        from econ.utility import ln_kappa

        rec = self.record(city)
        tilt = min(0.55, 0.34 * rec.rpp["rent"])
        skewed = ExpenditureShares.from_raw(tilt, 0.29, 0.37)
        w1 = persona.params.w_material
        params = PersonaParams(
            alpha=w1 * (skewed.s_goods + skewed.s_other),
            beta=w1 * skewed.s_rent,
            theta=persona.params.theta,
        )
        return ln_kappa(params)


def render_prompt(brief: PersonaBrief, observables: list[Observable]) -> str:
    """一个替身 prompt 序列化器，让泄漏扫描有文本可扫。"""
    lines = [f"You are: {brief.narrative}", "", "Options:"]
    for o in observables:
        lines.append(f"  {o.city.code}:")
        for block in (o.l1_economics, o.l0_amenities, o.l2_accounting, o.o3_representative_real):
            if block:
                for k, v in sorted(block.items()):
                    lines.append(f"    {k} = {v}")
    return "\n".join(lines)


def softmax_entropy(utilities: list[float], sigma: float) -> float:
    """真值选择分布的熵。

    放在测试里而不是 src/eval/：M5 的核心数学由人手写第一版，这里只是
    为了演示实验设计的一个性质。
    """
    m = max(u / sigma for u in utilities)
    exps = [math.exp(u / sigma - m) for u in utilities]
    total = sum(exps)
    probs = [e / total for e in exps]
    return -sum(p * math.log(p) for p in probs if p > 0)


@pytest.fixture
def cities() -> dict[str, CityRecord]:
    return dict(CITY_RECORDS)


@pytest.fixture
def tax() -> ProgressiveTax:
    return ProgressiveTax()


@pytest.fixture
def persona() -> Persona:
    """一个七维被压到五维的人设（三个舒适度 + 物质项拆成 α/β）。

    份额在这里就是冻结的常数，不是查表的结果——这正是架构修正一要求的
    形态：Persona 构造时用一个与城市无关的参照收入查一次，然后固定。
    """
    shares = ExpenditureShares(0.34, 0.29, 0.37)
    w1 = 0.52
    params = PersonaParams(
        alpha=w1 * (shares.s_goods + shares.s_other),
        beta=w1 * shares.s_rent,
        theta={"safety": 0.20, "sunshine": 0.13, "commute": 0.15},
    )
    brief = PersonaBrief(
        persona_id="p001",
        literalness=LiteralnessTier.IMPLICIT,
        narrative=(
            "I hate driving and cannot take grey winters. I would rather have a smaller "
            "place in a quiet neighbourhood than a big one I have to commute to."
        ),
        dimension_order=("commute", "sunshine", "safety"),
    )
    return Persona(
        persona_id="p001",
        params=params,
        shares=shares,
        occupation=OccupationId("SOC2018", "15-1252"),
        ability_quantile=50,
        brief=brief,
    )


@pytest.fixture
def core(cities, tax) -> WelfareCore:
    return WelfareCore(cities, tax, numeraire=USD_2024_US100, reference_id=REFERENCE_ID)


@pytest.fixture
def kernel() -> TransitionKernel:
    return TransitionKernel(
        {
            "C1": GrowthRates(real_wage=0.012, real_rent=0.021, real_interest=0.015),
            "C7": GrowthRates(real_wage=0.019, real_rent=0.006, real_interest=0.015),
            "C4": GrowthRates(real_wage=0.015, real_rent=0.011, real_interest=0.015),
        },
        savings_rate=0.15,
    )


@pytest.fixture
def city_a() -> CityId:
    return CITY_RECORDS["C1"].city


@pytest.fixture
def city_b() -> CityId:
    return CITY_RECORDS["C7"].city


def graph_of(core: WelfareCore, persona: Persona, city: CityId) -> UtilityGraph:
    return core.graph_for(persona, city)
