# 产品共享结构 v3（生活扩展）

v3 取代 [v2](../v2/README.md)（v2 目录原样保留为历史导出，界面层改读 v3 之前仍在用）。全部样例的人物都是合成的；01–50 用合成城市，51–54 用冻结的 E3B 六城真实资料包（`real_data_frozen`，[ADR0042](../../../DECISIONS/0042-e3b-real-life-pack.md)）；离线规划器是 mock，不是真实大语言模型。规则见 [ADR0041](../../../DECISIONS/0041-life-extension-food-enjoyment-hobbies.md)，技术说明见 [生活扩展](../../../docs/design/e1-8-life-extension.md)，给界面层的改动清单见 [交接](../../../docs/handoffs/e3-to-e5-life-extension.md)。

## 1. 这里有什么

* `*.schema.json`：14 份消息的 JSON Schema，由唯一权威结构 [src/common/product_contracts.py](../../../src/common/product_contracts.py) 导出，**不要手改**。
* `index.json`：结构版本、问卷 / 词表 / 健康问卷 / 转换版本，以及每份 schema 的 SHA-256。
* `samples/`：55 份公共样例，每份是 `{"message", "description_zh", "payload"}`，`payload` 按对应 schema 校验。

```bash
uv run python scripts/product_e3a.py check-schema
```

## 2. 版本

| 项 | v2 | v3 | v3.1（E3B，仍在本目录） |
|---|---|---|---|
| 结构 | `e3a-product-schema-v2` | `e3a-product-schema-v3` | `e3a-product-schema-v3.1` |
| 问卷 | `e3a-questionnaire-v0.1-draft` | `e3a-questionnaire-v0.2-draft` | 同 v3 |
| 词表 | `e3a-vocabulary-v1`（物质三项） | `e3a-vocabulary-v2`（物质四项） | `e3a-vocabulary-v3`（居住条件取代居住空间） |
| 资料包 | `pack.e3a.synthetic-v1` | `pack.e3a.synthetic-v2` | 同 v3，另加 `pack.e3b.us-v1`（`real_data_frozen`） |

## 3. 新样例

| 样例 | 消息 | 演示 |
|---|---|---|
| 39–43 | ProfileReview | 兴趣与口味：确认（含没认出与有歧义的片段、品牌只留存）、未答、通用评价（只写了本版不支持的“喜欢冲浪”，原文留存）、没认出、词表外 |
| 44–47 | YearFacts | 买到并当月练琴、买了没练、生病取消与暂停 + 网球套餐 + 没有吉他练不成、年初旧吉他到期 |
| 48 | LifeItemCompilation | 生活扩展候选的编译检查（v2）：可执行、拒绝、不可得、本版未支持、资料未知 |
| 49 | ProfileReview | 同一兴趣选了两次：审阅阶段拒绝 |
| 50 | ProductResult | 期末估值的范围说明：吉他按能力继续用，网球拍的用途参考模板没有考虑 |

v3 在独立复核之前按收尾修复（ADR0041 收尾一节）修订过一次；界面层还没有读 v3，所以没有另开 v4。

## 4. v3.1：E3B 真实资料包的公开事实

只加了可选字段，v3 的字段含义不变；空的新字段不序列化，所以合成样例只多出真实存在的事实（住处与片区、通勤、税、资料性质）。给界面层的改动清单见 [E3B 交接](../../../docs/handoffs/e3b-to-e5-real-pack.md)。

| 样例 | 消息 | 演示 |
|---|---|---|
| 51 | ProductPreflight | 真实资料包的预检查（休斯敦、西雅图）：逐城的资料性质 `data_nature` 与结构化范围说明 `scope_notes` |
| 52 | ProductResult | 真实资料包的结果：每年的住处与片区、逐月通勤、用车权、按哪份官方税规则预扣；逐城资料性质 |
| 53 | ProductPreflight | 旧金山的通勤关系表没有公共交通：警告 `commute_mode_missing` 与范围说明，预检查仍通过 |
| 54 | ProductPreflight | 没有确认高中学历：开局岗位要求的资格不从职称或自由文字授予，预检查阻断 |
