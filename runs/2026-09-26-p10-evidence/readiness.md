# Readiness：还差什么，按什么顺序补

三类阻塞**分开**列，因为处置方式不同：

    missing_input     缺真实数据 → 去取数
    pending_computation  数据在、算力没花够 → 去跑
    invalid_design    设计本身不匹配 → 去改设计，跑了也不算

**工程 synthetic PASS 与真实门判定永远分开。** 合成臂上的 PASS 只证明接口与
判据可执行，不是坍缩结论。

---

## 一、当前状态

| 项 | 状态 | 类别 |
|---|---|---|
| P9 研究主规格与拟合入口 | 已实现，主规模跑过 baseline | — |
| 门一（长通勤 profile） | **PASS**，五档全过 | — |
| P10 分层、配额、权重、两个报告 | 已实现，小型验证 | — |
| 估计对象 P/Q/S 与 ddof=0 矩 | 已实现，小型验证 | — |
| 方案 A 的 `t` 路径 | 已实现，小型验证 | — |
| 逐人表与分组对照 | 已实现，小型验证 | — |
| 联合窗口 SE 与 `pending_computation` | 已实现，小型验证 | — |
| `R_moment` @200 积分精度 | **established** | — |
| `R_post` @200 积分精度 | **not_established**（1000/1000 超容差，中位 3.3e−2） | pending_computation |
| `R_fit` @ 200 的充分性 | **未验证** | pending_computation |
| 高/低方差案例的主规模扫描 | 未跑 | pending_computation |
| 正式 B = 200–500 | 未跑 | pending_computation |
| 实测重复子集 | **没有** | missing_input |
| 真实匹配臂 / 打乱臂 | **没有** | missing_input |
| σ 的正式标定 | 依赖重复子集 | missing_input |
| 门二正式判定 | 依赖上三项 | missing_input + pending_computation |
| 门三正式判定 | 依赖重复子集 | missing_input |
| 散文语义忠实核查 | 无 `PersonaBrief` 抽取规则 | missing_input |

**没有任何一项可以写成"门已通过"。**

## 二、执行顺序与命令

每一阶段**落盘之后**再启动下一阶段。不要同时后台启动多个长任务。

### 阶段 1 — 补数值精度与主规模参考验证（零大模型调用）

```bash
# 1a 固定参数积分精度（已跑过一次；改容差或换参数后重跑）
uv run python scripts/diagnostics/check_integration_precision.py --budget-seconds 1500

# 1b R_fit 的参数重估扫描。**6–10 单核小时，需显式批准。**
#    它与 1a 问的不是同一件事：1a 固定参数，1b 重估参数。
uv run python scripts/diagnostics/check_integration_precision.py --help   # 见方案乙说明

# 1c 主规模三个校准案例（baseline / 0.5α / 2α），约 45 分钟
uv run python scripts/run_p8.py --config configs/p8.yaml --out runs/<id>/
```

### 阶段 2 — 导入真实先导数据

需要三份输入，schema 见第三节。**先导数据的 N/T/J 或抽样设计若与后续主实验
不同，地板必须按先导的实际设计另行匹配**——先导门通过**不能**直接转移到主实验。

### 阶段 3 — σ 标定与对应设计的正式地板

```bash
# 分歧率二分标定，跑在重复子集自己那批 scenario 上（硬规则 25）
# 绝不从匹配臂的 σ̂ 反查
uv run python scripts/run_p8.py --config configs/p8.yaml --sigma-from-repeats <path>
```

### 阶段 4 — 正式不确定性

```bash
# B = 200–500。线性外推 48–69 单核小时（**是外推不是实测**）。
# SE(W) 取同一 replicate 内 (s_shuffled − λ) 的变化。
uv run python scripts/run_p8.py --config configs/p8_formal.yaml --bootstrap 300
```

### 阶段 5 — 门二/门三与条件报告

只有阶段 2–4 齐了才跑。**输入未到时不得形成"必须先过门才能取得门输入"的循环**：
本轮只准备接口，不自行花 LLM 预算。

## 三、三份真实输入的最小 schema

### 3.1 重复子集（门三 + σ 标定）

```
persona_id      str    必须在主名单里
scenario_id     str    同一 (世界, 人设) 交叉点
repeat_index    int    0..R−1，**同题重复**
chosen_city_id  str
world_id        str
model_version   str    带日期戳的快照
```

**必须是同人同世界的真实重复运行。** 不同任务的不同选择**不是**重复不稳定。
共享重复记录的成对分歧**不是独立样本**，按人设处理依赖。

### 3.2 匹配臂

```
persona_id, choice_set_id, alternative_id, chosen, world_id, model_version
```

覆盖要求：主名单的每个人至少 T 个不同选择集；**部分覆盖不得伪装成全覆盖**，
缺失臂**不能当零**。

### 3.3 打乱臂

同上，另加 `permutation_seed` 与 `source_persona_id`（被打乱到谁）。
规模为主实验的 30%。**配对关系按实际覆盖保存。**

### 3.4 校验与错误

| 错误 | 处置 |
|---|---|
| persona_id 不在主名单 | `invalid_design`，拒绝 |
| 同一 (persona, choice_set) 出现多次且非 repeat | `invalid_design` |
| 覆盖低于声明 | 如实报实际覆盖，不补零 |
| 设计字段与地板不匹配 | `FloorReuseError`，拒绝复用 |

## 四、循环依赖的拆法

"必须先过门才能取得门输入"是一个真实的风险。拆法：

1. **门一**已过，且它不依赖任何 LLM 输入。
2. **门二/门三的输入是先导数据**，先导数据不需要过门就能取——它本身就是为了
   判门而取的。
3. 先导规模远小于主实验，成本可控。
4. **先导的地板必须按先导的实际设计匹配**，不能借用主实验设计的地板。

所以顺序是：取先导 → 按先导设计匹配地板 → 判门 → 再决定要不要花主实验预算。
