"""跑 P8 自检。零大模型调用。

uv run python scripts/run_p8.py                 # 用 configs/p8.yaml
uv run python scripts/run_p8.py --config X.yaml --out runs/my-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

import _bootstrap  # noqa: F401  # 把 src/ 放上 path
from common.runlog import new_run_id
from eval.floors import run_p8, write_run


def main() -> None:
    parser = argparse.ArgumentParser(description="P8 估计器自检（零大模型调用）")
    parser.add_argument("--config", type=Path, default=Path("configs/p8.yaml"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    payload = run_p8(config, args.config)
    out = args.out or Path("runs") / f"{new_run_id()}-p8"
    path = write_run(payload, out)
    print(f"P8 产出写入 {path}")
    print(f"  门二 {payload['verdicts']['gate_two']}  门三 {payload['verdicts']['gate_three']}")


if __name__ == "__main__":
    main()
