"""按层抽人设。**人设轴与世界轴分开**（README §11.9.2 的接口约束）。

本模块只产出**一批人**加上层标签与权重。这批人往哪些世界里投，是世界轴的事；
同一批人设可以投到多个世界实例，两条轴不在这里耦合。

抽样顺序是不可商量的：

    1. 生成候选库（只依赖 α 与 seed）
    2. 算 z = ‖w − μ₀‖，按事前边界打层标签
    3. 按配额在层内抽
    4. **然后才**生成选择数据

层标签在第 2 步就冻结。**不得**在拟合之后按 d 或按结果重抽人——那会让层成为
结果的函数，层内条件量随之失去事前含义（硬规则 21）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from econ.types import Persona
from worlds.personas import PersonaGenerator, PersonaSamplingConfig

from .plan import (
    SamplingPlan,
    StratifiedSample,
    StratumRealisation,
    WeightTarget,
    weight_for,
)
from .strata import stratification_variable


def weights_of(persona: Persona) -> np.ndarray:
    """人设的七维真值权重。与 `eval.floors.design.weights_of` 同义。

    这里重新写一遍而不是 import，是为了让 P10 不依赖 P8——依赖方向是
    P8 消费 P10，反过来会成环。
    """
    from worlds.dimensions import AMENITY_DIMENSIONS

    return np.array(
        [persona.params.w_material, *(persona.params.theta[d] for d in AMENITY_DIMENSIONS)]
    )


def draw_stratified(
    plan: SamplingPlan,
    *,
    pool_seed: int,
    config: PersonaSamplingConfig | None = None,
    weight_target: WeightTarget = "superpopulation",
    pool_size: int | None = None,
) -> tuple[list[Persona], StratifiedSample]:
    """按 `plan` 抽一批人设，并连同层标签、权重与溯源信息一起返回。

    `pool_size` 给定时走**有限候选库**：先生成这么多人，再在库内按层抽。库里
    某层不够配额时，**如实少抽并记录**，不从别的层借人——借人会同时破坏层的
    含义与权重口径。

    `pool_size` 为 `None` 时按需生成，直到每层配额都满。
    """
    scheme, alloc = plan.scheme, plan.allocation
    centre = np.asarray(scheme.centre, dtype=float)
    generator = PersonaGenerator(config or PersonaSamplingConfig())

    if pool_size is None:
        pool_size = plan.pool_size
    finite_pool = pool_size is not None

    pool = generator.generate(
        pool_size if finite_pool else _pool_for_quotas(alloc, scheme.population_share),
        seed=pool_seed,
    )
    z = stratification_variable(np.array([weights_of(p) for p in pool]), centre)
    index = scheme.assign(z)

    rng = np.random.default_rng(plan.seed)
    chosen: list[int] = []
    realisations: list[StratumRealisation] = []
    per_stratum: list[list[int]] = [
        list(np.flatnonzero(index == h)) for h in range(scheme.n_strata)
    ]

    for h, label in enumerate(scheme.labels):
        available = per_stratum[h]
        quota = alloc.quotas[h]
        take = min(quota, len(available))
        picked = rng.choice(available, size=take, replace=False) if take else np.empty(0, int)
        chosen.extend(int(i) for i in picked)
        realisations.append(
            StratumRealisation(
                label=label,
                population_share=scheme.population_share[h],
                quota=quota,
                n_realised=take,
                n_available=len(available) if finite_pool else None,
                design_weight=float("nan"),  # 下面用实际人数算，不用配额
                weight_basis="pending",
            )
        )

    n_total = len(chosen)
    pool_total = len(pool) if finite_pool else None
    realisations = [
        _with_weight(r, n_total=n_total, pool_total=pool_total, target=weight_target)
        for r in realisations
    ]

    chosen_sorted = sorted(chosen)
    personas = [pool[i] for i in chosen_sorted]
    ids = tuple(p.persona_id for p in personas)
    weight_by_label = {r.label: r.design_weight for r in realisations}
    stratum_of = {
        p.persona_id: scheme.labels[index[i]] for i, p in zip(chosen_sorted, personas, strict=True)
    }

    sample = StratifiedSample(
        persona_ids=ids,
        stratum_of=stratum_of,
        z={p.persona_id: float(z[i]) for i, p in zip(chosen_sorted, personas, strict=True)},
        weight_of={pid: weight_by_label[stratum_of[pid]] for pid in ids},
        strata=tuple(realisations),
        plan=plan,
        pool_seed=pool_seed,
        unused_pool_ids=tuple(
            pool[i].persona_id for i in range(len(pool)) if i not in set(chosen_sorted)
        )
        if finite_pool
        else (),
    )
    return personas, sample


def _with_weight(
    r: StratumRealisation, *, n_total: int, pool_total: int | None, target: WeightTarget
) -> StratumRealisation:
    from dataclasses import replace

    w, basis = weight_for(
        population_share=r.population_share,
        n_realised=r.n_realised,
        n_total=n_total,
        n_available=r.n_available,
        pool_total=pool_total,
        target=target,
    )
    return replace(r, design_weight=w, weight_basis=basis)


def _pool_for_quotas(alloc: Any, population_share: tuple[float, ...]) -> int:
    """按需生成时要造多大的库才够填满每层配额。

    最紧的一层决定规模：`max_h quota_h / p_h`，再留 60% 余量。宁可多造——
    造人设不花大模型调用，而库不够会静默少抽。
    """
    need = max(
        (q / p if p > 0 else 0.0) for q, p in zip(alloc.quotas, population_share, strict=True)
    )
    return int(np.ceil(need * 1.6)) + len(alloc.quotas)
