# 项目索引

**层与命名、轮次与状态、默认入口都只在这里写一次。** 其他文档不再各写一遍状态，只链接过来。改了状态、默认配置或命名，只改这一份。

更新：2026-10-19。

## 0. 现在怎么工作（开发期）

所有者 2026-10-18 决定：开发期按**普通软件项目**来做，不按研究项目。开发完成后冻结一个版本（git tag），再按研究标准一次性重跑研究问题。

| 保留 | 开发期不再做 |
|---|---|
| 自动化测试（单元、集成）与 lint，每次改动跑全量 | 每轮的旧版本逐字节比较、`expected_differences.md` |
| 安全检查：密钥不进仓库和日志；请求里不混进答案侧信息（研究真值、评分参数）；Agent SDK 的环境隔离 | 每轮保存源码快照 `historical_source/` |
| 花钱或占额度的保护：真实调用前确认、全局调用上限 | 每轮手工变异检查 |
| 改 `src/econ/` 之前先说明理由与数学含义 | 为保留旧路径而开新版本号、新旧代码永久并存 |
| git 是历史与回退手段：每实现完一个功能、测试与 lint 通过后就提交一次（只提交本次改动的文件）；推送由所有者决定 | 不准动旧归档；把旧归档逐字节钉死的测试 |
| | 每轮证据目录 README、prompt 原文归档、开工基线指纹、等独立审核才算完成 |

运行清单不再记源码指纹：改了代码，旧运行照样可以续跑或重放（配置、规则、事实等内容身份照旧核对）。只在真正的设计决定时写简短 ADR（`DECISIONS/`）。

## 1. 层与命名

旧的 E 编号与 M 编号都退役，统一用"层"。旧编号只在本表与历史文档里出现。

| 层 | 做什么 | 旧 E 编号 | 旧 M 编号 | 代码 | 主要说明 |
|---|---|---|---|---|---|
| **生活引擎** engine | 分身按月过日子：结算、评分、时间精力、职业、储蓄、疾病、跨期评价、城市汇总 | E1-1 – E1-6、E1 补充；E2 已并入 | M2 的生活部分 | `src/econ/life_*`、`src/common/life_*`、`src/ledger/life_*` | [e1-3](design/e1-3-life-model.md)、[e1-4](design/e1-4-time-energy.md)、[e1-5](design/e1-5-career-savings-risk.md)、[e1-6](design/e1-6-evaluation.md) |
| **代理层** agent | 规划接口、事实记忆、mock 与真实模型、传输层、预算、归档与重放 | E1-7A / E1-7B | M4 | `src/agentlayer/`、`src/orchestration/` | [e1-7](design/e1-7-agent-loop.md) |
| **接入层** intake | 用户输入、职业匹配、标准初态、真实资料、共享消息结构 | E3（含 E3-A） | M1、M1.5 | `src/product/`、`src/common/product_contracts.py`、`contracts/product/` | [共享约定](design/e3-e5-product-contract.md)、[E3-A](design/e3a-product-input.md) |
| **界面层** ui | 表单、进度、结果展示、网页入口 | E5 | M6 | `web/`（`src/ui/` 未使用） | [E5 交接](handoffs/e5-product-handoff.md)、[E5-A](design/e5-a-offline-ui.md) |
| **研究线** research | 大模型偏好理解评估（P8/P9/P10、三个门）、合成世界与人设、信息条件实验 | R、E4 | M3、M5 | `src/eval/`、`src/worlds/` | [README](../README.md) 第 3、7、10、11 章 |

旧的福利模型核心（`src/econ/core.py`、`kernel.py`、`utility.py` 等，M2）属于研究线的真值标尺；跨国扩展（M7）是 V2，暂不归层。

轮次编号：`层-序号`，例如 `engine-05c`、`agent-04`。已经写进 ADR 与 `runs/` 目录名的旧编号不改。

## 2. 轮次与状态

状态只用：**计划中** / **进行中** / **已实现**。"已实现"表示代码在、测试通过；"备注"里注明它曾经过的研究期独立审核（开发期不再要求）。

### 生活引擎

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| engine-01 | E1-1 | 契约：数据、行动、状态、信息边界 | 已实现 | | [0016](../DECISIONS/0016-life-model-contract.md) |
| engine-02 | E1-2 | 年度计划、月度结算、最低生活 | 已实现 | | [0017](../DECISIONS/0017-life-monthly-settlement.md) |
| engine-03 | E1-3 | 探索月度评分；功能计分、覆盖报告 | 已实现 | | [0018](../DECISIONS/0018-life-exploratory-monthly-scoring.md)、[0019](../DECISIONS/0019-life-e1-3-supplement.md) |
| engine-04a | E1-4A | 月度时间与精力约束 | 已实现 | 曾通过审核 | [0020](../DECISIONS/0020-life-monthly-time-energy.md) |
| engine-04b | E1-4B | 满足感 → 下一年精力额度 | 已实现 | 曾通过审核 | [0021](../DECISIONS/0021-life-satisfaction-energy-feedback.md) |
| engine-05a | E1-5A | 培训 → 岗位 → 收入；成长与交通评分；评分内容绑定 | 已实现 | 曾通过审核 | [0023](../DECISIONS/0023-life-training-abilities-jobs.md)、[0024](../DECISIONS/0024-life-growth-satisfaction-and-transport.md)、[0025](../DECISIONS/0025-life-scoring-content-binding.md) |
| engine-05b | E1-5B | 目标储蓄 | 已实现 | 曾通过审核 | [0026](../DECISIONS/0026-life-savings-goals.md) |
| engine-05c | E1-5C | 疾病、应急与恢复 | 已实现 | 曾通过审核 | [0027](../DECISIONS/0027-life-illness-emergency-recovery.md) |
| engine-06a | E1-6A | 单分身跨期评价 L / V / J | 已实现 | | [0028](../DECISIONS/0028-life-single-clone-evaluation.md) |
| engine-06b | E1-6B | 城市汇总 C / E / M | 已实现 | 曾通过审核 | [0029](../DECISIONS/0029-life-e1-6a-closeout-and-city-aggregation.md) |
| engine-07 | E1 补充 | 洗衣推广为日常生活便利 | 已实现 | 曾通过审核；交给接入层的说明见 [交接](handoffs/e1-daily-convenience-to-e3.md) | [0033](../DECISIONS/0033-life-daily-convenience.md) |
| engine-08 | — | 只保留产品版：每部分一套（规则、评分、模板、mock、样例），旧版本与兼容路径删除；测试改为产品样例上的精简套件 | 已实现 | 删除前的代码：git 标签 `pre-legacy-removal` | [0037](../DECISIONS/0037-life-keep-only-product-version.md) |

### 代理层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| agent-01 | E1-7A | mock 规划闭环、事实记忆、重放与恢复 | 已实现 | 曾通过审核 | [0030](../DECISIONS/0030-life-e1-7a-agent-planning-loop.md) |
| agent-02 | E1-7B | 真实千问接入、预算账本、服务记录 | 已实现 | 曾通过审核 | [0031](../DECISIONS/0031-life-e1-7b-live-model-pilot.md) |
| agent-03 | — | 产品角色用 Qwen3.8 Flash | 已实现 | 曾通过审核 | [0032](../DECISIONS/0032-product-planner-default-qwen3-8-flash.md) |
| agent-04 | — | 真实 LLM 测试用 Claude Haiku 5.5：Agent SDK 单轮调用、厂商适配的传输层、可选计费 | 已实现 | 真实试跑结果待汇总 | [0035](../DECISIONS/0035-haiku-test-planner-agent-sdk.md) |
| agent-05 | — | 按规模选测试模型：非全量真实 LLM 测试用 Sonnet 5.5 effort max；全规模产品流程测试用 Qwen 最新 Flash（`scripts/run_product_full.py`） | 已实现 | 离线测试通过 | [0038](../DECISIONS/0038-test-models-by-scale.md) |

### 接入层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| intake-01 | E3-A | 六维＋独立健康的输入适配、共享消息结构（v2） | 已实现 | | [0034](../DECISIONS/0034-e3a-product-input-contract.md) |
| intake-02 | E3-B | 全国性来源批量获取，统一地理、年份、职业与单位口径 | 计划中 | | |
| intake-03 | E3-C | 真实税与公开说明同源；资料包与确认画像接上运行入口，向界面层提供运行与结果 | 计划中 | | |
| intake-04 | — | LangGraph 外层运行流程：预检查 → 等用户确认 → 幂等登记 → 启动或恢复生活引擎 → 交付结果；SQLite 持久化，接上接入层（E3-A）的预检查、登记与投影和代理层的启动、恢复 | 已实现 | 原型：只接离线 mock 规划器，没有并发锁与托管；说明见 [LangGraph 外层流程](design/langgraph-product-workflow.md)；接线由 `tests/workflows` 覆盖 | |

### 界面层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| ui-01 | E5-A | 离线界面原型（React + TypeScript + Vite，`web/`；读共享结构 v2 的公共样例） | 已实现 | 说明见 [e5-a](design/e5-a-offline-ui.md) | [0036](../DECISIONS/0036-e5a-offline-ui.md) |
| ui-02 | E5-B / E5-C | 接接入层服务与运行器；串行排队、服务端密钥、预算与轻量部署 | 计划中 | | |

### 研究线

P8 / P9 / P10 已实现，门一已过，门二与门三待真实输入；开发期暂停，开发完成后再一次性重跑。见 [README](../README.md) 第 10、11 章。

## 3. 默认入口与配置

| 用途 | 配置 |
|---|---|
| 生活样例（唯一一份，测试、命令行与资料包共用） | `configs/life/synthetic_product.yaml`，评分 `scoring_product.yaml`、评价 `evaluation_product.yaml`、名单 `roster_product.yaml`、城市事实 `city_facts_product.yaml` |
| 非全量真实 LLM 测试（`scripts/run_life_e1_7b.py` 不写 `--config`） | `configs/life/run_product_sonnet.yaml`（Claude Sonnet 5.5，effort max，走订阅） |
| 全规模产品流程测试（所有城市、每城 ≥ 4 个分身、完整年限） | `scripts/run_product_full.py`（Qwen 最新 Flash，`product_planner`；每次运行一本新账本；`--mock` 零费用） |
| 产品样例的 Flash 配置 / Haiku 测试配置（显式 `--config`） | `configs/life/run_product_flash.yaml`（账本 `ledgers/qwen-flash-convenience/`）/ `configs/life/run_product_haiku.yaml` |
| 离线 mock 对照 | `configs/life/run_product_mock.yaml` |
| 接入层产品链 | `scripts/product_e3a.py` 显式编译产品样例 |

模型角色在 `configs/models.yaml`：研究基座 `base_a` / `base_b`，产品 `product_planner`（Flash，全规模测试也用它），测试 `test_planner_sonnet`（Sonnet，非全量测试默认）与 `test_planner`（Haiku）。密钥在 `.env`。

## 4. 文档各管什么

| 文档 | 只放 |
|---|---|
| [README](../README.md) | 愿景、产品流程、方法、研究问题 |
| [CLAUDE.md](../CLAUDE.md) | 编码代理每次要读的不变量、安全约束与工作方式 |
| [PLAN.md](../PLAN.md) | 当前阶段与下一步 |
| 本文件 | 层与命名、轮次与状态、默认入口 |
| [DECISIONS/](../DECISIONS/) | 为什么这样定（只在真正的设计决定时写） |
| [docs/design/](design/README.md) | 公式、代码位置、手算例子、省略了什么 |
| [参数与假设目录](design/parameter-assumption-register.md) | 参数的含义、取值与是否标定 |
| `runs/` | 运行输出；只在本机、不进 git，开发期可以清理、覆盖 |
| `ledgers/` | 付费预算账本（每本一个 `ledger.sqlite`，进 git） |
| [docs/history/](history/) | 旧版文档的完整存档（2026-10-18 重构之前的 CLAUDE.md、PLAN、README、设计索引） |

历史文档里的"PLAN §3.0.x"指存档里的 [旧 PLAN](history/pre-dev-refactor-2026-10-18/PLAN.md)。

## 5. 词汇表

| 词 | 意思 |
|---|---|
| 分身 | 同一个用户放进某座城市的一份模拟 |
| C / E / M | 完成且评分完整 / 生活出局 / 模拟缺失；原计划人数 N = C + E + M |
| L / V / J | 实际几年生活的评价 / 期末参考估值 / 两者合成的分数 |
| mock | 离线、确定性的模拟规划器，不代表模型能力 |
| 滚动型号 | 供应商没有带日期快照的模型名；只在产品与测试角色允许 |
| 合成范围 | 合成城市、合成参数、合成人设，不是现实结论 |
| 不记账（unmetered） | 运行配置写 `unmetered_reason`、不建费用账本，只受全局调用上限约束 |
