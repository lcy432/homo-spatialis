// 一次“确认并开始”的本地记录：发请求**之前**把这次操作的幂等键与原始请求存下来，回复丢了（断线、关页、刷新）之后
// 重发同一个请求、同一个键，服务找回同一个意图（DECISIONS/0048）。不猜“列表里最新一项”，不换新键另起。
// * 只存找回所需的内容（确认画像编号、选项、同意身份、知悉项、键、时间）；不存邀请秘密、Cookie 或任何服务端密钥。
// * 按确认画像编号隔离（画像编号属于这个人且不可猜）；退出或删除全部内容时清掉全部记录。
// * 浏览器存储可能不可用：读写都包在 try 里，失败时这次操作仍然进行，只是没有找回记录（页面会说明）。
import type { RunIntentCreateRequest } from "../contract/types";

export interface PendingStart {
  confirmed_id: string;
  /** 这次开始是从哪份草稿来的（找回时能跳回去看）；没有就是空。 */
  draft_id: string | null;
  request: RunIntentCreateRequest;
  created_at: string;
}

const PREFIX = "hs.start.pending.";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function savePendingStart(rec: PendingStart): boolean {
  const s = storage();
  if (!s) return false;
  try {
    s.setItem(PREFIX + rec.confirmed_id, JSON.stringify(rec));
    return true;
  } catch {
    return false;
  }
}

export function loadPendingStart(confirmedId: string): PendingStart | null {
  const s = storage();
  if (!s) return null;
  try {
    const raw = s.getItem(PREFIX + confirmedId);
    if (!raw) return null;
    const rec = JSON.parse(raw) as PendingStart;
    return rec && rec.request && rec.request.start ? rec : null;
  } catch {
    return null;
  }
}

export function clearPendingStart(confirmedId: string): void {
  try {
    storage()?.removeItem(PREFIX + confirmedId);
  } catch {
    // 清不掉就留着：下次打开仍会提示找回
  }
}

export function allPendingStarts(): PendingStart[] {
  const s = storage();
  if (!s) return [];
  const out: PendingStart[] = [];
  try {
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (!k?.startsWith(PREFIX)) continue;
      const raw = s.getItem(k);
      if (!raw) continue;
      try {
        const rec = JSON.parse(raw) as PendingStart;
        if (rec?.request?.start) out.push(rec);
      } catch {
        // 坏记录跳过
      }
    }
  } catch {
    return [];
  }
  return out.sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** 换身份、退出、删除全部内容：别人的或已作废的开始记录不能留在这台设备上。 */
export function clearAllPendingStarts(): void {
  const s = storage();
  if (!s) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k?.startsWith(PREFIX)) keys.push(k);
    }
    for (const k of keys) s.removeItem(k);
  } catch {
    // 忽略
  }
}
