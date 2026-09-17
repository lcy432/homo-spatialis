"""长通勤臂的新旧配对比较。零大模型调用。

协议在跑之前定死，跑完不改：
  世界种子块   5 个：(7_000, 7_100, 7_200, 7_300, 7_400)，每块 3 个世界
  人设         150，seed 20260906，两条件完全相同
  城市数       12（未改）
  真值噪声 σ   两档 {0.05, 0.10}
  估计器       difference_normal+diagonal 与 alr_logistic_normal+diagonal
               base_dim=material，draws=200 halton，fit seed=1，maxiter=500
  配对         同人设、同世界种子、同模拟 seed；**只有 calibration 不同**
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
from worlds.attributes import (
    AMENITY_ANCHORS,
    LONG_COMMUTE_ARM_2024,
    PROVISIONAL_US_2024,
)
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.selfcheck import simulate_choices

SEED_BLOCKS = (7_000, 7_100, 7_200, 7_300, 7_400)
N_PERSONAS, N_WORLDS = 150, 3
SIGMAS = (0.05, 0.10)
SPECS = (("difference_normal", "diff"), ("alr_logistic_normal", "alr"))
CI = ALL_DIMENSIONS.index("commute")


def build(calibration, block, personas, pmap, sigma):
    gen = WorldGenerator(calibration=calibration)
    tax = gen.tax()
    worlds = gen.generate_many(N_WORLDS, base_seed=block, reference_persona=reference_persona())
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, tax) for w in worlds for p in personas],
        pmap,
        sigma=sigma,
        seed=block,
    )
    minutes = np.array(
        [
            AMENITY_ANCHORS["commute"].reference * (1.0 - r.amenities_scaled["commute"])
            for w in worlds
            for r in w.cities.values()
        ]
    )
    return dataset_from_choice_observations(obs), minutes, [w.condition_number for w in worlds]


def main() -> None:
    personas = PersonaGenerator().generate(N_PERSONAS, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    truth = np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )

    n_cities_total = len(SEED_BLOCKS) * N_WORLDS * 12
    print(f"=== 1. 通勤属性分布（5 个种子块合计 {n_cities_total} 座城市）===")
    dists = {}
    for name, cal in (("旧配置", PROVISIONAL_US_2024), ("长通勤臂", LONG_COMMUTE_ARM_2024)):
        mins = np.concatenate([build(cal, b, personas, pmap, 0.05)[1] for b in SEED_BLOCKS])
        dists[name] = mins
        q = np.percentile(mins, [0, 10, 25, 50, 75, 90, 100])
        print(
            f"  {name:8s} min/p10/p25/中位/p75/p90/max = "
            + " / ".join(f"{v:.1f}" for v in q)
            + f"   ≥90min: {100 * (mins >= 90).mean():.1f}%"
            + f"   =120min: {100 * (mins >= 120).mean():.1f}%"
        )

    print("\n=== 2. 组内变异与识别诊断（σ=0.05，5 块的中位）===")
    rows = {}
    for name, cal in (("旧配置", PROVISIONAL_US_2024), ("长通勤臂", LONG_COMMUTE_ARM_2024)):
        w, u, absmin, conds = [], [], [], []
        for b in SEED_BLOCKS:
            ds, _, cn = build(cal, b, personas, pmap, 0.05)
            r = identifiability_report(ds)
            w.append(r.within_sd)
            u.append(r.unique_sd_relative)
            absmin.append(min(r.tangent_singular_values_absolute))
            conds.extend(cn)
        rows[name] = (np.median(w, axis=0), np.median(u, axis=0), np.median(absmin), conds)
        wm, um, am, cn = rows[name]
        print(f"  {name}")
        print(
            "    within_sd : "
            + " ".join(f"{d[:4]}={v:.4f}" for d, v in zip(ALL_DIMENSIONS, wm, strict=False))
        )
        print(
            "    unique rel: "
            + " ".join(f"{d[:4]}={v:.3f}" for d, v in zip(ALL_DIMENSIONS, um, strict=False))
        )
        print(f"    绝对切空间最小奇异值 {am:.4f}（σ=0.05）  世界条件数中位 {np.median(cn):.1f}")

    print("\n=== 3. 配对回收比较（每格 5 个种子块的均值）===")
    for sigma in SIGMAS:
        for spec, tag in SPECS:
            cfg = RecoveryConfig(parameterization=spec, n_draws=200, seed=1, maxiter=500)
            out = {}
            for name, cal in (("旧", PROVISIONAL_US_2024), ("新", LONG_COMMUTE_ARM_2024)):
                bias, rmse, corr, sig, failed = [], [], [], [], []
                for b in SEED_BLOCKS:
                    ds = build(cal, b, personas, pmap, sigma)[0]
                    try:
                        r = fit_recovery(ds, cfg)
                    except Exception as exc:
                        failed.append((b, type(exc).__name__))
                        continue
                    bias.append(r.w_hat.mean(axis=0) - truth.mean(axis=0))
                    rmse.append(np.sqrt(((r.w_hat - truth) ** 2).mean(axis=0)))
                    corr.append([np.corrcoef(r.w_hat[:, k], truth[:, k])[0, 1] for k in range(7)])
                    sig.append(r.sigma_hat)
                nan7 = np.full(7, np.nan)
                out[name] = (
                    np.mean(bias, axis=0) if bias else nan7,
                    np.mean(rmse, axis=0) if rmse else nan7,
                    np.mean(corr, axis=0) if corr else nan7,
                    float(np.mean(sig)) if sig else float("nan"),
                    failed,
                )
            print(f"\n  --- σ={sigma}  {tag} ---")
            print(f"    {'维度':<13}{'bias 旧→新':>20}{'RMSE 旧→新':>20}{'corr 旧→新':>20}")
            for k, d in enumerate(ALL_DIMENSIONS):
                mark = " <<" if d == "commute" else ""
                print(
                    f"    {d:<13}{out['旧'][0][k]:+9.4f} →{out['新'][0][k]:+9.4f}"
                    f"{out['旧'][1][k]:9.4f} →{out['新'][1][k]:9.4f}"
                    f"{out['旧'][2][k]:9.3f} →{out['新'][2][k]:9.3f}{mark}"
                )
            print(
                f"    σ̂（真值 {sigma}）: 旧 {out['旧'][3]:.4f} → 新 {out['新'][3]:.4f}"
                f"   反解失败 旧 {len(out['旧'][4])}/5  新 {len(out['新'][4])}/5"
            )
            for nm in ("旧", "新"):
                for blk, why in out[nm][4]:
                    print(f"      失败: {nm}配置 种子块 {blk} -> {why}")


if __name__ == "__main__":
    main()
