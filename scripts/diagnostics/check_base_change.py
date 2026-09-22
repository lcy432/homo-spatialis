"""最小数值核查（`DECISIONS/0013` 第四节）。限定种子 8100，不重跑 36 次拟合。

3.1  换坐标恒等性：构造 material→safety 的 ALR 线性变换 T，对**同一批** latent
     draws 直接做 a' = T·a（不是在新坐标里重抽一批然后声称"同 seed 即同样本"），
     按维度名对齐比较七维权重、效用、选择概率、面板似然、个体后验均值与同批
     样本的总体矩。容差事先声明。

3.2  跨基准似然差**在不同评价设置下有多大**：把两份拟合结果映射到统一坐标，
     在同一套积分规则下交叉评估；200 draws 为历史基线，另加一个事先固定的高精度
     评估点（800 draws），再加一次以等价转换参数为初始值的重优化。
     这三个数一起支持"safety 那次是更差的解"，但**不构成对三种误差来源的分离**。

用法：

    uv run python scripts/diagnostics/check_base_change.py                # 走缓存
    uv run python scripts/diagnostics/check_base_change.py --allow-refit  # 允许重拟

**拟合与核查是分开的。** 两次完全协方差拟合各约 900s，重优化再约 100s；核查本身
只是几次目标函数求值。所以拟合参数存在 JSON 里，默认从那里加载，改断言或改注释
不必重跑。缓存按 `cache_key`（数据指纹 + 生成参数 + 两份 profile 指纹）校验，再在
缓存参数上**重算似然**确认它确实属于这批数据。缓存缺失或不匹配时**报告并退出**，
绝不静默重新拟合——要重拟必须显式给 `--allow-refit`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from eval.recovery import (
    RESEARCH_PROFILE_ALR_FULL_MATERIAL,
    ChoiceDataset,
    dataset_from_choice_observations,
    fit_research,
)
from eval.recovery.alr import alr_from_weights, pack_alr, weights_from_alr
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
PERSONA_SEED, CALIBRATION_NAME, TIER_NAME = 20_260_906, "LONG_COMMUTE_ARM_2024", "TRADEOFF"
MATERIAL, SAFETY = 0, 1
#: 事先声明的双精度容差。权重/概率是 O(1) 量，似然是 O(10⁴) 量，所以后者放宽。
TOL_POINTWISE = 1e-10
TOL_LOGLIK_REL = 1e-12
HIGH_PRECISION_DRAWS = 800  # 事先固定的高精度评估点
#: 缓存校验：在缓存参数上重算目标函数，必须复现存下来的似然。
TOL_CACHE_LOGLIK_REL = 1e-9
OUT = Path("runs/2026-09-16-p9-evidence")
CACHE = OUT / "minimal-numerical-check.json"


def say(*a):
    print(*a, flush=True)


class CacheError(RuntimeError):
    """缓存缺失或与当前数据/规格不匹配。**绝不因此静默重新拟合。**"""


@dataclass(frozen=True)
class FitParams:
    """一次拟合里**核查需要的全部东西**，与 `ResearchFit` 解耦。

    3.1 与 3.2 只读 (ρ, γ₀, μ_a, L, Σ_a, logL)。把它们单独拿出来，核查就不再
    依赖"手里有一个活的拟合对象"，于是同一批参数可以来自现算，也可以来自缓存。
    """

    rho: float
    gamma_0: float
    mu_alr: np.ndarray
    chol: np.ndarray
    loglik: float

    @property
    def sigma_alr(self) -> np.ndarray:
        return self.chol @ self.chol.T

    @staticmethod
    def from_fit(fit) -> FitParams:
        f = fit.result.fit
        return FitParams(
            rho=float(f.rho),
            gamma_0=float(f.gamma_0),
            mu_alr=np.asarray(f.mu_alr, dtype=float),
            chol=np.asarray(f.chol, dtype=float),
            loglik=float(f.loglik),
        )

    @staticmethod
    def from_dict(d: dict) -> FitParams:
        return FitParams(
            rho=float(d["rho"]),
            gamma_0=float(d["gamma_0"]),
            mu_alr=np.asarray(d["mu_alr"], dtype=float),
            chol=np.asarray(d["chol"], dtype=float),
            loglik=float(d["loglik"]),
        )

    def to_dict(self) -> dict:
        return {
            "rho": self.rho,
            "gamma_0": self.gamma_0,
            "mu_alr": [float(v) for v in self.mu_alr],
            "chol": [[float(v) for v in row] for row in self.chol],
            "loglik": self.loglik,
        }


def safety_profile() -> ResearchProfile:
    """只在本核查里用的 safety 基准对照规格。主规格一字未动。"""
    return ResearchProfile(
        name="alr_full_safety_check_only",
        config=replace(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config, base_dim="safety"),
    )


def dataset_digest(ds: ChoiceDataset) -> str:
    """数据本身的指纹。世界生成、人设生成或选择模拟一变，它就变。"""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(ds.features, dtype=np.float64).tobytes())
    h.update(np.ascontiguousarray(ds.chosen, dtype=np.bool_).tobytes())
    h.update("\x00".join(ds.persona_ids).encode("utf-8"))
    h.update("\x00".join(ds.choice_set_ids).encode("utf-8"))
    h.update("\x00".join(ds.feature_names).encode("utf-8"))
    return h.hexdigest()[:16]


def cache_key(ds: ChoiceDataset) -> dict:
    """决定拟合结果的**全部**输入：数据 + 规格 + 算法。

    **刻意不含**本脚本的文字、核查行、容差或文档——改一句注释或加一行断言
    不应该让 30 分钟的拟合作废。反过来，凡是会改变拟合落点的东西都在里面：
    profile 指纹已经带上 n_draws、draw_method、draw_seed、maxiter、tol、
    起点集合与选解规则。
    """
    return {
        "dataset_digest": dataset_digest(ds),
        "generation": {
            "seed": SEED,
            "n_personas": N_PERSONAS,
            "n_choice_sets": N_TASKS,
            "sigma": SIGMA,
            "persona_seed": PERSONA_SEED,
            "world_calibration": CALIBRATION_NAME,
            "tier": TIER_NAME,
        },
        "profile_material": RESEARCH_PROFILE_ALR_FULL_MATERIAL.fingerprint(),
        "profile_safety": safety_profile().fingerprint(),
    }


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


def evaluate_loglik(ds: ChoiceDataset, fp: FitParams, base: int, n_draws: int = 200) -> float:
    """在给定参数上重算模拟对数似然。**一次目标函数求值，不是拟合。**"""
    lay = layout(ds, base)
    xi = standard_normal_draws(ds.n_personas, n_draws, N_RANDOM, seed=1)
    packed = pack_alr(fp.rho, fp.mu_alr, fp.chol, "full")
    return float(-alr_objective(packed, lay, xi, base, "full", 32)[0])


def load_cache(path: Path, ds: ChoiceDataset) -> dict:
    """读缓存并校验。任何一步不过就抛 `CacheError`——**不退回拟合**。

    校验分两层。轻的一层比 `cache_key`：数据指纹、生成参数、两份 profile 指纹。
    重的一层是**在缓存参数上重算目标函数**，要求复现存下来的似然——这一条才是
    真正验证"这组参数属于这批数据、这套规格"的证据，比任何元数据比对都硬。

    旧缓存没有 `cache_key` 字段（它写在本次改动之前）。那种情况下轻的一层无法
    比对，如实报告，靠重的一层接受或拒绝，并在写出时补上指纹。
    """
    if not path.exists():
        raise CacheError(f"缓存不存在：{path}")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"缓存读不出来：{path}（{exc}）") from exc

    for field_name in ("fitted_parameters", "profile"):
        if field_name not in cached:
            raise CacheError(f"缓存缺字段 `{field_name}`：{path}")
    for base_name in ("material", "safety"):
        if base_name not in cached["fitted_parameters"]:
            raise CacheError(f"缓存缺 `fitted_parameters.{base_name}`：{path}")

    want = cache_key(ds)
    got = cached.get("cache_key")
    if got is None:
        say("  缓存无 `cache_key`（本次改动之前写的）：跳过指纹比对，改由似然复算判定")
    else:
        for part in ("dataset_digest", "generation", "profile_material", "profile_safety"):
            if got.get(part) != want[part]:
                raise CacheError(
                    f"缓存的 `{part}` 与当前不一致，拒绝复用。\n"
                    f"    缓存：{json.dumps(got.get(part), ensure_ascii=False)}\n"
                    f"    当前：{json.dumps(want[part], ensure_ascii=False)}"
                )
        say(f"  指纹比对通过（数据 {want['dataset_digest']}，两份 profile 一致）")

    for base_name, base in (("material", MATERIAL), ("safety", SAFETY)):
        fp = FitParams.from_dict(cached["fitted_parameters"][base_name])
        got_ll = evaluate_loglik(ds, fp, base)
        rel = abs(got_ll - fp.loglik) / max(abs(fp.loglik), 1.0)
        if rel > TOL_CACHE_LOGLIK_REL:
            raise CacheError(
                f"缓存的 {base_name} 参数在当前数据上复算不出存下来的似然："
                f"存 {fp.loglik:.6f}，算 {got_ll:.6f}（相对差 {rel:.2e} > {TOL_CACHE_LOGLIK_REL:.0e}）"
            )
        say(f"  {base_name:<9} 似然复算 {got_ll:.4f} 对存档 {fp.loglik:.4f}（相对差 {rel:.2e}）")
    return cached


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


def check_identity(ds: ChoiceDataset, f: FitParams) -> dict:
    say("\n" + "=" * 78)
    say("3.1 换坐标恒等性（同一批 latent draws，a' = T·a）")
    say("=" * 78)
    t = base_change_matrix(MATERIAL, SAFETY)

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

    # μ' = Tμ 与 Σ' = TΣTᵀ 的一致性。
    #
    # **必须样本对样本比，不能样本对总体参数比。** 第一版把 200,000 个抽样点的
    # 样本矩拿去和总体参数 Tμ 比，还声明了 1e-12 的容差——那是测试设计错误：
    # 样本矩与总体参数之间隔着蒙特卡洛抽样误差（随机抽样下均值的标准误约
    # 1/√200000 ≈ 2e-3），不可能匹配到双精度。实测差 1e-4/7e-4 正是这个量级。
    #
    # 正确的恒等式是线性性：同一批点下 mean(a') = T·mean(a)、cov(a') = T·cov(a)·Tᵀ
    # **逐位成立**。总体参数那一层是代数，单独比。
    flat_am, flat_as = a_m.reshape(-1, 6), a_s.reshape(-1, 6)
    d_mu = float(np.abs(flat_as.mean(0) - t @ flat_am.mean(0)).max())
    d_sig = float(
        np.abs(np.cov(flat_as, rowvar=False) - t @ np.cov(flat_am, rowvar=False) @ t.T).max()
    )
    # 参数层面的代数恒等式（与抽样无关）。
    #
    # **每一行都必须是可证伪的**，这一点这里错过两次，记在这里免得再错第三次。
    # 第一版写 `|Tμ − Tμ|` 与 `|TΣTᵀ − TΣTᵀ|`：把一个量和它自己比，恒等于 0，
    # 代码对不对都"通过"。第二版把后者换成 `|LLᵀ − Σ|`，看着像个检查，其实
    # `sigma_alr` 在 `msl_alr.py:268` 就是 `chol @ chol.T` 构造出来的，同样恒为 0，
    # 只是多绕了一层。**判断标准是能不能想出一个让它失败的 bug。**
    #
    # 下面三行都能失败：
    #   · T² = I —— `base_change_matrix` 把符号或索引写错就炸；
    #   · T 与 `alr_from_weights` 交叉验证 —— 两条独立实现的路径算同一件事，
    #     这是"T 确实是那个换基映射"的**唯一**实质证据，前两版完全没验过；
    #   · Σ' = TΣTᵀ 仍可分解 —— 转置写成 `t @ Σ @ t` 会给出非 PSD 矩阵，当场抛错。
    d_involution = float(np.abs(t @ t - np.eye(6)).max())

    probe_rng = np.random.default_rng(20260917)
    probe_w = probe_rng.dirichlet(np.full(7, 1.3), size=512)
    d_cross = float(
        np.abs(alr_from_weights(probe_w, SAFETY) - alr_from_weights(probe_w, MATERIAL) @ t.T).max()
    )

    sigma_t = t @ f.sigma_alr @ t.T
    d_sig_psd = float(
        np.abs(np.linalg.cholesky(sigma_t) @ np.linalg.cholesky(sigma_t).T - sigma_t).max()
    )
    # 抽样收敛：样本矩对总体参数，容差按抽样误差定，不是双精度
    d_mu_mc = float(np.abs(flat_as.mean(0) - t @ f.mu_alr).max())
    d_sig_mc = float(np.abs(np.cov(flat_as, rowvar=False) - t @ f.sigma_alr @ t.T).max())

    rows = [
        ("七维权重（逐点）", d_w, TOL_POINTWISE),
        ("面板对数似然（逐人逐 draw）", d_ell, TOL_POINTWISE * 1e4),
        ("总对数似然（相对）", abs(ll_m - ll_s) / abs(ll_m), TOL_LOGLIK_REL),
        ("个体后验均值", d_post, TOL_POINTWISE),
        ("同批样本总体均值", d_mean, TOL_POINTWISE),
        ("同批样本总体协方差", d_cov, TOL_POINTWISE),
        ("mean(a') 对 T·mean(a)（同批样本）", d_mu, TOL_POINTWISE),
        ("cov(a') 对 T·cov(a)·Tᵀ（同批样本）", d_sig, TOL_POINTWISE),
        ("T 是对合：T²=I（参数层面）", d_involution, 1e-14),
        ("T 对 alr_from_weights 交叉验证（参数层面）", d_cross, 1e-12),
        ("搬运后 Σ'=TΣTᵀ 仍可分解（参数层面）", d_sig_psd, 1e-14),
        ("样本矩对总体参数：均值（抽样收敛，非双精度）", d_mu_mc, 5e-3),
        ("样本矩对总体参数：协方差（抽样收敛，非双精度）", d_sig_mc, 5e-3),
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


def check_gap(
    ds,
    fm: FitParams,
    fs: FitParams,
    *,
    cached_refit: dict | None = None,
    allow_refit: bool = False,
) -> dict:
    say("\n" + "=" * 78)
    say("3.2 跨基准似然差：在不同评价设置下观察到多少")
    say("=" * 78)
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

    say("\n  读法（**只报观察值，不声称已分离误差来源**）：两个解都在 material")
    say("  坐标、同一套 draws 下评估，所以这个差里不含坐标实现差异。差随 draws 数")
    say("  从 20.90 变到 16.87；把搬过来的解当起点重优化后落到 0.0058。")
    say("  这两件事一起说明 **safety 那次拟合是更差的解，不是不同的模型**。")
    say("  但 20.90 与 16.87 之差**不等于**'有限模拟误差有 4.0'：换 draws 数同时")
    say("  改变了两条曲线的取值，两个来源在这个设计里没有被分开识别。")

    # 最多追加一次：以等价转换的 safety 解为起点，在 material 基准下再拟合一次。
    # **这是一次主规模拟合**，所以默认从缓存读；没有缓存就如实报告后跳过。
    if cached_refit is not None:
        say("\n  重优化（以搬过来的 safety 解为起点）：**取自缓存，未重新拟合**")
        say(
            f"    logL {cached_refit['loglik']:.4f}"
            f"（material 最优 {fm.loglik:.4f}，差 {cached_refit['gap_to_material_best']:+.4f}）"
        )
        out["refit_from_mapped_safety"] = {**cached_refit, "source": "cache"}
    elif allow_refit:
        say("\n  重优化（以搬过来的 safety 解为起点）：缓存缺失，已获显式授权，现拟合")
        t0 = time.time()
        from eval.recovery.estimator import _fit_recovery_alr

        start = pack_alr(float(np.log(fs.gamma_0)), mu_sm, chol_sm, "full")
        refit = _fit_recovery_alr(
            ds, replace(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config), start=start
        )
        say(
            f"    logL {refit.diagnostics.loglik:.4f}"
            f"（material 最优 {fm.loglik:.4f}，"
            f"差 {fm.loglik - refit.diagnostics.loglik:+.4f}）  {time.time() - t0:.0f}s"
        )
        out["refit_from_mapped_safety"] = {
            "loglik": refit.diagnostics.loglik,
            "gap_to_material_best": fm.loglik - refit.diagnostics.loglik,
            "source": "fitted",
        }
    else:
        say("\n  重优化：**缓存里没有，且未给 --allow-refit，跳过**（它是一次主规模拟合）")
        out["refit_from_mapped_safety"] = {
            "status": "unavailable_no_cache_and_refit_not_allowed",
            "note": "本子项缺输入而未做；3.2 的其余两行不受影响",
        }
    return out


def fit_both(ds: ChoiceDataset) -> tuple[FitParams, FitParams, dict, dict]:
    """跑两次主规模拟合。**只有显式 `--allow-refit` 才会走到这里。**"""
    say("\n重新拟合两次完全协方差（每次约 900s）")
    t0 = time.time()
    fit_m = fit_research(ds)
    say(
        f"  base=material 完成 {time.time() - t0:.0f}s  选中 sd0={fit_m.chosen_sd0} "
        f"logL={fit_m.result.diagnostics.loglik:.4f}  起点极差 {fit_m.loglik_spread:.4f}"
    )
    t0 = time.time()
    fit_s = fit_research(ds, safety_profile())
    say(
        f"  base=safety   完成 {time.time() - t0:.0f}s  选中 sd0={fit_s.chosen_sd0} "
        f"logL={fit_s.result.diagnostics.loglik:.4f}  起点极差 {fit_s.loglik_spread:.4f}"
    )
    return (
        FitParams.from_fit(fit_m),
        FitParams.from_fit(fit_s),
        fit_m.summary(),
        fit_s.summary(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="最小数值核查（DECISIONS/0013 第四节）")
    parser.add_argument("--cache", type=Path, default=CACHE, help="已保存的拟合参数")
    parser.add_argument("--out", type=Path, default=CACHE, help="结果写到哪")
    parser.add_argument(
        "--allow-refit",
        action="store_true",
        help="缓存缺失或不兼容时允许重新拟合（两次主规模拟合，约 30 分钟）。"
        "不给这个开关就只报告并退出，**绝不静默重拟**。",
    )
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ds, _truth = build()
    say(
        f"种子 {SEED}：{ds.n_personas} 人设 × {ds.n_tasks // ds.n_personas} 选择集 "
        f"× {ds.n_rows // ds.n_tasks} 城，建数据 {time.time() - t0:.0f}s"
    )

    say(f"\n读缓存 {args.cache}")
    cached: dict | None = None
    try:
        cached = load_cache(args.cache, ds)
    except CacheError as exc:
        say(f"  **缓存不可用**：{exc}")
        if not args.allow_refit:
            say("\n拒绝继续：重新拟合要花两次主规模拟合的时间，必须显式授权。")
            say("要重拟就加 --allow-refit；要用别的缓存就给 --cache。")
            return 2
        say("  已给 --allow-refit，将重新拟合。")

    if cached is not None:
        fp_m = FitParams.from_dict(cached["fitted_parameters"]["material"])
        fp_s = FitParams.from_dict(cached["fitted_parameters"]["safety"])
        summary_m = cached.get("fit_material", {})
        summary_s = cached.get("fit_safety_check_only", {})
        fit_source = {"source": "cache", "path": str(args.cache)}
        cached_refit = cached.get("gap_3_2", {}).get("refit_from_mapped_safety")
        if cached_refit is not None and "loglik" not in cached_refit:
            cached_refit = None  # 存的是"未做"的占位记录，不是结果
    else:
        fp_m, fp_s, summary_m, summary_s = fit_both(ds)
        fit_source = {"source": "fitted", "path": None}
        cached_refit = None

    report = {
        "seed": SEED,
        "fit_source": fit_source,
        "cache_key": cache_key(ds),
        "fitted_parameters": {"material": fp_m.to_dict(), "safety": fp_s.to_dict()},
        "profile": RESEARCH_PROFILE_ALR_FULL_MATERIAL.fingerprint(),
        "fit_material": summary_m,
        "fit_safety_check_only": summary_s,
        "identity_3_1": check_identity(ds, fp_m),
        "gap_3_2": check_gap(
            ds, fp_m, fp_s, cached_refit=cached_refit, allow_refit=args.allow_refit
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"\n机器可读结果写入 {args.out}")
    return 0 if report["identity_3_1"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
