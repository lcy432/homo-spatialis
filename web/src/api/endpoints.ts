// E3C 的业务接口（路径与回复形状以 contracts/product/v3/openapi.json 为准）。每个函数只发一次请求，不重试、不改写内容。
import type {
  ConfirmedProfileView,
  DeletionReceipt,
  DisplayEnvelope,
  GeographyDisplayBundle,
  DraftSaveRequest,
  DraftView,
  ProductCapabilities,
  ProductResult,
  ProfileConfirmReply,
  ProfileConfirmRequest,
  ProfileList,
  ReviewReply,
  RunIntentCreateRequest,
  RunIntentView,
  RunList,
  RunStartPreview,
  RunStartPreviewRequest,
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

    /** 本人还在有效期内的确认画像（摘要：首次确认时间、到期时间）；单份带原 confirmed（别人的与不存在的一样 404，过期 410）。 */
    profiles: () => data(call<ProfileList>({ method: "GET", path: "/profiles", response: "ProfileList" })),
    profile: (confirmedId: string) =>
      data(call<ConfirmedProfileView>({ method: "GET", path: `/profiles/${seg(confirmedId)}`, response: "ConfirmedProfileView" })),

    /**
     * 显示附层（e3-display-v1）：同一次返回业务对象 source 与各文字位置的译文；只读、不续期。
     * 业务状态只从 source 读，fields 只用于文字；缺译文显示中文原文并标明，不自己翻译。
     */
    display: (kind: DisplayEnvelope["kind"], id: string | null, locale: "zh" | "en") =>
      data(
        call<DisplayEnvelope>({
          method: "GET",
          path: `/display?kind=${seg(kind)}${id ? `&id=${seg(id)}` : ""}&locale=${locale}`,
          response: "DisplayEnvelope",
        }),
      ),
    /** 地理显示清单：按资料包指纹；明确指定的资源版本不存在就是 404，不回退到最新版本。 */
    geography: (packFingerprint: string, resourceVersion?: string | null) =>
      data(
        call<GeographyDisplayBundle>({
          method: "GET",
          path: `/display/geography?pack_fingerprint=${seg(packFingerprint)}${resourceVersion ? `&resource_version=${seg(resourceVersion)}` : ""}`,
          response: "GeographyDisplayBundle",
        }),
      ),

    /** 设置预览（只读：不排队、不占次数、不调用模型、不写库）：有效设置、范围说明、知悉项与同意身份。 */
    startPreview: (req: RunStartPreviewRequest) =>
      data(
        call<RunStartPreview>({
          method: "POST",
          path: "/run-start-preview",
          body: req,
          bodyMessage: "RunStartPreviewRequest",
          response: "RunStartPreview",
        }),
      ),
    /** 准备模拟 / 一次“确认并开始”（带 start）：202 新建，200 找回同一个意图（同一个键或同样设置的原意图）。 */
    createIntent: (req: RunIntentCreateRequest) =>
      call<RunIntentView>({
        method: "POST",
        path: "/run-intents",
        body: req,
        bodyMessage: "RunIntentCreateRequest",
        response: "RunIntentView",
      }),
    intent: (id: string) => data(call<RunIntentView>({ method: "GET", path: `/run-intents/${seg(id)}`, response: "RunIntentView" })),
    cancel: (id: string) =>
      data(call<RunIntentView>({ method: "POST", path: `/run-intents/${seg(id)}/cancel`, response: "RunIntentView" })),

    runs: () => data(call<RunList>({ method: "GET", path: "/runs", response: "RunList" })),
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
