// 第 1 步：范围与生活事实（城市、职业、年龄、开局现金；资格先放在折叠区）。
import { useQuery } from "@tanstack/react-query";
import { useApiContext } from "../api/context";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { useFlow } from "../flow/FlowContext";
import { PageTitle, StepNav, Stepper } from "../components/Layout";

// 与 E3 的金额写法规则一致（src/product/exact.py）：不分组的非负十进制，或严格每三位一组的千分位。只用来提前提醒，正式检查在 E3。
const MONEY_PLAIN = /^(0|[1-9][0-9]*)(\.[0-9]+)?$/;
const MONEY_GROUPED = /^[1-9][0-9]{0,2}(,[0-9]{3})+(\.[0-9]+)?$/;

export function FillFacts() {
  const { caps, state, dispatch, clearDraft, loadSampleDraft } = useDraft();
  const { api, scenario, setScenario } = useApiContext();
  const { resetFlow } = useFlow();
  const d = state.draft;
  const sampleMode = state.pristineSample && !!scenario.review && scenario.draft === state.sampleId;
  const review = useQuery({
    queryKey: ["review", d.draft_id, d.revision, scenario.id],
    queryFn: () => api.review(d),
    enabled: sampleMode,
  });
  const basis = caps.money_bases[0];
  const cashText = d.cash.amount_text ?? "";
  const cashLooksWrong = cashText !== "" && !MONEY_PLAIN.test(cashText.trim()) && !MONEY_GROUPED.test(cashText.trim());
  const ageOk = state.ageText.trim() === "" || d.age.state === "provided";

  return (
    <div>
      <Stepper current={1} />
      <PageTitle>1 · {T.steps[0]}</PageTitle>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.scopeTitle}</h2>
        <ul>
          {caps.start_scenario.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
        <details>
          <summary>本版不支持什么、还有哪些限制</summary>
          <ul>
            {(caps.unsupported ?? []).map((s) => (
              <li key={s}>{s}</li>
            ))}
            {(caps.limits ?? []).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </details>
        <div className="row">
          <button
            type="button"
            onClick={() => {
              clearDraft();
              resetFlow();
            }}
          >
            {T.startBlank}
          </button>
          <button
            type="button"
            onClick={() => {
              setScenario("normal");
              resetFlow();
              loadSampleDraft("02");
            }}
          >
            {T.loadSample}
          </button>
        </div>
        {state.pristineSample ? <p className="note">{T.sampleLoaded}</p> : null}
      </section>

      <fieldset data-field="cities">
        <legend>{T.cities}</legend>
        {caps.cities
          .filter((c) => c.selectable)
          .map((c) => (
            // 整行都能点：复选框 22 像素，但所在的这一行至少 44 像素高
            <label key={c.city_ref} className="choice top">
              <input
                type="checkbox"
                name={`cities.${c.city_ref}`}
                aria-describedby={`city-note-${c.city_ref}`}
                checked={d.cities.city_refs?.includes(c.city_ref) ?? false}
                onChange={(e) => dispatch({ type: "cities/toggle", cityRef: c.city_ref, selected: e.target.checked })}
              />
              <span>
                <strong>{c.label}</strong>{" "}
                {c.data_status === "synthetic_fixture" ? <span className="chip warn">{T.sampleCity}</span> : null}
                <span id={`city-note-${c.city_ref}`} className="small muted" style={{ display: "block" }}>
                  {c.note}
                </span>
              </span>
            </label>
          ))}
      </fieldset>

      <fieldset>
        <legend>{T.occupation}</legend>
        <label htmlFor="occupation-text" className="visually-hidden">
          {T.occupation}
        </label>
        <p className="small muted" id="occupation-help">
          {T.occupationHelp}
        </p>
        <textarea
          id="occupation-text"
          name="occupation.raw_text"
          aria-describedby="occupation-help"
          value={d.occupation.raw_text ?? ""}
          maxLength={300}
          onChange={(e) => dispatch({ type: "occupation/text", text: e.target.value })}
          onBlur={() => dispatch({ type: "history/break" })}
          style={{ minHeight: "4em" }}
        />
        {sampleMode && review.data ? (
          <div>
            <h3>{T.occupationCandidates}</h3>
            <p>{review.data.occupation.message_zh}</p>
            <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
              {(review.data.occupation.candidates ?? []).map((c) => (
                <li key={c.candidate_id} className="card">
                  <strong>{c.label}</strong>
                  {d.occupation.selected_candidate === c.candidate_id ? <span className="chip accent"> 已选 </span> : null}
                  <ul className="small">
                    {c.duties.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="small note">{T.occupationFree}</p>
        )}
      </fieldset>

      <fieldset>
        <legend>{T.age}</legend>
        <label htmlFor="age" className="visually-hidden">
          {T.age}
        </label>
        <input
          id="age"
          name="age"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={state.ageText}
          aria-describedby="age-help age-err"
          aria-invalid={!ageOk || undefined}
          onChange={(e) => dispatch({ type: "age/text", text: e.target.value })}
          onBlur={() => dispatch({ type: "history/break" })}
          style={{ width: "6em" }}
        />
        <p className="small muted" id="age-help">
          {T.ageHelp(caps.age_min, caps.age_max)}
        </p>
        {!ageOk ? (
          <p className="small" id="age-err" style={{ color: "var(--error-line)" }}>
            {T.ageNotInt}
          </p>
        ) : null}
      </fieldset>

      <fieldset>
        <legend>{T.cash}</legend>
        <p className="small" id="cash-meaning">
          {T.cashMeaning}
        </p>
        <label htmlFor="cash">金额</label>{" "}
        <input
          id="cash"
          name="cash.amount_text"
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={cashText}
          aria-describedby="cash-meaning cash-rule cash-err"
          onChange={(e) =>
            dispatch({ type: "cash/text", text: e.target.value, currency: basis?.currency ?? "", pricePeriod: basis?.price_period ?? "" })
          }
          onBlur={() => dispatch({ type: "history/break" })}
          style={{ width: "10em" }}
        />
        <p className="small" id="cash-rule">
          {caps.money_input_rule_zh}
        </p>
        {cashLooksWrong ? (
          <p className="small warn" id="cash-err">
            {T.cashLooksWrong}
          </p>
        ) : null}
        {basis ? (
          <p className="small">
            {T.cashBasis}：{basis.price_period} 年 {basis.currency}（{basis.note}）。{caps.money_bases.length === 1 ? T.cashBasisOnly : null}
          </p>
        ) : null}
      </fieldset>

      {(caps.abilities ?? []).length ? (
        <details className="card" data-field="abilities">
          <summary>{T.abilities}</summary>
          <p className="small muted">{T.abilitiesNote}</p>
          {(caps.abilities ?? []).map((a) => {
            const cur = d.abilities?.find((x) => x.ability_id === a.ability_id)?.state ?? "";
            return (
              <fieldset key={a.ability_id}>
                <legend>{a.label}</legend>
                <p className="small muted">{a.ask_when}</p>
                {(
                  [
                    ["provided", T.abilityHeld],
                    ["unsure", T.abilityUnsure],
                    ["", T.abilityNone],
                  ] as const
                ).map(([v, label]) => (
                  <label key={v || "none"} className="choice">
                    <input
                      type="radio"
                      name={`abilities.${a.ability_id}`}
                      checked={cur === v}
                      onChange={() => dispatch({ type: "ability/set", abilityId: a.ability_id, state: v === "" ? null : v })}
                    />
                    {label}
                  </label>
                ))}
              </fieldset>
            );
          })}
        </details>
      ) : null}

      <StepNav next="/fill/2" />
    </div>
  );
}
