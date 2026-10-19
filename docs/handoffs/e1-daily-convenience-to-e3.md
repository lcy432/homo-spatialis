# E1 → E3 交接：物质偏好的"日常生活便利"

更新：2026-10-18（同日审核通过后补记第 0 节与几处说明）。供 E3 讨论与 coding agent 会话读取。规则见 [ADR0033](../../DECISIONS/0033-life-daily-convenience.md)，任务原文见
[prompt 归档](../design/prompts/2026-10-18-e1-daily-convenience.md)，证据见 [runs/2026-10-18-e1-daily-convenience](../../runs/2026-10-18-e1-daily-convenience/README.md)。

**状态：E1 内核已实现、自测通过，待独立复核。** 2026-10-18 补记：在声明的 V1 合成范围内通过独立审核，可以交接 E3；真实资料接入和参数标定尚未完成（[审核说明](../../runs/2026-10-18-e1-daily-convenience-review/README.md)、[audit.json](../../runs/2026-10-18-e1-daily-convenience-review/audit.json)；审核核查了什么、没有重跑什么，见 ADR0033 第八节）。本文是一份**可核验的交接说明**，不是共享接口本身：共享消息与词表由 E3 统一维护（见
[E3/E5 共享约定](../design/e3-e5-product-contract.md)第 2 节），本文不另造一套定义。待本轮独立复核之后，由所有者转交 E3 更新共同接口（复核已通过，见上）。

## 0. 先看：新便利版与旧默认配置

| 用途 | 当前配置与身份 |
|---|---|
| 现有 CLI 默认运行 | `configs/life/run_e1_7b_flash.yaml`，仍使用原生活配置（名单 `roster_e1_7b.yaml` → 协议 `life-v1-e1-7b-pilot`、评分 `e1-5c-health-v1`、提示模板 `e1-7b-prompt-v1`） |
| 本轮便利版合成运行 | `configs/life/run_e1_convenience_flash.yaml`（名单 `roster_e1_convenience.yaml`；mock 对照 `run_e1_convenience_mock.yaml`） |
| 便利版协议 | `life-v1-e1-convenience`（`synthetic_e1_convenience.yaml`） |
| 便利版评分 | `e1-convenience-v1`（`scoring_e1_convenience.yaml`） |
| 便利版提示模板 | `e1-convenience-prompt-v1` |

1. "旧默认"指生活配置与协议，不代表回退到 Max；两份运行配置的产品模型都是 Flash（产品角色 `product_planner`、`qwen3.8-flash`）。
2. 本轮通过审核没有自动改变 CLI 默认配置：`scripts/run_life_e1_7b.py` 不写 `--config` 时仍是 `run_e1_7b_flash.yaml`。
3. E3 新产品链应**显式**采用便利版对应的协议、评分、目录、评价与提示模板，不能依赖不写配置时的旧默认。
4. 新配置是合成接线参考：E3 按真实资料生成匹配的配置，不能把合成样例（K51、M52）当成真实城市直接上线。
5. 不能只换 `material_points` 字段而其他部分仍接旧评分或旧说明书。已知的几种混搭在加载与开跑前检查里被拒绝（旧评分版本拒绝 `daily_convenience`
   点数；协议与评分规格的便利版本必须一致；便利协议的运行必须用新提示模板），但检查不能代替显式选对整套配置。也不能拿旧运行的中间状态接着跑便利版
   （评分内容身份不同，跨年额度由 `scoring_content_changed` 拒绝），从共同初始状态开始。
6. 旧配置与旧归档保留，维持历史复现。本轮不改变默认入口。

**E3 应读取的文件**：本文；[ADR0033](../../DECISIONS/0033-life-daily-convenience.md)（规则、本地选择与第八节审核结论）；
[E3/E5 共享约定](../design/e3-e5-product-contract.md)；[审核说明](../../runs/2026-10-18-e1-daily-convenience-review/README.md)；[E1-3 技术说明第 21 节](../design/e1-3-life-model.md#daily-convenience)
与参数目录 SC-16–SC-20；便利版七份配置 `configs/life/{synthetic,scoring,city_facts,evaluation,roster}_e1_convenience.yaml`、`run_e1_convenience_flash.yaml`、
`run_e1_convenience_mock.yaml`；代码入口 `src/common/life_scoring_contracts.py`（版本与规格）、`src/ledger/life_scoring_config.py`（加载检查）、
`src/econ/life_scoring.py`（便利分）、`src/agentlayer/life_planning.py`（说明书）；测试 `tests/life/test_daily_convenience.py`。

## 1. 做到了什么

| 内容 | 状态 | 位置 |
|---|---|---|
| 新评分版本 `e1-convenience-v1`：物质三项之一"日常生活便利"，只读资源账里实际减少的家务小时 | 已实现、自测通过、待独立复核；2026-10-18 在声明的 V1 合成范围内通过独立审核 | `src/econ/life_scoring.py`、`src/common/life_scoring_contracts.py` |
| 加载检查、覆盖报告、内部对象隔离、内容身份与跨年额度绑定 | 同上 | `src/ledger/life_scoring_config.py`、`src/econ/life_coverage.py` |
| 合成样例：两座合成城（K51 设备便宜、M52 人工服务便宜）、一位用户 | 同上，**纯合成** | `configs/life/*_e1_convenience*.yaml` |
| 行动说明书的"日常杂务：设备与人工服务"一节（新提示模板） | 同上 | `src/agentlayer/life_planning.py` |
| mock 规则 `e1-convenience-mock-rules-v1`、期末估值的六个模板 | 同上 | `src/agentlayer/life_mock.py`、`configs/life/evaluation_e1_convenience.yaml` |
| 限额真实试跑（Qwen3.8 Flash） | 已运行，结果只说明这一次合成试跑：K51 使用人工服务完成三年；M52 连续提交不合法计划，没有开始实际生活，归为 M（模拟缺失），不是 E（生活出局）；真实模型没有执行设备购买，设备路径由离线与 mock 验证；6 次请求的费用按报价及账本舍入规则估算为 0.072675 元。不说明模型规划能力或真实城市适用性 | 证据目录 `live/` |
| 六维＋独立健康的产品输入适配 | **没有做**（E3-A 的任务）。2026-10-18 补记：E3-A 已实现、离线自测通过，待独立审核（[ADR0034](../../DECISIONS/0034-e3a-product-input-contract.md)） | 见第 6 节 |

## 2. 物质三项：字段与含义

物质细项点数（定和 100，用户明确给出）在这个版本的键是：

| 键 | 含义 | E1 怎样评分 |
|---|---|---|
| `housing_space` | 居住空间 | 实际住的那一处的面积（规格 `floor_area_sqft`） |
| `fresh_food` | 新鲜食品 | 实际执行的食品方案每周几顿新鲜餐（规格 `fresh_meals_per_week`） |
| `daily_convenience` | 日常生活便利：**用户愿意在多大程度上花钱减少日常杂务** | 这个月本人实际少做的家务小时 q（见下） |

    q = Σ_{洗衣, 打扫}（协议家务基准小时 − 当月资源账里本人的残余小时）      s = min(0.9, q / (q + 8))

* **点数的含义**：`daily_convenience` 点数表示相对于其他需求的重视程度，不直接表示愿付金额，也不是必须购买的指令。
* 实现方式有两种：**设备**（一次付款、服务期内持续减负）与**人工服务**（按月付款、实际买到的月份减负）。用户不分别填"设备偏好"和
  "服务偏好"，由分身根据偏好、报价、现金与时间安排去选。
* 同一项家务几个方案同时在用，只按残余最少的那一个算（结算写出的资源账已经这样记），不相加；第二台同样的设备不多减负。
* **没有第二份奖励**：便利替换旧洗衣分，不叠加购买奖励、花钱奖励或目标兑现奖励。减少杂务本身对应便利满足感；剩余未安排时间不额外获得休闲或
  满足感。实际利用腾出的资源参加其他活动时，按该活动原有规则评价（例如腾出精力去上课，课程照学习与成长规则评）。行动说明书里"省下的时间不会自动
  变成休息、休闲或满足感"按这个意思读；这次只订正文档解释，冻结的提示内容与提示版本不改。
* 这是 V1 主动采用的**粗粒度近似**：喜欢高端电子产品的人不一定愿意请人上门做家务，对陌生人进家的顾虑、服务质量、品牌都没有区分。
  不要把它描述为完整的物质生活评价。

**旧 `laundry` 输入不能自动当作新便利偏好（不能自动重解释为 `daily_convenience`）。** 旧问卷问的是"有没有洗衣条件"，含义不同：新评分版本在加载、年度交接与评分三处拒绝
`laundry` 键；旧评分版本也拒绝 `daily_convenience` 键。旧样例、旧配置与旧归档照旧按旧规则运行。

## 3. 最小合法样例

**用户（当前 E1 外层画像契约，仍是七维合计 100）：**

```yaml
- person_id: u301
  healthcare_semantics: health_risk_coping_v1
  occupation: {scheme: SOC2018, code: "43-9061"}
  dimension_points: {material: 40, safety: 5, healthcare: 10, opportunity: 10, leisure: 15, environment: 5, commute: 15}
  material_points: {housing_space: 30, fresh_food: 30, daily_convenience: 40}
  free_text: 下班以后不想再把很多时间花在洗衣服、打扫屋子上，愿意为省事花一些钱；但也不想因为花钱让自己手头太紧。
```

**协议的家务基准（全组共用，结构化字段）：**

```yaml
household_tasks:
  - {task: laundry, baseline_hours_per_month: 12, burden_per_hour: 0.5, description: 洗衣：……}
  - {task: cleaning, baseline_hours_per_month: 10, burden_per_hour: 0.6, description: 打扫：……}
```

**两种机会（城市目录；数值全部合成）：**

```yaml
- opportunity_id: k51.durable.convenience_pack        # 设备：一次付款，服务期内持续
  category: durable
  price: {amount: {amount: 1440, numeraire: USD_2024_LOCAL}, billing: one_time, per_unit: appliance-set}
  duration: {kind: durable_good, lasts_years: 5}       # effective_from 默认 same_year：买下当月起作用
  resource: {basis: passive, substitutes: [{task: laundry, residual_hours_per_month: 4},
                                           {task: cleaning, residual_hours_per_month: 6}], note: ……}
- opportunity_id: k51.service.chores                   # 人工服务：按月付款，只在买到的月份起作用
  category: service
  price: {amount: {amount: 216, numeraire: USD_2024_LOCAL}, billing: per_month, per_unit: service-month}
  duration: {kind: ongoing_service}
  resource: {basis: per_service_month, hours: 0, burden_per_hour: 0, quantity_scales: false,
             substitutes: [{task: laundry, residual_hours_per_month: 2}, {task: cleaning, residual_hours_per_month: 2}], note: ……}
```

**月度输出（买了设备包的那个月；节选，内部评分对象不进观察）：** 资源账的家务行 `laundry 4 小时（负担 2）`、`cleaning 6 小时（负担 18/5）`，
都记 `opportunity_id = k51.durable.convenience_pack`；评分分项：

```json
{"component_id": "daily_convenience", "status": "known", "z": 12.0, "raw": 0.6, "s": 0.6, "cap": 0.9, "tau": 8.0,
 "convenience": {"relief_hours": {"exact": "12"}, "tasks": [
   {"task": "laundry", "baseline_hours": {"exact": "12"}, "residual_hours": {"exact": "4"}, "relief_hours": {"exact": "8"}},
   {"task": "cleaning", "baseline_hours": {"exact": "10"}, "residual_hours": {"exact": "6"}, "relief_hours": {"exact": "4"}}]}}
```

资源账这个月不是完整检查、缺某项家务的行 → 这一项**未知**（有正权重时这个月总分不完整，不补 0）；重复的家务行、残余越界 → 拒绝。
没有采用任何方案 → 确定的 q = 0（不是未知）。

## 4. E3 将来要提供的资料

每个能替代家务的方案（设备或服务）至少要有：

| 资料 | 口径 | 缺了怎么办 |
|---|---|---|
| 报价 | 当地基年不变价（`<币种>_<基年>_LOCAL`）、计费周期（一次性 / 按月）、计价单位；同规格同口径 | 报价未知不填 0，方案标资料未知 |
| 有效期 | 设备寿命（年）、何时开始起作用（当月 / 下一年）；服务按实际付费的月份 | 不知道就写未知，不按"常见寿命"默认。当前 E1 可执行的耐用品要求明确的 1–30 年寿命（`MAX_DURABLE_YEARS = 30`）：寿命缺失保留在原始资料与适配层，不能填 null 后继续执行，也不能静默补值 |
| 供给 | 可得 / 确定不可得 / 资料未知，三者分开 | 目录里没有记录不等于确定没有 |
| 减负效果 | 每项代表性家务的"替代后本人残余小时"，与协议的家务基准同一单位（小时/月） | 见下 |
| 来源 | 观测 / 派生 / 估计 / 合成，写明地理粒度与时期 | 合成设定照实标合成 |

**消费价格指数不能单独证明"节省多少时间"。** 价格资料只说明花多少钱，不说明用了以后本人还要做多少小时家务。减负效果缺资料时，
必须**明示**是全组共用的统一假设（例如"各城同一款设备包的残余小时相同"，标成合成或估计）或记为未知，不能从价格倒推效果，也不能
因城市而悄悄改基准。统一效果假设必须显式登记（合成样例的登记见参数目录 SC-18）。同一比较组用同一套任务定义、单位与换算规则。

**覆盖报告的读法。** 当前便利覆盖报告的 `city_data=complete` 只说明评分要读的资料在这份合成目录里齐全，不能展示成"城市设备与服务目录已完整采集"。
E3 应分别报告两件事：实际轨迹的可评分性（这条生活的每个月能不能评），与可选方案的资料覆盖（候选设备与服务的报价、寿命、供给与减负资料齐不齐）。

## 5. 其他保留但未评分的东西

品牌、菜系、档次这类方案属性可以保留在 `attributes` 里供分身参考，本轮**不评分**（评分报告会写"已记录、本轮未评价"）；不从价格推断
品质偏好，也不重复奖励同一功能。

## 6. 仍归 E3 / E5 的事

* **E3**（E3-A）负责六维生活偏好＋独立健康应对的输入边界适配，本轮没有完成它（新问卷的物质细分要映射到这三项时，显式提升词表版本并让用户确认，不自动稀释旧权重）、
  职业、标准初态、真实资料与业务服务；共享接口与词表由 E3 统一维护。本轮的合成例子仍经当前 E1 的七维外层画像契约运行。（2026-10-18 补记：E3-A 已显式
  适配——六维产品画像与独立健康回答经 `scripts/product_e3a.py` 编译进入便利版整套配置，物质三项沿用本文第 2 节的键，旧 `laundry` 需重新确认；便利版的
  七维样例与历史归档不变。已实现、离线自测通过，待独立审核，见 [ADR0034](../../DECISIONS/0034-e3a-product-input-contract.md)。）
* **E5** 负责界面，使用同一接口做呈现、不另写评分算法：展示"日常生活便利"时用"愿意花钱减少日常杂务"这类说法，并提示它是粗粒度近似；不在前端另算分。
* **仍未支持**：生病时暂停零参与时间的按月服务以省钱（`pause_order` 只接受有参与时间的活动，`cancel_purchases_when_ill` 只覆盖一次性耐用品），
  本轮只保留限制说明。
* 是否让产品默认运行改用新协议，由所有者决定（本轮没有改产品入口的默认配置；审核通过后仍没有改，见第 0 节）。
