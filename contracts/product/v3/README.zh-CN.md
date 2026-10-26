# 产品共享结构

[English](README.md) · 简体中文

这里保存当前消息结构、公共样例和服务 API。目录名沿用 `v3`，确切版本见 [`index.json`](index.json)：当前为 `e3a-product-schema-v3.5`、问卷 v0.5、词表 v5、转换 v4，服务 API 为 v1.4。现有 35 份消息 schema 和 173 份公共样例。

在仓库根目录运行 `uv run --frozen python scripts/product_e3a.py check-schema` 和 `uv run --frozen python scripts/product_service.py check-openapi` 核对导出物。schema 和 OpenAPI 由代码生成，不要手改；修改权威定义后，分别用 `scripts/product_e3a.py export-schema` 和 `scripts/product_service.py openapi` 导出。前端类型另行生成，步骤见[开发指南](../../../docs/DEVELOPMENT.md#接口与生成文件)。

样例人物都是合成的，模拟使用离线 mock 或明确标出的测试传输，凭证是无效测试值。样例覆盖画像确认、预检查、生活记录、评价、会话、草稿、运行设置、开始与停止、删除和进度。它们展示消息与业务行为，不代表真实模型表现。早期样例的说明可能保留添加时的用语，当前接口以 `index.json` 和 schema 为准。

进一步阅读：[共享约定](../../../docs/design/e3-e5-product-contract.md)、[用户输入](../../../docs/design/e3a-product-input.md)、[生活扩展](../../../docs/design/e1-8-life-extension.md)、[六城资料包](../../../docs/design/e3b-life-pack.md)、[网页启动](../../../web/README.zh-CN.md)、[服务说明](../../../docs/design/e3c-service.md)。当前实现进度见[项目索引](../../../docs/INDEX.md#status)。

<a id="earlier-changes"></a>
## 早期变更记录

以下保留 v3 至 v3.3 的原始说明，其中的版本、样例数量和界面进度指当时状态。

v3 取代 [v2](../v2/README.md)（v2 目录原样保留为历史导出，界面层改读 v3 之前仍在用）。全部样例的人物都是合成的；01–50 用合成城市，51–55 用冻结的 E3B 六城真实资料包（`real_data_frozen`，[ADR0042](../../../DECISIONS/0042-e3b-real-life-pack.md)）；离线规划器是 mock，不是真实大语言模型。规则见 [ADR0041](../../../DECISIONS/0041-life-extension-food-enjoyment-hobbies.md)，技术说明见 [生活扩展](../../../docs/design/e1-8-life-extension.md)，给界面层的改动清单见 [交接](../../../docs/handoffs/e3-to-e5-life-extension.md)。

## 1. 这里有什么

* `*.schema.json`：28 份消息的 JSON Schema（v3.2 起含托管服务的 14 份），由唯一权威结构 [src/common/product_contracts.py](../../../src/common/product_contracts.py) 导出，**不要手改**。
* `index.json`：结构版本、问卷 / 词表 / 健康问卷 / 转换版本，以及每份 schema 的 SHA-256。
* `samples/`：101 份公共样例，每份是 `{"message", "description_zh", "payload"}`，`payload` 按对应 schema 校验。
* `openapi.json`：托管服务（E3C）的 HTTP 接口，由 `uv run python scripts/product_service.py openapi` 导出，**不要手改**；`uv run python scripts/product_service.py check-openapi` 核对它与实际应用生成的逐字段相同。

```bash
uv run python scripts/product_e3a.py check-schema
```

## 2. 版本

| 项 | v2 | v3 | v3.1（E3B） | v3.2（E3C） | v3.3（E3B 映射修正、E3C 收尾，本目录现在的版本） |
|---|---|---|---|---|---|
| 结构 | `e3a-product-schema-v2` | `e3a-product-schema-v3` | `e3a-product-schema-v3.1` | `e3a-product-schema-v3.2` | `e3a-product-schema-v3.3` |
| 问卷 | `e3a-questionnaire-v0.1-draft` | `e3a-questionnaire-v0.2-draft` | 同 v3 | 同 v3 | `e3a-questionnaire-v0.3-draft` |
| 词表 | `e3a-vocabulary-v1`（物质三项） | `e3a-vocabulary-v2`（物质四项） | `e3a-vocabulary-v3`（居住条件取代居住空间） | 同 v3.1 | `e3a-vocabulary-v4` |
| 资料包 | `pack.e3a.synthetic-v1` | `pack.e3a.synthetic-v2` | 同 v3，另加 `pack.e3b.us-v1`（`real_data_frozen`） | 同 v3.1 | 同 v3.1（E3B 资料包重新冻结） |

## 3. 新样例

| 样例 | 消息 | 演示 |
|---|---|---|
| 39–43 | ProfileReview | 兴趣与口味：确认（含没认出与有歧义的片段、品牌只留存）、未答、通用评价（只写了本版不支持的“喜欢冲浪”，原文留存）、没认出、词表外 |
| 44–47 | YearFacts | 买到并当月练琴、买了没练、生病取消与暂停 + 网球套餐 + 没有吉他练不成、年初旧吉他到期 |
| 48 | LifeItemCompilation | 生活扩展候选的编译检查（v2）：可执行、拒绝、不可得、本版未支持、资料未知 |
| 49 | ProfileReview | 同一兴趣选了两次：审阅阶段拒绝 |
| 50 | ProductResult | 期末估值的范围说明：吉他按能力继续用，网球拍的用途参考模板没有考虑 |

v3 在独立复核之前按收尾修复（ADR0041 收尾一节）修订过一次；界面层还没有读 v3，所以没有另开 v4。

## 4. v3.1：E3B 真实资料包的公开事实

只加了可选字段，v3 的字段含义不变；空的新字段不序列化，所以合成样例只多出真实存在的事实（住处与片区、通勤、税、资料性质）。给界面层的改动清单见 [E3B 交接](../../../docs/handoffs/e3b-to-e5-real-pack.md)。

| 样例 | 消息 | 演示 |
|---|---|---|
| 51 | ProductPreflight | 真实资料包的预检查（休斯敦、西雅图）：逐城的资料性质 `data_nature` 与结构化范围说明 `scope_notes`（含 `topic = data` 的治安资料逐城覆盖） |
| 52 | ProductResult | 真实资料包的结果：每年的住处与片区、逐月通勤、用车权、按哪份官方税规则预扣；逐城资料性质 |
| 53 | ProductPreflight | 旧金山的通勤关系表没有公共交通：警告 `commute_mode_missing` 与范围说明，预检查仍通过 |
| 54 | ProductPreflight | 没有确认高中学历：开局岗位要求的资格不从职称或自由文字授予，预检查阻断 |
| 55 | ProductResult | 旧金山（没有公交资料）：一个分身步行骑车上班；另一个分身两年买用车权开车上班（月费不含油费、油费按次），两个都完整 |

## 5. v3.2：托管服务（E3C）

只加了消息与可选字段，v3.1 的字段含义不变（旧样例只是重新盖了版本号，内容身份随之更新）。规则见 [ADR0043](../../../DECISIONS/0043-e3c-hosted-service.md)，接线见 [E3C 交接](../../../docs/handoffs/e3c-to-e5-service.md)。

* 新消息：`SessionExchangeRequest`、`SessionView`、`DraftView`、`DraftSaveRequest`、`ReviewReply`、`ProfileConfirmRequest`、`ProfileConfirmReply`、`RunIntentCreateRequest`、`RunIntentView`、`RunSubmitRequest`、`RunSubmitReply`、`RunList`、`DeletionReceipt`、`ServiceError`。
* 已有消息的变化：`RunReceipt.service` 增加 `hosted_service`；`CostStatus` 增加可选的币种、报价版本、上限、已核算 / 用量不确定 / 在途预留金额与服务尝试计数（mock 时不出现）；`RunProgress.updated_at`（可选）。
* 样例 60–96 由服务端的真实代码路径产生（测试时钟、测试秘密、合成人物、离线 mock；76–77 是**测试传输**，不是真实模型；凭证都是明确无效的测试值）：

| 样例 | 演示 |
|---|---|
| 60–61 | 专属链接换会话；会话视图（次数、规则、草稿与未结束的意图） |
| 62–68 | 新建草稿、带版本保存、旧版本冲突、审阅、确认请求与回复 |
| 69–75 | 准备模拟（排队检查）、等确认、确认开始、重复提交、键冲突 |
| 76–80 | 运行中（测试传输）、结束、进度、我的运行 |
| 81–85 | 阻断、暂停（可恢复）、部分结果、次数用完 |
| 86–91 | 删除回执、墓碑、已删除、删除全部、过期 |
| 92–96 | 未登录、CSRF、限流、付费未开、预检查过时 |

## 6. v3.3：E3B 映射修正与 E3C 收尾

v3.3 由 E3B 映射修正引入（问卷、词表、日常支持与抽象消费篮子，见 [E3B 映射修正 → E5](../../../docs/handoffs/e3b-mapping-to-e5.md)）。E3C 收尾在同一版本里加了托管服务的运行选项——只加可选字段；E5 还没有接服务，所以没有另开版本号。规则见 [ADR0043](../../../DECISIONS/0043-e3c-hosted-service.md) 收尾一节，给界面层的改动清单见 [E3C 交接](../../../docs/handoffs/e3c-to-e5-service.md) 第 11 节：

* `RunIntentCreateRequest` 增加可选的 `clones_per_city`、`years`、`max_cost_cny`（元，字符串，最多到分）；
* `ServiceRules.options`（用户能选的范围：`RunOptionsPolicy`）与 `ServiceRules.profile_retention_days`；`RunIntentView.settings`、`RunSummary.settings`（这次保存的有效设置：`RunSettingsView`）；`ServiceError.field`（只在 `run_options_invalid` 时出现）；
* 错误码 `run_scale_fixed` 换成 `run_options_invalid` 与 `run_settings_unavailable`。

| 样例 | 演示 |
|---|---|
| 97–99 | 较小规模的请求（1 座城、每城 2 个分身、5 元）、保存的有效设置、选项越界 |
| 100–101 | 停下且不能由用户恢复（**测试传输**：同一份规划的服务尝试已经用完；按钮与进度 `resumable = false` 一致） |
| 102 | 预算停下（测试传输，这次费用上限 0.1 元：下一次预留会超过上限，停在发送之前） |
| 103 | 开始之后的失败（受控夹具：暂停期间快照被改动、恢复被拒绝；说明写“已经用掉 1 次机会”，没有“取消”） |
| 104 | 最新六城资料（冻结的 E3B 真实资料包，说明里写着内容身份与构建身份）经托管服务路径的结果：六城各 1 个分身、3 年、离线 mock |

服务样例 60–104 在只含 HEAD 与 E3C 改动的干净工作树里由服务端真实代码路径重新导出（测试时钟、测试秘密、合成人物、离线 mock 或测试传输；凭证都是明确无效的测试值）。
