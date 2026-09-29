"""P8 的编排层：跑一次自检，把结果写成机器可读产出。零大模型调用。

判据与规模全部来自 `configs/p8.yaml`，本模块不内置阈值——与门一同一套纪律
（硬规则 20）。配置的 sha256 与设计指纹一起进产出，证明地板与主实验用的是
同一套设置。
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from eval.recovery import (
    RESEARCH_PROFILE_ALR_FULL_MATERIAL,
    RecoveryConfig,
    RecoveryResult,
    fit_research,
)
from eval.recovery.bootstrap import resample_persona_ids
from eval.recovery.design import base_index
from eval.recovery.msl_alr import alr_posterior_at
from eval.recovery.schema import ChoiceDataset
from eval.sampling.moments import empirical_alr_moments

from .bias import BiasTable, bias_table, calibration_alphas
from .design import ReferenceDesign, build_floor_arm, build_shuffled_arm, dimension_names
from .gates import gate_three, gate_two
from .geometry import arm_slope, collapse_ratio, projections, shared_geometry
from .scheme_a import PROTOCOL, PROTOCOL_VERSION, scheme_a_path
from .sigma import calibrate_sigma, measured_disagreement, simulate_repeats
from .window import (
    MIN_REPLICATES_FOR_FORMAL_SE,
    independent_se_would_be,
    joint_bootstrap,
    paired_slopes,
    window_uncertainty,
)


def _fit_research_adapter(dataset: ChoiceDataset, cfg: RecoveryConfig) -> RecoveryResult:
    """给 block_bootstrap 用的适配器：每个 replicate 走**同一套**正式拟合流程。"""
    profile = (
        replace(RESEARCH_PROFILE_ALR_FULL_MATERIAL, config=cfg)
        if cfg is not RESEARCH_PROFILE_ALR_FULL_MATERIAL.config
        else RESEARCH_PROFILE_ALR_FULL_MATERIAL
    )
    return fit_research(dataset, profile).result


def source_persona_ids(resampled: ChoiceDataset) -> list[str]:
    """把 bootstrap 面板 id 映回来源人设 id。

    ``take_personas`` 给重复抽中的人设发 ``<pid>#k`` 作为**独立的面板 id**（否则
    两个副本会被当成同一个更长的面板），来源 id 靠这一步找回来。
    """
    return [tag.split("#", 1)[0] for tag in resampled.persona_order]


def run_p8(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    design = ReferenceDesign(**config["design"])
    profile = RESEARCH_PROFILE_ALR_FULL_MATERIAL
    sigma_gen = float(config["noise"]["sigma_generating"])
    dims = dimension_names()
    out: dict[str, Any] = {
        "module": "P8_estimator_selfcheck",
        "language_model_calls": 0,
        "design": design.fingerprint(),
        "design_digest": design.digest(),
        "profile": profile.fingerprint(),
        "dimensions": list(dims),
    }
    if config_path is not None:
        import hashlib

        out["config_sha256_16"] = hashlib.sha256(config_path.read_bytes()).hexdigest()[:16]

    # ---------------------------------------------------------------- 5.1
    tables: list[BiasTable] = []
    alphas = calibration_alphas(tuple(config["calibration"]["alpha_scales"]))
    baseline_arm = baseline_fit = None
    baseline_seconds = 0.0
    for case, alpha in alphas.items():
        injected = None if case == "baseline" else alpha
        arm = build_floor_arm(design, sigma=sigma_gen, injected_alpha=injected)
        t0 = time.time()
        fit = fit_research(arm.dataset, profile)
        seconds = time.time() - t0
        tables.append(bias_table(arm, fit, case=case, injected_alpha=injected, seconds=seconds))
        if case == "baseline":
            baseline_arm, baseline_fit, baseline_seconds = arm, fit, seconds
    out["bias_tables"] = [t.as_dict() for t in tables]

    # ---------------------------------------------------------------- 5.2
    # 复用 baseline 案例的臂与拟合：同一份数据、同一个 profile，没有理由拟合两次。
    if baseline_arm is None:
        baseline_arm = build_floor_arm(design, sigma=sigma_gen)
        t0 = time.time()
        baseline_fit = fit_research(baseline_arm.dataset, profile)
        baseline_seconds = time.time() - t0
    floor_fit = baseline_fit
    floor_seconds = baseline_seconds
    truth = baseline_arm.injected_weights

    geom = shared_geometry(
        truth, floor_fit.result.w_center_hat, centre_source="floor_arm_fitted_centre"
    )
    proj_floor = projections(floor_fit.result.w_hat, truth, geom)
    slope_floor = arm_slope(
        proj_floor,
        geom,
        arm="floor",
        source="synthetic_truth_softmax",
        with_intercept=bool(config["geometry"]["regression_with_intercept"]),
    )

    shuffled_arm = build_shuffled_arm(
        design, sigma=sigma_gen, permutation_seed=int(config["geometry"]["shuffle_seed"])
    )
    shuffled_fit = fit_research(shuffled_arm.dataset, profile)
    proj_shuffled = projections(shuffled_fit.result.w_hat, truth, geom)  # **共用同一个 geom**
    slope_shuffled = arm_slope(
        proj_shuffled,
        geom,
        arm="shuffled",
        source="synthetic_shuffled",
        with_intercept=bool(config["geometry"]["regression_with_intercept"]),
    )

    out["geometry"] = {
        "shared": geom.as_dict(),
        "slopes": [slope_floor.as_dict(), slope_shuffled.as_dict()],
        "note": "中心/d/u 只构造一次，三个臂共用（DECISIONS/0002，硬规则 24）",
        "matched_arm": "absent_requires_language_model",
        "collapse_ratio_with_synthetic_matched_placeholder": collapse_ratio(
            float("nan"), slope_shuffled.slope, slope_floor.slope
        ).as_dict(),
        "floor_fit_seconds": floor_seconds,
    }

    # ---------------------------------------------------------------- 5.4
    ln_utils, crossings = _repeat_ln_utilities(design, config)
    repeats_cfg = config["repeat_subset"]
    if repeats_cfg.get("synthetic_validation", False):
        sim = simulate_repeats(
            crossings,
            [[f"c{i}" for i in range(design.n_cities)]],
            sigma=sigma_gen,
            n_repeats=int(repeats_cfg["n_repeats"]),
            seed=int(repeats_cfg["seed"]),
        )
        measurement = measured_disagreement(
            sim, n_alternatives=design.n_cities, source="synthetic_validation"
        )
    else:
        measurement = None
    calib = calibrate_sigma(ln_utils, measurement)
    out["sigma_calibration"] = {
        **calib.as_dict(),
        "measurement": None if measurement is None else measurement.as_dict(),
        "generating_sigma_is_input_not_output": sigma_gen,
        "never_read_from_matched_arm_sigma_hat": True,
    }

    # ---------------------------------------------------------------- 5.3
    out["lambda_path"] = _lambda_path(
        config, design, baseline_arm, floor_fit, geom, truth, calib.sigma or sigma_gen
    )

    # ---------------------------------------------------------------- 5.5
    gates_cfg = config["gates"]
    boot = _bootstrap_window(baseline_arm, shuffled_arm, geom, truth, config, profile)
    g2 = gate_two(
        lam=slope_floor.slope,
        s_shuffled=slope_shuffled.slope,
        window_se=boot["window_se"],
        n_bootstrap=boot["n_succeeded"],
        threshold_2a=float(gates_cfg["two"]["threshold_2a"]),
        threshold_2b=gates_cfg["two"].get("threshold_2b"),
        threshold_2c=float(gates_cfg["two"]["threshold_2c"]),
        min_bootstrap=int(gates_cfg["two"]["min_bootstrap"]),
        arm_sources={"floor": baseline_arm.source, "shuffled": shuffled_arm.source},
    )
    g3 = gate_three(None, None)  # 无实测重复子集 → pending_input
    out["gate_two"] = g2.as_dict()
    out["gate_three"] = g3.as_dict()
    out["bootstrap"] = boot
    out["verdicts"] = {
        "gate_two": g2.status,
        "gate_three": g3.status,
        "note": (
            "门二用的是**合成**地板臂与合成打乱臂；真实匹配/打乱臂需要大模型调用。"
            "本产出只证明接口与判据可执行，不是主实验的坍缩结论"
        ),
    }
    return out


def _repeat_ln_utilities(design: ReferenceDesign, config: dict[str, Any]):
    """重复子集那批 (世界, 人设) 交叉点上的 `ln V`（M2 单一真值来源）。"""
    from worlds.difficulty import Tier, instantiate
    from worlds.generator import WorldGenerator
    from worlds.personas import PersonaGenerator, PersonaSamplingConfig, reference_persona

    cfg = config["repeat_subset"]
    n_personas = int(cfg["n_personas"])
    n_scenarios = int(cfg["n_scenarios"])
    gen = PersonaGenerator(PersonaSamplingConfig(calibration=design.calibration()))
    personas = gen.generate(design.n_personas, seed=design.persona_seed)[:n_personas]
    world_gen = WorldGenerator(calibration=design.calibration(), n_cities=design.n_cities)
    worlds = world_gen.generate_many(
        n_scenarios, base_seed=design.world_seed, reference_persona=reference_persona()
    )
    tax = world_gen.tax()
    rows: list[np.ndarray] = []
    crossings: dict[tuple[str, str], np.ndarray] = {}
    for w in worlds:
        for p in personas:
            scen = instantiate(Tier[design.tier], w, p, tax)
            lnv = np.array(list(scen.utilities(p).values()))
            rows.append(lnv)
            crossings[(p.persona_id, w.world_id)] = lnv
    return np.array(rows), crossings


def _lambda_path(config, design, arm, floor_fit, geom, truth, sigma) -> dict[str, Any]:
    """`λ(t)`，**方案 A**（`DECISIONS/0015` 裁决四）。

    五个条件都在这里兑现，每一条都写进产出供核对：

    1. 共享中心 `c` 取匹配分布的 `E[softmax(a)]`。这里用 `geom.centre`，它来自
       `AlrFit.w_center`（权重空间实算），**不是** `softmax(μ_a)`。
    2. 注入协方差取**本次实际名单 S** 的 ALR 坐标矩（ddof=0），不是 P 的闭式解，
       也不是权重空间的 `np.cov`。
    3. 各臂、所有 `t` 共用同一个 `c` 与同一份 `d/u`——就是传进来的 `geom`，
       本函数**不另造中心**。
    4. 每个 `t` 的独立积分点校验若不过，该点标 invalid，**不发 λ**。
    5. 匹配臂缺席时用地板臂顶替，产出里带显式占位标记。
    """
    grid = np.array(config["lambda_path"]["t_grid"], dtype=float)
    base = base_index(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config.base_dim)

    # 条件 2：注入端取本次名单 S 的 ALR 矩
    injected = empirical_alr_moments(truth, base=base)
    c_a_matched = floor_fit.result.fit.sigma_alr

    # 条件 1、3：中心就是 geom 的那一个，不另造
    centre = geom.centre

    # 条件 5：匹配臂缺席
    matched_is_placeholder = True

    path = scheme_a_path(injected.covariance, c_a_matched, centre, grid, base=base)

    points: list[dict[str, Any]] = []
    for cal in path:
        entry: dict[str, Any] = {**cal.as_dict()}
        # 条件 4：校验不过就不发 λ
        if cal.valid:
            post = alr_posterior_at(
                arm.dataset,
                base,
                float(np.log(1.0 / sigma)),
                cal.mu_a,
                cal.chol,
                n_draws=RESEARCH_PROFILE_ALR_FULL_MATERIAL.config.n_draws,
                seed=RESEARCH_PROFILE_ALR_FULL_MATERIAL.config.seed,
            )
            proj = projections(post.weight_mean, truth, geom)
            sl = arm_slope(
                proj,
                geom,
                arm=f"floor_t={cal.t:.2f}",
                source="synthetic_truth_softmax",
                with_intercept=bool(config["geometry"]["regression_with_intercept"]),
            )
            entry["lambda"] = sl.slope
            entry["n_used"] = sl.n_used
        else:
            entry["lambda"] = None
            entry["n_used"] = 0
            entry["lambda_withheld_because"] = cal.status
        points.append(entry)

    realised = [p for p in points if p["lambda"] is not None]
    return {
        "protocol": PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_description": (
            "方案 A：共享权重中心固定为匹配分布的 E[softmax(a)]，只在 ALR 空间"
            "插值协方差并逐 t 校准位置 μ_a(t)。**不是**权重空间的均值/协方差共同插值"
        ),
        "sigma_used": sigma,
        "sigma_treated_as": "fixed_exogenous_along_t",
        "centre_source": geom.centre_source,
        "centre_is_e_softmax_not_softmax_of_mean": True,
        "injected_covariance_source": {
            "estimand": injected.estimand,
            "base_dim": base,
            "ddof": injected.ddof,
            "n": injected.n,
            "note": "本次实际名单 S 的 ALR 坐标矩；**不是** P 的闭式解",
        },
        "matched_arm_is_placeholder": matched_is_placeholder,
        "matched_placeholder_note": (
            "真实匹配臂缺席，C_a,matched 暂用地板臂自身的 Σ_a 顶替。"
            "**此产出不得当作坍缩结论**，只证明路径可执行"
        ),
        "shared_geometry_digest": {
            "centre": [float(v) for v in geom.centre],
            "n_personas": len(geom.d),
            "n_d_near_zero": geom.n_near_zero,
        },
        "points": points,
        "n_points_realised": len(realised),
        "n_points_requested": len(points),
        "status": "ok" if len(realised) == len(points) else "partial_some_t_invalid",
    }


def _bootstrap_window(arm, shuffled_arm, geom, truth, config, profile) -> dict[str, Any]:
    """窗口 `(ŝ_shuffled − λ̂)` 的联合 block bootstrap。

    **两个臂用同一份抽样索引。** 这是 `SE(W)` 有意义的唯一前提：`Var(ŝ − λ̂)`
    含 `−2·Cov(ŝ, λ̂)`，而协方差只有在两个数来自同一次抽样时才估得出来。
    各臂各抽各的、或者只抽地板臂然后拿 `std(λ)` 冒充 `SE(W)`，都会算错，
    而且方向取决于数据。

    每个 replicate 内：同一份 picks → 两个臂各拟合 → **重估匹配中心** →
    用那个中心构造**一份**共享几何 → 两个臂在这份几何上各出一条斜率。
    任一臂失败 → 整个 replicate 作废（按 ID 联合处理）。
    """
    b = int(config["bootstrap"]["n_replicates"])
    stratified = bool(config["bootstrap"].get("stratified", False))
    index = {pid: i for i, pid in enumerate(arm.persona_ids)}
    with_intercept = bool(config["geometry"]["regression_with_intercept"])

    def draw_picks(rep_seed: int) -> list[str]:
        # 条件：同一份索引；分层设计下保留事前层标签（strata 在 dataset 上）
        return resample_persona_ids(
            arm.dataset, np.random.default_rng(rep_seed), stratified=stratified
        )

    def fit_both(picks, rep_seed: int) -> dict[str, float]:
        cfg = replace(profile.config, seed=rep_seed)
        out: dict[str, float] = {}
        fits = {}
        for name, a in (("floor", arm), ("shuffled", shuffled_arm)):
            try:
                fits[name] = _fit_research_adapter(a.dataset.take_personas(picks), cfg)
            except Exception as exc:
                exc.arm = name  # type: ignore[attr-defined]
                raise

        # 该次重估匹配中心；**两个臂共用这一份几何**
        src = [tag.split("#", 1)[0] for tag in fits["floor"].persona_ids]
        w_true = truth[[index[p] for p in src]]
        g = shared_geometry(
            w_true, fits["floor"].w_center_hat, centre_source="replicate_refit_centre"
        )
        out["_centre"] = g.centre  # type: ignore[assignment]

        for name in ("floor", "shuffled"):
            proj = projections(fits[name].w_hat, w_true, g)
            sl = arm_slope(proj, g, arm=name, source="bootstrap", with_intercept=with_intercept)
            out[f"{name}|t=None|stratum=None"] = sl.slope
        return out

    reps = joint_bootstrap(
        n_replicates=b,
        seed=int(config["bootstrap"]["seed"]),
        draw_picks=draw_picks,
        fit_both=fit_both,
    )

    lam, lam_ids = paired_slopes(reps, "floor|t=None|stratum=None")
    sh, sh_ids = paired_slopes(reps, "shuffled|t=None|stratum=None")
    assert lam_ids == sh_ids, "两个臂的斜率必须来自同一组 replicate ID"

    unc = window_uncertainty(
        s_shuffled=sh,
        lam=lam,
        s_matched=None,  # 真实匹配臂缺席；r 的区间由 window_uncertainty 拒发
        n_requested=b,
        sigma_treated_as="fixed_exogenous_not_propagated",
        coverage="pointwise_single_t_no_strata",
    )
    failures: dict[str, int] = {}
    for r in reps:
        if not r.ok and r.failure:
            failures[r.failure] = failures.get(r.failure, 0) + 1

    return {
        **unc.as_dict(),
        "n_succeeded": len(lam_ids),
        "n_failed": sum(1 for r in reps if not r.ok),
        "failure_reasons": failures,
        "failed_arms": sorted({r.failed_arm for r in reps if r.failed_arm}),
        "both_arms_share_one_persona_draw": True,
        "geometry_rebuilt_per_replicate": True,
        "stratified": stratified,
        "lambda_values": [float(v) for v in lam],
        "s_shuffled_values": [float(v) for v in sh],
        "replicate_ids": lam_ids,
        "independence_diagnostic": (independent_se_would_be(sh, lam) if len(lam) > 1 else None),
        "is_engineering_smoke_not_formal_interval": len(lam_ids) < MIN_REPLICATES_FOR_FORMAL_SE,
        "note": (
            "小 B 输出不得充当正式置信区间，也不能作为门二 2b 的通过证据。"
            "σ 在本计算里是**固定条件**，其标定误差未传播"
        ),
    }


def write_run(payload: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "p8_selfcheck.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
