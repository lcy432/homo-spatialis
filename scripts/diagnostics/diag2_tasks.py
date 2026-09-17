"""诊断二：正式规模下每人需要多少**不同选择集**。

1000 人设，σ=0.05，3 个事先固定的种子，每人 6 / 8 / 12 次选择。

**前缀结构**：每个种子先生成 12 个世界并一次性模拟出全部 12 次选择，6 / 8 次
是同一套记录的**前缀**（按世界过滤行，不重新模拟）。因此三档之间人设相同、
选择记录相同，差别只有"用了前几个选择集"。

**每次选择都是一个不同的选择集（不同世界），不是同情境重复。** 重复子集是
另一件事（README §11.8），本诊断不涉及。

**统一拟合流程**（由诊断一确定，不逐格调整）：ALR + 对角，默认起点，单次拟合。
诊断一在同一规格下用四个离散度不同的起点得到的 logL 极差是 0.0055、参数几乎
相同，所以多起点在这个规格里买不到东西。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import RecoveryConfig, dataset_from_choice_observations, fit_recovery
from worlds.attributes import LONG_COMMUTE_ARM_2024
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, dirichlet_alpha_vector, reference_persona
from worlds.selfcheck import simulate_choices

N_PERSONAS, SIGMA = 1000, 0.05
SEEDS = (8_100, 8_200, 8_300)
TASK_COUNTS = (6, 8, 12)
MAX_TASKS = 12
CFG = RecoveryConfig(parameterization="alr_logistic_normal", n_draws=200, seed=1, maxiter=500)
ORACLE_SEED = SEEDS[0]  # 真值后验参照只在第一个种子上跑（成本）
ORACLE_DRAWS = 20_000


def full_records(seed):
    personas = PersonaGenerator().generate(N_PERSONAS, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    worlds = gen.generate_many(MAX_TASKS, base_seed=seed, reference_persona=reference_persona())
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, gen.tax()) for w in worlds for p in personas],
        pmap,
        sigma=SIGMA,
        seed=seed,
    )
    order = list(dict.fromkeys(o.world_id for o in obs))  # 世界出现顺序 = 前缀顺序
    truth = np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )
    return obs, order, truth


def prefix_dataset(obs, order, n_tasks):
    keep = set(order[:n_tasks])
    return dataset_from_choice_observations([o for o in obs if o.world_id in keep])


def oracle(ds, alpha, sigma, n_draws=ORACLE_DRAWS, seed=0, batch=200):
    """真值先验 + 真值 σ 下的后验均值。**这是数值近似**，靠重要性抽样，
    精度由 ESS 决定；ESS 低时它本身就有误差，不能当作精确上界。"""
    rng = np.random.default_rng(seed)
    x = ds.features
    ell = np.empty((ds.n_personas, n_draws))
    draws = np.empty((n_draws, 7))
    task_persona = ds.row_persona[ds.task_starts]
    pstarts = np.flatnonzero(np.r_[True, task_persona[1:] != task_persona[:-1]])
    for lo in range(0, n_draws, batch):
        hi = min(lo + batch, n_draws)
        w = rng.dirichlet(alpha, size=hi - lo)
        draws[lo:hi] = w
        v = (x @ w.T) / sigma
        m = np.maximum.reduceat(v, ds.task_starts, axis=0)
        e = np.exp(v - m[ds.row_task])
        s = np.add.reduceat(e, ds.task_starts, axis=0)
        ell[:, lo:hi] = np.add.reduceat(v[ds.chosen_row] - (m + np.log(s)), pstarts, axis=0)
    q = np.exp(ell - ell.max(axis=1, keepdims=True))
    q /= q.sum(axis=1, keepdims=True)
    return q @ draws, 1.0 / np.sum(q * q, axis=1)


def row(label, values, fmt="{:>8.3f}"):
    return f"  {label:<26}" + "".join(fmt.format(v) for v in values)


def main():
    alpha = np.array(dirichlet_alpha_vector())
    cache = {}
    for seed in SEEDS:
        t0 = time.time()
        cache[seed] = full_records(seed)
        print(f"种子 {seed}: 生成 {MAX_TASKS} 个世界 × {N_PERSONAS} 人设，{time.time() - t0:.0f}s")

    truth = cache[SEEDS[0]][2]
    base_rmse = np.sqrt(((truth.mean(axis=0)[None, :] - truth) ** 2).mean(axis=0))
    true_var = truth.var(axis=0)
    print("\n基线（对每个人都预测真值总体均值）的 RMSE：")
    print(row("", base_rmse, "{:>8.4f}"))
    print(row("真值总体方差", true_var, "{:>8.4f}"))
    print("\n维度顺序：" + " ".join(f"{d[:8]:>8}" for d in ALL_DIMENSIONS))

    for n_tasks in TASK_COUNTS:
        print("\n" + "=" * 92)
        print(f"每人 {n_tasks} 个不同选择集")
        print("=" * 92)
        for seed in SEEDS:
            obs, order, truth = cache[seed]
            ds = prefix_dataset(obs, order, n_tasks)
            t0 = time.time()
            r = fit_recovery(ds, CFG)
            w = r.w_hat
            corr = np.array([np.corrcoef(w[:, k], truth[:, k])[0, 1] for k in range(7)])
            rmse = np.sqrt(((w - truth) ** 2).mean(axis=0))
            bias = w.mean(axis=0) - truth.mean(axis=0)
            var_ratio = np.diag(r.sigma_w_hat) / true_var
            skill = 1.0 - rmse / base_rmse
            print(
                f"\n  种子 {seed}  σ̂={r.sigma_hat:.4f}(真值 {SIGMA})  "
                f"收敛={r.diagnostics.converged}  {time.time() - t0:.0f}s"
            )
            print(row("bias", bias, "{:>8.4f}"))
            print(row("RMSE", rmse, "{:>8.4f}"))
            print(row("corr", corr))
            print(row("总体方差 估/真", var_ratio))
            print(row("技巧分 vs 常数基线", skill))

        if True:
            obs, order, truth = cache[ORACLE_SEED]
            ds = prefix_dataset(obs, order, n_tasks)
            t0 = time.time()
            post, ess = oracle(ds, alpha, SIGMA)
            orm = np.sqrt(((post - truth) ** 2).mean(axis=0))
            ocorr = np.array([np.corrcoef(post[:, k], truth[:, k])[0, 1] for k in range(7)])
            print(
                f"\n  真值后验参照（**数值近似**，种子 {ORACLE_SEED}，{ORACLE_DRAWS} 抽样，"
                f"ESS 中位 {np.median(ess):.0f}，p05 {np.percentile(ess, 5):.0f}，{time.time() - t0:.0f}s）"
            )
            print(row("参照 RMSE", orm, "{:>8.4f}"))
            print(row("参照 corr", ocorr))


if __name__ == "__main__":
    main()
