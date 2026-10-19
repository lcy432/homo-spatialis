# 产品共享结构 v2（E3-A 收尾）

**状态：E3-A 收尾修复（独立审核 F1–F5）已实现、离线自测通过，待独立复核（2026-10-18）。** v2 取代 [v1](../v1/README.md)（v1 目录原样保留为历史导出，
不要再按它开发）。全部样例都是合成人物与合成城市；离线规划器是 mock，不是真实大语言模型。规则见
[ADR0034](../../../DECISIONS/0034-e3a-product-input-contract.md)（第九节是收尾），技术说明见 [docs/design/e3a-product-input.md](../../../docs/design/e3a-product-input.md)，
共享语义见 [E3/E5 共享约定](../../../docs/design/e3-e5-product-contract.md)。

## 1. 这里有什么

* `*.schema.json`：13 份消息的 JSON Schema，由唯一权威结构 [src/common/product_contracts.py](../../../src/common/product_contracts.py) 导出，**不要手改**。
* `index.json`：结构版本、问卷 / 词表 / 健康问卷 / 转换版本，以及每份 schema 的 SHA-256。
* `samples/`：39 份公共样例（请求与响应），每份是 `{"message", "description_zh", "payload"}`，`payload` 按对应 schema 校验。

```bash
uv run python scripts/product_e3a.py check-schema
```

```bash
uv run python scripts/product_e3a.py export-schema
```

```bash
uv run python scripts/product_e3a.py samples
```

`check-schema` 核对导出与权威结构逐字节相同、样例符合结构、带出处标签与中文说明、不含本机路径或凭证。改结构要先改权威结构、升级版本、重新导出、重新生成样例，
并通知 E5；不在前端临时加字段。

## 2. 版本

| 项目 | 值 | 相对 v1 |
|---|---|---|
| 结构版本 | `e3a-product-schema-v2` | 变了（见第 3 节） |
| 问卷（六维与物质题干） | `e3a-questionnaire-v0.1-draft` | 不变 |
| 词表 | `e3a-vocabulary-v1` | 不变 |
| 健康问卷 | `e3a-health-coping-q-v0.1-draft`（暂定文案，未验证） | 不变 |
| 转换 | `e3a-conversion-v2` | 变了：金额写法规则 |
| 资料包 | `pack.e3a.synthetic-v1`（`synthetic_fixture_only`） | 编号不变，内容指纹变了（报价未知的烘干机补写了计价单位） |

每条消息的 `meta` 带上面这些版本与资料包的内容指纹；`meta.sample` 是样例出处（`offline_run` 真实代码离线生成、`controlled_fixture` 受控夹具、
`contract_example` 标明为示例的契约响应）。v1 的确认画像与预检查在 v2 下失效（结构版本不同），需要重新确认。

## 3. 相对 v1 改了哪些字段（E5 需要同步的地方）

| 位置 | v1 | v2 | 为什么 |
|---|---|---|---|
| `ProductResult.clones[].detail_zh` | E1 内部说明原文（可能带异常） | 删除，改为 `explanation`（`PublicExplanation`：`category`、`reason_zh`、`impact_zh`、`next_action_zh`、`audit_ref`） | F5：公开文字只由类别生成 |
| `RunProgress.pause_reason` | 内部原因原文 | 删除，改为 `pause`（`PublicExplanation`，运行中或完成时为空） | F5 |
| `ProductIssue` | — | 新增可选 `audit_ref` | F5：预检查拒绝等有内部诊断时，给维护者一个不含内容的编号 |
| `OptionInventoryCoverage.cities[].items[]` | 只有 `outcome` | 新增 `supply_status`、`data_status`、`compile_status` | F2：供给、资料与编译分开；确定不可得是 `resolved` 但不能买 |
| `CatalogCompilation.compiled[]` | — | 新增 `unknown_reason`（报价未知 / 供给未知 / 两者都未知） | F2 |
| `ProductCapabilities` | — | 新增 `money_input_rule_zh` | F3：金额写法规则 |
| 问题代码 | — | 新增 `money_separator_invalid`、`money_negative`、`health_answer_duplicated`、`ability_duplicated`、`hard_requirement_duplicated`、`hard_requirement_conflict`、`profile_contract_rejected`，以及资料编译的 `source_*`、`price_unit_missing`、`participation_invalid` | F2–F4 |

## 4. 13 份消息

| 消息 | 谁产生 | 用途 |
|---|---|---|
| `ProductCapabilities` | E3 | 城市、职业匹配方式、六维与物质题干、参考改善范围、健康问卷、硬要求与属性偏好的支持状态、金额写法、单位与限制。界面只提供这里有的选项 |
| `UserInputDraft` | E5 提交 | 原始答案与逐项状态（未答 / 已提供 / 明确无所谓 / 暂不确定）；明确 0 是数值；表单入口，访谈接口只保留 |
| `ProfileReview` | E3 | 可读解释、职业候选、缺失、重复与冲突、未支持项、待确认内容与 `confirmable` |
| `ConfirmedProfile` | E3 | 已确认的语义、精确分数（`"50/3"`）与显示值、来源、内容身份；答案改过就失效 |
| `ProductPreflight` | E3 | 开局摘要、逐城与总体的可执行 / 可评分 / 可反馈、资料缺口、选项目录覆盖状态、人数与年数、规划器与费用、运行绑定 |
| `RunSubmission` / `RunReceipt` | E5 提交 / E3 | 确认与预检查引用、幂等键；accepted / duplicate_same_run / rejected 与原因 |
| `RunProgress` | E3 | 阶段、每个分身已执行年度、暂停说明（`pause`，其中“评价与汇总生成了没有”“能不能恢复”两句与 `report_available`、`resumable` 一致）、费用状态、能否恢复、报告是否可用 |
| `ProductResult` | E3 | E1 报告的白名单投影：C / E / M、分母、空值、L / V / J、每个分身的公开说明（`explanation`）、事件链、硬要求、逐年生活事实、选项目录覆盖、来源与限制 |
| `SourceFact`、`OpportunityCandidate`、`CatalogCompilation`、`OptionInventoryCoverage` | E3（资料侧） | 来源事实、可不完整的候选、编入 E1 的机会与未编入原因、声明范围内的选项目录覆盖（三种状态） |

**本轮没有托管服务。** 提交、幂等与进度只有本地离线桥接（`service: offline_bridge`）；断线查询与服务失败暂停是 `contract_example` 样例（32、33），
线上接口留 E3-C / E5-B。

## 5. 给 E5 的读法

1. **数值**：`ExactNumber.exact` 是精确值（例如 `"303924358974359/500000000000000"`），`display` 是显示值（`"0.6078"`）；比较、排序、合计都不要用显示值，
   也不要在前端重算 J、均值或范围。
2. **空值**：`mean_j`、`range_lower`、`range_upper` 为空时显示“没有可用于平均的完整结果”或“没有范围”，不要显示 0；`evaluation_status: not_generated` 时
   不显示任何城市分类。
3. **分母**：`mean_denominator` 是 C（完成且评价完整），`range_denominator` 是 C + M；`planned` = C + E + M。主值写“C 个完成且可评价分身的平均”。
4. **错误与说明**：`ProductIssue` 有 `code`、`field`、`message_zh`、`impact_zh`、`next_action_zh`、`severity` 与可选的 `audit_ref`；分身与批次的说明是
   `PublicExplanation`。界面可以简化措辞，但不能改变含义；`audit_ref` 只给维护者排查用（可以显示成“审计编号”），里面没有内部内容。
5. **金额输入**：按 `money_input_rule_zh` 提示用户（例如 `3000.50` 或 `3,000.50`）；前端校验只帮忙，正式检查以服务端为准，不要在前端“修正”用户的写法。
6. **重复答案**：同一题、同一资格、同一项要求只能提交一条；服务端会拒绝重复，前端防重不能代替它。
7. **用途标签**：`usage`（规划 / 初态 / 资格 / 评分 / 仅留存）与 `support`（支持 / 部分支持 / 未支持）要在确认页显示。
8. **健康回答**：独立于六维 100 分；不要画成第七个维度或风险分数。
9. **不要做的事**：职业映射、六维到旧七维的转换、金额换算、标准开局、覆盖率与 C / E / M 都在服务端。

## 6. 样例清单

| 文件 | 内容 |
|---|---|
| 01 | 能力清单（含金额写法） |
| 02–10 | 正常闭环：草稿、审阅、确认、预检查、提交、接受、重复提交（同一运行）、进度、结果（K51 买设备包、M52 按月买杂务服务） |
| 11 | 职业歧义：三个可读候选 |
| 12 | 未答与明确 0（物质 0 → 细分不适用） |
| 13–14 | 主动均分：50/3 与 100/3 |
| 15 | 健康问卷：暂不确定、未答、无明显倾向照实保存 |
| 16 | 旧问卷的“洗衣”点数需要重新确认 |
| 17 | 品牌、菜系只留存；“不让陌生人进家”没有执行保障 |
| 18–20 | 便利资料编译、选项目录覆盖（三种状态）、受控夹具的边界（混合来源、2.5 年寿命、按次计费） |
| 21 | 没有购买：便利分是确定的 0，轨迹照常评分 |
| 22 | 部分月份没有买到服务（受控夹具） |
| 23–25 | C / E / M 混合（M52 的 C = 0；通勤硬要求 1 人核验、1 人没有可信记录记为未核验）、全部出局、还没有评价 |
| 26–27b | 中断后的进度、程序错误暂停与它的结果（公开说明只按类别生成，带审计编号） |
| 28–31 | 标准开局被拒（公开的是类别说明与审计编号，原文在内部诊断）、职业没有对应岗位、金额没有合法换算、答案改过之后提交被拒 |
| 32–33 | 契约示例：断线后查询同一运行、服务失败暂停（手写形状，暂停说明由同一份白名单生成） |
| 34 | 正权重资料未知（受控夹具）：预检查报告缺口、M52 可评分 partial，不删掉、不重新归一化 |
| 35–36 | 资料编译的状态（受控夹具）：确定没有、供给未知、参与小时为负、缺计价单位、只有报价有来源、来源字段不对；覆盖报告的三种状态 |
| 37 | 金额写法错误（“3000,50”）被拒绝：纠正说明 |
| 38 | 重复与冲突答案（受控夹具）：同一健康题两个答案、同一资格两条、两条通勤上限，确认前拒绝 |
