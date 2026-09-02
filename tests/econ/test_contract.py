"""The same assertions, run against the real core once it exists.

To wire it up, expose a zero-argument ``build_core()`` in ``econ.core`` that
returns something satisfying the EconomicCore protocol, together with a persona
and a city set to exercise it. Until then these tests skip, and the message says
what is missing rather than reporting a false pass.
"""

import pytest

from econ.types import EconomicCore

try:  # pragma: no cover - the whole point is that this is absent for now
    from econ.core import build_core  # type: ignore[attr-defined]
except ImportError:
    build_core = None

needs_real_core = pytest.mark.skipif(
    build_core is None,
    reason="econ.core.build_core is not implemented yet; M2's first version is written by hand",
)


@needs_real_core
def test_real_core_satisfies_the_protocol():
    assert isinstance(build_core(), EconomicCore)


@needs_real_core
def test_real_core_passes_the_single_count_assertion():
    core = build_core()
    graph_for = getattr(core, "graph_for", None)
    assert graph_for is not None, (
        "the real core must expose graph_for(persona, city) -> UtilityGraph so that "
        "README §10.1 can traverse the utility computation graph"
    )
