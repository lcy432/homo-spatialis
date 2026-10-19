"""全仓库共享的 fixtures：一个真实规模的合成世界与一批人设。

放在根层是因为 eval 那边（基准线、扰动、门）也要用同一个世界——两处各造
一个的话，“同一批人设投到同一个世界”这句话就不成立了，而两条轴正交的
接口约束（§11.9）正是建立在它上面。

tests/econ/conftest.py 用同名 fixture 覆盖了 core / persona 等，那边是三维
舒适度的小世界，用来测纯函数与类型契约。就近的 conftest 优先，两套互不
干扰。"""

from __future__ import annotations

import os

# 并行测试（pytest -n auto，每个核一个进程）时，numpy 背后的 OpenBLAS / MKL 默认又按核数各开一批线程，
# 20 个进程 × 20 个线程会把内存分配挤爆（OpenBLAS: Memory allocation still failed）。测试里一律单线程；
# 必须在任何 import numpy 之前设置，子进程（scripts/ 的命令行测试）继承同一份环境。
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import pytest  # noqa: E402

from worlds.difficulty import Tier, instantiate  # noqa: E402
from worlds.generator import WorldGenerator  # noqa: E402
from worlds.personas import PersonaGenerator, reference_persona  # noqa: E402

SEED = 20260902

#: 研究线（M2 静态福利模型、M3 合成世界、M5 估计与门）的测试：开发期（所有者 2026-10-18）日常不跑，统一标 slow，
#: 用 pytest -m slow 显式打开。生活模型、产品、工作流与共用模块（common.money、common.ids 等）的测试照常跑。
RESEARCH_TESTS = (
    "tests/eval/",
    "tests/econ/",
    "tests/worlds/",
    "tests/test_baselines.py",
    "tests/test_perturbation.py",
    "tests/test_weights.py",
)


def pytest_collection_modifyitems(config, items):
    root = config.rootpath
    for item in items:
        rel = item.path.relative_to(root).as_posix()
        if rel.startswith(RESEARCH_TESTS):
            item.add_marker(pytest.mark.slow)


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
