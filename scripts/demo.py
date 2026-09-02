"""最小端到端示例：`uv run python scripts/demo.py`

M0/M2/M3 已经落地，所以这里跑的是真链条：配置 → run 目录 → 合成世界 →
人设 → 四档难度 → 真值 logit 模拟选择。**一次模型调用都没有**，因为
研究模式的前六块本来就不需要（§10.3 甚至要求先零成本自检一遍）。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.config import ExperimentConfig, ModelsConfig
from common.runlog import RunDir
from worlds.difficulty import ScenarioInfeasibleError, Tier, instantiate
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.selfcheck import hit_rate_of_truth, simulate_choices

CONFIG = Path("configs/experiment.example.yaml")
N_PERSONAS = 200


def main() -> int:
    cfg = ExperimentConfig.load(CONFIG)
    spec = ModelsConfig.load().spec(cfg.model_role)

    run = RunDir()
    run.write_meta(config=cfg.model_dump(), model_role=cfg.model_role, model=spec.model)

    generator = WorldGenerator()
    world = generator.generate(seed=cfg.seed, reference_persona=reference_persona())
    tax = generator.tax()
    personas = PersonaGenerator().generate(N_PERSONAS, seed=cfg.seed)
    lookup = {p.persona_id: p for p in personas}

    print(f"run_id        {run.run_id}")
    print(f"config        {cfg.name}  seed={cfg.seed}")
    print(f"reference     {cfg.reference_id}   numeraire {cfg.numeraire}")
    print(f"model role    {cfg.model_role} -> {spec.model or '(no snapshot pinned)'}")
    print()
    print(
        f"world         {world.world_id}: {len(world)} cities, "
        f"condition number {world.condition_number:.1f}"
    )
    print(f"personas      {len(personas)}, Dirichlet alpha 0.5")
    print()
    print(f"{'tier':<13}{'feasible':>10}{'distinct answers':>19}{'truth hit rate':>17}")

    for tier in Tier:
        scenarios = []
        for persona in personas:
            # demo 只统计可行性；真实实验里 ScenarioInfeasibleError 必须抛，
            # 静默跳过会让"某档命中率"里混进根本不是那一档的样本。
            with contextlib.suppress(ScenarioInfeasibleError):
                scenarios.append(instantiate(tier, world, persona, tax))
        answers = {s.correct_city.code for s in scenarios if s.correct_city is not None}
        observations = simulate_choices(scenarios, lookup, sigma=0.05, seed=cfg.seed)
        rate = hit_rate_of_truth(observations)
        shown = str(len(answers)) if answers else "n/a (tie)"
        print(f"{tier.name:<13}{len(scenarios):>10}{shown:>19}{rate:>17.3f}")

    print()
    print("no language model was called. the truth hit rate is the ceiling a")
    print("model is compared against -- not 1.0, because sigma makes even the")
    print("truth model miss sometimes.")
    print(f"run dir       {run.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
