"""服务测试的共同设置：**任何外网连接或真实模型调用都让测试失败**。

* 套接字只能连回环地址（Starlette 的 TestClient 在进程内调用应用，不走套接字）；
* 真实传输层（千问的 OpenAI 兼容传输、Claude Agent SDK 传输）与 ``openai.OpenAI`` 的构造直接报错；
* 不读项目的 ``.env``：凭证解析看不到任何真实密钥，缺凭证的路径照实报告。
"""

from __future__ import annotations

import ipaddress
import socket

import pytest


class LiveCallAttemptedError(AssertionError):
    pass


def _refuse(*_args, **_kwargs):
    raise LiveCallAttemptedError("服务测试禁止任何真实模型或外网调用")


def _loopback_only(original):
    def connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        try:
            ok = ipaddress.ip_address(host).is_loopback
        except ValueError:
            ok = host in ("localhost",)
        if not ok:
            raise LiveCallAttemptedError(f"服务测试禁止连接外部地址：{host}")
        return original(self, address, *args, **kwargs)

    return connect


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentlayer import life_llm
    from common import config as common_config

    monkeypatch.setattr(socket.socket, "connect", _loopback_only(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _loopback_only(socket.socket.connect_ex))
    monkeypatch.setattr(life_llm.OpenAICompatibleTransport, "__init__", _refuse)
    monkeypatch.setattr(life_llm.OpenAICompatibleTransport, "send", _refuse)
    monkeypatch.setattr(life_llm.ClaudeAgentSdkTransport, "__init__", _refuse)
    monkeypatch.setattr(common_config, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(common_config, "dotenv_conflicts", lambda: {})
    for name in ("DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    try:
        import openai

        monkeypatch.setattr(openai.OpenAI, "__init__", _refuse)
    except ImportError:  # pragma: no cover - openai 是项目依赖
        pass
