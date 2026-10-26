# Paid-call budget ledgers

English · [简体中文](README.zh-CN.md)

Each ledger directory contains `ledger.sqlite`, managed by `orchestration.life_budget`. Its `header` table fixes the limits and price record; its `events` table records reservations, settled usage, and uncertain usage. The run configuration's `paid_budget.ledger_dir` selects the ledger.

These shared ledgers retain spending across runs and are tracked in Git. Changing a run ID or output directory does not reset a shared ledger. Keep them when clearing local outputs, and do not edit them by hand.

The full-run launcher, `scripts/run_product_full.py`, instead creates a separate ledger at `<out>/ws/budget/ledger.sqlite` for each run. See [running with a real model](../docs/REPRODUCING.en.md#real-models) for setup and limits.

- `qwen-flash-convenience/` is used by the direct run configuration `configs/life/run_product_flash.yaml`. The full-run launcher replaces this path with its own per-run budget.
- `qwen-max-e1-7b-pilot/` and `qwen-flash-product/` retain earlier spending records. Their original run configurations have been retired.
