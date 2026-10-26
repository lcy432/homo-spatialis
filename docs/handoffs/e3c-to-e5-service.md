# E3C 托管服务交给界面层（E3 → E5）

日期：2026-10-23；2026-10-24 E3C 收尾（运行选项、独立审核 R1–R6 的修复，第 11 节是本轮的改动清单）；2026-10-24 E3 → E5 接口补齐（按钮与期限、双语显示附层、地理清单、结果色阶，第 12 节）；2026-10-25 独立复核 F1–F3 的收尾修复与四项小接口问题（第 13 节）；2026-10-25 输入体验收敛（输入目录、结构化资格答案、面板多选、去掉硬要求与属性偏好、旧草稿迁移）与 F3 遗留（第 14 节）。规则见 [ADR0043](../../DECISIONS/0043-e3c-hosted-service.md)，机制见 [E3C 托管服务](../design/e3c-service.md)。本轮只提供服务、接口与样例，**没有改 E5 页面**。

## 1. 现在能做什么、不能做什么

* **能**：在本机起一个离线 mock 服务（只绑定 127.0.0.1，零网络、零费用），E5 的页面照下面的接口接线、走完整条路：专属链接 → 填写与审阅 → 确认画像 → 运行前说明 → 确认开始 → 后台模拟 → 进度与结果 → 删除。
* **不能**：网页还不能正式给用户用——服务没有部署（没有主机、域名与 TLS），付费模型调用没有打开（`live_calls_enabled: false`），真实模型在这条服务链上没有试跑过。网页仍读 `contracts/product/v2/` 的部分要先迁到 v3（[E3B 交接](e3b-to-e5-real-pack.md)、[生活扩展交接](e3-to-e5-life-extension.md)）。
* 次数、排队、预算、后台调度、删除与过期**全部由服务端负责**；界面不另做一套计数、队列或重试。

## 2. 文件

| 文件 | 用途 |
|---|---|
| [`contracts/product/v3/openapi.json`](../../contracts/product/v3/openapi.json) | 全部 HTTP 接口（路径、请求与回复的结构、错误码）。生产不在线公开 `/docs`；本地 mock 服务在 `http://127.0.0.1:8765/api/v1/docs` 有交互式文档 |
| `contracts/product/v3/*.schema.json`、`index.json` | 公共结构 `e3a-product-schema-v3.4`（34 份消息，含显示附层的 5 份；第 14 节）。唯一权威是 `src/common/product_contracts.py` |
| `contracts/product/v3/samples/60_…–154_…` | 服务的样例（第 8 节；105–128 是显示附层，第 12.6、13.6 节；129–154 是新输入流程，第 14.8 节），全部由服务端真实代码路径产生，凭证是明确无效的测试值 |
| `contracts/product/display/v1/service-errors.json` | 固定双语错误目录（未登录、服务不可达时随页面携带；第 12.3 节） |
| `configs/product/service_local_mock.yaml` | 本地联调配置（`site_origin = http://localhost:5173`） |

## 3. 本地接线

1. 起服务（两个终端，都在仓库根目录）：

   ```bash
   uv run python scripts/product_service.py api --config configs/product/service_local_mock.yaml
   ```

   ```bash
   uv run python scripts/product_service.py worker --config configs/product/service_local_mock.yaml
   ```

2. 建一个测试空间，拿到链接（只显示一次；数据在 `runs/product-service-local/`，不进 git）：

   ```bash
   uv run python scripts/product_service.py invite create --label 本地联调 --config configs/product/service_local_mock.yaml
   ```

   打印的是 `http://localhost:5173/invite#token=…`。

3. Vite 开发服务器把 `/api` 代理到服务（这是给 E5 的建议改法，本轮没有改 `web/`）：

   ```ts
   server: { port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:8765" } }
   ```

   浏览器看到的始终是同一个来源 `http://localhost:5173`，所以 Cookie 与 Origin 检查都按同源工作。换成 `127.0.0.1:5173` 打开时，把配置里的 `site_origin` 跟着改（不放宽成多个来源）。

## 4. 会话交换与 CSRF

```ts
// /invite 页面：读 fragment → 交换 → 立刻从地址栏移除秘密
const token = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", "/");                     // 秘密不留在地址栏、历史或分享链接里
const r = await fetch("/api/v1/session", {
  method: "POST",
  headers: { "Content-Type": "application/json" },       // 只接受同源 JSON
  body: JSON.stringify({ invite_token: token }),
  credentials: "same-origin",
});
const session = await r.json();                          // SessionView：csrf_token、quota、rules、drafts、open_intent_id
let csrf = session.csrf_token;                           // 只放内存；刷新后用 GET /api/v1/session 取回

// 之后每个修改请求（POST / PUT / DELETE）：
fetch(url, { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body, credentials: "same-origin" });
```

* 不要把邀请秘密放进查询串、路径、localStorage、日志或任何模型请求；会话秘密在 HttpOnly Cookie 里，页面读不到也不需要读。
* 会话固定 7 天，查询不续期；收到 `401 unauthenticated` 就提示“重新打开你的专属链接”。链接被重发过时旧链接与旧会话都失效。
* 换设备后：`GET /api/v1/session` 的 `drafts`（还在保存期的草稿）与 `open_intent_id`（尚未结束的那一次）让用户接着走；`GET /api/v1/runs` 列出全部运行。

## 5. 页面流程与接口

| 步骤 | 请求 | 回复 |
|---|---|---|
| 新建草稿 | `POST /api/v1/drafts` | 201 `DraftView`（编号与修订号由服务器分配，城市已按规则选好） |
| 读 / 存 / 删草稿 | `GET` / `PUT`（`DraftSaveRequest`：`expected_revision` + 草稿）/ `DELETE /api/v1/drafts/{id}` | `DraftView` / `DraftView`（修订号 +1）/ `DeletionReceipt`；旧修订号 409 `revision_conflict` |
| 迁移旧草稿 | `POST /api/v1/drafts/{id}/migrate`（`DraftMigrateRequest`：`expected_revision`、`clear_removed`） | `DraftView`（修订号 +1）；没有同意清空 409 `migration_consent_required`，不需要迁移 409 `migration_not_needed`（第 14.6 节） |
| 审阅 | `POST /api/v1/drafts/{id}/review` | `ReviewReply`（审阅服务端保存的那一版） |
| 确认画像 | `POST /api/v1/profiles/confirm`（`ProfileConfirmRequest`：审阅编号、草稿编号、修订号、知悉项） | `ProfileConfirmReply`：`confirmed` 带确认画像，`refused` 带原因（草稿改过、知悉项缺失、审阅过时） |
| 准备模拟 | `POST /api/v1/run-intents`（`RunIntentCreateRequest`：`confirmed_id`，可选 `clones_per_city`、`years`、`max_cost_cny`） | 202 `RunIntentView`（`checking`，排队检查；`settings` 是这次保存的有效设置）；同一份画像、同样的设置再点一次 200 返回原意图；选项越界 409 `run_options_invalid` |
| 看流程 | `GET /api/v1/run-intents/{id}` | `RunIntentView`（只读快照） |
| 确认开始 | `POST /api/v1/run-intents/{id}/submit`（`RunSubmitRequest`：`confirm_with`、`idempotency_key`、`accept_scope: true`） | 202 `RunSubmitReply`（`accepted`，预留 1 次，`queued`）；重复 200（`duplicate_same_run`） |
| 开始前取消 | `POST /api/v1/run-intents/{id}/cancel` | `RunIntentView`（`cancelled`，预留退回） |
| 我的运行 | `GET /api/v1/runs` | `RunList` |
| 进度 / 结果 | `GET /api/v1/runs/{run_id}/progress` / `.../result` | `RunProgress` / `ProductResult`（没有结果时 409 `result_unavailable`） |
| 恢复 | `POST /api/v1/runs/{run_id}/resume` | 202 `RunIntentView`（`queued`）；不允许时 409 `not_resumable` |
| 删除一次运行 | `DELETE /api/v1/runs/{run_id}` | 202 `DeletionReceipt`（`deleting`） |
| 删除我的全部内容 | `DELETE /api/v1/me/data` | 202 `DeletionReceipt`（`all_data`） |
| 退出这个设备 | `DELETE /api/v1/session` | 204 |
| 能力清单 | `GET /api/v1/capabilities` | `ProductCapabilities` |

**按钮只看 `allowed_actions`**：`submit`、`cancel`、`resume`、`delete`、`view_progress`、`view_result`、`create_new_intent`。没有列出的就不显示。`waiting_for` 告诉页面在等谁：`user`（确认、修改、恢复）、`maintainer`（“不需要你重新填写，等维护者处理”）、`service`（排队检查、排队、运行中、删除收尾）、`none`。

阶段 `stage` 是网页流程，不是 C / E / M：`checking`、`awaiting_confirmation`、`blocked`、`queued`、`running`、`needs_attention`、`finished`、`cancelled`、`deleting`、`deleted`、`expired`。`finished` 只表示本次服务流程结束；结果里逐城、逐分身的 C / E / M、未知与部分结果照 E1 原样显示。不要把 HTTP 200 或 `finished` 写成“模拟成功”。

## 6. 重复提交、查询与恢复的区别

| 动作 | 会发生什么 | 不会发生什么 |
|---|---|---|
| **重复提交**（同一意图再 `submit`） | 同一个键、同一份内容：返回原运行（`duplicate_same_run`）；换了键：仍是原运行；同一个键配了别的内容：409 `idempotency_conflict` | 不再预留、不再排队、不生成第二批分身 |
| **查询**（`GET` 意图 / 进度 / 结果 / 运行列表） | 读 worker 写下的白名单快照 | 不恢复运行、不调用规划器、不重放历史、不续期、不写任何东西 |
| **恢复**（`resume`） | 只在 `allowed_actions` 有 `resume` 时（服务暂时不可用、E1 停在可以恢复的边界）：排队，worker 经 E1 的恢复入口逐条核对后接着跑 | 不另扣次数；已保存的回复不重新请求；发送状态不确定、预算停下、模型不符、归档冲突时没有这个按钮（等维护者） |

幂等键：每次用户点“确认开始”生成一个新键（例如 `crypto.randomUUID()`，8–80 位字母数字与 `_.:-`），**这一次点击的重发**（超时、断网）用同一个键与同一份内容。`confirm_with` 换了（重新检查过）就要新键。

轮询：`checking`、`queued`、`running`、`deleting` 时每 2–5 秒 `GET /api/v1/run-intents/{id}`；其他阶段停止轮询。轮询从不自动调用 `submit` 或 `resume`。

## 7. 错误

全部错误是 `ServiceError`：`status`、`code`、`message_zh`（发生了什么）、`impact_zh`（影响是什么）、`next_action_zh`（下一步怎么做）、可选的 `support_ref`（`sup-…`，用户联系维护者时报这个）与 `retry_after_seconds`。原样显示三句中文，不显示原始异常。

| 代码 | HTTP | 页面怎么做 |
|---|---|---|
| `unauthenticated` | 401 | 提示重新打开专属链接 |
| `not_found` | 404 | 别人的编号与不存在的一样；回到“我的运行” |
| `revision_conflict` | 409 | 重新读草稿，在新版本上修改 |
| `preflight_stale` | 409 | 重新读意图，看新的运行前说明后再确认（没有扣次数） |
| `quota_exhausted` | 409 | 3 次用完：只能看已有结果 |
| `idempotency_conflict` / `intent_conflict` | 409 | 新请求换新键；已有未结束的模拟时先完成、取消或删除它 |
| `not_resumable` / `result_unavailable` | 409 | 刷新状态，不重试 |
| `deleting` / `deleted` / `expired` | 409 / 410 / 410 | 内容已经不可查看 |
| `paid_calls_disabled` | 409 | 服务没有开启付费调用：可以填写与检查，不能开始 |
| `run_options_invalid` | 409 | 运行选项不在允许范围里：`field` 指出哪一项（`clones_per_city`、`years`、`max_cost_cny`、`cities`），按 `SessionView.rules.options` 的范围改了再提交；不回显用户填的值 |
| `run_settings_unavailable` | 409 | 这次运行保存的设置读不到：不要换成默认值重试，联系维护者（报支持编号） |
| `data_pack_unavailable` | 503 | 稍后再试 |
| `csrf_failed` / `origin_rejected` | 403 | 刷新页面（重新 `GET /api/v1/session` 取 CSRF） |
| `payload_too_large` | 413 | 缩短填写内容 |
| `rate_limited` | 429 | 等 `retry_after_seconds` |
| `invalid_request` | 422 | 检查表单；不回显原始校验信息 |
| `internal_error` | 500 | 显示支持编号 |

## 8. 样例（`contracts/product/v3/samples/`）

| 情形 | 样例 |
|---|---|
| 正常：会话、草稿、保存、审阅、确认、排队检查、等确认、确认开始、重复提交、结束、进度、运行列表 | 60–75、78–80 |
| 阻断（收银员在合成城市没有对应岗位） | 81 |
| 排队 | 70（检查中）、73（确认开始后排队） |
| 运行中（**测试传输**：真实模型规划器的通路，传输层是离线替身，不是真实模型的表现） | 76、77（费用三分、候选与服务尝试分开计） |
| 部分结果（受控夹具：一个分身出错，结果里有 M） | 84 |
| 次数用完 | 85 |
| 暂停（服务暂时不可用，可以恢复） | 82、83 |
| 停下且不能由用户恢复（**测试传输**：同一份规划的服务尝试已经用完；按钮与进度 `resumable = false` 一致） | 100、101 |
| 预算停下（测试传输，这次费用上限 0.1 元：下一次预留会超过上限，停在发送之前） | 102 |
| 开始之后的失败（受控夹具：暂停期间快照被改动、恢复被拒绝；说明写“已经用掉 1 次机会”，没有“取消”） | 103 |
| 运行选项：默认（只给 `confirmed_id`，服务用默认值）、较小规模的请求与保存的有效设置、越界 | 69、97、98、99 |
| 最新六城资料（冻结的 E3B 真实资料包，样例说明里写着内容身份与构建身份）的结果：六城各 1 个分身、3 年、离线 mock；逐城的资料性质与覆盖说明在 `data_nature` | 104 |
| 删除 | 86（回执）、87（墓碑）、88（已删除的结果 410）、89（删除全部） |
| 过期 | 90（意图）、91（结果 410） |
| 其他错误 | 65 版本冲突、75 键冲突、92 未登录、93 CSRF、94 限流、95 付费未开、96 预检查过时（服务设置变了：重新看运行前说明再确认，没有扣次数） |

样例用测试时钟（2026-10-24 起）与测试配置（合成资料包、默认每城 1 个分身；104 用 E3B 资料包）；生产默认六城 × 每城 4 个分身 × 3 年、20 元，允许每城 1–4 个分身、只开放 3 年、0.01–20 元（`SessionView.rules.options` 写明）。

## 9. 新字段要点

* `QuotaSummary`：`total = used + reserved + available`；`reserved` 是确认开始但还没启动的。
* `ServiceRules`：默认规模（`cities`、`clones_per_city`、`years`、`max_cost`）、规划器种类、付费开关、币种、各项保存天数（含 `profile_retention_days`）、`notes_zh`（原样显示）；`options` 是用户能选的范围（`RunOptionsPolicy`：`cities_allowed`、`clones_per_city` 的默认 / 最小 / 最大、`years_default`、`years_allowed`、`max_cost` 的默认 / 最小 / 最大与币种、`notes_zh`）。
* `RunIntentView.settings` / `RunSummary.settings`（`RunSettingsView`）：这次运行保存的有效设置——城市、每城分身数、年数、分身总数、规划器种类、费用上限与币种、规划候选上限、服务尝试上限、`settings_fingerprint`。运行前说明、进度与结果都按它，不按服务现在的默认值。删除或过期之后不出现。
* `ServiceError.field`：只在 `run_options_invalid` 时出现，指出哪一项选项。
* `CostStatus`（v3.2 新增的可选字段，mock 时不出现）：`currency`、`price_version`、`max_cost`、`settled_cost`、`uncertain_cost`、`in_flight_cost`、`service_attempts`、`service_attempt_limit`、`uncertain_attempts`。金额是应用侧按报价的估算，必须同时显示 `note`（不是供应商账单）。
* `RunProgress.updated_at`：快照写下的时间（UTC）；离线桥接不写。
* `RunReceipt.service = hosted_service`：服务端 SQLite 登记的提交。

## 10. 已验收与没有验证的

* 已验收（离线，`tests/product_service/` 与全量测试）：身份隔离与重发链接、草稿冲突、确认不可伪造、次数并发与重发、预留释放与不退款、运行选项（默认值、允许范围、越界在预留之前拒绝、保存后处处用它、同设置返回原意图与不同设置冲突、重启、设置过时）与身份一致、四个中断点的重启恢复、发送不确定、费用上限、查询无副作用、worker 独占、五种删除竞争与删除全部的并发写入、确认画像 30 天、过了到期时间的动作、恢复资格、开始前后的说明、过期、清理范围、信息隔离、结果语义、E3B 六城 × 4 × 3 全规模离线闭环。
* 没有验证：真实模型在这条服务链上的表现与费用（`live_calls_enabled` 没开过）；真实部署（主机、TLS、反向代理、systemd、磁盘与备份策略、日志轮转）；浏览器里的真实页面（E5 尚未接线）；多人同时使用时的排队等待时间（单个 worker 串行，一次全规模运行要几分钟到更久）。

## 11. E3C 收尾（2026-10-24）给 E5 的改动清单

1. **运行选项（新）**：“准备模拟”页可以让用户选每城分身数、年数与这次的费用上限（元，字符串，最多到分，例如 `"5"`、`"12.5"`）；不选就省略字段（服务用默认值）。范围只读 `SessionView.rules.options`，不要在页面里写死 1–4、3 年或 20 元。城市不是这里的选项：来自用户确认的画像（草稿里选的城市），必须在 `cities_allowed` 里。
2. **显示有效设置**：运行前说明旁边显示 `RunIntentView.settings`（城市、每城分身数、年数、分身总数、费用上限）；“我的运行”用 `RunSummary.settings`。有效设置在创建时就定了，之后改服务默认值不影响它。
3. **按钮仍只看 `allowed_actions`**：开始之后停下的运行，只有服务判断 E1 还能接着跑时才有 `resume`（`RunProgress.resumable` 与它一致）；没有 `resume` 时显示说明里的“不需要重新填写：等维护者处理，或删除这次运行”。运行进行中 `resumable = false`，`resume_note` 写“这次运行正在进行，不需要恢复”，原样显示即可。说明分开始前与开始后：开始前写“预留的次数已经退回”并给 `cancel`；开始后写“已经用掉 1 次机会（开始之后不退）”，没有 `cancel`。原样显示 `explanation` 的三句，不自己拼。
4. **错误码**：`run_scale_fixed` 删掉了（规模不再固定）；新增 `run_options_invalid`（带 `field`）与 `run_settings_unavailable`。`preflight_stale`：重新读意图、看新的运行前说明、换新键再确认。
5. **过期与删除**：确认画像从第一次确认起 30 天可以用来准备模拟（`rules.profile_retention_days`），重复确认不续期；过了页面显示的到期时间，确认开始、恢复、取消都会被拒绝（410 `expired`），不扣次数。删除全部之后，之前打开的页面再保存或确认都会被拒绝，不会把内容写回来。
6. **公共结构仍是 `e3a-product-schema-v3.3`**（E5 还没接服务，所以没有另开版本号）；`openapi.json` 与样例重新导出：`uv run python scripts/product_service.py check-openapi` 核对提交的接口文档与实际应用一致，`uv run python scripts/product_e3a.py check-schema` 核对结构与样例。
7. **本地联调不变**：`configs/product/service_local_mock.yaml`（离线 mock，只绑定 127.0.0.1），起服务与建链接的命令见第 3 节；离线联调脚本 `uv run python scripts/product_service.py e2e --out <新目录>` 不起端口，走同一条服务路径，可以用来对照页面看到的结果。

## 12. E3 → E5 接口补齐（2026-10-24：按钮、期限、双语显示附层、地理清单、色阶）

状态：**实现及离线自测完成，待独立复核**。设计见 [E3C：独立显示资源与服务边界补齐](../design/e3c-display-and-service-handoff.md)，请求单见 [E5 → E3](e5-bilingual-map-service-requests.md)。全部离线：没有真实或订阅模型调用、没有在线翻译、没有部署。本轮**没有改 `web/`**。

### 12.1 版本

| 层 | 版本 | 说明 |
|---|---|---|
| 核心公共结构 | `e3a-product-schema-v3.3`（不变） | 画像、预检查、结果的正文与内容身份都没变；翻译修正不让确认或运行失效 |
| 服务 API | `e3c-service-api-v1.1`（URL 仍是 `/api/v1`） | `openapi.json` 的 `info.version` |
| 显示附层 | `e3-display-v1` | `DisplayEnvelope`、`DisplayField`、`DisplayAttachment`、`MetricDisplaySpec`、`GeographyDisplayBundle` |
| 显示资源 | `e3-display-res-v1-<16 位内容摘要>` | 按资料包冻结在 `data/display/<资料包指纹>/<版本>/`，内容决定版本名、不可覆盖；当前登记见 `configs/product/display/registry.yaml`：合成资料包 `f58ef477…` → `e3-display-res-v1-0c6e23d0ce146d9a`；E3B 六城资料包 `48eda959…` → `e3-display-res-v1-43b216694e20aa77` |
| 服务库 | `e3c-service-store-v3` | 从 v2 打开时就地升级（第 12.7 节），不删库 |

### 12.2 新路由与改了的回复

| 请求 | 回复 | 要点 |
|---|---|---|
| `GET /api/v1/profiles` | 200 `ProfileList` | 本人还在有效期内的确认画像摘要 `ConfirmedProfileSummary`：`confirmed_id`、`content_fingerprint`、`draft_id`、`draft_revision`、`first_confirmed_at`、`expires_at`（来自服务库，不在 `ConfirmedProfile` 正文里）。重复确认、查询、切语言都不续期 |
| `GET /api/v1/profiles/{confirmed_id}` | 200 `ConfirmedProfileView`（`summary` + 原 `confirmed`） | 别人的与不存在的一样 404；过期 410（说明写“确认画像”）；画像过期不影响已经准备好的意图 |
| `GET /api/v1/display?kind=…&id=…&locale=zh\|en` | 200 `DisplayEnvelope` | 第 12.3 节。`kind` ∈ `capabilities`、`session`、`draft`、`review`、`profile`、`intent`、`progress`、`result`、`runs`；`capabilities` / `session` / `runs` 不带 `id`；`review` 用审阅编号，`profile` 用确认编号，`progress` / `result` 用运行编号，其余用对象编号；`locale` 省略是 `zh` |
| `GET /api/v1/display/geography?pack_fingerprint=…[&resource_version=…]` | 200 `GeographyDisplayBundle` | 第 12.5 节 |
| `POST /api/v1/profiles/confirm` | 200 `ProfileConfirmReply` | 新增 `profile_summary`（成功时有，失败时 `null`）与 `display`（中英两种文字） |
| 全部错误 | `ServiceError` | 新增可选的 `display`（中英两种文字，未登录也有）；新错误码 `cancel_not_allowed`（409） |
| `RunIntentView`、`RunSummary` | — | `expires_at` 是**当前阶段实际适用**的截止时间，新增 `expiry_basis`：`intent_activity`（最后一次有效业务操作起算）、`first_finished`（结果从首次结束起算）、`active_execution`（运行中，没有固定截止时间）、`unavailable`（删除中、已删除、已过期） |

**按钮仍只看 `allowed_actions`**，本轮改的是服务端怎么算：

* 真实模型的运行在付费调用关闭时**没有 `submit`**，`waiting_for = maintainer`，`explanation.category = service_paid_calls_off_waiting`；直接提交照旧 409 `paid_calls_disabled`、不扣次数（样例 112/113、95）。离线 mock 不受影响。
* “开始过没有”看服务库的启动记录（预留转为已使用），不看阶段：**恢复之后又在排队的运行没有 `cancel`**，`stage` 仍是 `queued`，`stage_zh` 与说明写“已经开始过、在排队等恢复”（`service_queued_resume`）；直接取消 409 `cancel_not_allowed`：已经开始、没有取消、次数不退，可以等待或删除（样例 109–111）。首次开始前的排队仍可取消、退回预留（`service_queued_first_start`）。读取之后状态又变了，动作接口照样可能拒绝：收到拒绝就重新读。
* 退出会话 204 没有正文；创建意图 202 新建 / 200 返回已有；确认开始 202 / 200（`duplicate_same_run`）；删除运行 202 删除中 / 200 已经处理过。OpenAPI 逐条声明了这些状态码与回复结构（`tests/product_service/test_e5_service_boundaries.py` 用一条完整故事核对每个实际出现的状态码）。
* 所有私有回复带 `Cache-Control: private, no-store`。

### 12.3 显示出口与 E5 adapter 怎么读

`DisplayEnvelope`：`display_schema_version`、`kind`、`id`、`source`（**与对应业务读取的回复完全相同**，按 `kind` 是固定的公共消息）、`source_view_fingerprint`（`source` 规范 JSON 的 SHA-256，只用来对齐显示文字）、`locale`、`resource_version`、`status`（`complete` / `partial` / `unavailable`）、`fields`、`metrics`。

外层的 `resource_version`：历史对象（审阅、确认画像、运行意图、进度、结果）是它绑定的版本，当场生成的对象（能力清单、会话、草稿、运行列表）是当前登记的版本（2026-10-25 收尾修复后的规则，见第 13 节）；每段文字实际用的版本看 `fields[].resource_version`。

`DisplayField`：`pointer`（相对 `source` 的 JSON Pointer）、`key`（稳定的模板键或 `entity.<种类>.<字段>:<编号>`）、`status`（`provided` / `missing`）、`text`（纯文字，缺失时 `null`）、`source_locale`（`zh`）、`missing_reason`（缺失时）、`resource_version`（这段文字用的资源版本）。

1. **业务状态只从 `source` 读**：数字、权重、编号、动作、确认 token、日期、C / E / M 都照 `source`，`fields` 只用于文字。`zh` 时 `fields[].text` 逐字等于 `source` 里的原文。
2. 显示某个文字位置：在 `fields` 里按 `pointer` 找；`provided` 用 `text`；`missing` 就显示 `source` 原文并标“英文说明尚未提供”，不要自己翻译，也不要拿中文去查另一份字典。缺失原因：`no_binding`（生成时没有记下绑定，或是升级前的旧对象）、`translation_missing`、`translation_stale`、`source_changed`、`ambiguous`、`untranslated_param`、`resources_unavailable`（这个对象记下的资源版本不存在）、`binding_stale`。
3. 用户自己写的文字（草稿里的自由填写、职业原文等）不翻译、不在 `fields` 里，照原样显示。
4. 修改请求（POST / PUT / DELETE）照旧走原接口；之后用显示出口只读刷新。错误与确认回复不必再读一次：直接用回复里的 `display.translations[locale].fields`（`source_view_fingerprint` 对的是去掉 `display` 之后的回复）。
5. 轮询时可以直接轮询 `GET /api/v1/display?kind=intent&id=…`：`source` 与文字同一次返回，不会把新进度与旧翻译拼在一起；读取不写库、不续期、不调用模型（与业务查询相同的保证）。
6. 未登录、会话失效、服务不可达时，用随包携带的固定双语错误目录 `contracts/product/display/v1/service-errors.json`（按 `code`，到期按对象）。动态业务问题只以服务回复里的 `display` 为准。
7. 结果色阶：`kind = result` 时 `metrics` 有一项 `MetricDisplaySpec`——`key = mean_j`、`minimum = "0"`、`maximum = "1"`、`unit = model_score`、`direction = higher_model_evaluation`、`denominator_field = mean_denominator`、`comparison_scope = within_result_only`、`evaluation_refs`（协议、评分、评价的公开引用）、`notes`（中英）。`minimum` / `maximum` 是合法数值域 [0, 1]，不是图例窗口；图例窗口按 ADR 0045 第 4 条在同一份结果之内适度放大（第 13 节）；超出 [0, 1] 是契约错误，不截断；C = 0 或没有评价不画最低分的颜色；有 M 时照常着色并标出缺失人数；缺失范围的分母是 C + M，不是置信区间；0.7 不是“现实里幸福 70%”，也不是排名。

### 12.4 显示资源从哪来

* 中文模板写在生成文字的代码里（`product.display.t`），输出与原来逐字相同；英文在 `configs/product/display/templates/*.yaml` 与 `entities/*`（按资料包），由 `scripts/product_display.py freeze` 冻结成不可覆盖的版本。**按实体编号、模板键与带类型的安全参数**（城市、职业、机会、片区、月份、金额、代码）组装，不按中文查英文；`ScopeNote.topic` 不单独当键；年度事实的 jobs / housing / food / training 等字符串数组在原投影处按机会编号、月份与公开事实记下绑定，页面不用拆中文。
* 私有绑定随所属对象保存（服务库里审阅、确认画像、运行意图的列，以及这次运行工作目录里的 `workflow/display/`），同一个所有者、同一个数据代号、同一个事务；删除、过期、草稿改修订、删除全部之后的迟到写回都一起管住。读取从不补写；升级前的旧对象显示为 `no_binding`。
* 历史对象按它记下的资源版本读（样例 120）；那个版本不存在就报 `resources_unavailable`，不回退到最新资源。
* 覆盖报告：离线联调的三条故事里（E3B 六城 × 每城 4 个分身 × 3 年；E3B 两城 × 每城 2 个分身；合成资料包），每种对象的中文都完整、英文全部提供：应展示 2,373 / 778 / 497 段，英文缺失 0（`runs/e3-e5-display-20261024/coverage.md`）。用户原文与识别别名照原文显示，不在分母里。这三条故事里没有出现、但结构上还会缺英文的：分身事件链里 E1 自己写成中文句子的记录（`/clones/*/event_chain/*`，要 E1 给结构化记录才能绑定，这一轮不改 `src/econ/`）；引用用户自己写的中文的说明（例如没认出的兴趣原文，报 `untranslated_param`）；只有测试夹具才会改的资料事实

### 12.5 地理清单

`GeographyDisplayBundle`：`display_schema_version`、`resource_version`、`pack_fingerprint`、`status`、`cities[]`、`sources[]`、`notes[]`（中英）。每座城：`city_ref`、`country`、`scheme`、`code`、`geography_version`、`country_names`、`names`（中英正式名与短名）、`model_kind`（`real` / `synthetic`）、`anchor_status`（`available` / `unknown` / `not_applicable`）、`anchor`（`latitude`、`longitude`，度；`crs = EPSG:4326`；`kind = metro_internal_point`；`source_refs`）、`support_scope`（`ref`、`version`，中英的居住、工作与说明）、`coverage_note`。

* 用哪个资料包：当前的看能力清单的 `meta.data_pack`；历史结果用那次结果 `source.meta.data_pack` 的指纹，并把显示读取回来的 `resource_version` 一起传进来，读到的就是那一版冻结的清单。省略 `resource_version` 用当前登记的版本（所以只有当前输入才省略；历史对象的版本为空时**不要请求**地理清单，见第 19 节）；明确请求的版本不存在或资料包没有登记：404，**不回退**；登记了但没有地理资源：`status = unavailable`、城市为空。
* 六城都是 `available`：Census 2024 Gazetteer CBSA 文件的都会区内部点（代表点，不是住处、主城市中心或都会区边界；都会区大，点可能离市区较远）。原始基准 NAD83（同一批内部点在官方 TIGER/Line 2024 CBSA 文件里也有，七位小数四舍五入到六位一致，那份文件的坐标系是 NAD83），按 EPSG 登记的 1188 号转换（三个平移量都是 0、精度 4 米）发布为 EPSG:4326，数值不变；边界版本是 2023 年 7 月的 CBSA 划定。合成城市 `not_applicable`、`anchor = null`。以后有城市没有可靠的点就是 `unknown`、`anchor = null`（样例 125）：仍在列表里、照常可选，不要用别的点代替，也不要画在地图上。
* 支持范围写明：纽约只支持纽约市五个区的居民、工作在纽约州；芝加哥只支持伊利诺伊州部分；旧金山都会区不含圣何塞（另一个都会区，CBSA 41940）。**清单不授予运行资格**：能选哪些城市只看能力清单与服务规则。
* 底图、投影、纹理、标签避让与拖转由 E5 负责；首版没有住宅、街道或 PUMA 多边形。生成器 `scripts/product_geography.py`（`build` / `check`），参数目录 PI-61。

### 12.6 样例（`contracts/product/v3/samples/`）

| 情形 | 样例 |
|---|---|
| 正常：运行意图的中英显示 | 105（zh）、106（en） |
| 画像日期可以重读 | 68（确认回复带 `profile_summary` 与 `display`）、107（本人画像列表）、108（单份画像） |
| 不允许的动作 | 112 / 113（付费关闭：没有 `submit`）、95（直接提交被拒）、109 / 110（恢复排队：没有 `cancel`）、111（直接取消被拒） |
| 即时错误 | 92（未登录，带中英 `display`）、114（确认被拒：审阅过时）；其他错误样例 65、75、85、88、91、93–96、99 同样带 `display` |
| 过期 | 117（确认画像 410）、118 / 119（过期意图的显示）、90 / 91（原有） |
| 部分结果与色阶 | 115（zh）、116（en） |
| 缺译文（受控夹具） | 122 |
| 历史版本（受控夹具） | 120（存下的说明按旧版本读）、121（明确请求不存在的版本：404，不回退） |
| 地理清单与缺坐标 | 123（E3B 六城）、124（合成城市：不给坐标）、125（缺坐标，受控夹具：芝加哥 `unknown`）、121（明确请求不存在的版本：404） |

样例都由服务端真实代码路径产生：合成人物、测试时钟、明确无效的测试凭证、离线 mock；“受控夹具”是故障注入或临时显示资源，不是仓库里登记的版本。

### 12.7 起服务、接线与服务库升级

* 起服务与建链接照第 3 节，命令不变。显示资源随仓库提交（`data/display/`、`configs/product/display/registry.yaml`），服务启动时不用另外准备；改了资源要运行 `uv run python scripts/product_display.py freeze`（新版本、旧版本保留），再 `check`。
* **服务库升级（v2 → v3）**：服务打开 v2 的库时在一个事务里只加显示绑定的空列（审阅、确认画像各一列，运行意图四列）、改版本号；草稿、审阅、确认画像、运行、次数事件、会话都保留，不要求删库重建，不改次数与留存政策。升级前的对象没有绑定：显示附层标 `no_binding`，业务读取照常。`tests/product_service/test_display_service.py::test_a_v2_store_is_upgraded_in_place_and_keeps_users_and_quota` 验证（v2 与 v3 的建表语句已与 `8ccec1c` 逐列核对：差别只有这六列）。升级之前照常停服务、备份 `service.sqlite`（服务本身不做长期备份）。
* 离线联调：`uv run python scripts/product_service.py e2e --out runs/<新目录>` 现在在结果之后还读每种对象的中英显示、地理清单与一个未登录错误，再删除这次运行与全部数据；`uv run python scripts/product_display.py coverage <summary.json> --out <报告.md>` 生成覆盖报告。**`--out` 放在仓库的 `runs/` 下**：Windows 的路径长度上限是 260 个字符，放在很深的临时目录里，运行工作目录里的文件名会超长。

### 12.8 已验收与还没有的

* 已实现并离线自测：离线全量测试 1251 条通过、0 失败、0 跳过（干净工作树：HEAD 加本轮文件，`uv run pytest -n auto`）；ruff check / format、`check-schema`（33 份 schema、122 份样例）、`check-openapi`、`scripts/product_display.py check`（0 处不一致、0 个 key 缺英文）、`scripts/product_geography.py check` 都通过；R3/R4 的原审核探针 5 条通过。三条本地 mock 服务故事从专属链接走到删除（运行删除 202 → 结果 410 `deleted`，删除全部之后没有运行）：E3B 默认规模 6 城 × 每城 4 个分身 × 3 年、20 元——22 C / 0 E / 2 M，两个 M 都是芝加哥已登记的期末估值缺失（与之前相同）；E3B 两城 × 每城 2 个分身、5 元——3 C / 1 M（同一原因）；合成资料包——2 C。三条故事里每种对象的中文完整、英文全部提供。定向测试：`tests/product_service/test_e5_service_boundaries.py`、`test_display_service.py`，`tests/product/test_display_*.py`（框架、资源、各层文字、地理）。本机证据 `runs/e3-e5-display-20261024/`
* 待独立复核：本节全部内容。
* 还缺资源或接线：E5 还没有接线（页面、显示 adapter、地图与地球上的城市点）；结果色阶的口径要所有者裁定（这一轮按任务写的是固定 [0, 1]、不拉伸，ADR 0045 第 4 条记的是界面适度放大，见 [交接单](e5-bilingual-map-service-requests.md) 第 13 节）；分身事件链里 E1 的中文记录没有英文；交接单第 12 节第 1–5 条（路径过长的启动检查、审阅里的内部键名、知悉项文字、作答状态的结构、模型语言）这一轮没有做

## 13. E3 → E5 收尾修复（2026-10-25：独立复核 F1–F3、四项小接口问题、色阶统一）

状态：**修复及离线自测完成，待独立复核**。复核报告在本机 `runs/e3c-display-review-20261025/README.md`；修复证据在 `runs/e3-e5-review-fixes-20261025/`。没有真实或订阅模型调用、没有在线翻译、没有部署、没有推送；本轮没有改 `web/`。

### 13.1 版本与迁移

| 层 | 新版本 | E5 要做的 |
|---|---|---|
| 显示附层 | `e3-display-v2`（`DisplayEnvelope` / `DisplayAttachment` / `GeographyDisplayBundle` 的 `display_schema_version`） | 按提交的 schema 重新生成类型；`missing_reason` 新增 `resources_invalid`、`binding_unverified`；`DisplayEnvelope` 新增 `acknowledgements`、`answer_states`；新模型 `DisplayText`、`DisplayAcknowledgement`、`DisplayAnswerState` |
| 服务 API | `e3c-service-api-v1.2`（URL 仍 `/api/v1`） | 路由、成功码与业务回复不变 |
| 核心公共结构 | `e3a-product-schema-v3.3`（不变） | 无 |
| 显示资源 | 合成包 `e3-display-res-v1-a7813e49b0e3fbdc`，E3B 包 `e3-display-res-v1-bf20cbd7db94ecb8`（当前）；上一轮的 `…0c6e23d0ce146d9a`、`…43b216694e20aa77` 保留 | 无（服务读冻结资源） |

旧数据：上一轮保存的显示绑定是按中文配上的，读取时报 `binding_unverified`（英文缺失，页面照中文原文显示），不重算、不补写；业务读取不受影响。审阅说明的中文措辞改了（第 13.4 节），保存过的旧审阅与确认画像不改写。

### 13.2 三项复核问题修好之后的规则

1. **显示故障不破坏业务回复**（F1）：资源校验没通过、登记表读不出来时，显示字段报 `resources_invalid`（缺版本报 `resources_unavailable`），原回复、HTTP 状态码与中文三句不变；未登录照样是 401 的安全 JSON；确认已经保存就回 200 `confirmed`、只保存一次；即时回复的附层生成出问题时 `display` 为空（照中文原文显示）。地理清单那一版资源坏了：200、`status = unavailable`、`notes` 写明原因。
2. **绑定按位置与实体身份**（F2）：同名的两个实体（例如两座城的“文员”）各有自己的 `key`，英文不同也各自正确；数组换顺序不串。`key` 是可靠的显示键，但业务身份仍然只看 `source`。
3. **历史正文用自己的版本**（F3）：当场的服务说明不接管内嵌的画像、预检查、进度、结果或停下的说明。`DisplayEnvelope.resource_version`：
   * 审阅、确认画像、结果：这个对象生成时绑定的版本；
   * 运行意图、进度：这次运行预检查时绑定的版本（同一次运行的进度与结果都用它）；**还没有绑定时为空**（例如还在排队检查）；
   * 能力清单、会话、草稿、运行列表：当前登记的版本。

   每段文字实际用的版本在 `fields[].resource_version`（当场说明用当前版本时写在那个字段上）。**地图读历史结果时，把那份结果显示读取回来的 `resource_version` 传给 `GET /api/v1/display/geography`**；它是空的就**不要请求地理清单**（不带版本请求读到的是当前版本，等于拿当前版本冒充），按“没有绑定的历史版本”处理（第 19 节）。明确请求的版本不存在仍是 404，不回退。

### 13.3 `acknowledgements` 与 `answer_states` 怎么读

**知悉项说明**（只在 `kind = review`）：

```text
acknowledgements[i] = {
  ack_id,                # 就是 source.review.pending_confirmations[i]，顺序相同、一一对应
  text: {key, zh, en, status, missing_reason, resource_version},
  statement_pointer      # 留存的陈述（ack:retained:<ref>）指向 source 里那条陈述，例如 /review/statements/2；其他为 null
}
```

* 页面按 `ack_id` 显示 `text`（当前语言是英文就用 `en`；`status = missing` 时显示 `zh` 并标明英文缺失；旧审阅没有保存时 `zh` 也为空，只显示编号对应的提示）。
* **确认时照旧提交 `pending_confirmations` 的编号**（`ProfileConfirmRequest.acknowledged`）。这些条目只是说明：不新增、不删除、不代替任何必须知悉的事项，也不表示已经勾选；页面不要自动勾选。
* 文字与确认被拒时“还没有确认：……”同一个来源（`product.answers.ack_display`）。

**作答状态**（`kind = capabilities`、`draft`、`review`）：

```text
answer_states[j] = {
  answer: leisure | taste,
  state,                 # 正式状态：leisure 的 unanswered / unsure / no_fixed_hobby / generic / described，taste 的 unanswered / unsure / no_preference / described
  name: {key, zh, en, status, missing_reason, resource_version},
  meaning: {…同上}
}
```

* 按 `(answer, state)` 找，不按顺序、不拆 `leisure_states_zh` 的冒号；`leisure_states_zh` / `taste_states_zh` 这两个业务字段照旧（内容不变），页面不再需要解析它们。
* 审阅里的作答状态用这份审阅绑定的版本，能力清单与草稿用当前版本。

### 13.4 审阅说明与启动诊断

* 兴趣与口味的审阅说明改成人能读的写法：例如口味写“喜欢川菜”（E3B 资料包）时是“口味与菜系：待选中——候选：中餐；有歧义：无；没认出：无”，没有 `['chinese']`、`[]` 之类的程序写法；类别编号（`candidates`）、匹配结果、用户原文与确认规则不变。
* 数据目录路径过长：服务启动时（建服务库与密钥之前）检查；Windows 没有开启长路径支持时数据目录不能超过 133 个字符，超过就报“数据目录路径过长，请改用较短目录”并停止启动（不建任何文件、不改系统设置）。开了长路径支持或不是 Windows 不限制。网页上看不到这条（服务根本没有启动）。

### 13.5 色阶已经统一

按 ADR 0045 第 4 条：`MetricDisplaySpec.minimum` / `maximum` 是合法数值域 [0, 1]，不是图例窗口；图例窗口由 E5 在同一份结果之内适度放大（同一结果的所有国家、两种地图共用一个窗口，图例写明两端，不暗示最低的城市是零分、最高的是满分）。C = 0 或没有评价不着最低分的颜色、M 单独标、均值分母与“只在本结果内比较”不变；E3 不另写色阶计算器，分数与比较结论不变。`notes` 已经改成这条规则（样例 115、116）。

### 13.6 样例

| 情形 | 样例 |
|---|---|
| 审阅的知悉项说明与作答状态（新） | 126 |
| 能力清单的作答状态（新） | 127 |
| 显示资源坏了：未登录错误仍是安全 JSON，英文 `resources_invalid`（受控夹具，新） | 128 |
| 显示附层 v2 重新导出（版本字段、历史版本语义、色阶说明） | 105–125 |
| 审阅说明改成可读写法 | 39、42、43、49（只是说明文字；编号、匹配结果与原文不变） |

### 13.7 已实现、待复核与还没有的

* 已实现并离线自测：离线全量测试 1271 条通过、0 失败、0 跳过（干净工作树：`32d7c21` 加本轮文件，`uv run pytest -n auto`）；ruff check / format、`check-schema`（33 份 schema、125 份样例）、`check-openapi`、`scripts/product_display.py check`（0 处不一致、0 个 key 缺英文）、`scripts/product_geography.py check` 都通过；独立复核的 5 条原反例与 R3/R4 原探针共 10 条通过；新的定向测试 `tests/product_service/test_e5_review_fixes.py`（18 条）与改写后的 `tests/product/test_display_framework.py`。六城本地 mock 服务闭环（同一冻结资料包、6 城 × 每城 4 个分身 × 3 年、20 元）：24 人完成三年，22 C / 0 E / 2 M，两个 M 都是芝加哥已登记的期末估值缺失（与基线相同），规划 104 / 216，次数 3 / 1 / 0 / 2，真实模型调用 0；九种显示对象中英完整，删除运行 202 → 结果 410，删除全部后没有运行。本机证据 `runs/e3-e5-review-fixes-20261025/`
* 待独立复核：本节全部内容。
* 还没有做：模型输入 / 输出语言（新功能，等所有者排期）；分身事件链里 E1 自己写成中文句子的记录（要 E3AB 给结构化事实绑定）；E5 接上新的 `acknowledgements` / `answer_states` 与历史版本规则；部署与真实模型试跑。

## 14. E3 输入体验收敛与 F3 遗留（2026-10-25）

状态：**实现及离线自测完成，待独立复核**。基线 `a851db3`，本轮改动是它之上的一个提交（只有本轮的文件，没有推送）；本机证据 `runs/e3-input-closeout-20261025/`。规则见
[ADR0046](../../DECISIONS/0046-e3-input-catalog-and-structured-answers.md)。主链不变：原始答案 → 审阅 → 用户确认画像 → 运行前说明 →
确认开始 → LangGraph → E1 → 产品结果。E3 定义目录、换算与语义；E5 只读接口、做页面，不自己做业务换算。

### 14.1 版本

| 项 | 现在 | 说明 |
|---|---|---|
| 共享结构 | `e3a-product-schema-v3.4` | 仍导出到 `contracts/product/v3/`（34 份 schema，新增 `DraftMigrateRequest`）。`ProductMeta.schema_version` 同时接受 v3.3：只为读回旧草稿、旧画像、已有运行与历史结果 |
| 问卷 / 词表 / 转换 | `e3a-questionnaire-v0.4-draft` / `e3a-vocabulary-v5` / `e3a-conversion-v3` | 新的审阅与确认必须用这一版；旧草稿先迁移（第 14.6 节） |
| 服务 API | `e3c-service-api-v1.3` | URL 前缀不变；新增 `POST /api/v1/drafts/{id}/migrate` |
| 显示附层 | `e3-display-v2`（不变） | 冻结了新的资源版本：合成 `e3-display-res-v1-10a01abc4052089a`、E3B `e3-display-res-v1-ea5466ac2d62ebec`；旧版本保留，历史对象照旧按它们绑定的版本读 |
| 健康问卷、E1 协议、评分 | 不变 | 没有改 E1 生活数学，没有重建六城资料包（资料包内容身份 `48eda959…` 不变） |

### 14.2 用户怎么填（新页面读 `capabilities.input_catalog`）

1. **职业**：按 `groups`（`order`）浏览 `occupations`（`order`），或在完整目录里按 `search` 的规则筛选（第 14.4 节）；用户点一个职业就提交
   `occupation = {state: "provided", mode: "catalog", selected_candidate: <concept_id>, catalog_version, catalog_fingerprint: <input_catalog.content_fingerprint>}`。
   **不带文字**：`raw_text` 必须为空（带了保存就 422），搜索框里的字不保存。审阅页显示 `review.occupation.candidates[0]` 的名称与职责供核对。
   文字描述（`mode: "text"`）只为兼容保留，不是新页面的必经入口。
2. **最高学历（单选）**：`education = {state: "provided", option_id}`，选项 `input_catalog.education`（高中以下、高中、读过大学但未取得学位、
   副学士、本科、硕士、博士或专业学位）；也可以 `unanswered` / `unsure`。职业证书不在这里。
3. **职业证书（多选）**：`certificates = {state: "provided", selected: [ability_id…]}`，选项 `input_catalog.certificates`（E3B 三张；每张写了
   要求它的职业）；**`selected: []` 表示明确“没有”**，与 `unanswered` 分开。
4. **相关经验（只在需要时问）**：选中职业的 `experience_question` 不为空时，显示 `input_catalog.experience` 里那一题的 `prompt` 与 `related`
   职业，提交 `experience = {state: "provided", entries: [{concept_id, years}]}`（整年数）；`entries: []` = 没有相关经验。年数门槛只写在
   `prompt` 里，判断在 E3。
5. **六维与物质**：照旧 `AllocationAnswer` / `PointEntry`。主动均分提交 `mode: "equal_split"`（服务端生成六维每项 50/3、物质每项 25）；
   滑块提交精确值：十进制或规范分数（均分后移动 5 点就是 `65/3`、`35/3`），显示的四舍五入不回写；合计由服务端重新核对，不按比例
   调整别的维度；物质父项为 0 时不用填四项。
6. **兴趣与口味（可跳过）**：面板 `input_catalog.leisure` / `taste`（`groups` + `entries`，成员都是能力清单词表里已有的键）。多选提交
   `{state: "selected", selected: [key…]}`（至少一个、不带文字）；一个都不选就是未答（`unanswered`）；“暂不确定”“没有固定爱好 / 没有口味
   偏好”“随当地体验”（`generic`）单独选，与选中的类别同时提交会被拒绝。`search_notes` 是搜索说明：“川菜”只找到“中餐”并说明本版不区分
   地方菜，“徒步”“游戏”找不到类别（本版没有，不会算成健身）。
7. **不再出现**：硬要求、“品牌、菜系等属性偏好”、兴趣与口味的文字解释（`sections.hard_requirements / attribute_preferences / interest_text`
   都是 `false`）。补充文字照旧，只供分身做计划参考，不改点数、现金、资格、兴趣类别，也不会变成硬要求。

### 14.3 系统怎么换算（全部在服务端）

* 审阅：`review.occupation.status = "catalog_selected"`；`review.qualifications` 给出来源（`structured_answers` / 兼容的 `ability_claims` /
  `none`）、按回答换算出的资格与理由（`granted[].because`）、选中职业的开局岗位要求与是否满足（`requirements[].met`）、要问的经验题
  （`ask_experience`）；`review.abilities` 是开局持有的资格。
* 规则（`confirmed_experience_mapping_v1`，与 E3B 编译时的 `Scope.from_confirmed` 相同，测试按冻结资料逐项核对）：学历“至少达到”——本科
  满足高中、读过大学、副学士、学士四级门槛，但不授予职业证书；证书只授予选中的那张；经验看相关职业与年数（例如在“餐厅服务员”做过 2 年
  满足餐厅厨师的“至少 1 年”）。职业、职称、自由文字与搜索词都不授予资格；未答、暂不确定不授予。
* 新的学历 / 证书 / 经验回答与旧的逐项资格确认（`abilities`）同时存在、结果不同：`qualification_conflict`，不取并集。
* 开局岗位要求不满足：`qualification_requirement_unmet`（warning，仍可确认；各城的开局会照实挡下，不补资格、不换职业）。
* 确认画像：`occupation.match_status = "catalog_selected"`、`raw_text = null`、带目录版本与内容身份；`qualifications` 记下原始回答与规则；
  兴趣 `confirmed`；`hard_requirements` 与 `retained` 都是空的。

### 14.4 目录字段与搜索规则

`InputCatalog`：`catalog_id`、`catalog_version`、`content_fingerprint`、`pack_id`、`sections`、`search`（`method`、`rules`、`no_match`）、
`groups`（`group_id`、`label`、`order`）、`occupations`（`concept_id`、`group_id`、`order`、`label`、`duties`、`search_terms`、
`qualification_refs`、`experience_question`）、`education`、`certificates`、`experience`（`question_id`、`ability_id`、`applies_to`、`related`、
`prompt`）、`qualification_states`、`leisure` / `taste`（`groups`、`entries`（`key`、`group_id`、`order`、`label`、`search_terms`）、
`search_notes`（`note_id`、`terms`、`points_to`、`note`）、`note`）、`notes`。名称、职责、分组与说明有中英文显示（显示附层），搜索词照原文、
不翻译，也不在显示文字里；职业代码不在目录里。

搜索（`catalog_search_nfkc_casefold_all_terms_v1`，参考实现 `product.input_catalog.search_occupations` / `search_panel`）：

1. 查询与搜索词都做 Unicode NFKC（全角半角统一，全角空格也算空格）、`casefold`（英文不分大小写）、去首尾空白；
2. 按空白切成几个词，**每个词都要命中**：一个词出现在这个条目的任意一个搜索词里就算命中；
3. 只筛已发布的条目，不生成新条目；结果按目录顺序；空查询给出（当前分组的）全部条目；
4. 找不到就显示 `search.no_match`（说明支持范围：本版只有目录里的 40 种职业，教师、护士、医生等不在范围），不自动换职业；
5. 兴趣与菜系同样筛 `entries[].search_terms`，另外把命中的 `search_notes` 的说明显示出来。

| 查询 | 结果（E3B） |
|---|---|
| `程序员`、`ＰＲＯＧＲＡＭＭＥＲ`、`Software Developers` | 软件开发工程师 |
| `软件 工程师` | 软件开发工程师（两个词都命中） |
| `软件 厨师`、`教师`、`护士` | 没有（显示 `no_match`） |
| 空查询 + 分组 `it_tech` | 电脑用户支持专员、网络支持专员、软件开发工程师、计算机系统分析师（固定顺序） |
| 菜系 `川菜` | 中餐 + 说明 `taste.chinese_regional`（不单独记成川菜偏好） |
| 兴趣 `徒步` / `打游戏` | 没有类别 + 说明（本版没有，不算成健身） |
| 兴趣 `跑步` | 健身 |

服务端只认提交的编号与目录身份：未知编号 `occupation_selection_invalid`，目录换了 `occupation_catalog_expired`，资料包没有目录
`occupation_catalog_unavailable`，目录模式带文字 422 `invalid_request`。

### 14.5 错误与缺失

| 代码 | 在哪 | 意思 |
|---|---|---|
| `occupation_selection_invalid` / `occupation_catalog_expired` / `occupation_catalog_unavailable` / `occupation_missing` | 审阅（error） | 编号不在目录、目录过期、资料包没有目录、还没选 |
| `education_option_unknown`、`certificate_unknown`、`certificate_duplicated`、`experience_occupation_not_related`、`experience_duplicated`、`qualification_section_not_offered`、`input_catalog_unavailable` | 审阅（error） | 资格回答不合法（学历门槛不能当证书选；合成资料包不问学历） |
| `qualification_conflict` | 审阅（error） | 新回答与旧的逐项资格确认结果不同 |
| `qualification_requirement_unmet` | 审阅（warning） | 选中职业的开局岗位要求还不满足 |
| `interest_state_conflict`、`interest_unknown_category`、`interest_duplicate_selection` | 审阅（error） | 选了类别又选“不确定 / 没有偏好”；词表外的类别；重复 |
| `removed_section_present` | 审阅（error） | 草稿里还有新问卷不再收集的硬要求或属性偏好（逐条列出） |
| `version_changed` | 审阅（error） | 旧版本的草稿：先迁移 |
| `migration_consent_required` / `migration_not_needed` | 服务错误 409 | 迁移没有同意 / 不需要迁移 |
| `invalid_request` | 服务错误 422 | 目录模式带文字、`selected` 状态没有类别或带文字等形状错误 |

英文缺失照旧按显示附层的原因报（`translation_missing`、`resources_unavailable`……）；新的目录文字在两个资料包的当前资源里都有英文。

### 14.6 旧草稿迁移（`DraftMigration`）——已去掉，见第 20 节

* `GET /api/v1/drafts/{id}` 的 `migration`：草稿的版本不是当前的、或里面还有硬要求 / 属性偏好时给出——`from_ / to_questionnaire_version`、
  `version_changed`、`removed`（逐条：`section`、`ref`、可读的 `summary`、用户原文 `raw_text`，原文不翻译）、`consent_required`、`message_zh`、
  `effects_zh`。读取不改写草稿。
* 用户看过之后点“迁移”：`POST /api/v1/drafts/{id}/migrate`，`DraftMigrateRequest {expected_revision, clear_removed}`。有要清空的答案时
  `clear_removed` 必须为 `true`，否则 409 `migration_consent_required`、草稿不变；修订号不是当前的 409 `revision_conflict`；已经是当前版本
  409 `migration_not_needed`。成功：版本换成当前、清空硬要求与属性偏好、其他答案原样保留、修订号 +1；之后重新审阅与确认。
* 不改写已经确认的画像、已经开始的运行与历史结果（它们用自己保存的副本；历史结果里的硬要求报告照旧显示）。旧版本的画像能读回，但不能
  直接准备新模拟（`preflight_stale`），要用新问卷重新确认。

### 14.7 F3：一次运行一版解释

同一份运行前说明身份的显示绑定只在第一次检查时写；确认开始前的再次核对、停下后的恢复都不重绑，进度、结果与运行意图（运行前说明部分）
继承这次运行已经确认的版本，之后发布的新资源不接管。绑定缺了或坏了：结果显示照实缺失（`unavailable`），不拿当前资源补；它记下的旧资源
被删或被改：`resources_unavailable` / `resources_invalid`，不回退到当前版本。业务输入真的变了，运行前说明身份就不同，照旧要重新检查与确认。

### 14.8 样例（`contracts/product/v3/samples/`，129–154 新增）

| 情形 | 样例 |
|---|---|
| E3B 输入目录（能力清单）/ 英文显示 | 129 / 130 |
| 新流程：草稿 → 审阅 → 英文显示 → 确认 | 131 → 132 → 133 → 134 |
| 资格：开局要求不满足（warning）/ 餐厅厨师的经验 / 与旧逐项确认冲突 | 135 / 136 / 137 |
| 职业：目录里没有的编号 / 目录过期 / 目录模式带文字（422） | 138 / 139 / 140 |
| 口味：没有偏好又选了中餐 | 141 |
| 定和分配：均分 / 均分后移动点数 / 零值 / 未分配满 / 超额 / 父项为 0 | 142 / 143 / 144 / 145 / 146 / 147 |
| 旧草稿：迁移说明 / 英文显示 / 迁移前审阅 / 迁移请求 / 没有同意 / 迁移后 / 不需要迁移 | 148 / 149 / 150 / 151 / 152 / 153 / 154 |
| 其余样例的变化 | 26 份新增（129–154）；01–128 里 7 份只有版本字段变化，其余是版本、内容身份与编号、新字段（输入目录、资格审阅、目录模式）、新的显示资源版本，以及去掉默认的 60 分钟通勤硬要求（新流程的结果因此变化：合成正常样例 10 的 J 从 0.6179 / 0.6204 变成 0.6056 / 0.6081，E3B 样例 104 里洛杉矶、芝加哥、西雅图变低；C / E / M 不变）。17、38、39、126、127 改了输入或说明（不再有属性偏好与两条通勤上限）；23 与 55 改用“历史画像”的受控夹具（画像带着旧问卷的往返 60 分钟上限），结果与原来相同 |

### 14.9 自测与没有验证的

* 离线自测：离线全量测试 1361 条通过、0 失败、0 跳过（干净工作树：`a851db3` 加本轮文件，`uv run pytest -n 8`，12 分 35 秒）；ruff check / format、`check-schema`（34 份 schema、151 份样例）、`check-openapi`、`scripts/product_display.py check`（0 处不一致、0 个 key 缺英文）、`scripts/product_geography.py check` 都通过；复核的原探针原样重跑 11 条通过（显示 5 条、R3/R4 5 条、F3 遗留 1 条）；新的定向测试 `tests/product/test_input_catalog.py`（目录结构、搜索规则，以及与 `Scope.from_confirmed` 逐项相同）、`test_structured_answers.py`、`test_draft_migration.py`、`tests/product_service/test_input_closeout.py`、`test_bound_display_version.py`（F3：修复前 5 条失败）。只有 F3 修复的六城 mock 闭环（旧输入）与复核基线逐项相同：22 C / 0 E / 2 M（两个 M 是芝加哥估值缺失）、规划 104 / 216、真实模型调用 0；新流程的六城 mock 闭环（按目录选办公室文员、最高学历高中、证书“没有”、没有硬要求）：24 人完成三年，22 C / 0 E / 2 M，规划 104 / 216，真实模型调用 0，九种显示对象 2,530 段英文全部提供，删除运行 202 → 410。生活数学、冻结资料与资料包没有改动。本机证据 `runs/e3-input-closeout-20261025/`
* 待独立复核：本节全部内容。
* 还没有做 / 本版限制：E5 页面没有接这些字段（目录浏览与搜索、资格题、面板、迁移对话框都要 E5 做）；目录的分组、搜索词与经验问题是人工
  维护的数据，没有做用户访谈或可用性测试；只支持资料包里已有的 40 种职业（E3B）、5 类兴趣与 10 种菜系，徒步、游戏等是待办；学历门槛是
  BLS EP 的全国典型入职要求，不是具体雇主的招聘条件；mock 通过不代表真实用户或真实模型的效果；没有部署、没有真实模型试跑。

## 15. 旧画像升级兼容修复（2026-10-25）

状态：**修复及离线自测完成，待独立复核**。独立复核（本机 `runs/e3-input-independent-20261025/`）发现 R1：v3.3 的确认画像读进新版之后
被补上 `qualifications`、`occupation.catalog_version`、`catalog_fingerprint` 等空字段，这些字段进了身份计算——没有改过的旧画像被判为身份
不符，已经开始的旧运行不能恢复，旧画像（18 段）与旧审阅（52 段）的英文显示为 `binding_stale`。本节是修复；基线 `7efc3ce`，修复是它之上的
一个提交（只有本轮的文件，没有推送）。规则见 [ADR0046](../../DECISIONS/0046-e3-input-catalog-and-structured-answers.md) 第 9 条。

* **公共字段没有变**：34 份 schema 与 OpenAPI 逐字节不变，样例不变，显示资源不变。**E5 无须补字段、重算身份或猜译文。** 只有一处读到的
  形状回到了原样：v3.3 的草稿、审阅与确认画像（`meta.schema_version = e3a-product-schema-v3.3`）不再带 v3.4 新增的键（这些键在 schema
  里本来就是可省略、有默认值的）；v3.3 对象里不能出现 v3.4 才有的内容（例如目录选中的职业、`selected` 状态），带了就当作非法输入拒绝。
* **旧运行**：旧版代码开始、在第 3 次规划停下的运行，新版打开同一个服务库后恢复并完成；已存的 3 次规划回复不重发（共 6 次，前 3 条
  原样），次数一共只用 1 次；结果与进度的英文按这次运行原来绑定的资源版本完整显示。旧画像仍不能准备新模拟（409 `preflight_stale`）。
* **旧显示**：旧画像、旧审阅、运行意图的英文按原绑定与原资源版本完整显示，读取不写库；旧资源被删或被改照实报 `resources_unavailable` /
  `resources_invalid`。真正改过的旧画像仍被拒绝（`confirmation_tampered`，运行里的副本被改就不能恢复）。
* **给 E5 的受控夹具**：`tests/fixtures/v33_objects/` 是旧版服务真实写下的草稿、审阅、确认画像与它们的显示绑定（合成人物，没有凭证），
  可以给迁移的浏览器测试用（交接单第 17 节说还缺能造旧版本草稿的夹具）。
* 自测：离线全量测试 1385 条通过、0 失败、0 跳过（干净工作树：`7efc3ce` 加本轮文件，`uv run pytest -n 8`）；新的 `tests/product/test_legacy_objects.py`（25 条，修复前 20 条失败）与 `tests/product_service/test_legacy_rows.py`；复核探针原样重跑 14 条通过（显示 5、R3/R4 5、F3 遗留 1、R1 身份 / 读取 3）；ruff、`check-schema`（34 份 schema、151 份样例）、`check-openapi`、显示与地理核对都通过；两进程升级探针（旧版 `a851db3` 开始并停下 → 新版恢复）的修复前后对照在本机证据 `runs/e3-compat-fix-20261025/`

## 16. 独立评分（2026-10-25，共享结构 v3.5）

状态：**实现及离线自测完成，待独立复核**。基线 `6c01e79`；所有者 2026-10-25 的“E3 输入与服务体验调整”按功能分三个提交（独立评分 /
一次开始 / 进度摘要），本节是第一个（只有这一项的文件，没有推送）。规则见 [ADR0047](../../DECISIONS/0047-e3-independent-ratings.md)。
主链不变：原始答案 → 审阅 → 用户确认画像 → E1 只读 `points`。E3 定义换算与状态，E5 做控件；页面可以按公开规则先算一个**标明为临时**的
预览，正式的权重以审阅为准。

### 16.1 版本

| 项 | 现在 | 说明 |
|---|---|---|
| 共享结构 | `e3a-product-schema-v3.5` | 仍导出到 `contracts/product/v3/`（34 份 schema）。`ProductMeta.schema_version` 同时接受 v3.3、v3.4：只为读回历史对象，它们照原样写出、身份不变（第 16.6 节） |
| 问卷 / 词表 / 转换 | `e3a-questionnaire-v0.5-draft` / `e3a-vocabulary-v5`（不变）/ `e3a-conversion-v4` | 新的审阅与确认必须用这一版；旧草稿先迁移 |
| 服务 API | `e3c-service-api-v1.4` | 本提交起：`DraftMigrateRequest.allocation_choice`、错误 `migration_choice_required`；一次开始与进度摘要在后两个提交里加 |
| 显示附层 | `e3-display-v2`（不变） | 新冻结的资源版本：合成 `e3-display-res-v1-8fae36bca51b48ea`、E3B `e3-display-res-v1-ff3e9e8264d8b665`；旧版本保留 |
| 输入目录、健康问卷、生活协议、评分 | 不变 | 没有改 E1 生活数学，没有重建六城资料包（`48eda959…` 不变） |

### 16.2 用户怎么填（读 `capabilities.points.ratings`）

1. 六维一组、物质四项一组，**各自独立**换算。每项一个 0–100 的原始评分：滑条步长 1（`slider_step`），也可以输入数字精调，最多两位小数
   （`max_decimals`）；不必凑满 100，改一项不影响别的项。
2. 提交：`six_dimensions = {mode: "ratings_normalized_v1", entries: {<key>: PointEntry}}`（物质同样）。`PointEntry` 照旧：
   `{state: "provided", value_text: "80"}` 是评分（写十进制；`"50/3"` 这种分数写法不是评分，会被拒绝）；`unanswered` / `indifferent` /
   `unsure` 是对应状态。**未答不是 0**，不预填任何值；明确的 0 是回答。
3. “同等重视”按钮：用户主动把这一组每项设成 50（`equal_weight_rating`），提交的就是这些 50；没有单独的模式。
4. 物质父项评 0：四项不适用（审阅 `material.status = "not_applicable"`），页面可以收起四项，但**不要清掉**草稿里已有的四项评分；父项
   重新大于 0 时原样恢复（不清零、不均分）。
5. 页面可以先显示临时的权重预览（必须标明是临时的，未答的不能画成 0 权重）；正式权重以审阅的 `lines[].exact` 为准。

### 16.3 系统怎么换算（服务端，精确分数）

* 要回答的项都回答了、原始合计大于 0：权重 = 100 × 评分 ÷ 原始合计。例：80/40/40/20/10/10（合计 200）→ 40/20/20/10/5/5；同比例的
  8/4/4/2/1/1 得到同样的权重；六维全 50 → 每项 `50/3`（显示 16.7）；物质四项全 50 → 每项 25。不加系数，不按职业、文字或消费能力调整。
* 审阅 `AllocationReview`：`mode`、`rule = "ratings_normalized_v1"`、`status`、`rating_total`（已答各项的原始合计，`{exact, display}`）、
  `total`（权重合计 100，完整时才有）；每行 `rating_exact` / `rating_display` 是原始评分，`exact` / `display` 是权重（没回答完时为空）。
  显示的四舍五入只供阅读，不回到计算。
* 确认画像 `ConfirmedAllocation`：`mode`、`rule`、`ratings`（原始评分，精确写法）、`rating_total`、`points`（最终权重，E1 只读它）、
  `display`。物质在 `material.allocation` 里同样。

### 16.4 错误（审阅里，`severity = error`）

| 代码 | 意思 |
|---|---|
| `ratings_all_zero` | 这一组全部是 0：“至少有一项大于 0，或主动选择同等重视”；不自动均分、不出权重 |
| `rating_out_of_range` / `rating_not_decimal` / `points_too_many_decimals` / `points_not_a_number` | 超过 100 / 写成分数 / 超过两位小数 / 不是数 |
| `allocation_unanswered` / `allocation_indifferent` / `allocation_unsure` | 还有项没给评分（不换成 0、不均分） |
| `allocation_mode_retired` | 用了旧的百分比分配（`manual` / `equal_split`）：这一版问卷不能用它确认 |

### 16.5 旧草稿迁移——已去掉，见第 20 节

* `DraftMigration` 新增 `allocations`（逐组照原样列出旧分配：`group`、`mode`、`lines[]` 的 `key`、`label`、`state`、`value_text`；
  `equal_split` 的各项写成当时的均分值）与 `allocation_choice_required`。旧百分比不是独立评分，系统不换算、不改读。
* `POST /api/v1/drafts/{id}/migrate` 的 `DraftMigrateRequest` 新增 `allocation_choice`：`rerate`（这两组改成独立评分、各项未答，用户重新
  打分）或 `equal_weight`（每项评分 50）。有旧分配时必须选，否则 409 `migration_choice_required`、草稿不变；有要清空的旧答案时先要
  `clear_removed = true`（否则仍是 `migration_consent_required`）。其他答案原样保留。
* 已经确认的旧画像、已经开始的运行与历史结果不改；旧画像仍不能直接准备新模拟（`preflight_stale`）。

### 16.6 旧对象

v3.3 与 v3.4 的草稿、审阅与确认画像照原样读回、照原样写出（没有 v3.5 的字段），内容身份、草稿指纹与显示绑定都与当初相同；旧对象里
出现 v3.5 才有的内容（例如 `ratings_normalized_v1`）就当作非法输入拒绝（`tests/fixtures/v34_objects/` 是 v3.4 真实导出的四份）。E5 现有
的草稿样本（`web/tests/fixtures/drafts/`，v3.4 的百分比）照原样审阅：旧分配按原义，同时带 `version_changed` 与 `allocation_mode_retired`
阻断；按 v3.5 重新生成后，`tests/product/test_web_drafts.py` 里对应的几条要换成独立评分的例子。

### 16.7 样例（`contracts/product/v3/samples/`）

| 情形 | 样例 |
|---|---|
| 草稿（原始评分 80/40/40/20/10/10）→ 审阅（合计 200、权重 40/20/20/10/5/5）→ 英文显示 → 确认画像（`ratings` + `points`） | 155 → 143 → 161 → 160 |
| 同等重视（六维 50/3、物质 25）/ 明确 0 / 全零拒绝 / 超过 100 / 同比例缩小 | 142 / 144 / 145 / 146 / 156 |
| 物质父项为 0（四项不适用、答案留着）→ 父项改回 30（四项恢复，饮食仍未答） | 147 → 158 |
| 当前版本草稿用旧百分比（照原义审阅 + `allocation_mode_retired`） | 157 |
| 旧草稿：迁移说明（`allocations`）/ 英文 / 迁移前审阅 / 迁移请求（`allocation_choice = rerate`）/ 没同意 / 没选择（409） / 迁移后 / 不需要 | 148 / 149 / 150 / 151 / 152 / 159 / 153 / 154 |
| 其他 | 12、13、14 改成独立评分的写法（主故事 30/20/20/10/10/10 正好合计 100，权重与 E1 输入不变）；名称改了的：143（原“均分后移动”）、145（原“差 1 分”）、146（原“多 1 分”）——这三种是旧百分比才有的情形 |

### 16.8 自测与没有验证的

* 离线自测（干净工作树：`6c01e79` 加本提交的文件）：全量离线测试 1400 条通过、0 失败、0 跳过（`uv run pytest -n 8`，13 分 53 秒）；ruff check /
  format 通过；`check-schema`（34 份 schema、158 份样例）、`check-openapi`、`scripts/product_display.py check`（0 处不一致、0 个 key 缺英文）、
  `scripts/product_geography.py check` 都通过。新的 `tests/product/test_ratings.py`（20 条：精确归一、同比例、明确 0 / 未答 / 全零、父项
  为 0 的往返、确认画像与 E1 只读 `points`、旧方式停用、v3.3 与 v3.4 旧草稿的显式迁移、v3.4 对象原样读回与身份不变）；旧对象的
  `test_legacy_objects.py`（25 条）照旧通过。两进程升级探针（旧版服务开始并停下的运行，新版恢复）在第三个提交之后、连同服务库 v4 一起重跑。
* 待独立复核：本节全部内容。
* 还没有做 / 本版限制：E5 页面没有接（评分控件、同等重视、父项为 0 的收起与恢复、迁移时的两种选择都要 E5 做）；独立评分只认比例，
  想表达“都不在乎”的人只能选无所谓 / 暂不确定，而这两种状态挡住确认；没有做用户访谈或可用性测试；mock 通过不代表真实用户或真实模型
  的效果；没有部署、没有真实模型试跑。

## 17. 一次开始：设置预览 → 一次“确认并开始”→ 后台检查与开始（2026-10-25，服务 API v1.4）

状态：**实现及离线自测完成，待独立复核**。同一任务的第二个提交（基线是第一个提交 `f7b64e5`；只有这一项的文件，没有推送）。规则见
[ADR0048](../../DECISIONS/0048-e3c-one-start.md)。主链不变：确认画像 → 设置预览 → 一次“确认并开始” → 后台运行前检查 → 幂等提交 →
LangGraph → E1 → 结果。E3 定义授权、核对与停下的规则；E5 只呈现、只读轮询，**不自己发第二次确认、不自己恢复或重开**。

### 17.1 用户怎么开始

1. 确认画像之后，页面用 `POST /api/v1/run-start-preview`（`RunStartPreviewRequest`：`confirmed_id` 与可选的 `clones_per_city` / `years` /
   `max_cost_cny`，和准备模拟同样的选项）取一份设置预览 `RunStartPreview`：
   * `settings`（城市、每城分身数、年数、分身总数、规划器种类、费用上限、调用上限、服务尝试上限、设置身份）、`planner`（规划器与费用
     说明，同预检查里的那一份）；**费用上限是停机上限，不是预计费用**；
   * `scope_zh`（范围说明：后台检查、普通资料缺口不再确认、什么情况停下、只开始一次，加资料包的限制与这份画像的兴趣范围说明）；
   * `acknowledgements`（开始之前要知悉的几项，`ack_id` + 中文；真实模型时多一项 `ack:start.cost_cap`）；
   * `start_allowed` / `start_refusal`（`paid_calls_disabled` / `quota_exhausted` / `intent_conflict`：此刻为什么不能开始）、`quota`；
   * `consent_fingerprint`（同意身份，见 17.2）与 `display`（这份回复的中英两种文字，即时回复没有可以再读的对象）。
   预览只读：不排队、不预留次数、不调用模型、不写库。设置或语言变了随时可以重新取。
2. 用户看过、勾选全部知悉项，点**一次**“确认并开始”：`POST /api/v1/run-intents`，`RunIntentCreateRequest` 带
   `start = {mode: "start_when_ready", consent_fingerprint, acknowledged: [全部 ack_id], accept: true, idempotency_key}`。
   新建 202，找回已有的 200；回复是 `RunIntentView`（阶段 `checking`，`start.state = "pending"`，还没有 `run_id`）。
3. 之后页面只读：`GET /api/v1/run-intents/{id}`（或 `GET /api/v1/runs`）。检查通过后服务按授权自动开始；**页面不要发 `submit`**（带
   授权的意图会 409 `start_already_authorized`）。关掉页面、刷新、断线都不影响。

### 17.2 同意身份与幂等

* `consent_fingerprint` = 确认画像（编号与内容身份）+ 资料包 + 有效设置（含模型、报价、费用上限与调用上限）+ 范围说明的业务版本
  （`scope_version = "e3c-start-scope-v1"`）+ 知悉项编号。**不含文字**：换语言不改它；设置、规模、费用上限、报价或知悉规则变了就不同。
* 服务端收到一次开始时按**当前**的预览重新核对：对不上 409 `start_consent_stale`（重新取预览）；知悉项不全 422
  `start_acknowledgement_missing`；需要付费调用而服务没开 409 `paid_calls_disabled`；已经有一次条件不同、尚未结束的准备 409
  `intent_conflict`。这些拒绝都不建任何东西。
* `idempotency_key`：这一次操作的键。**页面要在发请求之前把它和这次操作一起保存**（例如随画像编号与选项存进本地），连点、回复丢失、
  刷新或重开之后重发**同一个请求、同一个键**：服务端返回同一个意图（200），即使它已经结束、取消或删除——这就是“没拿到 intent_id
  时的找回办法”，不要猜“列表里最新一项”，也不要换新键另起。同一个键配不同的请求 409 `idempotency_conflict`。
* 新的键遇到尚未结束的同一份准备（同一画像、同样的有效设置，例如另一个标签页）：返回同一个意图，不另起运行。结束之后要再模拟：
  新的预览、新的键。

### 17.3 开始授权（`RunIntentView.start`）与确认来源

| 字段 | 意思 |
|---|---|
| `start.state = pending` | 检查通过后由服务按这份授权开始（`waiting_for = service`；阶段可能是 `checking`，或检查刚通过、正在开始的 `awaiting_confirmation`） |
| `start.state = used` | 已经按授权开始（`used_at`）；`confirmation_source = start_authorization`——确认来自检查前的这次授权，不是检查之后又点了一次 |
| `start.state = void` | 授权作废，不会再自动开始；`void_reason` 说为什么，`explanation` 写发生了什么、影响与下一步 |
| `start = null` | 没有授权的旧意图：照旧在检查之后由用户确认（`submit`），`confirmation_source = user_confirm` |

作废原因（`void_reason`）：`conditions_changed`（资料包、规划器、报价或设置变了，服务已按新条件重新检查）、`consent_changed`（要知悉的
范围说明变了）、`options_not_allowed`（选项不再被允许，意图标为检查没通过）、`paid_calls_disabled`、`quota_exhausted`、`preflight_blocked`
（检查没有通过）、`authorization_mismatch`（检查结果与授权的设置对不上）、`data_pack_unavailable`、`internal_error`、`cancelled`。

动作（`allowed_actions`）：带授权的意图**没有** `submit`；授权作废、意图仍在等确认、有次数、现在能开始时出现 `start_again`——页面重新取
预览、让用户看过新的说明后再“确认并开始”一次（**新的幂等键**）；`cancel` 照旧（开始之前）。付费调用关着时没有 `start_again`，
`waiting_for = maintainer`；开启之后也不会自动开始。

### 17.4 次数、费用与停下（与原来相同的规则）

* 预览与检查不扣次数；检查通过后在提交事务里预留 1 次，第一次进入 E1 才转为已使用。开始之前失败、取消或过期都不扣，已有的预留照原规则退回。
* 付费开关、费用上限、发送不确定、恢复资格、删除、过期与本人归属的核对都照旧；自动开始 ≠ 自动恢复 ≠ 失败后自动重开。
* 预览不编译生活输入：检查才会发现的普通资料缺口（例如某座城缺某项评分资料）不在预览里逐条列出，开始后不再要求确认，结果里照实标；
  阻断（检查没通过）与授权以外的变化照样停下说明。

### 17.5 样例（`contracts/product/v3/samples/`）

| 情形 | 样例 |
|---|---|
| 预览请求 / 预览（mock）/ 预览（真实模型、付费调用没开） | 162 / 163 / 164 |
| 一次开始的请求 → 检查中（`pending`）→ 英文显示 → 按授权跑完（`used`、`start_authorization`） | 165 → 166 → 176 → 167 |
| 结束之后同一个键重试（200，同一个意图）/ 同一个键配不同请求（409） | 168 / 169 |
| 同意身份对不上（409）/ 知悉项不全（422）/ 带授权的意图又收到确认（409） | 170 / 171 / 172 |
| 授权作废：报价变了（重新检查、`start_again`）/ 付费调用被关（等维护者）/ 检查没通过 | 173 / 174 / 175 |

173、174 用测试传输（离线替身，不是真实模型），标为 `controlled_fixture`。

### 17.6 自测与没有验证的

* 离线自测（干净工作树：`f7b64e5` 加本提交的文件）：全量离线测试 1419 条通过、0 失败（`uv run pytest -n 8`，15 分 26 秒）；ruff 通过；
  `check-schema`（36 份 schema、173 份样例）、`check-openapi`、显示核对都通过。新的 `tests/product_service/test_one_start.py`（18 条：
  预览只读、付费开关、关掉页面照样跑完、连点 / 并发 / 同键重试只有一个意图与一次扣次、带授权不接受第二次确认、检查与开始之间崩溃
  两种情形都只开始一次、付费调用被关 / 报价变了 / 选项收窄 / 知悉规则变了 / 检查没通过时授权作废、取消 / 删除 / 过期与开始竞争、删除
  之后不显示授权、旧意图照旧）；服务库 v2、v3 就地升到 v4 的测试。
* 待独立复核：本节全部内容。还没有做：E5 页面没有接；没有部署、没有真实模型试跑。

## 18. 等待页的进度摘要（2026-10-25，`RunIntentView.progress_summary`）

状态：**实现及离线自测完成，待独立复核**。同一任务的第三个提交（只有这一项的文件，没有推送）。规则见
[ADR0049](../../DECISIONS/0049-e3c-progress-summary.md)。

### 18.1 字段

每次读取运行意图（`GET /api/v1/run-intents/{id}`、一次开始的回复、`submit` 回复里的意图）都带 `progress_summary`，按服务库的阶段与 E1
写下的权威进度快照**当场生成、只读**；检查阶段还没有 `run_id` 也有：

| 字段 | 意思 |
|---|---|
| `phase` | `checking` / `queued` / `simulating` / `assembling_result` / `finished` / `needs_attention`，以及结束在 `cancelled` / `deleted` / `expired` |
| `sentence_zh` | 一句当前状态（显示附层给英文，指针 `/progress_summary/sentence_zh`）；新的一句覆盖上一句，**不是执行历史** |
| `updated_at` | 这句状态所依据的记录的时间（进度快照或意图本身的更新时间）；断线时页面照它写“最后已知” |
| `mode` | `indeterminate`：不定长（检查、排队、整理结果、还没有第一份快照、停下时没有快照）；`clone_years`：按已结算的分身年度数；`finished`：结果真的可以读 |
| `settled_clone_years` / `planned_clone_years` | 只在 `clone_years` 与有计数的 `finished` 里给：已结算 = E1 运行归档里已经执行并结算的年度行动条数（每个分身每年最多一条，进行中的分身也算到它过完的那一年）；计划 = E1 冻结的名单 × 年数 |

这两个数也写进进度快照（`RunProgress.clone_years_settled` / `clone_years_planned`，worker 从 E1 的归档与名单生成；离线桥接与旧快照没有这两项）。旧版本写下的快照没有计数时：每个分身都已经有结局记录才按记录相加（那时是准确的），否则不给计数（模拟中显示“这份进度快照没有记下已结算的分身年度数”），不拿只数了已结束分身的下界冒充进度。

### 18.2 怎么画（规则在 E3，页面照画）

* 检查、排队、整理结果：不定长的进度条；模拟中：`settled / planned`（例如“已结算 18/72 个分身年度”）。**不按调用次数、不按时间增长。**
* 提前出局、停下或中断之后没有经历的年份不算进已结算：一个分身第 1 年出局，它只算 1；所以完成时已结算可以小于计划。
* `finished` 只在结果可以读时出现；完成不等于所有分身都是 C（C / E / M 照结果分别呈现）。阶段是 `finished` 但结果读不到时给
  `needs_attention`（“模拟结束了，但结果暂时读不到”），不说完成。
* `needs_attention` 包括：检查没通过、开始授权作废、旧流程里检查通过等用户确认、停下了（可以恢复 / 等维护者）。原因与下一步看意图的
  `explanation` 与 `allowed_actions`，不要用永久转圈遮住。
* 只显示当前这一句，不累积执行历史；不含内部状态、路径、凭证或评分参数。

### 18.3 每种情形的一句话（中 / 英）

| `phase` | `mode` | 情形 | 中文 | English |
|---|---|---|---|---|
| `checking` | `indeterminate` | 检查中（旧流程，没有开始授权） | 正在后台检查这次模拟能不能开始（不使用次数） | Checking in the background whether this simulation can start (this does not use a run) |
| `checking` | `indeterminate` | 检查中，带开始授权 | 正在后台检查；通过后按你的确认自动开始 | Checking in the background; once it passes, it starts automatically as you confirmed |
| `checking` | `indeterminate` | 检查通过，正按授权开始 | 检查通过，正在按你的确认开始 | The check passed; starting as you confirmed |
| `queued` | `indeterminate` | 已确认，排队等首次启动 | 已确认开始，排队等待启动 | Start confirmed; queued to start |
| `queued` | `indeterminate` | 已经开始过，排队等恢复 | 已经开始过，排队等待恢复 | Already started before; queued to resume |
| `simulating` | `indeterminate` | 刚开始，还没有第一份进度快照 | 模拟刚开始，第一份进度还没有写下 | The simulation has just started; the first progress record has not been written yet |
| `simulating` | `clone_years` | 模拟中（按已结算的分身年度数） | 已结算 {settled}/{planned} 个分身年度 | {settled}/{planned} clone-years settled |
| `simulating` | `indeterminate` | 模拟中，旧版本的快照没有记下已结算的年度数 | 模拟进行中（这份进度快照没有记下已结算的分身年度数） | The simulation is running (this progress record does not include the number of settled clone-years) |
| `assembling_result` | `indeterminate` | E1 已结束，正在整理结果 | 模拟已经结束，正在整理结果 | The simulation has ended; assembling the results |
| `finished` | `finished` | 结果可以读（有进度快照） | 结果可以查看：已结算 {settled}/{planned} 个分身年度（完成、出局与模拟缺失见结果） | Results are ready: {settled}/{planned} clone-years settled (completions, exits and simulation gaps are in the results) |
| `finished` | `finished` | 结果可以读（没有进度快照） | 结果可以查看（完成、出局与模拟缺失见结果） | Results are ready (completions, exits and simulation gaps are in the results) |
| `needs_attention` | `indeterminate` | 旧流程：检查通过，等用户确认 | 检查通过，等你确认开始 | The check passed; waiting for you to confirm the start |
| `needs_attention` | `indeterminate` | 检查通过，等维护者开启付费调用 | 检查通过，等维护者开启付费模型调用 | The check passed; waiting for the maintainer to enable paid model calls |
| `needs_attention` | `indeterminate` | 开始授权作废 | 开始授权已经作废：需要你看过说明后重新确认 | The start authorization is no longer valid: read the explanation and confirm again |
| `needs_attention` | `indeterminate` | 开始之前停下（检查没通过等） | 开始之前停下了：原因与下一步见说明 | Stopped before starting: see the explanation for the reason and what to do next |
| `needs_attention` | `clone_years` | 停下了，可以恢复 | 停下了：已结算 {settled}/{planned} 个分身年度，可以恢复 | Stopped: {settled}/{planned} clone-years settled; you can resume |
| `needs_attention` | `clone_years` | 停下了，等维护者 | 停下了：已结算 {settled}/{planned} 个分身年度，等维护者处理 | Stopped: {settled}/{planned} clone-years settled; waiting for the maintainer |
| `needs_attention` | `indeterminate` | 停下了，没有进度快照 | 停下了，需要处理 | Stopped; needs attention |
| `needs_attention` | `indeterminate` | 流程结束但结果读不到 | 模拟结束了，但结果暂时读不到 | The simulation has ended, but the results cannot be read right now |
| `cancelled` | `indeterminate` | 开始之前取消 | 开始之前已取消 | Cancelled before starting |
| `deleted` | `indeterminate` | 删除中 | 正在删除 | Deleting |
| `deleted` | `indeterminate` | 已删除 | 已删除 | Deleted |
| `expired` | `indeterminate` | 已过期 | 已过期 | Expired |

（`{settled}` / `{planned}` 是已结算 / 计划的分身年度数，例如“已结算 18/72 个分身年度”。）

### 18.4 样例

| 情形 | 样例 |
|---|---|
| 模拟中（`simulating`、按已结算的分身年度数，测试传输）/ 英文 | 177 / 178 |
| 停下了、可以恢复（`needs_attention`、`clone_years`）的英文 | 179 |
| 结束但不是全部完成（`finished`，已结算小于计划，两个分身都是 M）/ 英文 | 180 / 181 |
| 开始之前取消（`cancelled`）/ 英文 | 182 / 183 |
| 其他阶段：检查中 70 / 166（英文 176）、排队 73、完成 78 / 167、检查没通过 81 / 175、开始授权作废 173 / 174、删除 87、过期 90（英文 119）；每一份运行意图样例都带 `progress_summary` | — |

### 18.5 自测

离线自测（干净工作树：`ebf5549` 加本提交的文件）：全量离线测试 1423 条通过、0 失败（`uv run pytest -n 8`，17 分 05 秒）；ruff 通过；
`check-schema`（36 份 schema、180 份样例）、`check-openapi`、显示核对都通过。新的 `tests/product_service/test_progress_summary.py`
（每种阶段的一句话与进度条、已结算按 E1 归档计数、旧快照不猜、读取与英文显示不写库、停下时只数已结算的年份）。两次旧版升级探针与
一座城的正式入口闭环见本机证据 `runs/e3-v35-experience-20261025/`。待独立复核。

## 19. 给 E5 的交接清单（2026-10-25 这一轮：独立评分、一次开始、进度摘要）

状态：**实现及离线自测完成，待独立复核**；没有部署、没有推送、没有调用真实 / 订阅 / 在线翻译模型。

| 事项 | 在哪 |
|---|---|
| 提交 | `f7b64e5` 独立评分（第 16 节）、`ebf5549` 一次开始（第 17 节）、第三个提交（本节所在的提交）进度摘要（第 18 节）；基线 `6c01e79` |
| 公共结构与 OpenAPI | `contracts/product/v3/`：36 份 schema（新增 `RunStartPreviewRequest`、`RunStartPreview`），`openapi.json` 是 `e3c-service-api-v1.4`；以这里为准，不要按建议的路由名或字段名自己实现 |
| 原始评分 ↔ 权重样例 | 155 → 143 → 161 → 160（80/40/40/20/10/10 → 40/20/20/10/5/5）、142（同等重视）、144（明确 0）、145（全零）、146（超过 100）、156（同比例）、147 → 158（物质父项 0 → 恢复）、157（旧百分比停用） |
| 预览与一次开始的请求 | 162（预览请求）、163 / 164（预览）、165（带 `start` 的创建请求）；回复 166 / 167 / 168 |
| 授权作废与错误 | 169–175（同键冲突、同意身份对不上、知悉项不全、带授权又确认、报价变了、付费调用被关、检查没通过） |
| 进度摘要的中英样例 | 第 18.3 节的句子表（每一种情形，中英都来自冻结的显示资源）与第 18.4 节的意图样例 |
| 旧草稿 | 不迁移（第 20 节）：旧版本的草稿照原样读回、审阅挡住确认，用户新开一份；已确认的旧画像、已开始的运行、历史结果不改；服务库 v3 → v4 打开时就地升级（只加一列），旧意图照旧流程 |

**E5 不要做的**：不要自己换算权重或把旧百分比改读成评分；不要在页面上轮询到“检查通过”再发 `submit` 或恢复；找回一次开始时同一次操作
一直用同一个键（只有授权作废之后用户重新确认，即 `start_again`，才用新键）；不要重算或重签旧对象的身份、不要把旧画像保存成新版本、不要拿当前译文补历史对象。

**历史地图的版本（澄清第 12.5、13.2 节的写法）**：地理清单 `GET /api/v1/display/geography` **省略版本时读到的是当前登记的版本**，不是
历史版本。所以历史结果（或运行意图）的显示信封 `resource_version` 为空时，**不要请求地理清单**——既不要不带版本请求，也不要拿当前版本
冒充；页面按“没有绑定的历史版本”显示，不画城市点、不用当前城市锚点补，底图、列表、表格与能读的事实照常显示。只有当前输入（选城）
才省略版本读当前清单。明确请求的版本不存在仍是 404，不回退。

**E5 的夹具**：`web/tests/fixtures/drafts/` 现在是 v3.4 的百分比草稿，E3 照原样审阅（旧含义 + 阻断，见第 16.6 节）；按 v3.5 重新生成
之后告诉 E3，`tests/product/test_web_drafts.py` 里对应的几条要换成独立评分的例子。`tests/fixtures/v33_objects/`、`tests/fixtures/v34_objects/`
是旧版真实写下的对象，可以用来做迁移与旧对象读取的浏览器联验。

**只是工程验证、还没有证明的**：全部是离线 mock 与测试传输（离线替身）；没有真实用户、没有真实模型、没有可用性测试；进度条只按 E1
已结算的年份前进，没有在真实模型的耗时下看过等待体验；联网部署、并发多用户与长时间运行没有测。

## 20. 去掉旧草稿迁移（2026-10-26）

状态：**实现及离线自测完成，待独立复核**。所有者 2026-10-26 决定（测试站上没有用户），规则见
[ADR0050](../../DECISIONS/0050-e3-no-old-draft-migration.md)。第 14.6 节与第 16.5 节的迁移流程作废。

* **删掉的**：`POST /api/v1/drafts/{id}/migrate`、`DraftMigrateRequest`、`DraftView.migration`（`DraftMigration`、`LegacyAllocation`、
  `LegacyAllocationLine`、`RemovedAnswer`）、错误 `migration_consent_required` / `migration_choice_required` / `migration_not_needed`、
  样例 148、149、151、152、153、154、159。公共结构仍是 v3.5、服务 API 仍是 v1.4，schema 35 份。
* **旧版本的草稿**（`meta.schema_version` 不是 v3.5）：`GET` 照原样读回；审阅报 `version_changed`（还有硬要求或属性偏好就
  `removed_section_present`，旧的百分比分配就 `allocation_mode_retired`），不能确认。页面提示“这是旧版本草稿，请新开一份”即可，
  不需要迁移对话框（样例 150 是这样一份草稿的审阅）。
* **不变**：已确认的旧画像、已开始的运行与历史结果照原身份读回、照原绑定显示；停下的旧运行照常恢复；历史结果的显示版本为空时
  不请求地理清单。
* **E5 要删的**：迁移对话框与它的调用（`MigrationPanel`、`migrateDraft`、迁移的 e2e 与断言）；重新生成类型以 `contracts/product/v3/` 为准。
