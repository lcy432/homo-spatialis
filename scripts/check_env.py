"""One-shot environment check: toolchain, keys, base-model connectivity.

Operational, not production code, which is why it lives in scripts/ rather than
src/. Run it with `make check-env`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.config import ConfigError, ModelsConfig, dotenv_conflicts, key_source

OK, WARN, BAD = "[ ok ]", "[warn]", "[fail]"
_SNAPSHOT_SHAPED = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def _tool(name: str, args: list[str]) -> None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
        first = (out.stdout or out.stderr).strip().splitlines()
        print(f"{OK} {name}: {first[0] if first else 'present'}")
    except (OSError, subprocess.SubprocessError):
        print(f"{WARN} {name}: not found on PATH")


def check_toolchain() -> None:
    print("-- toolchain --")
    v = sys.version_info
    tag = OK if (v.major, v.minor) >= (3, 11) else BAD
    print(f"{tag} python: {v.major}.{v.minor}.{v.micro} (need >= 3.11)")
    _tool("uv", ["uv", "--version"])
    _tool("git", ["git", "--version"])
    _tool("make", ["make", "--version"])


def check_keys(models: ModelsConfig) -> None:
    print("\n-- keys (values never printed) --")
    for name in sorted(dotenv_conflicts()):
        # 静默故障：两处取值不同时进程环境赢。旧的那个让探针报 401，
        # 而 401 看起来像凭证坏了，不像配置冲突。
        print(f"{BAD} {name}: 进程环境与 .env 取值不同，进程环境优先。")
        print("       若进程环境里那份是旧的，探针会报 401。删掉系统环境变量里的")
        print("       那一份，或删掉 .env 里那一行。")
    for role in ("base_a", "base_b"):
        env = models.spec(role).api_key_env
        source = key_source(env)
        present = source != "not found"
        detail = f"from {source}" if present else "not set"
        print(f"{OK if present else WARN} {role}: {env} {detail}")
    if not any(key_source(models.spec(r).api_key_env) != "not found" for r in ("base_a", "base_b")):
        print(
            f"{WARN} 没有任何密钥可见。若你确信已经设过：Windows 只把改动交给之后启动的\n"
            "       进程，已运行的进程继承的是旧副本。开一个新终端，或把密钥写进 .env。"
        )


def check_snapshots(models: ModelsConfig) -> None:
    print("\n-- model snapshots (CLAUDE.md hard rule 6) --")
    for role in ("base_a", "base_b"):
        m = models.spec(role).model
        if m is None:
            print(f"{WARN} {role}: no snapshot pinned; this role will raise if used")
        else:
            print(f"{OK} {role}: {m}")


def check_connectivity(models: ModelsConfig, role: str = "base_b") -> None:
    """探活并列出可用的快照。

    两级探针，顺序有讲究。先试 GET /models，因为它免费且能列出快照名；
    失败时**不下结论**，改用一次最小的 chat/completions 再判。

    这个回退是有来历的：2026-09-02 那次排查里，一个刚创建的 key 在
    GET /models 上返回 401 invalid_api_key，几十分钟后同一个 key 同一段
    代码返回 200——大概率是新 key 的生效延迟。当时只有单级探针，于是
    "阿里云说这个 key 无效"被当成了"这个 key 是坏的"，白绕了一圈。

    教训不是"多试一个端点"，是**一个探针失败只能说明那个探针失败**。
    所以两级都挂了才报 fail，只挂一级报 warn 并说明差异。
    """
    print(f"\n-- connectivity: {role} --")
    spec = models.spec(role)
    if not os.environ.get(spec.api_key_env):
        print(f"{WARN} skipped: {spec.api_key_env} not set")
        return
    if spec.base_url == "TBD":
        print(f"{WARN} skipped: base_url not configured yet")
        return

    from openai import OpenAI

    client = OpenAI(api_key=os.environ[spec.api_key_env], base_url=spec.base_url)

    listed: list[str] = []
    list_error: str | None = None
    try:
        listed = sorted(m.id for m in client.models.list().data)
    except Exception as exc:
        list_error = f"{type(exc).__name__}: {exc}"

    chat_ok = False
    chat_error: str | None = None
    if spec.model is not None:
        try:
            client.chat.completions.create(
                model=spec.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            chat_ok = True
        except Exception as exc:
            chat_error = f"{type(exc).__name__}: {exc}"

    if listed:
        dated = [i for i in listed if _SNAPSHOT_SHAPED.search(i)]
        print(f"{OK} GET /models: {len(listed)} visible, {len(dated)} snapshot-shaped")
        if spec.model and spec.model not in listed:
            print(f"{WARN} pinned model {spec.model!r} is not in the visible list")
    else:
        print(f"{WARN} GET /models failed: {list_error}")

    if spec.model is None:
        print(f"{WARN} chat probe skipped: no snapshot pinned")
    elif chat_ok:
        print(f"{OK} POST /chat/completions with {spec.model}: reachable")
    else:
        print(f"{BAD} POST /chat/completions failed: {chat_error}")

    if not listed and not chat_ok and spec.model is not None:
        print(f"{BAD} both probes failed -- treat the credential as the suspect")
    elif not listed and chat_ok:
        print(
            f"{WARN} the listing endpoint refused but chat works. The key is fine; "
            "do not conclude otherwise from a single failing probe."
        )


def main() -> int:
    check_toolchain()
    try:
        models = ModelsConfig.load()
    except ConfigError as exc:
        print(f"\n{BAD} configs/models.yaml: {exc}")
        return 1
    check_keys(models)
    check_snapshots(models)
    check_connectivity(models)
    print("\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
