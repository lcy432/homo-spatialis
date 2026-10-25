# E3C → E5：独立显示资源与服务边界补齐

日期：2026-10-24。对应 [E5 请求单](../handoffs/e5-bilingual-map-service-requests.md)。**这是架构设计和工程交接，不是已实现的 API。** 当前代码基线为 `8ccec1c`；R3/R4 修复已通过独立复核（59 条定向、4 条补充边界、全量离线 1152 条通过；本机证据在 `runs/e3c-r3r4-review-20261024/`）。此结论不包含本文件尚待实现的接口补齐。本文件不取代 `docs/INDEX.md` 的实现状态。

## 1. 本轮做什么

把当前可运行的 E3C 接口补齐为 E5 可直接使用的双语、地理显示与服务边界。仍由 E3 维护唯一公共定义，E5 消费它；业务实现由 coding agent 按本设计完成。

分三个工程步骤：

1. 服务动作、期限及 OpenAPI 成功路径补齐；这一步不依赖 AB 的翻译和坐标。
2. E3AB 提供有版本的双语资料与地理显示资源；E3C 接入独立显示出口、鉴权和历史版本读取。双方可并行，公共形状由 E3C 统一。
3. 离线联调、导出公共样例与迁移说明，交 E5 接线并做独立复核。

不改 E1 评分/结算数学、真实资料映射、菜单、模型语言、规模及预算默认值，不增加模型调用、队列或 LangGraph 流程。本轮不请求六维得分分解、完整逐月账本或模型理由投影。无需公开 checkpoint、内部线程编号、归档路径或评分参数。

## 2. 服务层需要补齐的确定规则

### 动作

- `allowed_actions` 是当前服务规则下的动作许可。真实模型运行且付费关闭时不提供 `submit`，同时给出关闭原因；mock 可正常提交。直接提交仍检查同一规则。状态在读取后发生变化时，动作接口仍可拒绝，前端刷新状态。
- “是否已经启动”以本运行的权威启动/机会消费记录为准，不以当前 `queued`、前端历史或 `run_id` 是否存在判断。
- 恢复排队的已启动运行不提供 `cancel`，取消事务也拒绝；不释放已使用机会、不清掉已有进度或结果。首次启动前的排队仍可取消并释放预留。已有删除规则不变。
- 此类直接取消返回 409 `cancel_not_allowed`，三段说明明确“已经启动／没有取消且次数不退／可按公开动作删除或等待”；不能复用只谈恢复的错误文案。付费关闭保持现有 `paid_calls_disabled`。已知付费关闭时，意图同时给出正式原因及等待维护者开启服务的说明。
- 公开阶段可继续使用 `queued`，服务生成“等待首次启动”或“等待恢复”的准确说明；E5 不再推导一套权限。不要仅修改文案或隐藏按钮。

### 期限与确认摘要

- `RunIntentView.expires_at` 和列表中的同名字段表达当前阶段实际适用的期限，不仅是结果期限。新增 `expiry_basis`：`intent_activity`（最后有效业务操作）、`first_finished`（首次结束）、`active_execution`（当前运行阶段不提供固定截止时间）、`unavailable`（已删除或没有可用期限）。
- 无结果的等待/暂停/排队期限直接复用服务端期限函数；有结果的使用首次结束期限。不得用 `updated_at` 替代 `last_activity_at`，查看、轮询、语言切换不续期。执行中仍按已有活动任务保护处理，不为了展示日期改变清理政策。
- `ProfileConfirmReply` 在成功时增加 `profile_summary`，失败时为空。其类型 `ConfirmedProfileSummary`：`confirmed_id`、`content_fingerprint`、`draft_id`、`draft_revision`、`first_confirmed_at`、`expires_at`。时间来自服务库存储行，重复确认返回相同日期，不写入 `ConfirmedProfile` 本体。
- 新增 `GET /api/v1/profiles`（本人仍有效的摘要列表）和 `GET /api/v1/profiles/{confirmed_id}`（`ConfirmedProfileView`：`summary` 与原 `confirmed`）。同源会话鉴权；不存在、他人的内容均 404；到期或删除按现有 410/404 清理规则处理，不复活正文。这样刷新和换设备也能获得日期。
- 画像过期只影响准备新意图；已准备的意图使用自己的副本与期限。服务错误的到期文案应能区分对象，不能对草稿、画像和未结束意图都声称“首次结束后 30 天”。

### HTTP 规范

- 创建意图：202 新建、200 返回已有；提交：202 accepted、200 duplicate_same_run；删除运行：202 deleting、200 已处理。各自 OpenAPI 声明实际响应结构。
- 其他路径按实现逐条核对，保留退出会话 204 无正文。补齐重复创建、重复提交、重复删除的公共样例和实际 HTTP 测试。

## 3. 双语采用独立显示附层

现有 `ConfirmedProfile`、`ProductPreflight`、`ProductResult` 等正文与内容指纹保持不变，包括原 `*_zh` 字段。不能翻译正文后复用原指纹，也不能通过把中文字段移出指纹来绕过身份核对。

### 出口和消息形状

新增只读出口 `GET /api/v1/display`。参数：

- `kind`：受控枚举 `capabilities | session | draft | review | profile | intent | progress | result | runs`。
- `id`：对象编号；对于 capabilities/session/runs 不提供。review 按保存的 review_id 读取；profile 按 confirmed_id；progress/result 按 run_id；其余按对应对象编号。
- `locale`：`zh | en`，默认 `zh`。不是用户输入语种，也不是规划器语言。

返回 `DisplayEnvelope`：

```text
display_schema_version: e3-display-v1
kind / id: 与请求对应
source: 由既有业务读取函数得到的原公共消息
source_view_fingerprint: 对本次 source 规范 JSON 的校验值，仅用于绑定显示
locale: zh | en
resource_version: 显示资源内容版本
status: complete | partial | unavailable
fields: [DisplayField, ...]
metrics: [MetricDisplaySpec, ...]   # 结果显示时提供，其余为空
```

`source` 必须使用按 kind 区分的已知公共模型，拒绝任意字典、任意文件路径与未知目标。review 对应 ReviewReply；profile 对应 ConfirmedProfileView；其他与现有读取接口相同。source 和显示附层在一次响应中返回，避免前端把刚更新的进度与上一版翻译拼在一起；E5 adapter 仍把 source 作为原业务消息，fields 只供显示。这个出口不执行保存、确认、预检查、恢复或重放。

`DisplayField`：`pointer`（相对 source 的 JSON Pointer）、`key`（稳定文案/实体字段键）、`status = provided | missing`、`text`（有翻译时的纯文字，否则 null）、`source_locale`。pointer 必须指向白名单中的可展示文字；缺失时由 E5 显示 source 原文和“英文说明尚未提供”。不能返回 HTML，也不能覆盖数值、权重、ID、动作枚举或秘密字段。数组下标只在绑定同一份 source 时使用；不用中文字符串当查找键。

无权、删除、过期等错误沿用原读取接口；请求目标非法用现有 422 `invalid_request`。源对象可读而英文资源缺失时仍返回原 source，附层标 partial/unavailable，不能用显示问题阻断业务读取。未登记的历史资料包身份不回退当前包；已登记但尚无地理资源的版本返回 unavailable。显示异常不透传堆栈或原始内部异常。

这些是新接口的设计名称，实施时生成 schema/OpenAPI 和样例后才算可消费。原有业务路径和 mutation 回复继续可用；E5 以显示出口读取当前可展示快照，mutation 后再按原规则只读刷新，不重复 mutation。

### 文案与动态绑定

- E3AB 提供按实体类型/稳定 ID/字段组织的双语资源，及按稳定 template key 组织的事实模板。个人用户原文、自由文本、职业输入语种不被翻译或回写。
- E3C 提供服务错误、权限、次数、期限、费用及状态的双语模板。数值取公共事实，参数具有类型与允许字段，不接受自由执行的模板表达式。
- 在审阅、预检查、年度事实/结果投影的生成处，利用当时已知的职业、城市、机会 ID、月份和公开状态生成显示绑定。特别是 jobs/housing/food/training 等当前只有格式化字符串的数组，不能让前端拆中文；应在源投影处保存独立的 `pointer → template key + 安全参数` 绑定。
- 私有绑定保存在所属审阅/意图/运行的数据范围内，与其同期限、同删除流程；不得复制到公共目录或共享 CDN。绑定不是新的业务状态，丢失只能导致显示缺口，不能改变原业务结果。GET 显示只读取或纯函数渲染，不能在查询时补跑 E1、写新结果或调用翻译模型。
- 更新前生成的运行：有足够的结构化事实可确定性重建绑定时可在受控迁移中生成；否则标 missing，绝不按中文猜语义。不得因当前资料包更新而改写历史运行的名称、范围或解释。
- 固定、无私人内容的 ServiceError 文案按 `code` 与受控 `field` 等参数导出双语目录，E5 可随包携带，供未登录/会话失效时使用；业务意义仍由 E3 维护。动态业务问题以服务生成的绑定为准，不能从任意中文或原始异常逆向翻译。
- 发布显示覆盖报告：需要展示的字段数、已提供/缺失及 key；用户原文明确不属于翻译覆盖分母。关键路径缺英文不能宣称“完整双语”。

## 4. 地理显示资源

新增 `GET /api/v1/display/geography?pack_fingerprint=<资料包指纹>`，返回 `GeographyDisplayBundle`。当前能力从 capabilities.meta.data_pack 取身份；历史运行从该运行 source.meta.data_pack 取身份。请求不接受任意路径。用已登记显示包和本人运行对应的历史版本；缺少版本明确不可用，不回退当前六城。

清单字段：

```text
display_schema_version / resource_version / pack_fingerprint
status: complete | partial | unavailable
cities[]:
  city_ref / country / scheme / code / geography_version
  country_names: {zh, en}
  names: {zh: {official, short}, en: {official, short}}
  model_kind: real | synthetic
  anchor_status: available | unknown | not_applicable
  anchor: {latitude, longitude, crs: EPSG:4326, kind, source_refs[]} | null
  support_scope: {ref, version, residence: {zh,en}, work: {zh,en}, note: {zh,en}}
  coverage_note: {zh,en}  # 来源覆盖需另读本运行 data_nature/scope_notes
sources[]: {source_ref, publisher, dataset, version, public_url, content_sha256}
```

- 纬度/经度用具名字段、单位为度：纬度 −90…90，经度 −180…180，拒绝 NaN/无穷。`kind` 限 `principal_city_point | metro_internal_point`，按真实来源选择，不能把代表点写成几何中心。
- 正式英文地区名以对应边界版本的来源为准，中文是人工核对的译名；短名不替代支持范围。
- 纽约 NYC 五区居住/NY 州支持工作地、芝加哥 IL 部分、旧金山 MSA 不含 San Jose 等必须双语表达。未来若资料覆盖范围不同，保留独立说明，不用一块地图边界替代所有来源。
- 合成城市 anchor 为 null、not_applicable；真实城市没有可靠锚点为 unknown，仍可从列表访问。显示清单不授予运行资格，能力和规则决定可选城市。
- E3AB 核对已有 Census Gazetteer 或主城市坐标资料及来源，E3C 只验证和发布；E5 负责底图许可、国家边界、投影、纹理、标签避让及拖转。首版不做住宅、街道、PUMA 多边形。

AB 协调结果（2026-10-24）：六城代表点已经存在于 `data/raw/intake/census_gazetteer_cbsa_2024/8c2916e66c685c88.bin`，为 Census 官方 `2024_Gaz_cbsa_national.zip` 的缓存，文件内按 `GEOID` 关联，`NAME` 为来源名称，`INTPTLAT / INTPTLONG` 为原始坐标。原文件 SHA-256：`0ec21c5d83982467903f1500fbbae87311edc81f09bed8ec1bbded80bce7eec3`。AB 与 C 均已读取这六条记录；应使用 `metro_internal_point`，不是主城市中心。**原始大地基准仍需官方依据核实**；必要时登记转换方法后才能发布 EPSG:4326。未核实前公共 anchor 保持 unknown/null，不能因数值看起来合理而贴 WGS84 标签。这项无需重新抓取六城生活资料。

## 5. mean_j 的显示承诺

当前评价规则下，已生成的有效 `mean_j` 数值域为 **[0,1]**。它是同一城市 C 类分身的 J 的算术平均；J 来自实际与参考生活逐月满足感的非负加权平均。代码依据：`econ.life_evaluation.combine`、`econ.life_aggregation` 的城市汇总，以及评分分项/健康项的 0–1 范围。

结果显示附层提供 `MetricDisplaySpec`：`key = mean_j`、`minimum = "0"`、`maximum = "1"`、`unit = model_score`、`direction = higher_model_evaluation`、`denominator_field = mean_denominator`、`comparison_scope = within_result_only`，附当前协议/评分/评价绑定的公开引用。该元数据只声明已存在分数的显示域，不计算新分数，不公开公式系数。

- 两种地图、同一结果中的所有国家共用固定尺度，不按国家或六城最小/最大重新拉伸。超出数值域报契约异常，不静默截断。
- C=0 或没有评价不着最低分颜色；C>0 且 M>0 可以着完整者均值颜色，同时显示 C/E/M 与分母。
- 比较仅限本轮同一已确认画像、运行设置、配套协议/评分/评价及资料包构成的受支持比较组。不同用户、不同轮次或未来不同国家“数值都在 0–1”不等于具有可比性。
- 对方向使用已有 comparisons；不建排行榜。缺失范围的分母 C+M，不是均值 C，不是置信区间，也不保证包含完整者均值。
- 0.7 不表示现实幸福 70%，不构成城市现实推荐或模型已标定的声明。

## 6. 版本、权限与交付文件

- 权威公共模型仍在 `src/common/product_contracts.py`。不要为了显示语言修改核心 `PRODUCT_SCHEMA_VERSION`、问卷、词表、转换或资料包身份；当前核心保持 v3.3，确认和运行不因翻译修正失效。
- 显示附层独立版本 `e3-display-v1`；服务 API 的现有独立版本由 `e3c-service-api-v1` 升为 `e3c-service-api-v1.1`（URL 仍 `/api/v1`）。服务回复增加期限字段的 schema 重新导出并在 index/交接明确属于服务层变化，不把它混入有指纹的核心业务消息。
- 新服务摘要、显示模型与枚举统一从同一权威文件导出，禁止 E5/AB 手写第二套公共 schema。展示资源更新只变 resource_version；实质问卷/职业映射变化走原业务版本规则。
- AB 已认可资料落点：`configs/product/display/`（可审阅的模板和来源登记），`data/display/<pack_fingerprint>/<resource_version>/`（不可覆盖的冻结实体/地理显示清单，不改 E1 原资料包）。这些是可追溯的展示资料，不重新生成生活机会。更新保留旧资源版本；私有绑定记录所用 resource_version，不能只按“最新资源”读取历史对象。尚无绑定的旧对象只能受控迁移或明确缺失，GET 不补写。
- E3C 导出：`contracts/product/v3/` 中新增 schema、OpenAPI 和公共样例；固定双语文案资源放 `contracts/product/display/v1/`；交接在 `docs/handoffs/e3c-to-e5-service.md` 增补实际使用方法。
- 显示出口与原读取接口相同鉴权、归属及删除/到期校验。私有返回 `Cache-Control: private, no-store`，不记录正文、原文或秘密；不能通过显示接口读回已经隐藏的数据。固定公开文案可以随 E5 包分发，绝不包含用户绑定。
- 本轮不添加长期备份、不部署、不开放真实模型，不改变原数据保留政策。

## 7. 验收与 E5 迁移

最低验收：

1. 付费关闭的真实规划器无 submit、直接提交拒绝且不扣次；mock 正常。恢复排队无 cancel，直接取消拒绝且进度、结果与机会记录不变；首次排队仍可取消。
2. 新设备能读画像首次确认/到期和意图独立期限；重复确认/查询/切语言不续期；到期后动作拒绝。画像到期不错误删除已准备的意图。旧 R3/R4 探针及其修复回归保持通过。
3. 全部实际 200/201/202/204 成功路径与 OpenAPI、样例一致，尤其重复操作。
4. 同一对象中英显示的 source 与其原业务读取完全一致，核心指纹、确认令牌、次数、账目、输入语种和规划器请求不变；同文案不同参数和不同城市不串译。显示绑定不能泄露秘密或内部评分对象。
5. 两用户互访显示/历史资料被拒；删除全部后迟到显示请求不复活内容；旧资料版本不偷偷用当前地区范围；源对象与显示绑定不同版本时明确缺口。
6. 六城锚点来源、坐标顺序、范围与双语名称核对；合成/缺坐标城市不伪造位置；列表仍可操作。地图/地球共用 0–1 域，C=0 不补零，M 不画成生活出局。
7. 关键业务状态英文覆盖及完整本地 mock 服务故事通过；没有真实模型或在线翻译。全量离线回归、lint、schema/OpenAPI 一致性检查按仓库规则执行。

E5 可先实现现有 v3.3 主链、UI 词典、共享状态、两种模式和显示 adapter。新增出口交付后，adapter 将 `DisplayEnvelope.source` 送入原业务状态，fields 只用于文字呈现；正式数字和权限永远取 source。缺显示资源时明确回退原文，缺坐标时使用列表，相应验收保持未完成。

AB 已只读确认本文件的归属、稳定 ID 与模板绑定方式及 mean_j 口径。待交付的是实际双语资源/覆盖报告、坐标基准证据和工程实现，不是等待另作一套架构设计。AB 特别确认 `ScopeNote.topic` 不能单独作为唯一翻译键；机会/片区标签须同时绑定资料包版本；年度字符串数组需在原投影处补结构化显示绑定。

最终工程反馈用中文区分“已实现并自测”“待独立复核”“AB 尚缺的资源”“E5 尚未接通”；附实际接口、样例号、版本、测试命令与结果。不能把本文件的接口草案当作已交付服务。
