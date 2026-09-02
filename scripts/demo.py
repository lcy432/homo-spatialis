"""The minimal end-to-end example: `make demo`.

At M0 the chain it exercises is config -> run identity -> metadata -> call log.
It touches no language model and no real data on purpose: those arrive in M2 and
M4, and until then this is what proves a clone can produce a run directory in
one command.
"""

from __future__ import annotations

from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.config import ExperimentConfig, ModelsConfig
from common.money import USD_2024_US100, Money
from common.runlog import CallRecord, RunDir

CONFIG = Path("configs/experiment.example.yaml")


def main() -> int:
    cfg = ExperimentConfig.load(CONFIG)
    models = ModelsConfig.load()
    spec = models.spec(cfg.model_role)

    run = RunDir()
    run.write_meta(
        config=cfg.model_dump(),
        model_role=cfg.model_role,
        model=spec.model,  # None until a snapshot is pinned
        provider=spec.provider,
    )

    # A placeholder record in the real schema, so the log format is exercised
    # from the first commit rather than invented later under deadline.
    run.log_call(
        CallRecord(
            run_id=run.run_id,
            seed=cfg.seed,
            role=cfg.model_role,
            model=spec.model or "unpinned-0000-00-00",
            request={"note": "M0 placeholder; no endpoint was contacted"},
            response={"note": "no response"},
            latency_ms=0.0,
        )
    )

    rent = Money(2400.0, USD_2024_US100)
    income = Money(7100.0, USD_2024_US100)
    burden = rent / income  # dimensionless, so it is a plain float

    print(f"run_id        {run.run_id}")
    print(f"run dir       {run.path}")
    print(f"config        {cfg.name}  seed={cfg.seed}")
    print(f"reference     {cfg.reference_id}")
    print(f"numeraire     {cfg.numeraire}")
    print(f"model role    {cfg.model_role} -> {spec.model or '(no snapshot pinned)'}")
    print(f"calls logged  {len(run.read_calls())}")
    print(f"sample calc   rent {rent} / income {income} = burden {burden:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
