// 服务快照的查询（TanStack Query）。键都带身份代次：换身份之后旧内容不会再被读到。
//
// 轮询：运行意图在 checking / queued / running / deleting 时每 3 秒只读查询一次，其他阶段停止；页面在后台时不轮询。
// 查询失败：断线按退避重试几次，429 按服务给的等待秒数；其它错误不重试，交给页面显示。修改类请求（提交、恢复、取消、删除）
// 从不自动重试——见各页面。
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import type { RunIntentView } from "../contract/types";
import { isApiError } from "./errors";
import { useSession } from "./session";

export const POLL_STAGES: ReadonlySet<RunIntentView["stage"]> = new Set(["checking", "queued", "running", "deleting"]);
export const POLL_MS = 3000;

export function pollIntervalFor(stage: RunIntentView["stage"] | undefined): number | false {
  return stage !== undefined && POLL_STAGES.has(stage) ? POLL_MS : false;
}

export function queryRetry(failureCount: number, error: unknown): boolean {
  if (!isApiError(error)) return false;
  if (error.kind === "network") return failureCount < 3;
  if (error.kind === "service" && error.code === "rate_limited") return failureCount < 3;
  return false;
}

export function queryRetryDelay(attempt: number, error: unknown): number {
  if (isApiError(error) && error.retryAfter) return error.retryAfter * 1000;
  return Math.min(30_000, 2000 * 2 ** attempt);
}

export function keysFor(epoch: number) {
  return {
    capabilities: ["s", epoch, "capabilities"] as const,
    draft: (id: string) => ["s", epoch, "draft", id] as const,
    review: (id: string, revision: number) => ["s", epoch, "review", id, revision] as const,
    intent: (id: string) => ["s", epoch, "intent", id] as const,
    runs: ["s", epoch, "runs"] as const,
    result: (runId: string, version: string) => ["s", epoch, "result", runId, version] as const,
  };
}

export type QueryKeys = ReturnType<typeof keysFor>;

/** 查询键（按身份代次缓存同一个对象，可以放进副作用依赖）。 */
export function useKeys(): QueryKeys {
  const { epoch } = useSession();
  return useMemo(() => keysFor(epoch), [epoch]);
}

export function useCapabilities() {
  const { api, status } = useSession();
  const keys = useKeys();
  return useQuery({
    queryKey: keys.capabilities,
    queryFn: () => api.capabilities(),
    enabled: status === "active",
    staleTime: 5 * 60_000,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
}

export function useIntent(id: string | null | undefined) {
  const { api, status } = useSession();
  const keys = useKeys();
  return useQuery({
    queryKey: keys.intent(id ?? ""),
    queryFn: () => api.intent(id as string),
    enabled: status === "active" && !!id,
    refetchInterval: (q) => pollIntervalFor(q.state.data?.stage),
    refetchIntervalInBackground: false,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
}

export function useRuns() {
  const { api, status } = useSession();
  const keys = useKeys();
  return useQuery({
    queryKey: keys.runs,
    queryFn: () => api.runs(),
    enabled: status === "active",
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
}

/**
 * 结果：键里带运行意图的 updated_at——意图有任何变化（恢复之后有了新结果、删除、过期），结果跟着重新读，不会一直显示旧内容。
 */
export function useResult(runId: string | null | undefined, version: string, enabled: boolean) {
  const { api, status } = useSession();
  const keys = useKeys();
  return useQuery({
    queryKey: keys.result(runId ?? "", version),
    queryFn: () => api.result(runId as string),
    enabled: status === "active" && !!runId && enabled,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
}
