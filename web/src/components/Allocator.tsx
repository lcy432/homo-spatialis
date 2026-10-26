// 独立评分（六维 / 物质四项，DECISIONS/0047）：每项一根 0–100 的滑块 + 数字精调，互不影响、不必凑 100。
// * 初始不预填：滑块停在 0 也是“未答”；用户拖动、输入数字或点“记为 0”才成为明确的 0 或别的评分。
// * 饼图是**临时预览**：权重 = 100 × 评分 ÷ 本组已答评分之和（精确分数），只画已答的项；未答的项列出来、不画成 0；
//   已答的全是 0 时没有可用比例，不画看似有效的均分图。正式权重以 E3 审阅为准，页面不自己换算业务真值。
// * “同等重视”是用户主动把本组每项设成 50（服务 points.ratings.equal_weight_rating），会替换已有答案时先说明，可撤销。
// * 一次指针手势（按下 → 拖动 → 松开）一条撤销记录，中途只本地预览、松手才写进草稿；下一次手势是新记录（E5-R5）。
//   连续方向键按同一编辑序列合并，失焦结束。无所谓 / 暂不确定保留状态、不转数值。写法不对的数字原样保留、给出提示，不悄悄截断。
// * 旧的百分比分配（manual / equal_split）在当前版本的草稿里不能编辑：显示原义并让用户明确改成独立评分（各项重新打分）。
import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AllocationGroup, DimensionDefinition, MaterialLeafDefinition, PointEntry } from "../contract/types";
import { useDraftEditor } from "../draft/DraftEditor";
import { groupKeys } from "../draft/draft";
import { Fraction, canonical, displayFixed, parsePoints } from "../draft/exact";
import { at } from "../display/scope";
import { Zh } from "../i18n/biz";
import { Dialog } from "./Dialog";
import { SupportChip } from "./common";
import styles from "./Allocator.module.css";

/** 能精确写成 decimals 位以内的小数就直接写（“5”“16.5”），否则写四舍五入后的显示值，另附精确值。 */
export function friendly(q: Fraction, decimals: number): { text: string; exact: string | null } {
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

/** 临时预览：已答且写法正确的评分按比例换成权重（精确分数）。 */
export function previewWeights(
  entries: Record<string, PointEntry | undefined>,
  keys: readonly string[],
  maxDecimals: number,
): { ratings: Map<string, Fraction>; total: Fraction; weights: Map<string, Fraction>; unanswered: string[]; unset: string[]; invalid: string[] } {
  const ratings = new Map<string, Fraction>();
  const unanswered: string[] = [];
  const unset: string[] = [];
  const invalid: string[] = [];
  let total = new Fraction(0);
  for (const k of keys) {
    const e = entries[k];
    if (!e) unanswered.push(k);
    else if (e.state !== "provided") unset.push(k);
    else {
      const p = e.value_text == null ? null : parsePoints(e.value_text, maxDecimals);
      if (!p || !p.ok || p.value.compare(100) > 0 || (e.value_text ?? "").includes("/")) invalid.push(k);
      else {
        ratings.set(k, p.value);
        total = total.add(p.value);
      }
    }
  }
  const weights = new Map<string, Fraction>();
  if (total.n !== 0n) for (const [k, r] of ratings) weights.set(k, r.mul(100).div(total));
  return { ratings, total, weights, unanswered, unset, invalid };
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

interface Drag {
  key: string;
  value: number;
  gesture: string;
}

let gestureCounter = 0;
const newGesture = (prefix: string) => `${prefix}-${(gestureCounter += 1)}`;

export function Allocator({ group, title, help }: { group: AllocationGroup; title: string; help: string }) {
  const { t } = useTranslation();
  const { caps, state, dispatch } = useDraftEditor();
  const uid = useId();
  const keys = groupKeys(caps, group);
  const defs = (group === "six_dimensions" ? caps.dimensions : caps.material_leaves) as (DimensionDefinition | MaterialLeafDefinition)[];
  const defBase = group === "six_dimensions" ? "/dimensions" : "/material_leaves";
  const rules = caps.points.ratings ?? null;
  const maxDec = rules?.max_decimals ?? caps.points.manual_max_decimals;
  const maxRating = rules?.maximum ?? 100;
  const step = rules?.slider_step ?? 1;
  const equalRating = rules?.equal_weight_rating ?? "50";
  const dispDec = caps.points.display_decimals;
  const answer = state.draft[group];
  const entries: Record<string, PointEntry | undefined> = answer.entries ?? {};
  const legacyMode = answer.mode === "manual" || answer.mode === "equal_split";

  const [equalDialog, setEqualDialog] = useState(false);
  const [drag, setDragState] = useState<Drag | null>(null);
  // 同一个事件里预览与提交先后到来（React 的 onChange 对 range 也是 input 事件）：用 ref 读最新的手势，不读闭包
  const dragRef = useRef<Drag | null>(null);
  const setDrag = (d: Drag | null) => {
    dragRef.current = d;
    setDragState(d);
  };
  // 当前手势的编号：按下指针时换新的（一次拖动 = 一条记录）；键盘序列按项共用一个编号，失焦时结束
  const gestureRef = useRef<Record<string, string>>({});
  const gestureOf = (k: string) => gestureRef.current[k] ?? (gestureRef.current[k] = `kb-${k}`);

  const shownEntries: Record<string, PointEntry | undefined> = drag
    ? { ...entries, [drag.key]: { state: "provided", value_text: String(drag.value) } }
    : entries;
  const preview = previewWeights(shownEntries, keys, maxDec);
  const answered = preview.ratings.size + preview.invalid.length;
  const previewKind: "none" | "zero" | "partial" | "complete" =
    preview.ratings.size === 0 && preview.invalid.length === 0 ? "none" : preview.total.n === 0n ? "zero" : preview.unanswered.length || preview.unset.length ? "partial" : "complete";

  const labelOf = (k: string) => defs.find((d) => d.key === k)?.label ?? k;
  const labelPointer = (k: string) => at(defBase, defs.findIndex((d) => d.key === k), "label");

  const onSlide = (k: string, raw: number) => {
    const value = Math.min(maxRating, Math.max(0, Math.round(raw / step) * step));
    setDrag({ key: k, value, gesture: gestureOf(k) });
  };
  /** 松手 / 松键 / 失焦：把这次手势的值写进草稿（只一次）。 */
  const endSlide = () => {
    const dr = dragRef.current;
    if (!dr) return;
    setDrag(null);
    dispatch({ type: "points/set", group, key: dr.key, value: String(dr.value), gesture: dr.gesture });
  };

  const hintOf = (text: string): string | null => {
    const p = parsePoints(text, maxDec);
    if (text.includes("/")) return t("ratings.hintDecimalOnly");
    if (!p.ok) return p.reason === "too_many_decimals" ? t("ratings.hintDecimals", { n: maxDec }) : t("ratings.hintNumber");
    if (p.value.compare(maxRating) > 0) return t("ratings.hintRange", { max: maxRating });
    return null;
  };

  // 饼图：只画已答的项，按它们之间的比例
  const slices: { key: string; from: number; to: number }[] = [];
  let acc = 0;
  for (const k of keys) {
    const w = preview.weights.get(k);
    if (!w || w.n === 0n) continue;
    const frac = toNumber(w) / 100;
    slices.push({ key: k, from: acc, to: acc + frac });
    acc += frac;
  }
  const totalText = friendly(preview.total, maxDec).text;
  const pieLabel = keys
    .map((k) => {
      const w = preview.weights.get(k);
      const r = preview.ratings.get(k);
      if (r && w) return t("ratings.ariaLine", { label: labelOf(k), rating: friendly(r, maxDec).text, weight: displayFixed(w, dispDec) });
      return `${labelOf(k)}：${t("states.unanswered")}`;
    })
    .join("；");

  if (legacyMode) {
    return (
      <div className={styles.wrap} data-allocator={group} data-mode={answer.mode ?? ""}>
        <h2>{title}</h2>
        <p className="box warn" data-legacy-allocation>
          {t("ratings.legacyMode")}
        </p>
        <ul>
          {keys.map((k) => {
            const e = entries[k];
            const v = e?.state === "provided" ? (e.value_text ?? "") : t(`states.${e?.state ?? "unanswered"}` as "states.unanswered");
            return (
              <li key={k}>
                <Zh pointer={labelPointer(k)}>{labelOf(k)}</Zh>：{answer.mode === "equal_split" ? t("ratings.legacyEqual") : v}
              </li>
            );
          })}
        </ul>
        <button type="button" className="primary" data-action="rerate" onClick={() => dispatch({ type: "points/rerate", group })}>
          {t("ratings.rerate")}
        </button>
      </div>
    );
  }

  return (
    <div className={styles.wrap} data-allocator={group} data-mode="ratings_normalized_v1" data-preview={previewKind} data-rating-total={canonical(preview.total)}>
      <h2>{title}</h2>
      <p className="muted">{help}</p>
      <p className="small">{t("ratings.meaning")}</p>

      <div className={styles.layout}>
        <figure className={styles.pie}>
          <svg viewBox="0 0 112 112" role="img" aria-label={`${title}：${pieLabel}`} data-pie={group}>
            {slices.length === 0 ? <circle cx={PIE_C} cy={PIE_C} r={PIE_R} className={styles.pieEmpty} /> : null}
            {slices.map((s) =>
              s.to - s.from >= 0.9999 ? (
                <circle key={s.key} cx={PIE_C} cy={PIE_C} r={PIE_R} className={styles.pieSlice} style={{ fill: `var(--pie-${(keys.indexOf(s.key) % 6) + 1})` }} data-slice={s.key} />
              ) : (
                <path key={s.key} d={arcPath(s.from, s.to)} className={styles.pieSlice} style={{ fill: `var(--pie-${(keys.indexOf(s.key) % 6) + 1})` }} data-slice={s.key} />
              ),
            )}
          </svg>
          <figcaption className={styles.pieCaption}>
            <span role="status" aria-live="polite" id={`${uid}-status`} className={styles.statusText}>
              {previewKind === "none"
                ? t("ratings.noneYet")
                : previewKind === "zero"
                  ? t("ratings.allZero")
                  : previewKind === "partial"
                    ? t("ratings.previewPartial", { n: answered, total: keys.length })
                    : t("ratings.previewComplete")}
            </span>
            {previewKind === "partial" ? (
              <span className="small muted">
                {preview.unanswered.length ? `${t("ratings.unansweredList")}：${preview.unanswered.map(labelOf).join("、")}` : null}
                {preview.unanswered.length && preview.unset.length ? "；" : null}
                {preview.unset.length ? `${t("ratings.unsetList")}：${preview.unset.map(labelOf).join("、")}` : null}
              </span>
            ) : null}
            {previewKind !== "none" ? <span className="small muted">{t("ratings.total", { x: totalText })}</span> : null}
            <span className="small muted">{t("ratings.previewNote")}</span>
          </figcaption>
        </figure>

        <div className={styles.controlsCol}>
          <div className={styles.toolbar}>
            <button type="button" data-action="equal-weight" onClick={() => (answered || preview.unset.length ? setEqualDialog(true) : dispatch({ type: "points/equalWeight", group, keys, value: equalRating }))}>
              {t("ratings.equalWeight")}
            </button>
            <button type="button" onClick={() => dispatch({ type: "history/undo" })} disabled={!state.past.length}>
              {t("common.undo")}
            </button>
            <button type="button" onClick={() => dispatch({ type: "history/redo" })} disabled={!state.future.length}>
              {t("common.redo")}
            </button>
          </div>
          <p className="small muted">{t("ratings.sliderHint", { max: maxRating, decimals: maxDec })}</p>
          {rules?.rules.length ? (
            <details className={styles.what}>
              <summary>{t("ratings.serviceRules")}</summary>
              <ul className="small">
                {rules.rules.map((r, i) => (
                  <Zh as="li" key={r} pointer={at("/points/ratings/rules", i)}>
                    {r}
                  </Zh>
                ))}
              </ul>
            </details>
          ) : null}
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
          const text = e?.state === "provided" ? (e.value_text ?? "") : "";
          const hint = e?.state === "provided" && text !== "" ? hintOf(text) : null;
          const stateSel = e?.state === "indifferent" || e?.state === "unsure" ? e.state : "";
          const rating = preview.ratings.get(k) ?? null;
          const weight = preview.weights.get(k) ?? null;
          const sliderValue = drag?.key === k ? drag.value : rating ? Math.round(toNumber(rating)) : 0;
          const describedBy = [descId, hint ? errId : null, stateSel ? `${errId}-state` : null].filter(Boolean).join(" ");
          const isDimension = "reference_improvement" in d;
          return (
            <li key={k} className={styles.row} data-key={k} data-state={e?.state ?? "unanswered"}>
              <div className={styles.rowHead}>
                <span className={styles.swatch} style={{ background: `var(--pie-${(i % 6) + 1})` }} aria-hidden="true" />
                <label htmlFor={sliderId} className={styles.rowLabel}>
                  <Zh pointer={at(defBase, i, "label")}>{d.label}</Zh>
                </label>
                {d.support !== "supported" ? <SupportChip support={d.support} /> : null}
                <span className={styles.rowValue} data-value={rating ? canonical(rating) : ""} data-weight={weight ? canonical(weight) : ""}>
                  {!e ? (
                    <span className="chip">{t("states.unanswered")}</span>
                  ) : stateSel ? (
                    <span className="chip">{t(`states.${stateSel}`)}</span>
                  ) : rating ? (
                    <>
                      <strong className="num">{friendly(rating, maxDec).text}</strong>
                      {weight ? (
                        <span className="small muted">
                          {" "}
                          {t("ratings.weightOf", { w: displayFixed(weight, dispDec) })}
                          {friendly(weight, dispDec).exact ? `（${canonical(weight)}）` : ""}
                        </span>
                      ) : null}
                    </>
                  ) : (
                    <span className="chip warn">{t("ratings.invalidChip")}</span>
                  )}
                </span>
              </div>
              <div className={styles.sliderRow}>
                <input
                  id={sliderId}
                  type="range"
                  min={0}
                  max={maxRating}
                  step={step}
                  className={styles.slider}
                  value={sliderValue}
                  disabled={!!stateSel}
                  aria-valuetext={rating ? t("ratings.ariaValue", { v: friendly(rating, maxDec).text }) : t("states.unanswered")}
                  aria-describedby={descId}
                  data-slider={k}
                  onPointerDown={() => {
                    gestureRef.current[k] = newGesture(`ptr-${k}`);
                  }}
                  onInput={(ev) => onSlide(k, Number(ev.currentTarget.value))}
                  onChange={() => undefined}
                  onPointerUp={() => {
                    endSlide();
                    gestureRef.current[k] = `kb-${k}`;
                  }}
                  onPointerCancel={() => {
                    endSlide();
                    gestureRef.current[k] = `kb-${k}`;
                  }}
                  onKeyUp={endSlide}
                  onBlur={() => {
                    endSlide();
                    dispatch({ type: "history/break" });
                  }}
                />
                {!e ? (
                  <button type="button" className="small" data-action="zero" onClick={() => dispatch({ type: "points/set", group, key: k, value: "0", gesture: newGesture(`zero-${k}`) })}>
                    {t("ratings.markZero")}
                  </button>
                ) : null}
              </div>
              <div className={styles.controls}>
                <label htmlFor={inputId} className="small muted">
                  {t("ratings.exactInput")}
                </label>
                <input
                  id={inputId}
                  name={`${group}.${k}`}
                  className={styles.input}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={text}
                  aria-describedby={describedBy}
                  aria-invalid={hint ? true : undefined}
                  onChange={(ev) => dispatch({ type: "points/text", group, key: k, text: ev.target.value })}
                  onBlur={() => dispatch({ type: "history/break" })}
                />
                <select
                  aria-label={`${d.label}：${t("priorities.unsureMenu")}`}
                  value={stateSel}
                  onChange={(ev) => {
                    const v = ev.target.value;
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
                    <Zh as="p" pointer={at(defBase, i, "definition")}>
                      {d.definition}
                    </Zh>
                    {isDimension ? (
                      <p className="small">
                        <Zh pointer={at(defBase, i, "reference_improvement")}>{(d as DimensionDefinition).reference_improvement}</Zh>
                      </p>
                    ) : (d as MaterialLeafDefinition).note ? (
                      <p className="small muted">
                        <Zh pointer={at(defBase, i, "note")}>{(d as MaterialLeafDefinition).note}</Zh>
                      </p>
                    ) : null}
                    {isDimension && d.support !== "supported" && (d as DimensionDefinition).support_note ? (
                      <p className="small muted">
                        <Zh pointer={at(defBase, i, "support_note")}>{(d as DimensionDefinition).support_note}</Zh>
                      </p>
                    ) : null}
                  </div>
                </details>
              </div>
              {hint ? (
                <p id={errId} className="small" style={{ color: "var(--error-line)" }}>
                  {t("priorities.invalid")}
                  {hint}
                </p>
              ) : null}
              {stateSel ? (
                <p id={`${errId}-state`} className="small">
                  {stateSel === "indifferent" ? t("states.indifferent") : t("states.unsure")}：{t("ratings.unsetNote")}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>

      <Dialog open={equalDialog} onClose={() => setEqualDialog(false)} title={t("ratings.equalWeight")}>
        <p>{t("ratings.equalWeightConfirm", { v: equalRating, n: answered + preview.unset.length })}</p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              dispatch({ type: "points/equalWeight", group, keys, value: equalRating });
              setEqualDialog(false);
            }}
          >
            {t("ratings.equalWeight")}
          </button>
          <button type="button" onClick={() => setEqualDialog(false)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
