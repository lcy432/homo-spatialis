# 产品共享结构 v3（生活扩展）

v3 取代 [v2](../v2/README.md)（v2 目录原样保留为历史导出，界面层改读 v3 之前仍在用）。全部样例都是合成人物与合成城市；离线规划器是 mock，不是真实大语言模型。规则见 [ADR0041](../../../DECISIONS/0041-life-extension-food-enjoyment-hobbies.md)，技术说明见 [生活扩展](../../../docs/design/e1-8-life-extension.md)，给界面层的改动清单见 [交接](../../../docs/handoffs/e3-to-e5-life-extension.md)。

## 1. 这里有什么

* `*.schema.json`：14 份消息的 JSON Schema，由唯一权威结构 [src/common/product_contracts.py](../../../src/common/product_contracts.py) 导出，**不要手改**。
* `index.json`：结构版本、问卷 / 词表 / 健康问卷 / 转换版本，以及每份 schema 的 SHA-256。
* `samples/`：49 份公共样例，每份是 `{"message", "description_zh", "payload"}`，`payload` 按对应 schema 校验。

```bash
uv run python scripts/product_e3a.py check-schema
```

## 2. 版本

| 项 | v2 | v3 |
|---|---|---|
| 结构 | `e3a-product-schema-v2` | `e3a-product-schema-v3` |
| 问卷 | `e3a-questionnaire-v0.1-draft` | `e3a-questionnaire-v0.2-draft` |
| 词表 | `e3a-vocabulary-v1`（物质三项） | `e3a-vocabulary-v2`（物质四项） |
| 资料包 | `pack.e3a.synthetic-v1` | `pack.e3a.synthetic-v2` |

## 3. 新样例

| 样例 | 消息 | 演示 |
|---|---|---|
| 39–43 | ProfileReview | 兴趣与口味：确认（含没认出与有歧义的片段、品牌只留存）、未答、通用评价、没认出、词表外 |
| 44–47 | YearFacts | 买到并当月练琴、买了没练、生病取消与暂停 + 网球套餐 + 没有吉他练不成、年初旧吉他到期 |
| 48 | LifeItemCompilation | 生活扩展候选的编译检查：可执行、拒绝、本版未支持、资料未知 |
