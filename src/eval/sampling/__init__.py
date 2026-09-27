"""P10：分层抽样、设计权重、两个报告接口。

依赖方向是 **P8 消费 P10**。本包不 import `eval.floors`。

---

## 一个尚未定义的估计对象（`ESTIMAND_GAP`）

这一条必须在打开分层之前裁决，所以放在包文档的最前面。

P9 的混合 logit 是在实验样本上**未加权**拟合的。它估出来的总体分布
`(μ̂_w, Σ̂_w)` 描述的是**被抽到的那群人**。简单随机抽样下那群人就是注入总体，
两者一致；**一旦按 z 分层并过采样尾层，两者就不一致了**——尾层是"离中心远"的
人，过采样他们会把 `Σ̂_w` 系统性地抬高。

**只给最后的报告加权修不了这件事。** `report_population()` 还原的是报告里的
那个量；它不回头改变似然，也不改变 `Σ̂_w`。说"拟合矩已经按人口校正过了"是错的。

这对本项目的**主证据**有直接后果：§1.2.3 说协方差比之所以干净，是因为分母
`Σ_injected` 有闭式解。那条论证默认分子 `Σ̂_w` 估的是同一个总体。过采样之后
分子估的是设计总体，分母还是注入总体，比值就混进了一个纯抽样设计造成的因子。

**三个可选方向**（都未实现，也都未裁决）：

1. **比例分配**（`Allocation.proportional`）。边际分布不变，权重恒为 1，
   `Σ̂_w` 仍然估注入总体，闭式分母继续成立。代价是放弃尾部过采样，
   `r(d)` 最外一箱的样本量回到碰运气。**本包默认给这一个**，因为它是唯一
   不需要新裁决就能用的方案。
2. **加权似然**（把设计权重放进 MSL 的人设项）。这会改变估计量及其抽样性质，
   属于 P9 数学改动，**本轮明确不做**。
3. **改口径**：承认 `Σ̂_w` 估的是设计总体，把闭式分母换成同一设计下的
   `Σ_design`（可由注入 α 加层配额数值算出）。不改估计器，改的是分母的定义。

**本轮的处置**：实现 1 与报告侧的加权；把 2 与 3 列为待科学裁决；产出里带
`estimand` 字段说明这次的拟合对象是谁。**不静默选一个**。
"""

from __future__ import annotations

from .draw import draw_stratified, weights_of
from .plan import (
    ALLOCATION_TOLERANCE,
    Allocation,
    SamplingPlan,
    StratifiedSample,
    StratumRealisation,
    WeightTarget,
    describe_allocation,
    design_weights,
    weight_for,
)
from .report import (
    population_moments,
    report_conditional,
    report_cross_stratum_relationship,
    report_population,
)
from .strata import (
    DEFAULT_STRATUM_LABELS,
    ReferencePopulation,
    StratumScheme,
    stratification_variable,
)

#: 上面那段文档的机器可读版本，进产出，免得它只活在 docstring 里。
ESTIMAND_GAP: dict[str, object] = {
    "issue": "未加权 MSL 的估计对象是被抽到的那群人，不是注入总体",
    "bites_when": "按 z 分层且非比例分配（过采样尾层）",
    "does_not_bite_when": "简单随机抽样，或按总体比例分配（权重恒为 1）",
    "report_weighting_does_not_fix_it": True,
    "affects": "协方差比的分子（§1.2.3 的主证据）",
    "options": [
        "proportional allocation (implemented, needs no new adjudication)",
        "weighted likelihood (NOT implemented; changes the P9 estimator)",
        "redefine the denominator as Sigma_design (NOT implemented)",
    ],
    "status": "待科学裁决",
}

__all__ = [
    "ALLOCATION_TOLERANCE",
    "DEFAULT_STRATUM_LABELS",
    "ESTIMAND_GAP",
    "Allocation",
    "ReferencePopulation",
    "SamplingPlan",
    "StratifiedSample",
    "StratumRealisation",
    "StratumScheme",
    "WeightTarget",
    "describe_allocation",
    "design_weights",
    "draw_stratified",
    "population_moments",
    "report_conditional",
    "report_cross_stratum_relationship",
    "report_population",
    "stratification_variable",
    "weight_for",
    "weights_of",
]
