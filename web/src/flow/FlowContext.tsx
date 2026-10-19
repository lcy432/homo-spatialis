// 确认与运行前说明的流程状态：确认画像、预检查、勾选的知悉项、每份预检查的幂等键、提交过的运行。只存在当前标签页。
//
// 确认画像与预检查记下它们对应的草稿版本：草稿在那之后改过，它们就失效（界面立刻标出，运行按钮变灰），要重新确认。
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useApiContext } from "../api/context";
import type { ConfirmedProfile, ProductPreflight } from "../contract/types";
import { newIdempotencyKey } from "../draft/draft";
import { safeRead, safeWrite } from "../draft/storage";

const FLOW_KEY = "e5a.flow.v1";

export interface BoundToDraft {
  draftId: string;
  revision: number;
}

export interface FlowState {
  scenarioId: string;
  confirmed: { profile: ConfirmedProfile; boundTo: BoundToDraft } | null;
  preflight: { preflight: ProductPreflight; boundTo: BoundToDraft } | null;
  acknowledged: string[];
  /** 预检查指纹 → 幂等键。提交之前先生成并存好，重复点击、断网重试、回执丢失后重试都用同一个键。 */
  idempotencyKeys: Record<string, string>;
  runs: { runId: string; preflightFingerprint: string }[];
}

const emptyFlow = (scenarioId: string): FlowState => ({
  scenarioId,
  confirmed: null,
  preflight: null,
  acknowledged: [],
  idempotencyKeys: {},
  runs: [],
});

interface FlowContextValue {
  flow: FlowState;
  setAcknowledged: (key: string, checked: boolean) => void;
  setConfirmed: (profile: ConfirmedProfile, boundTo: BoundToDraft) => void;
  setPreflight: (preflight: ProductPreflight, boundTo: BoundToDraft) => void;
  /** 取这份预检查的幂等键；没有就先生成并立即存进本标签页（在发送之前）。 */
  idempotencyKeyFor: (preflightFingerprint: string) => string;
  recordRun: (runId: string, preflightFingerprint: string) => void;
  resetFlow: () => void;
}

const FlowContext = createContext<FlowContextValue | null>(null);

export function FlowProvider({ children }: { children: ReactNode }) {
  const { scenario } = useApiContext();
  const [flow, setFlow] = useState<FlowState>(() => {
    const stored = safeRead<FlowState>(FLOW_KEY);
    return stored && stored.scenarioId === scenario.id ? stored : emptyFlow(scenario.id);
  });
  // 同步保存最新状态：幂等键要在发送之前就存好，不能等下一次渲染
  const ref = useRef(flow);

  const update = useCallback((fn: (f: FlowState) => FlowState) => {
    const next = fn(ref.current);
    ref.current = next;
    safeWrite(FLOW_KEY, next);
    setFlow(next);
  }, []);

  // 换了样例场景：之前的确认、预检查与提交都不再适用
  useEffect(() => {
    if (ref.current.scenarioId !== scenario.id) update(() => emptyFlow(scenario.id));
  }, [scenario.id, update]);

  const value = useMemo<FlowContextValue>(
    () => ({
      flow,
      setAcknowledged: (key, checked) =>
        update((f) => ({
          ...f,
          acknowledged: checked ? [...new Set([...f.acknowledged, key])] : f.acknowledged.filter((k) => k !== key),
        })),
      setConfirmed: (profile, boundTo) => update((f) => ({ ...f, confirmed: { profile, boundTo }, preflight: null })),
      setPreflight: (preflight, boundTo) => update((f) => ({ ...f, preflight: { preflight, boundTo } })),
      idempotencyKeyFor: (fp) => {
        const existing = ref.current.idempotencyKeys[fp];
        if (existing) return existing;
        const key = newIdempotencyKey();
        update((f) => ({ ...f, idempotencyKeys: { ...f.idempotencyKeys, [fp]: key } }));
        return key;
      },
      recordRun: (runId, fp) =>
        update((f) =>
          f.runs.some((r) => r.runId === runId) ? f : { ...f, runs: [...f.runs, { runId, preflightFingerprint: fp }] },
        ),
      resetFlow: () => update(() => emptyFlow(ref.current.scenarioId)),
    }),
    [flow, update],
  );
  return <FlowContext.Provider value={value}>{children}</FlowContext.Provider>;
}

export function useFlow(): FlowContextValue {
  const v = useContext(FlowContext);
  if (!v) throw new Error("FlowProvider 没有包住这个组件");
  return v;
}

/** 确认或预检查还对得上当前草稿吗？ */
export function stillBound(bound: BoundToDraft | undefined, draftId: string, revision: number): boolean {
  return !!bound && bound.draftId === draftId && bound.revision === revision;
}
