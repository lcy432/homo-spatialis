"""最小数值核查（`DECISIONS/0013` 第四节）。限定种子 8100，不重跑 36 次拟合。

3.1  换坐标恒等性：构造 material→safety 的 ALR 线性变换 T，对**同一批** latent
     draws 直接做 a' = T·a（不是在新坐标里重抽一批然后声称"同 seed 即同样本"），
     按维度名对齐比较七维权重、效用、选择概率、面板似然、个体后验均值与同批
     样本的总体矩。容差事先声明。

3.2  定位跨基准 27.7 的似然差：把两份拟合结果映射到统一坐标，在同一套积分规则
     下交叉评估；200 draws 保留为历史基线，另加一个事先固定的高精度评估点
     （800 draws）。最多追加一次以等价转换参数为初始值的跨基准拟合。

代价说明：诊断三只打印了摘要、没有保存参数向量，所以这一份核查必须重建种子
8100 上的两次完全协方差拟合（各四个起点）。这是本核查的最小必需重算。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import (
    RESEARCH_PROFILE_ALR_FULL_MATERIAL,
    ChoiceDataset,
    dataset_from_choice_observations,
    fit_research,
)
from eval.recovery.alr import pack_alr, weights_from_alr
from eval.recovery.msl import _panel_terms, _posterior_weights, layout, standard_normal_draws
from eval.recovery.msl_alr import N_RANDOM, alr_objective
from eval.recovery.profiles import ResearchProfile
from worlds.attributes import LONG_COMMUTE_ARM_2024
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.selfcheck import simulate_choices

SEED, N_PERSONAS, N_TASKS, SIGMA = 8_100, 1000, 12, 0.05
MATERIAL, SAFETY = 0, 1
#: 事先声明的双精度容差。权重/概率是 O(1) 量，似然是 O(10⁴) 量，所以后者放宽。
TOL_POINTWISE = 1e-10
TOL_LOGLIK_REL = 1e-12
HIGH_PRECISION_DRAWS = 800  # 事先固定的高精度评估点
OUT = Path("runs/2026-09-16-p9-evidence")


def say(*a):
    print(*a, flush=True)


def base_change_matrix(frm: int, to: int, n: int = 7) -> np.ndarray:
    """T：把以 ``frm`` 为基准的 ALR 坐标换成以 ``to`` 为基准。

    ``a^to_k = log(w_k/w_to) = a^frm_k − a^frm_to``，其中 ``a^frm_frm ≡ 0``。
    行按 ``other_indices(to)`` 排，列按 ``other_indices(frm)`` 排。
    """
    src = [k for k in range(n) if k != frm]
    dst = [k for k in range(n) if k != to]
    pos = {k: i for i, k in enumerate(src)}
    t = np.zeros((n - 1, n - 1))
    for r, k in enumerate(dst):
        if k != frm:
            t[r, pos[k]] = 1.0
        t[r, pos[to]] -= 1.0
    return t


def panel_loglik_from_a(a: np.ndarray, gamma_0: float, lay, base: int) -> np.ndarray:
    """给定**显式的** a 抽样点（n_personas, R, 6），算每人每 draw 的面板对数似然。

    绕开 (μ, L) 参数化，是 3.1 能成立的前提：两套坐标必须吃同一批样本点。
    """
    n_draws = a.shape[1]
    ell = np.empty((lay.n_personas, n_draws))
    for lo in range(0, n_draws, 32):
        hi = min(lo + 32, n_draws)
        w = weights_from_alr(a[:, lo:hi, :], base)
        xbar = np.zeros((lay.n_rows, hi - lo))
        for k in range(7):
            xbar += lay.x[:, k : k + 1] * w[lay.row_persona, :, k]
        ell[:, lo:hi] = _panel_terms(gamma_0 * xbar, lay)[0]
    return ell


def build():
    personas = PersonaGenerator().generate(N_PERSONAS, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    worlds = gen.generate_many(N_TASKS, base_seed=SEED, reference_persona=reference_persona())
    obs = simulate_choices(
        [instantiate(Tier.TRADEOFF, w, p, gen.tax()) for w in worlds for p in personas],
        pmap,
        sigma=SIGMA,
        seed=SEED,
    )
    truth = np.array(
        [[p.params.w_material] + [p.params.theta[d] for d in AMENITY_DIMENSIONS] for p in personas]
    )
    return dataset_from_choice_observations(obs), truth


# ---------------------------------------------------------------- 3.1


def check_identity(ds: ChoiceDataset, fit_material) -> dict:
    say("\n" + "=" * 78)
    say("3.1 换坐标恒等性（同一批 latent draws，a' = T·a）")
    say("=" * 78)
    f = fit_material.result.fit
    t = base_change_matrix(MATERIAL, SAFETY)
    assert np.allclose(t @ t, np.eye(6), atol=1e-14), "T 应当是对合"

    xi = standard_normal_draws(ds.n_personas, 200, N_RANDOM, seed=1)
    a_m = f.mu_alr + xi @ f.chol.T  # (n_personas, R, 6)，以 material 为基准
    a_s = a_m @ t.T  # **同一批点**换坐标，不是重抽

    w_m = weights_from_alr(a_m, MATERIAL)
    w_s = weights_from_alr(a_s, SAFETY)
    d_w = float(np.abs(w_m - w_s).max())

    lay_m, lay_s = layout(ds, MATERIAL), layout(ds, SAFETY)
    assert np.array_equal(lay_m.x, lay_s.x), "七维属性列与基准维无关"

    ell_m = panel_loglik_from_a(a_m, f.gamma_0, lay_m, MATERIAL)
    ell_s = panel_loglik_from_a(a_s, f.gamma_0, lay_s, SAFETY)
    d_ell = float(np.abs(ell_m - ell_s).max())

    q_m, ll_m = _posterior_weights(ell_m)
    q_s, ll_s = _posterior_weights(ell_s)
    post_m = np.einsum("ir,irk->ik", q_m, w_m)
    post_s = np.einsum("ir,irk->ik", q_s, w_s)
    d_post = float(np.abs(post_m - post_s).max())

    # 同批样本的总体矩（把 draws 摊平当一个样本）
    flat_m, flat_s = w_m.reshape(-1, 7), w_s.reshape(-1, 7)
    d_mean = float(np.abs(flat_m.mean(0) - flat_s.mean(0)).max())
    d_cov = float(np.abs(np.cov(flat_m, rowvar=False) - np.cov(flat_s, rowvar=False)).max())

    # μ' = Tμ 与 Σ' = TΣTᵀ 的一致性（分布层面，允许 Cholesky 因子不同）
    mu_s = t @ f.mu_alr
    sig_s = t @ f.sigma_alr @ t.T
    d_mu = float(np.abs(a_s.reshape(-1, 6).mean(0) - mu_s).max())
    d_sig = float(np.abs(np.cov(a_s.reshape(-1, 6), rowvar=False) - sig_s).max())

    rows = [
        ("七维权重（逐点）", d_w, TOL_POINTWISE),
        ("面板对数似然（逐人逐 draw）", d_ell, TOL_POINTWISE * 1e4),
        ("总对数似然（相对）", abs(ll_m - ll_s) / abs(ll_m), TOL_LOGLIK_REL),
        ("个体后验均值", d_post, TOL_POINTWISE),
        ("同批样本总体均值", d_mean, TOL_POINTWISE),
        ("同批样本总体协方差", d_cov, TOL_POINTWISE),
        ("μ' 对 Tμ（样本均值）", d_mu, 1e-12),
        ("Σ' 对 TΣTᵀ（样本协方差）", d_sig, 1e-12),
    ]
    say(f"  {'量':<30}{'最大差值':>14}{'容差':>12}{'通过':>6}")
    ok = True
    for name, got, tol in rows:
        passed = got <= tol
        ok = ok and passed
        say(f"  {name:<30}{got:14.3e}{tol:12.0e}{passed!s:>6}")
    say(f"\n  结论：{'全部在容差内' if ok else '**有项目超容差**'}")
    return {"passed": ok, "rows": [{"name": n, "max_diff": g, "tol": t} for n, g, t in rows]}


# ---------------------------------------------------------------- 3.2


def check_gap(ds, fit_material, fit_safety) -> dict:
    say("\n" + "=" * 78)
    say("3.2 跨基准似然差的来源")
    say("=" * 78)
    fm, fs = fit_material.result.fit, fit_safety.result.fit
    t_sm = base_change_matrix(SAFETY, MATERIAL)

    # 把 safety 解的**分布**搬到 material 坐标：μ''=Tμ', Σ''=TΣ'Tᵀ
    mu_sm = t_sm @ fs.mu_alr
    sig_sm = t_sm @ fs.sigma_alr @ t_sm.T
    chol_sm = np.linalg.cholesky(sig_sm + 1e-14 * np.eye(6))

    lay = layout(ds, MATERIAL)
    out = {}
    say(f"  {'评估点':<34}{'material 解':>16}{'safety 解(搬过来)':>20}{'差':>12}")
    for n_draws in (200, HIGH_PRECISION_DRAWS):
        xi = standard_normal_draws(ds.n_personas, n_draws, N_RANDOM, seed=1)
        ll_m = -alr_objective(
            pack_alr(fm.rho, fm.mu_alr, fm.chol, "full"), lay, xi, MATERIAL, "full", 32
        )[0]
        ll_s = -alr_objective(
            pack_alr(float(np.log(fs.gamma_0)), mu_sm, chol_sm, "full"),
            lay,
            xi,
            MATERIAL,
            "full",
            32,
        )[0]
        tag = "200 draws（历史基线）" if n_draws == 200 else f"{n_draws} draws（高精度）"
        say(f"  {tag:<34}{ll_m:16.4f}{ll_s:20.4f}{ll_m - ll_s:12.4f}")
        out[f"draws_{n_draws}"] = {"material": ll_m, "safety_mapped": ll_s, "gap": ll_m - ll_s}

    say("\n  读法：两个解都在 **material 坐标、同一套 draws** 下评估，所以这个差")
    say("  不含坐标实现差异。它随 draws 数变化的部分是有限模拟误差；剩下的部分")
    say("  是两个解本身不同（优化落点）。")

    # 最多追加一次：以等价转换的 safety 解为起点，在 material 基准下再拟合一次
    say("\n  追加一次拟合：以搬过来的 safety 解为起点，material 基准下重新优化")
    t0 = time.time()
    from eval.recovery.estimator import _fit_recovery_alr

    start = pack_alr(float(np.log(fs.gamma_0)), mu_sm, chol_sm, "full")
    refit = _fit_recovery_alr(ds, replace(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config), start=start)
    say(
        f"    logL {refit.diagnostics.loglik:.4f}"
        f"（material 最优 {fit_material.result.diagnostics.loglik:.4f}，"
        f"差 {fit_material.result.diagnostics.loglik - refit.diagnostics.loglik:+.4f}）"
        f"  {time.time() - t0:.0f}s"
    )
    out["refit_from_mapped_safety"] = {
        "loglik": refit.diagnostics.loglik,
        "gap_to_material_best": fit_material.result.diagnostics.loglik - refit.diagnostics.loglik,
    }
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ds, _truth = build()
    say(
        f"种子 {SEED}：{ds.n_personas} 人设 × {ds.n_tasks // ds.n_personas} 选择集 "
        f"× {ds.n_rows // ds.n_tasks} 城，建数据 {time.time() - t0:.0f}s"
    )

    say("\n重建两次完全协方差拟合（诊断三未保存参数向量，这是最小必需重算）")
    t0 = time.time()
    fit_m = fit_research(ds)
    say(
        f"  base=material 完成 {time.time() - t0:.0f}s  选中 sd0={fit_m.chosen_sd0} "
        f"logL={fit_m.result.diagnostics.loglik:.4f}  起点极差 {fit_m.loglik_spread:.4f}"
    )
    t0 = time.time()
    profile_s = ResearchProfile(
        name="alr_full_safety_check_only",
        config=replace(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config, base_dim="safety"),
    )
    fit_s = fit_research(ds, profile_s)
    say(
        f"  base=safety   完成 {time.time() - t0:.0f}s  选中 sd0={fit_s.chosen_sd0} "
        f"logL={fit_s.result.diagnostics.loglik:.4f}  起点极差 {fit_s.loglik_spread:.4f}"
    )

    report = {
        "seed": SEED,
        "profile": RESEARCH_PROFILE_ALR_FULL_MATERIAL.fingerprint(),
        "fit_material": fit_m.summary(),
        "fit_safety_check_only": fit_s.summary(),
        "identity_3_1": check_identity(ds, fit_m),
        "gap_3_2": check_gap(ds, fit_m, fit_s),
    }
    path = OUT / "minimal-numerical-check.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"\n机器可读结果写入 {path}")


if __name__ == "__main__":
    main()
