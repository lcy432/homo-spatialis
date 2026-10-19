// 定和分配器（六维 / 物质内部）。规则见所有者确认的设计（docs/design/prompts/2026-10-18-e5-a-offline-ui.md 模块 D）：
// 不预填；超过 100 不拦输入、不自动扣减，由用户点选从哪一项减；平均分配要用户主动点；均分之后改一项，先问要不要改成手动分配
// （其余各项保留精确的 50/3）；写法不对的项原样保留、不计入合计；−/＋ 每次 5 分只是方便。合计是填写辅助，正式结论以 E3 审阅为准。
import { useId, useState } from "react";
import type { AllocationGroup, DimensionDefinition, MaterialLeafDefinition, PointEntry } from "../contract/types";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { equalValue, groupKeys, groupLabels } from "../draft/draft";
import { Fraction, canonical, displayFixed, parsePoints, remainingOf, sumProvided } from "../draft/exact";
import type { ContentAction } from "../draft/reducer";
import { Dialog } from "./Dialog";
import { SupportChip } from "./common";
import styles from "./Allocator.module.css";

const STEP = 5;

type Picker =
  | { kind: "reduce"; amount: string }
  | { kind: "transfer"; to: string; amount: string; over: boolean }
  | null;

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

function definitionOf(d: DimensionDefinition | MaterialLeafDefinition) {
  if ("reference_improvement" in d) {
    return { definition: d.definition, reference: d.reference_improvement, note: d.support !== "supported" ? d.support_note : null };
  }
  return { definition: d.definition, reference: null, note: d.note };
}

export function Allocator({ group, title, help }: { group: AllocationGroup; title: string; help: string }) {
  const { caps, state, dispatch } = useDraft();
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
  const eqDisplay = displayFixed(new Fraction(eqExact), dispDec);

  const [equalDialog, setEqualDialog] = useState(false);
  const [pendingManual, setPendingManual] = useState<ContentAction | null>(null);
  const [picker, setPicker] = useState<Picker>(null);

  const counted = isEqual
    ? Object.fromEntries(keys.map((k) => [k, { state: "provided", value_text: eqExact }]))
    : entries;
  const totals = sumProvided(counted, keys, maxDec);
  const rem = remainingOf(totals.total);
  const amount = friendly(rem.amount, dispDec);
  const statusText = rem.kind === "full" ? T.full : rem.kind === "over" ? T.over(amount.text) : T.remaining(amount.text);

  /** 均分状态下的任何修改，先问要不要改成手动分配。 */
  const act = (a: ContentAction) => {
    if (isEqual) setPendingManual(a);
    else dispatch(a);
  };

  const valueOf = (k: string): Fraction | null => {
    const e = counted[k] as PointEntry | undefined;
    if (!e || e.state !== "provided" || e.value_text == null) return null;
    const p = parsePoints(e.value_text, maxDec);
    return p.ok ? p.value : null;
  };

  const plus = (k: string) => {
    if (isEqual) {
      setPendingManual({ type: "points/step", group, key: k, delta: STEP, maxDecimals: maxDec });
      return;
    }
    if (rem.kind === "remaining") {
      // 还没分满：直接加 5 分。加完超过 100 的部分照常显示“多出 x 分”，由用户决定从哪一项减（不自动扣）。
      dispatch({ type: "points/step", group, key: k, delta: STEP, maxDecimals: maxDec });
    } else {
      // 已经分满或已经超出：先问从哪一项挪 5 分过来，合计不变。
      setPicker({ kind: "transfer", to: k, amount: String(STEP), over: rem.kind === "over" });
    }
  };

  const candidates = (exclude: string | null) =>
    keys.filter((k) => k !== exclude && (valueOf(k)?.compare(0) ?? 0) > 0);

  return (
    <div className={styles.wrap}>
      <h2>{title}</h2>
      <p className="muted">{help}</p>

      <div className={`${styles.status} ${rem.kind === "over" ? styles.over : rem.kind === "full" ? styles.full : ""}`}>
        <p className={styles.statusText} role="status" aria-live="polite" id={`${uid}-status`}>
          {statusText}
          {amount.exact ? <span className="small muted">（{T.exactValue} {amount.exact}）</span> : null}
        </p>
        {rem.kind === "over" && !isEqual ? (
          <button type="button" onClick={() => setPicker({ kind: "reduce", amount: canonical(rem.amount) })}>
            {T.reduceFrom(amount.text)}
          </button>
        ) : null}
        {totals.invalid.length ? <span className="small">{T.notCounted(totals.invalid.length)}</span> : null}
      </div>

      <div className={styles.toolbar}>
        <button type="button" onClick={() => setEqualDialog(true)} disabled={isEqual}>
          {T.equalSplit}
        </button>
        <button type="button" onClick={() => dispatch({ type: "history/undo" })} disabled={!state.past.length}>
          {T.undo}
        </button>
        <button type="button" onClick={() => dispatch({ type: "history/redo" })} disabled={!state.future.length}>
          {T.redo}
        </button>
      </div>
      {isEqual ? <p className="note">{T.equalSplitOn(eqDisplay, eqExact)}</p> : null}
      <p className="small muted">{T.stepHint}</p>

      <ol className={styles.rows}>
        {defs.map((d) => {
          const k = d.key;
          const e = entries[k];
          const inputId = `${uid}-${k}`;
          const descId = `${uid}-${k}-desc`;
          const errId = `${uid}-${k}-err`;
          const info = definitionOf(d);
          const shown = isEqual ? eqDisplay : e?.state === "provided" ? (e.value_text ?? "") : "";
          const parsed = !isEqual && e?.state === "provided" && e.value_text != null ? parsePoints(e.value_text, maxDec) : null;
          const stateSel = !isEqual && (e?.state === "indifferent" || e?.state === "unsure") ? e.state : "";
          const describedBy = [descId, parsed && !parsed.ok ? errId : null, stateSel ? `${errId}-state` : null]
            .filter(Boolean)
            .join(" ");
          return (
            <li key={k} className={styles.row} data-key={k}>
              <div className={styles.rowHead}>
                <label htmlFor={inputId} className={styles.rowLabel}>
                  {labels[k]}
                </label>
                {d.support !== "supported" ? <SupportChip support={d.support} /> : null}
                {!isEqual && !e ? <span className="chip">{T.stateUnanswered}</span> : null}
              </div>
              <details className={styles.what}>
                <summary>{T.whatIsThis}</summary>
                <div id={descId}>
                  <p>{info.definition}</p>
                  {info.reference ? <p className="small">{info.reference}</p> : null}
                  {info.note ? <p className="small muted">{info.note}</p> : null}
                </div>
              </details>
              <div className={styles.controls}>
                <button
                  type="button"
                  className={styles.stepButton}
                  aria-label={`${labels[k]}减 ${STEP} 分`}
                  onClick={() => act({ type: "points/step", group, key: k, delta: -STEP, maxDecimals: maxDec })}
                >
                  −
                </button>
                <input
                  id={inputId}
                  name={`${group}.${k}`}
                  className={styles.input}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={shown}
                  aria-describedby={describedBy}
                  aria-invalid={parsed && !parsed.ok ? true : undefined}
                  onChange={(ev) => act({ type: "points/text", group, key: k, text: ev.target.value })}
                  onBlur={() => dispatch({ type: "history/break" })}
                />
                <button type="button" className={styles.stepButton} aria-label={`${labels[k]}加 ${STEP} 分`} onClick={() => plus(k)}>
                  ＋
                </button>
                <select
                  aria-label={`${labels[k]}：${T.unsureMenu}`}
                  value={stateSel}
                  onChange={(ev) => {
                    const v = ev.target.value;
                    act({
                      type: "points/state",
                      group,
                      key: k,
                      state: v === "indifferent" || v === "unsure" ? v : "unanswered",
                    });
                  }}
                >
                  <option value="">{T.unsureMenu}</option>
                  <option value="indifferent">{T.stateIndifferent}</option>
                  <option value="unsure">{T.stateUnsure}</option>
                </select>
              </div>
              {parsed && !parsed.ok ? (
                <p id={errId} className="small" style={{ color: "var(--error-line)" }}>
                  {T.pointsInvalid}
                  {parsed.hint}
                </p>
              ) : null}
              {stateSel ? (
                <p id={`${errId}-state`} className="small">
                  {stateSel === "indifferent" ? T.stateIndifferent : T.stateUnsure}：{T.stateUnsetNote}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>

      <Dialog open={equalDialog} onClose={() => setEqualDialog(false)} title={T.equalSplit}>
        <p>{T.equalSplitConfirm(eqDisplay, eqExact)}</p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              dispatch({ type: "points/equalSplit", group });
              setEqualDialog(false);
            }}
          >
            {T.equalSplit}
          </button>
          <button type="button" onClick={() => setEqualDialog(false)}>
            {T.cancel}
          </button>
        </div>
      </Dialog>

      <Dialog open={pendingManual !== null} onClose={() => setPendingManual(null)} title={T.toManual}>
        <p>{T.toManualConfirm(eqDisplay, eqExact)}</p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              dispatch({ type: "points/toManual", group, keys, value: eqExact });
              if (pendingManual) dispatch(pendingManual);
              setPendingManual(null);
            }}
          >
            {T.toManual}
          </button>
          <button type="button" onClick={() => setPendingManual(null)}>
            {T.cancel}
          </button>
        </div>
      </Dialog>

      <Dialog
        open={picker !== null}
        onClose={() => setPicker(null)}
        title={
          picker?.kind === "transfer" ? T.moveFrom(picker.amount, labels[picker.to] ?? "", picker.over) : T.reduceFrom(amount.text)
        }
      >
        <p className="small">{T.moveNote}</p>
        {picker && candidates(picker.kind === "transfer" ? picker.to : null).length === 0 ? (
          <p>没有可以挪分的项（其他项都还没有分数）。可以直接改数字。</p>
        ) : null}
        <ul className={styles.pickList}>
          {picker
            ? candidates(picker.kind === "transfer" ? picker.to : null).map((k) => {
                const v = valueOf(k);
                return (
                  <li key={k}>
                    <button
                      type="button"
                      onClick={() => {
                        if (picker.kind === "reduce") {
                          dispatch({ type: "points/reduce", group, key: k, amount: picker.amount, maxDecimals: maxDec });
                        } else {
                          dispatch({ type: "points/transfer", group, from: k, to: picker.to, amount: picker.amount, maxDecimals: maxDec });
                        }
                        setPicker(null);
                      }}
                    >
                      {labels[k]}（{v ? friendly(v, dispDec).text : "0"} 分）
                    </button>
                  </li>
                );
              })
            : null}
        </ul>
        <button type="button" onClick={() => setPicker(null)}>
          {T.cancel}
        </button>
      </Dialog>
    </div>
  );
}
