from __future__ import annotations

import pytest

from product.realdata import build

from .support import block_network, fixture_ctx


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture(scope="module")
def fixture_build(tmp_path_factory):
    """真实小样本上的完整构建（不经过 LangGraph），同一测试文件里共用一次。"""
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        ctx = fixture_ctx(tmp_path_factory.mktemp("intake_work"))
        refs = build.run_all(ctx)
    return ctx, refs
