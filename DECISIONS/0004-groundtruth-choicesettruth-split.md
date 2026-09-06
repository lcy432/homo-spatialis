# 0004 真值对象按层面拆成两个

**状态**：已采纳（v0.9 迁移期，细化 MIGRATION B1）｜**落点**：`src/econ/`（需硬规则 3 报备）

## 决定

```
GroundTruth      键 = persona × city × year        城市层面
    features, utility, ln_utility, ln_kappa, coefficients,
    equivalent_variation        ← 固定参照城市，城市层面

ChoiceSetTruth   键 = persona × 选择集 × year      集合层面
    welfare_gap, welfare_dispersion, welfare_range, argmax_city
```

## 为什么不全塞进 GroundTruth

MIGRATION B1 要求新增五个字段。其中只有 `features` 是城市层面的量，
`welfare_gap` / `welfare_dispersion` / `welfare_range` / `ev_by_city` 都是**选择集
层面**的：同一座城市放进不同的选择集，这几个数就不同。

而 `score(persona, city, year)` 的签名是单城市的。把集合层面的量挂在它上面有
两个后果：

1. **语义错位。** 字段看起来像城市的属性，实际是"这座城市 + 这个选择集"的属性。
   这类错位不会报错，只会让某个下游算出对不上的数——正是硬规则 4 防的那一类。
2. **N 倍重复计算。** 12 座城市，每座都要把全集合的 EV 算一遍。

拆开之后 B1 的意图完整保留：M5 仍然只靠真值对象就能拼出设计矩阵，不引用
`EconomicCore`（MIGRATION B3）。代价是 M5 拿两个对象而不是一个。

## `ev_by_city` 去哪了

它变成 `GroundTruth.equivalent_variation`（单城市，固定参照城市），
M5 需要"全集合的 EV"时按 persona × 选择集聚合。等价变差**必须有一个固定的参照
城市**才有意义，而参照城市的选择是集合无关的，所以 EV 本身确实是城市层面的量。

## 工时提醒

`equivalent_variation` 目前恒为 `None`——**等价变差根本没实现**。PLAN 估 P2 为
0.5 天，那只覆盖"加字段"。EV 要先有：定参照城市、在 Cobb-Douglas 闭式下解
`V(Y+EV, c₀) = V(Y, c)`、处理 `κ` 与 `w₁` 的幂次、并按硬规则 13 保证永远在真值
偏好参数下评价。加上本决策的接口拆分，实测工时按 1.5–2 天准备。

C1 的三条基准线（`L_rep`/`L_s`/`L_amen`）全部依赖 EV，门一又依赖 C1，所以这条
线上的低估会直接推迟门一。
