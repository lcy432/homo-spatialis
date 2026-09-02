"""估计器自检的数据源（README §10.3）。

> 在调用任何语言模型之前，先用真值模型生成的合成选择数据跑一遍回收流程，
> 确认估计器在已知答案上是无偏的。跳过这一步，后面将无法区分"模型错了"
> 与"尺子错了"。

这句话对 M3 的意思是：M3 必须能产出"世界 + 人设 + **由真值 logit 模拟出
的选择**"。本模块只负责造数据，**不做估计**——条件 logit 与混合 logit 的
核心数学由人手写第一版（CLAUDE.md 工作方式），M5 消费这里的输出。

分工的理由不只是流程：如果造数据和估计出自同一套代码的同一个人同一次
思路，自检就可能同时错在两边而互相抵消。造数据用的是效用的定义式，估计
用的是似然，两条路独立，对上了才说明估计器是对的。

--------------------------------------------------------------------------
σ 在这里是什么
--------------------------------------------------------------------------

模拟选择需要一个随机选择规则 P(j) ∝ exp(U_j/σ)。§11.6 说 σ 没有客观取值，
这在**主指标**上被第三档的精确并列绕开了——并列时任何 σ 都给均分。

但自检不一样：它要的不是"应有的分散度"，是"给定一个已知的数据生成过程，
估计器能不能把参数捞回来"。σ 在这里只是数据生成过程的一个已知参数，
自己定一个就行，因为**我们同时知道 σ 和 β，两个都是自己写进去的**。
真正要检验的是：估出来的 β̂ 是否等于写进去的 β。

所以本模块的 σ 与 §11.6 那个 σ 是两回事，不要混。这里的是"我造数据时
用了多大噪声"，那里的是"真值模型下应有多分散"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from econ.types import Persona
from worlds.difficulty import Scenario


@dataclass(frozen=True)
class ChoiceObservation:
    """一个人在一个备选集上的一次选择，外加它的设计矩阵与真值系数。

    ``design`` 的行序与 ``city_codes`` 一致，列序与 ``columns`` 一致。
    这三样必须一起传，因为列顺序错位是一种不会报错的失败：估出来的
    系数会安静地对应到错误的维度上。
    """

    persona_id: str
    world_id: str
    tier: str
    city_codes: tuple[str, ...]
    columns: tuple[str, ...]
    design: np.ndarray  # (备选数, 特征数)
    chosen_index: int
    true_coefficients: np.ndarray  # (特征数,)
    sigma: float


def scenario_design(
    scenario: Scenario, persona: Persona
) -> tuple[tuple[str, ...], tuple[str, ...], np.ndarray, np.ndarray]:
    """从一个情境取出 (城市码, 列名, 设计矩阵, 真值系数)。

    直接用 M2 的 GroundTruth.features 与 .coefficients，不另起一套表示。
    这与 §11.6 要求"并列在特征空间也精确"是同一条纪律。
    """
    truths = [scenario.core.score(persona, c, 2024) for c in scenario.cities]
    columns = tuple(sorted(truths[0].features))
    design = np.array([[t.features[c] for c in columns] for t in truths], dtype=float)
    coefficients = np.array([truths[0].coefficients[c] for c in columns], dtype=float)
    return tuple(c.code for c in scenario.cities), columns, design, coefficients


def choice_probabilities(design: np.ndarray, coefficients: np.ndarray, sigma: float) -> np.ndarray:
    """真值模型下的选择概率。P(j) ∝ exp(Σβx / σ)。

    用 Σβx 而不是 ln V：两者只差一个 ln κ，而 κ 跨城不变（M2 的断言三），
    在 softmax 里被约掉。写成 Σβx 是为了让"这就是条件 logit 的线性指数"
    这件事在代码上看得见——M5 要拟合的正是这个量。
    """
    if sigma <= 0:
        raise ValueError(f"σ 必须为正，得到 {sigma!r}")
    index = design @ coefficients / sigma
    index -= index.max()  # 数值稳定，不改变概率
    weights = np.exp(index)
    return weights / weights.sum()


def simulate_choices(
    scenarios: list[Scenario],
    personas: dict[str, Persona],
    *,
    sigma: float = 0.05,
    seed: int = 0,
) -> list[ChoiceObservation]:
    """按真值 logit 抽出每个人的选择。**零调用成本。**

    这是 §10.3 那句"在调用任何语言模型之前"的实现：整条回收流程可以在
    一个 token 都不花的情况下跑通并验证。

    σ 的默认值 0.05 相对 ln V 的跨城差异（通常 0.05–0.3）算是小噪声，
    于是选择大体跟着真值走但不是确定性的——正好是估计器该能处理的情形。
    调大 σ 会让选择更随机，是检验估计器在弱信号下表现的旋钮。
    """
    rng = np.random.default_rng(seed)
    out: list[ChoiceObservation] = []

    for scenario in scenarios:
        persona = personas[scenario.persona_id]
        codes, columns, design, coefficients = scenario_design(scenario, persona)
        probs = choice_probabilities(design, coefficients, sigma)
        chosen = int(rng.choice(len(codes), p=probs))
        out.append(
            ChoiceObservation(
                persona_id=scenario.persona_id,
                world_id=scenario.world_id,
                tier=scenario.tier.name,
                city_codes=codes,
                columns=columns,
                design=design,
                chosen_index=chosen,
                true_coefficients=coefficients,
                sigma=sigma,
            )
        )
    return out


def stack_for_estimation(
    observations: list[ChoiceObservation],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...]]:
    """把观测摞成 M5 要的形状：(设计张量, 选中索引, 真值系数矩阵, 列名)。

    要求所有观测的列名与备选数一致——不一致就抛，不做对齐。悄悄对齐是
    另一种"不会报错的失败"：列错位之后估出来的系数安静地挂到错误的维度
    上，而每一个数看上去都很合理。
    """
    if not observations:
        raise ValueError("没有观测")

    columns = observations[0].columns
    n_alt = len(observations[0].city_codes)
    for obs in observations:
        if obs.columns != columns:
            raise ValueError(f"列名不一致：{obs.columns} 与 {columns}")
        if len(obs.city_codes) != n_alt:
            raise ValueError(f"备选数不一致：{len(obs.city_codes)} 与 {n_alt}")

    design = np.stack([o.design for o in observations])
    chosen = np.array([o.chosen_index for o in observations], dtype=int)
    truth = np.stack([o.true_coefficients for o in observations])
    return design, chosen, truth, columns


def hit_rate_of_truth(observations: list[ChoiceObservation]) -> float:
    """真值模型自己的命中率。**这是命中率的上界，不是 100%。**

    有了 σ 的随机性，连真值模型自己都不会每次都选中 argmax。语言模型的
    命中率要跟这个数比，而不是跟 1 比——拿 1 当基准会把 σ 带来的不可避免
    的失误算到模型头上。
    """
    hits = 0
    for obs in observations:
        best = int(np.argmax(obs.design @ obs.true_coefficients))
        hits += int(obs.chosen_index == best)
    return hits / len(observations)
