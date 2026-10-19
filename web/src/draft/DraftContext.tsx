import { useQuery } from "@tanstack/react-query";
import { type Dispatch, type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useReducer, useState } from "react";
import { useApi } from "../api/context";
import { samplePayload } from "../contract/samples";
import type { ProductCapabilities, UserInputDraft } from "../contract/types";
import { emptyDraft, newDraftId } from "./draft";
import { type DraftAction, type DraftState, draftReducer, initialDraftState } from "./reducer";
import { type StoredVersions, safeRead, safeRemove, safeWrite, sameVersions, versionsOf } from "./storage";

const DRAFT_KEY = "e5a.draft.v1";

interface StoredDraft {
  versions: StoredVersions;
  state: Omit<DraftState, "past" | "future" | "coalesceKey">;
}

interface DraftContextValue {
  caps: ProductCapabilities;
  state: DraftState;
  dispatch: Dispatch<DraftAction>;
  /** 存着的草稿是按另一版问卷、词表或资料包填的。 */
  versionMismatch: boolean;
  clearDraft: () => void;
  loadSampleDraft: (sampleId: string) => void;
}

const DraftContext = createContext<DraftContextValue | null>(null);

function freshState(caps: ProductCapabilities): DraftState {
  const id = newDraftId();
  return initialDraftState(emptyDraft(caps, id), id);
}

function restore(caps: ProductCapabilities): { state: DraftState; mismatch: boolean } {
  const stored = safeRead<StoredDraft>(DRAFT_KEY);
  if (!stored?.state?.draft) return { state: freshState(caps), mismatch: false };
  const mismatch = !sameVersions(stored.versions, versionsOf(caps.meta));
  return { state: { ...stored.state, past: [], future: [], coalesceKey: null }, mismatch };
}

function DraftStore({ caps, children }: { caps: ProductCapabilities; children: ReactNode }) {
  const [boot] = useState(() => restore(caps));
  const [state, dispatch] = useReducer(draftReducer, boot.state);
  const [versionMismatch, setVersionMismatch] = useState(boot.mismatch);

  useEffect(() => {
    const { past: _p, future: _f, coalesceKey: _c, ...rest } = state;
    void _p;
    void _f;
    void _c;
    safeWrite(DRAFT_KEY, { versions: versionsOf(caps.meta), state: rest } satisfies StoredDraft);
  }, [state, caps]);

  const clearDraft = useCallback(() => {
    safeRemove(DRAFT_KEY);
    const fresh = freshState(caps);
    dispatch({ type: "draft/load", draft: fresh.draft, sampleId: null, ageText: "", ownDraftId: fresh.ownDraftId });
    setVersionMismatch(false);
  }, [caps]);

  const loadSampleDraft = useCallback((sampleId: string) => {
    const draft = samplePayload<UserInputDraft>(sampleId);
    // 真实消息不写样例出处标签；载入后与样例内容逐项相同
    delete (draft.meta as { sample?: unknown }).sample;
    dispatch({ type: "draft/load", draft, sampleId });
    setVersionMismatch(false);
  }, []);

  const value = useMemo(
    () => ({ caps, state, dispatch, versionMismatch, clearDraft, loadSampleDraft }),
    [caps, state, versionMismatch, clearDraft, loadSampleDraft],
  );
  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export function DraftProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: () => api.capabilities(), staleTime: Infinity });
  if (caps.isPending) {
    return (
      <p className="page-status" role="status">
        正在读取能力清单……
      </p>
    );
  }
  if (caps.isError) {
    return (
      <div className="page-status" role="alert">
        <p>读不到能力清单：{caps.error.message}</p>
        <button type="button" onClick={() => caps.refetch()}>
          再试一次
        </button>
      </div>
    );
  }
  return <DraftStore caps={caps.data}>{children}</DraftStore>;
}

export function useDraft(): DraftContextValue {
  const v = useContext(DraftContext);
  if (!v) throw new Error("DraftProvider 没有包住这个组件");
  return v;
}
