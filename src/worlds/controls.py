"""三个对照组（README §11.10）。

对照组不是附加的稳健性检查，它们是**主结果能不能被解读**的前提。命中率
高可以有很多种原因，其中大部分与偏好推断无关：

    属性置换  互换两城属性、保持标签。答案跟属性走还是跟标签走？
              跟标签走意味着模型在用"City 1 大概是个好地方"这类先验。
    无人设    不给人设，测基线选择。若基线命中率已经很高，那说明这批
              世界本身有个众人皆宜的答案，主结果里的命中率不能归给
              "读懂了这个人"。
    打乱人设  人设散文与真值权重随机错配。**这是最狠的一个**：若打乱组
              与匹配组选出同样的城市，说明人设根本没起作用，那么整个
              偏好回收的故事就是假的。

三个都是**变换**，不是新的生成路径。它们作用在已经生成好的世界与人设上，
所以对照组与主组共用同一批底料，差异只来自变换本身。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from common.ids import CityId
from econ.types import CityRecord, Persona, PersonaBrief
from worlds.generator import World


@dataclass(frozen=True)
class SwapRecord:
    """记录属性置换换了哪两座城，供结果解读时对照。"""

    a: CityId
    b: CityId


def permute_attributes(world: World, a: CityId, b: CityId) -> tuple[World, SwapRecord]:
    """互换两座城市的**属性**，保留它们的标签与辖区。

    保留辖区是刻意的：辖区决定税制，跟着属性走的话这就不只是"换属性"，
    而是把两座城市整个对调，标签也就跟着换了，对照就失去意义。
    """
    cities = dict(world.cities)
    ra, rb = cities[a.code], cities[b.code]

    def swapped(target: CityRecord, source: CityRecord) -> CityRecord:
        return CityRecord(
            city=target.city,
            rpp=source.rpp,
            wage_by_quantile=source.wage_by_quantile,
            amenities_scaled=source.amenities_scaled,
            jurisdiction=target.jurisdiction,
            variety_ln_price=source.variety_ln_price,
        )

    cities[a.code] = swapped(ra, rb)
    cities[b.code] = swapped(rb, ra)
    return replace(world, cities=cities), SwapRecord(a=a, b=b)


def strip_persona(persona: Persona) -> Persona:
    """无人设对照：把散文抽空，真值权重原样保留。

    权重必须保留，否则打分没有真值可比。被拿掉的只是**代理能读到的那
    部分**——这正是这个对照要测的：不给任何关于"他是谁"的信息时，模型
    选什么。
    """
    return replace(
        persona,
        brief=PersonaBrief(
            persona_id=persona.persona_id,
            literalness=persona.brief.literalness,
            narrative="",
            dimension_order=(),
        ),
    )


def shuffle_briefs(personas: list[Persona], seed: int) -> list[Persona]:
    """打乱人设：把每个人的散文换成别人的，真值权重不动。

    这是三个对照里最锋利的一个。匹配组与打乱组若选出同样的城市，说明
    散文根本没有进入决策——模型可能只是在读城市属性，或者在用某种与人
    无关的启发式。那种情况下"偏好回收"回收的不是偏好。

    用**导错排**（derangement）而不是普通洗牌：普通洗牌会让一部分人碰巧
    拿回自己的散文，那些样本既不属于匹配组也不属于打乱组，只会稀释对照
    的对比度。
    """
    n = len(personas)
    if n < 2:
        raise ValueError("至少要两个人设才能错配")

    rng = np.random.default_rng(seed)
    while True:
        order = rng.permutation(n)
        if not np.any(order == np.arange(n)):
            break

    return [
        replace(
            p,
            brief=replace(personas[int(j)].brief, persona_id=p.persona_id),
        )
        for p, j in zip(personas, order, strict=True)
    ]
