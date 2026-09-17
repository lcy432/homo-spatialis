"""诊断三：material 总体方差被低估的定位，以及进入 P8 的规格建议。

复用上一轮长通勤臂数据：1000 人设、每人 **12 个不同选择集**、σ=0.05、
种子 8100/8200/8300。数据与模拟 draws 设置固定（n_draws=200, halton, seed=1）。

三个规格：
  ①  ALR + 对角，base = material
  ②  ALR + 对角，base = safety
  ③  ALR + 完全协方差，base = material

四个事先固定的离散度起点（与上一轮相同）：sd0 ∈ {0.1, 0.5, 1.0, 2.0}。
**同一规格内按似然选解**，不按真值回收成绩挑起点；同时记录似然接近时
总体方差是否明显不同。跨规格不凭训练似然判优。

总体协方差一律取 ``AlrFit.sigma_w``——拟合分布在权重空间的抽样矩
（``alr.population_moments``），**不是个体后验均值的样本协方差**。脚本里有
一条运行时断言盯着这个区别。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import dataset_from_choice_observations
from eval.recovery.alr import pack_alr, unpack_alr
from eval.recovery.msl import layout
from eval.recovery.msl_alr import N_RANDOM, _starting_point, fit_mixed_logit_alr
from worlds.attributes import LONG_COMMUTE_ARM_2024
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, dirichlet_covariance, reference_persona
from worlds.selfcheck import simulate_choices

N_PERSONAS, N_TASKS, SIGMA = 1000, 12, 0.05
SEEDS = (8_100, 8_200, 8_300)
N_DRAWS, FIT_SEED, MAXITER, TOL = 200, 1, 500, 1e-7
START_SDS = (0.1, 0.5, 1.0, 2.0)
SPECS = (
    ("(1) ALR+对角 base=material", "material", "diagonal"),
    ("(2) ALR+对角 base=safety", "safety", "diagonal"),
    ("(3) ALR+完全 base=material", "material", "full"),
)
MI = ALL_DIMENSIONS.index("material")


def say(*a):
    print(*a, flush=True)


def build(seed):
    personas = PersonaGenerator().generate(N_PERSONAS, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    worlds = gen.generate_many(N_TASKS, base_seed=seed, reference_persona=reference_persona())
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, gen.tax()) for w in worlds for p in personas],
        pmap,
        sigma=SIGMA,
        seed=seed,
    )
    truth = np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )
    return dataset_from_choice_observations(obs), truth


def fit_one(ds, base_dim, covariance, sd0):
    base = ALL_DIMENSIONS.index(base_dim)
    lay = layout(ds, base)
    theta_default, _ = _starting_point(lay, base, covariance)
    rho0, mu0, _ = unpack_alr(theta_default, covariance)
    start = pack_alr(rho0, mu0, np.diag(np.full(N_RANDOM, sd0)), covariance)
    return fit_mixed_logit_alr(
        ds,
        base,
        n_draws=N_DRAWS,
        seed=FIT_SEED,
        covariance=covariance,
        maxiter=MAXITER,
        tol=TOL,
        start=start,
    )


def metrics(fit, truth, sigma_true_sample):
    w_hat = fit.posterior.weight_mean
    # 口径断言：总体协方差必须来自拟合分布，不能等于个体后验均值的样本协方差
    posterior_cov = np.cov(w_hat, rowvar=False)
    assert not np.allclose(fit.sigma_w, posterior_cov, rtol=1e-3), "总体矩口径被换掉了"
    return {
        "loglik": fit.loglik,
        "converged": fit.converged,
        "iters": fit.n_iterations,
        "sigma_hat": 1.0 / fit.gamma_0,
        "var_ratio": np.diag(fit.sigma_w) / np.diag(sigma_true_sample),
        "frob": float(np.linalg.norm(fit.sigma_w - sigma_true_sample, "fro")),
        "frob_rel": float(
            np.linalg.norm(fit.sigma_w - sigma_true_sample, "fro")
            / np.linalg.norm(sigma_true_sample, "fro")
        ),
        "center_bias": fit.w_center - truth.mean(axis=0),
        "rmse": np.sqrt(((w_hat - truth) ** 2).mean(axis=0)),
        "corr": np.array([np.corrcoef(w_hat[:, k], truth[:, k])[0, 1] for k in range(7)]),
        "post_sd": w_hat.std(axis=0),
    }


def row(label, values, fmt="{:>9.3f}"):
    return f"  {label:<20}" + "".join(fmt.format(v) for v in values)


def main():
    say("维度顺序: " + "".join(f"{d[:8]:>9}" for d in ALL_DIMENSIONS))
    say("Dirichlet 闭式方差: " + "".join(f"{v:>9.4f}" for v in np.diag(dirichlet_covariance())))
    say("")

    for seed in SEEDS:
        t0 = time.time()
        ds, truth = build(seed)
        sig_true = np.cov(truth, rowvar=False)
        say(f"########## 种子 {seed} （建数据 {time.time() - t0:.0f}s，{ds.n_rows} 行）##########")
        say(row("本样本真值方差", np.diag(sig_true), "{:>9.4f}"))

        for title, base_dim, covariance in SPECS:
            runs = []
            for sd0 in START_SDS:
                t1 = time.time()
                fit = fit_one(ds, base_dim, covariance, sd0)
                m = metrics(fit, truth, sig_true)
                m["sd0"] = sd0
                m["secs"] = time.time() - t1
                runs.append(m)

            say(f"\n=== {title} | 种子 {seed} ===")
            say(
                f"  {'起点':<10}{'logL':>13}{'收敛':>6}{'迭代':>6}"
                f"{'σ̂':>9}{'material 方差比':>16}{'秒':>7}"
            )
            for m in runs:
                say(
                    f"  sd0={m['sd0']:<6}{m['loglik']:13.4f}{m['converged']!s:>6}"
                    f"{m['iters']:>6}{m['sigma_hat']:9.4f}{m['var_ratio'][MI]:16.3f}{m['secs']:7.0f}"
                )
            ll = np.array([m["loglik"] for m in runs])
            vr = np.array([m["var_ratio"][MI] for m in runs])
            say(
                f"  logL 极差 {ll.max() - ll.min():.4f}   material 方差比极差 {vr.max() - vr.min():.3f}"
            )

            best = max(runs, key=lambda m: m["loglik"])
            say(f"  按似然选解 -> sd0={best['sd0']}")
            say(row("总体方差 估/真", best["var_ratio"]))
            say(row("中心偏差", best["center_bias"], "{:>9.4f}"))
            say(row("个体 RMSE", best["rmse"], "{:>9.4f}"))
            say(row("个体 corr", best["corr"]))
            say(row("个体后验 sd", best["post_sd"], "{:>9.4f}"))
            say(
                f"  Σ_w Frobenius 误差 {best['frob']:.5f}"
                f"（相对 {best['frob_rel']:.3f}）  σ̂ {best['sigma_hat']:.4f}"
            )
        say("")


if __name__ == "__main__":
    main()
