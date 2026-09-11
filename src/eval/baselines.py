"""三条零调用基准线与噪声地板。门一（§9.1.1、§9.1.1.1）。

    c*     = argmax_c EV(c | s_i, w_i)                    真值最优
    w_mat  = (1, 0, 0, 0, 0, 0, 0)                        全部权重压在物质维

    L_rep  = EV(c*) − EV( argmax_c EV(c | s̄  , w_mat) )   现状工具
    L_s    = EV(c*) − EV( argmax_c EV(c | s̄  , w_i  ) )   只错篮子
    L_amen = EV(c*) − EV( argmax_c EV(c | s_i , w_mat) )  只丢舒适度

**硬规则 13：EV 永远在真值参数下评价，近似参数只用于 argmax。** 用近似参数
同时做选择和评价，算出的是自洽的胡话而不是损失——那个版本跑起来完全正常，
每个数看着都合理，只是它回答的不是"这个人少拿了多少钱"。

`L_s + L_amen ≠ L_rep`（argmax 不可加）。三项分别报，不要相加。

--------------------------------------------------------------------------
把 ln V 拆开，扰动就不用重算整个世界
--------------------------------------------------------------------------

    ln V_c = ln κ(params) + w₁·(ln Y_c − ln P_c) + Σ_k θ_k · ln A_k,c

括号里那两项与 **params 无关**：`ln Y_c` 只依赖能力分位，`ln P_c` 只依赖
份额，`ln A_k,c` 只依赖城市。所以对固定的 (人设, 世界)，只要**不动份额**，
它们在所有扰动之间是常数。

于是噪声地板的 32 次扰动退化成 32 次点积，而不是 32 次重新打分。这不是
近似，是同一个公式换个写法——而且 `m_c` 与 `a_kc` 直接取自 M2 的
`GroundTruth.features`，没有第二套特征实现（MIGRATION B3）。

份额变了的那几条（`L_rep`、`L_s` 用 s̄）必须重新打分，因为 `ln P_c` 变了。
那是每人每档一次，不是每次扰动一次。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from common.ids import CityId
from econ.core import WelfareCore
from econ.types import Persona, PersonaParams
from econ.utility import FEATURE_LN_P, FEATURE_LN_Y, amenity_feature_name, welfare_loss


@dataclass(frozen=True)
class LinearForm:
    """(人设, 世界) 上 ln V 的与参数无关的部分。

    ``material`` 是每座城市的 `ln Y_c − ln P_c`，``amenity`` 是 `ln A_k,c`，
    行序与 ``cities`` 一致、列序与 ``dimensions`` 一致。
    """

    cities: tuple[CityId, ...]
    dimensions: tuple[str, ...]
    material: np.ndarray  # (城市数,)
    amenity: np.ndarray  # (城市数, 维数)

    def ln_v_minus_kappa(self, w_material: float, theta: np.ndarray) -> np.ndarray:
        """ln V − ln κ。argmax 只需要它，因为 κ 跨城不变（M2 断言三）。"""
        return w_material * self.material + self.amenity @ theta

    def ln_money_metric(self, w_material: float, theta: np.ndarray) -> np.ndarray:
        """ln e = (ln V − ln κ)/w₁。参照点 P=1、A=1，单位是美元/年。"""
        return self.ln_v_minus_kappa(w_material, theta) / w_material


def linear_form(core: WelfareCore, persona: Persona, cities: list[CityId]) -> LinearForm:
    """从 M2 的 features 取出与参数无关的部分。不另起一套特征实现。"""
    dims = tuple(persona.params.theta)
    truths = [core.score(persona, c, 2024) for c in cities]
    material = np.array(
        [t.features[FEATURE_LN_Y] - t.features[FEATURE_LN_P] for t in truths], dtype=float
    )
    amenity = np.array(
        [[t.features[amenity_feature_name(d)] for d in dims] for t in truths], dtype=float
    )
    return LinearForm(tuple(cities), dims, material, amenity)


def theta_vector(params: PersonaParams, dimensions: tuple[str, ...]) -> np.ndarray:
    return np.array([params.theta[d] for d in dimensions], dtype=float)


def material_only_params(shares) -> PersonaParams:
    """w_mat = (1, 0, …, 0)：全部权重压在物质维，六个舒适度维全零。

    这是现状生活成本计算器的参数（§1.2 问题二）：它**没有偏好权重维度**，
    只按实际收入排序。α 与 β 的内部劈分仍由份额决定（§7.2 通道一），因为
    那是行为揭示的，与"有没有偏好维度"无关。
    """
    return PersonaParams(
        alpha=shares.s_goods + shares.s_other,
        beta=shares.s_rent,
        theta=dict.fromkeys(
            ("safety", "healthcare", "opportunity", "leisure", "environment", "commute"), 0.0
        ),
    )


def with_shares(persona: Persona, shares) -> Persona:
    """换份额，保持 w₁ 与 θ 不变（§7.2 两通道各自独立）。"""
    w1 = persona.params.w_material
    return replace(
        persona,
        shares=shares,
        params=PersonaParams(
            alpha=w1 * (shares.s_goods + shares.s_other),
            beta=w1 * shares.s_rent,
            theta=dict(persona.params.theta),
        ),
    )


def _loss_against_truth(
    truth_form: LinearForm, persona: Persona, chosen_index: int
) -> float | None:
    """EV(真值最优) − EV(实选)，两端都在**真值参数**下评价（硬规则 13）。"""
    theta = theta_vector(persona.params, truth_form.dimensions)
    ln_e = truth_form.ln_money_metric(persona.params.w_material, theta)
    return welfare_loss(float(ln_e.max()), float(ln_e[chosen_index]))


@dataclass(frozen=True)
class PersonaBaselines:
    """一个人设在一个选择集上的三条基准线加噪声地板。

    地板有两个口径，两个都报（DECISIONS/0010）：

        noise_floor          人设内**均值**。规范口径。
        noise_floor_median   人设内中位数。原预注册口径，一并报告。

    均值恒等于 ``flip_probability × conditional_loss``：argmax 不翻转时
    损失严格为零，所以 `E[损失] = P(翻转)·E[损失|翻转]`。拆开报是因为
    "问卷噪声有 p 的概率改变你的推荐，改变时平均代价 X 美元"比一个合成
    的数更能说明发生了什么。
    """

    persona_id: str
    l_rep: float | None
    l_s: float | None
    l_amen: float | None
    noise_floor: float | None  # 人设内均值（规范）
    noise_floor_median: float | None  # 人设内中位数（原判据）
    flip_probability: float
    conditional_loss: float | None  # E[损失 | 翻转]
    n_perturbations: int
    argmax_true: str
    argmax_rep: str


def baselines_for(
    core: WelfareCore,
    persona: Persona,
    cities: list[CityId],
    representative_shares,
    *,
    perturbations: np.ndarray,
) -> PersonaBaselines:
    """一个人设在一个选择集上的三条基准线加噪声地板。

    ``perturbations`` 是 (次数, 7) 的**分**位移矩阵，第一列是物质维，
    与 worlds.dimensions.ALL_DIMENSIONS 同序。份额不动，所以真值那套
    LinearForm 可以直接复用（见模块文档）。
    """
    truth = linear_form(core, persona, cities)
    theta_true = theta_vector(persona.params, truth.dimensions)
    w1_true = persona.params.w_material
    ln_e_true = truth.ln_money_metric(w1_true, theta_true)
    best = int(np.argmax(ln_e_true))

    # --- 三条基准线。份额变了的两条要重新打分，因为 ln P 变了。---
    rep_persona = with_shares(persona, representative_shares)
    rep_form = linear_form(core, rep_persona, cities)

    def argmax_with(form: LinearForm, params: PersonaParams) -> int:
        return int(
            np.argmax(
                form.ln_v_minus_kappa(params.w_material, theta_vector(params, form.dimensions))
            )
        )

    i_rep = argmax_with(rep_form, material_only_params(representative_shares))
    i_s = argmax_with(rep_form, rep_persona.params)
    i_amen = argmax_with(truth, material_only_params(persona.shares))

    # --- 噪声地板。份额不动，所以复用 truth 的线性形式。---
    floors: list[float] = []
    for delta_points in perturbations:
        pts = np.array([w1_true, *theta_true]) * 100.0 + delta_points
        if pts.sum() <= 0 or (pts < 0).any():
            continue
        w = pts / pts.sum()
        idx = int(np.argmax(truth.ln_v_minus_kappa(float(w[0]), w[1:])))
        loss = welfare_loss(float(ln_e_true.max()), float(ln_e_true[idx]))
        if loss is not None:
            floors.append(loss)

    values = np.asarray(floors, dtype=float)
    flipped = values > 0.0
    return PersonaBaselines(
        persona_id=persona.persona_id,
        l_rep=_loss_against_truth(truth, persona, i_rep),
        l_s=_loss_against_truth(truth, persona, i_s),
        l_amen=_loss_against_truth(truth, persona, i_amen),
        noise_floor=float(values.mean()) if len(values) else None,
        noise_floor_median=float(np.median(values)) if len(values) else None,
        flip_probability=float(flipped.mean()) if len(values) else 0.0,
        conditional_loss=float(values[flipped].mean()) if flipped.any() else None,
        n_perturbations=len(values),
        argmax_true=cities[best].code,
        argmax_rep=cities[i_rep].code,
    )
