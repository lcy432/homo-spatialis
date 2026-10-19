# 0035 真实 LLM 测试改用 Claude Haiku 5.5：Claude Agent SDK 单轮调用、厂商适配与可选计费

> ⚠ “真实 LLM 测试一律用 Haiku”已被 [DECISIONS/0038](0038-test-models-by-scale.md) 取代：非全量用 Sonnet 5.5 max，全规模用 Qwen 最新 Flash。传输层与计费部分仍有效。

**状态**：已实现、离线自测通过；最小真实试跑见第六节（2026-10-18，本地时区 UTC−7）。授权来自所有者在对话里的要求与选择（原文见
[prompt 归档](../docs/design/prompts/2026-10-18-haiku-test-planner.md)）。`src/econ/` 没有改；生活、评分、评价与汇总的数学都没有改。代码未提交，
没有执行 `git add`。
**版本**：模型角色 `test_planner`（`claude-haiku-5-5`，滚动型号，effort `max`）与对照测试角色 `test_planner_sonnet`（`claude-sonnet-5-5`，滚动型号，
effort `medium`）；服务商 `claude_agent_sdk`；规划器 `llm.claude.test_planner` / `llm.claude.test_planner_sonnet`（版本 `e1-7b-llm-v1`）；
运行配置 `configs/life/run_e1_7b_haiku.yaml`（CLI 默认）、`configs/life/run_e1_convenience_haiku.yaml` 与 `configs/life/run_e1_7b_sonnet.yaml`（只在显式 `--config` 时）；依赖 `claude-agent-sdk 0.2.168`（自带命令行
2.1.301）。千问的产品角色 `product_planner`（Flash）、研究角色 `base_a` / `base_b`、提示模板、记忆政策与执行修订 `e1-7b-r2` 不变。
**指针**：[E1-7 技术说明](../docs/design/e1-7-agent-loop.md)第 18 节；参数目录 AG-22（并改 AG-13、AG-19、AG-21）；PLAN §3.0.23；证据
[runs/2026-10-18-haiku-test-planner/](../runs/2026-10-18-haiku-test-planner/README.md)。本记录推翻 [ADR0032](0032-product-planner-default-qwen3-8-flash.md)
"CLI 默认读 Flash 配置"的那一处（README、ADR0032、CLAUDE.md 与 E1-7 技术说明第 16 节的原处已留指针）；ADR0032 的产品角色本身不变。

> **当前规则一览**
>
> * **真实 LLM 测试与试跑一律用 `test_planner`（Claude Haiku 5.5，effort `max`）**；本轮另用 `test_planner_sonnet`（Claude Sonnet 5.5，effort `medium`）
>   在 Haiku 之后对照一次，只这一轮（所有者 2026-10-18 指定）。输出不另设上限：用模型自身的最大值 128,000，超时 600 秒。`scripts/run_life_e1_7b.py` 不写 `--config` 时用 `run_e1_7b_haiku.yaml`；Flash 与 Max 要显式
>   `--config`；`resume` / `replay` 照旧只按归档冻结的配置执行。离线测试照旧用 mock；`-m live` 的 3 条在线测试测的是研究基座 `base_b`，不换。
> * **Agent SDK 只当"一个输入、跑一轮、一个输出"的 API 密钥调用**：`max_turns=1`、`tools=[]`、`setting_sources=[]`、`strict_mcp_config`；每次新建、用完删除的
>   空工作目录与配置目录（`CLAUDE_CONFIG_DIR`，都在仓库之外）；命令行进程只继承白名单里的系统变量与网络代理变量，用项目自己的 `ANTHROPIC_API_KEY` 与官方端点——
>   不继承桌面 App 注入的端点与登录变量，不用所有者的 Claude 账号登录态。命令行内部重试、提示缓存、遥测、错误上报、自动更新与非必要流量都关掉。
> * **两层接口**：生活引擎只认规划器接口；真实模型规划器 `LlmPlanner`（旧名 `QwenPlanner` 是别名）只认传输层接口。厂商相关的三样——请求体写法、发送、
>   错误 / 用量 / 结束原因的翻译——按 `provider` 选：千问 `OpenAICompatibleTransport`，Claude `ClaudeAgentSdkTransport`。
> * **计费可选、额度也可选**：运行配置要么写 `paid_budget`，要么写 `unmetered_reason`（理由），二选一并进运行身份；规划器的预算闸门可以不传，这时
>   费用记为 `unmetered`。账本的次数上限与费用上限都可以不写——不写就只记账（照常预留与核算）、不拦截；三份 Claude 配置都不设上限（所有者要求）。
>   账本按报价币种记账（美元账本写 `*_usd`，人民币账本格式逐字节不变）；报价可以写分档上限，超过就停下（那是报价对不对，不是额度）。

## 一、所有者的要求（对话原话）

* "把之前所有 qwen3.8flash 负责的真实大模型测试的路径换成 haiku5.5 就行了。然后把它写进规则对应的文件里。告诉所有在这个项目里工作的其它
  coding agent 真实 llm 测试也用 haiku5.5 跑。"
* 选择：新增独立测试角色；仍用 Claude Agent SDK（"保持每次都是单次调用的规则"）；CLI 默认改成 Haiku 测试配置；只写进 CLAUDE.md；接线后最小试跑
  （1 个分身、1 年、最多 3 次请求、0.05 美元以内）。
* "是不是需要一个抽象层……只要实现这个接口就能够修改不同的调用"；"计费感觉在接口层面可以做成 optional 的"；"把 agent sdk 当作一个普通的 api key
  的使用模式……给一个输入，然后模型跑一轮，再给一个输出就行了"。

## 二、采用的规则

### 2.1 角色与设定

* `configs/models.yaml` 新增 `test_planner`（`ProductModelSpec`：`provider: claude_agent_sdk`、`base_url: https://api.anthropic.com`、`api_key_env:
  ANTHROPIC_API_KEY`、`model: claude-haiku-5-5`、`version_policy: rolling` 并写理由——Anthropic 只发布这一个型号名）。找不到角色报错，不回退。
* `LlmSettings`：`model_role` 加 `test_planner`，`provider` 加 `claude_agent_sdk`。Claude 设定**不写**温度（Haiku 只接受默认值）与 `enable_thinking`，
  写 `response_format: prompt_only`（没有接口级 JSON 模式，只靠提示要求一个 JSON 对象，解析照旧，允许代码块包装）、`thinking: adaptive`（命令行固定
  为自适应，关不掉）与 `effort`（`low` / `medium` / `high` / `xhigh` / `max`；Haiku 配置写 `max`、Sonnet 配置写 `medium`，所有者指定）；只能用在
  `test_planner` / `test_planner_sonnet` 上，这两个角色也只能用它。千问设定照旧必填温度、`enable_thinking: false`、`json_object`，
  不写新栏；新栏为空时不序列化，所以 Max 与 Flash 的设定指纹、规划器身份、运行身份逐字节不变。滚动型号允许在 `product_planner` 与 `test_planner`。
* 返回型号规则不变（`returned_model_accepted`）：只认 `claude-haiku-5-5` 或它后接一个快照日期。

### 2.2 传输层（`agentlayer.life_llm.ClaudeAgentSdkTransport`）

* 每份候选的每次服务尝试只调用一次 `claude_agent_sdk.query()`：系统提示与用户提示原样交给 SDK；选项见上面一览。用户提示按文字逐字交给命令行
  （命令行把它包成一个文本块）。
* 环境：SDK 会把本进程的环境整份传给命令行、选项只能覆盖不能删除，所以启动期间本进程环境临时只保留白名单（PATH、SYSTEMROOT、TEMP 等系统变量，
  以及 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY / NO_PROXY 这几个网络代理设置——千问路径的 OpenAI SDK 同样读它们，它们不涉及身份），
  用完原样恢复（串行使用，有锁）；显式写入密钥、官方端点、隔离的 `CLAUDE_CONFIG_DIR` / `HOME` / `USERPROFILE`、`CLAUDE_CODE_MAX_RETRIES=0`、
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`、`API_TIMEOUT_MS`、`DISABLE_PROMPT_CACHING` 与关闭遥测类的变量。
* 翻译：Anthropic 的结束原因 `end_turn` / `stop_sequence` → `stop`、`max_tokens` → `length`（运行器只把 `stop` 当正常结束；原始值另记）；错误按
  `AssistantMessage.error` 与 HTTP 状态码 → 凭证（401/403）、账户（402）、限流（429，可重试）、服务端（5xx、529，可重试）、请求（400）、其他；命令行
  进程失败 → `process_error`（不重试）；超时（请求超时 + 60 秒启动余量）→ `timeout`（可重试）。用量：输入 = 普通 + 缓存写 + 缓存读（缓存关闭时后两项
  为 0），输出含思考 token；SDK 自己估的 `total_cost_usd` 等记在 `provider_usage`，只作核对，不作扣额度依据。
* 请求明细里记的请求体是"交给 SDK 的那一份"（模型、两条消息、输出上限、单轮选项）；逐字节核对、最终请求检查与输入估计都对它做。

### 2.3 计费

* `PaidBudget`：`max_cost_cny` 或 `max_cost_usd`，与报价币种一致、只写一个；`PriceTable.currency` 加 `USD`，可写 `max_input_tokens_at_price`（分档报价这一档
  适用的输入上限）。账本事件与请求明细的金额栏按币种写（`reserved_usd` / `cost_usd`）；人民币账本的头文件、事件、汇总与旧记录逐字节不变。
* 输入估计超过分档上限就不预留、不发送；实际输入超过分档上限时，回复与用量照常落盘、按已记录报价核算，然后停下（`budget`），等所有者核对。
* 额度可选（所有者 2026-10-18）：`PaidBudget.max_requests` 与费用上限都可以不写，不写就不设那一项——账本照常发送前预留、返回后核算，头文件与汇总
  里那一项写 null，不因次数或费用拦截；写了上限的旧配置与旧账本逐字节不变。报价分档上限不是额度，照旧检查。三份 Claude 配置都不设上限。
* 不记账：`LifeRunConfig.unmetered_reason`（8–300 字）与 `paid_budget` 二选一；mock 两样都不写。规划器 `budget=None` 时不预留、不核算，请求明细费用状态
  `unmetered`；`budget_identity_problems` 照旧要求"声明了账本就必须带那一本，没声明就不能带"。不记账时仍受全局调用上限约束。

### 2.4 入口与配置

* `scripts/run_life_e1_7b.py`：`DEFAULT = run_e1_7b_haiku.yaml`（不写 `--config` 时），`PRODUCT`（Flash）与 `LEGACY_MAX` 只在显式 `--config` 时用；
  `_live_planner` 用 `make_transport` 按服务商选传输层，账本按运行配置可有可无。
* 两份 Haiku 配置与对应的 Flash 配置相同的是名单、事实、信息条件、每年 1 + 2 份候选、提示模板、记忆政策、服务重试；不同的是服务商的调用方式、
  模型与角色、effort `max`、输出上限（模型最大值 128,000，Flash 是 4,096）、超时（600 秒，Flash 是 90 秒）、报价与账本。两份共用本轮账本
  `runs/2026-10-18-haiku-test-planner/budget`，不设额度上限。Sonnet 对照配置 `run_e1_7b_sonnet.yaml`（只这一轮）与默认 Haiku 配置相同，只换模型与角色、
  effort `medium`、报价，账本单独一本 `budget-sonnet`，同样不设额度上限；三份配置的全局规划调用上限都是 3（本轮最小试跑：名单第一项的第 1 年）。
* 报价（2026-10-18 发送前核对官方价格页）：Haiku 5.5 提示不超过 100,000 token 时输入 0.10、输出 0.50 美元每百万 token（超过是 0.50 / 2.50）；Sonnet 5.5
  输入 2、输出 10 美元每百万 token，不分档；不假设缓存或批量折扣。

## 三、与千问路径的差异（降级，所有者已选择 Agent SDK）

本机抓包（假的 Anthropic 接口，零网络、零费用；`probe/`）核对的事实：

1. **请求次数**：成功、401、429、500、529 时一次调用只发 1 个 HTTP 请求；**400 时命令行会去掉两项可选功能再重发，最多共 3 次**（Anthropic 不对 400
   计费）。账本与请求明细按 1 次失败的服务尝试记。
2. **请求正文不全是我们的**：命令行在系统提示前加一行计费标记与一句 "You are a Claude agent, built on Anthropic's Claude Agent SDK."，在用户消息之后加一段
   运行环境系统消息（临时工作目录路径——在系统临时目录下，含 Windows 的短用户名、"不是 git 仓库"、平台与系统版本、关于下载文件的提醒、型号与知识截止、
   当天的真实日期）。这些对分身是额外信息
   （不是答案侧对象：没有 CLAUDE.md、项目文件、记忆、评分参数或研究真值）。与 Flash 相比，Haiku 收到的提示多了这些内容——两者不是同一个信息条件。
3. **思考关不掉**：无论选项怎样写，命令行都发 `thinking: adaptive`（`display: updates`）；effort 照写（抓包核对过 `max` 与 `medium`）。思考 token
   按输出计费，并且与回答共用输出上限；所以 Claude 配置不另设上限，直接用模型最大值 128,000（命令行不设时也自动填这个数，本机抓包核对过），
   超时放宽到 600 秒。按 128,000 预留，Haiku 一次请求约 0.069 美元、Sonnet 约 1.38 美元（保守上界，实际通常远低于此）。
4. **其他字段**：命令行另带 `metadata`（设备编号、会话编号）、`safeguards`（危险工具分类用的上下文，home 目录已改指隔离目录）与 `context_management`。
   继承原环境时还会带上 Windows 用户名与真实 home 目录——所以必须用白名单环境（对照抓包已脱敏保存）。
5. **"一轮"由抓包验证，不是 SDK 的保证**：命令行版本变了（随 `claude-agent-sdk` 升级），这些行为可能变。升级依赖之前要重跑 `probe/probe_transport.py`。

## 四、本地实现选择（对话里没有写死，请所有者复核）

1. effort：最初取 `low`（Flash 是"思考关闭"，取最接近的档位）；所有者 2026-10-18 改为 Haiku `max`、Sonnet `medium`；输出不另设上限（模型最大值
   128,000）、超时 600 秒也是所有者的决定。这一条不再是本地选择。
2. 环境白名单 + 覆盖 `HOME` / `USERPROFILE`：防止宿主登录态、端点、用户名与 home 路径进入请求。
3. 关闭提示缓存：每份候选的提示不同，缓存几乎没有收益，关掉后只按普通输入计价，账本不用处理缓存写入的 1.25 倍价。
4. 命令行进程失败不重试：可能已经发出请求，重试会重复计费；按"停下等所有者"处理。
5. 输入估计上限沿用 64,000；报价分档上限 100,000 写进报价，实际超过就停下。
6. 两份 Haiku 配置共用一本账，Sonnet 单独一本；三本都不设额度上限（所有者 2026-10-18：额度可选，Haiku 与 Sonnet 都去掉；先前提议的 3 次 / 0.05 美元
   与 0.50 美元已撤销）。三份配置的全局规划调用上限都是 3（本轮最小试跑）。
7. 结束原因的翻译放在传输层（厂商适配器的职责），运行器不动。这是本机抓包发现的缺陷：不翻译时 Anthropic 的正常结束 `end_turn` 会被当成截断，
   三份候选全被退回。
8. 已有测试只改 1 处：`test_product_flash.py` 断言"CLI 默认是 Flash"的那一行改成新默认。

## 五、离线验收（零网络、零费用；证据 `offline/`、`probe/`、`mutation/`）

见证据目录 README 的验收表（新测试、全量离线回归、两份配置的开跑前检查、经过真实命令行的本机先导、旧身份与旧归档的比较、针对性变异）。

## 六、最小真实试跑

见证据目录 README（若所有者尚未提供 `ANTHROPIC_API_KEY`，试跑没有执行，这一节写明"未执行"）。

## 七、限制与未做

* 研究角色、Flash 产品角色、Max 配置、旧归档与旧账本不变；旧归档用本轮开工时的源码快照（`historical_source/`）重放。
* 真实模型的结果只说明这个滚动型号在这一次合成试跑里的表现，不代表模型能力，也不能比较城市；`exploratory_synthetic` 不得改成正式可评价。
* 命令行另加的运行环境说明与强制思考使 Haiku 与 Flash 的信息条件不同；E4 若要比较两者，要另行处理。
* Agent SDK 的单轮行为靠抓包核对；扩大试跑要所有者另给预算，并换新账本、运行编号与证据目录。

## 八、订阅认证与第一次真实试跑（2026-10-18 补记）

* **订阅认证**（所有者选择）：`LlmSettings.auth: claude_subscription`——不要 `ANTHROPIC_API_KEY`，命令行用本机已登录的订阅凭证（`claude auth login`，用 SDK 自带的
  `claude.exe`）；不再隔离配置目录，启动时另放行找凭证用的路径变量（USERPROFILE、HOME、APPDATA 等），其余宿主变量照旧去掉；`setting_sources=[]`、单轮、无工具不变。
  Haiku 与 Sonnet 的三份配置都改成订阅 + `unmetered_reason`（先不计费，用量照记）。
* **订阅模式的代价**（本机抓包 `probe/probe_subscription.json`）：命令行会在第一条消息里附上账号邮箱（userEmail 上下文），请求的安全分类字段里还有用户名与 home 路径；
  没有 CLAUDE.md、项目路径或本机记忆。`CLAUDE_CODE_SIMPLE` 能去掉这些，但会让订阅认证失败，所以不能用。API 密钥模式没有这个问题。
* **第一次真实试跑**：`configs/life/run_e1_7b_haiku_medium.yaml`（Haiku 5.5，effort medium），先导 = 名单第一项第 1 年：1 次请求、第一份候选通过执行前检查并提交；
  耗时约 30 秒，输入 17,173 / 输出 5,501 token（其中思考 4,620），命令行估的费用 0.0045 美元（订阅下不计）；返回型号 `claude-haiku-5-5`。证据 `live/`。
