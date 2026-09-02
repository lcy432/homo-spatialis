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
from common.config import ConfigError, ModelsConfig

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
    for role in ("base_a", "base_b"):
        env = models.spec(role).api_key_env
        present = bool(os.environ.get(env))
        print(f"{OK if present else WARN} {role}: {env} {'set' if present else 'not set'}")


def check_snapshots(models: ModelsConfig) -> None:
    print("\n-- model snapshots (CLAUDE.md hard rule 6) --")
    for role in ("base_a", "base_b"):
        m = models.spec(role).model
        if m is None:
            print(f"{WARN} {role}: no snapshot pinned; this role will raise if used")
        else:
            print(f"{OK} {role}: {m}")


def check_connectivity(models: ModelsConfig, role: str = "base_b") -> None:
    """List snapshot-shaped model ids the key can see, to help pin `model`."""
    print(f"\n-- connectivity: {role} --")
    spec = models.spec(role)
    if not os.environ.get(spec.api_key_env):
        print(f"{WARN} skipped: {spec.api_key_env} not set")
        return
    if spec.base_url == "TBD":
        print(f"{WARN} skipped: base_url not configured yet")
        return
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ[spec.api_key_env], base_url=spec.base_url)
        ids = sorted(m.id for m in client.models.list().data)
    except Exception as exc:
        print(f"{BAD} could not list models: {type(exc).__name__}: {exc}")
        return
    dated = [i for i in ids if _SNAPSHOT_SHAPED.search(i)]
    print(f"{OK} endpoint reachable, {len(ids)} models visible, {len(dated)} snapshot-shaped")
    for i in dated[:40]:
        print(f"       {i}")
    if not dated:
        print(f"{WARN} no dated snapshots visible; pin one from the provider console instead")


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
