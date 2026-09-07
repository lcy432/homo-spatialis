"""全仓库共享的 fixtures：一个真实规模的合成世界与一批人设。

放在根层是因为 eval 那边（基准线、扰动、门）也要用同一个世界——两处各造
一个的话，“同一批人设投到同一个世界”这句话就不成立了，而两条轴正交的
接口约束（§11.9）正是建立在它上面。

tests/econ/conftest.py 用同名 fixture 覆盖了 core / persona 等，那边是三维
舒适度的小世界，用来测纯函数与类型契约。就近的 conftest 优先，两套互不
干扰。"""

from __future__ import annotations

import pytest

from worlds.difficulty import Tier, instantiate
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona

SEED = 20260902


@pytest.fixture(scope="session")
def ref_persona():
    return reference_persona()


@pytest.fixture(scope="session")
def generator():
    return WorldGenerator()


@pytest.fixture(scope="session")
def world(generator, ref_persona):
    return generator.generate(seed=SEED, reference_persona=ref_persona)


@pytest.fixture(scope="session")
def worlds(generator, ref_persona):
    return generator.generate_many(3, base_seed=SEED, reference_persona=ref_persona)


@pytest.fixture(scope="session")
def tax(generator):
    return generator.tax()


@pytest.fixture(scope="session")
def personas():
    return PersonaGenerator().generate(24, seed=7)


@pytest.fixture(scope="session")
def persona(personas):
    return personas[1]


def scenarios(world, personas, tax, tier: Tier):
    return [instantiate(tier, world, p, tax) for p in personas]


@pytest.fixture(scope="session")
def core(world, tax):
    """真实规模的 WelfareCore：十二座城市、六维舒适度。

    与 tests/econ/conftest.py 的同名 fixture 不同——那个是三维的小世界，
    用来测纯函数与类型契约。这个用来测在实际抽样分布下的行为。
    """
    from common.money import USD_2024_US100
    from econ.core import WelfareCore

    return WelfareCore(world.cities, tax, numeraire=USD_2024_US100, reference_id="SYNTH_2024")


@pytest.fixture(scope="session")
def city_a(world):
    return world.city_ids[0]


@pytest.fixture(scope="session")
def city_b(world):
    return world.city_ids[1]
