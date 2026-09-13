"""P9 测试的 fixtures。数据生成器在 ``synthetic.py``。"""

from __future__ import annotations

import pytest

from .synthetic import SyntheticChoices, make_synthetic


@pytest.fixture(scope="module")
def synthetic() -> SyntheticChoices:
    return make_synthetic(n_personas=120, n_tasks=6, n_alts=7, sigma=0.08, seed=20260912)


@pytest.fixture(scope="module")
def small() -> SyntheticChoices:
    return make_synthetic(n_personas=30, n_tasks=4, n_alts=5, sigma=0.1, seed=5)
