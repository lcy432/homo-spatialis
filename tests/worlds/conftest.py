"""M3 的 fixtures。世界与人设分开构造，因为两条轴必须正交。"""

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
