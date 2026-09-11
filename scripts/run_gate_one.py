"""跑门一。零大模型调用。

    uv run python scripts/run_gate_one.py

判据全部来自 configs/gate_one.yaml，本脚本不内置任何阈值。结果写进
runs/<run_id>/gate_one.json，含配置哈希——证明判据是事前的（硬规则 20）。
"""

from __future__ import annotations

from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.config import load_yaml
from common.runlog import RunDir
from eval.gates import run_gate_one, write_result

CONFIG = Path("configs/gate_one.yaml")


def main() -> int:
    config = load_yaml(CONFIG)
    print(f"config      {CONFIG}  status={config['meta']['status']}")
    print(
        f"personas    {config['sample']['n_personas']}   tau 梯子 {config['tau_ladder']['values']}"
    )
    print("running (zero language-model calls) ...\n")

    result = run_gate_one(config, CONFIG)
    run = RunDir()
    run.write_meta(config=config, gate="one")
    path = write_result(result, run.path)

    print(
        f"{'tau':>6} {'可行':>8} {'计入':>6} {'median L_rep':>14} {'CI 下沿':>12} "
        f"{'地板(均值)':>12} {'地板(中位)':>12} {'翻转率':>8} {'通过':>6}"
    )
    for r in result["per_rung"]:
        print(
            f"{r['tau']:>6} {r['feasible_fraction']:>8.1%} "
            f"{'是' if r['counts_toward_verdict'] else '否':>5} "
            f"{r['median_l_rep_usd']:>14,.0f} {r['ci_lower_usd']:>12,.0f} "
            f"{r['noise_floor_usd']:>12,.0f} {r['noise_floor_usd_median_within']:>12,.0f} "
            f"{r['flip_probability']:>8.1%} {'是' if r['passes'] else '否':>5}"
        )

    print(f"\n判定：{result['verdict'].upper()}")
    print(f"配置 sha  {result['config_sha']}")
    print(f"产出      {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
