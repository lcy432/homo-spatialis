// 接口错误分四类，页面按类显示，不透出原始异常：
// * service：服务返回的 ServiceError（code、三句中文、支持编号、等待秒数）；
// * network：请求没有送达或没有收到回复（断网、超时、服务停了）——不知道服务端有没有处理，修改类请求先查询再决定；
// * contract：发出去或收回来的内容不符合共享结构（不发送 / 不使用，不“修成能用”）；
// * stale：回复到达时身份已经换了（换邀请、退出、删除全部、会话失效）——丢弃，不写进缓存。
import type { ServiceError } from "../contract/types";

export type ApiErrorKind = "service" | "network" | "contract" | "stale";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly service: ServiceError | null;
  /** contract：哪份消息、哪个方向、校验错误。 */
  readonly messageName: string | null;
  readonly direction: "request" | "response" | null;
  readonly details: string[];

  constructor(
    kind: ApiErrorKind,
    opts: {
      status?: number | null;
      service?: ServiceError | null;
      messageName?: string | null;
      direction?: "request" | "response" | null;
      details?: string[];
      message?: string;
    } = {},
  ) {
    super(opts.message ?? opts.service?.code ?? kind);
    this.name = "ApiError";
    this.kind = kind;
    this.status = opts.status ?? null;
    this.service = opts.service ?? null;
    this.messageName = opts.messageName ?? null;
    this.direction = opts.direction ?? null;
    this.details = opts.details ?? [];
  }

  get code(): ServiceError["code"] | null {
    return this.service?.code ?? null;
  }

  /** 服务给的等待秒数（429 等）。 */
  get retryAfter(): number | null {
    return this.service?.retry_after_seconds ?? null;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export function hasCode(e: unknown, ...codes: ServiceError["code"][]): boolean {
  return isApiError(e) && e.code !== null && codes.includes(e.code);
}
