"""P9 参数化的一次性固定比较（第十三节）。

协议在跑之前定死，跑完不改：
  数据集   合成、条件良好（把估计器的比较与 M3 的 commute 缺陷隔开）
  人设数   150      每人任务数 6（训练）+ 3（留出）      候选数 7
  真值 w   Dirichlet(1.5, 0.5x6)，seed 20260913
  真值 σ   0.08     属性 sd 0.25
  draws    200，halton，seed 2；maxiter 500；tol 1e-7
  基准维   material 与 safety
  四个配置 difference_normal x {diagonal, full}, alr_logistic_normal x {diagonal, full}

留出似然是**总体边际**预测似然：在拟合好的参数下对留出任务积分掉随机系数，
不使用个体后验，因此不存在训练信息泄漏。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import ChoiceDataset, RecoveryConfig, fit_recovery
from eval.recovery.alr import pack_alr
from eval.recovery.msl import (
    N_RANDOM,
    _posterior_weights,
    _simulate,
    layout,
    pack,
    standard_normal_draws,
)
from eval.recovery.msl_alr import _simulate_alr
from worlds.personas import dirichlet_alpha_vector, dirichlet_covariance

N_PERSONAS, N_TRAIN, N_HOLDOUT, N_ALTS = 150, 6, 3, 7
SIGMA_TRUE, ATTR_SD, SEED_DATA = 0.08, 0.25, 20260913
N_DRAWS, SEED_FIT, MAXITER, TOL = 200, 2, 500, 1e-7
BASE_DIMS = ("material", "safety")


def build(weights, n_tasks, rng, tag):
    pid, cid, aid, ch, rows = [], [], [], [], []
    for i in range(len(weights)):
        person = f"p{i:04d}"
        for t in range(n_tasks):
            x = rng.normal(0.0, ATTR_SD, size=(N_ALTS, 7))
            u = x @ weights[i] / SIGMA_TRUE
            p = np.exp(u - u.max())
            p /= p.sum()
            k = int(rng.choice(N_ALTS, p=p))
            for j in range(N_ALTS):
                pid.append(person)
                cid.append(f"{person}-{tag}{t}")
                aid.append(f"city{j}")
                ch.append(j == k)
                rows.append(x[j])
    return ChoiceDataset.build(
        persona_ids=pid,
        choice_set_ids=cid,
        alternative_ids=aid,
        chosen=ch,
        features=np.array(rows, dtype=float),
    )


def holdout_loglik(result, holdout, base_index_value, cfg):
    lay = layout(holdout, base_index_value)
    xi = standard_normal_draws(lay.n_personas, cfg.n_draws, N_RANDOM, seed=cfg.seed)
    fit = result.fit
    if result.parameterization == "difference_normal":
        theta = pack(fit.gamma_0, fit.mu_gamma, fit.chol, cfg.covariance)
        ws, _, _ = _simulate(theta, lay, xi, cfg.covariance, cfg.draw_batch)
    else:
        theta = pack_alr(fit.rho, fit.mu_alr, fit.chol, cfg.covariance)
        ws, _, _, _ = _simulate_alr(
            theta, lay, xi, base_index_value, cfg.covariance, cfg.draw_batch
        )
    return _posterior_weights(ws.ell)[1]


def main():
    rng = np.random.default_rng(SEED_DATA)
    weights = rng.dirichlet(dirichlet_alpha_vector(), size=N_PERSONAS)
    train = build(weights, N_TRAIN, rng, "tr")
    holdout = build(weights, N_HOLDOUT, rng, "ho")

    sigma_true = dirichlet_covariance()
    center_true = weights.mean(axis=0)
    from eval.recovery.design import base_index

    header = (
        f"{'配置':34s} {'σ̂':>8s} {'σ误差%':>7s} {'中心RMSE':>9s} {'对角误差':>9s} "
        f"{'Frob误差':>9s} {'个体corr':>8s} {'越界%':>7s} {'留出LL':>10s} {'收敛':>5s} {'秒':>6s}"
    )
    print(header)
    print("-" * len(header))
    rows = {}
    for par in ("difference_normal", "alr_logistic_normal"):
        for cov in ("diagonal", "full"):
            for bd in BASE_DIMS:
                cfg = RecoveryConfig(
                    base_dim=bd,
                    parameterization=par,
                    covariance=cov,
                    n_draws=N_DRAWS,
                    seed=SEED_FIT,
                    maxiter=MAXITER,
                    tol=TOL,
                )
                t0 = time.time()
                r = fit_recovery(train, cfg)
                elapsed = time.time() - t0
                ho = holdout_loglik(r, holdout, base_index(bd), cfg)
                corr = float(
                    np.mean([np.corrcoef(r.w_hat[:, k], weights[:, k])[0, 1] for k in range(7)])
                )
                diag_err = float(np.abs(np.diag(r.sigma_w_hat) - np.diag(sigma_true)).max())
                frob = float(np.linalg.norm(r.sigma_w_hat - sigma_true, "fro"))
                rmse = float(np.sqrt(np.mean((r.w_center_hat - center_true) ** 2)))
                out = r.diagnostics.individual_simplex.fraction_outside * 100
                key = f"{par}|{cov}|{bd}"
                rows[key] = dict(sigma=r.sigma_hat, center=r.w_center_hat, rmse=rmse, ho=ho)
                print(
                    f"{par[:3]}+{cov[:4]}+{bd:9s}{'':11s}"[:34]
                    + f" {r.sigma_hat:8.4f} {100 * (r.sigma_hat / SIGMA_TRUE - 1):7.1f}"
                    f" {rmse:9.4f} {diag_err:9.5f} {frob:9.5f} {corr:8.3f}"
                    f" {out:7.1f} {ho:10.1f} {r.diagnostics.converged!s:>5s} {elapsed:6.1f}"
                )

    print("\n基准维跨度（同一参数化、同一协方差下两个基准维的差）：")
    for par in ("difference_normal", "alr_logistic_normal"):
        for cov in ("diagonal", "full"):
            a = rows[f"{par}|{cov}|material"]
            b = rows[f"{par}|{cov}|safety"]
            spread = float(np.abs(a["center"] - b["center"]).max())
            print(
                f"  {par:22s} {cov:8s} 中心最大跨度 {spread:.4f}"
                f"  σ̂ {a['sigma']:.4f} vs {b['sigma']:.4f}"
            )

    print(f"\n真值 σ = {SIGMA_TRUE}；注入中心 = {np.round(center_true, 4)}")
    print(f"注入协方差对角 = {np.round(np.diag(sigma_true), 5)}")


if __name__ == "__main__":
    main()
