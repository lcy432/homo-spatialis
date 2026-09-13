"""估计器的输入 schema 与规范化（P9）。

估计器**只看**选择数据与七个属性。真值权重、真值最优城市、福利损失一律不进
这里——不是靠 review 保证，是靠这个类型没有承载它们的字段（硬规则 1 的同款
手法：让泄漏必须先骗过构造函数）。

--------------------------------------------------------------------------
七个属性从哪来
--------------------------------------------------------------------------

M2 的 ``GroundTruth.features`` 有八列：``ln_y_net``、``ln_p``、六个
``amenity_*``，对应系数 ``+w₁``、``−w₁``、``θ_k``。而定和量表是七维，于是

    ln V − ln κ = w₁·(ln_y_net − ln_p) + Σ_k θ_k·ln A_k

物质维的属性是**两列之差**，系数才是那个和为一的 ``w₁``。这里只做这一步
合并，不重新定义任何福利数学（README §11.7.1.1、MIGRATION B3）。

--------------------------------------------------------------------------
行序被规范化，因此输入行序不改变拟合语义
--------------------------------------------------------------------------

构造时按 (人设, 选择集, 备选) 三级排序。下游的 panel 聚合靠"同一人设的行
连续"这个性质，而把它交给调用方维护是一个不会报错的失败：行序错了，似然
照常收敛，只是把别人的选择算进了你的面板。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from econ.utility import FEATURE_LN_P, FEATURE_LN_Y, amenity_feature_name
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS


class ChoiceDataError(ValueError):
    """输入不满足离散选择数据的最低要求。"""


def attributes_from_m2_features(features: Mapping[str, float]) -> np.ndarray:
    """M2 的八列 features → 七维属性向量，列序为 ``ALL_DIMENSIONS``。

    物质维是 ``ln_y_net − ln_p``；六个舒适度维原样取用。
    """
    material = float(features[FEATURE_LN_Y]) - float(features[FEATURE_LN_P])
    amenities = [float(features[amenity_feature_name(d)]) for d in AMENITY_DIMENSIONS]
    return np.array([material, *amenities], dtype=float)


@dataclass(frozen=True)
class ChoiceDataset:
    """一批人设在若干选择集上的选择，加上每个备选的七个属性。

    ``features`` 的行与 ``persona_ids`` / ``choice_set_ids`` / ``alternative_ids``
    一一对应，列序固定为 ``ALL_DIMENSIONS``。所有索引数组在 ``build`` 里算好，
    因为它们依赖规范行序。

    ``strata`` 是 P10 的分层标签（人设 → 层名），P9 只把它原样带着走，供 block
    bootstrap 在层内重抽；抽样权重与两个报告函数属 P10，不在这里。
    """

    persona_ids: tuple[str, ...]
    choice_set_ids: tuple[str, ...]
    alternative_ids: tuple[str, ...]
    chosen: np.ndarray  # (n_rows,) bool
    features: np.ndarray  # (n_rows, 7)
    strata: Mapping[str, str] | None

    # 规范行序下的索引（build 计算，不要手工传）
    persona_order: tuple[str, ...]
    task_keys: tuple[tuple[str, str], ...]
    row_task: np.ndarray  # (n_rows,) 行属于第几个选择集
    row_persona: np.ndarray  # (n_rows,) 行属于第几个人设
    task_starts: np.ndarray  # (n_tasks,) 每个选择集的首行
    persona_starts: np.ndarray  # (n_personas,) 每个人设的首行
    chosen_row: np.ndarray  # (n_tasks,) 每个选择集被选中的那一行

    feature_names: tuple[str, ...] = ALL_DIMENSIONS

    # ---------------------------------------------------------------- 构造

    @staticmethod
    def build(
        *,
        persona_ids: Sequence[str],
        choice_set_ids: Sequence[str],
        alternative_ids: Sequence[str],
        chosen: Sequence[bool] | np.ndarray,
        features: np.ndarray,
        strata: Mapping[str, str] | None = None,
    ) -> ChoiceDataset:
        n = len(persona_ids)
        if not (len(choice_set_ids) == len(alternative_ids) == len(chosen) == n):
            raise ChoiceDataError("五个行级数组长度必须一致")
        if n == 0:
            raise ChoiceDataError("空数据集")

        x = np.asarray(features, dtype=float)
        if x.shape != (n, len(ALL_DIMENSIONS)):
            raise ChoiceDataError(
                f"features 形状必须是 ({n}, {len(ALL_DIMENSIONS)})，得到 {x.shape}"
            )
        if not np.all(np.isfinite(x)):
            raise ChoiceDataError("features 含 NaN 或 Inf")

        y = np.asarray(chosen, dtype=bool)
        pid = list(persona_ids)
        cid = list(choice_set_ids)
        aid = list(alternative_ids)

        # 一个选择集只能属于一个人设，否则 panel 聚合没有定义
        owner: dict[str, str] = {}
        for p, c in zip(pid, cid, strict=True):
            if owner.setdefault(c, p) != p:
                raise ChoiceDataError(f"选择集 {c!r} 同时挂在 {owner[c]!r} 与 {p!r} 之下")

        # 规范排序：人设 → 选择集 → 备选。输入行序因此不影响任何下游语义。
        order = sorted(range(n), key=lambda i: (pid[i], cid[i], aid[i]))
        idx = np.array(order, dtype=int)
        pid = [pid[i] for i in order]
        cid = [cid[i] for i in order]
        aid = [aid[i] for i in order]
        x = x[idx]
        y = y[idx]

        persona_order = tuple(dict.fromkeys(pid))
        persona_index = {p: i for i, p in enumerate(persona_order)}
        task_keys: list[tuple[str, str]] = []
        seen: dict[tuple[str, str], int] = {}
        row_task = np.empty(n, dtype=int)
        for r, (p, c) in enumerate(zip(pid, cid, strict=True)):
            key = (p, c)
            if key not in seen:
                seen[key] = len(task_keys)
                task_keys.append(key)
            row_task[r] = seen[key]

        row_persona = np.array([persona_index[p] for p in pid], dtype=int)
        task_starts = np.flatnonzero(np.r_[True, row_task[1:] != row_task[:-1]])
        persona_starts = np.flatnonzero(np.r_[True, row_persona[1:] != row_persona[:-1]])

        counts = np.diff(np.r_[task_starts, n])
        if np.any(counts < 2):
            bad = task_keys[int(np.argmin(counts))]
            raise ChoiceDataError(f"选择集 {bad!r} 只有 {counts.min()} 个备选，至少要两个")

        n_chosen = np.add.reduceat(y.astype(int), task_starts)
        if not np.all(n_chosen == 1):
            bad = task_keys[int(np.flatnonzero(n_chosen != 1)[0])]
            raise ChoiceDataError(
                f"选择集 {bad!r} 有 {int(n_chosen[np.flatnonzero(n_chosen != 1)[0]])} 个被选项，"
                "必须恰好一个"
            )
        chosen_row = np.flatnonzero(y)

        for start, count in zip(task_starts, counts, strict=True):
            block = aid[start : start + count]
            if len(set(block)) != count:
                raise ChoiceDataError(f"选择集 {task_keys[row_task[start]]!r} 里备选 id 重复")

        if strata is not None:
            missing = [p for p in persona_order if p not in strata]
            if missing:
                raise ChoiceDataError(f"{len(missing)} 个人设没有层标签，例如 {missing[0]!r}")

        return ChoiceDataset(
            persona_ids=tuple(pid),
            choice_set_ids=tuple(cid),
            alternative_ids=tuple(aid),
            chosen=y,
            features=x,
            strata=dict(strata) if strata is not None else None,
            persona_order=persona_order,
            task_keys=tuple(task_keys),
            row_task=row_task,
            row_persona=row_persona,
            task_starts=task_starts,
            persona_starts=persona_starts,
            chosen_row=chosen_row,
        )

    # ---------------------------------------------------------------- 性质

    @property
    def n_rows(self) -> int:
        return len(self.persona_ids)

    @property
    def n_personas(self) -> int:
        return len(self.persona_order)

    @property
    def n_tasks(self) -> int:
        return len(self.task_keys)

    @property
    def tasks_per_persona(self) -> np.ndarray:
        task_persona = self.row_persona[self.task_starts]
        return np.bincount(task_persona, minlength=self.n_personas)

    @property
    def task_persona(self) -> np.ndarray:
        """(n_tasks,) 每个选择集属于第几个人设。"""
        return self.row_persona[self.task_starts]

    # ---------------------------------------------------------------- 重抽

    def take_personas(self, picks: Sequence[str], *, suffix: bool = True) -> ChoiceDataset:
        """按人设取子集，允许重复。

        **重复抽中的人设必须拿到新的 cluster id**，否则后端会把两个副本当成
        同一个面板，等于把这个人的选择次数翻倍——那会人为收窄个体后验，而
        block bootstrap 的整个目的正是度量个体后验的不确定性。
        """
        rows_of: dict[str, np.ndarray] = {}
        for i, p in enumerate(self.persona_order):
            start = self.persona_starts[i]
            stop = self.persona_starts[i + 1] if i + 1 < self.n_personas else self.n_rows
            rows_of[p] = np.arange(start, stop)

        missing = [p for p in picks if p not in rows_of]
        if missing:
            raise ChoiceDataError(f"未知人设 {missing[0]!r}")

        seen: dict[str, int] = {}
        take: list[np.ndarray] = []
        new_pid: list[str] = []
        new_cid: list[str] = []
        for p in picks:
            k = seen.get(p, 0)
            seen[p] = k + 1
            tag = f"{p}#{k}" if (suffix and k > 0) else p
            rows = rows_of[p]
            take.append(rows)
            new_pid.extend([tag] * len(rows))
            new_cid.extend(
                (f"{self.choice_set_ids[r]}#{k}" if (suffix and k > 0) else self.choice_set_ids[r])
                for r in rows
            )

        sel = np.concatenate(take)
        strata = None
        if self.strata is not None:
            # 副本继承原人设的层：tag 是 "<pid>#k"，切回去就是原 id。
            strata = {tag: self.strata[tag.split("#", 1)[0]] for tag in dict.fromkeys(new_pid)}

        return ChoiceDataset.build(
            persona_ids=new_pid,
            choice_set_ids=new_cid,
            alternative_ids=[self.alternative_ids[r] for r in sel],
            chosen=self.chosen[sel],
            features=self.features[sel],
            strata=strata,
        )


def dataset_from_choice_observations(
    observations: Sequence[object],
    *,
    strata: Mapping[str, str] | None = None,
) -> ChoiceDataset:
    """``worlds.selfcheck.ChoiceObservation`` → ``ChoiceDataset``。

    刻意**丢掉** ``true_coefficients`` 与 ``sigma``：它们是真值侧的，估计器
    不许看见。这个转换函数是那条边界在代码上的位置。
    """
    persona_ids: list[str] = []
    choice_set_ids: list[str] = []
    alternative_ids: list[str] = []
    chosen: list[bool] = []
    rows: list[np.ndarray] = []
    counter: dict[tuple[str, str, str], int] = {}

    for obs in observations:
        key = (obs.persona_id, obs.world_id, obs.tier)  # type: ignore[attr-defined]
        n = counter.get(key, 0)
        counter[key] = n + 1
        # 人设 id 必须进选择集 id：不同人设会在同一个 (世界, 档) 上各有一个
        # 情境，只按 (世界, 档) 编号会让它们撞成同一个选择集。
        task_id = f"{obs.persona_id}|{obs.world_id}|{obs.tier}|{n}"  # type: ignore[attr-defined]
        columns: tuple[str, ...] = obs.columns  # type: ignore[attr-defined]
        design: np.ndarray = obs.design  # type: ignore[attr-defined]
        for j, code in enumerate(obs.city_codes):  # type: ignore[attr-defined]
            feats = dict(zip(columns, design[j], strict=True))
            persona_ids.append(obs.persona_id)  # type: ignore[attr-defined]
            choice_set_ids.append(task_id)
            alternative_ids.append(code)
            chosen.append(j == obs.chosen_index)  # type: ignore[attr-defined]
            rows.append(attributes_from_m2_features(feats))

    return ChoiceDataset.build(
        persona_ids=persona_ids,
        choice_set_ids=choice_set_ids,
        alternative_ids=alternative_ids,
        chosen=chosen,
        features=np.array(rows, dtype=float),
        strata=strata,
    )
