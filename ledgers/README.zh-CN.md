# 付费预算账本

[English](README.md) · 简体中文

每本账一个目录，里面是 `ledger.sqlite`，由 `orchestration.life_budget` 管理：`header` 表保存建立时的额度与报价，`events` 表逐条记录预留、已核算用量与不确定用量。运行配置的 `paid_budget.ledger_dir` 指向所用账本。

这里的共享账本跨运行保留已用额度，并纳入 Git。换运行编号或输出目录不会重置共享账本；清理本机输出时应保留它们，不要手工修改。

全规模启动脚本 `scripts/run_product_full.py` 则为每次运行单独建立 `<out>/ws/budget/ledger.sqlite`。配置步骤与费用上限见[真实模型运行指南](../docs/REPRODUCING.md#real-models)。

- `qwen-flash-convenience/` 用于直接运行配置 `configs/life/run_product_flash.yaml`；全规模启动脚本会将此路径替换为本次运行自己的账本。
- `qwen-max-e1-7b-pilot/` 和 `qwen-flash-product/` 保留早期费用记录，原先的运行配置已经退役。
