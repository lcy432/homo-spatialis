"""逐人表：回答"**谁**没有被还原"。

`DECISIONS/0015` 第八节。**复用 P9 已有的个体后验，不按个体或新群体另拟合。**

--------------------------------------------------------------------------
三个问题，三套量，不互相冒充
--------------------------------------------------------------------------

    群体协方差       异质性整体是否被压缩
    逐人配对误差     哪些偏好组合没被还原   ← 本模块
    福利损失         实际代价

它们回答不同问题（`DECISIONS/0015` 裁决二），**不作为互相印证的独立证据重复
计数**。尤其：群体协方差比与 `proj` 已经机械耦合（硬规则 17），再把逐人误差
当第三条独立证据，等于把同一件事数三遍。

--------------------------------------------------------------------------
两条不能越的线
--------------------------------------------------------------------------

**权重误差大 ≠ AI 理解错了。** P8 的合成地板在同一组人上也可能差——那是测量
装置的极限，不是被测对象的错。所以每一行都带 `floor_rmse`，并在地板同样差时
标 `measurement_limited`。

**不对每个人做小分母的 `r` 比值。** `r = (s_matched − λ)/(s_shuffled − λ)` 是
斜率的比值，个体层面没有对应的分母；硬造一个会得到一堆爆炸的数，然后被读成
"这个人被坍缩了 17 倍"。个体层面只报**误差**与**分组对照**。

**不造"纯 AI 个体错误"标签。** 均值误差与协方差结果共用同一次拟合，不独立。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

#: 分组依据只能是**真值**或**事前记录的特征**，绝不能是回收出来的权重——
#: 按回收结果定义人群，等于让被测对象自己划分考区。
GroupingBasis = Literal["true_weights", "ex_ante_feature", "ex_ante_stratum"]

#: 发现阶段与确认阶段。规则必须在看确认数据之前冻结。
Stage = Literal["discovery", "confirmation"]


@dataclass(frozen=True)
class PersonRow:
    """一个人的一行。真值、回收、误差、事前特征、覆盖、代价。"""

    persona_id: str
    true_weights: np.ndarray  # (7,)
    recovered_weights: np.ndarray  # (7,)
    stratum: str | None
    z: float | None
    d: float | None
    arm: str
    n_choice_sets: int
    world_ids: tuple[str, ...] = ()
    welfare_loss_usd: float | None = None
    welfare_loss_normalised: float | None = None
    floor_rmse: float | None = None
    flags: tuple[str, ...] = ()

    @property
    def signed_error(self) -> np.ndarray:
        return self.recovered_weights - self.true_weights

    @property
    def rmse(self) -> float:
        return float(np.sqrt((self.signed_error**2).mean()))

    def as_dict(self, dimensions: Sequence[str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "persona_id": self.persona_id,
            "stratum": self.stratum,
            "z": self.z,
            "d": self.d,
            "arm": self.arm,
            "n_choice_sets": self.n_choice_sets,
            "n_worlds": len(self.world_ids),
            "rmse": self.rmse,
            "welfare_loss_usd": self.welfare_loss_usd,
            "welfare_loss_normalised": self.welfare_loss_normalised,
            "floor_rmse": self.floor_rmse,
            "flags": list(self.flags),
        }
        for k, name in enumerate(dimensions):
            out[f"true_{name}"] = float(self.true_weights[k])
            out[f"recovered_{name}"] = float(self.recovered_weights[k])
            out[f"error_{name}"] = float(self.signed_error[k])
        return out


@dataclass(frozen=True)
class IndividualTable:
    """整张表。**七维全留**，不删难测的 commute。"""

    rows: tuple[PersonRow, ...]
    dimensions: tuple[str, ...]
    arm: str
    stage: Stage = "discovery"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.rows)

    def as_records(self) -> list[dict[str, Any]]:
        return [r.as_dict(self.dimensions) for r in self.rows]

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "stage": self.stage,
            "n": len(self.rows),
            "dimensions": list(self.dimensions),
            "all_seven_dimensions_retained": len(self.dimensions) == 7,
            "notes": list(self.notes),
            "caveats": [
                "权重误差大不自动等于 AI 理解错误；地板同样差时标 measurement_limited",
                "不做个体层面的 r 比值（无对应分母）",
                "均值误差与协方差结果共用同一次拟合，**不独立**",
            ],
            "records": self.as_records(),
        }


def build_table(
    *,
    persona_ids: Sequence[str],
    true_weights: np.ndarray,
    recovered_weights: np.ndarray,
    dimensions: Sequence[str],
    arm: str,
    stratum_of: Mapping[str, str] | None = None,
    z_of: Mapping[str, float] | None = None,
    d: np.ndarray | None = None,
    n_choice_sets: int = 0,
    welfare_loss_usd: Mapping[str, float] | None = None,
    welfare_loss_normalised: Mapping[str, float] | None = None,
    floor_rmse: Mapping[str, float] | None = None,
    measurement_limited_ratio: float = 0.9,
) -> IndividualTable:
    """拼一张逐人表。**复用已有的个体后验**，这里不拟合任何东西。

    `measurement_limited_ratio`：当本臂的个体 RMSE 不超过地板 RMSE 的这个倍数
    以上（即 `rmse <= floor_rmse / ratio` 不成立、两者相当）时标记。默认 0.9
    表示"地板已经占了九成误差"就算测量受限。
    """
    tw = np.asarray(true_weights, dtype=float)
    rw = np.asarray(recovered_weights, dtype=float)
    if tw.shape != rw.shape:
        raise ValueError(f"真值 {tw.shape} 与回收 {rw.shape} 形状不一致")
    if tw.shape[1] != len(dimensions):
        raise ValueError(f"维数 {tw.shape[1]} 与维名 {len(dimensions)} 不一致")

    rows: list[PersonRow] = []
    for i, pid in enumerate(persona_ids):
        fr = None if floor_rmse is None else floor_rmse.get(pid)
        rmse = float(np.sqrt(((rw[i] - tw[i]) ** 2).mean()))
        flags: list[str] = []
        if fr is not None and fr > 0 and rmse <= fr / measurement_limited_ratio:
            flags.append("measurement_limited")
        rows.append(
            PersonRow(
                persona_id=pid,
                true_weights=tw[i],
                recovered_weights=rw[i],
                stratum=None if stratum_of is None else stratum_of.get(pid),
                z=None if z_of is None else z_of.get(pid),
                d=None if d is None else float(d[i]),
                arm=arm,
                n_choice_sets=n_choice_sets,
                welfare_loss_usd=None if welfare_loss_usd is None else welfare_loss_usd.get(pid),
                welfare_loss_normalised=None
                if welfare_loss_normalised is None
                else welfare_loss_normalised.get(pid),
                floor_rmse=fr,
                flags=tuple(flags),
            )
        )
    return IndividualTable(rows=tuple(rows), dimensions=tuple(dimensions), arm=arm)


def group_by(
    table: IndividualTable,
    grouping: Mapping[str, str],
    *,
    basis: GroupingBasis,
) -> dict[str, Any]:
    """按**事前**分组汇总。`basis` 必填，用来挡住"按回收权重分组"。"""
    if basis not in ("true_weights", "ex_ante_feature", "ex_ante_stratum"):
        raise ValueError(f"分组依据必须是事前的或真值的，得到 {basis!r}")
    groups: dict[str, list[PersonRow]] = {}
    for r in table.rows:
        groups.setdefault(grouping.get(r.persona_id, "__ungrouped__"), []).append(r)
    out = {}
    for name, members in sorted(groups.items()):
        err = np.array([m.signed_error for m in members])
        out[name] = {
            "n": len(members),
            "rmse_by_dim": [float(v) for v in np.sqrt((err**2).mean(axis=0))],
            "bias_by_dim": [float(v) for v in err.mean(axis=0)],
            "mean_rmse": float(np.mean([m.rmse for m in members])),
            "n_measurement_limited": sum(1 for m in members if "measurement_limited" in m.flags),
        }
    return {
        "grouping_basis": basis,
        "basis_is_never_recovered_weights": True,
        "dimensions": list(table.dimensions),
        "groups": out,
    }


def compare_arms(
    tables: Mapping[str, IndividualTable],
    grouping: Mapping[str, str],
    *,
    basis: GroupingBasis,
) -> dict[str, Any]:
    """同一真值分组下，matched / shuffled / P8 参照的对照。

    **同一组里地板也差**，就说明那组的困难来自装置而不是被测对象。这是本模块
    存在的主要理由：没有这个对照，一组大的权重误差会被直接读成"AI 不理解
    这类人"。
    """
    per_arm = {arm: group_by(t, grouping, basis=basis) for arm, t in tables.items()}
    names: set[str] = set()
    for g in per_arm.values():
        names.update(g["groups"])

    verdicts = {}
    for name in sorted(names):
        entry = {arm: g["groups"].get(name, {}).get("mean_rmse") for arm, g in per_arm.items()}
        floor = entry.get("floor")
        matched = entry.get("matched")
        if floor is not None and matched is not None and floor > 0:
            ratio = matched / floor
            verdicts[name] = {
                "mean_rmse_by_arm": entry,
                "matched_over_floor": ratio,
                "reading": (
                    "地板占主导，这一组的困难主要来自测量装置"
                    if ratio < 1.2
                    else "匹配臂明显差于地板，超出装置极限的部分才可能是理解误差"
                ),
            }
        else:
            verdicts[name] = {"mean_rmse_by_arm": entry, "reading": "缺臂，无法对照"}
    return {
        "grouping_basis": basis,
        "by_group": verdicts,
        "caveat": "福利损失按原规则报告、**不扣地板**（硬规则 18）；只有坍缩比例扣",
    }
