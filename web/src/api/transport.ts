// 只模拟网络条件（延迟、断网、提交已送达但回执丢失），用来检查界面在断线与重复提交时的行为。它不改变任何业务结果。
import { ApiError } from "./types";

export interface TransportSettings {
  /** 每次请求的延迟（毫秒）。 */
  latencyMs: number;
  /** 模拟断网：所有请求失败。浏览器真的离线（navigator.onLine 为 false）时也一样。 */
  offline: boolean;
  /** 下一次提交“已送达但回执丢失”：适配器记下这次提交，再对界面报网络错误。用一次就自动关掉。 */
  dropNextReceipt: boolean;
}

let settings: TransportSettings = { latencyMs: 200, offline: false, dropNextReceipt: false };
const listeners = new Set<() => void>();

export function getTransport(): TransportSettings {
  return settings;
}

export function setTransport(patch: Partial<TransportSettings>): void {
  settings = { ...settings, ...patch };
  for (const l of listeners) l();
}

export function subscribeTransport(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function browserOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

export async function simulateRequest(): Promise<void> {
  if (settings.latencyMs > 0) await new Promise((r) => setTimeout(r, settings.latencyMs));
  if (settings.offline || browserOffline()) throw new ApiError("network", "网络断开：请求没有送达");
}
