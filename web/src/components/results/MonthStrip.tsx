// 12 个月的条带：有 / 没有 / 那个月没有生活，三种状态各有纹理与文字（不只靠颜色）。月份按 E3 给的列表画，不从年度合计摊。
import { useTranslation } from "react-i18next";
import styles from "./Results.module.css";

export type MonthState = "on" | "off" | "none";

export function monthStates(on: readonly number[] | undefined, monthsLived: number, considered?: readonly number[]): MonthState[] {
  return Array.from({ length: 12 }, (_, i) => {
    const m = i + 1;
    if (m > monthsLived) return "none";
    if (on?.includes(m)) return "on";
    if (considered && !considered.includes(m)) return "none";
    return "off";
  });
}

export function MonthStrip({ label, states, values }: { label: string; states?: readonly MonthState[]; values?: readonly string[] }) {
  const { t } = useTranslation();
  const cells = Array.from({ length: 12 }, (_, i) => i);
  return (
    <div className="stack" data-strip={label}>
      <p className="small">{t("clone.monthStrip", { label })}</p>
      <ol className={`plain ${styles.months}`} aria-label={t("clone.monthStrip", { label })}>
        {cells.map((i) => {
          const s = states?.[i];
          const v = values?.[i];
          const cls = s === "on" ? styles.monthOn : s === "off" ? styles.monthOff : s === "none" ? styles.monthNone : "";
          const word = s === "on" ? t("clone.legendDone") : s === "off" ? t("clone.legendNo") : s === "none" ? t("clone.legendNotLived") : "";
          return (
            <li key={i} className={`${styles.month} ${cls}`} title={`${i + 1} · ${v ?? word}`}>
              <span className="visually-hidden">
                {i + 1}：{v ?? word}
              </span>
              <span aria-hidden="true">{v ?? (s === "on" ? "●" : s === "off" ? "○" : "—")}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
