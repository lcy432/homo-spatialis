# 要求 — 有效来源 — 实现位置 — 测试 — 状态

基准 commit `1f2cb9d`（开工时工作区干净，无未提交改动需要保护）。
`AGENTS.md` 不存在；本仓库的开发约定在 `CLAUDE.md`。

## 研究规格与兼容性

| 要求 | 有效来源 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| 主规格 = ALR + 完全协方差 + material 基准 | 用户本次裁决，`DECISIONS/0013` | `eval/recovery/profiles.py` | `test_floors.py::test_the_profile_fingerprint_carries_everything_that_must_match` | 完成 |
| 四固定起点、按似然选解 | 用户本次裁决 | `profiles.fit_research` | `::test_the_research_fit_selects_by_likelihood_not_by_recovery` | 完成 |
| 全部起点失败 → 结构化失败，不退回 | 用户本次裁决 | `profiles.ResearchFitError` | 由 `fit_research` 的 raise 路径覆盖 | 完成 |
| 底层默认值保持兼容 | 用户本次裁决 | `RecoveryConfig` 未动 | `::test_the_library_default_is_untouched_by_the_research_profile`；`test_recovery_golden.py` 全套 | 完成 |
| P8 与主实验共用同一拟合入口 | 用户本次裁决 | `floors/*` 一律调 `fit_research` | `::test_the_bootstrap_uses_the_same_research_fit_as_the_point_estimate` | 完成 |
| 差分/ALR 对角保留为历史对照 | `DECISIONS/0011` | 未改动 | `test_recovery_golden.py`（逐位） | 完成 |
| 为长通勤 profile 单跑门一，不改判据 | 用户本次要求；`DECISIONS/0012` 六 | `configs/gate_one_long_commute.yaml`、`run_gate_one.py --config` | `tests/eval/test_gate_one_calibration.py` | 完成，PASS（`runs/20260917T075153Z-c45fca`） |
| 旧门一结果适用范围不扩大 | 用户本次要求 | 两份配置哈希不同 | 同上 | 完成，默认路径复跑逐位一致 |
| 核查与拟合解耦、可复用缓存 | 用户本次要求 | `check_base_change.py` 的 `FitParams` / `load_cache` / `--allow-refit` | `tests/eval/test_minimal_check_cache.py`（10 条） | 完成，走缓存 ~1 分钟（原 ~35 分钟） |
| 缓存须校验数据、规格、算法 | 用户本次要求 | `cache_key` + 缓存参数上**重算似然** | `::test_parameters_that_do_not_belong_to_this_data_are_rejected` 等 3 条 | 完成 |
| 改文档/断言不使缓存失效 | 用户本次要求 | `cache_key` 不含脚本文字与容差 | `::test_editing_prose_or_a_tolerance_does_not_invalidate_the_cache` | 完成 |
| 缓存缺失不得静默重拟 | 用户本次要求 | 缺失 → 报告 + 退出码 2 | `::test_a_missing_cache_refuses_instead_of_fitting` 等 3 条 | 完成 |

## P8 功能（第五节）

| 要求 | 有效来源 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| 5.1 真值 softmax 偏差表 | PLAN P8 产出 1、6；README §10.3 | `floors/bias.py` | `::test_a_small_end_to_end_bias_table_is_produced` | 完成 |
| 5.1 高/低方差校准案例（cα） | PLAN P8 产出 1 | `bias.calibration_alphas` | `::test_the_calibration_cases_change_dispersion_but_not_the_theoretical_mean` | 完成 |
| 5.1 理论矩与实现矩分开 | 用户本次要求 | `bias.MomentSet.source` | `::test_a_small_end_to_end_bias_table_is_produced` | 完成 |
| 5.1 不逐元素除整个协方差 | 用户本次要求 | 只报对角比值 + Frobenius | 同上 | 完成 |
| 5.2 三臂共用中心/d/u | `DECISIONS/0002`，硬规则 24 | `floors/geometry.py` | `::test_all_arms_share_one_centre_and_one_d_and_u` | 完成 |
| 5.2 现行 r 公式，禁旧式 | `DECISIONS/0002` | `geometry.collapse_ratio` | `::test_the_collapse_ratio_uses_the_measured_shuffled_slope` | 完成 |
| 5.2 不夹到 [0,1] | README §1.2.2 | 同上 | `::test_the_collapse_ratio_is_not_clipped_to_the_unit_interval` | 完成 |
| 5.2 d 近零标记并计数 | 用户本次要求 | `geometry.D_NEAR_ZERO` | `::test_d_near_zero_is_flagged_and_excluded_rather_than_epsilon_patched` | 完成 |
| 5.2 打乱只破坏配对 | README §11.10 | `design.build_shuffled_arm` | `::test_shuffling_breaks_only_the_pairing` | 完成 |
| 5.2 合成臂与真实臂来源标签不同 | 用户本次要求 | `FloorArm.source`、`ArmSource` | 同上 | 完成 |
| 5.3 Σ_w(t) 路径 | README §1.2.2；PLAN P8 产出 3 | `floors/path.py` + `runner._lambda_path` | `::test_the_moment_calibration_actually_hits_the_target_not_just_returns_an_array` | 实现完成；两个案例分别记录，见下节 |
| 5.3 可行性不只看 PSD | 用户本次要求 | `path.simplex_moment_feasibility` | `::test_psd_alone_does_not_establish_simplex_feasibility` | 完成 |
| 5.3 目标矩被钉死、不重估总体 | 用户本次要求；`DECISIONS/0013` 六 | `alr_posterior_at` 在固定参数上算 | `::test_an_infeasible_target_is_refused_rather_than_approximated` | 完成 |
| 5.4 分歧率标定 σ | `DECISIONS/0003`，硬规则 25 | `floors/sigma.py` | `::test_sigma_calibration_recovers_a_known_sigma_from_synthetic_repeats` | 完成（合成验证） |
| 5.4 方向不写反 | `DECISIONS/0003` | 同上 | `::test_disagreement_increases_with_sigma` | 完成 |
| 5.4 上界 1 − 1/n | `DECISIONS/0003` | 同上 | `::test_a_disagreement_above_the_random_ceiling_has_no_solution` | 完成 |
| 5.4 无实测 → pending_input | 用户本次要求 | 同上 | `::test_without_real_repeats_the_status_is_pending_input_not_a_default` | 完成 |
| 5.4 不从匹配臂 σ̂ 倒查 | 硬规则 19 | `sigma.py` 无该路径 | — | 完成（缺席） |
| 5.5 门二 2a/2b/2c | `DECISIONS/0002` 第二节 | `floors/gates.py` | `::test_gate_two_*`（5 条） | 完成 |
| 5.5 2b 阈值缺失 → pending | 用户本次要求 | 同上 | `::test_gate_two_without_a_preregistered_2b_threshold_is_pending_not_pass` | 完成 |
| 5.5 门三与 MDE | README §10.3.4 | 同上 | `::test_gate_three_reports_an_mde_derived_from_residual_variance` | 完成 |
| 5.5 状态分档 | 用户本次要求 | `GateStatus` | 上述各条 | 完成 |

## 第六节（P10、抽样、不确定性）

| 要求 | 有效来源 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| P10 现状审计 | — | P10 **未实现** | — | 已审计：仓库无分层/报告函数 |
| P8 所需的分层输入契约 | 用户本次要求 | `ChoiceDataset.strata`（P9 已有） | `test_recovery_bootstrap.py::test_stratified_resampling_never_crosses_a_stratum` | 完成（契约 + 合成 fixture） |
| 按人设 block bootstrap | 硬规则 22 | `eval/recovery/bootstrap.py` | `::test_duplicate_personas_get_independent_panel_ids_with_recoverable_sources` | 完成 |
| 重复人设发独立面板 ID、保留来源 | 用户本次要求 | `take_personas` + `runner.source_persona_ids` | 同上 | 完成 |
| 每 replicate 走同一正式流程并重算几何 | 用户本次要求 | `runner._fit_research_adapter`、`_bootstrap_window` | `::test_the_bootstrap_uses_the_same_research_fit_as_the_point_estimate` | 完成 |
| 失败 replicate 记录原因与比例 | 用户本次要求 | `BootstrapOutcome.failure_reasons` | `test_recovery_bootstrap.py::test_a_failed_replicate_is_recorded_with_its_reason` | 完成 |
| 小 B 不充当正式区间 | 用户本次要求 | `_bootstrap_window` 的显式标记 | — | 完成（标记） |
| 配置指纹与不兼容拒绝 | README §10.3 | `ReferenceDesign.digest()` | `::test_two_designs_that_differ_anywhere_get_different_digests` | 完成（指纹）；**自动拒绝未实现** |

## Σ_w(t) 的两个案例（**不合并**）

新 pilot 全过**不等于**旧案例已解决：两次跑的是**不同的协议**，而旧案例的产出
没有留存。分别记录如下。

| 案例 | 协议 | 结果 | 产出 |
|---|---|---|---|
| 旧 smoke（本轮之前） | 只插值 Σ，均值固定在注入值 | **t=1 端点不可行**：`Var(commute)=0.1334` 超伯努利上界 `μ(1−μ)=0.0904`，标 `residual_above_tolerance`、不报 λ | **未留存**，只有本清单与 `DECISIONS/0013` 六的文字记录 |
| 新 pilot（2026-09-16） | 均值与 Σ 一起插值 | 五点全 `ok`，残差 ≤1.3e−4，λ 0.2562→0.2405 | `runs/2026-09-16-p8-synthetic-pilot/p8_selfcheck.json` |

**三条不得混淆的事：**

1. 旧案例的发现**没有被推翻**——"只插 Σ 在端点给出不可行矩对"仍然为真，它正是
   改协议的理由。
2. 新案例的成功**是换了协议之后的**，不是同一协议上的修复，因此不能说"端点问题
   已解决"，只能说"新协议下这五个点可行"。
3. **新协议本身待裁决**（`DECISIONS/0013` 六）：让均值跟着动改变了 `λ(t)` 曲线的
   含义，README §1.2.2 定义的是一条关于**协方差**的敏感性路径。

**流程教训**：旧 smoke 的 JSON 没留存，所以"旧案例与新 pilot 是否跑在同一批数据
上"现在已经无法核对。判定失败的产出与判定通过的产出同样是证据，都该进证据目录。

## 实际跑过的规模（**不要外推**）

| 项 | 跑到哪一步 | 产出 |
|---|---|---|
| baseline 校准案例 | **主规模已完成**：1000 人设 × 12 选择集 × 12 城，σ=0.05，四起点按似然选解（888s） | `runs/2026-09-16-p8-synthetic-pilot/p8_selfcheck.json` |
| 高/低方差案例（`cα`，c∈{0.5,2}） | **仅小样本覆盖**（`test_floors.py`，n=24）。主规模**没跑过**——pilot 配置写的是 `alpha_scales: [1.0]` | 无主规模产出 |
| bootstrap | **B=2，只验流程**。`is_engineering_smoke_not_formal_interval: true` | 同上 |
| σ 标定 | 合成重复数据验证接口，从 0.05 回收 0.0551，标 `synthetic_validation` | 同上 |
| 门一（长通勤 profile） | **PASS，五档全过**。对应修好 `--config` 之后的那次有效运行（`82328662d7f0125d`）；此前那次参数被静默忽略、实际跑的是默认配置，不计数 | `runs/2026-09-16-p9-evidence/gate-one/` |
| 最小数值核查 | 3.1 十三行全过；3.2 三个评价设置 | `minimal-numerical-check.json` |

**commute 的残余偏差不阻塞本参考设计。** 长通勤臂下 commute 方差比 0.835、个体
相关 0.583，明显差于其余六维（0.855–0.887）。这是**已知并已授权**的状态：P8 要量化
的正是估计器偏差，不是先把它消掉。`DECISIONS/0011` 裁决一另行了结，与 P8 能否开跑
是两件事。

## 未完成：按性质分三类

**待输入**（缺数据，不缺判断）

| 项 | 状态 | 缺什么 |
|---|---|---|
| σ 的正式校准 | `pending_input` | 实测重复子集 |
| 门二 2b 阈值 | `pending_input` | 预注册值。**不得事后看结果补** |
| 门二/门三的正式判定 | `pending_input` | 上两项 + 真实匹配/打乱臂（需大模型调用） |

**待科学裁决**（数据齐了也得有人拍板）

| 项 | 状态 | 要裁什么 |
|---|---|---|
| Σ_w(t) 协议：均值是否跟着插值 | 已实现，未裁决 | 让均值动改变了 λ(t) 的含义；README §1.2.2 定义的是协方差路径。见上一节两个案例 |
| `DECISIONS/0011` 裁决一（commute） | 未了结 | 组内 sd 已越过识别阈值，但回收相关仍只有 0.583。"损害可控"不等于"可识别" |

**待后续实验**（知道怎么做，本轮没做）

| 项 | 状态 | 要做什么 |
|---|---|---|
| 200 draws 对矩与个体后验是否够用 | **未验证** | 3.1 的恒等对任意 draws 数都成立，证明不了积分精度。需 R ∈ {200,800,3200} 的输出量对比，或独立重抽 draws 的模拟标准误 |
| 高/低方差案例的主规模扫描 | 未跑 | `configs/p8.yaml` 已配 `[0.5,1.0,2.0]`，约 45 分钟 |
| 正式 B=200–500 bootstrap | 未跑 | 能力已实现；线性外推约 48–69 单核小时（**是外推不是实测**） |
| P10 正式分层与两个报告函数 | 未实现 | 本轮明确不做 |
| 复用地板时的自动拒绝 | 未实现 | 指纹已具备，比对逻辑待 P10/主实验接入时加 |
