"""相关结构：因子模型，不是两两手填。"""

from __future__ import annotations

import numpy as np
import pytest

from worlds.attributes import LATENT_ORDER, AttributeCalibration, FactorLoadings
from worlds.dimensions import AMENITY_DIMENSIONS


def test_correlation_matrix_is_positive_semidefinite_by_construction():
    """因子模型的全部意义。第一版两两手填的矩阵有一个 -0.12 的特征值，
    也就是说那组数不对应任何真实存在的联合分布。"""
    m = AttributeCalibration().correlation_matrix()
    assert np.linalg.eigvalsh(m).min() > 0.0


def test_it_stays_valid_across_a_wide_range_of_loadings():
    for a in (0.2, 0.45, 0.7):
        for r in (0.3, 0.6, 0.86):
            cal = AttributeCalibration(loadings=FactorLoadings(a_amen=a, r_prod=r))
            assert np.linalg.eigvalsh(cal.correlation_matrix()).min() >= -1e-12


def test_the_three_roback_signs():
    """空间均衡 + 补偿性差异。第三条是单因子模型做不到的那一条。"""
    c = AttributeCalibration().implied_correlations()
    assert c["wage~rent"] > 0.5, "工资与房租强正相关，这是空间均衡本身"
    assert c["amenity~rent"] > 0.0, "好地方贵，这是补偿性差异"
    assert c["amenity~wage"] < 0.0, "好地方工资低——单因子结构下拿不到这个负号"


def test_rent_disperses_far_more_than_goods():
    """§5.2 的核心机制。两者接近的话个人化价格指数就失去区分力，
    项目的卖点当场消失。"""
    c = AttributeCalibration()
    assert c.ln_rent_sd > 5 * c.ln_goods_sd


def test_loadings_must_leave_room_for_idiosyncratic_variance():
    with pytest.raises(ValueError, match="不小于一"):
        FactorLoadings(w_prod=0.99, w_amen=0.30)


def test_the_amenity_loading_on_wage_is_hard_coded_negative():
    """符号不是可配的。它就是补偿性差异本身；改成正的，Roback 结构就没了。"""
    prod, amen = FactorLoadings().loading_vector("wage")
    assert prod > 0 and amen < 0


def test_latent_order_covers_every_amenity():
    assert set(AMENITY_DIMENSIONS) <= set(LATENT_ORDER)
