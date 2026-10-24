# 0043 E3C 托管服务：专属链接、服务端次数与单个 worker

日期：2026-10-23（所有者确认的架构，来自当天的 E3C 实施任务）。技术说明见 [E3C 托管服务](../docs/design/e3c-service.md)，给界面层的交接见 [E3C → E5](../docs/handoffs/e3c-to-e5-service.md)。

## 背景

E3A / E3B 的产品链只有本地离线桥接：提交登记是一个 JSON 文件，`E3Context.planner()` 只接受 mock，`WorkflowRuntime.confirm()` 同步跑完整个生活，工作目录靠进程级 `chdir`，进度费用写死为零，没有身份、次数、删除与过期。网页要正式用，需要一个服务端把这些接起来，但不能另写一套确认、规划或恢复流程。

## 决定

- **组件**：FastAPI（认证、业务请求、只读查询）+ 服务端 SQLite（邀请、会话、草稿、审阅、确认、运行意图、次数事件、提交、任务、发送许可与公开快照）+ **一个**独立 worker 进程（进程锁，串行执行任务；一次运行里照旧最多 16 个分身线程）+ 现有 LangGraph（预检查 → 等确认 → 再核对 → 登记 → E1 → 投影）+ 现有 E1。HTTP 进程不 `chdir`、不推进流程、不调用规划器；长任务不放进 HTTP 或 `BackgroundTasks`。
- **代码位置**：`product.service`（HTTP、认证、存储、队列、留存、公开视图）；`product.execution`（正式运行设置 `ExecutionSpec`）；`product.workspace`（工作目录与快照）。驱动 LangGraph 的 worker 放在 `workflows.service_worker` / `workflows.service_runs`，与已有的 E3 接入层在一起——现有的依赖方向规则不变：`product` 等核心包不依赖 LangGraph 或 `workflows`。
- **身份**：一个人一条专属链接 `https://<站点>/invite#token=<32 字节随机秘密>`，换成固定 7 天的会话（HttpOnly、SameSite=Strict、仅本主机；生产加 Secure 与 `__Host-` 前缀）。服务端只存两种秘密的 SHA-256；服务端的 `principal_id` 是唯一身份，别人的编号一律“没有找到”。修改请求要本站 Origin + 会话绑定的 CSRF token；邀请交换每来源每分钟 10 次、其他修改每身份每分钟 60 次；请求体上限 256 KiB。管理员只用本机命令行。
- **两次确认**：画像确认只收审阅引用与知悉项，服务端重新校验后调用 `confirm_profile`；运行确认带用户看到的那份预检查身份。**先按 `ExecutionSpec` 编译最终运行设置，再生成预检查**——用户确认的就是后台实际用的规划器、调用设置、报价与上限。生产固定六城 × 每城 4 个分身 × 3 年、`product_planner`、每次运行一本新账本、10 元上限（应用侧按报价估算）。
- **次数**：一共 3 次启动机会，以不可重复的事件记账（预留 → 首次启动 E1 前转为已使用 / 开始前取消或启动前检查失败时释放；管理员修正追加记录）。“接受、预留、写稳定运行编号、排队”在一个 `BEGIN IMMEDIATE` 短事务里；同一身份最多一个未结束的运行意图；同一意图换键也只对应一个运行。服务端 SQLite 是唯一权威的提交登记（`bridge.submit` 的核对提取成 `submission_problems` 复用，登记表可换）。
- **运行控制**：E1 运行器与 `LlmPlanner` 各加一个很窄的 `RunControl` 钩子：每次规划调用占位前问“停不停”，每一次服务尝试（含重试）预留与发送前问服务库要许可；删除之后不再批准，停在 `user_deletion`。不改 `src/econ/`，不破坏年度原子提交。
- **删除与过期**：删除立即不可查看，安全收尾后删掉登记过的工作目录（画像副本、编译输入、请求与回复、归档、结果缓存、账本、checkpoint、诊断），库里只留最小记录；删除与完成同时发生时删除优先。结果从首次结束起 30 天、草稿最后编辑起 30 天、等待中的意图最后一次业务操作起 30 天；查看与轮询不续期，重启先过期再恢复。
- **真实调用**：通路实现了（`LlmPlanner` + `make_transport` + `RoundBudget`），但 `live_calls_enabled` 默认关闭；关闭或缺凭证时明确报告，不换成 mock。本轮没有任何真实或订阅模型调用。

## 后果

- 公共结构升到 `e3a-product-schema-v3.2`：新增会话、草稿保存、审阅、确认请求与回复、运行意图视图、删除回执、统一安全错误；`RunReceipt.service` 增加 `hosted_service`；`CostStatus` 分开写已核算、用量不确定与在途预留；`RunProgress.updated_at`。v3.1 的字段含义不变。
- E1 的停止原因增加 `user_deletion`；`compile_inputs` / `run_preflight` 可以接正式运行设置；结果的规划器与费用照用户确认的预检查。
- 单机、单 worker、本机磁盘：不支持网络共享盘或多机。真实部署（主机、域名、TLS、备份策略）与真实模型试跑都不在本轮，要所有者另外决定。
