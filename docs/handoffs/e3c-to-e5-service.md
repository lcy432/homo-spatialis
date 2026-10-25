# E3C 托管服务交给界面层（E3 → E5）

日期：2026-10-23；2026-10-24 E3C 收尾（运行选项、独立审核 R1–R6 的修复，第 11 节是本轮的改动清单）；2026-10-24 E3 → E5 接口补齐（按钮与期限、双语显示附层、地理清单、结果色阶，第 12 节）。规则见 [ADR0043](../../DECISIONS/0043-e3c-hosted-service.md)，机制见 [E3C 托管服务](../design/e3c-service.md)。本轮只提供服务、接口与样例，**没有改 E5 页面**。

## 1. 现在能做什么、不能做什么

* **能**：在本机起一个离线 mock 服务（只绑定 127.0.0.1，零网络、零费用），E5 的页面照下面的接口接线、走完整条路：专属链接 → 填写与审阅 → 确认画像 → 运行前说明 → 确认开始 → 后台模拟 → 进度与结果 → 删除。
* **不能**：网页还不能正式给用户用——服务没有部署（没有主机、域名与 TLS），付费模型调用没有打开（`live_calls_enabled: false`），真实模型在这条服务链上没有试跑过。网页仍读 `contracts/product/v2/` 的部分要先迁到 v3（[E3B 交接](e3b-to-e5-real-pack.md)、[生活扩展交接](e3-to-e5-life-extension.md)）。
* 次数、排队、预算、后台调度、删除与过期**全部由服务端负责**；界面不另做一套计数、队列或重试。

## 2. 文件

| 文件 | 用途 |
|---|---|
| [`contracts/product/v3/openapi.json`](../../contracts/product/v3/openapi.json) | 全部 HTTP 接口（路径、请求与回复的结构、错误码）。生产不在线公开 `/docs`；本地 mock 服务在 `http://127.0.0.1:8765/api/v1/docs` 有交互式文档 |
| `contracts/product/v3/*.schema.json`、`index.json` | 公共结构 `e3a-product-schema-v3.3`（33 份消息，含显示附层的 5 份）。唯一权威是 `src/common/product_contracts.py` |
| `contracts/product/v3/samples/60_…–125_…` | 服务的样例（第 8 节；105–125 是显示附层，第 12.6 节），全部由服务端真实代码路径产生，凭证是明确无效的测试值 |
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

外层的 `resource_version` 是这次读取当场生成的文字用的版本（当前登记的）；保存下来的部分（预检查、进度、结果、停下的说明、审阅、确认画像）按它们生成时记下的版本渲染，每段文字实际用的版本看 `fields[].resource_version`。

`DisplayField`：`pointer`（相对 `source` 的 JSON Pointer）、`key`（稳定的模板键或 `entity.<种类>.<字段>:<编号>`）、`status`（`provided` / `missing`）、`text`（纯文字，缺失时 `null`）、`source_locale`（`zh`）、`missing_reason`（缺失时）、`resource_version`（这段文字用的资源版本）。

1. **业务状态只从 `source` 读**：数字、权重、编号、动作、确认 token、日期、C / E / M 都照 `source`，`fields` 只用于文字。`zh` 时 `fields[].text` 逐字等于 `source` 里的原文。
2. 显示某个文字位置：在 `fields` 里按 `pointer` 找；`provided` 用 `text`；`missing` 就显示 `source` 原文并标“英文说明尚未提供”，不要自己翻译，也不要拿中文去查另一份字典。缺失原因：`no_binding`（生成时没有记下绑定，或是升级前的旧对象）、`translation_missing`、`translation_stale`、`source_changed`、`ambiguous`、`untranslated_param`、`resources_unavailable`（这个对象记下的资源版本不存在）、`binding_stale`。
3. 用户自己写的文字（草稿里的自由填写、职业原文等）不翻译、不在 `fields` 里，照原样显示。
4. 修改请求（POST / PUT / DELETE）照旧走原接口；之后用显示出口只读刷新。错误与确认回复不必再读一次：直接用回复里的 `display.translations[locale].fields`（`source_view_fingerprint` 对的是去掉 `display` 之后的回复）。
5. 轮询时可以直接轮询 `GET /api/v1/display?kind=intent&id=…`：`source` 与文字同一次返回，不会把新进度与旧翻译拼在一起；读取不写库、不续期、不调用模型（与业务查询相同的保证）。
6. 未登录、会话失效、服务不可达时，用随包携带的固定双语错误目录 `contracts/product/display/v1/service-errors.json`（按 `code`，到期按对象）。动态业务问题只以服务回复里的 `display` 为准。
7. 结果色阶：`kind = result` 时 `metrics` 有一项 `MetricDisplaySpec`——`key = mean_j`、`minimum = "0"`、`maximum = "1"`、`unit = model_score`、`direction = higher_model_evaluation`、`denominator_field = mean_denominator`、`comparison_scope = within_result_only`、`evaluation_refs`（协议、评分、评价的公开引用）、`notes`（中英）。两种地图、所有国家共用这把固定尺度，不按城市重新拉伸；超出 [0, 1] 是契约错误，不截断；C = 0 或没有评价不画最低分的颜色；有 M 时照常着色并标出缺失人数；缺失范围的分母是 C + M，不是置信区间；0.7 不是“现实里幸福 70%”，也不是排名。

### 12.4 显示资源从哪来

* 中文模板写在生成文字的代码里（`product.display.t`），输出与原来逐字相同；英文在 `configs/product/display/templates/*.yaml` 与 `entities/*`（按资料包），由 `scripts/product_display.py freeze` 冻结成不可覆盖的版本。**按实体编号、模板键与带类型的安全参数**（城市、职业、机会、片区、月份、金额、代码）组装，不按中文查英文；`ScopeNote.topic` 不单独当键；年度事实的 jobs / housing / food / training 等字符串数组在原投影处按机会编号、月份与公开事实记下绑定，页面不用拆中文。
* 私有绑定随所属对象保存（服务库里审阅、确认画像、运行意图的列，以及这次运行工作目录里的 `workflow/display/`），同一个所有者、同一个数据代号、同一个事务；删除、过期、草稿改修订、删除全部之后的迟到写回都一起管住。读取从不补写；升级前的旧对象显示为 `no_binding`。
* 历史对象按它记下的资源版本读（样例 120）；那个版本不存在就报 `resources_unavailable`，不回退到最新资源。
* 覆盖报告：离线联调的三条故事里（E3B 六城 × 每城 4 个分身 × 3 年；E3B 两城 × 每城 2 个分身；合成资料包），每种对象的中文都完整、英文全部提供：应展示 2,373 / 778 / 497 段，英文缺失 0（`runs/e3-e5-display-20261024/coverage.md`）。用户原文与识别别名照原文显示，不在分母里。这三条故事里没有出现、但结构上还会缺英文的：分身事件链里 E1 自己写成中文句子的记录（`/clones/*/event_chain/*`，要 E1 给结构化记录才能绑定，这一轮不改 `src/econ/`）；引用用户自己写的中文的说明（例如没认出的兴趣原文，报 `untranslated_param`）；只有测试夹具才会改的资料事实

### 12.5 地理清单

`GeographyDisplayBundle`：`display_schema_version`、`resource_version`、`pack_fingerprint`、`status`、`cities[]`、`sources[]`、`notes[]`（中英）。每座城：`city_ref`、`country`、`scheme`、`code`、`geography_version`、`country_names`、`names`（中英正式名与短名）、`model_kind`（`real` / `synthetic`）、`anchor_status`（`available` / `unknown` / `not_applicable`）、`anchor`（`latitude`、`longitude`，度；`crs = EPSG:4326`；`kind = metro_internal_point`；`source_refs`）、`support_scope`（`ref`、`version`，中英的居住、工作与说明）、`coverage_note`。

* 用哪个资料包：当前的看能力清单的 `meta.data_pack`；历史结果用那次结果 `source.meta.data_pack` 的指纹，并把显示读取回来的 `resource_version` 一起传进来，读到的就是那一版冻结的清单。省略 `resource_version` 用当前登记的版本；明确请求的版本不存在或资料包没有登记：404，**不回退**；登记了但没有地理资源：`status = unavailable`、城市为空。
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
