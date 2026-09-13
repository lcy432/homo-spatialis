"""输入 schema 的验证与信息边界（P9 第五节）。"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from econ.utility import FEATURE_LN_P, FEATURE_LN_Y, amenity_feature_name
from eval.recovery import (
    ChoiceDataError,
    ChoiceDataset,
    attributes_from_m2_features,
    condition_number,
    dataset_from_choice_observations,
    design_matrix,
    weakly_identified_dimensions,
    within_task_variation,
)
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS, N_DIMENSIONS

from .synthetic import make_synthetic


def _rows(n_personas=2, n_tasks=2, n_alts=3, seed=0):
    s = make_synthetic(n_personas=n_personas, n_tasks=n_tasks, n_alts=n_alts, seed=seed)
    d = s.dataset
    return dict(
        persona_ids=list(d.persona_ids),
        choice_set_ids=list(d.choice_set_ids),
        alternative_ids=list(d.alternative_ids),
        chosen=d.chosen.copy(),
        features=d.features.copy(),
    )


# --------------------------------------------------------------------------
# 验证
# --------------------------------------------------------------------------


def test_a_choice_set_with_no_chosen_alternative_is_rejected():
    kw = _rows()
    kw["chosen"][:] = False
    with pytest.raises(ChoiceDataError, match="被选项"):
        ChoiceDataset.build(**kw)


def test_a_choice_set_with_two_chosen_alternatives_is_rejected():
    kw = _rows()
    first = int(np.flatnonzero(~kw["chosen"])[0])
    kw["chosen"][first] = True
    with pytest.raises(ChoiceDataError, match="被选项"):
        ChoiceDataset.build(**kw)


def test_a_choice_set_with_one_alternative_is_rejected():
    kw = _rows(n_alts=3)
    keep = [i for i in range(len(kw["persona_ids"])) if kw["choice_set_ids"][i] != "p0000-t0"]
    keep.append(int(np.flatnonzero(kw["chosen"])[0]))
    for key in ("persona_ids", "choice_set_ids", "alternative_ids"):
        kw[key] = [kw[key][i] for i in keep]
    kw["chosen"] = kw["chosen"][keep]
    kw["features"] = kw["features"][keep]
    with pytest.raises(ChoiceDataError, match="至少要两个"):
        ChoiceDataset.build(**kw)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_features_are_rejected(bad):
    kw = _rows()
    kw["features"][3, 2] = bad
    with pytest.raises(ChoiceDataError, match="NaN"):
        ChoiceDataset.build(**kw)


def test_a_choice_set_cannot_belong_to_two_personas():
    kw = _rows()
    kw["persona_ids"][0] = "someone-else"
    with pytest.raises(ChoiceDataError, match="同时挂在"):
        ChoiceDataset.build(**kw)


def test_duplicate_alternative_ids_within_a_choice_set_are_rejected():
    kw = _rows(n_alts=3)
    kw["alternative_ids"][0] = kw["alternative_ids"][1]
    with pytest.raises(ChoiceDataError, match="重复"):
        ChoiceDataset.build(**kw)


def test_wrong_feature_width_is_rejected():
    kw = _rows()
    kw["features"] = kw["features"][:, :4]
    with pytest.raises(ChoiceDataError, match="形状"):
        ChoiceDataset.build(**kw)


def test_missing_stratum_label_is_rejected():
    kw = _rows(n_personas=3)
    with pytest.raises(ChoiceDataError, match="层标签"):
        ChoiceDataset.build(**kw, strata={"p0000": "near"})


# --------------------------------------------------------------------------
# 面板分组与行序
# --------------------------------------------------------------------------


def test_panel_grouping_keeps_every_task_of_a_persona_together():
    s = make_synthetic(n_personas=8, n_tasks=5, n_alts=4, seed=11)
    d = s.dataset
    assert d.n_personas == 8
    assert np.all(d.tasks_per_persona == 5)
    # 同一人设的行必须连续，似然的分段聚合依赖这一点
    boundaries = np.flatnonzero(np.diff(d.row_persona) != 0)
    assert len(boundaries) == d.n_personas - 1
    assert np.all(np.diff(d.row_persona) >= 0)


def test_shuffling_the_input_rows_does_not_change_the_design():
    kw = _rows(n_personas=5, n_tasks=3, n_alts=4, seed=3)
    straight = ChoiceDataset.build(**kw)

    perm = np.random.default_rng(0).permutation(len(kw["persona_ids"]))
    shuffled = ChoiceDataset.build(
        persona_ids=[kw["persona_ids"][i] for i in perm],
        choice_set_ids=[kw["choice_set_ids"][i] for i in perm],
        alternative_ids=[kw["alternative_ids"][i] for i in perm],
        chosen=kw["chosen"][perm],
        features=kw["features"][perm],
    )

    assert straight.persona_ids == shuffled.persona_ids
    assert straight.choice_set_ids == shuffled.choice_set_ids
    assert straight.alternative_ids == shuffled.alternative_ids
    assert np.array_equal(straight.features, shuffled.features)
    assert np.array_equal(straight.chosen, shuffled.chosen)
    assert np.array_equal(straight.chosen_row, shuffled.chosen_row)


# --------------------------------------------------------------------------
# 信息边界：真值不进估计器
# --------------------------------------------------------------------------


def test_the_dataset_has_no_field_that_could_carry_ground_truth():
    """硬规则 1 的同款手法：泄漏必须先骗过类型，而不是骗过 code review。"""
    names = {f.name for f in dataclasses.fields(ChoiceDataset)}
    forbidden = {
        "params",
        "true_weights",
        "true_coefficients",
        "weights",
        "theta",
        "ground_truth",
        "correct_city",
        "welfare_loss",
        "sigma",
    }
    assert names & forbidden == set()


def test_conversion_from_selfcheck_drops_the_true_coefficients():
    from worlds.selfcheck import ChoiceObservation

    rng = np.random.default_rng(4)
    columns = (FEATURE_LN_Y, FEATURE_LN_P, *(amenity_feature_name(d) for d in AMENITY_DIMENSIONS))
    obs = []
    for i in range(3):
        for t in range(2):
            design = rng.normal(size=(4, len(columns)))
            obs.append(
                ChoiceObservation(
                    persona_id=f"p{i}",
                    world_id="w0",
                    tier=f"TIER{t}",
                    city_codes=("a", "b", "c", "d"),
                    columns=columns,
                    design=design,
                    chosen_index=1,
                    true_coefficients=np.full(len(columns), 999.0),
                    sigma=0.05,
                )
            )
    dataset = dataset_from_choice_observations(obs)
    assert dataset.n_personas == 3
    assert dataset.features.shape == (24, N_DIMENSIONS)
    # 真值系数是 999；它不该出现在任何地方
    assert not np.any(np.isclose(dataset.features, 999.0))


def test_the_seven_attributes_reproduce_m2s_own_log_utility(core, persona, world):
    """七个属性乘以七个权重必须等于 M2 自己算的 ln V − ln κ。

    这一条盯的是"评估层不得重新定义福利数学"：物质维是 ln_y_net − ln_p 两列
    之差，系数才是那个和为一的 w₁。写错了照样跑，只是估出来的东西不再是
    README §7.1.1 的那个 w。
    """
    w = np.array(
        [persona.params.w_material, *(persona.params.theta[d] for d in AMENITY_DIMENSIONS)]
    )
    assert abs(w.sum() - 1.0) < 1e-12

    for city in world.city_ids[:4]:
        truth = core.score(persona, city, 2024)
        x = attributes_from_m2_features(truth.features)
        assert x.shape == (N_DIMENSIONS,)
        assert float(x @ w) == pytest.approx(truth.ln_utility - truth.ln_kappa, abs=1e-12)


def test_feature_order_is_the_project_wide_dimension_order():
    s = make_synthetic(n_personas=2, n_tasks=2, n_alts=3, seed=0)
    assert s.dataset.feature_names == ALL_DIMENSIONS


# --------------------------------------------------------------------------
# 条件数
# --------------------------------------------------------------------------


def test_high_collinearity_shows_up_in_the_condition_number():
    s = make_synthetic(n_personas=6, n_tasks=3, n_alts=4, seed=9)
    d = s.dataset
    healthy = condition_number(design_matrix(d.features, 0), within_task=d.row_task)

    sick = d.features.copy()
    sick[:, 2] = sick[:, 1] + 1e-6 * sick[:, 2]  # 两维几乎共线
    ill = condition_number(design_matrix(sick, 0), within_task=d.row_task)

    assert healthy < 30.0
    assert ill > 1e3


def test_a_design_column_with_no_within_task_variation_is_infinite_not_merely_large():
    """不可识别与病态是两回事，返回 inf 而不是一个很大的数。

    注意这里直接喂设计矩阵：把某个**属性**设成常数并不会让对应的设计列变成
    常数，因为差分列是 `x_k − x_b`，随 `x_b` 一起动。
    """
    s = make_synthetic(n_personas=4, n_tasks=2, n_alts=3, seed=2)
    d = s.dataset
    design = design_matrix(d.features, 0)
    assert condition_number(design, within_task=d.row_task) < 1e3
    design[:, 3] = 1.0  # 这一列组内完全没有变异
    assert condition_number(design, within_task=d.row_task) == float("inf")


# --------------------------------------------------------------------------
# 组内变异：条件数抓不到的那一半
# --------------------------------------------------------------------------


def test_a_near_constant_attribute_is_named_not_just_counted():
    """某一维组内几乎不动时，要能说出是**哪一维**，而不只是给一个大数。"""
    s = make_synthetic(n_personas=10, n_tasks=4, n_alts=5, seed=101)
    d = s.dataset
    assert weakly_identified_dimensions(within_task_variation(d.features, d.row_task)) == ()

    crippled = d.features.copy()
    dead = ALL_DIMENSIONS.index("commute")
    crippled[:, dead] = 3.0 + 1e-4 * crippled[:, dead]  # 组内变异小两个数量级
    sd = within_task_variation(crippled, d.row_task)
    assert weakly_identified_dimensions(sd) == ("commute",)
    assert sd[dead] < 0.01 * float(np.median(sd))


def test_the_condition_number_alone_misses_it_when_that_dimension_is_the_base():
    """近乎常数的那一维当基准维时条件数很低，问题却没少——所以两个诊断都要。"""
    s = make_synthetic(n_personas=10, n_tasks=4, n_alts=5, seed=103)
    d = s.dataset
    crippled = d.features.copy()
    dead = ALL_DIMENSIONS.index("commute")
    crippled[:, dead] = 3.0 + 1e-4 * crippled[:, dead]

    as_other = condition_number(design_matrix(crippled, 0), within_task=d.row_task)
    as_base = condition_number(design_matrix(crippled, dead), within_task=d.row_task)
    assert as_other > 100.0  # 差分列 ≈ −基准列
    assert as_base < 30.0  # 条件数在这里什么都没说

    # 按维度的量则是基准维不变的
    sd = within_task_variation(crippled, d.row_task)
    assert weakly_identified_dimensions(sd) == ("commute",)


def test_the_diagnostic_travels_with_the_fitted_result():
    from eval.recovery import RecoveryConfig, fit_recovery

    s = make_synthetic(n_personas=20, n_tasks=3, n_alts=4, seed=107)
    result = fit_recovery(s.dataset, RecoveryConfig(n_draws=40, seed=1, maxiter=100))
    diag = result.diagnostics
    assert len(diag.attribute_within_sd) == N_DIMENSIONS
    assert diag.weakly_identified_dimensions == ()
    assert "weakly_identified_dimensions" in diag.as_dict()
