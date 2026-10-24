# E3B 映射修正交给界面层（E3 → E5）

日期：2026-10-24。规则见 [ADR0044](../../DECISIONS/0044-e3b-mapping-daily-support.md)，技术说明见 [E3B 映射修正](../design/e3b-mapping-and-daily-support.md)。本轮只提供接口、样例与迁移说明，**没有改 E5 页面**。

## 1. 唯一要读的版本

* 公共结构 `e3a-product-schema-v3.3`；问卷 `e3a-questionnaire-v0.3-draft`；词表 `e3a-vocabulary-v4`（健康问卷与转换版本不变）。
* 权威结构仍是 `src/common/product_contracts.py`；schema 与样例在 [`contracts/product/v3/`](../../contracts/product/v3/README.md)（目录名不变），用 `uv run python scripts/product_e3a.py check-schema` 核对（28 份 schema、93 份样例）。
* v3.2 的字段含义不变；v3.3 新增的都是可选字段，空的不序列化。

## 2. 用户需要重新确认的文案

物质四项里有两项含义改了。界面显示 `material_leaves[]` 的 `label` / `definition` / `note` 原话，不另写解释：

| 键 | 新标签 | 新定义（原话） |
|---|---|---|
| `daily_convenience` | 日常支持与便利 | 通信联网、家用日耗品是否方便获得，以及日常杂务是否省心。 |
| `personal_enjoyment` | 个人消费与享受 | 在其他生活开支之外，获得衣着、个人护理等个人消费的满足。 |

内部键不变。旧的确认画像（问卷 v0.2 / 词表 v3 / 结构 v3.2）提交时会得到 `version_changed` 问题（`field` 指到 `meta.questionnaire_version` 等），`next_action_zh` 是“按当前版本重新填写或重新确认相关答案”：界面应把用户带回审阅页，让他看过新文案后重新确认，不要静默沿用旧点数。

## 3. 结果里的新事实（`years[]`）

| 字段 | 内容 | 样例 |
|---|---|---|
| `support[]` | 三项日常支持各一行：`function`（`personal_communication` 个人通信 / `home_connectivity` 居家联网 / `household_supplies` 家用日耗品）、`label_zh`、提供它的机会编号、计划的月份、实际获得的月份（付了钱、服务在用）、没获得的月份。同一功能买两份不多算；没获得的月份这项功能就是没有，但不会因此出局、生病或少拿工资，也不节省任何小时 | 10、23、24、50、52、55 |
| `consumption` | 个人消费篮子：每月实际获得的标准篮子数（12 个字符串，精确写法；“0” = 没有，“—” = 那个月没有生活）、机会编号、计划了却没获得的月份。它是当月的消费，不是资产，不进期末财富 | 10、23、24、44、50、52、55 |

不再出现的东西：按摩、舒适阅读椅、靠垫（合成样例与真实资料包都删了）；生活扩展候选的编译检查里，享受类的通过例子换成消费篮子（样例 48）。

## 4. 机会目录里的新机会（给分身看的同一份公开事实）

* 三项支持服务：`functions` 写着 `personal_communication` / `home_connectivity` / `household_supplies`，按月付费，不占时间。价格怎么来写在 `attributes.cost_basis`：`ce_one_person_budget_proxy`（CE 一人消费单位的平均支出，预算代理）、`fcc_regional_reference`（FCC 2024 城市费率调查同规格报价的大区中位数）、`ce_one_person_budget_x_rpp_goods`（CE 一人支出 × 商品类价格系数）。`observed_unit_price: false`——都不是本地观测的单价，界面不能写成“当地价格”。
* 个人消费篮子：档位 0.25、0.5、1、1.5、2、3、4 个标准篮子（合成样例只放 0.5、1、2），`enjoyment[0].units_per_month` 是每月几个标准篮子，`attributes.standard_basket_price` 是本城一个标准篮子的估计价。
* 食品方案变成完整网格：每城 327 个（3 个篮子 × 家里做 0 / 3 / 7 / 14 / 21 顿 × 堂食 / 外送 / 各半 × 9 个菜系；家里做 21 顿时只有一个）；外食价格按统一成本代理，`attributes.taxes_fees_tips = not_itemized`；`cuisine_supply_evidence` 写本城有没有足够的 OSM 餐馆证据。
* 住房与岗位：每份年度菜单 60 项，从完整候选池抽；住房 `quality` 写结构、房间数、厨卫与租金来源（同一条 ACS 记录或插补）；岗位按工资分位与真实工时（低工时 = 每天工作短）。

## 5. 构建计数的新键

`data_nature.build[]` 读冻结资料包报告：`housing_candidates`（完整可执行住房池）、`job_candidates`（完整可执行岗位池）、`jobs_in_catalog`、`start_jobs`，排除原因 `excluded.housing.no_commute_area`、`excluded.job.*`。旧键 `housing_executable`、`jobs_placed` 不再出现。

## 6. 没变 / 不要做

* 评分系数、先验与后验、权重都不在任何公共消息里，界面不要推测或显示。
* 真实模型跑完整 E3B 目录现在超过上下文上限（见技术说明）；界面不要因此缩减或筛选菜单。

## 7. E3B 收尾的变化（2026-10-24）

规则见 [ADR0044](../../DECISIONS/0044-e3b-mapping-daily-support.md) 的“收尾”一节。公共结构的字段没有变（仍是 v3.3），样例重新导出（93 份，`check-schema` 通过）；本轮同样**不要求改 E5 页面**，只需知道下面几件事：

1. **新的阻断问题 `planning_request_too_large`**（预检查 `issues[]`，`field` = `cities.<城市编号>`）：某城首年规划请求的估计输入超过本次规划器的单次上限时，预检查不通过，每城一条。`message_zh` 写估计数与上限，`impact_zh` 写“这次模拟不能开始、不删减选项、不换模型、没有发请求、没有使用次数”，`next_action_zh` 写下一步。界面照常显示这三段原话，不要提供“删掉一些选项再试”之类的入口。托管服务启动前会用真正的运行编号再核对一次，超限时运行停在 `request_too_large`、预留次数退回。
2. **住房价位变了**：资料包加了普通住房代理（每个都会区 × 卧室组的租金 P10 以下不进普通可执行池），最低住房与菜单里的住房每月至少约 680–1,234 美元（按城不同），不再出现每月几美元到几十美元的住房。`data_nature.build[]` 里的 `housing_candidates` 现在数的是过了这道代理之后的普通可执行池（每城 1.3 万–8.5 万）。
3. **岗位的入职要求文字**：`attributes.related_experience` / `on_the_job_training` 里以前的 “nan” 现在是 BLS 原文 “None”（不需要）。
4. **宽带回退文案**：FCC 资料不可得时，居家联网的名称与说明换成 CE 预算代理的写法、不写速度档；现在六城都有 FCC 资料，这段文案不会出现。
5. **参考住房**是内部评价资料（估值期初按分身实际的岗位选），不在任何公共消息里，界面不要显示或推测。

### 7.1 最后收尾（2026-10-24）要界面层做的事

* **4 份旧草稿夹具**：`web/tests/fixtures/drafts/` 的 4 份草稿还是按公共结构 v2 生成的，所以 `tests/product/test_web_drafts.py` 的 4 条测试跳过（不是需要真实模型的测试）。请按现有 v3.3 公共结构与最终样例重新生成（在 `web/` 里运行 `UPDATE_DRAFT_FIXTURES=1 npx vitest run tests/draft-fixtures.test.ts`）并恢复这 4 条测试；本轮不改页面，也不另升公共结构版本。
* **停机预算 20 元**：正式服务每次运行的停机预算改成 20 元（`configs/product/service.yaml`）。预检查与进度里的 `max_cost` 照原样显示服务给的值，不要写成“保证花多少”——20 元是到了就停下的上限，预计花费另算。
* 请求里中文不再拆成模板碎片、Flash 输入护栏改成 192,000，都只影响发给模型的文字，没有公共字段变化。
