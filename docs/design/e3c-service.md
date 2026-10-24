# E3C 托管服务

更新：2026-10-24（本地时区 UTC−7；E3C 收尾：独立审核 R1–R6 的修复与运行选项）。决定见 [ADR0043](../../DECISIONS/0043-e3c-hosted-service.md)，给界面层的交接见 [E3C → E5](../handoffs/e3c-to-e5-service.md)，状态（部署、真实调用、网页接线）见 [INDEX](../INDEX.md)。

## 1. 用户怎么走

1. 维护者在本机命令行为一个人建专属空间，拿到一条只显示一次的链接 `https://<站点>/invite#token=<秘密>`，交给对方。
2. 对方打开链接：网页从地址的 `#token=` 读出秘密，用同源 JSON 请求换成会话，然后把秘密从地址栏移除。之后 7 天内凭 Cookie 访问；过期了重新打开原链接。
3. 填写草稿（服务器分配编号，带版本保存）→ 审阅 → **确认画像**（“系统这样理解我，是对的”）。改了草稿，旧的审阅作废、要重新审阅；确认画像从第一次确认起 30 天内可以用来准备模拟（重复确认不续期）。
4. 准备一次模拟：可以在服务允许的范围里选较小的规模（每城分身数、年数）与这次的费用上限；不选就用服务默认值（确认画像里的城市 × 每城 4 个分身 × 3 年、20 元）。服务排队做运行前检查（“排队检查”，不扣次数），出现运行前说明，写明这次的规模与上限。
5. **确认运行**（“我知道这次范围、限制和费用保护，使用一次机会开始”）：预留 1 次，排队。worker 启动 E1 之前的最后一步把预留转为已使用。
6. 轮询进度；结束后看结果（C / E / M、未知与部分结果照 E1 原样）。结果从首次结束起保存 30 天，查看不续期；可以随时删除。

次数：一共 3 次启动机会，不重置。填写、审阅、确认画像、运行前检查都不扣；确认开始预留 1 次；开始前取消或启动前核对失败退回；开始之后（部分结果、预算停下、删除）不退；刷新、查询、恢复不另扣；删除结果、换设备、重发链接都不改变次数。

## 2. 组件与接线

```text
E5 浏览器 ──同源 HTTPS（Cookie + CSRF）──> FastAPI（product.service.api）
                                              │ 业务函数（product.service.actions）
                                              ▼
                                     服务端 SQLite（product.service.store）
                                              │ 任务队列（preflight / run / resume / delete）
                                              ▼
                          单个 worker（workflows.service_worker，进程锁，串行）
                                              │ 每次模拟：独立工作目录 + LangGraph checkpoint + 新账本
                                              ▼
                现有 LangGraph（workflows.runtime / graph / e3_adapters）
                                              ▼
                 E3 编译 / 预检查 / 投影（product.*） ↔ 现有 E1（orchestration.life_runner）
```

| 复用 | 新增 |
|---|---|
| E3 的审阅、确认（`confirm_profile`）、编译、预检查、投影、能力清单；`bridge.submit` 的核对（提取成 `submission_problems`） | `ExecutionSpec`（`product.execution`）：服务配置产生的正式运行设置，先编译它再做预检查 |
| LangGraph 外层流程（start / confirm / revise / resume / resolve）与 E3 接入层 | 接入层可注入：规划器工厂、`RunControl`、启动前核对、登记表、并行分身数（`workflows.e3_adapters.E3Context`） |
| E1 的启动、恢复核对、归档、修订、服务重试、全局调用上限 | E1 运行器与 `LlmPlanner` 的发送许可钩子（`RunControl`）与停止原因 `user_deletion` |
| `LlmPlanner`、`make_transport`、`RoundBudget`、`read_ledger` | 进度费用按账本只读汇总（`bridge.cost_status`）；结果的规划器与费用照确认的预检查 |
| 工作目录准备（原 `product.fixtures.prepare_workspace`） | 提取成 `product.workspace`，并记下每个文件的 SHA-256 快照 |
| — | 服务库、会话与 CSRF、限流、请求体上限、统一安全错误、公开视图、worker 与它的监测 / 维护线程、管理员命令行 |

依赖方向不变：`product` 等核心包不依赖 LangGraph 或 `workflows`（`tests/workflows/test_isolation.py`），所以驱动流程的 worker 放在 `workflows/`，HTTP、存储与视图在 `product.service`。

## 3. 数据目录（持久盘，git 忽略，不做长期备份）

```text
<data_dir>/
  service.sqlite(-wal)        服务库（WAL、secure_delete；删除后做 WAL 检查点）
  server.key                  CSRF 的 HMAC 密钥（第一次启动生成）
  worker.lock                 worker 进程锁
  diagnostics/worker.jsonl    worker 的安全诊断（任务、意图编号、公开代码、异常类型与代码位置；没有正文与秘密）
  runs/<意图编号>/
    ws/                       E3 工作目录：资料包与基础样例的快照、已确认画像、本次输入、编译产物、E1 归档、本次运行的新账本 budget/
    workflow.sqlite           这次模拟的 LangGraph checkpoint
    diagnostics/              流程的内部诊断（原文只在这里）
```

默认 `runs/product-service/`（仓库的 `runs/` 整体 git 忽略）。服务器上把 `data_dir` 改成持久盘上的绝对路径。只支持本机磁盘，不支持网络共享盘或多机共用。

## 4. 身份与 HTTP 保护

- 邀请秘密与会话秘密都是 32 字节随机值（`secrets.token_urlsafe(32)`），服务端只存 SHA-256；比较用定长时间。CSRF token 是服务端密钥对会话秘密的 HMAC（会话绑定）。
- 重发链接：沿用原身份、次数与内容，旧链接与全部旧会话失效。撤销：链接与会话失效，内容与次数记录不动。
- 每个对象都按 `principal_id` 过滤；别人的编号与不存在的编号同一个回答 `not_found`。`person_ref`、`confirmed_id`、`run_id`、指纹都不是凭证；浏览器从不提交 `person_ref` 或用户编号。
- 修改请求：Origin 必须等于配置的 `site_origin`（带 `Sec-Fetch-Site` 时必须是 `same-origin`），并带 `X-CSRF-Token`。CORS 不开放（不回任何 CORS 头）。邀请交换只收同源 JSON。
- 限流：邀请交换按来源地址每分钟 10 次（先计数再看内容，来源不对、格式不对的尝试也算）；其他修改按身份每分钟 60 次；超出 429 + `Retry-After`。来源地址在反向代理后面由 uvicorn 的 `forwarded_allow_ips` 只信任代理。
- 请求体上限 256 KiB（先完整读入再交给应用，超过就 413）。生产不公开 `/docs` 与 `openapi.json`。响应都带 `Cache-Control: no-store`、`nosniff`、`frame-ancestors 'none'` 等安全头；生产加 HSTS。
- 日志与错误：不记 Cookie、邀请秘密、CSRF token、请求体或原始校验异常；错误只给固定中文“发生了什么—影响是什么—下一步怎么做”与支持编号 `sup-…`，原文只进服务端诊断。

## 5. 运行选项、正式运行设置与确认

运行选项分三层（ADR0043 收尾一节）：

| 层 | 在哪里 | 谁定 |
|---|---|---|
| 默认值（用户不选时用什么） | `configs/product/service.yaml`：`default_clones_per_city`、`default_years`（不写 = 资料包协议的年数）、`max_cost_cny` | 维护者 |
| 允许范围（用户能选什么） | 同一份配置：`allowed_cities`（空 = 资料包里的全部城市）、`clones_per_city_min` / `clones_per_city_max`、`years_allowed`（空 = 资料包支持的全部）、`max_cost_cny_min` / `max_cost_cny_max`（不写 = 默认值） | 维护者；年数还要与资料包运行声明的 `years_supported` 取交集（不写 = 只有协议的年数） |
| 本次有效设置 | 创建运行意图时由服务端校验并保存的 `ExecutionSpec`（`product.execution.RunPolicy.derive`） | 用户在允许范围里选，服务端校验 |

现在的值：默认 = 确认画像里的城市（E3B 资料包的六个都会区）× 每城 4 个分身 × 3 年、20 元；允许每城 1–4 个分身、只开放 3 年（E3B 资料包只为 3 年做过配套验证）、0.01–20 元。`SessionView.rules.options` 原样公开这些范围。

- **城市**来自用户确认的画像，必须是允许城市的子集；有不允许的城市就拒绝（`run_options_invalid`，`field = cities`），不替用户删城或补城。
- **每城分身数**：分身编号 `0 … n−1` 由服务端生成，不改资料包（资料包的内容身份照旧对得上）；菜单按编译器原来的规则分配，不重抽。
- **费用上限**：十进制字符串，最多到分（`"5"`、`"12.5"`、`"19.99"`；不收数字类型、科学记数法或更细的位数），在允许范围里；全部城市、全部分身、全部服务尝试共用这一份（每次运行一本新账本）。
- **推出来的上限不写常数**：基础规划 = 城市数 × 每城分身数 × 年数；规划候选上限 = 基础规划 × (1 + 每年最多修订次数)；服务尝试上限 = 候选上限 × (1 + 服务重试次数)。默认 6 × 4 × 3 = 72 次基础规划、216 份候选、432 次服务请求；1 城 × 2 个分身 × 3 年是 6、18、36。
- 越界在预留与任何调用之前拒绝：409 `run_options_invalid`，`field` 指出哪一项，不回显用户填的值。

有效设置固定：资料包路径与内容身份、城市、分身编号、年数、每年最多 2 次修订、规划器（生产是 `product_planner` 的真实模型，调用设定与报价读 `configs/life/run_product_flash.yaml`）、候选与服务尝试上限、费用上限（应用侧按报价估算）。它进入本次输入的身份；编译按它写 E1 运行配置（真实模型时版本 `e1-7b-run-v1`、`paid_budget.ledger_dir = budget`），**然后**才做 E1 运行前检查与产品预检查（含 `1b3201c` 的首年请求大小检查）。所以用户确认的预检查已经绑定了这次的规模、规划器与上限，后台不能换。

- **保存一次、处处用它**：意图保存有效设置与用户的选择；之后的检查、确认、执行、恢复、进度与结果都读这一份，不回到服务现在的默认值（缺了就停：`run_settings_unavailable` / 阻断 `settings_missing`）。改服务配置只影响之后新建的运行；worker 重启也照旧。
- **幂等**：同一份确认画像 + 同样的有效设置再创建一次，返回原意图；已有未结束的意图时，换了设置（或换了画像）是 `intent_conflict`。
- **设置过时**：确认开始之前服务的资料包、报价或允许范围变了，用保存的选择重新推一遍：选择已经不被允许 → 阻断 `options_not_allowed`（不扣次数，取消后重新准备；不静默改成别的规模）；有效设置的身份变了 → 回到“排队检查”，用户看新的运行前说明再确认（`preflight_stale`，不扣次数）。确认开始之后，运行绑定当时的设置与快照，快照缺失或被改动就停，不拿新资料冒充。
- 生产只用冻结的 E3B 资料包（`real_data_frozen`），不回退到合成资料包；某城条件不满足时阻断并说明，不删城、不减分身、不补资格。
- 确认画像的请求只带审阅引用与知悉项：服务端核对审阅与当前草稿、修订号、内容指纹与资料包都对得上，再调用 `confirm_profile`。之后改草稿不影响已确认的运行输入。

## 6. 次数、提交与排队

- 次数事件表 `quota_events`：`reserve` / `consume` / `release` 每个意图各最多一次（唯一约束），管理员修正 `admin_credit` / `admin_debit` 追加。公开 `total`、`used`、`reserved`、`available`。
- 同一身份最多一个未结束的运行意图（部分唯一索引）；同一份确认画像、同样的有效设置重复创建返回原意图，换了画像或设置冲突（`intent_conflict`）。
- 确认开始：在事务外核对（复用 `submission_problems`，读快照里的资料包），再在一个 `BEGIN IMMEDIATE` 事务里核对键、预留、写稳定运行编号、排队。运行编号 = `bridge.run_id_for`（内部幂等键由服务身份与意图身份派生），与 LangGraph 之后登记的相同；LangGraph 的提交节点登记到服务库（`ServiceSubmissionRegistry`），没有第二份 JSON 登记。
- 同一个键、同一份请求 → `duplicate_same_run`；同一个键、不同请求 → `idempotency_conflict`；同一意图换了键 → 仍是原运行。

## 7. worker 与重启

- portalocker 进程锁贯穿整个生命周期；第二个 worker 拒绝启动。前一个进程死掉之后锁随进程释放；新 worker 先检查过期，再把进行中的任务退回待办，由原流程与 E1 的核对决定怎么接：
  - 提交已登记、流程还没记下 → LangGraph 重跑提交节点，登记表返回原运行；
  - 已经启动 → E1 的恢复入口逐条核对归档、请求明细与账本；可靠落盘的回复复用，不重发；
  - E1 已完成、结果没投影 → 只读重建结果；
  - 发送状态不确定、身份或归档冲突、账本不一致 → 停下等维护者。
  不重置次数、账本或尝试计数，不新建运行目录。
- 预检查也由 worker 做（可能要等正在运行的模拟）。一次运行进行中时，监测线程按绝对路径读 E1 进度与账本写白名单快照（不切换目录、不推进流程）；HTTP 查询只读快照。
- 一次运行停下时，说明按**实际的次数记录**写（`quota_state`：预留 / 已使用 / 已退回），不按停止代码猜（R6）：
  - 还没启动 E1（再核对发现条件变了、快照坏了、开关没开、缺凭证、设置不再允许）：退回预留，“没有开始模拟，预留的次数已经退回”，用户可以取消后重新准备；
  - 已经启动：“模拟已经开始，这次运行已经用掉 1 次机会（开始之后不退）”，没有“取消”；只有 E1 真的还能接着跑时才给“恢复”，否则写“不需要重新填写：等维护者处理，或删除这次运行”，不提供无效的恢复按钮。
- **能不能由用户恢复**（R4）由服务按 E1 的记录判断，不只看停止代码：服务暂时不可用，而且每一份还没有可用回复的候选，在 E1 的服务记录里还有尝试次数（`1 + service_retries` 次之内），本次账本也还放得下下一次预留。尝试已经用完、预算停下、发送状态不确定、模型不符或归档冲突都等维护者。`RunProgress.resumable` 与按钮用同一个结论；运行进行中的快照（监测线程写）`resumable = false`，说明写“正在进行，不需要恢复”，不说“停下了”。

## 8. 真实规划器与费用

真实模型通路：`LlmPlanner` + `make_transport` + 这次运行工作目录里的新 `RoundBudget`（不用 `ledgers/` 里的历史账本）；恢复继续读原账本。`live_calls_enabled: false`（默认）时确认开始返回 `paid_calls_disabled`；缺凭证时运行停在 `credentials_missing` 并退回预留——都不换成 mock。

进度的费用：mock 是 `offline_mock_zero_cost`；真实模型是 `estimated_from_quote`，分开写 `settled_cost`（返回了用量、已核算）、`uncertain_cost`（可能已发出、用量不确定，保留预留，不记 0）、`in_flight_cost`（已预留、还没有结果），`estimated_cost` 是三者之和；`calls_used` / `call_limit` 是规划候选，`service_attempts` / `service_attempt_limit` 是服务尝试；带币种、报价版本与这次运行的费用上限（默认 20 元，全部城市、分身与服务尝试共用），并写明不是供应商账单。

## 9. 删除、过期与清理

- 删除一次运行：立即标为“删除中”、不可查看、停止批准新的发送许可（许可与删除标记在服务库里串行裁决，每次服务尝试单独申请）；已经在发送路径上的请求可以完成并记账。运行线程安全关闭后，先记下最小费用摘要，再删登记过的工作目录（拒绝路径穿越与链接），最后清掉库里的个人正文，标为已删除。删除与完成同时发生时删除优先：监测线程、结果写回、重新排队或重启都不能把内容写回来。
- 删除全部：草稿、审阅、确认与全部运行；保留不透明身份、次数事件、最小费用摘要、删除时间与防重记录。专属链接仍然有效。共享资料包不是个人内容，不动。
- **删除全部与正在进行的写入**（R1）：每个身份有一个数据代号 `data_generation`，删除全部时加一；草稿、审阅、确认画像与运行意图的每一次写入都在同一个事务里核对代号——删除之前开始、删除之后才落库的请求被拒绝，不会把内容写回来。
- **审阅与确认画像跟着草稿走**（R2）：草稿存了新修订，旧修订的审阅作废；删除一份草稿，连同它的审阅与由它确认的画像一起清掉；草稿过期带走它的审阅。确认画像从第一次确认起 30 天可以用来准备模拟（重复确认、查询都不续期），到期后正文清掉（正在排队检查、还要读它的意图除外）；已经确认开始的运行用自己工作目录里的快照，画像过期不影响它。
- 过期：结果首次结束起 30 × 24 小时；草稿最后编辑起 30 天；等待中、被阻断或待处理的意图最后一次有效业务操作起 30 天；确认画像如上。**每个动作都先按到期时间判断**（R3）：过了页面显示的到期时间，确认开始、恢复、取消一律按过期拒绝，不预留、不扣次数、不发请求；死掉的 worker 留下的“运行中”记录在重启时照样过期；次数视图把已到期、还没清理的预留按退回显示（只读，不写库）。worker 的维护线程清理文件；停机后重启先过期，再谈恢复或付费。
- SQLite：`secure_delete` 打开，删除后 `wal_checkpoint(TRUNCATE)`；这不是磁盘级的安全擦除。不做长期备份；不宣称替模型供应商删除了它那边的记录。

## 10. 启动（单机）

```bash
uv sync
```

本地联调（离线 mock、只绑定 127.0.0.1）：

```bash
uv run python scripts/product_service.py invite create --label 本地联调 --config configs/product/service_local_mock.yaml
uv run python scripts/product_service.py api --config configs/product/service_local_mock.yaml
uv run python scripts/product_service.py worker --config configs/product/service_local_mock.yaml
```

生产形态（这里只给示例，本轮没有部署）：把 `configs/product/service.yaml` 的 `site_origin` 改成真实站点、`data_dir` 改成持久盘上的绝对路径；API 与 worker 各一个进程（例如 systemd 两个服务），API 只绑定 `127.0.0.1:8765`，由反向代理终止 TLS：

```nginx
server {
    listen 443 ssl;
    server_name app.example.invalid;            # 换成真实域名
    ssl_certificate     /etc/ssl/app/fullchain.pem;
    ssl_certificate_key /etc/ssl/app/privkey.pem;
    client_max_body_size 256k;                  # 与服务的请求体上限一致

    location /api/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # 服务只信任 forwarded_allow_ips 里的代理
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 60s;                 # HTTP 不跑长任务
    }
    location / {
        root /srv/homo-spatialis/web/dist;      # E5 的静态页面，与 API 同源
        try_files $uri /index.html;
    }
    access_log /var/log/nginx/app.access.log;   # 只有方法、路径与状态；邀请秘密在 # 后面，不会到服务器
}
```

管理员操作（本机）：`invite create | reissue | revoke`、`jobs`（待处理任务与停下的运行）、`quota-adjust --credit|--debit --note`。导出 OpenAPI：`uv run python scripts/product_service.py openapi`（写 `contracts/product/v3/openapi.json`）；核对提交的文档与实际应用一致：`uv run python scripts/product_service.py check-openapi`（不一致退出码 1）。

离线联调（不起 HTTP 端口、不碰真实服务数据）：一个合成人物沿真实服务路径（HTTP → 业务函数 → worker → LangGraph → E1 → 产品结果）走完一次 mock 模拟，写 `summary.json`（资料身份、有效设置、计划与实际分身数、逐城 C / E / M、模拟缺失原因、费用与真实模型调用数）：

```bash
uv run python scripts/product_service.py e2e --out runs/e3c-e2e-<日期>/default
uv run python scripts/product_service.py e2e --out runs/e3c-e2e-<日期>/smaller --cities US:CBSA2023:42660,US:CBSA2023:16980 --clones 2 --max-cost 5
```

## 11. 验收对照（`tests/product_service/`）

| 场景 | 测试 |
|---|---|
| 1 身份隔离、重发链接 | `test_identity_and_input.py`：两人互看、改编号、运行编号不是凭证、重发 / 撤销、7 天会话、生产 Cookie |
| 2 草稿冲突 | `test_older_tab_save_conflicts_and_never_overwrites_newer_answers`、编号与修订号由服务器分配 |
| 3 确认不可伪造 | 浏览器带确认画像 / person_ref / 运行设置被拒；审阅过时、资料包引用被改、知悉项缺失被拒；服务端运行设置变了要重新检查 |
| 4–5 次数 | `test_quota.py`：8 个并发确认只预留一次、并发创建只有一个意图、丢失回复重发、换键、键冲突、3 次用完不重置、取消与最后核对失败退回、开始后不退 |
| 6–7、16–18 正式运行 | `test_runs.py`：E3B 六城 × 4 × 3 全规模离线闭环与身份一致；测试传输走 `LlmPlanner`（新账本、请求明细、费用三分、信息隔离）；并行不超费用上限；付费开关、缺凭证；部分结果的 C / E / M；可由用户恢复的暂停 |
| 8、9、12 重启与独占 | `test_recovery.py`（子进程里 `os._exit`）：登记后、刚启动、回复落盘后、E1 完成未投影；发送不确定；第二个 worker；接任时归档被改 |
| 13–15 删除、过期、清理 | `test_deletion_expiry.py`：排队中 / 发送前 / 在途 / 重试前 / 写结果时删除；结果、草稿、等待意图过期；停机重启；清理范围与 SQLite 字节检查；只删登记路径 |
| 11 查询无副作用 | `test_queries_change_nothing`：连续查询前后服务库与文件逐字节相同 |
| 收尾 R1–R6（独立审核的反例） | `test_closeout_r1_r6.py`：删除全部与审阅 / 确认 / 创建意图并发、草稿过期带走审阅与画像、画像 30 天且不续期、新修订作废旧审阅、过了到期时间的确认开始 / 恢复、死 worker 的运行记录、已到期预留的只读显示、尝试用完不给恢复、发送之前暂停仍可恢复、预算停下、提交的 OpenAPI 与实际应用一致、开始前后的说明 |
| 收尾 运行选项 | `test_run_options.py`：默认值、城市子集 / 分身数 / 预算改变这次设置与派生上限、较小规模真的按那个规模跑、调高上限后接受更大规模、越界在预留之前拒绝、两人互不影响、改默认值不影响已有运行、重启前后用保存的设置、同设置返回原意图与不同设置冲突、收窄范围阻断而不改规模、每次运行一本新账本、受控夹具验证其他年数、E3B 只开放 3 年；`test_identity_and_input.py`：报价变了要重新检查；`test_budget_config.py`：生产配置 20 元、192,000 输入、72 / 216 / 432 |
