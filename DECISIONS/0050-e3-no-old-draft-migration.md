# 0050 去掉旧草稿迁移

日期：2026-10-26。层：接入层与托管服务。所有者 2026-10-26 决定（测试站上没有用户）。取代 [0046](0046-e3-input-catalog-and-structured-answers.md)
第 6 条里“旧草稿给迁移说明、用户同意后迁移”的部分，与 [0047](0047-e3-independent-ratings.md) 第 6 条里“迁移时选择重新评分或同等重视”的部分。

## 决定

1. 删掉旧草稿迁移：`POST /api/v1/drafts/{id}/migrate`、`DraftMigrateRequest`、`DraftView.migration`（`DraftMigration`、`LegacyAllocation`、
   `RemovedAnswer`）与错误 `migration_consent_required` / `migration_choice_required` / `migration_not_needed`。
2. 旧版本的草稿照原样读回，审阅照实阻断（`version_changed`；还有硬要求或属性偏好就 `removed_section_present`；旧的百分比分配就
   `allocation_mode_retired`），不能确认；用户新开一份草稿按当前问卷填写。
3. 不变：已经确认的旧画像、已经开始的运行与历史结果照原版本读回、照原绑定显示，停下的旧运行照常恢复（0046 第 9 条、0047 第 8 条）。
   这是读历史记录，不是维护旧问卷。
4. 版本：公共结构仍是 v3.5、服务 API 仍是 v1.4（这一轮还没有对外发布）；schema 从 36 份变成 35 份。

## 后果

* 后端和页面都少一套迁移流程；旧草稿里半填的答案不会带到新草稿里（测试站上没有这样的用户）。
