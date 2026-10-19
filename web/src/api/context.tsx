import { type ReactNode, createContext, useCallback, useContext, useMemo, useState } from "react";
import { SampleAdapter } from "./sampleAdapter";
import { DEFAULT_SCENARIO, type Scenario, scenarioById } from "./scenarios";
import { getTransport, setTransport } from "./transport";
import type { ProductApi } from "./types";

const SCENARIO_KEY = "e5a.scenario";

function readStoredScenario(): string {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("scenario");
    if (fromUrl) return fromUrl;
    return sessionStorage.getItem(SCENARIO_KEY) ?? DEFAULT_SCENARIO;
  } catch {
    return DEFAULT_SCENARIO;
  }
}

interface ApiContextValue {
  api: ProductApi;
  adapter: SampleAdapter;
  scenario: Scenario;
  setScenario: (id: string) => void;
}

const ApiContext = createContext<ApiContextValue | null>(null);

declare global {
  interface Window {
    /** 只在开发模式提供：端到端测试用来读适配器的提交记录、调网络模拟。 */
    __E5A__?: { adapter: SampleAdapter; getTransport: typeof getTransport; setTransport: typeof setTransport };
  }
}

export function ApiProvider({ children, adapter: given }: { children: ReactNode; adapter?: SampleAdapter }) {
  const [adapter] = useState(() => {
    const a = given ?? new SampleAdapter();
    a.restoreOrStart(readStoredScenario());
    return a;
  });
  const [scenarioId, setScenarioId] = useState(adapter.scenario.id);
  if (import.meta.env.DEV && typeof window !== "undefined") {
    window.__E5A__ = { adapter, getTransport, setTransport };
  }
  const setScenario = useCallback(
    (id: string) => {
      adapter.setScenario(id);
      try {
        sessionStorage.setItem(SCENARIO_KEY, id);
      } catch {
        /* 存不了就只在本页有效 */
      }
      setScenarioId(id);
    },
    [adapter],
  );
  const value = useMemo(
    () => ({ api: adapter as ProductApi, adapter, scenario: scenarioById(scenarioId), setScenario }),
    [adapter, scenarioId, setScenario],
  );
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApiContext(): ApiContextValue {
  const v = useContext(ApiContext);
  if (!v) throw new Error("ApiProvider 没有包住这个组件");
  return v;
}

export const useApi = (): ProductApi => useApiContext().api;
