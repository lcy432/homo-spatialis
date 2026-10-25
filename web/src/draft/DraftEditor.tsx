// 一份服务器草稿的编辑器：载入服务器上的那一版，状态更新函数管还没保存的编辑与撤销，Autosaver 负责保存。
// 编辑内容只在页面内存里（不进浏览器存储）；离开页面前还有没保存的改动会提醒。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type Dispatch, type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useCapabilities, useKeys, queryRetry, queryRetryDelay } from "../api/queries";
import { useSession } from "../api/session";
import type { DraftView, ProductCapabilities } from "../contract/types";
import { Autosaver, type SaveStatus } from "./autosave";
import { type DraftAction, type DraftState, draftReducer, initialDraftState } from "./reducer";

export interface DraftEditorValue {
  caps: ProductCapabilities;
  draftId: string;
  /** 服务器最后存下的一版。 */
  base: DraftView;
  state: DraftState;
  dispatch: Dispatch<DraftAction>;
  save: SaveStatus;
  flush(): Promise<SaveStatus>;
  retrySave(): void;
  useServerVersion(): void;
  keepMyVersion(): void;
}

/** 测试里可以直接提供一个编辑器（不起服务）。 */
export const DraftEditorContext = createContext<DraftEditorValue | null>(null);

export function useDraftEditor(): DraftEditorValue {
  const ctx = useContext(DraftEditorContext);
  if (!ctx) throw new Error("useDraftEditor 要在 DraftEditorProvider 里面用");
  return ctx;
}

/** 读草稿与能力清单；都到了才挂上编辑器。 */
export function useDraftLoad(draftId: string) {
  const { api, status } = useSession();
  const keys = useKeys();
  const caps = useCapabilities();
  const draft = useQuery({
    queryKey: keys.draft(draftId),
    queryFn: () => api.getDraft(draftId),
    enabled: status === "active",
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
  return { caps, draft };
}

export function DraftEditorProvider({
  draftId,
  caps,
  initial,
  children,
}: {
  draftId: string;
  caps: ProductCapabilities;
  initial: DraftView;
  children: ReactNode;
}) {
  const { api } = useSession();
  const keys = useKeys();
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(draftReducer, initial.draft, initialDraftState);
  const [save, setSave] = useState<SaveStatus>({ kind: "saved", revision: initial.draft.revision });
  const [base, setBase] = useState<DraftView>(initial);
  const saverRef = useRef<Autosaver | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const initialRef = useRef(initial);

  useEffect(() => {
    let attached = true;
    const saver = new Autosaver(initialRef.current, {
      save: (draft, expected) => api.saveDraft(draftId, { draft, expected_revision: expected }),
      load: () => api.getDraft(draftId),
      onStatus: (s) => {
        if (attached) setSave(s);
      },
      onSaved: (v) => {
        queryClient.setQueryData(keys.draft(draftId), v);
        if (attached) setBase(v);
      },
    });
    saverRef.current = saver;
    saver.update(stateRef.current.draft);
    return () => {
      // 离开编辑器时还没保存的改动照样送出去；之后不再更新这个页面的状态
      attached = false;
      void saver.flush().finally(() => saver.dispose());
      if (saverRef.current === saver) saverRef.current = null;
    };
  }, [api, draftId, keys, queryClient]);

  useEffect(() => {
    saverRef.current?.update(state.draft);
  }, [state.draft]);

  useEffect(() => {
    const unsaved = save.kind !== "saved";
    if (!unsaved) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [save.kind]);

  const flush = useCallback(async () => (saverRef.current ? saverRef.current.flush() : save), [save]);
  const retrySave = useCallback(() => saverRef.current?.retryNow(), []);
  const useServerVersion = useCallback(() => {
    if (save.kind !== "conflict" || !saverRef.current) return;
    saverRef.current.useServer(save.server);
    setBase(save.server);
    queryClient.setQueryData(keys.draft(draftId), save.server);
    dispatch({ type: "draft/load", draft: save.server.draft });
  }, [save, queryClient, keys, draftId]);
  const keepMyVersion = useCallback(() => {
    if (save.kind !== "conflict" || !saverRef.current) return;
    saverRef.current.keepMine(save.server);
  }, [save]);

  const value = useMemo<DraftEditorValue>(
    () => ({ caps, draftId, base, state, dispatch, save, flush, retrySave, useServerVersion, keepMyVersion }),
    [caps, draftId, base, state, save, flush, retrySave, useServerVersion, keepMyVersion],
  );
  return <DraftEditorContext.Provider value={value}>{children}</DraftEditorContext.Provider>;
}
