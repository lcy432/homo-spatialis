# 付费预算账本

每本账一个目录，里面是 `ledger.sqlite`（`orchestration.life_budget`）：`header` 表一行（额度与报价，建立后不改），`events` 表逐条记预留、核算与不确定。运行配置的 `paid_budget.ledger_dir` 指向这里的目录。

账本记着已经花了多少钱，所以进 git、不随 `runs/` 清理；换运行编号或运行目录都读同一本账，额度不会重置。不要手工改。

- `qwen-max-e1-7b-pilot/`：`configs/life/run_e1_7b_live.yaml`（千问 Max）
- `qwen-flash-product/`：`configs/life/run_e1_7b_flash.yaml`（千问 Flash）
- `qwen-flash-convenience/`：`configs/life/run_e1_convenience_flash.yaml`（便利版 Flash）
