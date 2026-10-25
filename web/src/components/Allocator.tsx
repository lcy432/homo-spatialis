// 定和分配器（六维 / 物质内部）：饼图 + 每项一根滑块。合计基准固定 100；没分出去的部分是“未分配”扇区（中性纹理，不是第七个偏好）。
// * 初始不预填；“各项均分”要用户主动点（六维每项 50/3、物质每项 25，保存的是精确值）。
// * 增加某一项默认从未分配池取；减少退回未分配池。池不够时用户先选一个来源维度，再拖目标滑块：只改这两项，界面写明“休闲 → 安全”。
// * 手势开始记下精确基准；滑动按相对基准的整点数增减算（不从屏幕上的舍入数字反算），范围限制在双方可转移的额度内；
//   拖到端点就精确转完剩余（50/3 + 20/3 = 70/3 之类）。一次拖动 = 一条撤销记录；过程中只在本地预览，松手才写进草稿。
// * 未答的项显示“未答”，不当成 0；合计 100 而还有未答时，用户点“将其余项目设为 0”才写零。
// * 数字输入框保留（精确输入与无障碍）；写法不对的项原样保留、不计入合计。合计是填写辅助，正式结论以 E3 审阅为准。
import { useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AllocationGroup, DimensionDefinition, MaterialLeafDefinition, PointEntry } from "../contract/types";
import { useDraftEditor } from "../draft/DraftEditor";
import { equalValue, groupKeys, groupLabels } from "../draft/draft";
import { Fraction, type PointsParse, canonical, displayFixed, parsePoints, remainingOf, sumProvided } from "../draft/exact";
import { Zh } from "../i18n/biz";
import { Dialog } from "./Dialog";
import { SupportChip } from "./common";
import styles from "./Allocator.module.css";

/** 能精确写成 decimals 位以内的小数就直接写（“5”“16.5”），否则写四舍五入后的显示值，另附精确值。 */
function friendly(q: Fraction, decimals: number): { text: string; exact: string | null } {
  const scale = 10n ** BigInt(decimals);
  if ((q.n * scale) % q.d === 0n) {
    const whole = (q.n * scale) / q.d;
    let text = whole.toString().padStart(decimals + 1, "0");
    if (decimals > 0) text = `${text.slice(0, -decimals)}.${text.slice(-decimals)}`.replace(/\.?0+$/, "");
    return { text: text || "0", exact: null };
  }
  return { text: displayFixed(q, decimals), exact: canonical(q) };
}

function toNumber(q: Fraction): number {
  return Number(q.s * q.n) / Number(q.d);
}

function definitionOf(d: DimensionDefinition | MaterialLeafDefinition) {
  if ("reference_improvement" in d) {
    return { definition: d.definition, reference: d.reference_improvement, note: d.support !== "supported" ? d.support_note : null };
  }
  return { definition: d.definition, reference: null, note: d.note };
}

interface Drag {
  key: string;
  /** 手势开始时目标项的精确值；sliderStart 是它在滑块上的整数位置。 */
  base: Fraction;
  sliderStart: number;
  /** 这次手势能增加的精确额度（来源项的值，或未分配池）。 */
  room: Fraction;
  source: string | null;
  sourceBase: Fraction | null;
  value: Fraction;
}

const PIE_R = 48;
const PIE_C = 56;

function arcPath(startFrac: number, endFrac: number): string {
  const a0 = startFrac * 2 * Math.PI - Math.PI / 2;
  const a1 = endFrac * 2 * Math.PI - Math.PI / 2;
  const x0 = PIE_C + PIE_R * Math.cos(a0);
  const y0 = PIE_C + PIE_R * Math.sin(a0);
  const x1 = PIE_C + PIE_R * Math.cos(a1);
  const y1 = PIE_C + PIE_R * Math.sin(a1);
  const large = endFrac - startFrac > 0.5 ? 1 : 0;
  return `M ${PIE_C} ${PIE_C} L ${x0} ${y0} A ${PIE_R} ${PIE_R} 0 ${large} 1 ${x1} ${y1} Z`;
}

export function Allocator({ group, title, help }: { group: AllocationGroup; title: string; help: string }) {
  const { t } = useTranslation();
  const { caps, state, dispatch } = useDraftEditor();
  const uid = useId();
  const keys = groupKeys(caps, group);
  const labels = groupLabels(caps, group);
  const defs = (group === "six_dimensions" ? caps.dimensions : caps.material_leaves) as (DimensionDefinition | MaterialLeafDefinition)[];
  const maxDec = caps.points.manual_max_decimals;
  const dispDec = caps.points.display_decimals;
  const answer = state.draft[group];
  const entries: Record<string, PointEntry> = answer.entries ?? {};
  const isEqual = answer.mode === "equal_split";
  const eqExact = equalValue(caps, group);
  const eqDisplay = friendly(new Fraction(eqExact), dispDec).text;

  const [equalDialog, setEqualDialog] = useState(false);
  const [picker, setPicker] = useState<{ amount: string } | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [drag, setDragState] = useState<Drag | null>(null);
  // 同一个事件里预览与提交先后到来（React 的 onChange 对 range 也是 input 事件）：用 ref 读最新的手势，不读闭包
  const dragRef = useRef<Drag | null>(null);
  const setDrag = (d: Drag | null) => {
    dragRef.current = d;
    setDragState(d);
  };

  // 已写进草稿的值（均分状态下每项都是精确的均分值）
  const counted: Record<string, PointEntry | undefined> = isEqual
    ? Object.fromEntries(keys.map((k) => [k, { state: "provided" as const, value_text: eqExact }]))
    : entries;
  const totals = sumProvided(counted, keys, maxDec);
  const rem = remainingOf(totals.total);
  const committedOf = (k: string): Fraction | null => {
    const e = counted[k];
    if (!e || e.state !== "provided" || e.value_text == null) return null;
    const p = parsePoints(e.value_text, maxDec);
    return p.ok ? p.value : null;
  };
  const pool = rem.kind === "remaining" ? rem.amount : new Fraction(0);

  // 拖动中的预览：目标项与来源项按手势算，其余照已写入的值
  const shownOf = (k: string): Fraction | null => {
    if (drag) {
      if (k === drag.key) return drag.value;
      if (drag.source === k && drag.sourceBase) return drag.sourceBase.sub(drag.value.sub(drag.base));
    }
    return committedOf(k);
  };
  const shownTotal = useMemo(() => {
    let sum = new Fraction(0);
    for (const k of keys) {
      const v = shownOf(k);
      if (v) sum = sum.add(v);
    }
    return sum;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, drag, entries, isEqual]);
  const shownRem = remainingOf(shownTotal);
  const shownPool = shownRem.kind === "remaining" ? shownRem.amount : new Fraction(0);
  const unanswered = isEqual ? [] : keys.filter((k) => !entries[k]);
  const sourceValid = source !== null && keys.includes(source) && (committedOf(source)?.compare(0) ?? 0) > 0;
  const activeSource = sourceValid ? source : null;

  /** 这一项现在能增加多少（来源项的值，或未分配池）。 */
  const roomFor = (k: string): Fraction => {
    if (activeSource && activeSource !== k) return committedOf(activeSource) ?? new Fraction(0);
    return pool;
  };

  const commit = (dr: Drag) => {
    dispatch({
      type: "points/move",
      group,
      key: dr.key,
      value: canonical(dr.value),
      source: dr.source,
      maxDecimals: maxDec,
      fromEqual: isEqual ? { keys, value: eqExact } : undefined,
    });
  };

  /** 滑块的每一次输入：第一次记下基准，之后按相对基准的整点数算；端点精确转完。 */
  const onSlide = (k: string, raw: number, e: { currentTarget: HTMLInputElement }) => {
    const base = drag?.key === k ? drag.base : (committedOf(k) ?? new Fraction(0));
    const sliderStart = drag?.key === k ? drag.sliderStart : Math.round(toNumber(base));
    const src = drag?.key === k ? drag.source : activeSource && activeSource !== k ? activeSource : null;
    const sourceBase = drag?.key === k ? drag.sourceBase : src ? (committedOf(src) ?? new Fraction(0)) : null;
    const room = drag?.key === k ? drag.room : roomFor(k);
    const hiExact = base.add(room);
    const hi = toNumber(hiExact);
    let slider = Math.round(raw);
    let value: Fraction;
    if (slider >= Math.floor(hi) && slider >= sliderStart) {
      slider = Math.max(sliderStart, Math.floor(hi));
      value = hiExact; // 拖到头：精确转完剩余
    } else if (slider <= 0) {
      slider = 0;
      value = new Fraction(0);
    } else {
      value = base.add(slider - sliderStart);
      if (value.s < 0n) value = new Fraction(0);
      if (value.compare(hiExact) > 0) value = hiExact;
    }
    e.currentTarget.value = String(slider);
    setDrag({ key: k, base, sliderStart, room, source: src, sourceBase, value });
  };

  const endSlide = () => {
    const dr = dragRef.current;
    if (!dr) return;
    if (dr.value.compare(dr.base) !== 0) commit(dr);
    setDrag(null);
  };

  const hintOf = (p: Extract<PointsParse, { ok: false }>) =>
    p.reason === "too_many_decimals"
      ? t("priorities.hintDecimals", { n: maxDec })
      : p.reason === "fraction_not_canonical"
        ? t("priorities.hintFraction", { canonical: p.canonical })
        : t("priorities.hintNumber");

  const amount = friendly(shownRem.amount, dispDec);
  const statusText =
    shownRem.kind === "full"
      ? t("priorities.full")
      : shownRem.kind === "over"
        ? t("priorities.over", { x: amount.text })
        : t("priorities.remaining", { x: amount.text });

  // 饼图：扇区按点数占 100 的比例；超过 100 时按实际合计画、外圈标红
  const pieBase = shownRem.kind === "over" ? shownTotal : new Fraction(100);
  const slices: { key: string | null; from: number; to: number }[] = [];
  let acc = 0;
  for (const k of keys) {
    const v = shownOf(k);
    if (!v || v.n === 0n) continue;
    const frac = toNumber(v) / toNumber(pieBase);
    slices.push({ key: k, from: acc, to: acc + frac });
    acc += frac;
  }
  if (shownRem.kind === "remaining") slices.push({ key: null, from: acc, to: 1 });

  const pieLabel = keys
    .map((k) => {
      const v = shownOf(k);
      return `${labels[k]}：${v ? friendly(v, dispDec).text : t("states.unanswered")}`;
    })
    .concat(shownRem.kind === "remaining" ? [`${t("priorities.unallocated")}：${friendly(shownPool, dispDec).text}`] : [])
    .join("；");

  return (
    <div className={styles.wrap} data-allocator={group} data-mode={isEqual ? "equal_split" : "manual"}>
      <h2>{title}</h2>
      <p className="muted">{help}</p>

      <div className={styles.layout}>
        <figure className={styles.pie}>
          <svg viewBox="0 0 112 112" role="img" aria-label={`${title}：${pieLabel}`} data-pie={group}>
            <defs>
              <pattern id={`${uid}-hatch`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="5" className={styles.pieHatchLine} />
              </pattern>
            </defs>
            {slices.length === 0 ? <circle cx={PIE_C} cy={PIE_C} r={PIE_R} className={styles.pieEmpty} /> : null}
            {slices.map((s) =>
              s.to - s.from >= 0.9999 ? (
                <circle
                  key={s.key ?? "pool"}
                  cx={PIE_C}
                  cy={PIE_C}
                  r={PIE_R}
                  className={s.key ? styles.pieSlice : styles.piePool}
                  style={s.key ? { fill: `var(--pie-${(keys.indexOf(s.key) % 6) + 1})` } : { fill: `url(#${uid}-hatch)` }}
                  data-slice={s.key ?? "unallocated"}
                />
              ) : (
                <path
                  key={s.key ?? "pool"}
                  d={arcPath(s.from, s.to)}
                  className={s.key ? styles.pieSlice : styles.piePool}
                  style={s.key ? { fill: `var(--pie-${(keys.indexOf(s.key) % 6) + 1})` } : { fill: `url(#${uid}-hatch)` }}
                  data-slice={s.key ?? "unallocated"}
                />
              ),
            )}
            {shownRem.kind === "over" ? <circle cx={PIE_C} cy={PIE_C} r={PIE_R + 3} className={styles.pieOver} /> : null}
          </svg>
          <figcaption className={styles.pieCaption}>
            <span role="status" aria-live="polite" id={`${uid}-status`} className={styles.statusText}>
              {shownRem.kind === "full" ? <span className="stamp">{statusText}</span> : statusText}
              {amount.exact ? (
                <span className="small muted">
                  （{t("common.exact")} {amount.exact}）
                </span>
              ) : null}
            </span>
            {shownRem.kind === "remaining" ? (
              <span className={`small muted ${styles.poolNote}`}>
                <span className={styles.swatchPool} aria-hidden="true" /> {t("priorities.unallocatedNote")}
              </span>
            ) : null}
          </figcaption>
        </figure>

        <div className={styles.controlsCol}>
          <div className={styles.toolbar}>
            <button type="button" className={isEqual || keys.some((k) => entries[k]) ? undefined : "primary"} onClick={() => setEqualDialog(true)} disabled={isEqual}>
              {t("priorities.equalSplit")}
            </button>
            <button type="button" onClick={() => dispatch({ type: "history/undo" })} disabled={!state.past.length}>
              {t("common.undo")}
            </button>
            <button type="button" onClick={() => dispatch({ type: "history/redo" })} disabled={!state.future.length}>
              {t("common.redo")}
            </button>
          </div>
          {isEqual ? <p className="box note">{t("priorities.equalSplitOn", { each: eqDisplay, exact: eqExact })}</p> : null}

          {/* 来源：增加某一项时从哪里取。池里还有就默认从池取；池空了要用户明确选一个来源维度 */}
          <p className={styles.sourceRow}>
            <label htmlFor={`${uid}-source`}>{t("priorities.sourceLabel")}</label>{" "}
            <select id={`${uid}-source`} value={activeSource ?? ""} onChange={(e) => setSource(e.target.value || null)} data-source-select>
              <option value="">{t("priorities.sourcePool", { x: friendly(pool, dispDec).text })}</option>
              {keys
                .filter((k) => (committedOf(k)?.compare(0) ?? 0) > 0)
                .map((k) => (
                  <option key={k} value={k}>
                    {t("priorities.sourceDim", { label: labels[k], x: friendly(committedOf(k) as Fraction, dispDec).text })}
                  </option>
                ))}
            </select>
          </p>
          {pool.n === 0n && !activeSource && shownRem.kind !== "over" ? <p className="small muted">{t("priorities.poolEmpty")}</p> : null}
          <p className="small muted">{t("priorities.sliderHint")}</p>
        </div>
      </div>

      <ol className={styles.rows}>
        {defs.map((d, i) => {
          const k = d.key;
          const e = entries[k];
          const inputId = `${uid}-${k}`;
          const sliderId = `${uid}-${k}-slider`;
          const descId = `${uid}-${k}-desc`;
          const errId = `${uid}-${k}-err`;
          const info = definitionOf(d);
          const committed = committedOf(k);
          const shown = shownOf(k);
          const parsed = !isEqual && e?.state === "provided" && e.value_text != null ? parsePoints(e.value_text, maxDec) : null;
          const stateSel = !isEqual && (e?.state === "indifferent" || e?.state === "unsure") ? e.state : "";
          const textShown = isEqual ? eqExact : e?.state === "provided" ? (e.value_text ?? "") : "";
          const describedBy = [descId, parsed && !parsed.ok ? errId : null, stateSel ? `${errId}-state` : null].filter(Boolean).join(" ");
          const sliderDisabled = !!stateSel || (parsed !== null && !parsed.ok);
          const sliderValue = drag?.key === k ? Math.round(toNumber(drag.value)) : committed ? Math.round(toNumber(committed)) : 0;
          const transferText =
            drag?.key === k && drag.source ? t("priorities.transferFrom", { from: labels[drag.source] ?? drag.source, to: labels[k] ?? k }) : activeSource && activeSource !== k ? t("priorities.transferFrom", { from: labels[activeSource] ?? activeSource, to: labels[k] ?? k }) : null;
          const display = shown ? friendly(shown, dispDec) : null;
          return (
            <li key={k} className={styles.row} data-key={k} data-state={isEqual ? "provided" : (e?.state ?? "unanswered")}>
              <div className={styles.rowHead}>
                <span className={styles.swatch} style={{ background: `var(--pie-${(i % 6) + 1})` }} aria-hidden="true" />
                <label htmlFor={sliderId} className={styles.rowLabel}>
                  <Zh>{labels[k]}</Zh>
                </label>
                {d.support !== "supported" ? <SupportChip support={d.support} /> : null}
                <span className={styles.rowValue} data-value={shown ? canonical(shown) : ""}>
                  {!isEqual && !e ? (
                    <span className="chip">{t("states.unanswered")}</span>
                  ) : stateSel ? (
                    <span className="chip">{t(`states.${stateSel}`)}</span>
                  ) : display ? (
                    <>
                      <strong className="num">{display.text}</strong>
                      {display.exact ? <span className="small muted">（{display.exact}）</span> : null}
                    </>
                  ) : null}
                </span>
              </div>
              <div className={styles.sliderRow}>
                <input
                  id={sliderId}
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  className={styles.slider}
                  value={sliderValue}
                  disabled={sliderDisabled}
                  aria-valuetext={display ? `${display.text}${display.exact ? `（${display.exact}）` : ""}` : t("states.unanswered")}
                  aria-describedby={descId}
                  data-slider={k}
                  onInput={(ev) => onSlide(k, Number(ev.currentTarget.value), ev)}
                  onChange={() => undefined}
                  onPointerUp={endSlide}
                  onPointerCancel={endSlide}
                  onKeyUp={endSlide}
                  onBlur={() => {
                    endSlide();
                    dispatch({ type: "history/break" });
                  }}
                />
                {transferText ? <span className={`small ${styles.transfer}`}>{transferText}</span> : null}
              </div>
              <div className={styles.controls}>
                <label htmlFor={inputId} className="small muted">
                  {t("priorities.exactInput")}
                </label>
                <input
                  id={inputId}
                  name={`${group}.${k}`}
                  className={styles.input}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={textShown}
                  aria-describedby={describedBy}
                  aria-invalid={parsed && !parsed.ok ? true : undefined}
                  onChange={(ev) =>
                    isEqual
                      ? dispatch({ type: "points/move", group, key: k, value: ev.target.value, source: null, maxDecimals: maxDec, fromEqual: { keys, value: eqExact } })
                      : dispatch({ type: "points/text", group, key: k, text: ev.target.value })
                  }
                  onBlur={() => dispatch({ type: "history/break" })}
                />
                <select
                  aria-label={`${labels[k]}：${t("priorities.unsureMenu")}`}
                  value={stateSel}
                  onChange={(ev) => {
                    const v = ev.target.value;
                    if (isEqual) dispatch({ type: "points/toManual", group, keys, value: eqExact });
                    dispatch({ type: "points/state", group, key: k, state: v === "indifferent" || v === "unsure" ? v : "unanswered" });
                  }}
                >
                  <option value="">{t("priorities.unsureMenu")}</option>
                  <option value="indifferent">{t("states.indifferent")}</option>
                  <option value="unsure">{t("states.unsure")}</option>
                </select>
                <details className={styles.what}>
                  <summary>{t("priorities.whatIsThis")}</summary>
                  <div id={descId}>
                    <Zh as="p">{info.definition}</Zh>
                    {info.reference ? (
                      <p className="small">
                        <Zh>{info.reference}</Zh>
                      </p>
                    ) : null}
                    {info.note ? (
                      <p className="small muted">
                        <Zh>{info.note}</Zh>
                      </p>
                    ) : null}
                  </div>
                </details>
              </div>
              {parsed && !parsed.ok ? (
                <p id={errId} className="small" style={{ color: "var(--error-line)" }}>
                  {t("priorities.invalid")}
                  {hintOf(parsed)}
                </p>
              ) : null}
              {stateSel ? (
                <p id={`${errId}-state`} className="small">
                  {stateSel === "indifferent" ? t("states.indifferent") : t("states.unsure")}：{t("priorities.unsetNote")}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>

      <div className={`${styles.status} ${rem.kind === "over" ? styles.over : rem.kind === "full" ? styles.full : ""}`}>
        <span aria-hidden="true">{statusText}</span>
        {rem.kind === "over" && !isEqual ? (
          <button type="button" onClick={() => setPicker({ amount: canonical(rem.amount) })}>
            {t("priorities.reduceFrom", { x: amount.text })}
          </button>
        ) : null}
        {rem.kind === "full" && unanswered.length ? (
          <button type="button" data-action="rest-zero" onClick={() => dispatch({ type: "points/setRestZero", group, keys: unanswered })}>
            {t("priorities.setRestZero", { n: unanswered.length })}
          </button>
        ) : null}
        {totals.invalid.length ? <span className="small">{t("priorities.notCounted", { n: totals.invalid.length })}</span> : null}
      </div>

      <Dialog open={equalDialog} onClose={() => setEqualDialog(false)} title={t("priorities.equalSplit")}>
        <p>{t("priorities.equalSplitConfirm", { each: eqDisplay, exact: eqExact })}</p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              dispatch({ type: "points/equalSplit", group });
              setEqualDialog(false);
            }}
          >
            {t("priorities.equalSplit")}
          </button>
          <button type="button" onClick={() => setEqualDialog(false)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>

      <Dialog open={picker !== null} onClose={() => setPicker(null)} title={t("priorities.reduceFrom", { x: amount.text })}>
        <p className="small">{t("priorities.moveNote")}</p>
        <ul className={styles.pickList}>
          {picker
            ? keys
                .filter((k) => (committedOf(k)?.compare(0) ?? 0) > 0)
                .map((k) => (
                  <li key={k}>
                    <button
                      type="button"
                      onClick={() => {
                        dispatch({ type: "points/reduce", group, key: k, amount: picker.amount, maxDecimals: maxDec });
                        setPicker(null);
                      }}
                    >
                      {t("priorities.pointsOf", { label: labels[k], v: friendly(committedOf(k) as Fraction, dispDec).text })}
                    </button>
                  </li>
                ))
            : null}
        </ul>
        <button type="button" onClick={() => setPicker(null)}>
          {t("common.cancel")}
        </button>
      </Dialog>
    </div>
  );
}
