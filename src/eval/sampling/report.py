"""两个**显式**的报告接口（README §11.9.2）。

    report_population()    人口层面。按设计权重还原到目标总体。
    report_conditional()   层内条件量。在层内计算，不跨层加权。

**刻意做成两个函数，不是一个 `weighted=True/False` 参数。** 参数式接口在半年后
会被误用，而误用不会报错——它只是悄悄报出一个还原到错误总体的数。

**一处常见的过度推广**：条件量不需要权重，是因为它**本来就条件在层上**。这不能
推广成"所有跨层回归都不用加权"。`proj ~ d` 这类回归跨越所有层，样本里各层的
比例是设计出来的而不是总体的；把它当成人口层面的关系就需要交代权重，或者明确
声明报的是"本设计样本上的关系"。`report_conditional` 因此在产出里写明分层变量
与各层实际人数，让读的人能判断这是哪一种。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from .plan import StratifiedSample

#: 一个把 persona_id 映到标量的量（例如美元损失、proj_i、d_i）。
Quantity = Mapping[str, float]


def _aligned(
    sample: StratifiedSample, values: Quantity
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    ids = [p for p in sample.persona_ids if p in values]
    y = np.array([values[p] for p in ids], dtype=float)
    w = np.array([sample.weight_of[p] for p in ids], dtype=float)
    return y, w, ids


def report_population(
    sample: StratifiedSample,
    values: Quantity,
    *,
    statistic: str = "mean",
) -> dict[str, Any]:
    """人口层面的量。**按设计权重还原。**

    例："中位用户每年损失 X 美元"。样本里尾层的比例是设计出来的，不还原就等于
    在报一个"按我们的抽样设计加权过的人口"，那不是任何人关心的总体。
    """
    y, w, _ids = _aligned(sample, values)
    if y.size == 0:
        return {"statistic": statistic, "n": 0, "value": None, "reason": "没有可用观测"}

    if statistic == "mean":
        value = float(np.sum(w * y) / np.sum(w))
    elif statistic == "median":
        value = _weighted_quantile(y, w, 0.5)
    else:
        raise ValueError(f"未知统计量 {statistic!r}")

    return {
        "level": "population",
        "statistic": statistic,
        "value": value,
        "n": int(y.size),
        "weights_applied": True,
        "weight_basis": sorted({s.weight_basis for s in sample.strata}),
        "weight_target": _weight_target(sample),
        "unweighted_for_comparison": float(np.mean(y) if statistic == "mean" else np.median(y)),
        "effective_sample_size": float(np.sum(w) ** 2 / np.sum(w**2)),
    }


def report_conditional(
    sample: StratifiedSample,
    values: Quantity,
    *,
    statistic: str = "mean",
) -> dict[str, Any]:
    """层内条件量。**不加权**，因为它本来就条件在层上。

    产出里写明分层变量是 `z = ‖w − μ₀‖`（抽样变量），**不是** `d = ‖w − ĉ‖`
    （分析变量）。按 z 分的层不等于按 d 的等频分箱；把前者说成后者，`r(d)`
    曲线的每一箱就名不副实。
    """
    y, _w, ids = _aligned(sample, values)
    by_stratum: dict[str, dict[str, Any]] = {}
    for label in (s.label for s in sample.strata):
        idx = [i for i, p in enumerate(ids) if sample.stratum_of[p] == label]
        if not idx:
            by_stratum[label] = {"n": 0, "value": None}
            continue
        v = y[idx]
        by_stratum[label] = {
            "n": int(v.size),
            "value": float(np.mean(v) if statistic == "mean" else np.median(v)),
            "z_range": [
                float(min(sample.z[ids[i]] for i in idx)),
                float(max(sample.z[ids[i]] for i in idx)),
            ],
        }
    return {
        "level": "conditional",
        "statistic": statistic,
        "weights_applied": False,
        "why_no_weights": (
            "层内条件量。**不可推广为『任何跨层回归都不用加权』**——"
            "跨层回归见 report_cross_stratum_relationship，那里必须显式选口径"
        ),
        "stratification_variable": "z = ||w - injection_mean|| (ex-ante sampling variable)",
        "is_not": "d = ||w - fitted_centre|| (post-fit analysis variable)",
        "by_stratum": by_stratum,
    }


def report_cross_stratum_relationship(
    sample: StratifiedSample,
    x: Quantity,
    y: Quantity,
    *,
    weighted: bool,
) -> dict[str, Any]:
    """跨层回归（例如 `proj ~ d`）。**这里必须显式选，因为两种都讲得通。**

    不加权 → 报的是"本设计样本上的关系"。
    加权   → 报的是"目标总体上的关系"。

    与上面两个函数不同，这个必须带参数：跨层关系没有一个"天然正确"的口径，
    藏一个默认值只会让读的人以为有。产出里如实写明选了哪个。
    """
    ids = [p for p in sample.persona_ids if p in x and p in y]
    if len(ids) < 3:
        return {"level": "cross_stratum", "n": len(ids), "slope": None, "reason": "观测太少"}
    xv = np.array([x[p] for p in ids], dtype=float)
    yv = np.array([y[p] for p in ids], dtype=float)
    w = np.array([sample.weight_of[p] for p in ids], dtype=float) if weighted else np.ones(len(ids))

    xm = np.sum(w * xv) / np.sum(w)
    ym = np.sum(w * yv) / np.sum(w)
    var = np.sum(w * (xv - xm) ** 2)
    slope = float(np.sum(w * (xv - xm) * (yv - ym)) / var) if var > 0 else float("nan")
    return {
        "level": "cross_stratum",
        "n": len(ids),
        "slope": slope,
        "intercept": float(ym - slope * xm) if np.isfinite(slope) else None,
        "weights_applied": weighted,
        "estimand": "target population" if weighted else "this design's sample",
    }


def _weight_target(sample: StratifiedSample) -> str:
    bases = {s.weight_basis for s in sample.strata}
    if any("finite_pool" in b for b in bases):
        return "finite_pool"
    if any("superpopulation" in b for b in bases):
        return "superpopulation"
    return "unknown"


def _weighted_quantile(y: np.ndarray, w: np.ndarray, q: float) -> float:
    order = np.argsort(y)
    ys, ws = y[order], w[order]
    cum = np.cumsum(ws) - 0.5 * ws
    cum /= np.sum(ws)
    return float(np.interp(q, cum, ys))


def population_moments(
    sample: StratifiedSample, weights_by_persona: Mapping[str, Sequence[float]]
) -> dict[str, Any]:
    """按设计权重还原的七维权重矩。

    **这只还原报告里的矩，不还原任何拟合出来的矩。** P9 的 MSL 是在实验样本上
    未加权拟合的，它的 Σ̂_w 描述的是**被抽到的那群人**的总体分布。在这里加权
    得到的矩与那个 Σ̂_w 是两个东西，不能互相替代，也不能说"拟合结果已被校正"。
    详见 `eval.sampling.ESTIMAND_GAP`。
    """
    ids = [p for p in sample.persona_ids if p in weights_by_persona]
    if not ids:
        return {"n": 0, "mean": None, "covariance": None}
    w_mat = np.array([list(weights_by_persona[p]) for p in ids], dtype=float)
    ws = np.array([sample.weight_of[p] for p in ids], dtype=float)
    ws = ws / ws.sum()
    mean = ws @ w_mat
    centred = w_mat - mean
    cov = (centred * ws[:, None]).T @ centred / (1.0 - np.sum(ws**2))
    return {
        "n": len(ids),
        "mean": [float(v) for v in mean],
        "covariance": [[float(v) for v in row] for row in cov],
        "weights_applied": True,
        "applies_to": "reported moments only",
        "does_not_apply_to": "the unweighted MSL fit's population moments",
    }


def as_callable(values: Callable[[str], float], ids: Sequence[str]) -> dict[str, float]:
    """小工具：把一个函数变成本模块要的 `{persona_id: value}`。"""
    return {p: float(values(p)) for p in ids}
