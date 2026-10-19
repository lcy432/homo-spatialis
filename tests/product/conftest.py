"""E3-A 测试的共同设置：**任何真实模型或网络调用都让测试失败**（发送入口断言）。

* E1 的真实模型传输（``agentlayer.life_llm.OpenAICompatibleTransport``）的构造与发送都被替换成直接报错；
* ``openai.OpenAI`` 的构造与 ``httpx`` 的发送也被替换成直接报错。

E3-A 的全部代码路径只用可控的 mock 规划器；这些断言保证测试一旦尝试真实调用就失败，而不是悄悄花钱或依赖网络。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


class LiveCallAttemptedError(AssertionError):
    pass


def _refuse(*_args, **_kwargs):
    raise LiveCallAttemptedError("E3-A 测试禁止任何真实模型或网络调用")


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from agentlayer import life_llm

    monkeypatch.setattr(life_llm.OpenAICompatibleTransport, "__init__", _refuse)
    monkeypatch.setattr(life_llm.OpenAICompatibleTransport, "send", _refuse)
    monkeypatch.setattr(httpx.Client, "send", _refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse)
    try:
        import openai

        monkeypatch.setattr(openai.OpenAI, "__init__", _refuse)
    except ImportError:  # pragma: no cover - openai 是项目依赖
        pass


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO
