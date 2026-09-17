"""门的执行器。零大模型调用。

门一（§9.1.1.1）：在福利离散度高于 τ 的选择集上，`L_rep` 的中位数是否超过
§9.3 的噪声地板。若在任何现实可构造的选择集上都不超过 → 判定个人化在本装置
上不可测量。

**判据全部来自 `configs/gate_one.yaml`，本模块不内置任何阈值。** 配置的哈希
进产出，证明判据是事前的（硬规则 20）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from common.money import USD_2024_US100
from common.providers import ExpenditureShares
from econ.core import WelfareCore
from eval.baselines import PersonaBaselines, baselines_for
from eval.perturbation import diagnose, sample_truncated
from worlds.attributes import CALIBRATIONS, DEFAULT_CALIBRATION_NAME
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.targets import TargetInfeasibleError, solve_for_ln_dispersion

REFERENCE_ID = "SYNTH_2024"


def config_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def representative_shares(personas: list) -> ExpenditureShares:
    """s̄：人设总体的平均支出份额。

    现状生活成本计算器用的就是这个——它不认识你，只能拿平均篮子平减
    （§1.2 问题二）。取总体均值而不是某个硬编码常数，是因为"代表性"的
    含义就是"这批人的平均"，而那是可以算出来的，不该拍。
    """
    rows = np.array([[p.shares.s_rent, p.shares.s_goods, p.shares.s_other] for p in personas])
    return ExpenditureShares.from_raw(*rows.mean(axis=0))


def bootstrap_median_ci(
    values: np.ndarray, *, resamples: int, confidence: float, rng: np.random.Generator
) -> tuple[float, float]:
    """中位数的百分位 bootstrap 区间，重抽**人设**。

    注意这不是硬规则 22 说的 block bootstrap——那一条针对 `ŵ_i = γ_k/γ_0`
    这类比值估计量在整条流水线上的传播。这里是一个跨人设的普通中位数。
    """
    if len(values) == 0:
        return float("nan"), float("nan")
    draws = rng.choice(values, size=(resamples, len(values)), replace=True)
    medians = np.median(draws, axis=1)
    alpha = (1.0 - confidence) / 2.0
    return float(np.quantile(medians, alpha)), float(np.quantile(medians, 1.0 - alpha))


@dataclass
class RungResult:
    tau: float
    n_personas_feasible: int
    feasible_fraction: float
    counts_toward_verdict: bool
    n_personas_money_metric_missing: int
    median_l_rep_usd: float
    ci_lower_usd: float
    ci_upper_usd: float
    median_l_s_usd: float
    median_l_amen_usd: float
    noise_floor_usd: float
    noise_floor_usd_median_within: float
    flip_probability: float
    conditional_loss_usd: float
    passes: bool


def _median(values: list[float | None]) -> float:
    clean = [v for v in values if v is not None]
    return float(np.median(clean)) if clean else float("nan")


def run_gate_one(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """跑门一。零大模型调用。"""
    sample = config["sample"]
    pert = config["perturbation"]
    ladder = config["tau_ladder"]
    decision = config["decision"]

    # 世界标定默认是 PROVISIONAL_US_2024，与已跑过的门一结果逐位一致；
    # 只有显式写出 world_calibration 才会换。**换了标定就是另一次运行**，
    # 判据一字未动，旧结果的适用范围也不因此扩大（DECISIONS/0013 第七节）。
    calibration = CALIBRATIONS[sample.get("world_calibration", DEFAULT_CALIBRATION_NAME)]
    generator = WorldGenerator(n_cities=sample["n_cities"], calibration=calibration)
    world = generator.generate(seed=sample["world_seed"], reference_persona=reference_persona())
    tax = generator.tax()
    base_core = WelfareCore(world.cities, tax, numeraire=USD_2024_US100, reference_id=REFERENCE_ID)
    cities = [rec.city for rec in world.cities.values()]
    personas = PersonaGenerator().generate(sample["n_personas"], seed=sample["persona_seed"])
    sbar = representative_shares(personas)

    # 扰动只依赖人设的权重，不依赖世界，所以每人抽一次、各档复用。
    # 各档重抽会把"档间差异"与"抽样噪声"混在一起，而我们要的是前者。
    rng = np.random.default_rng(sample["persona_seed"])
    draws = {
        p.persona_id: sample_truncated(
            np.array([p.params.w_material, *p.params.theta.values()]) * 100.0,
            pert["draws_per_persona"],
            rng,
            sweeps=pert["gibbs_sweeps"],
        )
        for p in personas
    }

    boot = decision["bootstrap"]
    boot_rng = np.random.default_rng(sample["persona_seed"] + 1)
    rungs: list[RungResult] = []

    for tau in ladder["values"]:
        rows: list[PersonaBaselines] = []
        infeasible = 0
        for p in personas:
            try:
                core = solve_for_ln_dispersion(base_core, p, cities, tau)
            except TargetInfeasibleError:
                infeasible += 1
                continue
            rows.append(baselines_for(core, p, cities, sbar, perturbations=draws[p.persona_id]))

        feasible = len(rows)
        fraction = feasible / len(personas)
        l_rep = np.array([r.l_rep for r in rows if r.l_rep is not None], dtype=float)
        lo, hi = bootstrap_median_ci(
            l_rep, resamples=boot["resamples"], confidence=decision["confidence"], rng=boot_rng
        )
        floor = _median([r.noise_floor for r in rows])
        counts = fraction >= ladder["min_feasible_fraction"]

        rungs.append(
            RungResult(
                tau=float(tau),
                n_personas_feasible=feasible,
                feasible_fraction=fraction,
                counts_toward_verdict=bool(counts),
                n_personas_money_metric_missing=sum(1 for r in rows if r.l_rep is None),
                median_l_rep_usd=float(np.median(l_rep)) if len(l_rep) else float("nan"),
                ci_lower_usd=lo,
                ci_upper_usd=hi,
                median_l_s_usd=_median([r.l_s for r in rows]),
                median_l_amen_usd=_median([r.l_amen for r in rows]),
                noise_floor_usd=floor,
                noise_floor_usd_median_within=_median([r.noise_floor_median for r in rows]),
                flip_probability=float(np.mean([r.flip_probability for r in rows])),
                conditional_loss_usd=_median([r.conditional_loss for r in rows]),
                # 判据：置信下沿严格超过地板。要下沿而不是点估计，因为这个门
                # 的结论是"要不要重新定位整个项目"。
                passes=bool(counts and lo > floor),
            )
        )

    verdict = "pass" if any(r.passes for r in rungs) else "fail"
    points = np.array([[p.params.w_material, *p.params.theta.values()] for p in personas]) * 100.0
    diagnostics = {
        scheme: str(diagnose(points[:200], scheme, draws_per_persona=32, seed=7))
        for scheme in pert["report_diagnostics_for"]
    }

    return {
        "gate": "one",
        "verdict": verdict,
        "config_sha": config_sha(config_path),
        "config_status": config["meta"]["status"],
        "n_personas": sample["n_personas"],
        "representative_shares": {
            "s_rent": sbar.s_rent,
            "s_goods": sbar.s_goods,
            "s_other": sbar.s_other,
        },
        "perturbation_diagnostics": diagnostics,
        "per_rung": [asdict(r) for r in rungs],
    }


def write_result(result: dict[str, Any], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "gate_one.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
