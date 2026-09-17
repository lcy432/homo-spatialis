"""诊断一：leisure 拟合异常是不是优化问题。

复用出现异常的那批数据（150 人设 × 每人 3 次选择 × 12 城，长通勤臂，σ=0.05），
当前 ALR + 对角规格，**4 个事先固定、只有初始离散度不同的起点**。
数据、模拟 draws（seed=1, n=200, halton）、优化设置（L-BFGS-B, maxiter=500,
tol=1e-7）全部保持一致。

**同一规格内按似然选解，不按真值回收成绩选解。**

三种离散度分开报，它们是三件不同的事：
  潜空间 sd        sqrt(diag(Σ_a))    拟合出来的 a 空间异质性
  权重空间总体方差  diag(Σ_w)          映回权重空间的总体方差
  个体后验均值 sd   sd_i(ŵ_i)          点估计实际散开多少（被收缩过）
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import dataset_from_choice_observations
from eval.recovery.alr import pack_alr, unpack_alr
from eval.recovery.msl import layout, standard_normal_draws
from eval.recovery.msl_alr import N_RANDOM, _starting_point, alr_objective, fit_mixed_logit_alr
from worlds.attributes import LONG_COMMUTE_ARM_2024
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.selfcheck import simulate_choices

N_PERSONAS, N_TASKS, SIGMA, SEED_BLOCK = 150, 3, 0.05, 7_000
N_DRAWS, FIT_SEED, MAXITER, TOL = 200, 1, 500, 1e-7
BASE = 0  # material
START_SDS = (0.1, 0.5, 1.0, 2.0)  # 事先固定的四个初始离散度（0.5 是现行默认）

LI = ALL_DIMENSIONS.index("leisure")
CI = ALL_DIMENSIONS.index("commute")
# 潜空间坐标顺序 = 去掉基准维之后的六维
LATENT_DIMS = [d for i, d in enumerate(ALL_DIMENSIONS) if i != BASE]
LAT_LI = LATENT_DIMS.index("leisure")
LAT_CI = LATENT_DIMS.index("commute")


def build_data():
    personas = PersonaGenerator().generate(N_PERSONAS, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    worlds = gen.generate_many(N_TASKS, base_seed=SEED_BLOCK, reference_persona=reference_persona())
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, gen.tax()) for w in worlds for p in personas],
        pmap,
        sigma=SIGMA,
        seed=SEED_BLOCK,
    )
    truth = np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )
    return dataset_from_engine(obs), truth


def dataset_from_engine(obs):
    return dataset_from_choice_observations(obs)


def report(fit, ds, truth, covariance, label):
    theta = pack_alr(fit.rho, fit.mu_alr, fit.chol, covariance)
    lay = layout(ds, BASE)
    xi = standard_normal_draws(ds.n_personas, N_DRAWS, N_RANDOM, seed=FIT_SEED)
    _, grad = alr_objective(theta, lay, xi, BASE, covariance, 32)

    latent_sd = np.sqrt(np.diag(fit.sigma_alr))
    pop_var = np.diag(fit.sigma_w)
    w_hat = fit.posterior.weight_mean
    post_sd = w_hat.std(axis=0)
    rmse = np.sqrt(((w_hat - truth) ** 2).mean(axis=0))
    corr = np.array([np.corrcoef(w_hat[:, k], truth[:, k])[0, 1] for k in range(7)])

    return {
        "label": label,
        "loglik": fit.loglik,
        "converged": fit.converged,
        "iters": fit.n_iterations,
        "gradmax": float(np.abs(grad).max()),
        "gradnorm": float(np.linalg.norm(grad)),
        "latent_sd": latent_sd,
        "pop_var": pop_var,
        "post_sd": post_sd,
        "rmse": rmse,
        "corr": corr,
        "sigma_hat": 1.0 / fit.gamma_0,
    }


def show(rows, truth, title):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)
    print(
        f"  {'起点':<12}{'logL':>12}{'收敛':>6}{'迭代':>6}{'|grad|max':>11}{'|grad|':>10}{'σ̂':>9}"
    )
    for r in rows:
        print(
            f"  {r['label']:<12}{r['loglik']:12.4f}{r['converged']!s:>6}{r['iters']:>6}"
            f"{r['gradmax']:11.3e}{r['gradnorm']:10.3e}{r['sigma_hat']:9.4f}"
        )

    best = max(rows, key=lambda r: r["loglik"])
    print(f"\n  按似然选解 -> {best['label']}（logL {best['loglik']:.4f}）")
    spread = max(r["loglik"] for r in rows) - min(r["loglik"] for r in rows)
    print(f"  四个起点的 logL 极差 = {spread:.4f}")

    for dim, lat_i, w_i in (("leisure", LAT_LI, LI), ("commute", LAT_CI, CI)):
        print(f"\n  --- {dim} ---")
        print(
            f"  {'起点':<12}{'潜空间 sd':>12}{'权重空间总体方差':>18}{'个体后验均值 sd':>17}"
            f"{'RMSE':>9}{'corr':>8}"
        )
        for r in rows:
            print(
                f"  {r['label']:<12}{r['latent_sd'][lat_i]:12.4f}{r['pop_var'][w_i]:18.5f}"
                f"{r['post_sd'][w_i]:17.4f}{r['rmse'][w_i]:9.4f}{r['corr'][w_i]:8.3f}"
            )
        print(
            f"  {'真值':<12}{'—':>12}{truth[:, w_i].var():18.5f}{truth[:, w_i].std():17.4f}"
            f"{'—':>9}{'—':>8}"
        )


def main():
    ds, truth = build_data()
    print(
        f"数据：{ds.n_personas} 人设 × {ds.n_tasks // ds.n_personas} 个**不同选择集** × "
        f"{ds.n_rows // ds.n_tasks} 城（不是同情境重复）"
    )

    lay = layout(ds, BASE)
    for covariance in ("diagonal", "full"):
        theta_default, _ = _starting_point(lay, BASE, covariance)
        rho0, mu0, _ = unpack_alr(theta_default, covariance)
        rows = []
        for sd0 in START_SDS:
            start = pack_alr(rho0, mu0, np.diag(np.full(N_RANDOM, sd0)), covariance)
            fit = fit_mixed_logit_alr(
                ds,
                BASE,
                n_draws=N_DRAWS,
                seed=FIT_SEED,
                covariance=covariance,
                maxiter=MAXITER,
                tol=TOL,
                start=start,
            )
            rows.append(report(fit, ds, truth, covariance, f"sd0={sd0}"))
        show(
            rows,
            truth,
            f"ALR + {covariance}：四个固定起点（rho 与 mu_a 相同，只有初始离散度不同）",
        )


if __name__ == "__main__":
    main()
