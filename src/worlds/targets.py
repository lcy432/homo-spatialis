"""按目标福利量求解城市属性。P5。

四档难度是**定性**的（占优、权衡、并列、对抗），但门一与问题三要的是**定量**
的东西：

    门一   "在福利离散度高于 τ 的选择集上，L_rep 的中位数是否超过噪声地板"
    问题三 信噪比按选择集的福利离散度分组

没有这个求解器，只能随机生成一堆世界再事后按离散度分组，每个区间的样本量
靠碰运气——而门一是第一个决策点，用一个样本量不受控的判据去决定"项目要不要
重新定位"，风险不对称。这就是 P5 提前到门一之前的理由。

--------------------------------------------------------------------------
它是线性方程组，不是数值求根
--------------------------------------------------------------------------

由 §7.1.1，

    ln V_c = ln κ + w₁·(ln Y_c − ln P_c) + Σ_k θ_k · ln A_k,c

**ln V 对每个 ln A_k 都是线性的**，系数就是 θ_k。所以"把某座城市的 ln V
挪动 δ"就是把它某一维的 ln A 挪动 δ/θ_k——一次除法，不是迭代。残差因此是
浮点噪声，与 §11.3 第三档的精确并列同源。

--------------------------------------------------------------------------
只往下调：可行方向由 A 的值域决定
--------------------------------------------------------------------------

标度值 `A_k ∈ (0,1]`。**上方有硬天花板（1），下方在对数空间无界**（A → 0
时 ln A → −∞）。所以"把某座城市的 ln V 往上抬"随时可能撞顶，"往下压"几乎
总有余地。

两个求解器都据此构造成**只降不升**。这不是数值技巧，它把可行性从"看运气"
变成了结构性的：第一版的间隔求解器往上抬最优城市，在 12 座城市的世界里
目标稍大一点就全维度撞顶。

--------------------------------------------------------------------------
目标量是 (世界, 人设) 的函数，不是世界的属性
--------------------------------------------------------------------------

福利离散度是**这个人**在这批城市上的效用离散度。同一批城市，对偏好不同的
人离散度不同。所以本模块与 difficulty 一样是"存函数不存配对"：给定
(world, persona, target) 现算出一个调整过的核，仓库里不物化任何配对。

这也正是 §1.2 问题三想要的语义——适用边界 τ 是对**某个用户**的判断，系统
在运行时算出当前选择集对他的福利离散度，低于 τ 就如实告知。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from common.ids import CityId
from econ.core import WelfareCore
from econ.types import Persona
from econ.utility import MIN_AMENITY

# 达成值与目标的容差。求解是代数的，残差应当是浮点噪声（1e-16 量级），
# 所以这个阈值宽得离谱是故意的：它不是精度要求，是"有东西悄悄改了我的解"
# 的探测器。
_ACHIEVED_TOL = 1e-9


class TargetInfeasibleError(RuntimeError):
    """在任何维度上都够不到这个目标。

    是错误不是警告：静默地给一个"尽量接近"的世界，会让"离散度 τ 以上的
    选择集"这一组里混进达不到 τ 的样本，而门一的判据正是按这个分组下的。
    """


def _dimensions_by_weight(persona: Persona) -> list[str]:
    """按权重降序。权重大的杠杆大：要挪动 δ 需要改 δ/θ_k，θ_k 越小改得越夸张。"""
    return sorted(
        (d for d in persona.params.theta if persona.params.theta[d] > 0.0),
        key=lambda d: persona.params.theta[d],
        reverse=True,
    )


def _shifted_amenity(
    core: WelfareCore, city: CityId, dim: str, delta_ln_v: float, theta: float
) -> float:
    """把某城某维的标度值改成"使该城 ln V 移动 delta_ln_v"所需的值。

    ln V 对 ln A_k 的偏导恰好是 θ_k，所以 Δ(ln A_k) = Δ(ln V)/θ_k。
    """
    current = core.record(city).amenities_scaled[dim]
    return math.exp(math.log(current) + delta_ln_v / theta)


def _apply(
    core: WelfareCore, moves: dict[CityId, float], dim: str, persona: Persona
) -> WelfareCore:
    """把一组 (城市 -> ln V 位移) 落到某一维上。越界则抛，不夹。

    夹回 [0,1] 会把"精确达成目标"变成"接近目标"，而这一档的整个用途就是
    让离散度成为一个**可控参数**而不是一个观察值。
    """
    theta = persona.params.theta[dim]
    out = core
    for city, delta in moves.items():
        value = _shifted_amenity(out, city, dim, delta, theta)
        # 下界是 MIN_AMENITY 而不是 0：M2 的 _amenities 会做
        # `max(raw, MIN_AMENITY)`，低于它的解会被**静默夹住**，于是求解器
        # 返回一个没打中目标的世界而不报错。第一版就掉进过这个坑。
        if not MIN_AMENITY <= value <= 1.0:
            raise TargetInfeasibleError(
                f"要让 {city} 的 ln V 移动 {delta:+.4f}，{dim!r} 需要标度值 "
                f"{value:.4g}，超出 [{MIN_AMENITY:g}, 1]"
            )
        out = out.with_amenity(city, dim, value)
    return out


def ln_utilities(core: WelfareCore, persona: Persona, cities: Sequence[CityId]) -> dict[str, float]:
    """走 score_set，不另起一条路。两份 ln V 的实现迟早会分叉。"""
    return dict(core.score_set(persona, list(cities), 2024).ln_utility)


def solve_for_ln_gap(
    core: WelfareCore, persona: Persona, cities: Sequence[CityId], target: float
) -> WelfareCore:
    """求解属性，使**最优与次优**的 ln V 差恰好等于 ``target``。

    见模块文档的"只往下调"原则：δ < 0 时压低最优那一座，δ > 0 时压低
    **除最优之外的全部**城市。两种情形都只降不升。

    压全部非最优而不是只压次优：只压次优的话，它可能掉到第三名之下，
    于是"次优"换了人，实际达成的间隔小于 target——而且不会报错。整体
    下压保持它们之间的次序，达成的间隔精确等于 target。

    **代价是离散度会跟着变。** 一个维度只有一个自由度，间隔与离散度不能
    同时钉死。两个求解器因此不可组合，要哪个就调哪个。
    """
    if target <= 0.0:
        raise ValueError(f"目标间隔必须为正，得到 {target!r}；等于零请用第三档的精确并列")

    ln_v = ln_utilities(core, persona, cities)
    ordered = sorted(cities, key=lambda c: ln_v[c.code], reverse=True)
    best = ordered[0]
    delta = target - (ln_v[best.code] - ln_v[ordered[1].code])

    # 只往下调（见模块文档）：δ<0 压低最优那一座，δ>0 压低其余全部。
    # 压全部非最优而不是只压次优——只压次优可能让它掉到第三名之下，于是
    # "次优"换了人，达成的间隔小于 target，而且不会报错。
    moves = {best: delta} if delta < 0.0 else {c: -delta for c in ordered[1:]}

    return _solve_on_best_dimension(
        core,
        persona,
        moves,
        f"目标间隔 {target:.4f}",
        target=target,
        check=lambda c: c.score_set(persona, list(cities), 2024).ln_welfare_gap,
    )


def solve_for_ln_dispersion(
    core: WelfareCore, persona: Persona, cities: Sequence[CityId], target: float
) -> WelfareCore:
    """求解属性，使全部城市 ln V 的标准差恰好等于 ``target``。

    把每座城市相对某个锚点的偏离按 λ = target/当前标准差 缩放：

        新 ln V_c = 锚点 + λ·(ln V_c − 锚点)

    **标准差与锚点无关**：`新_c − 新均值 = λ·(x_c − 均值)`，所以无论锚在
    哪里，SD 都恰好乘 λ。这给了我们一个自由度，用它保证"只往下调"：

        λ > 1（放大离散度） 锚在**最大值**，于是所有城市都往下走
        λ < 1（压缩离散度） 锚在**最小值**，于是所有城市也都往下走

    仿射且 λ > 0，所以**排序完全不变**。这一点对门一很关键：把同一批城市
    按几个不同的 τ 复制出来时，它们只在离散度上不同，不在"谁是正确答案"
    上不同，分组比较就不会混进"题目本身换了"这个因素。
    """
    if target <= 0.0:
        raise ValueError(f"目标离散度必须为正，得到 {target!r}")

    ln_v = ln_utilities(core, persona, cities)
    values = list(ln_v.values())
    mean = sum(values) / len(values)
    current = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    if current <= 0.0:
        raise TargetInfeasibleError("全部城市效用相同，缩放没有支点")

    scale = target / current
    anchor = max(values) if scale > 1.0 else min(values)
    moves = {c: (scale - 1.0) * (ln_v[c.code] - anchor) for c in cities}

    return _solve_on_best_dimension(
        core,
        persona,
        moves,
        f"目标离散度 {target:.4f}（当前 {current:.4f}，需缩放 ×{scale:.2f}）",
        target=target,
        check=lambda c: c.score_set(persona, list(cities), 2024).ln_welfare_dispersion,
    )


def _solve_on_best_dimension(
    core: WelfareCore,
    persona: Persona,
    moves: dict[CityId, float],
    what: str,
    *,
    target: float,
    check,
) -> WelfareCore:
    """按权重降序试各维度，并**验证达成值**。权重大的杠杆大，通常第一个就成。"""
    failures: list[str] = []
    for dim in _dimensions_by_weight(persona):
        try:
            solved = _apply(core, moves, dim, persona)
        except TargetInfeasibleError as exc:
            failures.append(str(exc))
            continue
        achieved = check(solved)
        if abs(achieved - target) > _ACHIEVED_TOL:
            # 兜底：求解是代数的，打不中就说明有别的东西改了解（例如某处
            # 新加的夹取）。宁可报错也不要返回一个"差不多"的世界——本模块
            # 的全部用途就是让目标量成为可控参数。
            failures.append(f"{dim!r} 解出来了但达成 {achieved:.6f} 而非 {target:.6f}")
            continue
        return solved
    raise TargetInfeasibleError(
        f"{what} 在任何维度上都够不到。尝试记录：\n  " + "\n  ".join(failures[:4])
    )


def dispersion_ladder(
    core: WelfareCore,
    persona: Persona,
    cities: Sequence[CityId],
    targets: Sequence[float],
) -> dict[float, WelfareCore]:
    """一组只在福利离散度上不同的选择集。门一与问题三的分组直接用它。

    每一档都从**同一个** core 求解，不是链式地在上一档基础上再调——链式
    会让误差累积，而且"第三档"变成"前两档的函数"，分组就不再是正交的。
    """
    return {t: solve_for_ln_dispersion(core, persona, cities, t) for t in targets}
