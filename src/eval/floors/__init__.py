"""P8：估计器自检的两条地板，以及门二、门三。零大模型调用。

README §10.3 把目标写在标题上：**量化偏差，不是确认无偏**。所以这里的产出是
一张偏差表加两条地板，不是一个通过/不通过。

拟合一律走 `eval.recovery.fit_research`（研究 profile），与主实验共用同一个
入口——两处各配一份 `RecoveryConfig` 是本项目最容易发生、也最难发现的漂移。
"""

from .bias import BiasTable, MomentSet, bias_table, calibration_alphas, run_case
from .design import (
    CALIBRATIONS,
    FloorArm,
    ReferenceDesign,
    build_floor_arm,
    build_shuffled_arm,
    dimension_names,
    persona_with_weights,
    weights_of,
)
from .gates import Criterion, GateThreeResult, GateTwoResult, gate_three, gate_two
from .geometry import (
    D_NEAR_ZERO,
    ArmSlope,
    CollapseRatio,
    SharedGeometry,
    arm_slope,
    collapse_ratio,
    projections,
    shared_geometry,
)
from .path import (
    MOMENT_RTOL,
    MomentCalibration,
    MomentFeasibility,
    calibrate_logistic_normal,
    covariance_path,
    simplex_moment_feasibility,
)
from .runner import run_p8, source_persona_ids, write_run
from .sigma import (
    DisagreementMeasurement,
    RepeatObservation,
    SigmaCalibration,
    calibrate_sigma,
    measured_disagreement,
    simulate_repeats,
    theoretical_disagreement,
)

__all__ = [
    "CALIBRATIONS",
    "D_NEAR_ZERO",
    "MOMENT_RTOL",
    "ArmSlope",
    "BiasTable",
    "CollapseRatio",
    "Criterion",
    "DisagreementMeasurement",
    "FloorArm",
    "GateThreeResult",
    "GateTwoResult",
    "MomentCalibration",
    "MomentFeasibility",
    "MomentSet",
    "ReferenceDesign",
    "RepeatObservation",
    "SharedGeometry",
    "SigmaCalibration",
    "arm_slope",
    "bias_table",
    "build_floor_arm",
    "build_shuffled_arm",
    "calibrate_logistic_normal",
    "calibrate_sigma",
    "calibration_alphas",
    "collapse_ratio",
    "covariance_path",
    "dimension_names",
    "gate_three",
    "gate_two",
    "measured_disagreement",
    "persona_with_weights",
    "projections",
    "run_case",
    "run_p8",
    "shared_geometry",
    "simplex_moment_feasibility",
    "simulate_repeats",
    "source_persona_ids",
    "theoretical_disagreement",
    "weights_of",
    "write_run",
]
