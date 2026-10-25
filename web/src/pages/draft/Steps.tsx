// 第 1–4 步：地区与城市、生活事实、偏好与取舍、健康兴趣与补充。题目、选项、标签与说明都是 E3 能力清单的原话；
// 候选（职业、兴趣、口味）来自服务对已保存草稿的审阅，用户主动选中才算数，不预勾。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InterestReview, ProductCapabilities } from "../../contract/types";
import { Allocator } from "../../components/Allocator";
import { CityList } from "../../components/CityList";
import { useGeography } from "../../api/queries";
import { CityMapSlot } from "../../components/CityMapSlot";
import { MapStage } from "../../components/MapStage";
import { Section } from "../../components/common";
import { PageTitle, StepNav } from "../../components/Layout";
import { useDraftEditor } from "../../draft/DraftEditor";
import { parsePoints } from "../../draft/exact";
import type { HealthChoice, LeisureState, TasteState } from "../../draft/reducer";
import { useDraftReview } from "../../draft/review";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";
import { ModelLanguageChoice } from "../StartPage";

// 与 E3 的金额写法规则一致（src/product/exact.py）：不分组的非负十进制，或严格每三位一组的千分位。只用来提前提醒，正式检查在 E3。
const MONEY_PLAIN = /^(0|[1-9][0-9]*)(\.[0-9]+)?$/;
const MONEY_GROUPED = /^[1-9][0-9]{0,2}(,[0-9]{3})+(\.[0-9]+)?$/;

export function PlaceStep() {
  const { t } = useTranslation();
  const { caps, state, dispatch, draftId } = useDraftEditor();
  const [country, setCountry] = useState<string | null>(null);
  const selected = state.draft.cities.city_refs ?? [];
  // 城市在地图上的位置：E3 的地理显示清单，按当前资料包的指纹读（读不到就不画点，列表照常）
  const geo = useGeography(caps.meta.data_pack.content_fingerprint);
  // 平面地图默认放大到唯一能选的国家（只有一个国家时世界图大半是空白）；用户随时能回到世界图
  const supportedCountries = useMemo(() => [...new Set(caps.cities.filter((c) => c.selectable).map((c) => c.country))], [caps.cities]);
  const [zoom, setZoom] = useState<string | null>(() => (supportedCountries.length === 1 ? (supportedCountries[0] ?? null) : null));
  return (
    <div>
      <MapStage
        panelLabel={t("place.title")}
        head={
          <>
            <PageTitle>{t("place.title")}</PageTitle>
            <p>{t("place.help")}</p>
            <BizNotice />
          </>
        }
        map={
          <CityMapSlot
            purpose="input"
            fill
            cities={caps.cities}
            geography={geo.data ?? (geo.isError ? null : undefined)}
            selected={selected}
            onToggle={(ref, on) => dispatch({ type: "cities/toggle", cityRef: ref, selected: on })}
            onCountry={setCountry}
            country={country}
            focus={zoom}
            onFocus={setZoom}
          />
        }
      >
        <CityList
          cities={caps.cities}
          selected={selected}
          onToggle={(ref, on) => dispatch({ type: "cities/toggle", cityRef: ref, selected: on })}
          onSetAll={(refs) => dispatch({ type: "cities/set", cityRefs: refs })}
          country={country}
          onCountry={setCountry}
        />
        {selected.length === 0 ? <p className="box warn">{t("place.needOne")}</p> : null}
        <StepNav draftId={draftId} next="facts" />
      </MapStage>
    </div>
  );
}

function OccupationCandidates() {
  const { t } = useTranslation();
  const { state, dispatch, save } = useDraftEditor();
  const d = state.draft;
  const { review, current, query } = useDraftReview(!!d.occupation.raw_text?.trim());
  if (!d.occupation.raw_text?.trim()) return null;
  if (!review) {
    return <p className="small muted">{save.kind === "saved" && query.isFetching ? t("facts.candidatesChecking") : t("facts.candidatesWait")}</p>;
  }
  const occ = review.occupation;
  return (
    <div className="stack" aria-busy={!current || undefined}>
      <h3>{t("facts.candidatesTitle")}</h3>
      {!current ? <p className="small muted">{t("facts.candidatesChecking")}</p> : null}
      <p>
        <Zh>{occ.message_zh}</Zh>
      </p>
      <ul className="plain stack">
        {(occ.candidates ?? []).map((c) => {
          const picked = d.occupation.selected_candidate === c.candidate_id;
          return (
            <li key={c.candidate_id} className="card" data-candidate={c.candidate_id}>
              <strong>
                <Zh>{c.label}</Zh>
              </strong>{" "}
              {picked ? <span className="chip accent">{t("facts.picked")}</span> : null}
              <ul className="small">
                {c.duties.map((x) => (
                  <Zh as="li" key={x}>
                    {x}
                  </Zh>
                ))}
              </ul>
              <button
                type="button"
                className={picked ? undefined : "primary"}
                aria-pressed={picked}
                onClick={() => dispatch({ type: "occupation/select", candidateId: picked ? null : c.candidate_id })}
              >
                {picked ? t("facts.unpick") : t("facts.pick")}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function FactsStep() {
  const { t } = useTranslation();
  const { caps, state, dispatch, draftId } = useDraftEditor();
  const d = state.draft;
  const basis = caps.money_bases[0];
  const cashText = d.cash.amount_text ?? "";
  const cashLooksWrong = cashText !== "" && !MONEY_PLAIN.test(cashText.trim()) && !MONEY_GROUPED.test(cashText.trim());
  const ageOk = state.ageText.trim() === "" || d.age.state === "provided";
  const lang = d.occupation.language ?? "zh";
  return (
    <div>
      <PageTitle>{t("steps.facts")}</PageTitle>
      <BizNotice />
      <ModelLanguageChoice
        value={lang}
        onChange={(l) => dispatch({ type: "language/set", language: l })}
        languages={caps.occupation.languages}
        name="occupation.language"
      />

      <fieldset data-field="occupation">
        <legend>{t("facts.occupation")}</legend>
        <p className="small muted" id="occupation-help">
          {t("facts.occupationHelp")} {t("facts.occupationLang", { lang: lang === "en" ? t("facts.langEn") : t("facts.langZh") })}
        </p>
        <label htmlFor="occupation-text" className="visually-hidden">
          {t("facts.occupation")}
        </label>
        <textarea
          id="occupation-text"
          name="occupation.raw_text"
          lang={lang === "en" ? "en" : "zh-CN"}
          aria-describedby="occupation-help"
          value={d.occupation.raw_text ?? ""}
          maxLength={300}
          onChange={(e) => dispatch({ type: "occupation/text", text: e.target.value })}
          onBlur={() => dispatch({ type: "history/break" })}
          style={{ minHeight: "4em" }}
        />
        <OccupationCandidates />
      </fieldset>

      <fieldset data-field="age">
        <legend>
          <label htmlFor="age">{t("facts.age")}</label>
        </legend>
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
          {t("facts.ageHelp", { min: caps.age_min, max: caps.age_max })}
        </p>
        {!ageOk ? (
          <p className="small" id="age-err" style={{ color: "var(--error-line)" }}>
            {t("facts.ageNotInt")}
          </p>
        ) : null}
      </fieldset>

      <fieldset data-field="cash">
        <legend>{t("facts.cash")}</legend>
        <p className="small" id="cash-meaning">
          {t("facts.cashMeaning")}
        </p>
        <label htmlFor="cash">{t("facts.cashAmount")}</label>{" "}
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
        {basis ? <span> {basis.currency}</span> : null}
        <p className="small" id="cash-rule">
          <Zh>{caps.money_input_rule_zh}</Zh>
        </p>
        {cashLooksWrong ? (
          <p className="small box warn" id="cash-err">
            {t("facts.cashLooksWrong")}
          </p>
        ) : null}
        {basis ? (
          <p className="small">
            {t("facts.cashBasis", { period: basis.price_period, currency: basis.currency })}（<Zh>{basis.note}</Zh>）。
            {caps.money_bases.length === 1 ? t("facts.cashBasisOnly") : null}
          </p>
        ) : null}
      </fieldset>

      {(caps.abilities ?? []).length ? (
        <fieldset data-field="abilities">
          <legend>{t("facts.abilities")}</legend>
          <p className="small muted">{t("facts.abilitiesNote")}</p>
          {(caps.abilities ?? []).map((a) => {
            const cur = d.abilities?.find((x) => x.ability_id === a.ability_id)?.state ?? "";
            return (
              <fieldset key={a.ability_id}>
                <legend>
                  <Zh>{a.label}</Zh>
                </legend>
                <p className="small muted">
                  <Zh>{a.ask_when}</Zh>
                </p>
                {(
                  [
                    ["provided", t("facts.abilityHeld")],
                    ["unsure", t("facts.abilityUnsure")],
                    ["", t("facts.abilityNone")],
                  ] as const
                ).map(([v, label]) => (
                  <label key={v || "none"} className="choice">
                    <input
                      type="radio"
                      name={`abilities.${a.ability_id}`}
                      value={v || "none"}
                      checked={cur === v}
                      onChange={() => dispatch({ type: "ability/set", abilityId: a.ability_id, state: v === "" ? null : v })}
                    />
                    {label}
                  </label>
                ))}
              </fieldset>
            );
          })}
        </fieldset>
      ) : null}

      <Section title={t("facts.startTitle")}>
        <ZhList items={caps.start_scenario} />
      </Section>

      <StepNav draftId={draftId} prev="place" next="priorities" />
    </div>
  );
}

export function PrioritiesStep() {
  const { t } = useTranslation();
  const { caps, state, draftId } = useDraftEditor();
  const six = state.draft.six_dimensions;
  const parent = six.entries?.material;
  let materialStatus: "need_parent" | "zero" | "open";
  if (six.mode === "equal_split") materialStatus = "open";
  else if (!parent || parent.state !== "provided" || parent.value_text == null) materialStatus = "need_parent";
  else {
    const p = parsePoints(parent.value_text, caps.points.manual_max_decimals);
    materialStatus = !p.ok ? "need_parent" : p.value.n === 0n ? "zero" : "open";
  }
  return (
    <div>
      <PageTitle>{t("steps.priorities")}</PageTitle>
      <BizNotice />
      <Allocator group="six_dimensions" title={t("priorities.sixTitle")} help={t("priorities.sixHelp")} />
      <section aria-label={t("priorities.materialTitle")}>
        {materialStatus === "open" ? (
          <Allocator group="material" title={t("priorities.materialTitle")} help={t("priorities.materialHelp")} />
        ) : (
          <>
            <h2>{t("priorities.materialTitle")}</h2>
            <p className="box note">{materialStatus === "zero" ? t("priorities.materialNotApplicable") : t("priorities.materialNeedParent")}</p>
          </>
        )}
      </section>
      <StepNav draftId={draftId} prev="facts" next="more" />
    </div>
  );
}

const LEISURE_STATES: LeisureState[] = ["unanswered", "unsure", "no_fixed_hobby", "generic", "described"];
const TASTE_STATES: TasteState[] = ["unanswered", "unsure", "no_preference", "described"];

/** E3 给的作答说明按状态顺序排列，每句冒号前是名称、冒号后是含义；长度对不上就不硬配，原样列出。 */
function stateTexts(states: readonly string[], texts: readonly string[]): { label: string; meaning: string }[] | null {
  if (texts.length !== states.length) return null;
  return texts.map((s) => {
    const i = s.indexOf("：");
    return i > 0 ? { label: s.slice(0, i), meaning: s.slice(i + 1) } : { label: s, meaning: "" };
  });
}

function InterestSection({
  field,
  title,
  states,
  texts,
  vocabulary,
  placeholder,
  review,
}: {
  field: "leisure_interest" | "taste";
  title: string;
  states: readonly (LeisureState | TasteState)[];
  texts: readonly string[];
  vocabulary: ProductCapabilities["interests"]["cuisines"];
  placeholder: string;
  review: InterestReview | null;
}) {
  const { t } = useTranslation();
  const { state, dispatch } = useDraftEditor();
  const cur = state.draft[field];
  const curState = cur?.state ?? "unanswered";
  const selected = cur?.selected ?? [];
  const labelOf = (key: string) => vocabulary.find((v) => v.key === key)?.label ?? key;
  const described = stateTexts(states, texts);
  const candidates = review?.candidates ?? [];
  return (
    <Section title={title}>
      <fieldset data-field={field}>
        <legend>{t("more.stateList")}</legend>
        {described ? (
          states.map((s, i) => (
            <label key={s} className="choice top">
              <input
                type="radio"
                name={`${field}.state`}
                value={s}
                checked={curState === s}
                onChange={() => dispatch({ type: "interest/state", field, state: s })}
              />
              <span>
                <Zh>{described[i]?.label}</Zh>
                {described[i]?.meaning ? (
                  <span className="small muted" style={{ display: "block" }}>
                    <Zh>{described[i]?.meaning}</Zh>
                  </span>
                ) : null}
              </span>
            </label>
          ))
        ) : (
          <>
            <p className="small box warn">{t("more.statesMismatch")}</p>
            <ZhList items={texts} />
          </>
        )}
      </fieldset>
      {curState === "described" ? (
        <div className="stack">
          <label htmlFor={`${field}-text`}>{t("more.describe")}</label>
          <textarea
            id={`${field}-text`}
            name={`${field}.text`}
            placeholder={placeholder}
            value={cur?.text ?? ""}
            maxLength={500}
            onChange={(e) => dispatch({ type: "interest/text", field, text: e.target.value })}
            onBlur={() => dispatch({ type: "history/break" })}
          />
          {review ? (
            <div className="stack">
              <p className="small">
                <Zh>{review.message_zh}</Zh>
              </p>
              {candidates.length ? (
                <fieldset>
                  <legend>{t("more.candidates")}</legend>
                  {candidates.map((k) => (
                    <label key={k} className="choice">
                      <input
                        type="checkbox"
                        name={`${field}.selected.${k}`}
                        checked={selected.includes(k)}
                        onChange={(e) => dispatch({ type: "interest/select", field, key: k, selected: e.target.checked })}
                      />
                      <Zh>{labelOf(k)}</Zh>
                    </label>
                  ))}
                </fieldset>
              ) : null}
              {review.ambiguous?.length ? (
                <div>
                  <p className="small">{t("more.ambiguous")}</p>
                  <ZhList items={review.ambiguous} />
                </div>
              ) : null}
              {review.unrecognized?.length ? (
                <div>
                  <p className="small">{t("more.unrecognized")}</p>
                  <ZhList items={review.unrecognized} />
                </div>
              ) : null}
            </div>
          ) : (
            <p className="small muted">{t("more.reviewPending")}</p>
          )}
          <details>
            <summary>{t("more.fromVocabulary")}</summary>
            {vocabulary.map((v) => (
              <label key={v.key} className="choice">
                <input
                  type="checkbox"
                  checked={selected.includes(v.key)}
                  onChange={(e) => dispatch({ type: "interest/select", field, key: v.key, selected: e.target.checked })}
                />
                <Zh>{v.label}</Zh>
              </label>
            ))}
          </details>
          {!selected.length ? <p className="small muted">{t("more.selectedNone")}</p> : null}
        </div>
      ) : null}
    </Section>
  );
}

export function MoreStep() {
  const { t } = useTranslation();
  const { caps, state, dispatch, draftId } = useDraftEditor();
  const d = state.draft;
  const hq = caps.health_questionnaire;
  const freeText = d.free_text.text ?? "";
  const freeLen = [...freeText].length;
  const [newKind, setNewKind] = useState((caps.attribute_kinds ?? [])[0]?.kind ?? "");
  const [newNeed, setNewNeed] = useState(caps.material_leaves[0]?.key ?? "");
  const [newText, setNewText] = useState("");
  const [newHard, setNewHard] = useState(false);
  const interestText = (d.leisure_interest?.state === "described" && !!d.leisure_interest.text) || (d.taste?.state === "described" && !!d.taste.text);
  const { review } = useDraftReview(interestText);

  return (
    <div>
      <PageTitle>{t("steps.more")}</PageTitle>
      <BizNotice />

      <Section title={t("more.healthTitle")}>
        <p className="box note">{t("more.healthNote")}</p>
        <p>
          <Zh>{hq.common_prompt}</Zh>
        </p>
        {hq.questions.map((q, qi) => {
          const item = d.health.items?.find((i) => i.question_id === q.question_id);
          // 没选 = 未答（choice 为空），与“暂不确定”分开保存；不预选任何一项
          const current = item?.choice ?? null;
          return (
            <fieldset key={q.question_id} data-field="health">
              <legend>
                {qi + 1}. <Zh>{q.text}</Zh>
              </legend>
              {hq.options.map((o) => (
                <label key={o.option_id} className="choice">
                  <input
                    type="radio"
                    name={`health.items.${q.question_id}`}
                    value={o.option_id}
                    checked={current === o.option_id}
                    onChange={() => dispatch({ type: "health/choice", questionId: q.question_id, choice: o.option_id as HealthChoice })}
                  />
                  <Zh>{o.label}</Zh>
                </label>
              ))}
              {current ? (
                <button type="button" className="link" onClick={() => dispatch({ type: "health/choice", questionId: q.question_id, choice: null })}>
                  {t("more.healthSkip")}
                </button>
              ) : (
                <p className="small muted">{t("states.unanswered")}</p>
              )}
            </fieldset>
          );
        })}
        <p className="small muted">
          <Zh>{hq.unanswered_rule}</Zh>
        </p>
      </Section>

      <InterestSection
        field="leisure_interest"
        title={t("more.leisureTitle")}
        states={LEISURE_STATES}
        texts={caps.interests.leisure_states_zh}
        vocabulary={caps.interests.leisure_categories}
        placeholder={t("more.describePlaceholderLeisure")}
        review={review?.leisure_interest ?? null}
      />
      <InterestSection
        field="taste"
        title={t("more.tasteTitle")}
        states={TASTE_STATES}
        texts={caps.interests.taste_states_zh}
        vocabulary={caps.interests.cuisines}
        placeholder={t("more.describePlaceholderTaste")}
        review={review?.taste ?? null}
      />
      <p className="small muted">
        <Zh>{caps.interests.matching_note_zh}</Zh>
      </p>

      <Section title={<label htmlFor="free-text">{t("more.freeTitle")}</label>}>
        <p className="small" id="free-text-note">
          {t("more.freeNote")}
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
          {t("more.freeCount", { n: freeLen, max: caps.free_text_max_chars })}
          {freeLen > caps.free_text_max_chars ? `　${t("more.freeOver", { max: caps.free_text_max_chars })}` : null}
        </p>
      </Section>

      {(caps.hard_requirement_options ?? []).length ? (
        <Section title={t("more.hardTitle")}>
          <p className="small">{t("more.hardNote")}</p>
          {(caps.hard_requirement_options ?? []).map((o) => {
            const cur = d.hard_requirements?.find((h) => h.option_id === o.option_id);
            const inputId = `hard-${o.option_id}`;
            return (
              <fieldset key={o.option_id} data-field="hard_requirements">
                <legend>
                  <Zh>{o.label}</Zh>
                </legend>
                <label className="choice">
                  <input
                    type="checkbox"
                    checked={!!cur}
                    onChange={(e) => dispatch({ type: "hard/set", optionId: o.option_id, enabled: e.target.checked, value: null })}
                  />
                  {t("more.hardOn")}
                </label>
                {cur ? (
                  <p>
                    <label htmlFor={inputId}>{t("more.hardValue", { unit: o.unit, min: o.minimum, max: o.maximum, step: o.step })}</label>{" "}
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
                <p className="small muted">
                  <Zh>{o.verification}</Zh>
                </p>
              </fieldset>
            );
          })}
        </Section>
      ) : null}

      {(caps.attribute_kinds ?? []).length ? (
        <Section title={t("more.attrTitle")}>
          <p className="small">{t("more.attrNote")}</p>
          {(d.attribute_preferences ?? []).length ? (
            <ul>
              {(d.attribute_preferences ?? []).map((p) => {
                const kind = (caps.attribute_kinds ?? []).find((k) => k.kind === p.kind);
                const need = caps.material_leaves.find((m) => m.key === p.need);
                return (
                  <li key={p.preference_id}>
                    <Zh>{kind?.label ?? p.kind}</Zh>（<Zh>{need?.label ?? p.need}</Zh>）：“{p.raw_text}”
                    {p.as_hard_requirement ? <span className="chip warn">{t("more.attrHard")}</span> : null}{" "}
                    <span className="small muted">
                      <Zh>{kind?.note}</Zh>
                    </span>{" "}
                    <button type="button" className="link" onClick={() => dispatch({ type: "attr/remove", preferenceId: p.preference_id })}>
                      {t("more.attrRemove")}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <div className="stack">
            <p>
              <label htmlFor="attr-kind">{t("more.attrKind")}</label>{" "}
              <select id="attr-kind" value={newKind} onChange={(e) => setNewKind(e.target.value)}>
                {(caps.attribute_kinds ?? []).map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </select>
            </p>
            <p>
              <label htmlFor="attr-need">{t("more.attrNeed")}</label>{" "}
              <select id="attr-need" value={newNeed} onChange={(e) => setNewNeed(e.target.value)}>
                {caps.material_leaves.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </p>
            <p>
              <label htmlFor="attr-text">{t("more.attrText")}</label>
              <input id="attr-text" type="text" maxLength={300} value={newText} onChange={(e) => setNewText(e.target.value)} style={{ width: "100%" }} />
            </p>
            <label className="choice">
              <input type="checkbox" checked={newHard} onChange={(e) => setNewHard(e.target.checked)} />
              {t("more.attrHard")}
            </label>
            {newHard ? <p className="small box warn">{t("more.attrHardNote")}</p> : null}
            <button
              type="button"
              disabled={!newText.trim()}
              onClick={() => {
                dispatch({ type: "attr/add", kind: newKind, need: newNeed, rawText: newText, asHardRequirement: newHard });
                setNewText("");
                setNewHard(false);
              }}
            >
              {t("more.attrAdd")}
            </button>
          </div>
        </Section>
      ) : null}

      <StepNav draftId={draftId} prev="priorities" next="review" />
    </div>
  );
}
