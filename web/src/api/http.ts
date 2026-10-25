// 同源 HTTP 客户端（E3C：/api/v1，会话在 HttpOnly Cookie，CSRF 只放内存）。
//
// * 修改请求（POST / PUT / DELETE）带 X-CSRF-Token；只有用专属链接换会话的那一次不带（还没有 token）。
// * 发送前按共享结构校验请求体，收到后按共享结构校验回复（Ajv：不填默认值、不转类型、不删字段）；对不上就报 contract，不发送或不使用。
// * 2xx 都按各自的回复形状处理（200 / 201 / 202 不等于“模拟完成”，业务状态看回复内容）；204 没有回复体，不解析、不校验。
// * 非 2xx 按 ServiceError 校验；401 通知会话层（锁住私有视图、清缓存）。
// * 每个请求记下发出时的身份代次：回复到达时代次变了（换邀请、退出、删除全部、会话失效），丢弃，不写进缓存。
// * 不自动重试：查询的重试由查询层决定，提交、恢复、取消、删除从不自动重发。
import { checkMessage } from "../contract/validate";
import type { MessageName, ServiceError } from "../contract/types";
import { ApiError } from "./errors";

export const API_BASE = "/api/v1";

export type Method = "GET" | "POST" | "PUT" | "DELETE";

export interface HttpContext {
  csrf(): string | null;
  epoch(): number;
  onUnauthenticated(epochAtRequest: number): void;
}

export interface RequestSpec {
  method: Method;
  path: string;
  body?: unknown;
  /** 请求体的消息名（发送前校验）。 */
  bodyMessage?: MessageName;
  /** 2xx 回复的消息名；null = 只接受 204（没有回复体）。 */
  response: MessageName | null;
  /** 换会话那一次不带 CSRF。 */
  noCsrf?: boolean;
  signal?: AbortSignal;
}

export interface Reply<T> {
  status: number;
  data: T;
}

function contractError(name: string, direction: "request" | "response", errors: string[], status?: number): ApiError {
  return new ApiError("contract", { messageName: name, direction, details: errors, status: status ?? null });
}

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

export async function request<T>(ctx: HttpContext, spec: RequestSpec, fetchImpl: typeof fetch = fetch): Promise<Reply<T>> {
  const epoch = ctx.epoch();
  if (spec.bodyMessage) {
    const check = checkMessage(spec.bodyMessage, spec.body);
    if (!check.ok) throw contractError(spec.bodyMessage, "request", check.errors);
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (spec.body !== undefined) headers["Content-Type"] = "application/json";
  if (spec.method !== "GET" && !spec.noCsrf) {
    const token = ctx.csrf();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let res: Response;
  try {
    res = await fetchImpl(`${API_BASE}${spec.path}`, {
      method: spec.method,
      headers,
      body: spec.body === undefined ? undefined : JSON.stringify(spec.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: spec.signal,
    });
  } catch (e) {
    if (ctx.epoch() !== epoch) throw new ApiError("stale");
    throw new ApiError("network", { message: e instanceof Error ? e.message : "network" });
  }
  if (ctx.epoch() !== epoch) throw new ApiError("stale", { status: res.status });

  if (res.status >= 200 && res.status < 300) {
    if (res.status === 204 || spec.response === null) {
      if (spec.response !== null) throw contractError(spec.response, "response", ["收到 204，但这个接口应当有回复体"], 204);
      return { status: res.status, data: undefined as T };
    }
    const data = await readJson(res);
    if (ctx.epoch() !== epoch) throw new ApiError("stale", { status: res.status });
    const check = checkMessage(spec.response, data);
    if (!check.ok) throw contractError(spec.response, "response", check.errors, res.status);
    return { status: res.status, data: data as T };
  }

  const body = await readJson(res);
  if (ctx.epoch() !== epoch) throw new ApiError("stale", { status: res.status });
  if (body === undefined) {
    // 代理或网关给的非 JSON 错误页（服务没起来、502/503/504）：当成连不上，不当成业务错误
    throw new ApiError("network", { status: res.status, message: `HTTP ${res.status}` });
  }
  const check = checkMessage("ServiceError", body);
  if (!check.ok) throw contractError("ServiceError", "response", check.errors, res.status);
  const service = body as ServiceError;
  if (res.status === 401 || service.code === "unauthenticated") ctx.onUnauthenticated(epoch);
  throw new ApiError("service", { status: res.status, service });
}
