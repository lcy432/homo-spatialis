# 0038 按规模选真实 LLM 测试的模型

日期：2026-10-19（所有者决定）。部分取代 [0035](0035-haiku-test-planner-agent-sdk.md) 里"真实 LLM 测试一律用 Haiku 5.5"的那一条；传输层、订阅认证与可选计费不变。

## 决定

| 测试规模 | 模型 | 入口 |
|---|---|---|
| 非全量：先导、小名单、部分城市或少量分身、几年以内的试跑 | Claude Sonnet 5.5，effort max（`test_planner_sonnet`，走本机订阅，不计费） | `scripts/run_life_e1_7b.py`（不写 `--config` 时用 `configs/life/run_product_sonnet.yaml`） |
| 全规模：走完整产品链，资料包里所有可用城市、每城 4 个及以上分身、完整年限；或部分城市但大量分身、超过 3 年 | Qwen 最新的 Flash（`product_planner`，现在是 `qwen3.8-flash`） | `scripts/run_product_full.py` |

Haiku（`test_planner`）与 Max（`base_b`）只在所有者明确要求时用 `--config` 显式选。

## 全规模入口怎么做

- 产品链与 `product.scenarios` 的离线闭环相同：资料包 → 答案草稿 → 审阅 → 确认 → 编译 → E1 预检查 → 运行 → 评价与汇总。
- 规模参数：`--repeats`（每城分身数，不少于 4，改资料包的 `run.repeat_indexes`）、`--years`（改工作区里那份生活输入的模拟年限，仓库样例不动）。
- 只把编译出来的 mock 规划器换成 Flash；模型调用设定与报价从 `configs/life/run_product_flash.yaml` 读，不另写一套。
- 付费：每次运行在运行目录里开一本新账本，次数上限 = 年度数 × 3 份候选 × 2 次服务请求，费用上限 `--max-cost-cny`（默认 10 元，按报价估算）。
- `--mock` 用同一条产品链、mock 规划器，零网络零费用；`tests/product/test_run_product_full.py` 用它覆盖入口。

## 理由

小规模试跑看计划质量，Sonnet max 在订阅额度里，不花钱；全规模要几十到上百次调用，Flash 单价低（2026-10-19 的 6 个年度试跑约 0.11 元），也是产品角色本身。
