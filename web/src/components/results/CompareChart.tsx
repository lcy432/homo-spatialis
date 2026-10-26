// 城市比较图：每座城一行。圆点 = 完成且评价完整的分身的平均 J（分母 C），颜色用本轮共用的 J 色阶；
// 横条 = 模拟缺失范围（分母 C + M）。横条不是误差条或置信区间，不保证包含圆点：两者各按公共值定位，不扩范围、不夹住均值。
// 数轴包含全部均值与全部范围端点。点与条用百分比定位（任何宽度下位置都准确）；表格是等价的文字版本。
import { useTranslation } from "react-i18next";
import { Zh } from "../../i18n/biz";
import { useMode } from "../../ui/mode";
import { type ColorWindow, positionIn, rampColor } from "../../view/colorScale";
import { type CityRow, chartDomain, niceTicks, tickDecimals } from "../../view/results";
import styles from "./Results.module.css";

export function CompareChart({ rows, window: w }: { rows: readonly CityRow[]; window: ColorWindow | null }) {
  const { t } = useTranslation();
  const { mode } = useMode();
  const domain = chartDomain(rows);
  if (!domain) return null;
  const [lo, hi] = domain;
  const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const ticks = niceTicks(domain);
  const dec = tickDecimals(ticks);
  return (
    <figure className={styles.chart} aria-labelledby="chart-cap">
      <figcaption id="chart-cap" className="small">
        {t("results.chartNote")}
      </figcaption>
      <div className={styles.chartLegend} aria-hidden="true">
        <span>
          <span className={styles.legendDot} /> {t("results.meanDot")}
        </span>
        <span>
          <span className={styles.legendBand} /> {t("results.rangeBar")}
        </span>
      </div>
      <div className={styles.chartRows} role="img" aria-label={t("results.chartTitle")}>
        <div className={styles.axisRow} aria-hidden="true">
          <span />
          <div className={styles.axis}>
            {ticks.map((v) => (
              <span key={v} className={styles.tick} style={{ left: `${x(v)}%` }}>
                {v.toFixed(dec)}
              </span>
            ))}
          </div>
        </div>
        {rows.map((r) => (
          <div key={r.cityRef} className={styles.chartRow} data-city={r.cityRef}>
            <span className={styles.rowLabel}>
              <Zh pointer={r.labelPointer}>{r.label}</Zh>
            </span>
            <div className={styles.track}>
              {ticks.map((v) => (
                <span key={v} className={styles.grid} style={{ left: `${x(v)}%` }} />
              ))}
              {r.low !== null && r.high !== null ? (
                // 两端严格按公共值定位，没有最小宽度（独立核查 F4）：上下界相等就画一道竖线，不把零宽范围画成一段
                <span
                  className={r.high > r.low ? styles.band : styles.bandPoint}
                  data-band={`${r.city.range_lower?.exact}..${r.city.range_upper?.exact}`}
                  style={r.high > r.low ? { left: `${x(r.low)}%`, width: `${x(r.high) - x(r.low)}%` } : { left: `${x(r.low)}%` }}
                  title={`${t("results.rangeBar")} ${r.city.range_lower?.display}–${r.city.range_upper?.display}`}
                />
              ) : null}
              {r.mean !== null ? (
                <span
                  className={styles.dot}
                  data-mean={r.city.mean_j?.exact}
                  style={{ left: `${x(r.mean)}%`, background: w ? rampColor(positionIn(r.mean, w), mode) : undefined }}
                  title={`${t("results.meanDot")} ${r.city.mean_j?.display}`}
                />
              ) : (
                <span className={styles.noMean}>{t("results.noColor")}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </figure>
  );
}

export function ColorLegend({ window: w }: { window: ColorWindow | null }) {
  const { t } = useTranslation();
  const { mode } = useMode();
  if (!w) return <p className="small muted">{t("results.noColor")}</p>;
  const stops = Array.from({ length: 11 }, (_, i) => rampColor(i / 10, mode));
  return (
    <div className={styles.legend} data-window={`${w.lo.toFixed(4)}..${w.hi.toFixed(4)}`}>
      <p className="small">
        <strong>{t("results.legendTitle")}</strong>
      </p>
      <div className={styles.ramp} style={{ background: `linear-gradient(90deg, ${stops.join(", ")})` }} aria-hidden="true" />
      <div className={styles.rampLabels}>
        <span>
          {w.lo.toFixed(3)} · {t("results.legendLow")}
        </span>
        <span>
          {t("results.legendHigh")} · {w.hi.toFixed(3)}
        </span>
      </div>
      <p className="small muted">{t("results.legendWindow", { lo: w.lo.toFixed(3), hi: w.hi.toFixed(3) })}</p>
    </div>
  );
}

/** C / E / M 组成条：长度按人数，三段各有不同的纹理与文字，不只靠颜色。 */
export function CemBar({ planned, c, e, m }: { planned: number; c: number; e: number; m: number }) {
  const { t } = useTranslation();
  if (!planned) return null;
  const pct = (n: number) => `${(n / planned) * 100}%`;
  return (
    <div className={styles.cem} role="img" aria-label={t("results.cem", { planned, c, e, m })}>
      {c ? <span className={styles.cemC} style={{ width: pct(c) }}>C {c}</span> : null}
      {e ? <span className={styles.cemE} style={{ width: pct(e) }}>E {e}</span> : null}
      {m ? <span className={styles.cemM} style={{ width: pct(m) }}>M {m}</span> : null}
    </div>
  );
}
