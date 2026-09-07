"""被测基座的连通性冒烟测试。**默认不跑。**

    uv run pytest -m live

--------------------------------------------------------------------------
为什么主测试套件必须保持零网络
--------------------------------------------------------------------------

不是因为麻烦，是三条硬理由：

  CI 没有密钥         `.env` 在 .gitignore 里（硬规则 2），GitHub Actions 上
                      不存在这个文件。让主套件依赖它等于让 CI 永远红。
  门是零调用的        门一、P8 的地板自检、P9 的 bootstrap 在设计上就要求
                      零大模型调用（§10.3：在花任何预算之前跑通）。把网络
                      塞进测试会模糊"这一步到底要不要花钱"这条线。
  快与确定            391 个测试跑 1.4 秒。一次网络往返就是几秒，而且会
                      随机失败——一个偶尔变红的套件很快就没人看了。

所以密钥服务的是：这个文件、`scripts/check_env.py`、`scripts/generate_personas.py`
的一次性散文冻结，以及将来的 M4。不服务主套件。
"""

from __future__ import annotations

import pytest

from common.config import ModelsConfig

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def resolved():
    return ModelsConfig.load().resolve("base_b")


def _client(resolved):
    from openai import OpenAI

    return OpenAI(api_key=resolved.api_key, base_url=resolved.base_url)


def test_the_pinned_snapshot_is_visible(resolved):
    """硬规则 6：钉死的快照必须真的存在，不能是打错的字符串。"""
    ids = {m.id for m in _client(resolved).models.list().data}
    assert resolved.model in ids, f"{resolved.model} 不在端点可见的 {len(ids)} 个模型里"


def test_a_minimal_completion_round_trips(resolved):
    reply = _client(resolved).chat.completions.create(
        model=resolved.model,
        messages=[{"role": "user", "content": "Reply with exactly one word: ok"}],
        max_tokens=16,
        extra_body={"enable_thinking": False},
    )
    assert reply.choices[0].message.content
    assert reply.model.startswith("qwen")


def test_thinking_can_be_switched_off(resolved):
    """成本相关，不只是功能相关。

    这个快照默认开启推理：一句"回一个词"的请求实测回了 222 个 completion
    token，其中 218 个是 reasoning。§11.9 的预算按每次约 400 输出 token、
    36,000 次调用估的，推理开着的话输出侧会显著超。

    `enable_thinking=false` 把同一个请求压到 1 个 token。这条测试盯住这个
    开关仍然有效——它一旦失效，预算估计就失去依据。
    """
    client = _client(resolved)
    msg = [{"role": "user", "content": "Reply with exactly one word: ok"}]
    quiet = client.chat.completions.create(
        model=resolved.model,
        messages=msg,
        max_tokens=16,
        extra_body={"enable_thinking": False},
    )
    assert quiet.usage.completion_tokens < 20, (
        f"关掉推理之后仍然用了 {quiet.usage.completion_tokens} 个 token，预算估计要重做"
    )
