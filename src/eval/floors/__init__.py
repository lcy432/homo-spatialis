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
from .reuse import (
    MATCHED_FIELDS,
    PROVENANCE_ONLY,
    FloorDescriptor,
    FloorReuseError,
    ReuseVerdict,
    check_reuse,
    descriptor_from_run,
)
from .runner import run_p8, source_persona_ids, write_run
from .scheme_a import (
    LEGACY_PROTOCOL,
    MEAN_TOL,
    PROTOCOL,
    PositionCalibration,
    alr_covariance_path,
    calibrate_position,
    scheme_a_path,
    shared_centre_from_fit,
)
from .sigma import (
    DisagreementMeasurement,
    RepeatObservation,
    SigmaCalibration,
    calibrate_sigma,
    measured_disagreement,
    simulate_repeats,
    theoretical_disagreement,
)
from .window import (
    MIN_REPLICATES_FOR_FORMAL_SE,
    WindowUncertainty,
    independent_se_would_be,
    window_uncertainty,
)

__all__ = [
    "CALIBRATIONS",
    "D_NEAR_ZERO",
    "LEGACY_PROTOCOL",
    "MATCHED_FIELDS",
    "MEAN_TOL",
    "MIN_REPLICATES_FOR_FORMAL_SE",
    "MOMENT_RTOL",
    "PROTOCOL",
    "PROVENANCE_ONLY",
    "ArmSlope",
    "BiasTable",
    "CollapseRatio",
    "Criterion",
    "DisagreementMeasurement",
    "FloorArm",
    "FloorDescriptor",
    "FloorReuseError",
    "GateThreeResult",
    "GateTwoResult",
    "MomentCalibration",
    "MomentFeasibility",
    "MomentSet",
    "PositionCalibration",
    "ReferenceDesign",
    "RepeatObservation",
    "ReuseVerdict",
    "SharedGeometry",
    "SigmaCalibration",
    "WindowUncertainty",
    "alr_covariance_path",
    "arm_slope",
    "bias_table",
    "build_floor_arm",
    "build_shuffled_arm",
    "calibrate_logistic_normal",
    "calibrate_position",
    "calibrate_sigma",
    "calibration_alphas",
    "check_reuse",
    "collapse_ratio",
    "covariance_path",
    "descriptor_from_run",
    "dimension_names",
    "gate_three",
    "gate_two",
    "independent_se_would_be",
    "measured_disagreement",
    "persona_with_weights",
    "projections",
    "run_case",
    "run_p8",
    "scheme_a_path",
    "shared_centre_from_fit",
    "shared_geometry",
    "simplex_moment_feasibility",
    "simulate_repeats",
    "source_persona_ids",
    "theoretical_disagreement",
    "weights_of",
    "window_uncertainty",
    "write_run",
]
