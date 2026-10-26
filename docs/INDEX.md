# 项目索引

这里是读者、贡献者和编码代理共用的文档入口。**层与命名、轮次与状态、默认入口都只在这里维护。** 其他文档解释各自的主题，并链接到这里查询进度。

文档导航更新：2026-10-26。各轮实现与核验状态见下方记录。

<a id="reading-routes"></a>
## 按问题阅读

| 我想了解什么 | 从这里开始 |
|---|---|
| 项目为什么做、能体验什么 | [English README](../README.md) · [中文 README](../README.zh-CN.md) |
| 在本机跑一遍、查看输出 | [复现说明](REPRODUCING.md) · [网页启动指南](../web/README.md) |
| 生活如何执行、结果如何评价 | [生活引擎的数学逻辑](METHODS.md#life-kernel) → [详细设计](design/README.md) |
| 城市资料从哪里来、怎样成为可选机会 | [A、B 两种映射](DATA.md#mapping-methods) → [数据来源](DATA.md) → [贡献指南](../CONTRIBUTING.md) |
| 找代码、改功能、运行检查 | [架构说明](ARCHITECTURE.md) → [开发指南](DEVELOPMENT.md) |
| 研究问题、数学标尺和文献依据 | [研究方法](RESEARCH.md) · [参考文献](REFERENCES.md) |
| 已经做到哪里、接下来做什么 | [当前状态](#status) · [版本路线](../PLAN.md) |
| 查旧章节、早期设计与决策 | [历史导航](history/README.md) · [决策记录](../DECISIONS/README.md) |

编码代理先读 [CLAUDE.md](../CLAUDE.md) 的共同规则，再按本表选择本次任务涉及的文档。研究方法、详细设计与历史记录按需阅读，不必为了修改一个模块通读全部历史。

<a id="governance"></a>
## 文档与实现不一致时

项目所有者最新明确决定优先。愿景与当前范围看双语 README，版本方向看 PLAN，模块状态看本索引；具体机制与数值协议查有效的设计和 ADR。代码说明当前实际行为，文档说明目标与约束；两者不一致时先记录差距，再决定改哪边。历史材料只解释当时的背景，已被替代的规则不作为当前要求。

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

<a id="layers"></a>
## 1. 层与命名

旧的 E 编号与 M 编号都退役，统一用"层"。旧编号只在本表与历史文档里出现。

| 层 | 做什么 | 旧 E 编号 | 旧 M 编号 | 代码 | 主要说明 |
|---|---|---|---|---|---|
| **生活引擎** engine | 分身按月过日子：结算、评分、时间精力、职业、储蓄、疾病、跨期评价、城市汇总 | E1-1 – E1-6、E1 补充；E2 已并入 | M2 的生活部分 | `src/econ/life_*`、`src/common/life_*`、`src/ledger/life_*` | [e1-3](design/e1-3-life-model.md)、[e1-4](design/e1-4-time-energy.md)、[e1-5](design/e1-5-career-savings-risk.md)、[e1-6](design/e1-6-evaluation.md)、[生活扩展](design/e1-8-life-extension.md) |
| **代理层** agent | 规划接口、事实记忆、mock 与真实模型、传输层、预算、归档与重放 | E1-7A / E1-7B | M4 | `src/agentlayer/`、`src/orchestration/` | [e1-7](design/e1-7-agent-loop.md) |
| **接入层** intake | 用户输入、职业匹配、标准初态、真实资料、共享消息结构 | E3（含 E3-A） | M1、M1.5 | `src/product/`、`src/common/product_contracts.py`、`src/common/intake_contracts.py`、`contracts/product/` | [共享约定](design/e3-e5-product-contract.md)、[E3-A](design/e3a-product-input.md)、[intake-02 真实资料](design/intake-02-real-data.md) |
| **界面层** ui | 表单、进度、结果展示、网页入口 | E5 | M6 | `web/`（`src/ui/` 未使用） | [E5 网页](design/e5-web-ui.md)、[E5 → E3 交接单](handoffs/e5-bilingual-map-service-requests.md)、[E5-A（存档）](design/e5-a-offline-ui.md) |
| **研究线** research | 大模型偏好理解评估（P8/P9/P10、三个门）、合成世界与人设、信息条件实验 | R、E4 | M3、M5 | `src/eval/`、`src/worlds/` | [研究方法](RESEARCH.md)、[参考文献](REFERENCES.md) |

旧的福利模型核心（`src/econ/core.py`、`kernel.py`、`utility.py` 等，M2）属于研究线的真值标尺；跨国比较是长期方向，版本安排见 [PLAN](../PLAN.md)。

轮次编号：`层-序号`，例如 `engine-05c`、`agent-04`。已经写进 ADR 与 `runs/` 目录名的旧编号不改。

<a id="status"></a>
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
| engine-09 | — | 生活扩展：完整食品方案、个人消费与享受、装备与爱好（同月买同月用、套餐、寿命与替换、参与事实）；物质四项与兴趣、口味的接入；期末参考生活按能力使用期末装备；代理看到新事实、看不到评分 | 已实现 | 独立审核 R1–R9 的收尾修复及离线自测完成，待独立复核（[ADR0041](../DECISIONS/0041-life-extension-food-enjoyment-hobbies.md) 收尾一节）。单次输入上限 Flash 与 Sonnet 128,000、Haiku 72,000；评分 `e1-life-v1`、评价 `e1-life-evaluation-v3`、记忆 `e1-life-memory-v2`、提示模板 `e3a-product-prompt-v2`、共享结构 `e3a-product-schema-v3`（`contracts/product/v3/`）；界面层仍读 v2（网页草稿测试暂时跳过，见 [交接](handoffs/e3-to-e5-life-extension.md)）；只在合成样例上可执行（真实资料缺做饭时间与活动参与时间）；说明见 [生活扩展](design/e1-8-life-extension.md) | [0041](../DECISIONS/0041-life-extension-food-enjoyment-hobbies.md) |

### 代理层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| agent-01 | E1-7A | mock 规划闭环、事实记忆、重放与恢复 | 已实现 | 曾通过审核 | [0030](../DECISIONS/0030-life-e1-7a-agent-planning-loop.md) |
| agent-02 | E1-7B | 真实千问接入、预算账本、服务记录 | 已实现 | 曾通过审核 | [0031](../DECISIONS/0031-life-e1-7b-live-model-pilot.md) |
| agent-03 | — | 产品角色用 Qwen3.8 Flash | 已实现 | 曾通过审核 | [0032](../DECISIONS/0032-product-planner-default-qwen3-8-flash.md) |
| agent-04 | — | 真实 LLM 测试用 Claude Haiku 5.5：Agent SDK 单轮调用、厂商适配的传输层、可选计费 | 已实现 | 真实试跑结果待汇总 | [0035](../DECISIONS/0035-haiku-test-planner-agent-sdk.md) |
| agent-05 | — | 按规模选测试模型：非全量真实 LLM 测试用 Sonnet 5.5 effort max；全规模产品流程测试用 Qwen 最新 Flash（`scripts/run_product_full.py`） | 已实现 | 离线测试通过 | [0038](../DECISIONS/0038-test-models-by-scale.md) |
| agent-06 | — | 每个分身并行地过生活：发送前占调用名额、批次状态只升不降、恢复先核对再并行、Claude 环境锁只罩住启动 | 已实现 | 离线测试通过 | [0039](../DECISIONS/0039-parallel-clones.md) |

### 接入层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| intake-01 | E3-A | 六维＋独立健康的输入适配、共享消息结构（v2） | 已实现 | | [0034](../DECISIONS/0034-e3a-product-input-contract.md) |
| intake-02 | E3-B | 真实资料与机会数据库：六个验证都会区的官方与公开来源批量获取、标准化与溯源，住房 B 映射（ACS 加权条件分布，中位数与标准误成对，官方州别顶码）与 A 覆盖网格，按板块的机会注册表（资料缺口与 E1 缺口分开）、分页查询与菜单（共享、不重抽），LangGraph 资料构建流程（可恢复、按内容身份复用、原始文件指纹约束读取、冻结） | 已实现；收尾修复（独立审核 F1–F6 与两处口径）离线自测通过，待独立复核 | 构建 `562c3233…`：真实记录 144,743、机会 85,316（真实记录本身 66,350、派生 324、映射生成 18,642）、菜单 252；现在能编进 E1 的 0 项（活动的日期与年龄限制 E1 表达不了），只缺资料 21,474 / 只缺 E1 8,621 / 两样都缺 55,149 / 参考事实 72；美国目录的家电 1,634 个（241 个只在加拿大销售的不计）；HUD 被拒、FBI 要密钥、旧金山快餐与咖啡 504；冻结库只在本机，清单与报告在 `data/frozen/intake/us-v1/`；说明见 [intake-02 技术说明](design/intake-02-real-data.md) | [0040](../DECISIONS/0040-intake-02-real-data-database.md) |
| intake-03 | E3-B 收尾 | 六城可执行生活资料包与最小引擎适配：片区与居住条件、按住处片区的地区条件、通勤关系表与每月执行记录、官方 2024 年税（摘句核对）、设备故障与预先声明的替换、年度菜单与续租；都会区安全背景（FBI 2024、纽约州 DCJS）与休闲共同背景 1；独立审核 F1–F6 的修复（用车费不含油费、构建身份核对实际内容、参考住房按参考岗位绑定、mock 用车、可见事实不写内部锚点、E5 逐城治安说明）；六城组装、冻结与产品链（mock、LangGraph、恢复、零调用重放） | 已实现；修复及离线自测完成，待独立复核 | 资料包 `data/frozen/life/us-v1/pack/`（构建 `7cc38665…`），产品配置 `configs/product/e3b_us_v1_pack.yaml`（`real_data_frozen`）；提示模板 `e3b-product-prompt-v3`（E3B 收尾）；缺口：旧金山公交、芝加哥公园真实活动、四城培训、博物馆价格、纽约都会区新泽西与宾州部分的犯罪资料；说明见 [E3B 资料包](design/e3b-life-pack.md)、[通勤资料](design/e3b-commute-data.md)、[生活机会与职业](design/e3b-items-data.md) | [0042](../DECISIONS/0042-e3b-real-life-pack.md) |
| intake-03b | E3-B 映射修正 | 住房完整加权经验支持表（同记录毛租金或标明的插补、删失与厨卫照实报）与岗位（OEWS 20 个年薪支持点 × 同记录的工时 × 就业片区联合分布）的完整候选池，每份年度菜单从完整池抽 60 项与 30 / 60 / 120 诊断；A 板块共同结构与对数正态零售修正（现在没有同规格报价）；完整 21 餐食品网格与外食成本代理；日常支持（通信、居家联网、家用日耗品；所有者授权的评分调整）与抽象个人消费篮子（取代按摩与居家舒适）；公共结构 v3.3（旧确认要重新审阅）。**E3B 收尾（2026-10-24）**：普通住房代理 P10、参考住房 v3（就业覆盖 3/5 + 估值期初实际通勤检查的内部选择器）、本地报价接线与宽带回退、intake 不再生成旧三档岗位、按板块的可逆请求压缩（提示模板 v3）与正式预检查、菜单诊断 | 最后收尾（报价按逐规格核实的基础价口径、真实日历；中文整句不拆词；产品 Flash 输入护栏 192,000；正式服务停机预算 20 元）：离线自测通过，待独立复核 | 资料包 `data/frozen/life/us-v1/pack/`（构建 `4a1e7379…`，intake `60cf49b4…`）；芝加哥两个 M 是所有者接受的 V1 通勤资料缺失限制；E3C 接收 `1b3201c` 的检查与 20 元预算配置（[交接](handoffs/e3b-closeout-to-e3c.md)）；E5 已按 v3.3 重新生成 4 份草稿夹具（`85f7c90`），全量离线测试不再有跳过；说明见 [E3B 映射修正](design/e3b-mapping-and-daily-support.md)，交接见 [E3B 映射修正 → E5](handoffs/e3b-mapping-to-e5.md) | [0044](../DECISIONS/0044-e3b-mapping-daily-support.md) |
| intake-05 | E3-C | 托管服务：一人一条专属链接与 7 天会话（Cookie + CSRF、限流）、3 次启动机会（服务端事件记账，预留 → 已使用 / 退回）、服务端确认画像、运行意图与正式运行设置（`product_planner`、每次运行一本新账本）、单个 worker 驱动现有 LangGraph 与 E1（进程锁、重启恢复、发送许可）、删除与 30 天过期；公共结构 v3.2 与 OpenAPI。**E3C 收尾（2026-10-24）**：运行选项分默认值 / 允许范围 / 本次有效设置（默认六城 × 每城 4 个分身 × 3 年、20 元；允许每城 1–4 个分身、只开放 3 年、0.01–20 元；创建运行意图时保存，之后不变）；独立审核 R1–R6 的修复（删除全部挡住并发写入、审阅随修订作废与确认画像 30 天、每个动作先判到期、恢复按 E1 记录与预算判断、OpenAPI 与实际应用一致、停下的说明分开始前后）；接收 `1b3201c` 的请求大小检查与 20 元预算配置；公共结构仍是 v3.3，样例 60–104。**E3 → E5 接口补齐（2026-10-24）**：动作许可与付费开关一致、恢复排队不能取消（`cancel_not_allowed`）、意图的实际期限与依据（`expiry_basis`）、可重读的画像日期（`/api/v1/profiles`）、OpenAPI 实际成功码；双语显示附层 `e3-display-v1`（`GET /api/v1/display`，即时回复带中英文字，私有绑定随所属对象，资源按资料包与版本冻结）、地理清单（六城都会区代表点，EPSG:4326）、结果色阶 `MetricDisplaySpec`；服务 API `e3c-service-api-v1.1`、服务库 v3（从 v2 就地升级）、样例 105–125 | 已实现；收尾 R3（过期排队任务）、R4（无发送历史的恢复估计）在 `8ccec1c` 已独立复核通过（全量离线 1152 条通过；本机证据 `runs/e3c-r3r4-review-20261024/`）；**E3 → E5 接口补齐**（本机证据 `runs/e3-e5-display-20261024/`）：独立复核（`runs/e3c-display-review-20261025/`）动作、期限与地理清单通过，显示附层有 F1–F3 三项阻塞；**收尾修复（2026-10-25）：修复及离线自测完成，待独立复核**——显示故障不破坏业务回复、按位置与实体身份绑定、历史正文用自己的绑定版本、知悉项说明与带键的作答状态、可读的审阅说明、启动时检查数据目录路径长度、色阶按 ADR 0045 统一；显示附层 `e3-display-v2`、服务 API `e3c-service-api-v1.2`、样例 126–128（本机证据 `runs/e3-e5-review-fixes-20261025/`）；**输入体验收敛（2026-10-25）：实现及离线自测完成，待独立复核**——输入目录（职业 9 组与搜索词、最高学历 / 证书 / 相关经验题、兴趣与菜系面板）、按目录直接选职业、资格答案按 E3B 已有规则换算、面板多选、新问卷不再收集硬要求与属性偏好（旧草稿显式迁移）、F3 遗留（一次运行一版解释）；共享结构 v3.4、问卷 v0.4、词表 v5、转换 v3、服务 API `e3c-service-api-v1.3`、样例 129–154（本机证据 `runs/e3-input-closeout-20261025/`，[ADR0046](../DECISIONS/0046-e3-input-catalog-and-structured-answers.md)）；独立复核（`runs/e3-input-independent-20261025/`）其余通过、R1 旧画像升级兼容阻塞；**兼容修复（2026-10-25）：修复及离线自测完成，待独立复核**——v3.3 对象按原版本序列化，旧身份、旧绑定与已开始运行的恢复都照原样（本机证据 `runs/e3-compat-fix-20261025/`）；**独立评分（2026-10-25）：实现及离线自测完成，待独立复核**——`ratings_normalized_v1`：每项 0–100 独立评分，精确归一成权重（100 × 评分 ÷ 合计），全零拒绝、“同等重视”是用户主动设 50、物质父项为 0 时四项保留；旧百分比分配迁移时由用户选择重新评分或同等重视；共享结构 v3.5、问卷 v0.5、转换 v4、服务 API `e3c-service-api-v1.4`、样例 142–161（[ADR0047](../DECISIONS/0047-e3-independent-ratings.md)）；**一次开始（2026-10-25）：实现及离线自测完成，待独立复核**——设置预览（只读：有效设置、范围说明、必要知悉项与同意身份）、一次“确认并开始”（`start_when_ready`：授权保存在意图上，检查通过后 worker 按授权在同一个提交事务里开始，LangGraph 确认记录写明来源；同一个键的重试找回同一个意图）、条件变了或停下时授权作废并说明、服务库 v4（就地升级）、样例 162–176（[ADR0048](../DECISIONS/0048-e3c-one-start.md)）；**进度摘要（2026-10-25）：实现及离线自测完成，待独立复核**——运行意图的 `progress_summary`（阶段、一句当前状态、更新时间；检查、排队、整理为不定长，模拟中按 E1 归档里已结算的分身年度数，完成只在结果可以读时）、进度快照记下已结算 / 计划的分身年度数、样例 177–183（[ADR0049](../DECISIONS/0049-e3c-progress-summary.md)；本机证据 `runs/e3-v35-experience-20261025/`）；**去掉旧草稿迁移（2026-10-26）：实现及离线自测完成，待独立复核**——旧版本的草稿照原样读回、审阅挡住确认，用户新开一份；迁移接口与它的类型、错误码删掉（[ADR0050](../DECISIONS/0050-e3-no-old-draft-migration.md)）；模型输入 / 输出语言与 E1 事件链英文没有做，见 [交接单](handoffs/e5-bilingual-map-service-requests.md) 第 15 节 | 离线联调用 E3B 资料包 `48eda959…`（构建 `4a1e7379…`）；没有部署（主机、域名、TLS 都没有），付费调用开关关闭（`live_calls_enabled: false`），真实模型没有在服务链上试跑，网页还没接线；说明见 [E3C 托管服务](design/e3c-service.md)，交接见 [E3C → E5](handoffs/e3c-to-e5-service.md) | [0043](../DECISIONS/0043-e3c-hosted-service.md)、[0046](../DECISIONS/0046-e3-input-catalog-and-structured-answers.md)、[0047](../DECISIONS/0047-e3-independent-ratings.md)、[0048](../DECISIONS/0048-e3c-one-start.md)、[0049](../DECISIONS/0049-e3c-progress-summary.md)、[0050](../DECISIONS/0050-e3-no-old-draft-migration.md) |
| intake-06 | — | 资料缺口补齐与 E1 扩展（活动日历、整期报名、年龄资格；在职积累经验） | 计划中 | 原先排在 E3-C 名下，E3C 改做托管服务后另排 | |
| intake-04 | — | LangGraph 外层运行流程：预检查 → 等用户确认 → 幂等登记 → 启动或恢复生活引擎 → 交付结果；SQLite 持久化，接上接入层（E3-A）的预检查、登记与投影和代理层的启动、恢复 | 已实现 | 原型本身只接离线 mock；托管服务（intake-05）的 worker 驱动同一套流程，注入真实规划器、停止控制、启动核对与服务端登记表；说明见 [LangGraph 外层流程](design/langgraph-product-workflow.md)；接线由 `tests/workflows` 与 `tests/product_service` 覆盖 | |

### 界面层

| 编号 | 旧编号 | 内容 | 状态 | 备注 | ADR |
|---|---|---|---|---|---|
| ui-01 | E5-A | 离线界面原型（React + TypeScript + Vite，`web/`；读共享结构 v2 的公共样例） | 已实现（已被 ui-02 取代，存档） | 说明见 [e5-a](design/e5-a-offline-ui.md)（存档） | [0036](../DECISIONS/0036-e5a-offline-ui.md) |
| ui-02 | E5-B / E5-C | 网页接 E3C 托管服务（共享结构 v3.5、服务 API v1.4、显示附层 v2）：专属链接换会话、服务器草稿（自动保存、冲突由用户选）、输入页按 E3 的目录与结构（职业目录分组 + 搜索 + 直接选、学历单选、证书多选、按需的经验题、六维与物质四项的独立评分（0–100 滑块 + 数字精调、临时权重预览、同等重视）、兴趣与口味多选面板；旧版本问卷的草稿只提示新开一份（不迁移）；没有硬要求、属性偏好与文字解释）、审阅与确认（原始评分与精确权重分开、知悉项说明按 ack_id）、本次设置预览与一次“确认并开始”（发送前存找回记录、同键找回、作废后 start_again）、同一个等待页（progress_summary 的进度条与一句话、授权状态）、只读轮询、恢复 / 取消 / 删除、我的运行；结果三层（城市比较 → 单城详情 → 分身年度记录）；双语：界面字典 + E3 业务文字按显示信封的字段位置取英文（缺译显示原文并标“未翻译”，原生控件也取英文）；历史结果的地图只用结果绑定的显示资源版本（为空就不请求）；默认稿纸 + 平面地图，可切换的写实地球（react-globe.gl，可拖动）；显示语言与模型语言是开局选项。次数、排队、预算、后台调度与删除过期由 E3C 负责，界面不另做 | 进行中 | 本地 mock 主闭环（浏览器 → E3C → worker → LangGraph → E1）、输入页、历史地图、一次开始的找回由 Playwright 自动验收（桌面 + 手机）；2026-10-26 修了独立核查的 E5-R1–R7（R6 以独立评分替换解决）并接上 v3.5 / API v1.4：独立评分、设置预览与一次开始、进度摘要；同日按所有者决定删除草稿迁移与旧百分比分配控件，页面只适配当前问卷。修复及离线自测完成，待独立复核；没有部署、没有真实模型。还开着：模型语言字段、事件链里 E1 中文句子的结构化绑定、真正旧版运行的浏览器联验，见 [交接单](handoffs/e5-bilingual-map-service-requests.md) 第 17–19 节；说明见 [e5-web-ui](design/e5-web-ui.md) | [0045](../DECISIONS/0045-e5-web-dual-mode-bilingual.md) |

### 研究线

P8 / P9 / P10 已实现，门一已过，门二与门三待真实输入；开发期暂停，开发完成后再一次性重跑。定义与判据见[研究方法](RESEARCH.md#research-gates)。

<a id="entry-points"></a>
## 3. 默认入口与配置

| 用途 | 配置 |
|---|---|
| 生活样例（唯一一份，测试、命令行与资料包共用） | `configs/life/synthetic_product.yaml`，评分 `scoring_product.yaml`、评价 `evaluation_product.yaml`、名单 `roster_product.yaml`、城市事实 `city_facts_product.yaml` |
| 非全量真实 LLM 测试（`scripts/run_life_e1_7b.py` 不写 `--config`） | `configs/life/run_product_sonnet.yaml`（Claude Sonnet 5.5，effort max，走订阅） |
| 全规模产品流程测试（所有城市、每城 ≥ 4 个分身、完整年限） | `scripts/run_product_full.py`（Qwen 最新 Flash，`product_planner`；每次运行一本新账本；`--mock` 零费用） |
| 产品样例的 Flash 配置 / Haiku 测试配置（显式 `--config`） | `configs/life/run_product_flash.yaml`（账本 `ledgers/qwen-flash-convenience/`）/ `configs/life/run_product_haiku.yaml` |
| 离线 mock 对照 | `configs/life/run_product_mock.yaml` |
| 托管服务（E3C） | `scripts/product_service.py`（api / worker / invite / jobs / quota-adjust / openapi / check-openapi / e2e 离线联调）；生产 `configs/product/service.yaml`（真实规划器、付费开关关闭），本地联调 `configs/product/service_local_mock.yaml`（离线 mock，只绑定 127.0.0.1） |
| 接入层产品链 | `scripts/product_e3a.py` 显式编译产品样例 |
| 六城真实生活资料包（E3B） | `scripts/life_pack_us.py`：`fetch-tax`、`fetch-safety`（联网）→ `check-tax`、`check-safety` → `build`（离线，读 intake 冻结库与两份冻结资料，写 `data/frozen/life/us-v1/pack/` 与产品配置）；产品链 `scripts/run_product_full.py --mock --pack configs/product/e3b_us_v1_pack.yaml --abilities edu_at_least_hs`；敏感性 `scripts/life_pack_sensitivity.py` |
| 真实资料构建（intake-02） | 配置 `configs/intake/us_v1.yaml`；`scripts/intake_data.py`：`fetch`（唯一联网的命令）→ `build`（LangGraph，离线，可恢复）→ `export`；`query` / `menu` / `lineage` 查冻结库；测试样本由 `scripts/intake_fixtures.py` 生成 |

模型角色在 `configs/models.yaml`：研究基座 `base_a` / `base_b`，产品 `product_planner`（Flash，全规模测试也用它），测试 `test_planner_sonnet`（Sonnet，非全量测试默认）与 `test_planner`（Haiku）。密钥在 `.env`。

<a id="document-roles"></a>
## 4. 文档各管什么

| 文档 | 只放 |
|---|---|
| [README](../README.md) / [中文 README](../README.zh-CN.md) | 项目动机、体验入口、当前范围与路线摘要；两种语言保持相同事实 |
| [METHODS](METHODS.md) | 生活模拟的方法概览、评价方式与验证边界 |
| [REPRODUCING](REPRODUCING.md) / [网页指南](../web/README.md) | 安装、试跑、输出与检查步骤 |
| [ARCHITECTURE](ARCHITECTURE.md) / [DEVELOPMENT](DEVELOPMENT.md) | 模块职责、代码入口与开发方式 |
| [DATA](DATA.md) / [CONTRIBUTING](../CONTRIBUTING.md) | 资料来源与映射、贡献入口与城市接入方向 |
| [RESEARCH](RESEARCH.md) / [REFERENCES](REFERENCES.md) | 研究问题、静态数学标尺、实验方法与文献依据 |
| [CLAUDE.md](../CLAUDE.md) | 编码代理共用的不变量、安全约束与工作方式；[AGENTS.md](../AGENTS.md) 只负责引路 |
| [PLAN.md](../PLAN.md) | 版本方向与未来工作 |
| 本文件 | 阅读路线、文档分工、层与命名、轮次与状态、默认入口 |
| [DECISIONS/](../DECISIONS/) | 为什么这样定（只在真正的设计决定时写） |
| [docs/design/](design/README.md) | 公式、代码位置、手算例子、省略了什么 |
| [参数与假设目录](design/parameter-assumption-register.md) | 参数的含义、取值与是否标定 |
| `runs/` | 运行输出；只在本机、不进 git，开发期可以清理、覆盖 |
| `ledgers/` | 付费预算账本（每本一个 `ledger.sqlite`，进 git） |
| [历史导航](history/README.md) | 旧版文档的完整存档与旧章节对应；保留 2026-10-18、2026-10-26 两次拆分前的记录 |

历史文档里的“PLAN §3.0.x”指 [2026-10-18 之前的 PLAN](history/pre-dev-refactor-2026-10-18/PLAN.md)；旧“README §…”可以从[历史导航](history/README.md)定位。新文档直接链接主题页与稳定锚点。

<a id="glossary"></a>
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
