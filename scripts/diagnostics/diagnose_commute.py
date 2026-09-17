"""四项诊断。零大模型调用，只读，不改仓库。

固定配置（与 DECISIONS/0012 的配对比较同一套，数字可以直接对照）：
  长通勤臂 LONG_COMMUTE_ARM_2024；150 人设 seed 20260906；12 城；
  真值 σ = 0.05；估计器 draws=200 halton，fit seed=1，base_dim=material。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import (
    RecoveryConfig,
    dataset_from_choice_observations,
    fit_recovery,
    identifiability_report,
)
from worlds.attributes import AMENITY_ANCHORS, LONG_COMMUTE_ARM_2024
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, dirichlet_alpha_vector, reference_persona
from worlds.selfcheck import simulate_choices

N_PERSONAS, SIGMA, SEED = 150, 0.05, 20260906
CI = ALL_DIMENSIONS.index("commute")
LONG_MIN = 60.0


def make(calibration, n_worlds, seed_block, personas, pmap, sigma=SIGMA):
    gen = WorldGenerator(calibration=calibration)
    tax = gen.tax()
    worlds = gen.generate_many(
        n_worlds, base_seed=seed_block, reference_persona=reference_persona()
    )
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, tax) for w in worlds for p in personas],
        pmap,
        sigma=sigma,
        seed=seed_block,
    )
    return dataset_from_choice_observations(obs), worlds


def truth_matrix(personas):
    return np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )


def per_dim(name, values):
    return f"  {name:<22}" + " ".join(
        f"{d[:4]}={v:7.4f}" for d, v in zip(ALL_DIMENSIONS, values, strict=False)
    )


# ---------------------------------------------------------------- 检查一


def check_one(ds, truth, personas):
    print("\n" + "=" * 78)
    print("检查一 · 与「统一猜总体均值」基线比：P9 学到了多少**个体**信息")
    print("=" * 78)
    res = fit_recovery(
        ds, RecoveryConfig(parameterization="alr_logistic_normal", n_draws=200, seed=1)
    )
    w_hat = res.w_hat
    rmse_p9 = np.sqrt(((w_hat - truth) ** 2).mean(axis=0))
    # 基线甲：P9 自己估出的总体中心，对每个人都猜同一个数
    rmse_center = np.sqrt(((res.w_center_hat[None, :] - truth) ** 2).mean(axis=0))
    # 基线乙：**真值**总体均值（最好的常数预测器，P9 不可能知道它）
    rmse_oracle_const = np.sqrt(((truth.mean(axis=0)[None, :] - truth) ** 2).mean(axis=0))

    print(per_dim("P9 个体 RMSE", rmse_p9))
    print(per_dim("猜 P9 估的总体中心", rmse_center))
    print(per_dim("猜真值总体均值", rmse_oracle_const))
    skill = 1.0 - rmse_p9 / rmse_oracle_const
    print(per_dim("技巧分 1−RMSE比", skill))
    print("\n  技巧分 > 0 才说明 P9 比「对每个人猜同一个数」强。")
    print(f"  七维平均技巧分 {skill.mean():+.3f}；commute 单独 {skill[CI]:+.3f}")
    return res


# ---------------------------------------------------------------- 检查二


def check_two(personas, pmap):
    print("\n" + "=" * 78)
    print("检查二 · 真值下长通勤城市的选择概率与支配情况")
    print("=" * 78)
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    tax = gen.tax()
    worlds = gen.generate_many(3, base_seed=7_000, reference_persona=reference_persona())
    ref = AMENITY_ANCHORS["commute"].reference

    p_long, p_short, chosen_long, dominated, gaps = [], [], [], [], []
    for w in worlds:
        recs = list(w.cities.values())
        minutes = np.array([ref * (1 - r.amenities_scaled["commute"]) for r in recs])
        is_long = minutes >= LONG_MIN - 1e-9
        core = instantiate(Tier.TRADEOFF, w, personas[0], tax).core
        for p in personas:
            truths = [core.score(p, r.city, 2024) for r in recs]
            lnv = np.array([t.ln_utility for t in truths])
            pr = np.exp((lnv - lnv.max()) / SIGMA)
            pr /= pr.sum()
            p_long.append(pr[is_long].sum())
            p_short.append(pr[~is_long].sum())
            chosen_long.append(int(np.argmax(pr)) in np.flatnonzero(is_long))
            gaps.append((lnv.max() - lnv[is_long].max()) / SIGMA)
        # 支配：七个属性上是否被某座城市全面压过
        feats = np.array(
            [
                [t.features[c] for c in sorted(truths[0].features)]
                for t in [core.score(personas[0], r.city, 2024) for r in recs]
            ]
        )
        sign = np.array(
            [
                -1.0 if c == "ln_p" else 1.0
                for c in sorted(core.score(personas[0], recs[0].city, 2024).features)
            ]
        )
        good = feats * sign
        for i in np.flatnonzero(is_long):
            dominated.append(
                any(
                    np.all(good[j] >= good[i]) and np.any(good[j] > good[i])
                    for j in range(len(recs))
                    if j != i
                )
            )

    print(f"  真值 softmax 下选到**某座长通勤城市**的概率：均值 {np.mean(p_long):.4f}")
    print(f"    （随机基线 3/12 = 0.25；短通勤一侧 {np.mean(p_short):.4f}）")
    print(f"  长通勤城市是真值 argmax 的人设占比：{np.mean(chosen_long) * 100:.2f}%")
    print(f"  长通勤城市被其他城市**七属性全面支配**的比例：{np.mean(dominated) * 100:.1f}%")
    finite = np.array(gaps)[np.isfinite(gaps)]
    print(f"  最优城市与最好的长通勤城市之间的 (ln V 差)/σ：中位 {np.median(finite):.1f}")
    print("\n  读法：这个比值就是 softmax 的指数差。大于 ~5 时长通勤选项的选择概率")
    print("  已经贴近零——属性上有变异，但选择上从不被选中，似然里就没有它的信息。")


# ---------------------------------------------------------------- 检查三


def check_three(personas, pmap, truth):
    print("\n" + "=" * 78)
    print("检查三 · 每人 3 / 6 / 12 次选择（其余配置完全固定）")
    print("=" * 78)
    print(
        f"  {'每人次数':<10}{'commute corr':>14}{'commute RMSE':>14}{'七维平均corr':>14}{'σ̂':>10}"
    )
    for n_tasks in (3, 6, 12):
        ds, _ = make(LONG_COMMUTE_ARM_2024, n_tasks, 7_000, personas, pmap)
        r = fit_recovery(
            ds, RecoveryConfig(parameterization="alr_logistic_normal", n_draws=200, seed=1)
        )
        corr = np.array([np.corrcoef(r.w_hat[:, k], truth[:, k])[0, 1] for k in range(7)])
        rmse = np.sqrt(((r.w_hat - truth) ** 2).mean(axis=0))
        print(
            f"  {n_tasks:<10}{corr[CI]:>14.3f}{rmse[CI]:>14.4f}{corr.mean():>14.3f}{r.sigma_hat:>10.4f}"
        )
    print("\n  若 commute 随次数明显上升，瓶颈是观察太少；若持平，瓶颈在设计。")


# ---------------------------------------------------------------- 检查四


def oracle_posterior(ds, alpha, sigma, n_draws=40_000, seed=0, batch=2_000):
    """在**真值先验 Dirichlet(α) 与真值 σ** 下的精确后验均值。

    先验即提议分布，所以重要性权重就是似然本身。这是给定这批数据的信息上限：
    任何估计器都不可能系统性超过它。
    """
    rng = np.random.default_rng(seed)
    x = ds.features
    n_p = ds.n_personas
    ell = np.empty((n_p, n_draws))
    draws = np.empty((n_draws, 7))

    for lo in range(0, n_draws, batch):
        hi = min(lo + batch, n_draws)
        w = rng.dirichlet(alpha, size=hi - lo)
        draws[lo:hi] = w
        v = (x @ w.T) / sigma  # (n_rows, b)
        m = np.maximum.reduceat(v, ds.task_starts, axis=0)
        e = np.exp(v - m[ds.row_task])
        s = np.add.reduceat(e, ds.task_starts, axis=0)
        logp = v[ds.chosen_row] - (m + np.log(s))
        task_persona = ds.row_persona[ds.task_starts]
        starts = np.flatnonzero(np.r_[True, task_persona[1:] != task_persona[:-1]])
        ell[:, lo:hi] = np.add.reduceat(logp, starts, axis=0)

    mx = ell.max(axis=1, keepdims=True)
    q = np.exp(ell - mx)
    q /= q.sum(axis=1, keepdims=True)
    ess = 1.0 / np.sum(q * q, axis=1)
    return q @ draws, ess


def check_four(ds, truth, p9_result):
    print("\n" + "=" * 78)
    print("检查四 · 与「真值先验 + 真值 σ」的精确后验对照")
    print("=" * 78)
    alpha = np.array(dirichlet_alpha_vector())
    post, ess = oracle_posterior(ds, alpha, SIGMA)
    print(f"  重要性抽样有效样本数：中位 {np.median(ess):.0f} / 40000（越高越可信）")

    oc = np.array([np.corrcoef(post[:, k], truth[:, k])[0, 1] for k in range(7)])
    pc = np.array([np.corrcoef(p9_result.w_hat[:, k], truth[:, k])[0, 1] for k in range(7)])
    orm = np.sqrt(((post - truth) ** 2).mean(axis=0))
    prm = np.sqrt(((p9_result.w_hat - truth) ** 2).mean(axis=0))

    print(per_dim("上限 corr（真值先验）", oc))
    print(per_dim("P9 corr", pc))
    print(per_dim("上限 RMSE", orm))
    print(per_dim("P9 RMSE", prm))
    print("\n  分解：")
    print(
        "    数据本身的损失   1 − 上限corr : "
        + " ".join(f"{d[:4]}={1 - v:.3f}" for d, v in zip(ALL_DIMENSIONS, oc, strict=False))
    )
    print(
        "    P9 相对上限的损失 上限−P9    : "
        + " ".join(f"{d[:4]}={a - b:+.3f}" for d, a, b in zip(ALL_DIMENSIONS, oc, pc, strict=False))
    )


def main():
    personas = PersonaGenerator().generate(N_PERSONAS, seed=SEED)
    pmap = {p.persona_id: p for p in personas}
    truth = truth_matrix(personas)
    ds, _ = make(LONG_COMMUTE_ARM_2024, 3, 7_000, personas, pmap)
    rep = identifiability_report(ds)
    print(
        f"数据集：{ds.n_personas} 人设 × {ds.n_tasks // ds.n_personas} 次选择 × "
        f"{ds.n_rows // ds.n_tasks} 城；commute within_sd={rep.within_sd[CI]:.4f}"
    )

    p9 = check_one(ds, truth, personas)
    check_two(personas, pmap)
    check_three(personas, pmap, truth)
    check_four(ds, truth, p9)


if __name__ == "__main__":
    main()
