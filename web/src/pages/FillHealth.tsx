// 第 3 步：健康风险应对（独立问卷，不占 100 分）与可选补充（文字、硬要求、品牌菜系等）。
import { useState } from "react";
import { PageTitle, StepNav, Stepper } from "../components/Layout";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import type { HealthChoice } from "../draft/reducer";

export function FillHealth() {
  const { caps, state, dispatch } = useDraft();
  const d = state.draft;
  const hq = caps.health_questionnaire;
  const freeText = d.free_text.text ?? "";
  const freeLen = [...freeText].length;
  const [newKind, setNewKind] = useState((caps.attribute_kinds ?? [])[0]?.kind ?? "");
  const [newNeed, setNewNeed] = useState(caps.material_leaves[0]?.key ?? "");
  const [newText, setNewText] = useState("");
  const [newHard, setNewHard] = useState(false);

  return (
    <div>
      <Stepper current={3} />
      <PageTitle>3 · {T.steps[2]}</PageTitle>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.health}</h2>
        <p className="note">{T.healthNote}</p>
        <p>{hq.common_prompt}</p>
        {hq.questions.map((q, qi) => {
          const item = d.health.items?.find((i) => i.question_id === q.question_id);
          const current = item ? (item.choice ?? "skip") : "";
          return (
            <fieldset key={q.question_id} data-field="health">
              <legend>
                {qi + 1}. {q.text}
              </legend>
              {hq.options.map((o) => (
                <label key={o.option_id} className="choice">
                  <input
                    type="radio"
                    name={`health.items.${q.question_id}`}
                    checked={current === o.option_id}
                    onChange={() => dispatch({ type: "health/choice", questionId: q.question_id, choice: o.option_id as HealthChoice })}
                  />
                  {o.label}
                </label>
              ))}
              <label className="choice">
                <input
                  type="radio"
                  name={`health.items.${q.question_id}`}
                  checked={current === "skip"}
                  onChange={() => dispatch({ type: "health/choice", questionId: q.question_id, choice: null })}
                />
                {T.healthSkip}
              </label>
            </fieldset>
          );
        })}
        <p className="small muted">{hq.unanswered_rule}</p>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>
          <label htmlFor="free-text">{T.freeText}</label>
        </h2>
        <p className="small" id="free-text-note">
          {T.freeTextNote}
        </p>
        <textarea
          id="free-text"
          name="free_text"
          value={freeText}
          aria-describedby="free-text-note free-text-count"
          aria-invalid={freeLen > caps.free_text_max_chars || undefined}
          onChange={(e) => dispatch({ type: "freeText/text", text: e.target.value })}
          onBlur={() => dispatch({ type: "history/break" })}
        />
        <p id="free-text-count" className="small" style={freeLen > caps.free_text_max_chars ? { color: "var(--error-line)" } : undefined}>
          {T.freeTextCount(freeLen, caps.free_text_max_chars)}
          {freeLen > caps.free_text_max_chars ? `　${T.freeTextOver(caps.free_text_max_chars)}` : null}
        </p>
      </section>

      {(caps.hard_requirement_options ?? []).length ? (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>{T.hardRequirements}</h2>
          <p className="small">{T.hardRequirementsNote}</p>
          {(caps.hard_requirement_options ?? []).map((o) => {
            const cur = d.hard_requirements?.find((h) => h.option_id === o.option_id);
            const inputId = `hard-${o.option_id}`;
            return (
              <fieldset key={o.option_id} data-field="hard_requirements">
                <legend>{o.label}</legend>
                <label className="choice">
                  <input
                    type="checkbox"
                    checked={!!cur}
                    onChange={(e) => dispatch({ type: "hard/set", optionId: o.option_id, enabled: e.target.checked, value: null })}
                  />
                  {T.hardOn}
                </label>
                {cur ? (
                  <p>
                    <label htmlFor={inputId}>
                      {o.parameter === "max_commute_roundtrip_minutes" ? "往返不超过" : "数值"}（{o.unit}，{o.minimum}–{o.maximum}，步长 {o.step}）
                    </label>{" "}
                    <input
                      id={inputId}
                      name={`hard_requirements.${o.option_id}`}
                      type="number"
                      min={o.minimum}
                      max={o.maximum}
                      step={o.step}
                      value={cur.parameter_value ?? ""}
                      onChange={(e) =>
                        dispatch({
                          type: "hard/set",
                          optionId: o.option_id,
                          enabled: true,
                          value: e.target.value === "" || !Number.isInteger(Number(e.target.value)) ? null : Number(e.target.value),
                        })
                      }
                      onBlur={() => dispatch({ type: "history/break" })}
                      style={{ width: "7em" }}
                    />
                  </p>
                ) : null}
                <p className="small muted">{o.verification}</p>
              </fieldset>
            );
          })}
        </section>
      ) : null}

      {(caps.attribute_kinds ?? []).length ? (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>{T.attributes}</h2>
          <p className="small">{T.attributesNote}</p>
          {(d.attribute_preferences ?? []).length ? (
            <ul>
              {(d.attribute_preferences ?? []).map((p) => {
                const kind = (caps.attribute_kinds ?? []).find((k) => k.kind === p.kind);
                const need = caps.material_leaves.find((m) => m.key === p.need);
                return (
                  <li key={p.preference_id}>
                    {kind?.label ?? p.kind}（{need?.label ?? p.need}）：“{p.raw_text}”
                    {p.as_hard_requirement ? <span className="chip warn">{T.attrHard}</span> : null}{" "}
                    <span className="small muted">{kind?.note}</span>{" "}
                    <button type="button" className="link" onClick={() => dispatch({ type: "attr/remove", preferenceId: p.preference_id })}>
                      {T.attrRemove}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <div className="stack">
            <p>
              <label htmlFor="attr-kind">{T.attrKind}</label>{" "}
              <select id="attr-kind" value={newKind} onChange={(e) => setNewKind(e.target.value)}>
                {(caps.attribute_kinds ?? []).map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </select>
            </p>
            <p>
              <label htmlFor="attr-need">{T.attrNeed}</label>{" "}
              <select id="attr-need" value={newNeed} onChange={(e) => setNewNeed(e.target.value)}>
                {caps.material_leaves.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </p>
            <p>
              <label htmlFor="attr-text">{T.attrText}</label>
              <input id="attr-text" type="text" maxLength={300} value={newText} onChange={(e) => setNewText(e.target.value)} style={{ width: "100%" }} />
            </p>
            <label className="choice">
              <input type="checkbox" checked={newHard} onChange={(e) => setNewHard(e.target.checked)} />
              {T.attrHard}
            </label>
            {newHard ? <p className="small warn">{T.attrHardNote}</p> : null}
            <button
              type="button"
              disabled={!newText.trim()}
              onClick={() => {
                dispatch({ type: "attr/add", kind: newKind, need: newNeed, rawText: newText, asHardRequirement: newHard });
                setNewText("");
                setNewHard(false);
              }}
            >
              {T.attrAdd}
            </button>
          </div>
        </section>
      ) : null}

      <StepNav prev="/fill/2" next="/fill/4" />
    </div>
  );
}
