// 页面只依赖这个业务访问接口。E5-A 由 SampleAdapter 实现（原样返回 E3 的公共样例）；E5-B 换成 HttpAdapter（接 E3-C 的服务入口）。
// 无论后端怎样编排（含 LangGraph），页面都继续消费同一套共享消息。
import type {
  ConfirmedProfile,
  ConfirmedRef,
  ProductCapabilities,
  ProductIssue,
  ProductPreflight,
  ProductResult,
  ProfileReview,
  RunProgress,
  RunReceipt,
  RunSubmission,
  UserInputDraft,
} from "../contract/types";

/**
 * 确认请求：草稿 + 用户勾选的知悉项。共享结构里还没有这份消息（需求 B1，docs/handoffs/e5-to-e3-interface-requests.md），
 * 这里先按 E3 现有函数 product.answers.confirm_profile 的参数写；E3 定下消息之后改成它。
 */
export interface ConfirmRequest {
  draft: UserInputDraft;
  acknowledged: string[];
}

/** 确认的结果：确认画像，或拒绝与原因（拒绝的回复格式同样等 E3 定，需求 B1）。 */
export type ConfirmOutcome =
  | { status: "confirmed"; profile: ConfirmedProfile }
  | { status: "refused"; issues: ProductIssue[] };

export interface ProductApi {
  capabilities(): Promise<ProductCapabilities>;
  review(draft: UserInputDraft): Promise<ProfileReview>;
  confirm(request: ConfirmRequest): Promise<ConfirmOutcome>;
  preflight(confirmed: ConfirmedRef): Promise<ProductPreflight>;
  submit(submission: RunSubmission): Promise<RunReceipt>;
  progress(runId: string): Promise<RunProgress>;
  result(runId: string): Promise<ProductResult>;
}

export type ApiErrorKind = "network" | "not_in_samples" | "contract" | "not_implemented";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  constructor(kind: ApiErrorKind, message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
  }
}

export const isNetworkError = (e: unknown): boolean => e instanceof ApiError && e.kind === "network";
