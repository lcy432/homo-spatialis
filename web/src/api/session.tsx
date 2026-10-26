// 会话与身份（E3C）。
//
// * 专属链接 /invite#token=… 的秘密在页面加载的最早时刻读出、立刻从地址栏与历史里抹掉，只留在内存里，交给换会话的那一次请求；
//   不进查询串、浏览器存储、日志或截图。
// * 会话在 HttpOnly Cookie 里；CSRF token 只放内存，刷新后用 GET /session 取回。
// * 身份代次（epoch）：换邀请、退出、401、删除全部都先把代次加一、取消在途查询、清空缓存，再加载新内容。查询键都带代次，
//   旧代次的迟到回复被 HTTP 层丢弃，不会闪现另一个人的内容。
import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { SessionView } from "../contract/types";
import { clearAllPendingStarts } from "../draft/startRecord";
import { type ServiceApi, makeApi } from "./endpoints";
import { ApiError, isApiError } from "./errors";

// sign_out_unconfirmed：点了退出，但服务没有确认（请求没送到，或回执丢了）。私有页面先藏起来，不承诺会话已失效，
// 给“再试一次退出”与“重新检查”（独立核查 F1）。
export type SessionStatus = "loading" | "none" | "active" | "expired" | "signed_out" | "sign_out_unconfirmed" | "unreachable";

const pendingInvite: { token: string | null } = { token: null };

/** 在 React 渲染之前调用：/invite#token=… 的秘密读进内存，并立刻从地址栏与历史里移除。 */
export function captureInviteFromLocation(loc: Location = window.location, hist: History = window.history): void {
  if (loc.pathname !== "/invite") return;
  const token = new URLSearchParams(loc.hash.slice(1)).get("token");
  hist.replaceState(null, "", "/invite");
  pendingInvite.token = token && token.trim() ? token : null;
}

/** 取走内存里的秘密（只能取一次）。 */
export function takePendingInvite(): string | null {
  const t = pendingInvite.token;
  pendingInvite.token = null;
  return t;
}

export function hasPendingInvite(): boolean {
  return pendingInvite.token !== null;
}

interface SessionContextValue {
  status: SessionStatus;
  epoch: number;
  api: ServiceApi;
  view: SessionView | null;
  /** 最近一次换会话失败的错误（显示用）。 */
  exchangeError: ApiError | null;
  /** 最近一次退出没有得到服务确认的错误（显示用）。 */
  signOutError: ApiError | null;
  exchange(token: string): Promise<boolean>;
  refresh(): Promise<void>;
  signOut(): Promise<void>;
  /** 删除全部内容之后：会话还在，但旧内容全部作废——换代次、清缓存，再重新读会话。 */
  resetAfterDeleteAll(): Promise<void>;
  retryBoot(): void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children, api: injected }: { children: ReactNode; api?: ServiceApi }) {
  const queryClient = useQueryClient();
  const csrf = useRef<string | null>(null);
  const epochRef = useRef(0);
  const statusRef = useRef<SessionStatus>("loading");
  const [status, setStatusState] = useState<SessionStatus>("loading");
  const [epoch, setEpoch] = useState(0);
  const [view, setView] = useState<SessionView | null>(null);
  const [exchangeError, setExchangeError] = useState<ApiError | null>(null);
  const [signOutError, setSignOutError] = useState<ApiError | null>(null);
  const signingOut = useRef(false);
  const [bootTick, setBootTick] = useState(0);

  const setStatus = useCallback((s: SessionStatus) => {
    statusRef.current = s;
    setStatusState(s);
  }, []);

  /** 作废当前身份：代次加一、取消在途查询、清空全部缓存与 CSRF。 */
  const dropIdentity = useCallback(
    (next: SessionStatus) => {
      epochRef.current += 1;
      setEpoch(epochRef.current);
      csrf.current = null;
      setView(null);
      void queryClient.cancelQueries();
      queryClient.clear();
      // 这台设备上的“确认并开始”找回记录属于上一个身份：一起清掉
      clearAllPendingStarts();
      setStatus(next);
    },
    [queryClient, setStatus],
  );

  const onUnauthenticated = useCallback(
    (epochAtRequest: number) => {
      if (epochAtRequest !== epochRef.current) return;
      // 退出过程中的 401 = 服务那边已经没有这个会话了：就是退出成功（上一次的回执可能丢了）
      if (signingOut.current) dropIdentity("signed_out");
      else if (statusRef.current === "active" || statusRef.current === "sign_out_unconfirmed") dropIdentity("expired");
    },
    [dropIdentity],
  );

  const api = useMemo(
    () =>
      injected ??
      makeApi({
        csrf: () => csrf.current,
        epoch: () => epochRef.current,
        onUnauthenticated: (e) => onUnauthenticated(e),
      }),
    [injected, onUnauthenticated],
  );

  const adopt = useCallback(
    (v: SessionView) => {
      csrf.current = v.csrf_token;
      setView(v);
      setStatus("active");
    },
    [setStatus],
  );

  // 进页面先问一次会话：有就接着用，401 就是没有会话（不是“失效”）。/invite 页由换会话流程负责。
  useEffect(() => {
    if (statusRef.current !== "loading") return;
    if (hasPendingInvite() || window.location.pathname === "/invite") return;
    let alive = true;
    const at = epochRef.current;
    api.session().then(
      (v) => {
        if (alive && at === epochRef.current) adopt(v);
      },
      (e: unknown) => {
        if (!alive || at !== epochRef.current) return;
        if (isApiError(e) && e.kind === "service" && e.status === 401) setStatus("none");
        else if (isApiError(e) && e.kind === "stale") return;
        else setStatus("unreachable");
      },
    );
    return () => {
      alive = false;
    };
  }, [api, adopt, setStatus, bootTick]);

  const exchange = useCallback(
    async (token: string) => {
      setExchangeError(null);
      dropIdentity("loading");
      try {
        adopt(await api.exchange(token));
        return true;
      } catch (e) {
        if (isApiError(e) && e.kind === "stale") return false;
        setExchangeError(isApiError(e) ? e : new ApiError("network"));
        setStatus("none");
        return false;
      }
    },
    [api, adopt, dropIdentity, setStatus],
  );

  const refresh = useCallback(async () => {
    const at = epochRef.current;
    try {
      const v = await api.session();
      if (at === epochRef.current) adopt(v);
    } catch {
      // 401 已经由 HTTP 层通知；其它错误留给调用方的页面显示
    }
  }, [api, adopt]);

  const signOut = useCallback(async () => {
    const at = epochRef.current;
    signingOut.current = true;
    setSignOutError(null);
    try {
      await api.signOut();
      if (at === epochRef.current) dropIdentity("signed_out");
    } catch (e) {
      if (at !== epochRef.current) return; // 401 已经由 HTTP 层处理成“已退出”
      if (isApiError(e) && e.kind === "stale") return;
      // 服务没有确认：请求可能没送到，也可能处理了但回执丢了。先藏起私有页面，但不说“已经退出”。
      setSignOutError(isApiError(e) ? e : new ApiError("network"));
      void queryClient.cancelQueries();
      setStatus("sign_out_unconfirmed");
    } finally {
      signingOut.current = false;
    }
  }, [api, dropIdentity, queryClient, setStatus]);

  const resetAfterDeleteAll = useCallback(async () => {
    // 会话本身还在：换代次、清缓存，但不切到“加载中”（页面上的删除回执要留着）
    dropIdentity("active");
    try {
      adopt(await api.session());
    } catch (e) {
      if (isApiError(e) && e.status === 401) setStatus("none");
      else setStatus("unreachable");
    }
  }, [api, adopt, dropIdentity, setStatus]);

  const retryBoot = useCallback(() => {
    setStatus("loading");
    setBootTick((n) => n + 1);
  }, [setStatus]);

  const value = useMemo<SessionContextValue>(
    () => ({ status, epoch, api, view, exchangeError, signOutError, exchange, refresh, signOut, resetAfterDeleteAll, retryBoot }),
    [status, epoch, api, view, exchangeError, signOutError, exchange, refresh, signOut, resetAfterDeleteAll, retryBoot],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession 要在 SessionProvider 里面用");
  return ctx;
}
