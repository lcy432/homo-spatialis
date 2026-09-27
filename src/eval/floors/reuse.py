"""地板能不能复用：比设计，不比选择记录。

一条地板臂是"在这个设计下，这个估计器有多少偏差"。把它当成另一次运行的正式
地板，前提是**两次的设计一致**。不一致就拒绝——不是警告，是拒绝，因为一个
不匹配的地板会安静地给出一个数，而那个数会被当成偏差扣掉。

**比什么**：目标人群与 α、分层与配额、人数/题数/候选数、世界 profile、噪声来源、
估计配置。

**不比什么**：两个臂的选择记录逐行相同。地板臂与匹配臂本来就不同——一个由真值
softmax 生成，一个由大模型生成；打乱臂连配对都是故意打乱的。要求逐行相同等于
要求两个臂是同一个臂。数据集指纹**记录**下来供溯源，但不参与匹配判定。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

ReuseStatus = Literal["ok", "refused"]

#: 必须逐项相同才允许复用。顺序即报告顺序。
MATCHED_FIELDS: tuple[str, ...] = (
    "target_population",
    "injected_alpha",
    "stratification",
    "allocation",
    "n_personas",
    "n_choice_sets_per_persona",
    "n_cities",
    "world_calibration",
    "tier",
    "noise_source",
    "sigma_generating",
    "estimation_profile",
)

#: 只记录、不参与判定。改了它们不影响复用资格。
PROVENANCE_ONLY: tuple[str, ...] = (
    "dataset_digest",
    "arm_source",
    "persona_seed",
    "world_seed",
    "choice_seed",
    "run_id",
)


@dataclass(frozen=True)
class FloorDescriptor:
    """一条地板臂的设计描述。`matched` 参与判定，`provenance` 只溯源。"""

    matched: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [k for k in MATCHED_FIELDS if k not in self.matched]
        if missing:
            raise ValueError(f"地板描述缺少必比字段：{missing}")

    def digest(self) -> str:
        blob = json.dumps(self.matched, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": dict(self.matched),
            "matched_digest": self.digest(),
            "provenance": dict(self.provenance),
            "provenance_does_not_affect_reuse": True,
        }


@dataclass(frozen=True)
class ReuseVerdict:
    status: ReuseStatus
    mismatches: tuple[dict[str, Any], ...]
    floor_digest: str
    target_digest: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "floor_digest": self.floor_digest,
            "target_digest": self.target_digest,
            "mismatches": [dict(m) for m in self.mismatches],
            "compared": list(MATCHED_FIELDS),
            "not_compared": list(PROVENANCE_ONLY),
            "note": (
                "只比设计字段。两个臂的选择记录本来就不同（真值 softmax vs 大模型 "
                "vs 打乱配对），要求逐行相同等于要求它们是同一个臂"
            ),
        }

    def raise_if_refused(self) -> None:
        if not self.ok:
            names = ", ".join(m["field"] for m in self.mismatches)
            raise FloorReuseError(
                f"设计不匹配，拒绝把这条地板当作正式地板复用。不一致的字段：{names}"
            )


class FloorReuseError(RuntimeError):
    """设计不匹配。**拒绝，不降级为警告。**"""


def check_reuse(floor: FloorDescriptor, target: FloorDescriptor) -> ReuseVerdict:
    """能不能把 `floor` 当作 `target` 这次运行的正式地板。"""
    mismatches = [
        {"field": k, "floor": floor.matched.get(k), "target": target.matched.get(k)}
        for k in MATCHED_FIELDS
        if floor.matched.get(k) != target.matched.get(k)
    ]
    return ReuseVerdict(
        status="ok" if not mismatches else "refused",
        mismatches=tuple(mismatches),
        floor_digest=floor.digest(),
        target_digest=target.digest(),
    )


def descriptor_from_run(
    *,
    design: Any,
    sigma_generating: float,
    noise_source: str,
    estimation_profile: dict[str, Any],
    target_population: str,
    injected_alpha: list[float] | None,
    sampling: dict[str, Any] | None = None,
    dataset_digest: str | None = None,
    arm_source: str | None = None,
    run_id: str | None = None,
) -> FloorDescriptor:
    """从一次 P8 运行拼出描述。

    `sampling` 是 P10 的 `StratifiedSample.as_dict()`（或 `None` 表示简单随机
    抽样）。**没有分层也要显式说**——把"没分层"与"忘了记"区分开，否则一条
    未分层的地板可以安静地复用给一条分层的运行。
    """
    fp = design.fingerprint()
    if sampling is None:
        stratification: Any = "simple_random_sampling_no_strata"
        allocation: Any = None
    else:
        stratification = sampling["plan"]["scheme"]
        allocation = sampling["plan"]["allocation"]
    return FloorDescriptor(
        matched={
            "target_population": target_population,
            "injected_alpha": list(injected_alpha) if injected_alpha is not None else None,
            "stratification": stratification,
            "allocation": allocation,
            "n_personas": fp["n_personas"],
            "n_choice_sets_per_persona": fp["n_choice_sets_per_persona"],
            "n_cities": fp["n_cities"],
            "world_calibration": fp["world_calibration"],
            "tier": fp["tier"],
            "noise_source": noise_source,
            "sigma_generating": sigma_generating,
            "estimation_profile": estimation_profile,
        },
        provenance={
            "dataset_digest": dataset_digest,
            "arm_source": arm_source,
            "persona_seed": fp["persona_seed"],
            "world_seed": fp["world_seed"],
            "run_id": run_id,
        },
    )
