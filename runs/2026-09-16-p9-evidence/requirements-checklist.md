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
| 5.3 Σ_w(t) 路径 | README §1.2.2；PLAN P8 产出 3 | `floors/path.py` + `runner._lambda_path` | `::test_the_moment_calibration_actually_hits_the_target_not_just_returns_an_array` | 完成，端点有残差 |
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

## 未完成 / 待输入

| 项 | 状态 | 阻塞原因 |
|---|---|---|
| σ 的正式校准 | `pending_input` | 无实测重复子集 |
| 门二 2b 阈值 | `pending_input` | 仓库无预注册值 |
| 门二/门三的正式判定 | `pending_input` | 上两项 + 无真实匹配/打乱臂 |
| Σ_w(t) 的 t=1 端点 | `residual_above_tolerance` | logistic-normal 未必能精确表示该目标 |
| P10 正式分层与两个报告函数 | 未实现 | 本轮明确不做 |
| `DECISIONS/0011` 裁决一（commute） | 未了结 | 需要人来裁 |
| 复用地板时的自动拒绝 | 未实现 | 指纹已具备，比对逻辑待 P10/主实验接入时加 |
