"""intake-02 测试的共用工具：真实小样本的位置、离线构建上下文、断网守卫。

``tests/fixtures/intake/cache/`` 是 ``scripts/intake_fixtures.py`` 从真实原始文件切出的子集（芝加哥都会区），每份样本的
``.meta.json`` 记着原文件的 SHA-256 与切法。样本只为检验流程，分布不代表现实。
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from product.realdata.build import BuildContext
from product.realdata.config import load_config

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "intake"
CONFIG = FIXTURES / "config_chicago.yaml"
CACHE = FIXTURES / "cache"


def fixture_ctx(work: Path, cache: Path = CACHE, **kwargs) -> BuildContext:
    return BuildContext(config=load_config(CONFIG), work=work, cache=cache, **kwargs)


def _refuse(*_args, **_kwargs):
    raise OSError("intake-02 的测试不联网：只读保存的小样本")


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
