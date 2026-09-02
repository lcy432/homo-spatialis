"""四档难度。README §11.3。

--------------------------------------------------------------------------
存函数，不存配对
--------------------------------------------------------------------------

§11.9 要求人设轴与世界轴正交：世界生成器要支持"同一批人设投放到多个世界
实例"，而不是一次性生成（人设, 世界）配对。

但第三、四档**天生依赖人设**：

  第三档  求解属性值使两座城市效用精确相等——相等是对某一个人而言的。
          换个权重就不并列了。
  第四档  名义工资最高的城市不是实际福利最高的——这依赖这个人的支出
          结构和权重。同一组城市，对住房支出高的人是对抗型，对住房支出
          低的人可能不是。

第一、二档相反，是**纯世界属性**：一座城市若在每个属性上都更好、还更
便宜、工资还更高，那对谁都占优，不需要知道是谁。

解法是不物化配对，而是把第三、四档写成**确定性函数**：

    scenario = instantiate(tier, world, persona, tax)

仓库里只存两个独立的列表（世界、人设）；某档在某个交叉点上长什么样，
在需要时现算。同一个 (world, persona) 永远算出同一个结果，所以可复现；
而两条轴在存储与抽样上仍然完全正交。

代价是每次用都要算一遍。这个代价很小——求解是一次代数变形，不是数值
求根（见 econ.utility.solve_ln_amenity_for_tie）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from common.ids import CityId
from econ.core import WelfareCore
from econ.types import CityRecord, Persona
from worlds.dimensions import AMENITY_DIMENSIONS
from worlds.generator import World

NUMERAIRE = "USD_2024_US100"
REFERENCE_ID = "SYNTH_2024"


class Tier(Enum):
    """四档。数字与 §11.3 的编号一致。"""

    DOMINANT = 1  # 健全性检查，过不了即停项目
    TRADEOFF = 2  # 检验隐含权重是否等于注入值
    EXACT_TIE = 3  # 检验方差而非众数——信息量最大的一档
    ADVERSARIAL = 4  # 检验货币幻觉


class ScenarioInfeasibleError(RuntimeError):
    """这个 (世界, 人设) 交叉点上造不出这一档。

    是错误不是警告：静默降级成另一档，会让"第三档命中率"这个数里混进
    根本不是第三档的样本。
    """


@dataclass(frozen=True)
class Scenario:
    """在一个 (世界, 人设) 交叉点上实例化出来的情境。**不入库，现算。**

    ``correct_city`` 是真值 argmax。第三档它是 None，因为并列集合里没有
    唯一正确答案——那一档问的本来就不是"选对了没有"，是"面对真平局会不会
    集体倒向一边"。
    """

    tier: Tier
    world_id: str
    persona_id: str
    core: WelfareCore
    cities: tuple[CityId, ...]
    correct_city: CityId | None = None
    tied_cities: tuple[CityId, ...] = ()
    tie_dimension: str | None = None
    adjusted_city: CityId | None = None  # 第三档里被改动的是哪一座

    def utilities(self, persona: Persona) -> dict[str, float]:
        return {c.code: self.core.score(persona, c, 2024).ln_utility for c in self.cities}


def _core_of(world: World, tax) -> WelfareCore:
    return WelfareCore(world.cities, tax, numeraire=NUMERAIRE, reference_id=REFERENCE_ID)


def dominant_city(world: World) -> CityId | None:
    """找出帕累托占优的城市，没有则返回 None。**与人设无关。**

    占优的定义取最强的那一种：每个舒适度维都不差、每个价格分项都不高、
    工资不低，且至少有一处严格更好。这么强的定义下它对**任何**权重向量
    都是最优，这正是第一档要的——一个连它都答不对的模型，后面的档没有
    讨论价值（§11.3：过不了即停项目）。
    """
    records = list(world.cities.values())
    for candidate in records:
        if all(_weakly_dominates(candidate, other) for other in records if other is not candidate):
            return candidate.city
    return None


def _weakly_dominates(a: CityRecord, b: CityRecord) -> bool:
    better_anywhere = False
    for dim in AMENITY_DIMENSIONS:
        if a.amenities_scaled[dim] < b.amenities_scaled[dim]:
            return False
        if a.amenities_scaled[dim] > b.amenities_scaled[dim]:
            better_anywhere = True
    for k in ("rent", "goods", "other"):
        if a.rpp[k] > b.rpp[k]:
            return False
        if a.rpp[k] < b.rpp[k]:
            better_anywhere = True
    for q, w in a.wage_by_quantile.items():
        if w < b.wage_by_quantile[q]:
            return False
        if w > b.wage_by_quantile[q]:
            better_anywhere = True
    return better_anywhere


def make_dominant(world: World, target: CityId, *, margin: float = 0.05) -> World:
    """把某座城市改造成帕累托占优。与人设无关，所以是纯世界变换。

    做法是取每个属性上全世界最好的值，再往好的方向推 ``margin``。不是
    "抬高一点点"——占优必须是无争议的，否则第一档就不再是健全性检查。
    """
    records = dict(world.cities)
    rec = records[target.code]
    others = [r for code, r in records.items() if code != target.code]

    amenities = {
        d: min(1.0 - 1e-9, max(r.amenities_scaled[d] for r in others) * (1.0 + margin))
        for d in AMENITY_DIMENSIONS
    }
    rpp = {k: min(r.rpp[k] for r in others) * (1.0 - margin) for k in ("rent", "goods", "other")}
    wages = {
        q: max(r.wage_by_quantile[q] for r in others) * (1.0 + margin) for q in rec.wage_by_quantile
    }

    records[target.code] = CityRecord(
        city=rec.city,
        rpp=rpp,
        wage_by_quantile=wages,
        amenities_scaled=amenities,
        jurisdiction=rec.jurisdiction,
        variety_ln_price=rec.variety_ln_price,
    )
    return World(
        world_id=world.world_id,
        seed=world.seed,
        cities=records,
        growth=world.growth,
        label_permutation=world.label_permutation,
        condition_number=world.condition_number,
        calibration=world.calibration,
    )


def tie_dimension_for(persona: Persona) -> str:
    """第三档用哪一维来解并列：**取这个人权重最大的那一维。**

    这是"Dirichlet 不加下限"那条裁决的配套。α=0.5 偏向稀疏，会造出权重
    接近零的维度，而并列求解要除以 θ_k，除零无解。三条路里选了这一条：

      加下限            削弱稀疏性，而稀疏正是我们要的区分度来源
      跳过解不出的人设  丢失是非随机的（丢的全是稀疏人设），样本有偏
      挑最大权重维      ← 每个人设都解得出，且用的是对他最有分辨力的那维

    副作用是不同人设用不同维度构造并列。这不是缺点：并列本来就是对某一
    个人而言的，用他最在乎的那一维去拉平，比用一个他根本不在乎的维度
    更贴近"真无差异"的含义。
    """
    return max(persona.params.theta, key=lambda d: persona.params.theta[d])


def make_exact_tie(
    core: WelfareCore, persona: Persona, target: CityId, other: CityId
) -> tuple[WelfareCore, str, CityId]:
    """构造 ``target`` 与 ``other`` 的精确并列。返回 (新核, 用的维度, 被改动的城市)。

    残差是浮点噪声而不是求解器容差——在对数空间里这是一次代数变形。
    §11.6 的 σ 无关性整个建立在"精确"之上：近似并列时参考熵会随一个无法
    标定的 σ 在几乎整个 [0, ln n] 区间上摆动。

    **两级回退，因为并列可以从两边做。**

    第一次尝试是把落后的那座城市在这一维上往上抬。它可能无解：如果那座
    城市这一维已经接近 1，再抬就出界了，而求解器拒绝夹回边界（夹回去
    得到的是"接近"不是"精确"）。

    这时正确的做法不是放弃，是**反过来把领先的那座城市往下压**。同一个
    并列，从另一侧构造，可行域直接翻倍。

    两侧都不行才换维度，按权重降序试。换维度是最后手段，因为权重最大的
    那一维杠杆最大（要闭合的效用差是 Δ/θ_k，θ_k 越小需要的属性改动越
    夸张），换到小权重维度往往只是把出界推迟一步。

    这些回退不是为了"尽量成功"。它们是为了让第三档的样本**不带选择性**：
    直接放弃会丢掉一批人设，而丢掉的是有系统特征的那一批（属性恰好贴着
    边界的），第三档的方差就不再是总体的方差了。
    """
    dims = sorted(persona.params.theta, key=lambda d: persona.params.theta[d], reverse=True)
    failures: list[str] = []

    for dim in dims:
        if persona.params.theta[dim] <= 0.0:
            continue
        # 先抬落后者，再压领先者。
        for adjusted, anchor in ((other, target), (target, other)):
            try:
                value = core.solve_amenity_for_tie(persona, anchor, adjusted, dim)
            except ValueError as exc:
                failures.append(f"{dim}/{adjusted.code}: {exc}")
                continue
            return core.with_amenity(adjusted, dim, value), dim, adjusted

    raise ScenarioInfeasibleError(
        f"{other} 与 {target} 在任何维度、任何一侧都拉不平。尝试记录：\n  "
        + "\n  ".join(failures[:6])
    )


def highest_nominal_wage(core: WelfareCore, persona: Persona, cities: tuple[CityId, ...]) -> CityId:
    return max(cities, key=lambda c: core.record(c).wage_by_quantile[persona.ability_quantile])


def best_city(core: WelfareCore, persona: Persona, cities: tuple[CityId, ...]) -> CityId:
    return max(cities, key=lambda c: core.score(persona, c, 2024).ln_utility)


def is_adversarial(core: WelfareCore, persona: Persona, cities: tuple[CityId, ...]) -> bool:
    """名义工资最高的城市是不是**不**等于福利最高的城市。

    这一档能不能存在，取决于世界里工资与房租的相关（worlds.attributes 的
    因子模型）。若两者不相关，名义工资最高的城市大概率也是福利最高的，
    货币幻觉就无从检验——题目本身不成立。
    """
    return highest_nominal_wage(core, persona, cities) != best_city(core, persona, cities)


def make_adversarial(
    core: WelfareCore, persona: Persona, cities: tuple[CityId, ...], *, max_steps: int = 40
) -> WelfareCore:
    """把工资最高的那座城市推贵，直到它不再是福利最优。

    只动房租，不动工资：动工资会改变"谁是名义工资最高的城市"，那就不是
    在构造对抗，而是在换题目。每一步把租金抬 4%，直到 argmax 分离。
    """
    if is_adversarial(core, persona, cities):
        return core

    top_wage = highest_nominal_wage(core, persona, cities)
    for _ in range(max_steps):
        rec = core.record(top_wage)
        bumped = CityRecord(
            city=rec.city,
            rpp={**rec.rpp, "rent": rec.rpp["rent"] * 1.04},
            wage_by_quantile=rec.wage_by_quantile,
            amenities_scaled=rec.amenities_scaled,
            jurisdiction=rec.jurisdiction,
            variety_ln_price=rec.variety_ln_price,
        )
        core = core.with_record(top_wage, bumped)
        if is_adversarial(core, persona, cities):
            return core

    raise ScenarioInfeasibleError(
        f"把 {top_wage} 的租金抬了 {max_steps} 次仍未与福利最优分离。"
        "通常是这个人设的 w₁ 太低（几乎不在乎物质），此时房租再高也影响不了他的排序"
    )


def instantiate(tier: Tier, world: World, persona: Persona, tax) -> Scenario:
    """**这就是那个"函数"。** 给定档位、世界、人设，现算出情境。

    同一组输入永远给出同一个输出，所以可复现；而仓库里存的仍然只有互相
    独立的世界列表与人设列表，两条轴保持正交。

    第一、二档不看 persona（纯世界属性），第三、四档看——签名统一是为了
    让调用方不必知道哪档依赖人设。
    """
    core = _core_of(world, tax)
    cities = world.city_ids

    if tier is Tier.DOMINANT:
        winner = dominant_city(world)
        if winner is None:
            # 随机抽出来的世界几乎不会自带帕累托占优的城市，所以这一档
            # 基本都要现造。造完之后 core 要重建。
            world = make_dominant(world, cities[0])
            core = _core_of(world, tax)
            winner = dominant_city(world)
            if winner is None:  # pragma: no cover - make_dominant 保证成立
                raise ScenarioInfeasibleError("构造占优城市之后仍检测不到占优")
        return Scenario(
            tier=tier,
            world_id=world.world_id,
            persona_id=persona.persona_id,
            core=core,
            cities=cities,
            correct_city=winner,
        )

    if tier is Tier.TRADEOFF:
        if dominant_city(world) is not None:
            raise ScenarioInfeasibleError(
                f"{world.world_id} 里存在帕累托占优的城市，构不成权衡档。"
                "换一个世界种子，不要在这里改属性——改了就不是随机抽出来的世界了"
            )
        return Scenario(
            tier=tier,
            world_id=world.world_id,
            persona_id=persona.persona_id,
            core=core,
            cities=cities,
            correct_city=best_city(core, persona, cities),
        )

    if tier is Tier.EXACT_TIE:
        target = best_city(core, persona, cities)
        runner_up = max(
            (c for c in cities if c != target),
            key=lambda c: core.score(persona, c, 2024).ln_utility,
        )
        tied_core, dim, adjusted = make_exact_tie(core, persona, target, runner_up)
        return Scenario(
            tier=tier,
            world_id=world.world_id,
            persona_id=persona.persona_id,
            core=tied_core,
            cities=cities,
            correct_city=None,  # 并列集合里没有唯一正确答案，这是设计
            tied_cities=(target, runner_up),
            tie_dimension=dim,
            adjusted_city=adjusted,
        )

    if tier is Tier.ADVERSARIAL:
        adv = make_adversarial(core, persona, cities)
        return Scenario(
            tier=tier,
            world_id=world.world_id,
            persona_id=persona.persona_id,
            core=adv,
            cities=cities,
            correct_city=best_city(adv, persona, cities),
        )

    raise ValueError(f"未知档位 {tier!r}")  # pragma: no cover
