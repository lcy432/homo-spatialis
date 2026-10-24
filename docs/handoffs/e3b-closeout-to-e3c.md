# E3B 最后收尾交给托管服务（E3B → E3C）

日期：2026-10-24。规则见 [ADR0044](../../DECISIONS/0044-e3b-mapping-daily-support.md) 的“最后收尾”一节，请求大小与费用见 [E3B 映射修正](../design/e3b-mapping-and-daily-support.md) §10。只列 E3C 要接收或核对的东西；本轮没有另写第二套检查。

1. **提交 `1b3201c`（E3C 负责的边界）**：`ProductPreflight` 对本次运行绑定的全部首年请求做输入检查，超过上限每城一条 `planning_request_too_large`；`E1RunAdapter.start` 在把预留变成已使用之前用真正的运行编号再核对，超限停在 `request_too_large`、预留退回，同键重发不多扣也不多退。文件：`src/product/preflight.py`、`src/workflows/e3_adapters.py`、`service_runs.py`、`service_worker.py`、`state.py`、`tests/product_service/test_request_size.py`。预检查、启动前复核与发送前检查共用 `orchestration.life_runner.year_request` / `first_year_input_checks` 与 `agentlayer.life_llm.input_size_check`。请 E3C 接收并与并行修改核对。
2. **本轮的预算与护栏配置**（所有者 2026-10-24）：
   * `configs/product/service.yaml` 的 `max_cost_cny` 从 "10" 改为 "20"（每次六城 × 4 分身 × 3 年的运行），沿用原有的费用预留、停止与恢复；`live_calls_enabled` 仍是 false。
   * `configs/life/run_product_flash.yaml` 的 `max_input_tokens` 从 128000 改为 192000（服务读这份的调用设定）；输出上限 4,096、每年最多 2 次修订、服务重试不变。
   * 新测试 `tests/product_service/test_budget_config.py`：生产配置得出的运行设置是 20 元、192,000 输入、72 次基础规划（216 份候选、432 次服务请求上限）；按护栏顶格每次预留 0.164660 元，第 122 次被原有规则拒绝。
3. **E3C 自己的文件**：本轮只改了上面两份配置，没有动 E3C 的代码与文档。提交时 HEAD 里 `src/product/service/config.py` 与 `src/product/execution.py` 的默认值还是 10，`docs/design/e3c-service.md`、`docs/handoffs/e3c-to-e5-service.md`、[ADR0043](../../DECISIONS/0043-e3c-hosted-service.md) 也还写着 10 元；并行窗口未提交的修改里默认值已经改成 20 并加了按次的费用选项，由 E3C 提交时统一。`tests/product_service/test_budget_config.py` 按 HEAD 的 `build_execution_spec` 写，E3C 若改了它的签名请一并调整。另外，那份未提交的修改让 `scripts/product_e3a.py samples` 在共享检出里跑到服务样例时失败（`ServiceConfig` 不再接受 `repeat_indexes`），并会删掉 60–85 号服务样例：本轮的公共样例是在只含 HEAD 与 E3B 改动的干净工作树里导出的，E3C 提交时请重新导出服务样例。
4. 预计花费不等于停机预算：72 次基础规划预计约 7.7–8.1 元，按 mock 的修订率约 11.1–11.7 元；每次规划都用满 2 次修订约 23.3–24.5 元，会在约 176 次调用时被 20 元停下。这些是项目估计 token，不是服务商 usage。
