"""散文生成与三道闸。用桩客户端，不碰网络。"""

from __future__ import annotations

import json

import pytest

from worlds.dimensions import ALL_DIMENSIONS, MATERIAL
from worlds.narrative import (
    DIMENSION_PROSE,
    NarrativeError,
    attach_frozen,
    freeze,
    generate_narratives,
    qualitative_brief,
)
from worlds.personas import PersonaGenerator


class CleanStub:
    """写一段干净散文的桩。不含数字，不排序。"""

    def __init__(self):
        self.prompts: list[str] = []

    def write(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return (
            "I would rather live somewhere quiet than somewhere lively. "
            "I put up with a small place if the street feels calm at night."
        )


class LeakyStub:
    """把权重原样写进散文的桩。用来证明闸门真的会拦。"""

    def __init__(self, persona):
        self.persona = persona

    def write(self, system: str, user: str) -> str:
        w = self.persona.params.theta
        return "My weights are " + ", ".join(f"{v:.4f}" for v in w.values())


@pytest.fixture
def few():
    return PersonaGenerator().generate(12, seed=41)


# --------------------------------------------------------------------------
# 模型看不到数字
# --------------------------------------------------------------------------


def test_the_brief_contains_no_digits(few):
    """定性化的全部意义：模型没见过数字，就写不出数字。这比"写完再扫"强，
    因为扫描只能发现已经发生的泄漏。"""
    for p in few:
        brief = qualitative_brief(p, tuple(ALL_DIMENSIONS))
        assert not any(ch.isdigit() for ch in brief)


def test_the_brief_uses_everyday_wording_not_project_jargon(few):
    brief = qualitative_brief(few[0], tuple(ALL_DIMENSIONS))
    assert "amenity" not in brief.lower()
    assert "weight" not in brief.lower()
    assert DIMENSION_PROSE[MATERIAL] in brief


def test_the_brief_follows_the_given_order(few):
    order = tuple(reversed(ALL_DIMENSIONS))
    brief = qualitative_brief(few[0], order)
    positions = [brief.index(DIMENSION_PROSE[d]) for d in order]
    assert positions == sorted(positions)


def test_a_dominant_weight_gets_the_dominant_bucket():
    from worlds.narrative import _bucket

    assert "dominates" in _bucket(0.55)
    assert "barely" in _bucket(0.01)


# --------------------------------------------------------------------------
# 三道闸
# --------------------------------------------------------------------------


def test_clean_narratives_pass_and_are_attached(few):
    stub = CleanStub()
    out = generate_narratives(few, stub, seed=2)
    assert len(out) == len(few)
    for p in out:
        assert p.brief.narrative
        assert set(p.brief.dimension_order) == set(ALL_DIMENSIONS)
    assert len(stub.prompts) == len(few)


def test_a_leaked_weight_vector_is_caught(few):
    """闸门真的会拦，不是装饰。"""
    with pytest.raises(NarrativeError, match="没通过闸门"):
        generate_narratives([few[0]], LeakyStub(few[0]), seed=2, max_retries=2)


def test_an_empty_narrative_is_caught(few):
    class Empty:
        def write(self, system: str, user: str) -> str:
            return "   "

    with pytest.raises(NarrativeError):
        generate_narratives([few[0]], Empty(), seed=2, max_retries=1)


def test_narration_order_is_randomised_across_personas(few):
    out = generate_narratives(few, CleanStub(), seed=2)
    orders = {p.brief.dimension_order for p in out}
    assert len(orders) > 1, "所有人设用了同一个叙述顺序，随机化没生效"


def test_a_generator_that_sorts_by_weight_is_caught(monkeypatch, few):
    """把泄漏实现出来，确认人群层面的顺序检验会拦住它。

    这条检验的价值全在于它抓得住真正的失败模式：一个按权重降序铺陈维度
    的生成器，散文里一个数字都没有，前两道闸完全放行。
    """
    import worlds.narrative as narrative_module

    many = PersonaGenerator().generate(80, seed=42)

    def weights_of(p):
        return {MATERIAL: p.params.w_material, **dict(p.params.theta)}

    sorted_orders = iter(
        [tuple(sorted(ALL_DIMENSIONS, key=lambda d: -weights_of(p)[d])) for p in many]
    )

    class SortingRng:
        def permutation(self, _values):
            return list(next(sorted_orders))

    monkeypatch.setattr(narrative_module.np.random, "default_rng", lambda seed: SortingRng())
    with pytest.raises(NarrativeError, match="秩序信息已泄漏"):
        generate_narratives(many, CleanStub(), seed=2)


# --------------------------------------------------------------------------
# 冻结
# --------------------------------------------------------------------------


def test_frozen_file_holds_no_weights(tmp_path, few):
    out = generate_narratives(few, CleanStub(), seed=2)
    path = freeze(out, tmp_path / "p.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    blob = json.dumps(payload)
    assert "alpha" not in blob and "theta" not in blob
    for row in payload:
        assert set(row) == {"persona_id", "literalness", "narrative", "dimension_order"}


def test_reattaching_reproduces_the_same_personas(tmp_path, few):
    """权重由 seed 决定可以重算，散文不行——所以只冻结散文，再贴回去。"""
    out = generate_narratives(few, CleanStub(), seed=2)
    path = freeze(out, tmp_path / "p.json")
    fresh = PersonaGenerator().generate(12, seed=41)
    rebuilt = attach_frozen(fresh, path)
    for a, b in zip(out, rebuilt, strict=True):
        assert a.brief.narrative == b.brief.narrative
        assert a.brief.dimension_order == b.brief.dimension_order
        assert a.params == b.params


def test_a_missing_narrative_is_refused_not_skipped(tmp_path, few):
    """静默跳过会得到一批没有散文的人设，而它们在实验里看起来跟
    "无人设对照组"一模一样。"""
    out = generate_narratives(few[:3], CleanStub(), seed=2)
    path = freeze(out, tmp_path / "p.json")
    with pytest.raises(NarrativeError, match="冻结文件里没有"):
        attach_frozen(few, path)
