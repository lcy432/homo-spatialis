"""P8 的参考设计与地板臂数据构造。零大模型调用。

--------------------------------------------------------------------------
参考设计是地板的一部分，不是背景信息
--------------------------------------------------------------------------

README §10.3：自检必须过"与主实验**完全相同**的估计器与完全相同的样本规模，
否则收缩幅度不同、地板不可比"。所以人数、每人**不同选择集**数、候选数、世界
profile、噪声来源、拟合配置全部进指纹，复用地板之前逐项比对，不匹配就拒绝。

**"每人 12 个不同选择集"不等于"同人同世界重复 12 次"。** 前者是本设计的
`n_choice_sets`，后者是 §11.8 的重复子集（150 人 × 3 次），两条预算线互不替代。
旧文档里的 5–8 次是**先前敏感性证据**（`runs/2026-09-16-p9-evidence/`），不是
当前地板的设计点。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from econ.types import Persona, PersonaParams
from eval.recovery import ChoiceDataset, dataset_from_choice_observations
from worlds.attributes import CALIBRATIONS, AttributeCalibration
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, PersonaSamplingConfig, reference_persona
from worlds.selfcheck import simulate_choices


@dataclass(frozen=True)
class ReferenceDesign:
    """P8 与主实验共用的参考设计。**改任何一项都要重新匹配地板。**"""

    n_personas: int = 1000
    n_choice_sets: int = 12  # 不同选择集数；**不是**同情境重复次数
    n_cities: int = 12
    world_calibration: str = "LONG_COMMUTE_ARM_2024"
    tier: str = "TRADEOFF"
    persona_seed: int = 20260906
    world_seed: int = 8_100

    def calibration(self) -> AttributeCalibration:
        return CALIBRATIONS[self.world_calibration]

    def fingerprint(self) -> dict[str, Any]:
        return {
            "n_personas": self.n_personas,
            "n_choice_sets_per_persona": self.n_choice_sets,
            "n_cities": self.n_cities,
            "world_calibration": self.world_calibration,
            "tier": self.tier,
            "persona_seed": self.persona_seed,
            "world_seed": self.world_seed,
            "choice_sets_are_distinct_worlds": True,
            "repeat_subset_is_separate": True,
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.fingerprint(), sort_keys=True).encode()).hexdigest()[
            :16
        ]


def weights_of(persona: Persona) -> np.ndarray:
    """人设的七维真值权重，按 ``ALL_DIMENSIONS`` 排。"""
    return np.array(
        [persona.params.w_material, *(persona.params.theta[d] for d in AMENITY_DIMENSIONS)]
    )


def persona_with_weights(persona: Persona, w: np.ndarray) -> Persona:
    """把一组注入权重装回人设，走 §7.2 的两通道映射。

    份额与能力分位原样保留，所以 M2 的七维属性 `x` 一位不变——只有权重变了。
    这正是 P8 的高/低方差校准案例需要的：同一批世界、同一批特征，只换注入分布。
    """
    w = np.asarray(w, dtype=float)
    if abs(w.sum() - 1.0) > 1e-9 or np.any(w < 0):
        raise ValueError(f"注入权重必须非负且和为一，得到 sum={w.sum()!r}")
    s = persona.shares
    w1 = float(w[0])
    return replace(
        persona,
        params=PersonaParams(
            alpha=w1 * (s.s_goods + s.s_other),
            beta=w1 * s.s_rent,
            theta={d: float(v) for d, v in zip(AMENITY_DIMENSIONS, w[1:], strict=True)},
        ),
    )


@dataclass(frozen=True)
class FloorArm:
    """一条地板臂：注入权重 + 由真值 softmax 生成的面板选择。"""

    dataset: ChoiceDataset
    injected_weights: np.ndarray  # (n_personas, 7)，按 persona_order 对齐
    persona_ids: tuple[str, ...]
    sigma_generating: float  # **输入**，不是拟合出来的 σ̂
    design: ReferenceDesign
    source: str = "synthetic_truth_softmax"  # 与真实大模型臂区分开


def build_floor_arm(
    design: ReferenceDesign,
    *,
    sigma: float,
    injected_alpha: np.ndarray | None = None,
    choice_seed: int | None = None,
) -> FloorArm:
    """直接由注入权重与规范城市特征生成面板选择。**零大模型调用。**

    ``injected_alpha`` 给出 Dirichlet 参数；``None`` 表示用主实验的注入分布
    （`DECISIONS/0006` 的 α = (1.5, 0.5×6)）。校准案例通过缩放 α 改变离散度而
    **不改变理论均值**，因为 Dirichlet 的均值只依赖 α 的比例。
    """
    generator = PersonaGenerator(PersonaSamplingConfig(calibration=design.calibration()))
    personas = generator.generate(design.n_personas, seed=design.persona_seed)

    if injected_alpha is not None:
        alpha = np.asarray(injected_alpha, dtype=float)
        rng = np.random.default_rng(design.persona_seed + 977)
        personas = [persona_with_weights(p, rng.dirichlet(alpha)) for p in personas]

    world_gen = WorldGenerator(calibration=design.calibration(), n_cities=design.n_cities)
    worlds = world_gen.generate_many(
        design.n_choice_sets, base_seed=design.world_seed, reference_persona=reference_persona()
    )
    tax = world_gen.tax()
    tier = Tier[design.tier]
    pmap = {p.persona_id: p for p in personas}
    obs = simulate_choices(
        [instantiate(tier, w, p, tax) for w in worlds for p in personas],
        pmap,
        sigma=sigma,
        seed=design.world_seed if choice_seed is None else choice_seed,
    )
    dataset = dataset_from_choice_observations(obs)
    order = {p.persona_id: i for i, p in enumerate(personas)}
    injected = np.array([weights_of(personas[order[pid]]) for pid in dataset.persona_order])
    return FloorArm(
        dataset=dataset,
        injected_weights=injected,
        persona_ids=dataset.persona_order,
        sigma_generating=sigma,
        design=design,
    )


def build_shuffled_arm(design: ReferenceDesign, *, sigma: float, permutation_seed: int) -> FloorArm:
    """打乱臂：**只破坏人设与真值权重的配对**，不重排属性列或城市标签。

    第 i 个人设的**选择**由 `w_{π(i)}` 生成，而 `proj` 仍以 `w_i` 为真值。份额
    与能力分位不动，所以七维属性 `x` 一位没变——重排属性或标签会伪造出一个
    看起来更强的对照，那不是 §11.10 说的那个臂。
    """
    generator = PersonaGenerator(PersonaSamplingConfig(calibration=design.calibration()))
    personas = generator.generate(design.n_personas, seed=design.persona_seed)
    truth = np.array([weights_of(p) for p in personas])

    rng = np.random.default_rng(permutation_seed)
    perm = rng.permutation(len(personas))
    scrambled = [persona_with_weights(p, truth[perm[i]]) for i, p in enumerate(personas)]

    world_gen = WorldGenerator(calibration=design.calibration(), n_cities=design.n_cities)
    worlds = world_gen.generate_many(
        design.n_choice_sets, base_seed=design.world_seed, reference_persona=reference_persona()
    )
    tax = world_gen.tax()
    tier = Tier[design.tier]
    pmap = {p.persona_id: p for p in scrambled}
    obs = simulate_choices(
        [instantiate(tier, w, p, tax) for w in worlds for p in scrambled],
        pmap,
        sigma=sigma,
        seed=design.world_seed + 31,
    )
    dataset = dataset_from_choice_observations(obs)
    order = {p.persona_id: i for i, p in enumerate(personas)}
    injected = np.array([truth[order[pid]] for pid in dataset.persona_order])
    return FloorArm(
        dataset=dataset,
        injected_weights=injected,  # 未打乱的真值，proj 以它为准
        persona_ids=dataset.persona_order,
        sigma_generating=sigma,
        design=design,
        source="synthetic_shuffled",
    )


def dimension_names() -> tuple[str, ...]:
    return ALL_DIMENSIONS
