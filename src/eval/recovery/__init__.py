"""P9：偏好回收估计器（面板混合 logit，差分法设定）。

--------------------------------------------------------------------------
一分钟用法
--------------------------------------------------------------------------

```python
from eval.recovery import ChoiceDataset, RecoveryConfig, fit_recovery

dataset = ChoiceDataset.build(
    persona_ids=[...],        # 每行一个
    choice_set_ids=[...],
    alternative_ids=[...],
    chosen=[...],             # 每个选择集恰好一个 True
    features=x,               # (n_rows, 7)，列序 = worlds.dimensions.ALL_DIMENSIONS
    strata=None,              # 可选，P10 的层标签
)
result = fit_recovery(dataset, RecoveryConfig(base_dim="material", n_draws=400))
```

要从 M3 的自检数据直接构造，用 ``dataset_from_choice_observations``：它只取
设计矩阵，**刻意丢掉** ``true_coefficients``。

--------------------------------------------------------------------------
两个参数化
--------------------------------------------------------------------------

```python
RecoveryConfig(parameterization="difference_normal")     # 默认，原有分支
RecoveryConfig(parameterization="alr_logistic_normal")   # ALR/logistic-normal
```

|  | `difference_normal` | `alr_logistic_normal` |
|---|---|---|
| 潜空间 | `γ_k = w_k/σ`，正态 | `a_k = log(w_k/w_b)`，正态 |
| `Σw = 1` | 恒成立 | 恒成立 |
| `0 ≤ w ≤ 1` | **不保证**，越界如实报告 | 构造保证，`0 < w < 1` |
| 尺度 | `γ_0` 直接估 | `ρ = log γ_0`，恒正 |
| `Σ_w` | `B·(Σ_γ/γ_0²)·Bᵀ`，闭式 | 权重空间实算的矩，非 delta 近似 |
| 个体后验 | `E[γ|y]` 反解 | `E[w(a)|y]`，**不是** `w(E[a|y])` |
| xlogit 交叉验证 | 有（对角规格） | 无（xlogit 表达不了） |

`RecoveryResult` 的 `mu_gamma`/`sigma_gamma` 装的是**所用参数化的潜空间矩**
（别名 `mu_latent`/`sigma_latent`）；`w_center_hat`、`sigma_w_hat`、`w_hat`
在两个分支下含义完全相同，下游按它们取值即可。

**默认刻意不改。** 换默认估计器是一次科学裁决；证据与待裁决项见
`DECISIONS/0011`。

--------------------------------------------------------------------------
可识别性诊断（M3 那一侧的问题）
--------------------------------------------------------------------------

```python
from eval.recovery import identifiability_report
report = identifiability_report(dataset)
report.flagged(threshold=0.10)     # 阈值必须显式传，没有生产默认
```

`within_sd` 问"这一维在选择集内部动不动"，`unique_sd` 问"它动的部分是不是
能被别的维度解释掉"，切空间奇异谱问"哪个**权重组合**最测不出来"。三者都
基准维无关——条件数不是。名单非空时先修世界，不要先解读 `ŵ`。

**权重合法 ≠ 权重可识别。** ALR 只保证前者。

--------------------------------------------------------------------------
换基准维
--------------------------------------------------------------------------

``RecoveryConfig(base_dim=...)`` 接七个维度名之一。一次跑一组：

```python
from eval.recovery import fit_base_dimension_robustness, base_dimension_spread

results = fit_base_dimension_robustness(dataset, cfg)   # 默认物质 + 两个舒适度维
spread = base_dimension_spread(results)
```

差分不是等距变换，**对角 Σ_γ 不是基准维不变的假设**（§11.7.1.3）。不同基准
维给出不同结果是模型的性质，程序把差异摆出来，不做让它们看齐的调整。

--------------------------------------------------------------------------
怎么读诊断
--------------------------------------------------------------------------

``result.diagnostics``：

- ``converged`` / ``message`` / ``n_iterations``：优化器状态。
- ``gamma_0`` / ``sigma_hat`` / ``gamma_0_warning``：`γ_0 = 1/σ`。它是**结果与
  诊断**，不是地板臂的工作点——地板臂的 σ 由重复子集按分歧率标定（硬规则
  19、25，`DECISIONS/0003`）。本包没有任何把 `sigma_hat` 交给 P8 的路径。
- ``design_condition_number``：组内中心化并列等尺度化之后的条件数。超过 30
  （Belsley-Kuh-Welsch）意味着属性共线到反解不稳，与模型好坏无关。
- ``attribute_within_sd`` / ``weakly_identified_dimensions``：**先看这一条，再看
  条件数。** 条件 logit 只识别组内差异，某一维在选择集内部几乎不动时，它的权重
  根本不是从数据里识别出来的；而 `Σw = 1` 会把那个任意值从别的维度里扣走，于是
  **整个 `ŵ` 都歪掉**，不只是那一维。它抓得到条件数抓不到的情形：把那个近乎常数
  的维度选作基准维，条件数会降到很低，问题却一点没少（此时 `γ_0` 只能从一列几乎
  不动的数里识别）。名单非空时，先修世界的属性生成，不要先解读 `ŵ`。
- ``covariance``：Σ_w 的秩应为 6、半正定、行和为零。这三条是构造上恒成立的，
  实测它们等于在测浮点。
- ``population_simplex`` / ``individual_simplex``：随机系数取正态时不保证
  `0 ≤ w_k ≤ 1`。**越界被报告，不被修正**——clip / project / renormalize 会
  改变估计量本身。越界严重时该由 P8 的数字决定下一步，而不是就地换分布。
- ``posterior_ess_median``：个体后验的有效抽样数。贴近 ``n_draws`` 说明该人设
  的数据几乎没有约束住 γ_i（后验≈先验）。

--------------------------------------------------------------------------
不确定性
--------------------------------------------------------------------------

``block_bootstrap``：按人设重抽（不重抽个体内选择），每个 replicate 重新拟合
整条流水线。下游指标走 ``metric_fn`` 回调——坍缩比例的公式版本由调用方决定，
P9 内部不写死任何一版。
"""

from .alr import (
    ALR_COORD_CAP,
    alr_from_weights,
    population_moments,
    weights_from_alr,
)
from .bootstrap import (
    BootstrapOutcome,
    BootstrapReplicate,
    block_bootstrap,
    default_metrics,
    resample_persona_ids,
    stack_metric,
)
from .design import (
    BaseDimensionError,
    base_index,
    condition_number,
    design_column_names,
    design_matrix,
    differenced,
    differenced_utility,
    other_indices,
    systematic_utility,
    weakly_identified_dimensions,
    within_task_variation,
)
from .estimator import (
    DEFAULT_BASE_DIMS,
    RecoveryConfig,
    base_dimension_spread,
    fit_base_dimension_robustness,
    fit_recovery,
)
from .identifiability import (
    IdentifiabilityReport,
    identifiability_report,
    simplex_tangent_basis,
    unique_variation,
    within_centered,
)
from .inversion import (
    GAMMA0_FLOOR,
    DegenerateScaleError,
    check_gamma_0,
    expansion_matrix,
    is_positive_semidefinite,
    numerical_rank,
    sigma_q_from_sigma_gamma,
    sigma_w_from_sigma_gamma,
    simplex_violation,
    weights_from_gamma,
)
from .msl import (
    MslFit,
    fit_conditional_logit,
    fit_mixed_logit,
    layout,
    standard_normal_draws,
)
from .msl_alr import (
    AlrFit,
    AlrPosterior,
    alr_posterior_at,
    fit_mixed_logit_alr,
)
from .profiles import (
    MULTISTART_SD0,
    RESEARCH_PROFILE_ALR_FULL_MATERIAL,
    ResearchFit,
    ResearchFitError,
    ResearchProfile,
    StartOutcome,
    fit_research,
)
from .results import (
    CovarianceReport,
    RecoveryDiagnostics,
    RecoveryResult,
    SimplexReport,
)
from .schema import (
    ChoiceDataError,
    ChoiceDataset,
    attributes_from_m2_features,
    dataset_from_choice_observations,
)

__all__ = [
    "ALR_COORD_CAP",
    "DEFAULT_BASE_DIMS",
    "GAMMA0_FLOOR",
    "MULTISTART_SD0",
    "RESEARCH_PROFILE_ALR_FULL_MATERIAL",
    "AlrFit",
    "AlrPosterior",
    "BaseDimensionError",
    "BootstrapOutcome",
    "BootstrapReplicate",
    "ChoiceDataError",
    "ChoiceDataset",
    "CovarianceReport",
    "DegenerateScaleError",
    "IdentifiabilityReport",
    "MslFit",
    "RecoveryConfig",
    "RecoveryDiagnostics",
    "RecoveryResult",
    "ResearchFit",
    "ResearchFitError",
    "ResearchProfile",
    "SimplexReport",
    "StartOutcome",
    "alr_from_weights",
    "alr_posterior_at",
    "attributes_from_m2_features",
    "base_dimension_spread",
    "base_index",
    "block_bootstrap",
    "check_gamma_0",
    "condition_number",
    "dataset_from_choice_observations",
    "default_metrics",
    "design_column_names",
    "design_matrix",
    "differenced",
    "differenced_utility",
    "expansion_matrix",
    "fit_base_dimension_robustness",
    "fit_conditional_logit",
    "fit_mixed_logit",
    "fit_mixed_logit_alr",
    "fit_recovery",
    "fit_research",
    "identifiability_report",
    "is_positive_semidefinite",
    "layout",
    "numerical_rank",
    "other_indices",
    "population_moments",
    "resample_persona_ids",
    "sigma_q_from_sigma_gamma",
    "sigma_w_from_sigma_gamma",
    "simplex_tangent_basis",
    "simplex_violation",
    "stack_metric",
    "standard_normal_draws",
    "systematic_utility",
    "unique_variation",
    "weakly_identified_dimensions",
    "weights_from_alr",
    "weights_from_gamma",
    "within_centered",
    "within_task_variation",
]
