"""`Σ_w(t)` 的**正式**协议：方案 A —— 共享权重中心，只动 ALR 协方差。

`DECISIONS/0015` 裁决四。旧的"权重均值与协方差一起线性插值"降为**有标签的
历史/实验性协议**，正式入口拒绝它（`PROTOCOL` 进 run 与 floor 签名）。

--------------------------------------------------------------------------
协议
--------------------------------------------------------------------------

沿 t 网格：

    C_a(t) = (1−t)·C_a,S + t·C_a,matched          ALR 空间的协方差路径
    L_t L_t' = C_a(t)
    求 μ_a(t) 使  E[S_b(μ_a(t) + L_t ξ)] = c      只校准六维**位置**

`c` 是共享权重中心，取匹配拟合分布的 `E[S_b(a)]`。**固定的是 `E[w]`，不是
`μ_a`**，也**不是**权重空间协方差的线性插值。

`t = 0` 用的是**实现的注入 ALR 协方差** `C_a,S`（同一份名单、同一基准、ddof=0），
不是 P 的闭式解。这**不意味着**知道精确的真值先验，也**不保证**匹配真值 ALR
均值——`t=0` 的均值被钉在 `c` 上，而 `c` 来自匹配臂。

--------------------------------------------------------------------------
为什么位置校准是凸问题
--------------------------------------------------------------------------

在固定点集 `{ξ_r}` 上定义

    F_t(μ) = mean_r log(1 + Σ_k exp(μ_k + (L_t ξ_r)_k)) − c_nonbase' μ

每一项 `log(1 + Σ_k exp(·))` 是 log-sum-exp 的仿射复合，凸；减去线性项仍凸。
梯度

    ∇F_t(μ) = mean_r softmax_nonbase(μ + L_t ξ_r) − c_nonbase

正是**模拟权重均值减目标**。所以驻点就是"均值命中目标"，而凸性保证它唯一
（严格凸，除非 ξ 退化）。这比直接对 `‖达到均值 − 目标‖²` 做最小二乘好：后者
非凸、有平坦区，优化器会停在别处还报告"收敛"。

--------------------------------------------------------------------------
两条不能省的检查
--------------------------------------------------------------------------

**用独立积分点复核均值。** 求解器在 `{ξ_r}` 上把梯度压到零，只说明它在**那批
点**上命中了目标。换一批点还命中，才说明分布本身命中了。容差跑之前配置并记录。

**失败就标失败。** 数值不收敛、`C_a(t)` 非 PSD、独立点上的均值残差超容差 →
该 t 标 `invalid`，**不发 λ/r**，产物保留。不裁剪、不归一化、不放宽容差。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize

from eval.recovery.alr import weights_from_alr

#: 协议名。进 run 与 floor 签名；正式入口只认这个。
PROTOCOL = "scheme_a_shared_weight_centre"
#: 协议版本。**改变数值含义就要跳版本**，并进缓存/产物的兼容性检查。
#: 缺这个字段的旧产物**不得**默认解释成新协议——见 `protocol_of`。
PROTOCOL_VERSION = "v1"
#: 已降级的旧协议。**正式入口拒绝**，只允许显式标注的历史复现。
LEGACY_PROTOCOL = "joint_mean_covariance_interpolation_experimental"

#: 独立积分点上的均值残差容差（按 `max_k |达到 − 目标|` 的绝对值）。
#: 权重是 O(0.1) 量，报到千分位，所以 2e-3 是"看得见的误差"的量级。
MEAN_TOL = 2e-3
#: 容差要**可分辨**：比较自身的蒙特卡洛噪声必须远小于它。低于这个倍数就说明
#: 这个检查分辨不了声明的容差——那是检查设计的问题，与校准好坏无关，单独报。
NOISE_FLOOR_FACTOR = 3.0
#: 求解器自己的梯度容差，比上面紧一个量级——它只管把驻点找准。
SOLVER_GTOL = 1e-10


class ProtocolMismatchError(RuntimeError):
    """产物/缓存的 t 路径协议与要求的不一致。**拒绝，不降级为警告。**"""


def protocol_of(payload: dict[str, Any]) -> str:
    """读一份产物的 t 路径协议。**缺字段绝不当成新协议。**

    这条是本次审核最该防的那个失败模式的一部分：新模块写好了、测试过了，而
    旧产物因为"没写协议字段"被默认解释成新协议，于是旧数字悄悄获得了新身份。
    缺字段只可能是在协议字段存在之前写的，所以它**一定**是旧协议。
    """
    lp = payload.get("lambda_path", payload)
    name = lp.get("protocol")
    if name is None:
        return LEGACY_PROTOCOL
    # 旧产物把整段描述写在 protocol 里，不是一个名字。
    if name.startswith("DECISIONS/0013"):
        return LEGACY_PROTOCOL
    version = lp.get("protocol_version")
    return f"{name}@{version}" if version else name


def require_formal_protocol(payload: dict[str, Any]) -> str:
    """正式结果入口的守门。**旧协议在这里没有通过的可能。**

    注意这与"旧协议须显式选"不是一回事：历史复现与实验入口可以跑旧协议，
    但**不能靠一个开关让旧协议获得正式结果身份**。所以这里没有 `allow_legacy`
    参数——要跑旧的就别走这个函数。
    """
    got = protocol_of(payload)
    want = f"{PROTOCOL}@{PROTOCOL_VERSION}"
    if got != want:
        raise ProtocolMismatchError(
            f"正式结果要求 t 路径协议 {want}，产物是 {got}。"
            "旧协议的产物可作历史参考，**不能**当作正式结果；"
            "缺协议字段的产物一律按旧协议处理，不默认成新的"
        )
    return got


def softmax_with_base(a: np.ndarray, base: int) -> np.ndarray:
    """`S_b(a)`：在基准维补零后取 softmax。`a` 是 (..., 6)。

    **委托到 `eval.recovery.alr.weights_from_alr`**，不另写一份。本模块曾经有
    过自己的实现，与回收分支在截断上分叉：`a=[60,50,0,0,0,0]`、基准维 0 时
    一个给 `[0.5, 0.5]`、另一个给 `[0.99995, 0.00005]`，差 0.49995。
    分叉不会报错，只会让校准、似然与后验对应不同的模型。
    """
    return weights_from_alr(a, base)


def shared_centre_from_fit(
    mu_alr: np.ndarray, chol: np.ndarray, base: int, *, n_draws: int = 200_000, seed: int = 991
) -> np.ndarray:
    """`c = E[S_b(a)]`，权重空间实算。

    **不能取 `S_b(E[a])`。** softmax 非线性，由 Jensen 两者不等，而且差得很大：
    在注入分布自己的闭式参数上实测，material 维 0.2452 vs 0.5519（真值 0.3333）。
    用错会把中心挪到 0.55，`d_i = ‖w_i − c‖` 全体跟着错。

    也**不能**取个体后验均值的平均来替代——那个量已经被收缩过，是另一件东西。
    """
    xi = np.random.default_rng(seed).standard_normal((n_draws, len(mu_alr)))
    return softmax_with_base(np.asarray(mu_alr) + xi @ np.asarray(chol).T, base).mean(axis=0)


def alr_covariance_path(
    c_a_injected: np.ndarray, c_a_matched: np.ndarray, t_grid: np.ndarray
) -> list[np.ndarray]:
    """`C_a(t) = (1−t)·C_a,S + t·C_a,matched`。**ALR 空间**，不是权重空间。"""
    a = np.asarray(c_a_injected, dtype=float)
    b = np.asarray(c_a_matched, dtype=float)
    return [(1.0 - float(t)) * a + float(t) * b for t in np.asarray(t_grid, dtype=float)]


def _objective(mu: np.ndarray, z: np.ndarray, target_nonbase: np.ndarray, base: int):
    """`F_t(μ)` 与 `∇F_t(μ)`。`z = L_t ξ` 已经算好。"""
    a = mu[None, :] + z
    n = a.shape[1] + 1
    keep = [k for k in range(n) if k != base]
    full = np.zeros((a.shape[0], n), dtype=float)
    full[:, keep] = a
    m = full.max(axis=1, keepdims=True)
    lse = m[:, 0] + np.log(np.exp(full - m).sum(axis=1))
    value = float(lse.mean() - target_nonbase @ mu)
    grad = softmax_with_base(a, base)[:, keep].mean(axis=0) - target_nonbase
    return value, grad


@dataclass(frozen=True)
class PositionCalibration:
    """一个 t 点的位置校准结果。**目标与实际达到分开报。**"""

    t: float
    status: str  # "ok" | "not_psd" | "solver_failed" | "mean_residual_above_tolerance"
    mu_a: np.ndarray | None
    chol: np.ndarray | None
    target_centre: np.ndarray
    achieved_mean_insample: np.ndarray | None
    achieved_mean_holdout: np.ndarray | None
    achieved_covariance: np.ndarray | None
    mean_abs_error_holdout: float
    #: 独立点均值自身的蒙特卡洛标准误（逐维取最大）。**容差必须大于它的几倍**，
    #: 否则这个检查测的是自己的噪声。
    mc_se_holdout: float
    #: 要让这个检查分辨得了声明的容差，独立点至少得有多少个。**噪声地板不是
    #: 死路**：SE 按 `1/√n` 降，所以它总能被算出来，把它报出来才叫可操作。
    n_draws_check_needed: int | None
    n_draws_solve: int
    n_draws_check: int
    protocol: str = PROTOCOL

    @property
    def valid(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        def vec(x):
            return None if x is None else [float(v) for v in x]

        return {
            "t": self.t,
            "status": self.status,
            "protocol": self.protocol,
            "target_centre": vec(self.target_centre),
            "achieved_mean_insample": vec(self.achieved_mean_insample),
            "achieved_mean_holdout": vec(self.achieved_mean_holdout),
            "achieved_variance_diagonal": None
            if self.achieved_covariance is None
            else [float(v) for v in np.diag(self.achieved_covariance)],
            "mean_abs_error_holdout": self.mean_abs_error_holdout,
            "mc_se_holdout": self.mc_se_holdout,
            "n_draws_check_needed": self.n_draws_check_needed,
            "mean_tolerance": MEAN_TOL,
            "tolerance_is_resolvable": self.mean_abs_error_holdout == self.mean_abs_error_holdout
            and NOISE_FLOOR_FACTOR * self.mc_se_holdout <= MEAN_TOL,
            "n_draws_solve": self.n_draws_solve,
            "n_draws_check": self.n_draws_check,
            "note": (
                "只校准位置 μ_a(t)；C_a(t) 由路径给定。均值达成用**独立积分点**复核，"
                "求解器自己那批点上的残差不算数"
            ),
        }


def calibrate_position(
    c_a_t: np.ndarray,
    centre: np.ndarray,
    *,
    base: int,
    t: float,
    n_draws: int = 80_000,
    n_draws_check: int = 500_000,
    seed: int = 4_099,
    check_seed: int = 7_717,
    start: np.ndarray | None = None,
    maxiter: int = 500,
) -> PositionCalibration:
    """求 `μ_a(t)` 使 `E[S_b(μ_a + L_t ξ)] = centre`。

    `start` 传上一个 t 的解做温启动——沿 t 相邻点的解也相邻，这能把迭代数压下来，
    而且**不影响解**（凸问题，唯一驻点）。
    """
    c = np.asarray(centre, dtype=float)
    cov = np.asarray(c_a_t, dtype=float)
    dim = cov.shape[0]
    keep = [k for k in range(dim + 1) if k != base]
    target = c[keep]

    try:
        chol = np.linalg.cholesky(cov + 1e-14 * np.eye(dim))
    except np.linalg.LinAlgError:
        return PositionCalibration(
            t=t,
            status="not_psd",
            mu_a=None,
            chol=None,
            target_centre=c,
            achieved_mean_insample=None,
            achieved_mean_holdout=None,
            achieved_covariance=None,
            mean_abs_error_holdout=float("nan"),
            mc_se_holdout=float("nan"),
            n_draws_check_needed=None,
            n_draws_solve=n_draws,
            n_draws_check=n_draws_check,
        )

    z = np.random.default_rng(seed).standard_normal((n_draws, dim)) @ chol.T
    mu0 = np.asarray(start, dtype=float) if start is not None else np.log(target / c[base])

    res = minimize(
        _objective,
        mu0,
        args=(z, target, base),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": maxiter, "gtol": SOLVER_GTOL, "ftol": 1e-16},
    )
    mu = res.x
    achieved_in = softmax_with_base(mu[None, :] + z, base).mean(axis=0)

    # 独立积分点复核。求解器在自己那批点上把梯度压到零是必然的，说明不了分布。
    z2 = np.random.default_rng(check_seed).standard_normal((n_draws_check, dim)) @ chol.T
    w2 = softmax_with_base(mu[None, :] + z2, base)
    achieved_out = w2.mean(axis=0)
    cov_out = (w2 - achieved_out).T @ (w2 - achieved_out) / w2.shape[0]
    err = float(np.abs(achieved_out - c).max())
    mc_se = float(np.sqrt(np.diag(cov_out) / w2.shape[0]).max())
    # SE ∝ 1/√n，所以"要多少点才够"是可以直接解出来的。
    needed = int(np.ceil(w2.shape[0] * (NOISE_FLOOR_FACTOR * mc_se / MEAN_TOL) ** 2))

    # **求解器自己那批点上的残差说明不了任何事**：凸问题的驻点就是"在那批点上
    # 均值命中目标"，所以它必然接近机器精度（实测 6e-11）。真正的误差是用
    # `n_draws` 个点解出来的 μ̂ 与无穷点解 μ* 之差，它只在**独立**点上才显形，
    # 且随 `n_draws`（不是 `n_draws_check`）下降。实测：20k 解 → 2.3e-3，
    # 80k 解 → 2e-4 量级。
    if not res.success and np.abs(res.jac).max() > 1e-6:
        status = "solver_failed"
    elif NOISE_FLOOR_FACTOR * mc_se > MEAN_TOL:
        status = "tolerance_below_noise_floor"
    elif err > MEAN_TOL:
        status = "mean_residual_above_tolerance"
    else:
        status = "ok"

    return PositionCalibration(
        t=t,
        status=status,
        mu_a=mu,
        chol=chol,
        target_centre=c,
        achieved_mean_insample=achieved_in,
        achieved_mean_holdout=achieved_out,
        achieved_covariance=cov_out,
        mean_abs_error_holdout=err,
        mc_se_holdout=mc_se,
        n_draws_check_needed=needed if needed > w2.shape[0] else None,
        n_draws_solve=n_draws,
        n_draws_check=n_draws_check,
    )


def scheme_a_path(
    c_a_injected: np.ndarray,
    c_a_matched: np.ndarray,
    centre: np.ndarray,
    t_grid: np.ndarray,
    *,
    base: int,
    **kw: Any,
) -> list[PositionCalibration]:
    """整条路径，沿 t 温启动。失败的点照常返回，**标着 status**。"""
    out: list[PositionCalibration] = []
    start: np.ndarray | None = None
    for cov, t in zip(alr_covariance_path(c_a_injected, c_a_matched, t_grid), t_grid, strict=True):
        cal = calibrate_position(cov, centre, base=base, t=float(t), start=start, **kw)
        if cal.mu_a is not None:
            start = cal.mu_a
        out.append(cal)
    return out
