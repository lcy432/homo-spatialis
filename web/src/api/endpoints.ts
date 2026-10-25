// E3C 的业务接口（路径与回复形状以 contracts/product/v3/openapi.json 为准）。每个函数只发一次请求，不重试、不改写内容。
import type {
  DeletionReceipt,
  DraftSaveRequest,
  DraftView,
  ProductCapabilities,
  ProductResult,
  ProfileConfirmReply,
  ProfileConfirmRequest,
  ReviewReply,
  RunIntentCreateRequest,
  RunIntentView,
  RunList,
  RunProgress,
  RunSubmitReply,
  RunSubmitRequest,
  SessionView,
} from "../contract/types";
import { type HttpContext, type Reply, request } from "./http";

const seg = (s: string) => encodeURIComponent(s);

export function makeApi(ctx: HttpContext, fetchImpl: typeof fetch = (...a) => fetch(...a)) {
  const call = <T>(spec: Parameters<typeof request>[1]) => request<T>(ctx, spec, fetchImpl);
  const data = async <T>(p: Promise<Reply<T>>) => (await p).data;
  return {
    /** 用专属链接的秘密换会话（同源 JSON，不带 CSRF）。秘密只在这一次请求体里出现。 */
    exchange: (inviteToken: string) =>
      data(
        call<SessionView>({
          method: "POST",
          path: "/session",
          body: { invite_token: inviteToken },
          bodyMessage: "SessionExchangeRequest",
          response: "SessionView",
          noCsrf: true,
        }),
      ),
    session: () => data(call<SessionView>({ method: "GET", path: "/session", response: "SessionView" })),
    signOut: () => data(call<void>({ method: "DELETE", path: "/session", response: null })),
    capabilities: () => data(call<ProductCapabilities>({ method: "GET", path: "/capabilities", response: "ProductCapabilities" })),

    createDraft: () => data(call<DraftView>({ method: "POST", path: "/drafts", response: "DraftView" })),
    getDraft: (id: string) => data(call<DraftView>({ method: "GET", path: `/drafts/${seg(id)}`, response: "DraftView" })),
    saveDraft: (id: string, req: DraftSaveRequest) =>
      data(
        call<DraftView>({ method: "PUT", path: `/drafts/${seg(id)}`, body: req, bodyMessage: "DraftSaveRequest", response: "DraftView" }),
      ),
    deleteDraft: (id: string) =>
      data(call<DeletionReceipt>({ method: "DELETE", path: `/drafts/${seg(id)}`, response: "DeletionReceipt" })),
    review: (id: string) =>
      data(call<ReviewReply>({ method: "POST", path: `/drafts/${seg(id)}/review`, response: "ReviewReply" })),
    confirm: (req: ProfileConfirmRequest) =>
      data(
        call<ProfileConfirmReply>({
          method: "POST",
          path: "/profiles/confirm",
          body: req,
          bodyMessage: "ProfileConfirmRequest",
          response: "ProfileConfirmReply",
        }),
      ),

    /** 准备模拟：202 新建（检查排队），200 同一份画像与同样设置的原意图。 */
    createIntent: (req: RunIntentCreateRequest) =>
      call<RunIntentView>({
        method: "POST",
        path: "/run-intents",
        body: req,
        bodyMessage: "RunIntentCreateRequest",
        response: "RunIntentView",
      }),
    intent: (id: string) => data(call<RunIntentView>({ method: "GET", path: `/run-intents/${seg(id)}`, response: "RunIntentView" })),
    /** 确认开始：202 接受，200 重复提交（同一个运行）。 */
    submit: (id: string, req: RunSubmitRequest) =>
      call<RunSubmitReply>({
        method: "POST",
        path: `/run-intents/${seg(id)}/submit`,
        body: req,
        bodyMessage: "RunSubmitRequest",
        response: "RunSubmitReply",
      }),
    cancel: (id: string) =>
      data(call<RunIntentView>({ method: "POST", path: `/run-intents/${seg(id)}/cancel`, response: "RunIntentView" })),

    runs: () => data(call<RunList>({ method: "GET", path: "/runs", response: "RunList" })),
    progress: (runId: string) =>
      data(call<RunProgress>({ method: "GET", path: `/runs/${seg(runId)}/progress`, response: "RunProgress" })),
    result: (runId: string) =>
      data(call<ProductResult>({ method: "GET", path: `/runs/${seg(runId)}/result`, response: "ProductResult" })),
    resume: (runId: string) =>
      data(call<RunIntentView>({ method: "POST", path: `/runs/${seg(runId)}/resume`, response: "RunIntentView" })),
    deleteRun: (runId: string) =>
      data(call<DeletionReceipt>({ method: "DELETE", path: `/runs/${seg(runId)}`, response: "DeletionReceipt" })),
    deleteAll: () => data(call<DeletionReceipt>({ method: "DELETE", path: "/me/data", response: "DeletionReceipt" })),
  };
}

export type ServiceApi = ReturnType<typeof makeApi>;
