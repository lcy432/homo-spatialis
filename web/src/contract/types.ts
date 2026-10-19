// 页面用到的共享消息类型。全部来自 generated/contracts.ts（由 E3 的 schema 生成），这里只是集中导出，不另写字段。
export type {
  AbilityClaim,
  AllocationAnswer,
  AllocationLine,
  AllocationReview,
  AttributePreferenceAnswer,
  CapabilityStatus,
  CityCoverageSummary,
  CityOption,
  CityPreflight,
  CityResult,
  CloneProgress,
  CloneResult,
  ComparisonLine,
  ConfirmedProfile,
  ConfirmedRef,
  CostStatus,
  DimensionDefinition,
  ExactNumber,
  HardRequirementAnswer,
  HardRequirementOption,
  HealthItemAnswer,
  HealthReviewItem,
  HouseholdHours,
  ItemStatus,
  MaterialLeafDefinition,
  OccupationCandidate,
  OptionCoverageLine,
  PlannerAndCost,
  PointEntry,
  PreflightRef,
  ProductCapabilities,
  ProductIssue,
  ProductMeta,
  ProductMoney,
  ProductPreflight,
  ProductResult,
  ProfileReview,
  PublicExplanation,
  RequirementLine,
  RequirementSummaryLine,
  RunProgress,
  RunReceipt,
  RunSubmission,
  StatementReview,
  UserInputDraft,
  YearFacts,
} from "./generated/contracts";

/** 共享结构里的 13 份消息名（与 contracts/product/v2/index.json 的 messages 相同）。 */
export type MessageName =
  | "CatalogCompilation"
  | "ConfirmedProfile"
  | "OpportunityCandidate"
  | "OptionInventoryCoverage"
  | "ProductCapabilities"
  | "ProductPreflight"
  | "ProductResult"
  | "ProfileReview"
  | "RunProgress"
  | "RunReceipt"
  | "RunSubmission"
  | "SourceFact"
  | "UserInputDraft";

/** 样例 22 是 ProductResult 里的一个 YearFacts 片段，不是一份完整消息。 */
export type ValidatedName = MessageName | "YearFacts";

export type AnswerState = "unanswered" | "provided" | "indifferent" | "unsure";
export type AllocationGroup = "six_dimensions" | "material";
