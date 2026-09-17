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
    block_bootstrap,
    fit_research,
)
from eval.recovery.msl_alr import alr_posterior_at
from eval.recovery.schema import ChoiceDataset

from .bias import BiasTable, bias_table, calibration_alphas
from .design import ReferenceDesign, build_floor_arm, build_shuffled_arm, dimension_names
from .gates import gate_three, gate_two
from .geometry import arm_slope, collapse_ratio, projections, shared_geometry
from .path import calibrate_logistic_normal, covariance_path, simplex_moment_feasibility
from .sigma import calibrate_sigma, measured_disagreement, simulate_repeats


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
    """`Σ_w(t)` 路径上的 λ(t)。总体参数被目标矩钉死，**不重新拟合总体**。"""
    grid = np.array(config["lambda_path"]["t_grid"], dtype=float)
    sigma_injected = np.cov(truth, rowvar=False)
    sigma_matched = floor_fit.result.sigma_w_hat  # 开发阶段用地板臂自身的 Σ̂ 占位
    targets = covariance_path(sigma_injected, sigma_matched, grid)

    # **均值必须与协方差一起插值。** §1.2.2 只写了 Σ(t)，没写均值目标；实测
    # 把注入均值配上拟合协方差会在端点给出不可行的 (均值, Σ)——本仓库实测
    # Var(commute)=0.1334 超过该均值下的伯努利上界 0.0904，那对矩不对应单纯形
    # 上任何分布。总体是 (均值, 协方差) 一对，插值就要一起插（DECISIONS/0013 第六节）。
    mean_injected = truth.mean(axis=0)
    mean_matched = floor_fit.result.w_center_hat
    means = [(1.0 - float(t)) * mean_injected + float(t) * mean_matched for t in grid]

    from eval.recovery.design import base_index

    base = base_index(RESEARCH_PROFILE_ALR_FULL_MATERIAL.config.base_dim)
    points: list[dict[str, Any]] = []
    for t, cov, target_mean in zip(grid, targets, means, strict=True):
        cal = calibrate_logistic_normal(target_mean, cov, base=base, t=float(t))
        entry: dict[str, Any] = {**cal.as_dict()}
        if cal.status == "ok":
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
            s = arm_slope(
                proj,
                geom,
                arm=f"floor_t={t:.2f}",
                source="synthetic_truth_softmax",
                with_intercept=bool(config["geometry"]["regression_with_intercept"]),
            )
            entry["lambda"] = s.slope
            entry["n_used"] = s.n_used
        else:
            entry["lambda"] = None
        points.append(entry)

    realised = [p for p in points if p["lambda"] is not None]
    return {
        "protocol": "DECISIONS/0013 sixth section: weight-space moment calibration, "
        "population parameters pinned by target moments, posterior evaluated at fixed "
        "parameters (population NOT re-estimated)",
        "sigma_used": sigma,
        "target_mean_source": "interpolated_with_covariance_(1-t)*injected+t*matched",
        "why_mean_is_interpolated": (
            "只插值 Σ 会在端点给出不可行的 (均值, Σ)：实测 Var(commute)=0.1334 "
            "超过注入均值下的伯努利上界 0.0904。总体是一对矩，必须一起插值"
        ),
        "sigma_matched_source": "placeholder_floor_arm_fitted_sigma_w_until_real_matched_arm_exists",
        "feasibility_of_endpoints": [
            simplex_moment_feasibility(means[0], targets[0]).as_dict(),
            simplex_moment_feasibility(means[-1], targets[-1]).as_dict(),
        ],
        "points": points,
        "n_points_realised": len(realised),
        "n_points_requested": len(points),
        "status": "ok" if len(realised) == len(points) else "partial_residual_above_tolerance",
    }


def _bootstrap_window(arm, shuffled_arm, geom, truth, config, profile) -> dict[str, Any]:
    """窗口 `(ŝ_shuffled − λ̂)` 的 block bootstrap。本轮跑小 B 的工程 smoke。"""
    b = int(config["bootstrap"]["n_replicates"])
    index = {pid: i for i, pid in enumerate(arm.persona_ids)}

    def metric(result):
        src = [tag.split("#", 1)[0] for tag in result.persona_ids]
        w_true = truth[[index[s] for s in src]]
        g = shared_geometry(w_true, result.w_center_hat, centre_source="replicate_refit_centre")
        proj = projections(result.w_hat, w_true, g)
        s = arm_slope(proj, g, arm="floor", source="synthetic_truth_softmax")
        return {"lambda": s.slope, "n_used": float(s.n_used)}

    outcome = block_bootstrap(
        arm.dataset,
        profile.config,
        n_replicates=b,
        seed=int(config["bootstrap"]["seed"]),
        metric_fn=metric,
        fit_fn=_fit_research_adapter,
    )
    lam = outcome.metric_values("lambda")
    return {
        "n_requested": b,
        "n_succeeded": len(outcome.successes),
        "n_failed": outcome.n_failed,
        "failure_reasons": outcome.failure_reasons(),
        "lambda_values": [float(v) for v in lam],
        "lambda_se": float(np.std(lam, ddof=1)) if len(lam) > 1 else None,
        "window_se": float(np.std(lam, ddof=1)) if len(lam) > 1 else None,
        "is_engineering_smoke_not_formal_interval": True,
        "note": "小 B 输出不得充当正式置信区间，也不能作为门二 2b 的通过证据",
    }


def write_run(payload: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "p8_selfcheck.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
