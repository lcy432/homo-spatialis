"""一次性脚本：生成一千个人设的散文并冻结。

**只跑一次。** 散文是唯一要花调用成本、也是唯一必须冻结的部分：权重由
seed 决定可以随时重算，散文不行——每跑一次实验就重生成一批的话，两次
实验的结果不可比，"人设"这个自变量就不再固定。

    uv run python scripts/generate_personas.py --n 1000 --seed 20260902

跑完检查 data/frozen/personas_v1.json，人工过一遍再提交。三道闸在
worlds.narrative 里自动执行，任何一道不过就抛，不会写出半成品。
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.config import ModelsConfig
from worlds.narrative import FROZEN_PATH, DashScopeNarrativeClient, freeze, generate_narratives
from worlds.personas import PersonaGenerator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--role", default="base_b")
    parser.add_argument("--out", default=str(FROZEN_PATH))
    parser.add_argument("--dry-run", action="store_true", help="只打印一个 brief，不调用模型")
    args = parser.parse_args()

    personas = PersonaGenerator().generate(args.n, seed=args.seed)

    if args.dry_run:
        from worlds.dimensions import ALL_DIMENSIONS
        from worlds.narrative import qualitative_brief

        print(qualitative_brief(personas[0], tuple(ALL_DIMENSIONS)))
        print("\n（这就是模型能看到的全部。没有任何数字。）")
        return 0

    resolved = ModelsConfig.load().resolve(args.role)
    client = DashScopeNarrativeClient(
        model=resolved.model, base_url=resolved.base_url, api_key=resolved.api_key
    )
    print(f"generating {args.n} narratives with {resolved.model} ...")
    written = generate_narratives(personas, client, seed=args.seed)
    path = freeze(written, __import__("pathlib").Path(args.out))
    print(f"frozen to {path}")
    print("Review it by hand before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
