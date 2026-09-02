"""人设生成：抽出一千个不同的人。研究模式专用。

与产品模式的关键差异（§11.2）：

    产品模式  一个权重向量，投到 N 座城市。问"这个人该去哪"。
    研究模式  N 个不同的权重向量，投进合成世界。问"模型能不能读懂
              这些人分别是谁"。

这个差异是硬的。权重回收的含义是"我给了你 w_i，你的行为能不能让我读回
ŵ_i"；若所有代理共用同一个 w，只有一个参数点，没有任何东西可以拟合，
测到的全部是采样噪声。

本模块不直接拼 Persona，而是产出 Elicitation，再交给 worlds.elicitation
的 build_persona。这样合成人设与真实用户走同一个构造器，不会分叉。

--------------------------------------------------------------------------
权重抽样：Dirichlet(α=0.5)，不加下限
--------------------------------------------------------------------------

均匀单纯形抽样（等价于 α=1）会产出大量"七维都差不多"的人，而那些人区分度
最低——他们对任何城市都没有强烈意见，从他们的选择里几乎回收不出信息。
α < 1 偏向稀疏，造出有主导维度的人。

**不加下限**是明确的裁决。§6.3 那个"每维至少 3 分"的下限是给**问卷**用的，
防止真实用户把某一维归零；研究模式下我们自己造真值，没有这个约束，而且
稀疏正是我们要的。

代价是会出现权重接近零的维度，而 §11.3 第三档的并列求解器要除以 θ_k，
除零无解。解法不是加下限，而是**第三档挑权重最大的那一维来解并列**——
见 worlds.difficulty。这样每个人设都能构造并列，且用的是对他最有分辨力
的那一维。

另一条与真实用户的差异：§6.3 说真人会给 10、20、30 这样的整数，而
"生成合成人设时不沿用真实用户分布的整数偏好"。Dirichlet 给的是连续值，
正好符合——回收测试需要注入权重有方差，整数锚定会把分布粗化。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from common.ids import OccupationId
from econ.types import Persona
from worlds.attributes import QUANTILES, AttributeCalibration
from worlds.dimensions import ALL_DIMENSIONS
from worlds.elicitation import ConstantSumResponse, Elicitation, build_persona
from worlds.shares import BehaviouralAnswers

# 合成职业。中性编码，不复用真实 SOC，免得模型调用它对某个职业的先验。
SYNTH_OCCUPATIONS: tuple[OccupationId, ...] = tuple(
    OccupationId("SYNTH_OCC", f"O{i}") for i in range(1, 6)
)

# §6.4 的可贸易品三题。V1 全美同值，因此这三列方差为零——白送一个泄漏
# 对照：若模型的选择与零方差列相关，说明估计层在读噪声。
VARIETY_OPTIONS: dict[str, tuple[str, ...]] = {
    "cuisine": ("northern", "southern", "mixed"),
    "car_brand": ("domestic", "imported", "none"),
    "phone_brand": ("brand_a", "brand_b"),
}


@dataclass(frozen=True)
class PersonaSamplingConfig:
    """抽样参数。**这是 CLAUDE.md 里唯一还没最终裁决的一处**，所以每一项
    都做成配置，等实验设计定了再调。"""

    dirichlet_alpha: float = 0.5
    occupations: tuple[OccupationId, ...] = SYNTH_OCCUPATIONS
    quantiles: tuple[int, ...] = QUANTILES
    household_sizes: tuple[int, ...] = (1, 1, 2, 2, 3, 4)  # 权重体现在重复次数上
    tenures: tuple[str, ...] = ("rent", "rent", "own")
    dining_out: tuple[str, ...] = ("rare", "sometimes", "sometimes", "often")
    calibration: AttributeCalibration = field(default_factory=AttributeCalibration)

    def __post_init__(self) -> None:
        if self.dirichlet_alpha <= 0:
            raise ValueError("Dirichlet 的 α 必须为正")


class PersonaGenerator:
    """从一个 seed 出发，确定性地造出一批互不相同的人。"""

    def __init__(self, config: PersonaSamplingConfig | None = None) -> None:
        self.config = config or PersonaSamplingConfig()

    def _reference_income(self, quantile: int) -> float:
        """与城市无关的参照收入，用来查份额（架构修正一）。

        取全国口径的中位工资乘以这个人的能力分位比。**不取任何一座城市的
        税后收入**——那会让份额随城市变，而份额随城市变会同时破坏价格
        指数、κ 的跨城不变性、以及 §11.7 的 logit 设定。
        """
        c = self.config.calibration
        return c.median_wage * c.quantile_ratios[quantile]

    def sample_elicitation(self, rng: np.random.Generator) -> Elicitation:
        """抽一份"问卷答卷"，形状与真实用户填出来的完全一致。"""
        cfg = self.config

        # 定和量表：Dirichlet 抽出七维权重，乘一百变成"分"。
        weights = rng.dirichlet([cfg.dirichlet_alpha] * len(ALL_DIMENSIONS))
        points = {d: float(w * 100.0) for d, w in zip(ALL_DIMENSIONS, weights, strict=True)}

        # 顺序效应（§6.3）：呈现顺序随机化并记录置换。
        presentation = tuple(rng.permutation(list(ALL_DIMENSIONS)))
        # 叙述顺序（架构修正五）：与呈现顺序**独立**再抽一次。
        # 共用同一个置换会在两条本该无关的通道之间造出相关。
        narration = tuple(rng.permutation(list(ALL_DIMENSIONS)))

        quantile = int(rng.choice(cfg.quantiles))
        behavioural = BehaviouralAnswers(
            household_size=int(rng.choice(cfg.household_sizes)),
            tenure=str(rng.choice(cfg.tenures)),
            has_car=bool(rng.random() < 0.78),
            dining_out=str(rng.choice(cfg.dining_out)),
            reference_income=self._reference_income(quantile),
        )

        variety = {k: str(rng.choice(v)) for k, v in VARIETY_OPTIONS.items()}

        return Elicitation(
            constant_sum=ConstantSumResponse(points=points, presentation_order=presentation),
            behavioural=behavioural,
            occupation=cfg.occupations[int(rng.integers(len(cfg.occupations)))],
            ability_quantile=quantile,
            free_text=None,  # 合成人设不走自由文本；那条路服务真实用户
            variety=variety,
            narration_order=narration,
        )

    def generate(self, n: int, seed: int, *, prefix: str = "p") -> list[Persona]:
        """一批人设。同一个 seed 永远给出同一批人。

        narrative 留空：散文由 worlds.narrative 单独生成一次并冻结，不在
        抽样时现造。分开是因为散文要花调用成本，而权重不用——抽样可以
        随便重跑，散文不能。
        """
        rng = np.random.default_rng(seed)
        out: list[Persona] = []
        for i in range(n):
            elicitation = self.sample_elicitation(rng)
            out.append(build_persona(elicitation, persona_id=f"{prefix}{i:05d}"))
        return out


def reference_persona(config: PersonaSamplingConfig | None = None) -> Persona:
    """条件数检查用的参照人设：七维等权、中位分位、居中的行为答案。

    它**不是**一个典型人设，它是一把量尺。用等权是因为条件数要衡量的是
    城市属性之间的共线性，而不是某个人的偏好恰好让某几列失去分辨力；
    等权让每一维都进入设计矩阵。

    这道闸因此是必要非充分的：边缘人设（某几维权重接近零）的实际条件数
    会更差。世界通过了这道闸不等于对每个人设都好用。
    """
    cfg = config or PersonaSamplingConfig()
    even = 100.0 / len(ALL_DIMENSIONS)
    return build_persona(
        Elicitation(
            constant_sum=ConstantSumResponse(points=dict.fromkeys(ALL_DIMENSIONS, even)),
            behavioural=BehaviouralAnswers(
                household_size=2,
                tenure="rent",
                has_car=True,
                dining_out="sometimes",
                reference_income=cfg.calibration.median_wage,
            ),
            occupation=cfg.occupations[0],
            ability_quantile=50,
        ),
        persona_id="reference",
    )
