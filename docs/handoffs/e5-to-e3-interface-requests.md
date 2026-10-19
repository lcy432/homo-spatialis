# E5 → E3 接口需求

更新：2026-10-18（本地时区 UTC−7）。E5 首轮讨论（所有者确认“合并版”方案）之后提出，**待 E3 排期与答复**。

本文只提需求，不改变现有契约：权威结构仍是 `src/common/product_contracts.py` → [contracts/product/v2/](../../contracts/product/v2/README.md)，字段名与含义
由 E3 决定。每条写明：用户在界面上遇到什么、现在的契约是什么样、建议的请求或回复（字段名只是举例）、E5 在 E3 答复之前怎么做。E3 答复时请直接
在对应条目下写“E3 答复（日期）：……”，不要删改原文。E5 后续轮次新发现的缺口追加在文末，编号接着往下排。

例子里的“小林”是公共样例 02–10 的合成用户 u501（文员，35 岁，开局现金 3,000，比较合成城 K51、M52）。

| 组 | 什么时候需要 | 条目 |
|---|---|---|
| A | 影响 E5-A 的呈现与可用性观察，越早越好 | A1–A10 |
| B | E5-B 接线之前必须有 | B1–B8 |
| C | E5-C 上线之前要定 | C1–C4 |

### LangGraph 外层流程原型之后（2026-10-19 补）

接入层的 LangGraph 外层流程原型（intake-04，[说明](../design/langgraph-product-workflow.md)）已经覆盖或改变了下面几条。界面层接服务时以流程为准，
仍经 E3 的接口回复（流程说明第 10 节第 7 条）：

| 条目 | 流程原型之后 | 还缺什么 |
|---|---|---|
| B2 进度：由谁处理 | 公开视图有 `waiting_for`（confirmation / revision / resolution）与 `needs_attention` | 运行中的当前位置与更新时间仍要 E3 在 RunProgress 里给（界面要写“正在过第几年”） |
| B3 恢复入口 | `resume` 只在 E1 可恢复时接上；`resolve` 由人发起重试或放弃，“恢复”不会自己重试 | E3-C 决定谁能调用；界面只显示“需要处理”与审计编号 |
| B4 提交与幂等 | 幂等键在 `start` 时传入；确认带回 `confirm_with`；同一个键的两个流程绑定同一个运行 | “同一份预检查、换了一个键”仍会开新运行（第 3 点的服务端保护仍需要） |
| B6 预检查请求 | 预检查由 `start` 产生，请求只带已确认画像与本次输入的引用 | — |

新增：

### B8 流程的公开视图收进共享结构

- **界面场景**：小林点“开始运行”之前，页面要知道流程是在等他确认（显示预检查与“开始运行”）、在等他改条件，还是在等维护者处理。
- **现在**：流程原型的公开视图（`stage` / `stage_zh`、`waiting_for`、`preflight.public` 与 `confirm_with`、`run_id` / `progress` / `result`、
  `attention` / `notice` / `error`）由 `src/workflows/runtime.py` 输出，不在 `contracts/product/` 里，没有 schema，也不在 `index.json` 的哈希核对里。
- **建议**：E3 把它（至少公开部分）收进共享结构、导出 schema 与样例（等待确认、运行中、需要处理、已交付、被拒），或者在 RunProgress 里加提交前的阶段
  （流程说明第 10 节第 2 条）。界面在这之前不接一份没有 schema 的 JSON。

## A. 影响 E5-A 呈现

### A1 结果里只有英文键的字段，要有中文

- **界面场景**：小林打开 K51 分身的故事页，“第 1 年第 1 月 健康状态”一栏只能显示 `healthy`；硬要求一栏只有 `commute_limit` 与 `satisfied_every_month`；
  期末估值用的参考方案只显示 `p3_current_service`；家务只显示 `laundry`、`cleaning`。
- **现在**：`YearFacts.health_months`、`YearFacts.status`、`RequirementLine.need` / `summary`、`RequirementSummaryLine.need`、`CloneResult.reference_plan`、
  `HouseholdHours.task` 都只有键（`src/product/results.py` 第 175、187、225、231–232 行附近）。中文其实已经有来源：`common/life_contracts.py` 的
  `HEALTH_STATUS_LABEL`（健康 / 小病 / 大病）；评价配置里每个模板的 `label`（例如 p3_current_service = “现职 + 较舒适的生活 + 人工杂务服务”）；协议
  `household_tasks` 的 `description`（`CatalogCompilation.tasks` 里也有）；`RequirementSummary` 的注释。
- **建议**：沿用 `CloneProgress` 已有的 `status` + `status_zh` 写法，在结果里给这些键配上中文，例如：

  ```json
  {"health_months": ["healthy", "major"], "health_months_zh": ["健康", "大病"],
   "reference_plan": "p3_current_service", "reference_plan_zh": "现职 + 较舒适的生活 + 人工杂务服务"}
  ```

  或在能力清单里给一张有版本的对照表。两种都行，由 E3 选。
- **E5 在此之前**：集中写在 `web/src/contract/pending-labels.ts`，每条注明上面的来源，只转写、不新增解释；没登记的键显示“未登记的状态（代码：xxx）”。
  E3 给出正式文字后删除。

### A2 知悉项要带说明文字

- **界面场景**：确认页要小林勾选“我知道这是从健康、没有已有物品开始的简化情景”，但审阅只给了键 `ack:standard_start`。
- **现在**：`ProfileReview.pending_confirmations` 是字符串列表；说明文字在 `product/answers.py` 的 `_ack_text` 里，没有进入契约。
- **建议**：`"pending_confirmations": [{"key": "ack:standard_start", "text_zh": "这是一次从健康、没有已有物品……开始的简化情景"}]`。
- **E5 在此之前**：`ack:standard_start` 用能力清单的 `start_scenario`；`ack:retained:<ref>` 用对应 `statements` 条目的原文与 `message_zh`；其他键显示键名并写
  “待 E3 提供说明”。

### A3 答案状态的显示用词

- **界面场景**：分配器的“说不准”菜单要显示“无所谓 / 暂不确定”，未答要显示“还没有分配”。
- **现在**：能力清单没有这几种状态的用词；审阅问题的文字里用的是“还没有分配 / 无所谓 / 暂不确定”（`product/answers.py` 第 276–280 行）。另外健康题里
  “无明显倾向”是一个可以确认的有效回答，与六维的“无所谓”不是一回事，用词不能混。
- **建议**：能力清单加一组有版本的用词，例如 `"answer_state_labels": {"unanswered": "还没有分配", "indifferent": "无所谓", "unsure": "暂不确定"}`。
- **E5 在此之前**：照审阅文字里的用词。

### A4 通勤硬要求：“用途”与说明矛盾

- **界面场景**：确认页上，小林的“通勤往返不超过 60 分钟”同时显示用途标签“规划、评分”和说明“不进分数，也不拦截计划”。
- **现在**：受支持的硬要求在审阅里写 `usage=("planning", "scoring")`（`product/answers.py` 第 819 行），说明却是 `verification` 原文“不进分数”。
- **建议**：去掉 `scoring`，或者新增一个表示“单独核验并报告”的用途值。由 E3 定。
- **E5 在此之前**：照原样显示两者，在证据与报告里标出这处矛盾。

### A5 数据格式

- **家务小时里的“—”**：`HouseholdHours.monthly_hours` 在没有生活的月份写“—”，与精确数混在一个字符串数组里（样例 23、24）。建议改成 `null`。
  E5 在此之前把“—”显示为“没有生活记录”。
- **事件链前缀重复、没有结构**：样例 23 的事件写成“第 1 年第 1 月：第 1 年第 1 月月初新发大病（实验指定的事件）”，年月出现了两次。建议去掉重复；最好
  给出结构，时间线才好排：

  ```json
  {"year_index": 1, "month": 1, "kind": "illness_started", "text_zh": "月初新发大病（实验指定的事件）"}
  ```

  E5 在此之前逐条原样显示。

### A6 资格题什么时候出现

- **界面场景**：用户说“我是会计”，候选城市的会计岗位要求会计从业资格。界面应当这时（且只在这时）问“你是否已经持有会计从业资格？”。
- **现在**：`AbilityOption.ask_when` 只是一句说明（`common/product_contracts.py` 第 301–306 行）；机器能读的信号只有预检查里的
  `start_job_requires_unconfirmed_ability`（`product/compile.py`），那时用户已经确认过画像了。
- **建议**：审阅里给出需要问的资格：
  `"ability_questions": [{"ability_id": "accounting_cert", "reason_zh": "候选城市的会计岗位要求这项资格"}]`。
- **E5 在此之前**：放在默认折叠的“已经持有的资格（可选）”里，显示 `ask_when` 原文。

### A7 缺的样例

- 审阅场景 11、12、13、16、17、30、37、38 没有对应的原始草稿样例（只有正常场景有 02），界面没法演示“这些答案是怎么填出来的”。
- 进度：`queued`、运行到一半（例如第一个分身完成 2 年、第二个还没开始）、`budget_exhausted`。
- 真实模型的预检查（`planner.paid: true`、有 `max_cost`）与相应的进度和结果费用。
- 需要问资格的审阅（配合 A6）。

E5 在此之前：没有草稿的场景从确认页开始演示并在页面上说明；缺样例的状态先做好页面、用组件测试覆盖空态。

### A8 两条交互规则请确认

1. **均分之后改一项**：小林先点“平均分配”（六项各 50/3），再把安全改成 20。E5 的做法是先问“改成手动分配吗？其余各项保留精确的 50/3”，同意后草稿变成
   `mode: "manual"`，其余五项 `{"state": "provided", "value_text": "50/3"}`，安全 `"20"`；界面显示“多出 3.3 分”（精确 10/3），由小林决定从哪一项减。
   E3 的解析器接受规范分数，形状上可行；请确认这样记录符合答案语义（用户没有亲手输入“50/3”，但它来自用户主动选择的均分）。
2. **草稿版本与过时的审阅**：E5-A 在本标签页生成一次 `draft_id`，`revision` 从 1 开始、每次内容变化加 1；收到的审阅如果 `draft.revision` 不等于当前版本，
   就当作过时丢掉（小林改了物质分数，旧版本的审阅晚到，不能覆盖新答案）。请确认 `draft_id` / `revision` 由谁分配；E5-B 接线之后是否改由服务端分配。

### A9 发到浏览器的确认画像里带内部职业代码

- **现在**：`ConfirmedProfile.occupation.internal_mappings` 含国家分类代码（样例 04 是 SOC 2018 `43-9061`）。共享约定说用户看不到职业编号。
- **E5 的承诺**：界面任何地方都不显示它，测试里检查页面文字不出现这个代码。
- **请 E3 决定**：发给浏览器的那一份是否去掉它（例如另给一份公开投影）。

### A10 属性偏好挂在哪一项需求下

- **现在**：`AttributePreferenceAnswer.need` 是自由字符串，能力清单没有写可选值；E3 自己生成样例 17 时用的是物质细项的键（`daily_convenience`、
  `fresh_food`，见 `scripts/product_e3a.py` 第 335–347 行）。
- **建议**：在能力清单里写明每种属性偏好可以挂的需求，例如 `attribute_kinds[].needs`。
- **E5 在此之前**：让用户从 `material_leaves` 里选，与样例 17 一致。

## B. E5-B 接线之前必须有

### B1 确认请求与拒绝回复

- **界面场景**：小林勾选知悉项，点“确认我的信息”。
- **现在**：没有对应的消息；`product.answers.confirm_profile(pack, draft, person_ref=…, acknowledged=…)` 拒绝时抛 `ConfirmationRefusedError`。
- **建议**：

  ```json
  {"meta": "（照抄能力清单的 meta）",
   "draft": "（完整的 UserInputDraft，或服务端已保存草稿的引用）",
   "acknowledged": ["ack:standard_start"]}
  ```

  成功返回 `ConfirmedProfile`；失败返回 `{"status": "refused", "issues": [ProductIssue, …]}`，草稿版本过时也用这个格式给出明确代码。`person_ref` 由服务端
  按登录会话决定（C1），不由浏览器填。

### B2 进度：当前位置、更新时间、由谁处理

- **界面场景**：小林在进度页等着，想知道“现在跑到哪了”；批次停下时想知道“要我做什么，还是等别人”。
- **现在**：`RunProgress` 没有当前位置与更新时间。E1 自己说过，`running` 也可能是崩溃之后留下的状态（`common/life_planning_contracts.py` 第 715 行注释），
  没有更新时间就分不出来；`resumable: true` 也没说谁能恢复。
- **建议**：

  ```json
  {"stage": "running", "current": {"clone_ref": "u501.m52.r0", "year_index": 2},
   "updated_at": "2026-10-18T10:21:05Z", "handled_by": null, "queue_position": null}
  ```

  停下时 `handled_by` 写 `"maintainer"`、`"user"` 或 `"none"`（不能恢复），界面据此写“等维护者处理，你不需要重新填写”或给出按钮。

### B3 恢复入口

- **现在**：E1 有 `resume_run`（`orchestration/life_runner.py` 第 1974 行），只在 `running` / `interrupted` / `halted` 时可以恢复，尝试次数与预算不清零；
  批次停下要所有者决定之后才恢复（样例 33 的 `resume_note`）；程序错误暂停不能续跑，只能用新的运行重跑（样例 27）。产品层与网络接口都还没有。
- **建议**：恢复请求带 `run_id` 与它自己的幂等键，回复是“已恢复”或结构化拒绝；重复点击只产生一次恢复执行。E5 建议 V1 只由维护者恢复——发送状态不确定、
  预算不足、模型不符这些情况，用户点一下就可能重复付费。
- **E5 在此之前**：不做恢复按钮。

### B4 提交：持久幂等、回执丢失、同一份预检查去重

- **现在**：本地桥接（`product/bridge.py` 第 49–132 行）对“同一个键 + 同一份预检查”返回原运行，对“同一个键 + 另一份预检查”拒绝；但“同一份预检查 + 新的键”
  会开一次新运行。`RunReceipt.service` 只能是 `offline_bridge` 或 `contract_example_only`。
- **界面场景**：小林点了运行，网络断了，接着又关掉了标签页（草稿只存在标签页，幂等键也跟着丢了）。下次换新键提交，同一份预检查会再跑一次；托管时就是
  重复付费。
- **建议**：
  1. 登记持久保存，提交原子化；
  2. 回执丢失后，浏览器用原来的键、原来的请求重发，取回同一次运行（现在本地桥接已经这样，托管版保住这个行为）；
  3. 服务端另加一条保护：同一个用户、同一份预检查已经有运行时，直接返回那次运行，不依赖浏览器还记得键；
  4. `service` 增加托管取值。

### B5 真实模型的费用投影

- **现在**：`product.bridge.progress` 把费用写死成 `offline_mock_zero_cost`（第 175–180 行）。
- **建议**：真实模型运行写 `estimated_from_quote`，给出已用调用次数、上限、按报价估算的金额与币种，并保留“以供应商账单为准”的说明；预检查的
  `max_cost` 与之同源。

### B6 预检查请求与可调参数

- **现在**：`run_preflight` 接收完整的确认画像与编译目录（`product/preflight.py` 第 102 行）；每城分身数与年数来自资料包的运行设置。
- **建议**：网络请求只带确认引用，画像由服务端保存。V1 是否允许用户调整城市数之外的参数（每城分身数、年数）请 E3 与所有者确认；如果允许，要有正式的
  请求字段与上限。E5 目前只显示，不提供调整。

### B7 分身自己的说法（模型理由）

- **界面场景**：小林想知道“为什么分身第 1 年去上会计课”。所有者已经定了“环境实际账目与模型自己的理由分开展示”。E1-7B 就有现成的例子：Flash 在理由里把
  税估成约 1,500，环境实际只扣 600（合成税表：税前 18,000，其中超过 12,000 的 6,000 按 10% 计税）。
- **现在**：`ProductResult` 不含规划理由与需求摘要（E3-A 技术说明第 9 节“不进结果的东西”）。
- **建议**：按分身、按年度给出带归属的文字，例如

  ```json
  {"year_index": 1, "kind": "rationale", "attribution": "agent_self_report", "text_zh": "……", "truncated": false}
  ```

  界面放在“分身自己的说法（可能算错）”一栏，旁边是“环境记录（以此为准）”。是否需要额外的信息隔离检查、长度上限，由 E3 定。
- **E5 在此之前**：留出位置，写“本版结果还没有提供分身自己的理由”，不放任何模拟内容。

## C. E5-C 上线之前要定（E5 的建议，供所有者与 E3 决定）

- **C1 访问控制与用户归属**：`person_ref` 由服务端会话决定；每次运行只能由创建它的会话打开；不拿 run_id 当钥匙。
- **C2 留存与删除**：建议草稿与结果默认保留 30 天，用户可以随时一键删除；真实用户的文字与模型回复不进仓库，不做公开分享链接。
- **C3 单执行者队列与费用保护**：现在的预算账本只支持串行；E3 与 E5 共用一套队列，不各建一套。
- **C4 “我的运行”**：用户回来时能看到自己已有的运行与状态（配合 B4 的同一份预检查去重）。

## 追加（E5 后续轮次）

以下是 E5-A 实现时新发现的（2026-10-18，[ADR0036](../../DECISIONS/0036-e5a-offline-ui.md)）。

### A11 给用户看的文字里出现了字段名

- **界面场景**：运行前说明里，“能完整评分”的原因写着“逐城说明见 cities；不保证未来每个月都能完整评分”（样例 05 的 `scorable.reasons`）。用户不知道 `cities` 是什么。
- **建议**：改成“逐城说明见下面每座城市的卡片”之类的人话。E5 照原样显示，不改写。

### A12 年龄只能存整数，用户的原文丢了

- **界面场景**：用户在年龄框里写“35岁”。契约里 `AgeAnswer.value` 是整数，E5-A 只能把年龄记成未答、在界面上提示“写成整数”；E3 审阅时看不到用户原来写了什么。
- **建议**：在 `AgeAnswer` 里保留原文（例如 `raw_text`），由 E3 判断写法；或者确认“年龄只收整数”是有意的，E5 继续在前端提示。

### A13 运行中途，分身的状态取什么值

- **现在**：`CloneProgress.status` 是自由字符串；E1 的 `CLONE_RUN_STATUS_TEXT` 只有结局类的状态（正常生活完、出局、停止……），没有“正在过第 2 年”这样的中途状态。E5-A 的组件测试只能自己构造
  一个 `running`（“进行中”）来测页面。
- **建议**：配合 A7 的“运行到一半”样例，定下中途状态的取值与中文。

### A14 样例 22 没有出处标签

- **现在**：样例 22 是 `ProductResult` 里的一个 `YearFacts` 片段，没有 `meta`，界面显示不了它是离线运行还是受控夹具（README 第 6 节写的是受控夹具）。
- **建议**：片段样例也带一份出处说明（例如放在样例文件的顶层），或者在 README 里写明 E5 应该怎样标注。
