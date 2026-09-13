"""M3 可识别性诊断（P9 第八节、第十四节 14.5）。只读，不改任何生成。"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import (
    ChoiceDataset,
    identifiability_report,
    simplex_tangent_basis,
    unique_variation,
    within_centered,
)
from worlds.dimensions import ALL_DIMENSIONS, N_DIMENSIONS

from .synthetic import make_synthetic


def _rebuild(dataset: ChoiceDataset, features: np.ndarray) -> ChoiceDataset:
    return ChoiceDataset.build(
        persona_ids=list(dataset.persona_ids),
        choice_set_ids=list(dataset.choice_set_ids),
        alternative_ids=list(dataset.alternative_ids),
        chosen=dataset.chosen,
        features=features,
    )


def test_the_tangent_basis_is_orthonormal_and_kills_the_all_ones_vector():
    q = simplex_tangent_basis()
    assert q.shape == (N_DIMENSIONS, N_DIMENSIONS - 1)
    assert np.allclose(q.T @ q, np.eye(N_DIMENSIONS - 1), atol=1e-14)
    assert np.abs(q.T @ np.ones(N_DIMENSIONS)).max() < 1e-14


def test_within_centering_removes_exactly_the_choice_set_means():
    s = make_synthetic(n_personas=5, n_tasks=3, n_alts=4, seed=1)
    d = s.dataset
    c = within_centered(d.features, d.row_task)
    sums = np.zeros((d.n_tasks, N_DIMENSIONS))
    np.add.at(sums, d.row_task, c)
    assert np.abs(sums).max() < 1e-12  # 每个选择集内每一列的和为零


def test_a_near_constant_dimension_is_flagged():
    s = make_synthetic(n_personas=12, n_tasks=4, n_alts=5, seed=101)
    clean = identifiability_report(s.dataset)
    assert clean.flagged(threshold=0.10) == ()

    dead = ALL_DIMENSIONS.index("commute")
    x = s.dataset.features.copy()
    x[:, dead] = 3.0 + 1e-4 * x[:, dead]
    report = identifiability_report(_rebuild(s.dataset, x))

    assert report.flagged(threshold=0.10) == ("commute",)
    assert report.within_sd_relative[dead] < 0.01
    assert report.unique_sd_relative[dead] < 0.01


def test_a_dimension_that_varies_but_is_explained_by_the_others_is_also_flagged():
    """``within_sd`` 大而 ``unique_sd`` 小——单看前者会放过它。"""
    s = make_synthetic(n_personas=12, n_tasks=4, n_alts=5, seed=103)
    x = s.dataset.features.copy()
    victim = ALL_DIMENSIONS.index("leisure")
    x[:, victim] = 0.5 * x[:, 1] + 0.5 * x[:, 2] + 1e-4 * x[:, victim]
    report = identifiability_report(_rebuild(s.dataset, x))

    assert report.within_sd_relative[victim] > 0.5  # 它明明在动
    assert report.unique_sd_relative[victim] < 0.01  # 但动的部分是别人的
    assert "leisure" in report.flagged(threshold=0.10)


def test_the_weakest_tangent_direction_names_the_offending_combination():
    s = make_synthetic(n_personas=12, n_tasks=4, n_alts=6, seed=107)
    x = s.dataset.features.copy()
    dead = ALL_DIMENSIONS.index("commute")
    x[:, dead] = 3.0 + 1e-4 * x[:, dead]
    report = identifiability_report(_rebuild(s.dataset, x))

    direction = np.abs(report.weakest_direction)
    assert int(np.argmax(direction)) == dead

    # 要的性质是**谱上出现一个缺口**，不是最小奇异值低于某个绝对数：后者依赖
    # 候选数与属性尺度，写死一个绝对阈值只是在拟合这一个 fixture。
    sv = report.tangent_singular_values
    assert sv[-1] < 0.5 * sv[-2]

    healthy = identifiability_report(s.dataset)
    assert healthy.tangent_singular_values[-1] > 0.9 * healthy.tangent_singular_values[-2]
    assert report.tangent_condition_number > 2 * healthy.tangent_condition_number


def test_rescaling_a_column_cannot_hide_its_lack_of_within_choice_variation():
    """整列乘一个大常数只放大了尺度，组内相对变异不变——诊断不能被这样骗过。

    这是 ``design.condition_number`` 必须先等尺度化、而切空间谱**不能**等尺度化
    的同一件事的两面：前者问共线，后者问信息量。
    """
    s = make_synthetic(n_personas=12, n_tasks=4, n_alts=5, seed=109)
    x = s.dataset.features.copy()
    dead = ALL_DIMENSIONS.index("commute")
    x[:, dead] = 3.0 + 1e-4 * x[:, dead]
    plain = identifiability_report(_rebuild(s.dataset, x))

    blown = x.copy()
    blown[:, dead] *= 1000.0
    scaled = identifiability_report(_rebuild(s.dataset, blown))

    # 放大之后绝对 sd 变大了……
    assert scaled.within_sd[dead] > 100 * plain.within_sd[dead]
    # ……但相对量级只是跟着整体标度走，仍然被判为弱
    assert "commute" in scaled.flagged(threshold=0.10)


def test_changing_the_difference_base_does_not_change_the_diagnostic():
    """诊断是基准维无关的——这正是条件数做不到的那一半。"""
    s = make_synthetic(n_personas=10, n_tasks=4, n_alts=5, seed=113)
    x = s.dataset.features.copy()
    dead = ALL_DIMENSIONS.index("commute")
    x[:, dead] = 3.0 + 1e-4 * x[:, dead]
    ds = _rebuild(s.dataset, x)
    report = identifiability_report(ds)

    from eval.recovery import condition_number, design_matrix

    conds = {
        d: condition_number(design_matrix(ds.features, i), within_task=ds.row_task)
        for i, d in enumerate(ALL_DIMENSIONS)
    }
    # 条件数随基准维剧烈变化（把死掉的那维当基准时反而很低）
    assert conds["commute"] < 30.0
    assert max(conds.values()) > 100.0
    # 诊断不随基准维变化——它根本不接基准维
    assert report.flagged(threshold=0.10) == ("commute",)


def test_a_healthy_dataset_has_a_flat_tangent_spectrum():
    s = make_synthetic(n_personas=40, n_tasks=4, n_alts=6, seed=127)
    report = identifiability_report(s.dataset)
    assert report.flagged(threshold=0.10) == ()
    assert report.tangent_condition_number < 2.0
    assert min(report.tangent_singular_values) > 0.5


def test_the_report_is_json_shaped():
    import json

    s = make_synthetic(n_personas=8, n_tasks=3, n_alts=4, seed=131)
    text = json.dumps(identifiability_report(s.dataset).as_dict())
    assert len(json.loads(text)["within_sd"]) == N_DIMENSIONS
    assert len(json.loads(text)["tangent_singular_values"]) == N_DIMENSIONS - 1


def test_unique_variation_matches_a_direct_least_squares_residual():
    rng = np.random.default_rng(3)
    m = rng.normal(size=(200, N_DIMENSIONS))
    got = unique_variation(m)
    others = np.delete(m, 2, axis=1)
    coef, *_ = np.linalg.lstsq(others, m[:, 2], rcond=None)
    assert got[2] == pytest.approx(float((m[:, 2] - others @ coef).std()), rel=1e-12)
