# E3C 托管服务交给界面层（E3 → E5）

日期：2026-10-23。规则见 [ADR0043](../../DECISIONS/0043-e3c-hosted-service.md)，机制见 [E3C 托管服务](../design/e3c-service.md)。本轮只提供服务、接口与样例，**没有改 E5 页面**。

## 1. 现在能做什么、不能做什么

* **能**：在本机起一个离线 mock 服务（只绑定 127.0.0.1，零网络、零费用），E5 的页面照下面的接口接线、走完整条路：专属链接 → 填写与审阅 → 确认画像 → 运行前说明 → 确认开始 → 后台模拟 → 进度与结果 → 删除。
* **不能**：网页还不能正式给用户用——服务没有部署（没有主机、域名与 TLS），付费模型调用没有打开（`live_calls_enabled: false`），真实模型在这条服务链上没有试跑过。网页仍读 `contracts/product/v2/` 的部分要先迁到 v3（[E3B 交接](e3b-to-e5-real-pack.md)、[生活扩展交接](e3-to-e5-life-extension.md)）。
* 次数、排队、预算、后台调度、删除与过期**全部由服务端负责**；界面不另做一套计数、队列或重试。

## 2. 文件

| 文件 | 用途 |
|---|---|
| [`contracts/product/v3/openapi.json`](../../contracts/product/v3/openapi.json) | 全部 HTTP 接口（路径、请求与回复的结构、错误码）。生产不在线公开 `/docs`；本地 mock 服务在 `http://127.0.0.1:8765/api/v1/docs` 有交互式文档 |
| `contracts/product/v3/*.schema.json`、`index.json` | 公共结构 `e3a-product-schema-v3.2`（28 份消息）。唯一权威是 `src/common/product_contracts.py` |
| `contracts/product/v3/samples/60_…–96_…` | 服务的样例（第 8 节），全部由服务端真实代码路径产生，凭证是明确无效的测试值 |
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
| 准备模拟 | `POST /api/v1/run-intents`（`confirmed_id`） | 202 `RunIntentView`（`checking`，排队检查）；同样条件再点一次 200 返回原意图 |
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
| `run_scale_fixed` | 409 | 固定比较全部六个都会区：在草稿里选全部城市 |
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
| 删除 | 86（回执）、87（墓碑）、88（已删除的结果 410）、89（删除全部） |
| 过期 | 90（意图）、91（结果 410） |
| 其他错误 | 65 版本冲突、75 键冲突、92 未登录、93 CSRF、94 限流、95 付费未开、96 预检查过时 |

样例用测试时钟（2026-10-24 起）与测试配置（合成资料包、每城 1 个分身）；生产固定六城 × 每城 4 个分身 × 3 年（`SessionView.rules` 写明）。

## 9. 新字段要点

* `QuotaSummary`：`total = used + reserved + available`；`reserved` 是确认开始但还没启动的。
* `ServiceRules`：规模、规划器种类、付费开关、费用上限与币种、各项保存天数、`notes_zh`（原样显示）。
* `CostStatus`（v3.2 新增的可选字段，mock 时不出现）：`currency`、`price_version`、`max_cost`、`settled_cost`、`uncertain_cost`、`in_flight_cost`、`service_attempts`、`service_attempt_limit`、`uncertain_attempts`。金额是应用侧按报价的估算，必须同时显示 `note`（不是供应商账单）。
* `RunProgress.updated_at`：快照写下的时间（UTC）；离线桥接不写。
* `RunReceipt.service = hosted_service`：服务端 SQLite 登记的提交。

## 10. 已验收与没有验证的

* 已验收（离线，`tests/product_service/` 与全量测试）：身份隔离与重发链接、草稿冲突、确认不可伪造、次数并发与重发、预留释放与不退款、固定规模与身份一致、四个中断点的重启恢复、发送不确定、费用上限、查询无副作用、worker 独占、五种删除竞争、过期、清理范围、信息隔离、结果语义、E3B 六城 × 4 × 3 全规模离线闭环。
* 没有验证：真实模型在这条服务链上的表现与费用（`live_calls_enabled` 没开过）；真实部署（主机、TLS、反向代理、systemd、磁盘与备份策略、日志轮转）；浏览器里的真实页面（E5 尚未接线）；多人同时使用时的排队等待时间（单个 worker 串行，一次全规模运行要几分钟到更久）。
