# CLAUDE.md

本文件是 Claude Code 每次会话自动加载的项目说明。设计文档在 `README.md`，文献在 `RELATED_WORK.md`，逐条决策在 `DECISIONS/`，一次性迁移动作在 `MIGRATION_v0.5_to_v0.9.md`。本文件只讲工程约定与硬规则。

> **本文件保持无标记（不用删除线与加粗表示差异），因为它是被机器每次读取的配置文件。版本差异见文末变更记录。**
>
> **划分原则：本文件装不变量（永远为真的约束），迁移文档装一次性动作（做完就作废的改动）。**

---

## 项目一句话

一个可计算的空间福利模型（产品模式：算出一个人在哪座城市过得最好），并用它作为真值标尺，把大语言模型的偏好理解误差换算成美元，回答两个问题：它伤害了谁，以及它是否已经够好（研究模式）。

**开工前必读 `README.md` 的第 3、7、11 章，以及 §1.2**。第 3 章是信息边界，第 7 章是福利模型的全部数学，第 11 章是研究模式，§1.2 是三个研究问题、问题一的三点标尺、以及 `proj_i` 只能部分识别这一结论（v0.9 重写，与旧版本冲突）。

---

## 当前进度

2026-10-17 当前入口：E1 实施包在声明的合成范围内结束，E1-7B 收尾和 Flash 切换已通过 [独立核查](runs/2026-10-14-e1-7b-flash-review/audit.json)。下一步 E3/E5 并行，E4/R 研究可后置。先读 [共享约定](docs/design/e3-e5-product-contract.md)，再读 [E3 交接](docs/handoffs/e3-data-input-handoff.md)或 [E5 交接](docs/handoffs/e5-product-handoff.md)。本文件后面的阶段性“待复核”等文字保留历史，不覆盖这一状态。

职责：E3 处理用户输入、职业映射、标准初态、美国资料与业务服务；E5 处理界面和前端，不复制业务算法。新产品六维定和＋独立健康需显式适配现有七维 E1 契约（2026-10-18 E3-A 已实现、离线自测通过；同日独立审核发现 F1–F5，收尾修复已实现、离线自测通过、待独立复核，见 DECISIONS/0034 第九节）；不填医疗占位分、不自动按文本调权重、不要求用户职业代码。真实税与运行状态也需显式适配。共享定义由 E3 主责，双方用同一份样例联调；当前研究与历史协议保持兼容。本文档更新不授权修改代码或付费运行。2026-10-18：E1 最小补充“日常生活便利”（DECISIONS/0033）已实现、自测通过、待独立复核，给 E3 的交接见 docs/handoffs/e1-daily-convenience-to-e3.md。同日补记：2026-10-18，在声明的 V1 合成范围内通过独立审核，可以交接 E3；真实资料接入和参数标定尚未完成（runs/2026-10-18-e1-daily-convenience-review/audit.json，DECISIONS/0033 第八节）。CLI 默认运行（scripts/run_life_e1_7b.py 不写 --config）仍是 configs/life/run_e1_7b_flash.yaml 的原生活配置，模型仍是 Flash；E3 新产品链须显式采用便利版（configs/life/run_e1_convenience_flash.yaml 对应的协议 life-v1-e1-convenience、评分 e1-convenience-v1、目录、评价与提示模板 e1-convenience-prompt-v1），对照表见交接第 0 节。

真实 LLM 测试（2026-10-18，DECISIONS/0035，所有在本项目工作的编码代理都适用）：要真实调用大语言模型的测试与试跑一律用 Claude Haiku 5.5（configs/models.yaml 的 test_planner 角色，经 Claude Agent SDK 单轮调用）；scripts/run_life_e1_7b.py 不写 --config 时就是 configs/life/run_e1_7b_haiku.yaml，便利版用 configs/life/run_e1_convenience_haiku.yaml；Flash（product_planner）与 Max（base_b）只在所有者明确要求时用 --config 显式选。认证走本机已登录的 Claude 订阅（配置写 auth: claude_subscription，先不计费、写 unmetered_reason），不需要 ANTHROPIC_API_KEY；本机先用 claude auth login 登录。

| 模块 | 状态 |
|---|---|
| M0 工程骨架 | ✅ |
| M1 数据抓取 | ⬜ **未实现**（`scripts/fetch_data.py` 直接 exit 1）。合成世界自带城市属性，M2–M5 不等它 |
| M1.5 数据转化与溯源 | 🟡 部分：RPP 归一化与若干溯源契约已落地；`scripts/freeze_reference.py` 仍是占位 |
| M2 福利模型 | ✅ |
| M3 世界与人设生成 | ✅ |
| M4 代理层 | 🟡 E1-7A（2026-10-12）：规划接口、安全请求、mock 规划器与多分身运行器（src/orchestration/）已实现，收尾已通过独立复核（runs/2026-10-12-e1-7a-closeout-review/audit.json）；E1-7B（2026-10-13）：真实千问模型规划器、付费预算账本与 8 个分身 × 3 年的合成小规模试跑已实现；独立审核发现 F1–F3，收尾（执行修订 e1-7b-r2）已实现、离线验收通过、待独立复核；2026-10-13 起产品运行默认模型是 Qwen3.8 Flash（产品角色 product_planner，滚动型号，DECISIONS/0032），最小真实试跑（1 次请求）已完成、待独立复核；2026-10-18 起真实 LLM 测试用 Claude Haiku 5.5（test_planner，Claude Agent SDK 单轮调用，DECISIONS/0035）。散文生成客户端**不等于**被评估的代理层 |
| M5 评估层 | 🟡 **进行中，是目前最完整的一块**：P8/P9/P10 已实现，门一已过；门二/门三待真实输入 |
| M6 用户前端 | ⬜ 待做 |
| M7 跨国扩展 | V2 |

> 这张表在 2026-09-28 的代码审核里被查出与实际不符（M1 标 ✅ 而脚本 exit 1，
> M5 标"待做"而它是实现最多的一块）。**它每次会话都被机器读取**，所以标错的
> 代价不是看着别扭，是让下一轮按错的前提开工。

**在开始 M4 之前，先打完 `PLAN.md` 的 v0.9 补丁清单，并通过三个门**（见下）。阻塞项是 P1、P2、P8、P9、P10。

---

## 三个门（M4 之前必须判定）

三者都零大模型调用（门二、门三消费重复子集的产出），都在花主实验预算之前跑。**门一或门二不过，停下来报告，不要先开跑再说。**

| 门 | 判据 | 不过怎么办 | 出处 |
|---|---|---|---|
| **一：产品前提** | 在福利离散度高于 τ 的选择集上，`L_rep` 的中位数是否超过 §9.3 的噪声地板（每维 ±5 分扰动诱导的 EV 变动中位数）。若在任何现实可构造的选择集上都不超过，判定个人化在本装置上不可测量 | 停，产品前提可能不成立 | §9.1.1.1 |
| **二：测量窗口** | 三条同时成立（全部预注册）：**2a** 去衰减后 `λ̂/ŝ_shuffled ≤ 0.8`；**2b** 窗口 `(ŝ_shuffled − λ̂)` 与其 bootstrap 标准误之比不低于阈值；**2c** `ŝ_shuffled ≥ 下限`（建议 0.5）| 停，需增加每人选择次数或降噪 | §10.3.1、§10.3.3、`DECISIONS/0002` |
| **三：公式前提** | 人内不一致度对 `d_i` 的回归是否显著。显著则 λ 是 `d_i` 的函数 | 不停，但 M5 必须实现 `r(d)` 曲线版本而非单一 `r` | §10.3.4 |

**门二与门三共用同一批重复子集数据，因此重复子集必须先于主实验跑**，它是门的输入，不是主实验的副产品。

阈值与判据结果写进 `runs/<run_id>/`，不要只打印在终端。`make gates` 一条命令跑完三个门。

---

## 治理：README 为准，推翻必须留指针

**README 是唯一的权威。** 当 `DECISIONS/` 里的记录推翻 README 的某一处时，
**同一个 commit 必须在 README 的原处留一行指针**：

```
> ⚠ 本段已被 DECISIONS/00NN 推翻，见该记录。
```

**没有指针的 `DECISIONS/` 记录不生效。** 这条防的是一个具体的失败模式：
决策记录写得再清楚，读 README 的人不会知道它存在，于是下一次"按文档实现"
就把已经推翻的做法重新写回去，而重新写回去的代码跑得完全正常。

指针在下一次版本号跳变时被正式重写吸收，届时删掉指针、直接改正文。

---

## 概念裁决（已收敛，不要再问）

| 问题 | 裁决位置 |
|---|---|
| 效用函数形式与间接效用 | README §7.1、§7.1.1 |
| 两通道权重映射 | §7.2 |
| 住房质量（方案 B） | §7.1.2 |
| 舒适度基数化与参照总体 | §5.3、§7.3 |
| 单次计入 | §7.4 |
| 四条断言 | §7.4.1 |
| 享乐适应（V1 不实现） | §7.5.1 |
| 跨年计价（基年不变价） | §5.8 |
| 离散度比的参考熵与 σ | §11.6 |
| **问题一的结论量（三点标尺与 `r(t)` 区间）** | **§1.2.1、§1.2.2** |
| **指标分工：协方差比主证据，`proj` 方向证据** | **§1.2.3、§11.7.3** |
| **两个口径：坍缩比例扣地板，美元损失不扣** | **§1.2.4** |
| **地板臂 σ 由重复子集标定** | **§10.3.1、§11.8** |
| **不确定性传播用 block bootstrap** | **§11.7.4** |
| **人设抽样分层与加权还原** | **§11.9.1、§11.9.2、`DECISIONS/0014`** |
| **估计对象 P/Q/S 与协方差分母取 S** | **`DECISIONS/0015` 裁决三** |
| **`Σ_w(t)` 用方案 A（共享权重中心）** | **`DECISIONS/0015` 裁决四** |
| **commute：采用长通勤设计，保留七维并披露弱回收** | **`DECISIONS/0015` 裁决五** |
| **混合 logit 的估计设定（差分法、γ 空间对角）** | **§11.7.1.1、§11.7.1.2** |
| **福利损失的基准线与分解（`L_rep`/`L_s`/`L_amen`）** | **§9.1.1** |
| **人设权重的抽样分布：物质维 α₁=1.5，六个舒适度维 α=0.5** | **§11.9、§6.3.1、`DECISIONS/0006`** |
| 研究模式跑在哪套城市上 | §11.3（合成城市；真实都会区只服务产品模式） |
| 三个研究问题 | §1.2 |

**不再有"尚未裁决"项。** v2 曾把权重抽样分布列为未决、并写"若 OECD BLI 可获取则主实验改用该分布"，**该条已被撤销**：Dirichlet(α=0.5) 是主臂，真实分布只作对照臂（理由见 §5.1 的三条限定：可得性未证实、0–10 评分口径与 §6.3 矛盾、默认值 5 造成均匀向量堆积）。

---

## 生活模型（E1）导航与冲突说明

生活模型契约见 DECISIONS/0016（E1-1），月度结算见 DECISIONS/0017（E1-2），探索月度评分与 E1-2 收尾见 DECISIONS/0018（E1-3），E1-3 增补（服务数量、功能叶子、来源四类、比较组与覆盖报告、自然景观、方案级通勤）见 DECISIONS/0019，月度时间与精力约束（E1-4A）见 DECISIONS/0020，上一年满足感 → 下一年月度精力额度的有界反馈（E1-4B）见 DECISIONS/0021，相关培训与技能 → 岗位选择 → 实际收入的最小闭环（E1-5A）见 DECISIONS/0023，E1-5A 补充（发展维成长满足感、广义交通评分、学习边界订正）见 DECISIONS/0024，评分内容身份与跨年精力额度的绑定（E1-5A 收尾）见 DECISIONS/0025，目标储蓄（E1-5B）见 DECISIONS/0026，疾病、应急与恢复（E1-5C）见 DECISIONS/0027，单分身跨期评价（E1-6A：实际 N 年逐月汇总、参考方案期末估值与合成分，以及所有者确认的城市层面口径）见 DECISIONS/0028，E1-6A 收尾（结果身份、已有物品、运行入口的配置检查、基础生活模板与治疗分支）与 E1-6B 城市汇总（冻结名单、C / E / M 分类、完整者均值、模拟缺失范围、事件链、城市比较与评价对照）见 DECISIONS/0029，E1-6B 的最小收尾（审核 R1–R3：标准开局 v1、冻结前每个运行臂的跨城配置检查、敏感性证据配对、硬要求核验人数）见 DECISIONS/0029 第八节，E1-7A 代理规划、事实记忆与合成城市闭环（mock 规划器、城市背景事实与评分参数分开、执行前检查与有限修订、多分身调度与失败范围、重放与年度边界恢复）见 DECISIONS/0030，其最小收尾（审核 F1–F3：恢复一致性、规划器设定、领薪月份）见 DECISIONS/0030 第六节，E1-7B 真实千问模型接入（公开合成税说明与行动说明书、三类失败与停下、付费预算账本、付费前冻结输入、小规模合成试跑）见 DECISIONS/0031，其收尾（审核 F1–F3：服务记录一致性、预算一致性、税说明）见 DECISIONS/0031 第八节，产品生活规划器默认模型切换为 Qwen3.8 Flash（产品角色、滚动型号、返回型号规则、入口与最小真实试跑）见 DECISIONS/0032，物质维的洗衣推广为日常生活便利（便利分只读资源账里实际减少的家务小时、新旧隔离、说明书与 mock、期末估值模板、限额试跑）见 DECISIONS/0033，真实 LLM 测试改用 Claude Haiku 5.5（Claude Agent SDK 单轮调用、厂商适配的传输层、可选计费与美元账本）见 DECISIONS/0035。长期技术说明见 docs/design/e1-3-life-model.md、docs/design/e1-4-time-energy.md 与 docs/design/e1-5-career-savings-risk.md（A 部分；B 目标储蓄；C 疾病、应急与恢复）以及 docs/design/e1-6-evaluation.md（E1-6A 跨期评价；第 9 节 E1-6B 城市汇总）与 docs/design/e1-7-agent-loop.md（E1-7A 代理规划闭环；第 15 节 E1-7B；第 16 节产品默认模型；第 17 节日常生活便利的说明书、mock 与入口；第 18 节真实 LLM 测试用 Haiku 5.5），日常生活便利的评分在 docs/design/e1-3-life-model.md 第 21 节。落点：src/common/life_contracts.py、src/common/life_billing.py、src/common/life_scoring_contracts.py、src/common/life_feedback_contracts.py、src/common/life_units.py、src/ledger/life_catalog.py、src/ledger/life_scoring_config.py、src/econ/life_settlement.py、src/econ/life_resources.py、src/econ/life_scoring.py、src/econ/life_coverage.py、src/econ/life_feedback.py、src/econ/life_year.py、src/econ/life_career.py、src/econ/life_savings.py、src/econ/life_health.py、src/common/life_evaluation_contracts.py、src/ledger/life_evaluation_config.py、src/econ/life_evaluation.py、src/econ/life_terminal_value.py、src/common/life_aggregation_contracts.py、src/ledger/life_roster.py、src/econ/life_aggregation.py、src/agentlayer/life_views.py、src/common/life_city_facts.py、src/common/life_planning_contracts.py、src/ledger/life_city_facts.py、src/ledger/life_run_config.py、src/agentlayer/life_planning.py、src/agentlayer/life_mock.py、src/orchestration/life_runner.py、src/orchestration/life_archive.py、src/agentlayer/life_llm.py、src/orchestration/life_budget.py、configs/life/、scripts/check_life_contract.py、scripts/demo_life_scoring.py、scripts/demo_life_e1_3s.py、scripts/demo_life_e1_4a.py、scripts/demo_life_e1_4b.py、scripts/demo_life_e1_5a.py、scripts/demo_life_e1_5a_growth_transport.py、scripts/demo_life_e1_5b.py、scripts/demo_life_e1_5c.py、scripts/demo_life_e1_5c_closeout.py、scripts/demo_life_e1_6a.py、scripts/demo_life_e1_6b.py、scripts/demo_life_e1_7a.py、scripts/run_life_e1_7b.py、scripts/demo_life_e1_convenience.py、tests/life/。实施轮次与状态见 PLAN §3.0、§3.0.2、§3.0.3、§3.0.4（E1-4B r2 于 2026-10-08 在合成范围内通过复核，见 runs/2026-10-08-e1-4b-r2-review/audit.json）、§3.0.6（E1-5A 于 2026-10-09 在合成范围内通过独立审核，见 runs/2026-10-09-e1-5a-review/audit.json）与 §3.0.7（E1-5A 补充已实现；独立复核发现的评分内容绑定缺陷由 §3.0.8 修复，已实现、自测通过，待独立复核；2026-10-09 在合成范围内通过独立复核，见 runs/2026-10-09-e1-5ab-review/audit.json）与 §3.0.9（E1-5B 目标储蓄已实现、自测通过，待独立复核；2026-10-09 在合成范围内通过独立复核，见同一份 audit.json）与 §3.0.10（E1-5C 疾病、应急与恢复已实现、自测通过，待独立复核；证据 runs/2026-10-11-e1-5c-illness-recovery/；同日主体通过独立核查，发现三个收尾项）与 §3.0.11（E1-5C 收尾修订已实现、自测通过，待独立复核；证据 runs/2026-10-11-e1-5c-closeout-fixes/；2026-10-11 在合成范围内通过独立复核，见 runs/2026-10-11-e1-5c-closeout-review/audit.json）与 §3.0.12（E1-6A 单分身跨期评价已实现、自测通过；证据 runs/2026-10-11-e1-6a-evaluation/；同日独立审核发现 F1–F4，见 runs/2026-10-11-e1-6a-review/）与 §3.0.13（E1-6A 收尾与 E1-6B 城市汇总 2026-10-12 已实现、本轮自测通过；证据 runs/2026-10-12-e1-6b-aggregation/；同日独立审核发现 R1–R3，见 runs/2026-10-12-e1-6b-review/）与 §3.0.14（E1-6B 最小收尾已实现、本轮自测通过，待独立复核；证据 runs/2026-10-12-e1-6b-closeout/；E1-6 只分 A、B 两轮；2026-10-12 在合成范围内通过独立复核，见 runs/2026-10-12-e1-6b-closeout-review/audit.json，E1-6 的 V1 工程范围结束）与 §3.0.15（E1-7A 代理规划闭环已实现、自测通过，待独立复核；证据 runs/2026-10-12-e1-7a-agent-loop/；E1-7 分 A、B 两轮，B 未开始；同日独立审核发现 F1–F3，见 runs/2026-10-12-e1-7a-review/）与 §3.0.16（E1-7A 三项收尾已实现、自测通过，待独立复核；证据 runs/2026-10-12-e1-7a-closeout-fixes/；E1-7B 未开始；2026-10-12 在合成离线范围内通过独立复核，见 runs/2026-10-12-e1-7a-closeout-review/audit.json）与 §3.0.17（E1-7B 真实模型接入与小规模试跑已实现、离线验收通过、试跑已完成，待独立复核；证据 runs/2026-10-13-e1-7b-live-pilot/；同日独立审核发现 F1–F3，见 runs/2026-10-13-e1-7b-review/）与 §3.0.18（E1-7B 收尾已实现、离线验收通过，待独立复核；证据 runs/2026-10-13-e1-7b-closeout/）与 §3.0.19（产品默认模型切换为 Qwen3.8 Flash，已实现、离线验收通过、最小真实试跑已完成，待独立复核；证据 runs/2026-10-13-product-flash-switch/）与 §3.0.20（日常生活便利，已实现、自测通过，待独立复核；证据 runs/2026-10-18-e1-daily-convenience/；2026-10-18 在声明的 V1 合成范围内通过独立审核，见 runs/2026-10-18-e1-daily-convenience-review/audit.json），E1-5 的分轮范围见 §3.0.5，后续轮次待解决的问题见 PLAN §3.0.1。本节只做导航，下面的硬规则全部继续有效。

- 上面"不再有尚未裁决项"指旧静态研究协议的概念裁决。生活模型有八条数学政策：收付时序已由 DECISIONS/0017 定下；失败处理部分定下，仍有开放部分（LLM 重试、计划不可行后的重试、出局期间怎么计分、E1-5C 停止分身的重试与计分）；评分映射与物质细偏好聚合只有 DECISIONS/0018 的合成探索版本，仍未正式裁决；精力反馈只定下了月度额度约束（DECISIONS/0020：当月实际负担 ≤ 当月额度，超限是计划不可行），满足感对下一年精力的反馈只有 DECISIONS/0021 的合成探索版本（E1-4B），经验标定、α 的取值与是否允许低于基础仍未定，政策仍是 undecided；风险只有 DECISIONS/0027 的合成探索版本（E1-5C 的疾病：概率表、病程、恢复负担、治疗价格与疗效、γ 与 H 都是合成设定），仍未正式裁决；折现与期末价值只有 DECISIONS/0028 的合成探索版本（E1-6A：评价规格声明的月度时间权重 δ，主设定 1；从真实终态出发、参考估值 12 个月；2026-10-12 起参考估值是 DECISIONS/0029 的基础生活模板 × 治疗分支），仍未正式裁决。全部定下之前生活协议不能标为可评分，也不得由实现者静默选定，不得靠把待定政策标成 decided 来解锁演示；探索评分能运行不等于协议可正式评价；"不折现""残值折成未来一年消费"不是默认值。
- 生活结算的分工：common 只放形状与局部约束（计费换算 common/life_billing.py 是结算与加载器共用的唯一一份），ledger 只做加载与跨对象检查（结算前检查与加载器共用一份），econ/life_settlement.py 只做执行、记账与状态推进，agentlayer 只管许可与可见事实。结算不读偏好真值、不接评分器；金额在内部用整数最小单位，守恒是精确等式，不加容差。
- 生活评分（econ/life_scoring.py）只读实际结算记录，不读计划与理由，不改结算结果；价格、工资与现金余额不进入评分。年付服务与耐用品只从 HoldingService 读服务月，同一需求的替代满足只计一次。先逐月评分再求年均，十二个月都可评分才有年均。锚点 τ 只在评分规格里，所有城市共用；基础条件、改善效率与上限可因城而异但须带来源，且基础条件单独不得超过上限。有正权重的数据未知时该月不完整，不补零、不逐城重新归一化；出局当月与之后单列状态。产品偏好只认用户明确给出的数值（七维点数与物质细项点数各除以 100），不足就报信息不足，不把序数换成数字、不借用研究真值。评分规格、城市评分条件、评分偏好与评分结果都不进代理观察或反馈。参数是合成探索设定，不得据此排序城市。
- E1-3 增补（DECISIONS/0019）只在新协议 life-v1-e1-3s 与评分版本 e1-3-supplement-v1 启用；旧协议照 DECISIONS/0018 运行，旧样例、旧配置与旧 runs 不改写。持有服务数量由结算写出、评分直接读取；只有旧评分版本读旧记录时才按顺序推断，信息不足报错，不默认为 1。按功能过滤的来源只取“存在”或数量时不得相加，同功能第二件不再加分；只有读取明确容量规格的来源可以相加。功能分类不自动拆偏好：拆出的评分叶子必须由样例或用户明确给点数。方案属性（品牌、菜系、档次）只存储、报告未评价，不从价格推断偏好。环境景观 S = min(c, 基础 + β·A_nature)，β 只写在评分规格里、全组共用；β > 0 时景观未知不当零，基础环境已含同一景观不得再加。方案级通勤读当月实际所住住房（含兜底）的往返分钟，且当月恰好一份执行了的线下工作才评分；无工作、远程、多岗位报不支持/信息不足，不给最高分也不照扣；往返分钟不得列成可选安排（这条只管 e1-3-supplement-v1 及更早的版本；评分版本 e1-5a-growth-transport-v1 改为广义交通，见 E1-5A 补充条）。比较组冻结共同粒度，确定不可得合法、未声明不合法。覆盖报告只报告：不设百分比阈值，不解锁正权重未知的总分。
- E1-3 边界修复（DECISIONS/0019 第十二节，2026-10-08）的接口约定：非 ADR0018 版本的每个评分来源必须声明 measure（presence 有没有、件数不进入，只能 any 或 max；capacity 规格容量 × 件数，必须 per_unit；level 只取最好一项的规格水平，只能 max；count 一次体验算一次，只能 sum、只读 experience），按功能过滤的来源只能是 presence 或 capacity，且与生活输入 function_kinds 的声明一致。每座城用 function_supply 写明评分要用的功能是 available、unavailable 还是 unknown，并与目录交叉核对；目录里没有记录不等于确定没有，供给未确认且当月没有在用物品时该项报未知，不给确定的 0。单位只认 common/life_units.py 登记的规范单位与别名：比较组单位表的资料单位必须与评分读取单位是同一单位，评分器不换算原始资料（换算在资料整理时做并标 derived），读取单位到分项单位只能是同量纲的登记换算，方案级通勤的 τ 与往返分钟同一单位；不按字段名猜单位。覆盖报告是 coverage-v2：规则覆盖、城市资料覆盖、实际行动可评分性分开报，生活事实来源与评分参数来源分开报，候选与取值复用 econ.life_scoring 的 source_candidate 与 source_reading，不另写一套；依赖里还有合成设定就不是 data_coverage；coverage-v1 旧报告可读但 catalog_facts_checked 为 False。内部评分对象与它们原样序列化后的字典在正式加载、可见投影与最终 payload 三处都被拒绝（RESERVED_SCORING_KEYS 加 serialized_internal 结构检查，内部类型清单只在 internal_scoring_models 登记一处）；这不是对改名或自然语言隐写的通用检测器。新增内部评分类型必须加进 internal_scoring_models，新增保留键不得与可见对象的字段名重叠。
- E1-4A 月度时间与精力（DECISIONS/0020）只在生活规则 e1-4a-monthly-v1 启用（E1-4B 的 e1-4b-monthly-feedback-v1 沿用同一套月度资源规则）：这两个版本必须带 resource_rules，其他版本不能带，旧规则的协议出现资源事实或资源预算依据报错，不悄悄忽略。新协议里每项机会必须写资源事实（计量依据 passive / per_service_month / per_use / per_occurrence / job、小时、每小时负担、数量算不算，零也显式写），旧字段 time_cost_hours_per_year 与 energy_burden 必须为 0；缺资料是 data_error，不当零。每月时间预算 = 年度净可分配小时 / 12，月与月不借用；energy.level 只有在尺度 e1-4a-monthly-quota-v1 下才是每月可承受负担额度（必须为正；E1-4A 协议里同一个人跨月跨年跨城不变，E1-4B 见下条），旧尺度的数值不得解释成额度。精力不结转、不回血、没有负负担，满足感不在年内改变额度，额度用完不另扣幸福分；年与年之间的满足感反馈只在 E1-4B 协议启用（下条）。时间与精力的算法只有一份，在 econ/life_resources.py（纯函数，只依赖 common.life_contracts，不读偏好真值、评分或行动理由），结算是唯一调用方；ledger 只检查资源资料齐不齐，不算小时、不引用 econ；评分不读资源账（唯一例外：E1-5A 补充版本的广义交通只读到岗次数 onsite_visits 这一个计数，见 DECISIONS/0024）。结算在每月第 7 步之后只按实际执行的东西核算（实际住房含兜底、拿到工资的工作、执行了的服务、发生了的体验、参加了的培训、服务期内的持有物；年付服务与持有物只从持有服务读）；超限是 plan_infeasible（不提交账本与下一年状态，带诊断，不自动删活动），不是 input_error；比较用精确有理数，等于额度通过，不加容差。已安排时间 ≤ 预算，未安排时间（time_remaining）月底作废，不解释成休息、不回血、不结转，也不要求安排满。每月记录的 resource_check 只有 complete / known_lower_bound / not_reached 三种，within_limits 只在 complete 时有值。出局当月（执行修订 r2，DECISIONS/0020 第十二节）记已入账工资对应的工作并检查已知下限：已知用量超额度就是 plan_infeasible，资金出局不能绕过资源约束；没超照常出局，但不得写成完整检查或全年通过；需要到岗而没有住房时通勤记为未知，全远程时为零。岗位方案绑定薪酬与工时、到岗日，计划不能改；多份工作（含同月两份到岗工作）可以执行，工资、工时、负担相加。通勤 = Σ 实际所住住房的往返分钟 × 每个到岗岗位的到岗次数 / 60，每个岗位每次到岗一趟完整往返，不合并行程；onsite_visits 与 job_workdays_sum 是累计计数，不是日历天数。家务取替代后残余用时（本人处理整项家务仍要花的整月时间）的最小值，不逐项相减。执行修订 RESOURCE_RULES_REVISION（当前 r2-2026-10-08）写进资源账与诊断；改资源执行语义要换修订号并在 ADR 记录，不保留旧修订的执行器。可见字段 time_energy 是公开资源事实；精力额度与用量服从 show_energy_level，标准产品观察用 standard_product_permission 显式打开，研究对照可显式关闭（隐藏的是额度与汇总，公开的每小时负担照样可见）；MonthlyResourceRules、ResourceYear、MonthResources 整体不进 payload；资源诊断将来给代理时精力部分同样服从许可。不得用 LLM 估计资源数值。
- E1-4B 满足感 → 下一年额度（DECISIONS/0021）只在生活规则 e1-4b-monthly-feedback-v1 启用：这个版本必须同时带 resource_rules 与显式的 energy_feedback（漏填报错，不得静默当成关闭），其他版本不能带；它的状态必须写 energy_quota_basis，E1-4A 与更早的状态不能写。公式只有一套：下一年额度 = 基础额度 × (1 + α × S)，向下保留到 0.01 负担点（先存未量化值，不加 epsilon）；S 只取 E1-3 探索评分器对这一年实际结算结果的完整年度平均（12 个月都已评分、有限、在 [0, 1]）；0 ≤ α ≤ 0.10、两位小数、预先声明，不看结果调。只用上一年、每次从固定基础额度算（不复利）、不低于基础、下一年 12 个月同一额度；时间预算不变，空闲时间不回血，加成不进满足感。不得同时实现 ADR0016 带参考点、可正可负的建议公式。分数只由 econ.life_year.run_life_year 在结算之后、用这一年年初状态对本次结果调用 score_year 得到，不缓存、不接受外部同名分数；人、分身、城市、年份、评分规格与偏好来源对不上即拒绝；产品路径只用 StatedLifeProfile，缺偏好时阻断，不借研究真值。关闭或 α = 0 不需要评分，业务结果复现 E1-4A（只允许 outcome.notes 与 energy_quota_basis 不同）；反馈开着却没有完整评分是 feedback_blocked：当年结算保留、不返回下一年状态，不补零、不用部分月份、不重新归一化、不维持旧加成、不擅自恢复基础额度。结算产生的下一年状态是 pending_feedback 候选，不能结算（energy_quota_pending）也不能构造观察；重复交接不叠加；加成状态不得作为关闭对照的起点（feedback_policy_switched）；计划不可行与输入、资料错误不反馈不推进，出局不更新，第 N 年后是终态、不算加成。同一个人跨城基础额度与时间预算一致，之后的当前额度可以因城而异。观察的 channels 分开记额度是否可见、反馈是否真正开启（satisfaction_feedback）与研究条件，不用 dynamic_feedback_channel 判断 E1-4B 是否开启；research_text_only 必须在反馈关闭时进行；分数、反馈政策、反馈记录与年度交接结果都是内部对象，新增内部类型登记在 internal_feedback_models()（由 internal_models() 合成后供三处检查读取），不扩大保留键。年度交接执行修订 r2（DECISIONS/0021 第十三节）：已产生的额度绑定政策内容指纹（EnergyFeedbackPolicy.content_fingerprint；参与的是开关、α、公式、滞后、统计量、量化、评分规格与版本、偏好来源，以及 r3 起声明了的评分内容身份（DECISIONS/0025），policy_id、source、note 不参与）；第一年状态不绑定，候选状态在结算产生时绑定，正式下一年状态沿用反馈记录；编号、版本或内容任何一项不同都是输入错误，加载、结算与观察只用 common.life_contracts.energy_quota_problems 这一份检查，不在三个入口各写一套；缺 policy_fingerprint 的旧状态拒绝，不猜它通过了检查。反馈只从 econ.life_year.settle_and_score 封存的年度绑定 ScoredYear 生成（feedback_record(protocol=, year=)），不接受分别传入的账目与分数；apply_feedback 只写回记录指向的那一份候选状态。同人、同城、同年、同版本不足以认定同一次生活。能执行、能完整评分、能更新精力三件事分开报告；不补未知分、不删正权重、不重新归一化来制造完整评分，不因缺分自动关闭反馈。单个分身的预期停止（出局、反馈信息不足、计划不可行、输入或资料错误）只停这个分身，报告保留全部预定分身与停止原因、分类互斥并能与原计划人数核对；意外程序错误显式报告、不吞掉、不假定只影响一个分身；不删除未完成分身、不补新分身顶替、不只用完成者均值代表整座城市（正式调度在 E1-7，停止与缺失怎样进入评价在 E1-6）。
- E1-5A 相关培训与技能 → 岗位 → 实际收入（DECISIONS/0023）只在生活规则 e1-5a-monthly-career-v1 启用：这个版本必须同时带 resource_rules、显式的 energy_feedback 与 career_rules（漏填报错，不得静默当成没有学习收益或关闭反馈），其他版本不能带职业规则；旧协议出现 employer_id、grants、abilities、employment 或完成年月报错，不悄悄忽略，旧协议的 training_completed 年内解锁照旧。能力只有已获得 / 未获得（资格证书、技能、有作用域的知识），作用域 general / occupation / employer 只限制谁可以要求它，不让它满足别的要求；岗位与培训的要求只认 ability: / occupation: / employer:，旧写法 skill:、training_completed: 在新协议里是资料错误，新写法在旧协议同样报错；未定义引用、作用域冲突、雇主专属知识由别家授予都是资料错误，看不懂的要求不当作满足；不按行业名称判断相关性，不用 LLM 裁定。有效参加 = 计划月 + 学费已结清 + 当月实际执行；新协议每门课都必须按参加月（per_service_month）占用正的小时与正的每小时负担，零价合法、价格未知不当零价，学习与其他活动一起受月度时间与精力检查；报名只在起始月尝试一次；累计参加月数达到要求才完成，跨年保留进度、不重收学费；整份试算失败不留学费、进度或能力；出局保留已参加月份，出局当月不算参加。完成授予课程声明的能力，按能力 ID 去重、保留最早来源，第 y 年完成的能力从第 y + 1 年起可用（HeldAbility 校验，行动年的状态不得持有尚不可用的能力），不追溯改变当年工资。资格只用 common.life_contracts.qualification_gaps 一份判断（加载、结算前检查、结算与可见投影共用），按年初状态：能力只认年初可用的，职业与雇主只认年初的当前岗位，不读开局身份；计划里有年初不合格的项目整份退回（input_error，qualification_missing）。岗位可得且合格就按目录声明的薪酬、工时、到岗与负担执行（确定性合成规则，没有录用概率、名额或分身之间的竞争）；取得资格不自动换岗、不自动选最高工资、不同时保留两份岗位，工资只来自实际执行的岗位，走原有工资、税与资源账，不另写算法；多份工作照常可以执行。年末由 econ.life_career 按实际领薪写岗位记录 EmploymentRecord（用户陈述的职业、实际岗位与领薪月份、当前岗位 = 最后一个结算月实际在岗，出局年取出局当月），PersonYearState.occupation 只是兼容摘要（没有岗位或职业不止一个时为空，不取第一份冒充全部）；新协议不读数值 skills。能力与岗位记录在结算收尾时写入、在 settle_and_score 封存年度绑定之前完成，候选状态指纹覆盖它们，跨试算混用被拒。在 E1-5A 原评分版本里学习与职业本身不计满足感，偏好、反馈公式与基础精力尺度不变（评分版本 e1-5a-growth-transport-v1 的成长项见下条）。可见出口逐字段新增已有能力、岗位记录、机会的雇主与授予、按年初状态的资格提示（现在能选 / 还缺什么，未来目标标为当前不可执行）与规则说明，本轮没有新增许可开关；职业规则、能力与岗位的内部记录、当年职业结果与资格判断对象登记在 agentlayer.life_views 的禁止类型清单里。能力、学费、学习时长、负担与工资全部是合成设定，不得写成已标定的技能熟练程度或培训回报。
- E1-5A 补充（DECISIONS/0024）只在评分版本 e1-5a-growth-transport-v1（新协议 life-v1-e1-5a-growth-transport）启用，这个版本改变了评分，不得再称评分公式未变；成长项 growth 与广义交通 transport 只属于 GROWTH_TRANSPORT_VERSIONS，旧版本声明它们报错，新版本必须两项都声明，η = 0、ρ 等对照是另一个实验身份（反馈政策必须声明评分内容身份，换参数就换声明，见 DECISIONS/0025；仍建议另起规格编号）。发展维 z = b + η_growth × g_m，s = min(c, z/(z+τ))，月度总分仍由原有七维加权；b 是城市发展条件、不由本人学习结果回填；g_m 是当月实际新增的能力进度之和（年初已有 = 1，未有取授予它的课程中最高的“实际累计参加月数 / 要求月数”，0–1，多课取最高不相加），只读年初状态与同一次结算写出的学习记录、只用本月及之前的参加月份，不读理由、学费、工资或课程名称；没付成学费、没参加、被拒绝、已有或已学完的能力、暂停月份都不长；学习记录与年初状态对不上就拒绝；能力拆分粒度影响成长量（登记在 CA-06）；证书本身、换岗、职位名称与涨薪不另加分；资格仍下一行动年生效。通勤维键仍是 commute、人话含义是交通便捷程度及出行对生活的影响：k = ρ + (1 − ρ)·D/D_ref，T_eq = k·T，S = τ_T/(τ_T + T_eq)，T 读实际所住住房（含兜底）的往返分钟（必须是资源规则的 commute_minutes_key），D 读资源账的 onsite_visits（不另写到岗统计、不按工作方式字样或岗位数量猜，不在 D_ref 截断），资源账与评分读到的住房或分钟对不上就拒绝；无工作、远程、混合、多岗位资料齐全时都评，住房分钟或资源账缺失报未知、不补 0，出局月照旧单列。T_eq 是评分中间量、不是实际出行分钟，工作通勤的时间与负担账不乘 k、不变；非工作出行没有核算，用住房工作往返分钟代理广义出行是合成映射假设。η_growth、ρ、D_ref 只写在评分规格里、全组共用，都是合成探索设定，不得写成已由真实数据估计；成长与交通的规格与明细是内部对象（登记在 internal_scoring_models），η_growth、ρ、D_ref、k、T_eq 不进观察。覆盖报告与评分器同步：广义交通对任何安排可评、发展维有行动机制。新评分会改变下一年额度与计划可行性，这是预期行为；旧评分产生的中间状态不能接着在新协议里用，从共同初始状态重跑。课程长度是 12 个有效参加月的整数倍（不支持短课程），同一年里同一门课不能分段，整年暂停后恢复已支持、不重收学费；培训只代表主动的额外投入，不含正常履职或入职培训；qualification_missing 在 E1-7 是可修改、可重试的计划拒绝，与资料损坏、程序错误、生活出局分开。
- 评分内容身份与跨年精力额度（DECISIONS/0025，年度交接修订 r3-2026-10-09）：LifeScoringSpec.content() / content_fingerprint 复用 canonical_fingerprint，默认包含规格的全部字段（以后新增的也包含），只排除 spec_id、protocol_id、note、omissions、分项 description、来源 note、景观/成长/交通的 definition；键顺序不参与，列表顺序与单位写法参与，数值没有容差。评分版本不在 UNBOUND_SCORING_VERSIONS（e1-3-explore-v1、e1-3-supplement-v1）里的反馈政策必须声明 scoring_content_fingerprint（进入政策内容，格式 v2），漏写报错、不默认通过；加载（scoring_config_problems）与年度交接（life_year，结算之前）用 common.life_scoring_contracts.scoring_content_problems 这一份核对实际规格；结算的候选状态、反馈记录与正式下一年状态的 EnergyQuotaBasis 记下它；energy_quota_problems 的 scoring_content_changed 在加载、结算前检查与观察三处拒绝“额度来自另一套评分内容”：输入错误，不留账目，不恢复基础额度，不关闭反馈，不猜旧状态来源。第一年状态不绑定；关闭反馈、α = 0 不需要评分但照样核对来源。旧格式政策照旧是 r2、产物逐字节不变；未声明时期产生的中间状态在新路径不能续跑，从初始输入重跑。以后改评分数学（含 E1-5B）就声明新的内容身份。城市评分条件、比较组、偏好与硬要求只在年度绑定里逐年封存，跨年不绑定（已知边界，待所有者决定）。scoring_content_fingerprint 是保留键，不进观察。
- E1-5B 目标储蓄（DECISIONS/0026）只在生活规则 e1-5b-monthly-savings-v1 启用（= E1-5A 的全部规则 + 目标储蓄；这个版本必须同时带 resource_rules、energy_feedback、career_rules 与 savings_rules，其他版本不能带 savings_rules；状态必须写 savings_goals，没有目标写空列表，旧协议的状态、计划写了目标字段一律报错，旧文字 goals 不解析成资金目标）。目标只能是一次性计价的耐用品或一门课的一次性学费（common.life_contracts.savings_goal_kind），同一时刻一个未结束目标；年度计划年初用 savings_goal 指令设定、保留、取消、替换或改时点，不写就延续；目标 ID 由计划给出、全史唯一。建立时按现有可购买性规则拒绝：不存在、确定缺货、报价未知（不补零）、口径不同、总额为 0、耐用品仍在持有、学费已结清或已学完；指令或关联不成立是 input_error（goal_not_supported / goal_rejected / goal_link_invalid），整份计划退回。目标是软约束：不建账户、不转钱、不扣款、不锁钱；econ/life_savings.py 的记录器只读结算算好的月末余额与已发生的支出（结算是唯一调用方，三处调用），每个完整过完的月份月末记余额、目录当前报价 × 数量（common.life_billing.goal_quote / one_time_total_minor，复用 unit_minor）、max(0, 报价 − 余额) 与"余额是否不少于报价"（只是比较，不叫已获得）；报价未知或缺货时差距为未知；兑现那个月不再比较。兑现只认 ActionItem.goal_id 指向的那一条在某月实际全额付清（耐用品买成 durable_purchase_executed，学费付清 tuition_paid_in_full——不表示学完），付不起、缺货、未知、被拒、余额只是够了、没写 goal_id 都不兑现；一笔交易最多兑现一个目标。目标年月月末仍未兑现记 due_unrealized（逾期），不出局、不自动延期、不自动购买；出局当月及以后不再更新，终态保存目标与余额。目标与事件在年度绑定封存之前写进候选状态与 EnvironmentOutcome.savings，跨试算混用与重复交接由现有绑定拒绝。评分器不读目标：设定、存钱、兑现都不加满足感，同样的行动只改目标时现金流、逐月分数与额度必须相同；期末现金、未兑现目标与残值归 E1-6，不加"安心分"。可见出口是 AgentObservation.savings 与 VisibleFeedback.savings（build_observation 的 savings_rules 与状态的 savings_goals 必须同时有或同时没有）；SavingsRules、SavingsGoal、GoalQuote、GoalEvent、GoalRealization、GoalMonth、SavingsYear 登记在禁止类型清单里。演示的额外开支（探亲）不是疾病机制；疾病应急归 E1-5C。
- E1-5C 疾病、应急与恢复（DECISIONS/0027）只在生活规则 e1-5c-monthly-health-v1 启用（= E1-5B 的全部规则 + 疾病；这个版本必须同时带 resource_rules、energy_feedback、career_rules、savings_rules、health_rules、illness_events 与 health_input，其他版本都不能带；新协议的状态必须写 health、年度计划必须写 emergency，旧协议的状态、计划、城市目录（treatments）或画像写了健康字段一律报错，不悄悄启用）。七维里的 healthcare 点数在新协议表示健康风险应对倾向（health_risk_coping_v1，只供代理计划）：不是病痛权重、患病概率、恢复速度或疗效，结算（签名里没有画像）与风险抽签都不读它；画像与研究真值必须写 healthcare_semantics 才能进新协议，旧画像进新协议、新画像交给旧评分版本，在加载、年度交接（结算之前）与评分三处都被拒绝；不从年龄、财富或旧答案推断风险态度，不按健康输入替代理选择治疗。评分版本 e1-5c-health-v1：w̃_k = w_k / Σ_{j≠healthcare} w_j（按十进制写法精确相除），B = Σ w̃_k s_k（六维分项与 E1-5B 逐项相同，医疗维没有城市背景分项、声明了就拒绝），S = (1 − γ)·B + γ·H(月初健康状态)，γ 与 H 只写在评分规格的 health 里、全组共用（合成：γ = 0.2，H = 1 / 0.5 / 0）；同等病情扣分相同、不乘个人权重，付医疗费不加分，没有未满足医疗的额外惩罚，六维点数全 0 报信息不足、不用平均权重；只改健康输入时现金、事件、病情与逐月分数逐位相同；γ = 0 对照是另一个评分内容身份，从共同初始状态重跑，额度不跨臂（scoring_content_changed）。世界规则（HealthRules，全组共用、合成设定）：健康 / 小病 / 大病，一次一病，新事件只在健康月份月初发生，月初状态决定当月的工作限制与健康分；小病照常上班、多一份恢复时间与负担，大病整月病假、没有工资（没有工时、到岗与通勤），暂停一切有参与时间的主动活动；不治疗分别病满声明的月数月末自然恢复，在某个病月治疗则当月月末恢复，只在治疗还有用的月份购买，一次疾病最多付一次；恢复（recovery）与就医（treatment）走 econ.life_resources.month_resources 的 extra 进入同一本时间与精力账，没有疾病精力系数，E1-4B 额度公式不变（新评分尺度改变下一年额度是预期的）。城市只经治疗目录（CityLifeCatalog.treatments，小病、大病各一条 available / unavailable / unknown，当地基年一次性报价、就医小时与负担、需求与来源）影响生活，疗效全组共用；未知不当免费、也不当没有：计划要治却遇到未知是资料缺口，这个分身停在那个月（stopped / data_gap），不影响别的分身。应急安排（EmergencyPlan）必须显式写：小病、大病各选 treat_if_possible 或 decline，以及暂停顺序（只能是有参与时间的主动活动）；漏写是 emergency_missing，不默认总是治疗。付款顺序：最低生活检查 → 住房、食品与兜底 → 治疗 → 其他行动，不借钱、不用未来工资、不自动降级。提交边界：先用同一计划、同一年初状态做没有新事件的试算，不可行是年初的计划问题、什么都不提交；真实执行里事件之后的不可行不回滚——病月依次试不暂停、按顺序逐项暂停、全部暂停且放弃治疗，仍不行就停止（PersonYearState.status = stopped、StopRecord，emergency_insufficient），停止月之前全部保留，停止月不提交执行（资源 blocked，评分 stopped_month / after_stop，没有年均），交接 stopped、不更新精力；停止是模拟器的支持边界，不是出局，正式重试在 E1-7。衔接：病假不是辞职（岗位记录分开写领薪月份与病假月份）；暂停的上课不加进度、不重收学费；已付清的年付服务与持有物照样流逝服务月但没有参与收益；医疗花费与病假少挣的钱走同一本现金账，目标进展按余额更新，没有圈存与安心分。事件来源（IllnessEventSource，实验设置）：none / injected / random，指定与随机走同一个处理器（econ.life_health.HealthYearTracker，结算是唯一调用方）；月概率只按年龄段 × 上月实际工作相对负担查表；随机键 = 流版本、配对身份、种子、人、重复分身序号、绝对月份、事件类别的规范 JSON 取 SHA-256 前 64 位 ÷ 2^64，不含城市、分身编号、遍历顺序、重试次数或规则指纹，不用 Python hash()——重放、重试、增减别的分身都不重抽，同序号的分身跨城共用运气。健康状态绑定产生它的规则与事件来源的内容指纹（共同初始状态不绑定），换了就是 health_rules_changed / illness_source_changed。默认信息条件 default_no_risk_relation：代理看得到自己的病情、规则、恢复成本、本城治疗与上一年的病月记录，看不到疾病与工作负担、年龄的关系（方向、概率表、系数）、随机数、事件来源、未来事件与 γ、H；健康规则、风险表、事件来源、抽签、停止与暂停记录等登记在禁止类型清单里（RESERVED_HEALTH_KEYS 另管风险表与抽签的键）。没有保险、带薪病假、借贷、死亡、永久伤残、多病叠加、疾病名称或小病请假；概率、病程、疗效与价格都没有标定，不得据此排序城市。收尾修订（DECISIONS/0027 第十五节，2026-10-11）：状态与疾病规则的关系只用 common.life_contracts.health_rules_state_problems 这一份判断（规则版本或内容指纹不同 health_rules_changed；非第 1 年状态缺绑定、年龄不在风险表、延续疾病早该结束 health_state），加载与结算前检查（ledger.life_catalog.health_state_problems，另核对事件来源 illness_source_changed）与观察构造（build_observation，在任何按许可裁剪的代码之前、不受显示开关影响）共用，规则冲突时不构造观察；第 1 年的共同初始状态可以不绑定。年度计划的应急安排可以写 cancel_purchases_when_ill：按机会 ID 列出这一年计划里的一次性耐用品购买（计划里同一机会只能出现一次），购买当月月初生着病（新发或延续，月末治好也算）就取消这一次——不查报价与现金、不扣款、不生成持有物、不顺延、不补购、不自动延期目标；健康月份照常买；不读以后的事件；只覆盖一次性耐用品，引用不存在、不在计划里或不是一次性耐用品是 emergency_cancel_invalid（执行前退回）；记录是 ItemStatus cancelled_when_ill 与 MonthPause kind agent_cancel_when_ill，与资金不足、不可得、资料未知分开；关联目标的购买被取消时目标不兑现、到期记"关联的购买已取消"；声明原样记进 HealthYear.cancel_purchases_when_ill，可见于 VisibleHealthYear.declared_purchase_cancellations；不由健康输入生成，不改 pause_order 的含义。H(健康) 固定为 1（健康项的归一化基准，相对健康的扣分就是 γ(1 − H)），HealthStateValues 拒绝其他值、不自动改回 1；γ 与小病、大病的 H 是待研究的合成评价参数。只看重交通的人大病月分数可能高于上班月：符合公式，登记给 E1-6，不加疾病惩罚、不改通勤公式、不裁剪分数。
- E1-6A 单分身跨期评价（DECISIONS/0028；2026-10-12 收尾后评价版本 e1-6a-evaluation-v2、执行修订 e1-6a-r2，DECISIONS/0029；v1 / r1 当前代码拒绝执行，旧配置与证据保留，不保留旧修订的执行器）不改任何生活或评分语义，只在逐月结果之上做三件事：L_actual = 实际 T = 12N 个月（N 读协议，不写死 36）逐月满足感按 w_m = δ^(m−1) 的加权平均，T 个月都有合法评分才给；V_reference = 从真实终态出发、评价规格里预先冻结的基础生活模板（最多 6 个，岗位、住房、食品、服务、条件购买，不含治疗选择）按估值期初健康状态展开的分支在 H = 12 个月估值期里真实结算与评分后，能完整执行、完整评分、已知满足硬要求的最高 V_p（参考集合内最高，不是预测、不是全局最优）；分支规则 by_continuing_illness_v1：估值期初健康时每个模板一个分支（编号就是模板编号，治疗选择未触发，应急安排写 decline 只为满足年度计划格式、不代表偏好），带病时每个模板只针对正在延续的这场病展开 模板.treat（treat_if_possible）与 模板.decline 两个分支，合计最多 12 次试算；每个分支交给原有 E1-5C 结算，decline 不因现金够而改成治疗；选择比较模板 + 分支，并列按分支编号字典序展示；治疗选择不由医疗点数生成、不调用 LLM；应急安排只在带疾病规则的协议上写；现金更多 V 不下降不是普遍验收要求（所有者 2026-10-12 撤回），只能作为个别样例的事实测试；J = 两部分按月份权重合成（N = 3、δ = 1 时 0.75 L + 0.25 V），两部分都完整才给，不改成 L、不重新归一化。δ 与 H 写在评价规格里（δ 十进制、0 < δ ≤ 1，主设定 1；H 只支持 12，其他长度拒绝，不静默改成 12），都是合成评价选择。评价只接受受控运行里的年度结果：每一年带着 run_life_year 封存的年度绑定（私有属性，不序列化），交接用 handoff_from 从封存绑定重新推出，不相信结果对象里的字段；每一年外层的人、分身、城市、年份必须与封存的 ScoredYear 逐项一致，评价器拿到的目录必须是封存生活的城市，完整与部分轨迹同样核对，CloneEvaluation 的身份与重复分身序号取自封存绑定，重算结果指纹不能让错标签变合法；正式入口 evaluate_clone 与 value_terminal 在任何试算之前经 check_city_binding 调用与加载器同一份按城检查 ledger.life_evaluation_config.city_binding_problems，不通过报配置错误，不混成无法评价，合法的确定没有 / 资料未知不受影响；从 JSON 还原的结果拒绝（不支持直接导入，从原始输入重放）。估值只对正常结束的终态，估值上下文只在 econ.life_terminal_value 的估值入口构造并记下每项转换（派生协议 N → N+1、事件来源换成没有新发疾病的 none、terminal → active、年份不重置、到期持有物不带入、健康绑定改指派生来源、额度用第 N 年完整年度满足感按原反馈政策重算，关闭时用基础值，第 N 年不完整则估值不完整）；普通 settle_year 照旧拒绝真实终态，估值状态不接回实际生活。参考方案变成普通年度计划由现有结算与评分执行，不复制工资、税、资源、病程或评分数学；不新开、不续学课程；条件购买在槽位认得出是哪一件物品时先看期初持有，在服务期内就照用、不要求新品可得或报价，没有才查供给、价格与资金，不做跨品牌替代；方案里任何一项因钱不够被拒就是已知不可行；资料未知、评分不完整、硬要求无法判断是无法评价——有无法评价的候选就不给 V_reference（已知最好结果只作诊断），全部不可行也不填 0，不把实际完成生活的分身改成出局。评价规格有内容身份（去掉编号、备注、标签与说明），单分身结果绑定规格、协议、目录、税、评分输入、各年年度绑定、终态与各方案指纹。评价侧内部对象登记在 internal_evaluation_models，保留键 RESERVED_EVALUATION_KEYS 不与可见字段重名，不进观察。城市层面口径（所有者 2026-10-11）：确认的生活出局不进城市平均、不当未知分数、比例以原计划人数作分母并保留事件链；模拟问题（资料缺失、评分不支持、反馈受阻、程序错误）与出局分开，另作不确定性范围、不补零；停止不取消别人的结果，stopped 不自动改称出局；城市平均是完成且评分完整的分身平均——计算在 E1-6B（下一条），A 只提供分类与证据（含逐月事实摘要 MonthFact 与结算问题代码）。已知的交通—健康反例（只看重交通的人大病月分数可能更高）原样保留，不调疾病惩罚、不改交通权重。
- E1-6B 城市汇总（DECISIONS/0029，汇总版本 e1-6b-aggregation-v1、分类规则 c_e_m_owner_2026_10_12_v1、范围规则 known_months_plus_unit_bounds_v1）只读 E1-6A 的单分身评价结果，不改任何生活、评分或评价语义。汇总单位是用户（偏好组）× 城市 × 重复分身序号；原计划名单必须在跑任何生活之前由 econ.life_aggregation.freeze_roster 冻结并带内容身份（ledger.life_roster 只做加载与跨对象检查，含标准开局 v1 与每个运行臂的完整跨城评价配置检查；税与评分输入的身份函数在 econ，ledger 不引用 econ），不从收到多少结果反推人数；名单为空或内部冲突整体拒绝。每个名单项恰好一类：C 实际生活正常完成、实际评分完整、估值完整、J 非空且在 [0, 1]、核对通过；E 可信结算记录明确为付不起最低住房与食品而出局；M 其余，八种原因按最早失败的环节（未运行 / 未返回、计划或执行未完成、资料缺失、评分不完整、精力反馈受阻、E1-5C 停止、估值不完整或没有可用参考分支、输入 / 配置 / 程序错误）；N = C + E + M；completed_scored 不是 C 的充分条件；停止、资料缺口、计划不可行、没有可行参考分支与程序问题不改称出局。每份结果进入汇总前重算指纹并对照冻结名单核对身份（有记录时含重复序号）、运行臂、协议、目录与税、评分输入、第 1 年开局与时间权重；单项问题记为该项的 M 且不提取任何已知月份，名单外结果单列拒收，不同 δ、H、N 或评价内容不能静默平均。完整评价分身的平均分 = Σ J ÷ C（同一批 C 的 L、V 一起给，C = 0 为空，不用 L 代替 J）；出局比例 = E ÷ N，缺失比例 = M ÷ N；模拟缺失范围的 M 项 下界 = (K + W_future × v_low) ÷ (W_actual + W_future)、上界 = (K + W_actual − W_known + W_future × v_high) ÷ (W_actual + W_future)，K 与 W_known 只取可信的已知实际月份，V 完整时上下界用 V、否则 0 与 1，C 项取 [J, J]，出局者不进，城市范围的分母是 C + M；检查 0 ≤ 下界 ≤ 上界 ≤ 1，矛盾报错不裁剪；全程精确分数，显示值不参与判断；不插补、不重加权、不补零、不 bootstrap，已知最好分支不当 V、不收窄范围。范围不是置信区间。不设未经所有者决定的最低通过人数或缺失比例门槛。事件链只从已核对的记录生成，停止不写成看不起病，资料未知不写成当地没有，标签不互斥、不是因果贡献。硬要求单独报告，写明原计划人数、有可信记录的核验人数与未核验人数（没有记录不是核验过、没问题），不删人、不扣分、不改出局。城市比较给范围差 [A 下 − B 上, A 上 − B 下]，含 0 就不能确定方向，不是显著性检验，不发布排行，出局率一起展示。评价对照臂在名单里事先声明、用同一批实际生活、只改评价规格，不覆盖主结果；H 只支持 12。标准开局 v1（2026-10-12 收尾 R1，START_CONDITION_RULE v1_standard_start_2026_10_12，DECISIONS/0029 第八节）：城市比较只接受第 1 年 active、还没有任何生活记录的开局——健康、没有病史与月份负担记录，没有开局持有物、培训进度、结构化资金目标与经历摘要，岗位是开局声明、精力额度是第一年基础值、能力只来自用户陈述；同一用户的个人条件（ledger.life_roster.PERSONAL_CONDITIONS：账户口径与现金、年度净可分配小时、精力额度与尺度、资源预算依据、基础额度、开局能力、年龄、陈述的职业、旧协议的数值技能、文字目标）逐字段一致；分身编号、城市、重复序号、开局声明的当地岗位与健康绑定可以不同。检查只在 ledger.life_roster.roster_problems（load_roster 与直接 freeze_roster 共用），冻结之前拒绝并写明分身、字段与原因，不是生活出局、不删人、不改原计划人数、不修改或补齐状态，不进通用生活引擎（第二年带物品、带病跨年与终态估值照旧）；不做跨城物品等价映射、初始疾病抽样或状态生成器，也不得为通过检查把所有分身改成某一个分身的状态；改支持范围要换规则名并记 ADR。roster_problems 对每个声明的运行臂复用 evaluation_spec_problems（R2），缺规格、多出未声明的规格给明确诊断。冻结名单记 start_condition_rule 与 aggregation_revision e1-6b-r2（内容格式 v2，名单项另记用户声明的硬要求），城市报告与敏感性报告抄录；旧冻结名单读入时拒绝，不给历史结果事后补上已通过新检查的标签。敏感性对照（R3）先核对报告与评价自身的指纹，再按报告里每个名单项的 evaluation_fingerprint 一一配对传入的评价结果（缺、多、重复、旧缓存、错运行臂都是证据问题，不用字典覆盖静默解决；没有可信评价的名单项保留缺失含义、不拿别的结果补），配对之后才比较逐年封存的生活绑定；lives_check 是 identical、different、evidence_mismatch 或 no_comparable_evidence，actual_lives_identical 只在 identical 时为真，证据不足与生活不同分开说，写明比较覆盖；核验失败不改写两份报告、不取消主结果。汇总侧内部类型登记在 internal_aggregation_models（由 internal_models 合成），保留键 RESERVED_AGGREGATION_KEYS 不与可见字段重名，不进观察。全部是合成探索版，不得据此排序真实城市，exploratory_synthetic 不得改成正式可评价。
- E1-7A 代理规划、事实记忆与合成城市闭环（DECISIONS/0030，规划接口 e1-7a-planning-v1、运行配置 e1-7a-run-v1、提示模板 e1-7a-prompt-v1、记忆政策 e1-7a-memory-v1、城市事实 e1-7a-city-facts-v1）：本轮的规划器是可控的 mock（agentlayer.life_mock），只检验接口、状态传递、错误处理与信息隔离，不代表真实模型的能力，不得拿它的结果说明模型会生活；真实模型的接线、服务重试、token 与费用记录、付费试跑都在 E1-7B，没有所有者批准的预算不得调用。规划器只拿 agentlayer.life_planning 的安全投影：请求 = 许可投影的观察 + 城市背景事实（VisibleCityFact）+ 获准的记忆 + 公开规则 [+ 上一份候选的安全诊断]，交给规划器的是纯 JSON 载荷（request_payload，构造与序列化后各扫描一遍），渲染进提示文字的也是同一份；编排器（src/orchestration/）可以持有内部对象，但不得把内部运行 bundle、研究真值、评分配置或完整内部状态交给规划器。输出只认一个 JSON 对象（PlannerOutput：需求摘要、计划理由、年度行动），身份由运行器填写，多写字段是格式错误，只用 json.loads 与契约校验、不执行输出里的代码；理由进 YearAction.rationale（结算不读），需求摘要只进规划记录与下一年的代理自述——只改摘要或理由、行动相同，生活结果必须相同，自述不改变余额、资格、进度或满足感，也不改写用户的点数、硬要求或研究真值。城市背景事实：可见事实表（common.life_city_facts.CityFactSheet：含义、数值或类别、单位、已知 / 确定不存在 / 资料未知、时间口径、来源性质，合成事实标合成）与内部换算 FactScoringLink（linear_base_v1，b = 系数 × 数值，来源与事实来源分开，不能标 observed；确定不存在必须声明对应值）；ledger.life_city_facts 在开跑前核对各城定义相同、已知事实换算的 b 与评分条件逐位相等、未知只对应未知、有城市背景的评分分项都有可见事实，不通过就拒绝启动；运行中规划前与执行后核对目录与事实的身份。不得把 CityScoringConditions 或换算参数放进观察（内部类型登记在禁止清单，换算键登记在 RESERVED_FACT_LINK_KEYS）。执行前检查只用 econ.life_settlement.check_year_plan（与 settle_year 共用 _screen 与无事件月度执行器，什么都不提交，返回 PlanCheck 的阶段与问题）；能否修订按阶段与问题代码判断（orchestration.life_runner.classify_check），不按状态字符串：plan 阶段可修订（运行器填写的身份与年份除外，那是程序错误），无事件试算的 plan_infeasible 与只有 treatment_data_unknown 的 data_error 可修订，其他资料错误是城市资料问题，参数、状态与城市资料阶段是内部错误。每年 1 次正常规划、最多 max_revisions_per_year 次修订（样例 2，范围 0–5，开跑前固定并记入运行清单）；网络重试与计划修订分开计数（本轮固定 0），不伪造 token 或费用。安全诊断只给获准公开、有助于改计划的信息：精力额度、用量与超出量服从 show_energy_level，不给内部评分、隐藏参数、未来事件或"换成某方案分数更高"。修订耗尽不替分身挑方案、不换规划器、不删偏好、不补分、不换人。计划通过之后真实执行里的疾病、出局、停止与反馈受阻都是已经发生的生活：不回到年初重问规划器、不重抽、不改写已提交的月份。记忆（agentlayer.life_planning.build_memory）：事实取自可见反馈，最近 3 年、每年 8 条、每条 240 字，固定顺序；代理自述只保留上一年并标 agent_self_report；截断写明；用户陈述不经过这一层；混入别的分身、别的城或未来年份就拒绝；一切记忆、诊断与历史响应服从当前许可。调度：顺序（名单顺序）；全局调用上限；生活出局、E1-5C 停止、反馈受阻、修订耗尽只停这个分身；城市资料问题只停这个分身并标明城市；共享配置、契约冲突或未知程序错误记为这个分身的程序错误（RunFailure，不提取已知月份）并暂停批次；没轮到的分身保留为未运行；原名单不缩小，不补新样本。运行前写支持范围说明。E1-6 接线只调用 evaluate_clone 与 aggregate_arm，不在运行器里重写 L、V、J、城市平均或缺失范围；部分轨迹带 PlanningStop（TrajectoryRecord.planning_stop，只能接在 not_finished 的最后一年之后；第 1 年就没有被接受时用 RunFailure(stage = planning, planning_stop)），汇总按 PLANNING_STOP_REASON 细分原因（修订耗尽 → 计划或执行未完成、城市资料 → 资料缺失、调用上限 → 未运行），C / E / M 与范围公式不变，没有用到时新字段不序列化。归档：运行清单记下全部内容身份（配置、名单、协议、目录与税、评分、评价、事实与对应、许可、提示模板、记忆政策、规划器与受控行为、事件来源、关键代码）；每次调用的完整可见请求与原始响应、处置、接受的行动与绑定指纹都保存；不写密钥；mock 的身份与用量不写进真实模型的调用日志。重放只用接受的行动经 run_life_year 重建（0 次规划调用），不反序列化结果后手工设置封存标记；恢复先核对运行清单，任何身份不同就拒绝，重放已接受的年度并核对绑定，已记录的回答复用，尝试次数与调用次数不清零；不承诺月内恢复。收尾（DECISIONS/0030 第六节，执行修订 e1-7a-r2，运行清单记 execution_revision 与 planner_behavior）：恢复与重放共用一份归档核对 orchestration.life_runner.load_archive_records（键不重复、不用后一条覆盖前一条；存下的请求、提示文字与原始响应重算的指纹与记录相同；检查记录对应它的调用；已接受的行动指向处置为 accepted 的那份候选，并且就是用 parse_output 解析那份原始响应得到的行动；只有最后一次调用可以停在“响应已落盘、检查尚未落盘”或“检查已落盘、年度尚未执行”）；恢复再从第一个分身起用同一套解析、执行前检查、处置与执行重放已经记录的路径，检查记录、已接受的行动与分身结局逐项与归档比较；所有已有记录都重放到之前不写任何文件、不调用规划器（_leave_replay），重放中的意外错误也拒绝恢复；冲突是运行归档的问题（RunRefusedError 写明分身、年份、第几份候选与字段），不归为生活出局，不靠重新询问规划器掩盖，归档层不另写计划判断。规划器接口有 effective_spec()，运行器在建归档与第一次调用之前、恢复在读归档之前比较 PlannerSpec.behavior_fingerprint（身份去掉 note，加受控行为的分身、行为与年份；mock 按集合取用受控行为，先后与重复不进入身份）与身份；漏传、替换或多出受控行为都拒绝，不报设定的对象不能作为正式运行的规划器；真实模型的温度、token 与服务重试将来加进 PlannerSpec。事实记忆的领薪月份由 agentlayer.life_planning.month_ranges 按真实月份写区间、保留缺口并写月数（“1–4、6–12 月，共 11 个月”），不用最小与最大月份拼区间，多份岗位各写各的。r1 的旧归档在新执行器下被运行清单核对拒绝，从初始输入重跑，不写迁移、不改写历史归档。期末估值仍由已有代码执行，估值结果与参考分支不返回给生活中的分身。全部是合成探索版，exploratory_synthetic 不得改成正式可评价。
- E1-7B 真实模型接入（DECISIONS/0031，规划接口 e1-7b-planning-v1、提示模板 e1-7b-prompt-v1、运行配置 e1-7b-run-v1、执行修订 e1-7b-r1、规划器 llm.qwen.base_b / e1-7b-llm-v1）：真实模型规划器 agentlayer.life_llm.QwenPlanner 与 mock 同一个 plan() / effective_spec() 接口，只收运行器交来的安全投影（PlannerCall），不收运行 bundle；模型行为设置（configs/models.yaml 的 base_b、快照 qwen3.7-max-2026-06-08、思考关闭、温度 0.2、JSON object、输出上限 4,096）写在运行配置 LlmSettings 里，与 models.yaml 和实际规划器报出的设定逐项核对，不一致零调用拒绝；不得自动换模型、开思考、提高输出上限、扩大样本或追加费用。请求另附本城实际绑定的合成税说明（visible_tax_rules，从同一份 SyntheticTaxPolicy 投影，发送前与执行用的绑定核对）与年度行动说明书（action_guide，局部示例、编号 example.*，不是最优计划）；资料未知不得改成不治、不得替换计划；写给开发者的说明（DEVELOPER_NOTE_MARKERS）开跑前扫目录与事实表、发送前扫最终请求；最终请求检查 final_request_problems。三类失败分开：计划不合法 → 已有的有限修订；服务失败 → 每份候选最多再试 1 次（只对超时、断连、限流、服务端错误），否则整批停下（RunProgress.status = halted + halt_kind，不是生活结果、不替分身生成计划、可恢复、计数不清零）；程序错误 → 沿用 E1-7A。服务重试不消耗修订，修订不多给服务重试；每份候选最多 2 次实际请求，每年最多 3 份候选（硬规则 7 的常数次调用不变）。付费预算 orchestration.life_budget.RoundBudget：一本账在运行目录之外（paid_budget.ledger_dir），发送前按保守上界预留、返回后按实际用量核算，没有用量保留预留、不记零，头文件额度不改、换目录或 run_id 不重置；只是按已记录报价的应用侧控制，不得写成供应商级账单上限；没有账单之前费用写"按报价估算"。服务请求明细 provider_calls.jsonl（ProviderCallRecord，每次请求 intent + outcome，立即落盘）与规划记录 calls.jsonl 分开，恢复与重放核对两者一致；只有 intent 没有 outcome 的请求不自动重发（uncertain_send）。第一次付费调用之前冻结全部输入进归档 inputs/，重放与恢复用 preflight_from_snapshot、不需要密钥。日志与异常只记安全字段，不存密钥、鉴权头、环境变量或含凭证的配置对象。输入估计 ascii1_nonascii2_v1 不是实际 token，超过上限本地拒绝、不删减事实。试跑结果只说明这个快照在这一次合成试跑里的表现，不得据此比较城市或宣称模型能力；exploratory_synthetic 不得改成正式可评价。收尾（DECISIONS/0031 第八节，执行修订 e1-7b-r2，公开税说明 e1-7b-visible-tax-v2）：服务回复能否用来规划只由 agentlayer.life_llm.service_history 一份检查判断（请求编号属于本运行并对应分身 / 年份 / 候选；服务商、角色、快照与设定指纹等于当前设定；报价版本；发送意图与结果按顺序配对、不重复、成功之后不再发、次数有上限；每次意图的正文逐字节等于这份候选按当前设定由 expected_provider_body 渲染的请求体；返回模型等于设定），新回复写进明细之后、缓存命中、恢复与重放（load_archive_records 经 service_archive_problems，含还没有规划记录的那一份服务历史，最多一份、只在允许中断时）都调用它，不另写判断；服务成功不等于可以用来规划：结果记录 usable_for_planning，返回模型不同的回复原文、用量与费用保留，首次、缓存与恢复都停在 model_mismatch，不重试、不换模型；对不上是归档问题（ServiceRecordMismatchError → RunRefusedError），在追加记录、执行生活或调用规划器之前拒绝。规划器实际持有的预算由 orchestration.life_budget.budget_identity_problems 在开跑与恢复的第一次调用之前核对（额度、报价全文、解析后的账本绝对路径、头文件），恢复之前由 ledger_history_problems 按运行 / 请求 / 服务尝试核对本运行的服务明细与共享账本（缺预留、缺核算、金额矛盾都拒绝，不当空白预算、不猜测补回；其他运行的条目合法存在，额度按整本账算）；已预留、发送意图没落盘时沿用那份预留，不重复计数；只读重放不碰账本，付费对账（read_ledger、budget_reconciliation）另报。公开税说明按实际第一档税率写，不再假定第一档免税。旧执行修订的归档由新代码按身份拒绝，历史原样重放用证据目录里保存的旧源码快照。
- 产品生活规划器的默认模型（DECISIONS/0032，2026-10-13；其中“入口不写 --config 时读 Flash 配置”一处已被 DECISIONS/0035 推翻：2026-10-18 起入口默认读 Haiku 测试配置 configs/life/run_e1_7b_haiku.yaml，产品角色本身不变）：后续产品运行默认用 configs/life/run_e1_7b_flash.yaml——configs/models.yaml 的产品角色 product_planner、qwen3.8-flash、version_policy: rolling（官方页面与账户都没有它带日期的快照，不得编造日期）。研究角色 base_a / base_b 与 Max 配置 configs/life/run_e1_7b_live.yaml（base_b 的 qwen3.7-max-2026-06-08，身份与原试跑相同）不变；旧 Max 只能用 --config 显式选。滚动型号只在产品角色：研究用 ModelSpec 仍只接受带日期的快照，产品用 common.config.ProductModelSpec 必须声明版本策略、滚动必须写 rolling_note；运行配置 LlmSettings 的 rolling 只能配在 model_role: product_planner 上，不写 version_policy 时按固定版本处理且不进指纹；ModelsConfig.spec 找不到角色报错，不回退到 base_b 或别的模型。返回型号只由 agentlayer.life_llm.returned_model_accepted 判断（固定版本只认同名；滚动型号认同名或同名后接一个快照日期；-latest、别的型号、cmaas- 前缀都不认），service_history 与结果记录的 usable_for_planning 都用它；不认的回复保留服务与费用事实、批次停在 model_mismatch。模型、角色、版本策略、报价与账本都进运行身份，Max 与 Flash 的归档互不能接着跑；scripts/run_life_e1_7b.py 的 resume / replay / mock 只按归档冻结的配置执行、写 --config 就报错，pilot / main 必须写新的 --run-id。每次真实运行写 model_profile.json（请求型号、角色、版本策略、返回型号规则、实际调用参数、报价、账本、写入时间），实际返回型号逐次记在 provider_calls.jsonl；滚动型号的复现只能靠归档重放，不能靠重新调用。扩大试跑要所有者另给预算并换新账本、运行编号与证据目录；不得顺手改提示、温度、输出上限或思考开关来优化表现。
- 真实 LLM 测试与试跑用 Claude Haiku 5.5（DECISIONS/0035，2026-10-18）：所有在本项目工作的编码代理，凡是要真实调用大语言模型的测试、先导或试跑，一律用 configs/models.yaml 的测试角色 test_planner（claude-haiku-5-5，滚动型号，经 Claude Agent SDK，effort max）——（2026-10-18 这一轮另用对照角色 test_planner_sonnet（claude-sonnet-5-5，effort medium，configs/life/run_e1_7b_sonnet.yaml）在 Haiku 之后跑一次，只这一轮，不是长期规则）scripts/run_life_e1_7b.py 不写 --config 时就是 configs/life/run_e1_7b_haiku.yaml，日常生活便利版是 configs/life/run_e1_convenience_haiku.yaml；不要改用 Flash 或 Max，除非所有者明确要求（那时显式写 --config configs/life/run_e1_7b_flash.yaml 或 run_e1_7b_live.yaml）。离线测试照旧用 mock；-m live 的研究基座测试（base_b）不换；产品角色 product_planner 仍是 Flash。Agent SDK 只当“一个输入、跑一轮、一个输出”的 API 密钥调用：agentlayer.life_llm.ClaudeAgentSdkTransport（max_turns 1、tools []、setting_sources []、strict_mcp_config、每次新建并删除的隔离工作目录与配置目录、命令行进程只继承白名单系统变量与网络代理变量，默认 auth: claude_subscription 用本机已登录的 Claude 订阅（这时额外放行用户目录变量、不隔离配置目录，见 DECISIONS/0035 第八节），auth: api_key 时用项目 .env 的 ANTHROPIC_API_KEY 与官方端点；都不用桌面 App 注入的端点；命令行内部重试与提示缓存关闭）；不得给它工具、多轮、会话恢复或读设置文件。传输层接口 Transport 是厂商适配的唯一位置（请求体写法 _provider_body、发送、错误 / 用量 / 结束原因的翻译——Anthropic 的 end_turn 译成 stop），LlmPlanner（旧名 QwenPlanner）与预算、服务记录、一致性核对与厂商无关；新增服务商就加一份传输层与设定校验，不复制规划器。Claude 设定不写温度与 enable_thinking，写 response_format: prompt_only、thinking: adaptive（命令行固定，关不掉）与 effort（所有者指定 Haiku max、Sonnet medium），输出不另设上限（用模型最大值 128,000，思考 token 与回答共用）、超时 600 秒，只用在 test_planner / test_planner_sonnet 上。计费可选：运行配置的 paid_budget 与 unmetered_reason 二选一，进运行身份；额度也可选：账本的 max_requests 与费用上限都可以不写（只记账、不拦截），Haiku 与 Sonnet 的配置都不设额度上限；账本按报价币种记账（美元写 *_usd，人民币账本格式不变），报价可以写 max_input_tokens_at_price（Haiku 5.5 的 100,000 档），估计超过不发送、实际超过就停下。命令行会另加 Agent SDK 自我介绍与运行环境说明（含当天日期与临时目录路径），强制自适应思考，400 时最多重发 3 次——这些由本机抓包核对（runs/2026-10-18-haiku-test-planner/probe/），升级 claude-agent-sdk 之前要重跑探针。新的真实试跑要所有者批准预算，并换新账本、运行编号与证据目录。
- 日常生活便利（DECISIONS/0033，2026-10-18）只在评分版本 e1-convenience-v1（协议 life-v1-e1-convenience，生活规则仍是 e1-5c-monthly-health-v1）启用；这个版本登记在 HEALTH_SCORING_VERSIONS 与 GROWTH_TRANSPORT_VERSIONS 里（沿用健康语义与成长、广义交通），另属 CONVENIENCE_SCORING_VERSIONS。物质三项是 housing_space、fresh_food、daily_convenience（用户愿意在多大程度上花钱减少日常杂务）；便利分 q_m = Σ 任务组合（协议 household_tasks 的基准小时 − 当月资源账 household_task 行的本人残余小时），s = min(c, q/(q + τ))，τ（分项 tau）与 c（convenience.cap）全组共用、只写在评分规格里，城市条件写 plan_level；评分器只读这两样结构化事实，不重算替代、不解析说明文字、不读价格、付款或成交。设备与人工服务只通过实际减少的小时起作用，同样的 q 同样的分；成交、花钱金额与目标兑现不计分，便利版本不能再有洗衣分项或按 laundry / 便利任务功能取“有没有”的来源（convenience_version_problems），旧版本不能声明便利分项。资源账这个月不是完整检查、缺任务行 → 未知（不补 0）；重复任务行、残余越界、单位冲突（便利单位必须是登记的 小时/月 且与分项单位相同）、协议没有声明的任务（convenience_protocol_problems，加载与评分前共用）→ 拒绝；没有采用方案 → 确定的 0。便利版本拒绝 laundry 点数、旧版本拒绝 daily_convenience 点数（convenience_preference_problems，加载器与评分器共用）。ConvenienceSpec、TaskRelief、ConvenienceTerm 登记在 internal_scoring_models，保留键加 relief_hours、tau_hours；convenience 的说明文字不进内容身份，其余参与。行动说明书的日常杂务一节只在提示模板 e1-convenience-prompt-v1 出现（PROMPT_TEMPLATE_GUIDES；旧模板 e1-7b-prompt-v1 的请求与身份逐字节不变），便利协议的运行必须用新模板；mock 规则 e1-convenience-mock-rules-v1（ConvenienceMockPlanner）只把 M5 换成按每小时减负花费挑设备或服务，旧 e1-7a 规则不变，mock_planner_for 按声明身份构造；scripts/run_life_e1_7b.py 的 --mock-config 供 offline-check 与 mock 用，offline-check 核对 mock 配置与真实配置是同一份名单与资料。这是粗粒度近似（设备与人工、品牌、服务质量没有区分），τ、c、残余、价格与寿命都是合成设定，不得据此排序城市；六维＋独立健康的产品输入归 E3。2026-10-18 审核通过后的读法（DECISIONS/0033 第八节）：减少杂务本身对应便利满足感，剩余未安排时间不额外获得休闲或满足感，实际利用腾出的资源参加其他活动时按该活动原有规则评价（说明书里“省下的时间不会自动变成休息、休闲或满足感”按这个意思读，冻结的提示内容与版本不改）；daily_convenience 点数是相对于其他需求的重视程度，不是愿付金额，也不是必须购买的指令；便利覆盖的 city_data=complete 只说明评分要读的资料在合成目录里齐全，不等于城市设备与服务目录已完整采集；可执行的耐用品要求明确的 1–30 年寿命，缺失时保留在原始资料与适配层，不填 null 继续执行、不静默补值。默认入口不随审核改变：不写 --config 仍是原生活配置；不能只换 material_points 而其他部分仍接旧评分或旧说明书，也不能拿旧运行的中间状态接着跑便利版；生病时暂停零参与时间的按月服务以省钱仍未支持。
- E3-A 用户输入、共享契约与 E1 边界适配（DECISIONS/0034，2026-10-18，已实现、离线自测通过、待独立审核）：共享消息的唯一权威结构是 src/common/product_contracts.py（13 份消息，结构版本 e3a-product-schema-v2，收尾前是 v1），导出的 JSON Schema 与公共样例在 contracts/product/v2/（contracts/product/v1/ 是历史导出），scripts/product_e3a.py check-schema 核对一致性；改结构先改权威结构、升版本、重新导出与生成样例，不在前端另写一套。产品画像 common.life_product_profile.ProductLifeProfile（六维 material / safety / opportunity / leisure / environment / commute 精确合计 100，物质三项父项为正时精确合计 100、父项 0 记不适用；独立健康回答不占 100 分、不是第七个权重；没有 healthcare），偏好来源 product_confirmed_six_dim，协议的健康输入声明 IndependentHealthInputDeclaration（independent_health_answers_v1）。点数用规范分数（"30"、"50/3"），econ.life_scoring.preference_from_confirmed_profile 精确相除后只转一次浮点，effective_weights 对这个来源直接用六维权重（只允许健康评分版本），B 与 S = (1 − γ)·B + γ·H 不变；不得补 healthcare=0 或伪造七维原始权重。econ.life_year.PREFERENCE_INPUT_TYPES 是偏好来源 → 输入类型的显式对照表，不认识就报错，不落入研究真值分支。同一个人只能有一种画像，产品画像不配研究真值，七维输入不能进独立健康协议，六维画像不能进非健康评分版本或旧提示模板。派生产品协议 life-v1-e3a-product 与便利版规则和评分内容相同（评分内容身份不变），提示模板 e3a-product-prompt-v1（说明书多一节用户陈述的读法；旧模板逐字节不变），mock 规则 e3a-product-mock-rules-v1（应急是固定测试政策，不读健康回答）；观察许可 show_health_answers 对产品画像没有默认值，隐藏的部分列在 not_permitted。原始答案、审阅、确认分层保存，内容身份只核对内容；改答案、词表、问卷、转换版本或资料包，旧确认与旧预检查失效。职业只用受控词表与明确规则，不授予资格；金额只接受资料包登记的口径（本版只有 USD / 2024 → USD_2024_LOCAL 恒等），其他阻断，不改标签；文字 2,000 字符不截断、不提取；属性偏好只留存；没有执行保障的硬要求报未支持。便利资料经 SourceFact → OpportunityCandidate → CatalogCompilation 进入 E1：寿命必须是明确的 1–30 整数年，报价未知编成 availability unknown，寿命、效果、本人参与未知或按次计费不编入；来源性质取最弱者；OptionInventoryCoverage 单独报告、分母不缩小。产品结果是 E1 报告的白名单投影（N = C + E + M、均值分母 C、缺失范围分母 C + M、空值不补 0），不含评分参数、模型理由、本机路径或密钥，不回灌给仍在生活的分身。产品链用 scripts/product_e3a.py 显式编译便利版整套配置，CLI 默认配置不变；本轮只有合成资料包、mock 与本地离线桥接，没有托管服务、真实资料或真实模型调用。收尾（DECISIONS/0034 第九节）：产品运行的运行清单另记 source_set e3a-product-sources-r1 与 common/life_product_profile.py 的指纹（只在名单里有六维产品画像时，旧协议不变），开发期（所有者 2026-10-18）只记录、不拦截续跑与重放；资料编译要求每个用到的已知字段有记录这个字段、单位对得上的来源事实，单个数值有事实支持，缺来源、缺计价单位、非法数值逐候选拒绝，覆盖条目分开写供给、资料与编译状态；金额只接受不分组十进制或严格千分位；重复或冲突的健康题、资格与同一 E1 需求的硬要求在确认前拒绝；公开错误（分身说明、批次暂停、预检查拒绝）只由 product.public_errors 按类别生成，不透传异常原文，原文留在运行归档或编译目录的 diagnostics/。
- 最低住房与最低食品由城市数据声明（CityLifeCatalog.minimum_living），不从城市名、标签或 LLM 推断。缺失、不完整，或最低方案比目录里已知可得的同类合格方案更贵（按数量 1、同一日历月、同一套月度计费换算比较），都是数据错误，不是分身出局；出局分身保留记录、不复活、不从分母删除（这里的分母指原计划人数与出局比例；城市平均分怎样处理出局者以 DECISIONS/0028 为准：城市平均排除已确认的生活出局者，原计划人数、出局比例与原因的报告保留全部分身，模拟问题造成的未完成计算另作不确定性分析）；终态编号由协议的 N 推导为 N + 1。
- 生活结算的税只接受显式的合成测试替身（LifeTaxBinding，状态 synthetic_test_double）；没有配置不等于零税。旧税表与生活协议的计价口径尚未核查、旧接口缺少相应验证，未经 V2 前税表核查不得把 worlds.taxes 等现有税表接到 _LOCAL 金额上，也不得做隐式货币转换或改标签。
- 硬规则 1 的实质不变：答案侧对象不进任何 prompt。旧静态协议的适配器仍只接受 Observable；生活协议的可见出口是 agentlayer.life_views.AgentObservation，只能由显式许可逐字段构造，不得整体序列化 PersonYearState、EnvironmentOutcome 或旧 Persona。
- 硬规则 9 管的是旧 Cobb-Douglas 支出份额。生活模型里偏好是人的稳定属性，实际消费安排是行动、可以因城而异，二者不冲突；不得用支出份额代替生活模型的偏好。
- 硬规则 12 继续适用。生活模型的当地报价口径是 <币种>_<基年>_LOCAL（当地价格水平、基年不变价），不得用空间平减的 USD_2024_US100 装原始标价；价格指数不得当付款单价。
- 硬规则 3 不变：src/econ/life_settlement.py（E1-2，DECISIONS/0017）、src/econ/life_scoring.py（E1-3，DECISIONS/0018；增补 DECISIONS/0019）、src/econ/life_coverage.py（E1-3 增补，DECISIONS/0019）、src/econ/life_resources.py（E1-4A，DECISIONS/0020；同轮修改 life_settlement.py 的资源检查）、src/econ/life_feedback.py 与 src/econ/life_year.py（E1-4B，DECISIONS/0021；同轮修改 life_settlement.py 的下一年候选状态），以及 src/econ/life_career.py（E1-5A，DECISIONS/0023；同轮修改 life_settlement.py 的培训完成记录、年初资格判断与年末职业事实）是经授权新增或修改的模块；E1-5A 补充（DECISIONS/0024）经授权修改 life_scoring.py（成长项与广义交通）与 life_coverage.py（覆盖同步）；E1-5A 收尾（DECISIONS/0025）经授权修改 life_year.py、life_feedback.py 与 life_settlement.py（评分内容身份的核对与传递，公式不变）；E1-5B（DECISIONS/0026）经授权新增 life_savings.py（目标的年初指令、月末进展与兑现记录，不动钱）并修改 life_settlement.py（三处调用与写回）；E1-5C（DECISIONS/0027）经授权新增 life_health.py（随机键、治疗决定、病程转移与逐月健康记录的纯函数与记录器）并修改 life_settlement.py（年初无事件试算、月度试算与提交边界、病假、治疗、暂停、停止）、life_resources.py（恢复与就医经 extra 进入同一资源算法、停止月）、life_career.py（病假月份与停止年的岗位记录）、life_savings.py（停止月）、life_scoring.py 与 life_coverage.py（六维换算与健康项）、life_feedback.py 与 life_year.py（停止的交接、画像含义核对）；E1-5C 收尾修订（DECISIONS/0027 第十五节）经授权修改 life_settlement.py（生病月份取消声明的购买、目标接线）、life_health.py（年度记录记下声明）、life_savings.py（未兑现原因写已取消）与 life_scoring.py（只改注释）；E1-6A（DECISIONS/0028）经授权新增 life_evaluation.py（轨迹核对、逐月加权汇总、合成分）与 life_terminal_value.py（估值上下文、参考方案适配与选择），并修改 life_year.py（把封存的年度绑定挂在年度结果的私有属性上；把反馈记录 → 交接的映射提取成 run_life_year 与轨迹核对共用的 handoff_from，原逻辑不变）；E1-6A 收尾与 E1-6B（DECISIONS/0029）经授权修改 life_evaluation.py（外层身份核对、逐月事实摘要、运行入口的配置检查）、life_terminal_value.py（基础生活模板 × 治疗分支、条件购买先看持有、按城配置检查、只在带疾病规则的协议写应急安排）与 life_year.py（公开税绑定与评分输入的身份函数，原算法不变），并新增 life_aggregation.py（名单冻结、结果核对、分类、均值、范围、比较、事件链与评价对照的纯函数，不含生活数学）；E1-6B 最小收尾（DECISIONS/0029 第八节）经授权修改 life_aggregation.py（冻结写入检查身份与硬要求清单、硬要求核验人数、敏感性证据配对）与 life_terminal_value.py（估值不完整理由的一句文字，数值不变）；E1-7A（DECISIONS/0030）经授权修改 life_settlement.py（把结算前检查提取成 _screen 并新增执行前检查的公共入口 check_year_plan，settle_year 的行为逐字节不变）、life_evaluation.py（evaluate_clone 接受规划停止）与 life_aggregation.py（规划停止的原因映射，分类与公式不变）；E1 补充日常生活便利（DECISIONS/0033）经授权修改 life_scoring.py（便利分项与输入核对）与 life_coverage.py（覆盖同步）；E3-A（DECISIONS/0034）经授权窄范围修改 life_scoring.py（六维偏好转换与有效权重分支）、life_year.py（偏好来源 → 输入类型的显式分派）与 life_aggregation.py（取画像的兼容入口），数学不变，同样属于 src/econ/，后续修改仍须先说明改动与数学含义并取得授权。不得把旧间接效用直接当成任意生活行动的评分器。不得把经济公式写进 common、ledger 或 agentlayer 来绕开审查。

---

## 硬规则

1. **绝不把 `GroundTruth` 送进任何 prompt。** 代理层适配器的签名只接受 `Observable`。`AgentInput` 构造时递归遍历自身并拒绝任何答案侧对象，包括埋在 dict 与 list 里的。
2. **绝不提交任何密钥。** `.env` 在 `.gitignore` 里，仓库只放 `.env.example`。
3. **未经询问不得修改 `src/econ/`。** 它是真值的唯一来源，改动会让此前跑过的实验作废。要改先说明理由并等确认。因此凡是会迭代几轮的东西（例如住房质量调整）放 `src/ledger/`，不放 `src/econ/`。
4. **每个原始数据列在效用计算图里只能被引用一次**（§7.4）。记账数的是"项"不是"读取次数"。
5. **语言模型不生成数值。** 数据转化只做模式映射，最终表里每个值必须可溯源。
6. **模型版本用带日期戳的快照，不用滚动别名。** 版本字符串写进每一条日志。（适用范围说明，2026-10-13，DECISIONS/0032：唯一的例外是产品生活规划器角色 product_planner——供应商没有提供固定快照时，可以在产品配置里显式声明 version_policy: rolling 并写明理由，同时记录每次实际返回的型号与时间；研究角色 base_a / base_b 与研究实验不受影响，仍然只用带日期的快照。2026-10-18，DECISIONS/0035：测试规划器角色 test_planner 同样可以显式声明滚动型号——Claude Haiku 5.5 只发布 claude-haiku-5-5 这一个型号名。）
7. **不写记忆流、不写多轮对话。** 每代理每年常数次调用。（适用范围说明，2026-10-12 E1-7A，DECISIONS/0030：生活协议的分身每年一次正常规划、最多两次修订，每次调用都是一份完整的单轮安全请求，不带对话历史；年度事实记忆是有长度上限的结构化记录，不是记忆流。旧静态研究实验的条件不变。2026-10-18，DECISIONS/0035：经 Claude Agent SDK 调用时同样是每次一个输入、一轮、一个输出——max_turns 1、无工具、不恢复会话、不带对话历史。）
8. **M2 必须对国家无知（country-blind）。** 纯函数 `indirect_utility(y_net, p, a, params)` 只吃标准化量，不接触任何原始列、不知道城市、不知道国家。若 `src/econ/` 里出现 `if country == ...`，说明上游抽象漏了，修上游不修 M2。验收标准是 `FAKE_COUNTRY` fixture 跑通时 M2 一行不改。
9. **支出份额 `s` 是 `Persona` 的字段，不是 `(Persona, City)` 的函数。** 装配层不得持有 CEX provider 的引用，`_price_index` 只读 `persona.shares`。这条**通过删除依赖注入来保证，不是通过加断言**。理由见 §5.2.2：按城市查表会同时毁掉价格指数的定义、`κ` 的跨城不变性、以及条件 logit 的正确设定，而这三条在排序测试里都发现不了。
10. **`UtilityGraph` 的 `g` 参数只穿装配层**（`_income` / `_price_index` / `_amenities`），不进纯函数。因为原始列只在装配层被读取。
11. **价格指数全程在对数空间。** `_price_index` 返回 `ln_p` 而非 `p`，并带断言 `abs(sum(s) − 1) < tol`。这条断言不可移除：份额不和为一，`P` 就不是价格的一次齐次函数，它就不是价格指数。
12. **所有货币量带 `numeraire_id`**（如 `"USD_2024_US100"`）。跨 numeraire 的福利水平比较是**类型错误**；无量纲量（回收误差、坍缩比例）永远放行。全程基年不变价，三个增长率一律取实际增长率。
13. **福利损失永远在真值偏好参数下评价，近似参数只用于 argmax。** 若用近似参数同时做选择和评价，算出的是自洽的胡话而不是损失。这条适用于 `L_rep`、`L_s`、`L_amen`、`L_llm` 全部四项（§9.1.1）。
14. **w 空间不存在对角协方差。** `Σw = 1` ⟹ `1'Σ1 = 0`，而非零对角阵的 `1'Σ1 > 0`。混合 logit 一律按 §11.7.1.1 的差分法在 γ 空间估计（6 个随机系数对角协方差，`γ_0` 固定），逐维压缩作为派生量从变换回来的 7×7 秩 6 矩阵取对角线。基准维的选择是自由度，必须跑多基准维稳健性。
15. **打乱人设臂是完全坍缩的天花板，不是零假设。** 在 `proj_i` 与美元福利损失两条曲线上都是天花板，匹配臂应当低于它。地板来自 §10.3 的收缩自检。**不要把打乱臂用作协方差比的天花板**（打乱不改变人设总体，两臂的总体协方差应当接近）。
16. **坍缩的主证据是协方差比，不是 `proj`。** `proj` 只能部分识别：地板需要知道估计器看到的 `Σ_pop`，而坍缩就是压缩 `Σ_pop`，循环打不开。因此它报 `r(t)` 曲线而非点估计，且只作"往哪压"的方向证据。协方差比识别干净，因为两端都是已知输入——Dirichlet(α=0.5) 的协方差有闭式解（§1.2.2、§1.2.3、§11.7.3）。
17. **`proj` 与协方差比机械耦合，不得作为互相印证的证据。** Σ 被压小会加剧收缩从而抬高 `proj`，后者部分重复计入前者。结果部分必须自己写明这一条。
18. **坍缩比例扣地板，美元福利损失不扣任何东西。** 地板是估计器的性质，而估计器不在产品里。两个口径有意不同：代码里不共用"扣地板"的开关，报告里不合并成一句话（§1.2.4）。
19. **地板臂的 σ 从重复子集标定，绝不从匹配臂的 `σ̂ = 1/γ_0` 读。** 后者混入设定误差，会把地板变成处理变量的函数。标定关系是校准启发式而非恒等式，注释里要写明（§10.3.1）。
20. **不得实现"看结论选端点"的条件规则。** `r(t)` 的判据对两个方向对称：两个声明都要求整条曲线支持，跨零就报"不可判定"。阈值写进 `configs/` 并在跑之前 commit（§1.2.2）。
21. **人设抽样分层，分层变量是 `z_i = ‖w_i − μ₀‖`，`μ₀` 是注入均值，不是 `d_i`。** 落点 `src/eval/sampling/`（P10）。`μ₀ = worlds.personas.dirichlet_mean()` = `[1/3, 1/9×6]`，**必须显式传**——`eval.sampling.stratification_variable` 没有默认中心，因为默认 `1/7` 会给所有人的距离加一个固定偏移，层整体歪掉而代码照常跑完（`DECISIONS/0006`）。**`z`（抽样变量，事前）与 `d`（分析变量，用拟合出的中心）永远不互换**，报告里要写明用的是哪一个。层标签在选择数据产生之前冻结，**不得按拟合后的 `d` 或结果重抽人**。层边界与层概率来自独立参照总体并保存生成依据，不从本轮结果调箱。人口层面与条件层面的报告做成两个显式函数，不用 `weighted` 参数（§11.9.1、§11.9.2）；**跨层回归两种口径都讲得通，那里必须显式选**，不能拿"条件量不用加权"一路推广过去。
22. **比值估计量的置信区间一律用 block bootstrap**（重抽人设，不重抽个体内的选择），200–500 次。差分法分支下 `ŵ_i = γ_k/γ_0` 在 `γ_0 → 0` 时矩不存在；**ALR 主规格下不会发散**——`w = softmax(a)` 逐分量有界于 (0,1)，信息不足的表现是个体后验**收缩回先验**（`DECISIONS/0014`）。bootstrap 的理由不受影响：收缩同样非线性，delta 方法同样要多传播 `w_b = 1 − Σw_k` 那一层。**门二的 0.8 是预注册的设计阈值，不是从矩发散推出的定理**（§10.3.3 已订正）。`SE(W)` 取**同一 replicate 内** `ŝ_shuffled − λ̂` 的变化，**不得**按独立相加（实测可差 2.56 倍）；有效 replicate 不足是 `pending_computation`，与缺数据的 `pending_input` 分开，两者都不输出 PASS（`DECISIONS/0015`）。
23. **坍缩比例的分母用打乱臂的实测斜率，不用理论值 1。** `r = (ŝ_matched − λ̂)/(ŝ_shuffled − λ̂)`。理论值 1 未被衰减而两个实测值被衰减，量纲不一致，旧公式在中等测量误差下低估坍缩四成（已数值验证）。理论值 1 降为诊断：`ŝ_shuffled` 就是衰减因子 `b` 的估计，也是 `d` 信噪比的度量。见 `DECISIONS/0002`。
24. **`d_i` 与 `u_i` 只算一次，用匹配臂的 `ŵ_center`，三个臂原样共用。** 这不是便利条件，是硬规则 23 能成立的唯一原因：偏误因子 `b = 1 − Cov(⟨δ,u⟩,d)/Var(d)` 只依赖 δ、`u`、`d`，三者不随臂变化时它才是三臂共用的乘性因子，才消得掉。各臂各估各的中心，`b` 就不同，消不掉。语义上也更对：问的是"匹配臂是否比理想估计器更把人往**匹配臂的那个中心**推"，方向由被研究对象定义一次。见 `DECISIONS/0002`。
25. **地板臂的 σ 用分歧率二分标定，且跑在重复子集自己那批 scenario 上。** 目标量是分歧率（重复对里选出不同城市的比例），不是"一致性"——方向写反了代码照常跑，只是 λ 系统性偏到另一边。分歧率对 σ 单调递增，二分即可，但它**依赖选择集**（备选越接近，同样的 σ 给出越高的分歧率），所以必须在同一批 (世界, 人设) 交叉点上标定，不能另找一批世界。上界是 `1 − 1/n` 不是 1：实测分歧率超过它则无解，那是装置有问题的信号（重复对里混进了不同条件）。见 `DECISIONS/0003`。
27. **集合层面福利量的规范形式是 ln V，美元是派生量。** 存 `ln_welfare_gap` / `ln_welfare_dispersion` / `ln_welfare_range`；美元版是 property，带 `numeraire_id`，落在语义区间外返回 `None`。**分箱、分组、按难度排序一律用 ln 版**——美元版在区间外是 `None`，用它分箱会悄悄削掉一块非随机样本。归一化福利损失在 ln V 空间算，因此与 `w₁` 无关（分子分母各含一个 1/w₁，约掉）。美元只用于对用户报数，本国范围内，附缺失说明。货币度量的语义区间**两侧都要守**：`1/w₁` 在指数上，低 w₁ 时 `ln e` 往哪边跑取决于 `(ln V − ln κ)` 的符号，只守下侧会在正侧抛 OverflowError（`DECISIONS/0004`）。
26. **泄漏扫描的单值容差与向量指纹容差必须分开。** 单值 `rtol = 1e-3`（一个数匹配是弱证据，容差必须紧），指纹 `rtol = 4e-2`（七个数同时匹配且构成真值多重集，即使每个只匹配到百分之二也是压倒性证据）。统一成 1e-3 会漏掉**整数百分数**形态的倾倒，而 §6.3 说人会给 10、20、30，那正是最可能的泄漏形态：权重 0.1768 整数化成 "18" 的相对误差是 1.8e-2，比 1e-3 大一个量级。见 `DECISIONS/0001`。

---

## 技术栈

```
Python 3.11+
uv                依赖与虚拟环境管理
pandas            M1 数据抓取与清洗（读 BLS 的 XLSX 用它）
polars            下游分析可选，parquet 作交换格式
numpy / scipy     M2 福利模型、M5 估计
pydantic          配置与结构化输出校验
pyyaml            实验配置
httpx             HTTP
tenacity          重试与指数退避
python-dotenv     本地密钥加载
openai            调用 DashScope 的 OpenAI 兼容端点
claude-agent-sdk  真实 LLM 测试调用 Claude Haiku 5.5（单轮、无工具，DECISIONS/0035）
pytest            测试
ruff              格式与静态检查
matplotlib        出图
```

估计层：条件 logit **先手写似然用 `scipy.optimize.minimize` 求解**，再用 `xlogit` 或 `pylogit` 交叉验证。**混合 logit（随机参数 logit）用 `xlogit`，它对 M5 是核心依赖。**

**`xlogit` 的用法受硬规则 14 约束**：设计矩阵是 7 列（一列基准维原值 `x_7` 作**固定**系数，六列差分 `x_k − x_7` 作**随机**系数、对角协方差），不是七列原值。反解 `σ = 1/γ_0`、`w_k = γ_k/γ_0`、`w_7 = 1 − Σw_k`。个体 `ŵ_i` 从个体后验均值同法反解。

---

## 目录结构

```
homo-spatialis/
├── README.md                       设计文档（活文档，人读）
├── RELATED_WORK.md                 文献库（人读）
├── DECISIONS/                      编号决策记录（编码代理读）
├── CLAUDE.md                       本文件
├── PLAN.md                         实施计划与补丁清单
├── MIGRATION_v0.5_to_v0.9.md       一次性迁移动作（打完补丁后归档）
├── pyproject.toml
├── Makefile
├── .env.example
├── src/
│   ├── data/              M1
│   ├── ledger/            M1.5  字段台账、参照总体、分位表、质量调整价
│   ├── econ/              M2    福利模型（真值，改动需询问）
│   ├── worlds/            M3    合成世界与人设生成
│   ├── agentlayer/        M4    语言模型接口（不叫 agents：PyPI 的 openai-agents 装成顶层 agents，会静默劫持）
│   ├── eval/              M5    评估
│   ├── ui/                M6    问卷与结果展示
│   └── common/            leakcheck 等
├── tests/
├── configs/
├── scripts/
├── data/{raw,processed,frozen}/
├── runs/
└── notebooks/
```

---

## 命令约定

```
make setup      uv sync
make test       pytest
make lint       ruff check && ruff format --check
make data       抓取并清洗（幂等）
make freeze     构建并冻结参照总体与分位表
make gates      **目前只跑门一**（L_rep 判据）。门二/门三要等真实重复子集与匹配/打乱臂，
                接口在 eval/floors/gates.py，入口未接。零大模型调用
make demo       跑一个最小的端到端示例
```

---

## 数据获取决策

- **OEWS 走批量下载**，不走 API。
- **BEA 走 API**（`BEA_API_KEY`）。
- **处理后的 parquet 直接提交进仓库**，不引入 git-lfs 或 DVC。`data/raw` 加 gitignore。
- **分位表与参照总体一旦冻结就不再变**，文件名带 `reference_id`。绝不在查询时现算分位。
- **RPP 在 ledger 层归一到全美等于 1**（原发布口径是全美等于 100），让 `ln P` 落在 0 附近。
- **OECD BLI 用户权重数据是对照臂用的，不是主实验依赖。** 拿不到不阻塞任何模块。

---

## 语言模型配置

**两条完全独立的凭证路径：**

**路径一，Claude Code 自己用的**：客户端层面配置，与项目代码无关。

**路径二，项目代码在运行时调用的被测基座**：走 DashScope 的 OpenAI 兼容端点。

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
```

**两个基座**（§10.4 要求不同家族、不同来源国）：`BASE_MODEL_A` 为前沿模型，`BASE_MODEL_B` 为 DashScope 的 Qwen，均取带日期后缀的快照版本，写进 `configs/models.yaml`。产品生活规划器另有独立角色 `product_planner`（2026-10-13 起是 `qwen3.8-flash`，滚动型号，见 DECISIONS/0032），不是研究基座，换它不影响 base_a / base_b。

**统一适配器**：两个基座通过同一个 OpenAI 兼容客户端调用，只换 `base_url`、`api_key`、`model`。不要为不同厂商写两套代码路径，否则无法保证两个基座收到同一个 prompt。
（适用范围说明，2026-10-18，DECISIONS/0035：这条约束两个研究基座。生活规划器的测试角色 `test_planner`（Claude Haiku 5.5）经 Claude Agent SDK 调用，是第二条传输路径；它与千问共用同一个规划器 `LlmPlanner`、同一份渲染好的提示文字与同一套记录与核对，厂商差异只在 `agentlayer.life_llm` 的传输层里。密钥是项目 `.env` 里的 `ANTHROPIC_API_KEY`，与上面的路径一（Claude Code 自己的凭证）无关，传输层不继承宿主的端点与登录变量。命令行另加的提示内容与强制思考使它与千问不是同一个信息条件。）

**调用规范**：结构化输出用 JSON schema 加 `pydantic` 二次校验；并发用 `asyncio` 加 `Semaphore`；重试用 `tenacity` 指数退避默认三次；每次调用写一行 JSONL 到 `runs/<run_id>/calls.jsonl`，含请求、响应、耗时、token 数、seed、模型版本。

---

## 参数与假设登记（2026-10-08 起）

按 README 文件分工与 DECISIONS/0022，开工先查 docs/design/parameter-assumption-register.md 中相关 ID。新增或改变环境数值/规则、资料到模型的映射、评价方法、代理信息或认知假设时，同一轮必须更新该目录：字段、单位/范围、实际值或未定状态、版本、依据、可见性、影响与验证位置。运行配置仍是数值的实际来源；不能把文档候选写成已有 API，不能为填表发明默认值。收尾报告列出新增/变更 ID 和未标定项，实际实验保存配置与代码/数据版本。

区分合成设定、观测资料、派生量、用户陈述、估计输出与数值计算控制。单位换算、资金守恒、信息隔离和真实制度值不能为了提高得分被优化掉。世界规则与代理风险知识分开：E1-5 默认不主动披露模拟器设定的疾病—工作负担关系（包括方向、概率表与系数）；定性提示须显式列为信息条件，不撤回公开时间/负担成本，也不预告随机事件。隐藏信息不能保证模型无相关常识。

E1-5 用少量岗位相关资格或技能，学习须占明确额外时间，不能通用证书自动涨薪。储蓄通过未来消费和应急发挥作用，不加独立“安心分”。V1 不追求最大真实性。E1-6 的完成者偏差处理等待所有者提出方案，当前保留原计划人数与停止分类，不擅自补零、删人或改分母（2026-10-11 所有者已确认口径，见 DECISIONS/0028 第二节：城市平均是完成且评分完整的分身平均，确认的生活出局另报比例，模拟问题另作不确定性范围；计算在 E1-6B；2026-10-12 已按这一口径实现，所有者决定不设最低通过人数或缺失比例门槛，见 DECISIONS/0029）。参数学习/模拟校准属于后续研究，不用当前被测模型的高分证明环境规则正确。

这些是本轮文档约定，不授权修改 src/econ/ 或启动 E1-5；实现仍按对应 prompt 的范围与许可。

## 工作方式

- 开发期按普通软件项目做（所有者 2026-10-18）：每次改动跑自动化测试与 lint（make test、make lint）就是验收。默认不再做研究期的流程：旧版本逐字节比较、历史源码快照、每轮变异实验、每轮证据目录与 prompt 归档、为保住旧路径另起版本号、把旧归档逐字节钉住的测试。改了代码，旧运行仍可续跑与重放（源码指纹照记、不拦截；配置、规则、事实等内容身份照旧核对）。研究定稿时冻结一个版本，再统一补做研究级核验。
- 开发期照旧保留：安全检查（不提交密钥、日志不写密钥，答案侧对象不进提示，Agent SDK 的隔离环境）、付费或消耗额度的真实调用要所有者同意、改 src/econ/ 先说明（硬规则 3）、下面全部硬规则。
- git 是历史与安全网，提交与推送由所有者自己做；编码代理不 git add、不提交、不推送，保留别的窗口未提交的改动。
- **任务粒度要小。**
- **M2 与 M5 的核心数学由人类先写第一版**，Claude Code 负责 review 与补测试。M0、M1、M4 的工程部分可以放手。
- **一次只处理一份 `DECISIONS/` 记录。**
- **门一或门二不过，停下来报告，不要自行放宽阈值继续。门三显著则改实现 `r(d)` 版本。**

---

## 变更记录

**v4（对应 README v0.9）**

- "两个停止门"扩为"三个门"，新增门三（λ 的 `d` 依赖，决定 M5 实现哪套公式）；写明门二、门三共用重复子集，因此重复子集要先于主实验跑
- 新增硬规则 16 到 22：协方差比为主证据、两指标机械耦合、两个口径不得合并、地板 σ 由重复子集标定、禁止条件端点规则、分层抽样与两个报告函数、block bootstrap
- 裁决表新增六行
- 阻塞项加 P10
- 迁移文档更名为 `MIGRATION_v0.5_to_v0.9.md`

**v3（对应 README v0.8）**

- 新增"两个停止门"一节：`L_rep` 判据与 `λ > 0.8` 判据，均在 M4 之前判定
- 新增硬规则 11（对数空间与份额断言）、12（numeraire 与实际增长率）、13（损失在真值偏好下评价）、14（w 空间无对角协方差，差分法估计）、15（打乱臂是天花板不是零假设）
- 硬规则 3 补上"会迭代的东西放 ledger 层"
- 硬规则 9 补上"通过删除依赖注入保证，不是加断言"
- 裁决表新增四行；**撤销 v2 的"若 OECD BLI 可获取则主实验改用该分布"**，Dirichlet(α=0.5) 定为已裁决
- 技术栈补 `xlogit` 的设计矩阵约束
- 目录树补 `MIGRATION_v0.5_to_v0.8.md`；命令补 `make gates`
- 文首补"不变量 vs 一次性动作"的划分原则

**v2（对应 README v0.6）**

- 进度从"设计阶段"更新为 M0–M3 完成
- 新增硬规则 8（M2 对国家无知）、9（`s` 是 `Persona` 字段）、10（`g` 只穿装配层）
- 裁决表扩充：间接效用、住房方案 B、四条断言、享乐适应撤回、跨年计价
- 技术栈补 `xlogit` 作为混合 logit 的核心依赖
- 目录树补 `RELATED_WORK.md`、`DECISIONS/`、`src/common/`
- 数据决策补 RPP 归一化
- 仓库名由 `spatial-welfare-agents` 改为 `homo-spatialis`
