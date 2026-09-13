"""P9 测试共用的合成选择数据生成器。

生成过程刻意与估计器**两条路独立**：这里用效用的定义式 `Σ w_k x_k` 加 softmax
造数据，估计器走的是似然。同一套思路造数据又估计，自检会同时错在两边而互相
抵消（``worlds.selfcheck`` 的模块文档里有同一条理由）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eval.recovery import ChoiceDataset
from worlds.personas import dirichlet_alpha_vector


@dataclass(frozen=True)
class SyntheticChoices:
    dataset: ChoiceDataset
    true_weights: np.ndarray  # (n_personas, 7)，**只给测试看，不进 dataset**
    sigma: float
    persona_ids: tuple[str, ...]


def make_synthetic(
    *,
    n_personas: int = 60,
    n_tasks: int = 5,
    n_alts: int = 6,
    sigma: float = 0.08,
    seed: int = 0,
    attribute_sd: float = 0.25,
    weights: np.ndarray | None = None,
    strata: bool = False,
) -> SyntheticChoices:
    rng = np.random.default_rng(seed)
    w = (
        rng.dirichlet(dirichlet_alpha_vector(), size=n_personas)
        if weights is None
        else np.asarray(weights, dtype=float)
    )

    pid: list[str] = []
    cid: list[str] = []
    aid: list[str] = []
    chosen: list[bool] = []
    rows: list[np.ndarray] = []

    for i in range(w.shape[0]):
        person = f"p{i:04d}"
        for t in range(n_tasks):
            x = rng.normal(0.0, attribute_sd, size=(n_alts, 7))
            u = x @ w[i] / sigma
            p = np.exp(u - u.max())
            p /= p.sum()
            pick = int(rng.choice(n_alts, p=p))
            for j in range(n_alts):
                pid.append(person)
                cid.append(f"{person}-t{t}")
                aid.append(f"city{j}")
                chosen.append(j == pick)
                rows.append(x[j])

    labels = None
    if strata:
        # 事前可算的分层代理量：到注入均值的距离（硬规则 21）。这里只要两层。
        from worlds.personas import dirichlet_mean

        d = np.linalg.norm(w - dirichlet_mean(), axis=1)
        cut = float(np.median(d))
        labels = {f"p{i:04d}": ("far" if d[i] > cut else "near") for i in range(w.shape[0])}

    dataset = ChoiceDataset.build(
        persona_ids=pid,
        choice_set_ids=cid,
        alternative_ids=aid,
        chosen=chosen,
        features=np.array(rows, dtype=float),
        strata=labels,
    )
    return SyntheticChoices(
        dataset=dataset, true_weights=w, sigma=sigma, persona_ids=dataset.persona_order
    )
