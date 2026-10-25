// 视觉模式：稿纸（默认）或写实地球。模式只是外观：同一份答案、选择、运行与结果，切换不保存、不提交任何东西。
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { type VisualMode, initialMode, saveMode } from "../prefs";

interface ModeValue {
  mode: VisualMode;
  setMode(mode: VisualMode): void;
}

const ModeContext = createContext<ModeValue>({ mode: "paper", setMode: () => undefined });

export function ModeProvider({ children, initial }: { children: ReactNode; initial?: VisualMode }) {
  const [mode, setModeState] = useState<VisualMode>(() => initial ?? initialMode());
  useEffect(() => {
    document.documentElement.dataset.mode = mode;
  }, [mode]);
  const setMode = useCallback((m: VisualMode) => {
    saveMode(m);
    setModeState(m);
  }, []);
  const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);
  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}

export function useMode(): ModeValue {
  return useContext(ModeContext);
}
