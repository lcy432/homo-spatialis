// 12 个月的条带。月份按 E3 给的列表画，不从年度合计摊；每个月的状态都有纹理、符号与文字，不只靠颜色。
// 状态按公开的月份列表细分，不把所有“不是获得”的月份叫同一件事（独立核查 F3）：
//   on          已获得 / 在用
//   planned_off 已安排但没获得（服务与日常支持才有“安排”）
//   off         这个月没有安排（服务）/ 不在用（设备）——分身仍然在生活
//   none        那个月没有生活（年中结束之后的月份）
//   unknown     记录不足：公开资料里既没有获得列表也没有安排列表，不补零、不推断原因
import { useTranslation } from "react-i18next";
import styles from "./Results.module.css";

export type MonthState = "on" | "planned_off" | "off" | "none" | "unknown";

/** 条带的类型决定每个状态的措辞：服务（安排 / 获得）、设备（在用 / 不在用）。 */
export type MonthStripKind = "service" | "device";

export function monthStates(
  on: readonly number[] | null | undefined,
  monthsLived: number,
  planned?: readonly number[] | null,
): MonthState[] {
  return Array.from({ length: 12 }, (_, i) => {
    const m = i + 1;
    if (m > monthsLived) return "none";
    if (on == null && planned == null) return "unknown";
    if (on?.includes(m)) return "on";
    if (planned != null) return planned.includes(m) ? "planned_off" : "off";
    return "off";
  });
}

const SYMBOL: Record<MonthState, string> = { on: "●", planned_off: "◌", off: "·", none: "—", unknown: "?" };
const CLASS: Record<MonthState, string | undefined> = {
  on: styles.monthOn,
  planned_off: styles.monthPlannedOff,
  off: styles.monthOff,
  none: styles.monthNone,
  unknown: styles.monthUnknown,
};

export function MonthStrip({
  label,
  states,
  values,
  kind = "service",
}: {
  label: string;
  states?: readonly MonthState[];
  values?: readonly string[];
  kind?: MonthStripKind;
}) {
  const { t } = useTranslation();
  const word = (s: MonthState): string => {
    if (s === "on") return kind === "device" ? t("clone.month.inUse") : t("clone.month.obtained");
    if (s === "planned_off") return t("clone.month.plannedMissing");
    if (s === "off") return kind === "device" ? t("clone.month.notInUse") : t("clone.month.notPlanned");
    if (s === "none") return t("clone.month.notLived");
    return t("clone.month.unknown");
  };
  const cells = Array.from({ length: 12 }, (_, i) => i);
  return (
    <div className="stack" data-strip={label}>
      <p className="small">{t("clone.monthStrip", { label })}</p>
      <ol className={`plain ${styles.months}`} aria-label={t("clone.monthStrip", { label })}>
        {cells.map((i) => {
          const s = states?.[i];
          const v = values?.[i];
          const text = v ?? (s ? word(s) : "");
          return (
            <li
              key={i}
              className={`${styles.month} ${s ? CLASS[s] : ""}`}
              data-month-state={s}
              title={`${i + 1} · ${text}`}
              style={{ animationDelay: `${i * 25}ms` }}
            >
              <span className="visually-hidden">
                {i + 1}：{text}
              </span>
              <span aria-hidden="true">{v ?? (s ? SYMBOL[s] : "")}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
