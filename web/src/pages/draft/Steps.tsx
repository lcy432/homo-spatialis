// 第 1–4 步：地区与城市、生活事实（职业目录、年龄、现金、资格）、偏好与取舍、健康与兴趣。
// 题目、选项、标签、分组与说明都是 E3 能力清单与输入目录的原话；页面只收集回答，不换算资格、不推断职业、不改点数。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CatalogOccupation, InputCatalog, InterestPanel, ProductCapabilities } from "../../contract/types";
import { Allocator } from "../../components/Allocator";
import { CatalogPicker } from "../../components/CatalogPicker";
import { ChoicePanel, type PanelGroupDef, type PanelItem } from "../../components/ChoicePanel";
import { CityList } from "../../components/CityList";
import { useGeography } from "../../api/queries";
import { CityMapSlot } from "../../components/CityMapSlot";
import { Dialog } from "../../components/Dialog";
import { MapStage } from "../../components/MapStage";
import { Section } from "../../components/common";
import { PageTitle, StepNav } from "../../components/Layout";
import { occupationById } from "../../draft/catalog";
import { useDraftEditor } from "../../draft/DraftEditor";
import { parsePoints } from "../../draft/exact";
import type { HealthChoice, InterestField, InterestSpecialState } from "../../draft/reducer";
import { useDraftReview } from "../../draft/review";
import { BizNotice, DText, Zh, ZhList } from "../../i18n/biz";
import { useAnswerState } from "../../display/scope";
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

/** 兼容：没有输入目录的资料包（合成包）仍按文字描述 + 服务给的候选。 */
function OccupationTextCompat() {
  const { t } = useTranslation();
  const { state, dispatch, save } = useDraftEditor();
  const d = state.draft;
  const lang = d.occupation.language ?? "zh";
  const { review, current, query } = useDraftReview(!!d.occupation.raw_text?.trim());
  const occ = review?.occupation ?? null;
  return (
    <fieldset data-field="occupation">
      <legend>{t("facts.occupation")}</legend>
      <p className="small muted" id="occupation-help">
        {t("facts.noCatalog")}
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
      {d.occupation.raw_text?.trim() ? (
        !occ ? (
          <p className="small muted">{save.kind === "saved" && query.isFetching ? t("facts.candidatesChecking") : t("facts.candidatesWait")}</p>
        ) : (
          <div className="stack" aria-busy={!current || undefined}>
            <h3>{t("facts.candidatesTitle")}</h3>
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
        )
      ) : null}
    </fieldset>
  );
}

export function ExperienceQuestion({ catalog, occupation }: { catalog: InputCatalog; occupation: CatalogOccupation }) {
  const { t } = useTranslation();
  const { state, dispatch } = useDraftEditor();
  const d = state.draft;
  const q = occupation.experience_question ? (catalog.experience ?? []).find((x) => x.question_id === occupation.experience_question) : null;
  const exp = d.experience;
  const entries = exp?.state === "provided" ? (exp.entries ?? []) : [];
  const stale = entries.filter((e) => !q || !q.related.includes(e.concept_id));
  // “有相关经验”但还没填年数时，草稿里与“没有相关经验”一样是空表（契约没有这个中间态）：这里只记用户点了哪个单选
  const [wantsEntries, setWantsEntries] = useState(false);
  if (!q) {
    // 这个职业没有经验题：旧条目（为别的职业填的）照实列出，由用户决定去留，不悄悄删
    if (!stale.length) return null;
    return (
      <fieldset data-field="experience">
        <legend>{t("facts.experience")}</legend>
        <p className="small box warn">{t("facts.expStale")}</p>
        <StaleEntries entries={stale} catalog={catalog} />
      </fieldset>
    );
  }
  const mode: "unanswered" | "unsure" | "none" | "has" =
    exp?.state === "unsure" ? "unsure" : exp?.state === "provided" ? (entries.length || wantsEntries ? "has" : "none") : "unanswered";
  return (
    <fieldset data-field="experience">
      <legend>{t("facts.experience")}</legend>
      <Zh as="p">{q.prompt}</Zh>
      <p className="small muted">{t("facts.expNote")}</p>
      {(
        [
          ["has", t("facts.expHas")],
          ["none", t("facts.expNone")],
          ["unsure", t("facts.expUnsure")],
          ["unanswered", t("facts.expUnanswered")],
        ] as const
      ).map(([v, label]) => (
        <label key={v} className="choice">
          <input
            type="radio"
            name="experience.state"
            value={v}
            checked={mode === v}
            onChange={() => {
              setWantsEntries(v === "has");
              // “有相关经验”先成为空表，由下面填年数；其他状态照原样
              if (v === "has") {
                if (exp?.state !== "provided") dispatch({ type: "experience/state", state: "none" });
              } else dispatch({ type: "experience/state", state: v });
            }}
          />
          {label}
        </label>
      ))}
      {mode === "has" ? (
        <div className="stack">
          {q.related.map((cid) => {
            const o = occupationById(catalog, cid);
            const cur = entries.find((e) => e.concept_id === cid);
            const id = `exp-${cid}`;
            return (
              <p key={cid}>
                <label htmlFor={id}>{t("facts.expYears", { label: o?.label ?? cid })}</label>{" "}
                <input
                  id={id}
                  name={`experience.${cid}`}
                  type="number"
                  min={0}
                  max={60}
                  step={1}
                  inputMode="numeric"
                  value={cur ? String(cur.years) : ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    const n = v === "" ? null : Number(v);
                    dispatch({ type: "experience/set", conceptId: cid, years: n === null || !Number.isInteger(n) || n < 0 ? null : n });
                  }}
                  onBlur={() => dispatch({ type: "history/break" })}
                  style={{ width: "6em" }}
                />
              </p>
            );
          })}
          {stale.length ? (
            <>
              <p className="small box warn">{t("facts.expStale")}</p>
              <StaleEntries entries={stale} catalog={catalog} />
            </>
          ) : null}
        </div>
      ) : null}
    </fieldset>
  );
}

function StaleEntries({ entries, catalog }: { entries: readonly { concept_id: string; years: number }[]; catalog: InputCatalog }) {
  const { t } = useTranslation();
  const { dispatch } = useDraftEditor();
  return (
    <ul className="small">
      {entries.map((e) => (
        <li key={e.concept_id}>
          <Zh>{occupationById(catalog, e.concept_id)?.label ?? e.concept_id}</Zh>：{e.years}{" "}
          <button type="button" className="link" onClick={() => dispatch({ type: "experience/set", conceptId: e.concept_id, years: null })}>
            {t("common.remove")}
          </button>
        </li>
      ))}
    </ul>
  );
}

function Qualifications({ catalog }: { catalog: InputCatalog }) {
  const { t } = useTranslation();
  const { state, dispatch } = useDraftEditor();
  const d = state.draft;
  const s = catalog.sections;
  const edu = d.education;
  const eduMode = edu?.state === "provided" ? (edu.option_id ?? "") : (edu?.state ?? "unanswered");
  const cert = d.certificates;
  const certSelected = cert?.state === "provided" ? (cert.selected ?? []) : [];
  const certMode: "has" | "none" | "unsure" | "unanswered" =
    cert?.state === "provided" ? (certSelected.length ? "has" : "none") : cert?.state === "unsure" ? "unsure" : "unanswered";
  const selectedOcc = d.occupation.mode === "catalog" ? occupationById(catalog, d.occupation.selected_candidate) : null;
  const certItems: PanelItem[] = (catalog.certificates ?? []).map((c) => ({
    key: c.ability_id,
    label: c.label,
    groupId: "all",
    searchTerms: [c.label],
    note: t("facts.certFor", { list: c.occupations.map((id) => occupationById(catalog, id)?.label ?? id).join("、") }),
  }));
  const certGroups: PanelGroupDef[] = [{ id: "all", label: t("facts.certificates") }];
  return (
    <Section title={t("facts.qualTitle")}>
      <p className="small muted">{t("facts.qualNote")}</p>
      {(d.abilities ?? []).length ? (
        <div className="box warn" data-legacy-abilities>
          <p>{t("facts.legacyAbilities", { n: (d.abilities ?? []).length })}</p>
          <button type="button" onClick={() => dispatch({ type: "abilities/clear" })}>
            {t("facts.legacyAbilitiesClear")}
          </button>
        </div>
      ) : null}
      {s.education && catalog.education?.length ? (
        <fieldset data-field="education">
          <legend>{t("facts.education")}</legend>
          {[...catalog.education]
            .sort((a, b) => a.order - b.order)
            .map((o) => (
              <label key={o.option_id} className="choice">
                <input
                  type="radio"
                  name="education"
                  value={o.option_id}
                  checked={eduMode === o.option_id}
                  onChange={() => dispatch({ type: "education/set", state: "provided", optionId: o.option_id })}
                />
                <Zh>{o.label}</Zh>
              </label>
            ))}
          <label className="choice">
            <input type="radio" name="education" value="unsure" checked={eduMode === "unsure"} onChange={() => dispatch({ type: "education/set", state: "unsure" })} />
            {t("facts.eduUnsure")}
          </label>
          <label className="choice">
            <input
              type="radio"
              name="education"
              value="unanswered"
              checked={eduMode === "unanswered"}
              onChange={() => dispatch({ type: "education/set", state: "unanswered" })}
            />
            {t("facts.eduUnanswered")}
          </label>
        </fieldset>
      ) : null}
      {s.certificates && catalog.certificates?.length ? (
        <fieldset data-field="certificates" data-certificates={certMode}>
          <legend>{t("facts.certificates")}</legend>
          <div className="row">
            {(
              [
                ["none", t("facts.certNone")],
                ["unsure", t("facts.certUnsure")],
                ["unanswered", t("facts.certUnanswered")],
              ] as const
            ).map(([v, label]) => (
              <button key={v} type="button" aria-pressed={certMode === v} data-certificates-state={v} onClick={() => dispatch({ type: "certificates/state", state: v })}>
                {label}
              </button>
            ))}
          </div>
          <p className="small muted">{t("facts.certHas")}</p>
          <ChoicePanel
            name="certificates"
            items={certItems}
            groups={certGroups}
            selected={certSelected}
            onToggle={(k, on) => dispatch({ type: "certificates/toggle", abilityId: k, selected: on })}
          />
        </fieldset>
      ) : null}
      {s.experience && selectedOcc ? <ExperienceQuestion catalog={catalog} occupation={selectedOcc} /> : null}
    </Section>
  );
}

export function FactsStep() {
  const { t } = useTranslation();
  const { caps, state, dispatch, draftId } = useDraftEditor();
  const d = state.draft;
  const catalog = caps.input_catalog ?? null;
  const basis = caps.money_bases[0];
  const cashText = d.cash.amount_text ?? "";
  const cashLooksWrong = cashText !== "" && !MONEY_PLAIN.test(cashText.trim()) && !MONEY_GROUPED.test(cashText.trim());
  const ageOk = state.ageText.trim() === "" || d.age.state === "provided";
  const lang = d.occupation.language ?? "zh";
  const selectedConcept = d.occupation.mode === "catalog" ? (d.occupation.selected_candidate ?? null) : null;
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

      {catalog && catalog.sections.occupation === "catalog_select" ? (
        <Section title={t("facts.occupation")}>
          <p className="small muted">{t("facts.occupationHelp")}</p>
          {d.occupation.mode !== "catalog" && d.occupation.raw_text ? (
            <div className="box warn" data-legacy-occupation-text>
              <p>{t("facts.legacyText", { text: d.occupation.raw_text })}</p>
              <button type="button" onClick={() => dispatch({ type: "occupation/clear" })}>
                {t("facts.clearOccupation")}
              </button>
            </div>
          ) : null}
          <CatalogPicker
            catalog={catalog}
            selected={selectedConcept}
            onSelect={(o) =>
              dispatch({ type: "occupation/catalog", conceptId: o.concept_id, catalogVersion: catalog.catalog_version, catalogFingerprint: catalog.content_fingerprint })
            }
            onClear={() => dispatch({ type: "occupation/clear" })}
          />
        </Section>
      ) : (
        <OccupationTextCompat />
      )}

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

      {catalog && (catalog.sections.education || catalog.sections.certificates || catalog.sections.experience) ? (
        <Qualifications catalog={catalog} />
      ) : (caps.abilities ?? []).length ? (
        <LegacyAbilities caps={caps} />
      ) : null}

      <Section title={t("facts.startTitle")}>
        <ZhList items={caps.start_scenario} />
      </Section>

      <StepNav draftId={draftId} prev="place" next="priorities" />
    </div>
  );
}

/** 兼容：没有输入目录时的逐项资格确认（合成资料包）。 */
function LegacyAbilities({ caps }: { caps: ProductCapabilities }) {
  const { t } = useTranslation();
  const { state, dispatch } = useDraftEditor();
  const d = state.draft;
  return (
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
      <section aria-label={t("priorities.materialTitle")} data-material={materialStatus}>
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

/** 面板的条目与分组：有输入目录就用目录；没有（合成包）就用能力清单的词表（一个分组，别名当搜索词）。 */
function panelOf(field: InterestField, caps: ProductCapabilities, t: (k: "more.leisureTitle" | "more.tasteTitle") => string): { items: PanelItem[]; groups: PanelGroupDef[]; panel: InterestPanel | null } {
  const panel = field === "leisure_interest" ? (caps.input_catalog?.leisure ?? null) : (caps.input_catalog?.taste ?? null);
  if (panel) {
    const groups = [...panel.groups].sort((a, b) => a.order - b.order).map((g) => ({ id: g.group_id, label: g.label }));
    const items = [...panel.entries].sort((a, b) => a.order - b.order).map((e) => ({ key: e.key, label: e.label, groupId: e.group_id, searchTerms: e.search_terms }));
    return { items, groups, panel };
  }
  const vocab = field === "leisure_interest" ? caps.interests.leisure_categories : caps.interests.cuisines;
  const gid = "all";
  return {
    items: vocab.map((v) => ({ key: v.key, label: v.label, groupId: gid, searchTerms: [v.label, ...v.aliases] })),
    groups: [{ id: gid, label: t(field === "leisure_interest" ? "more.leisureTitle" : "more.tasteTitle") }],
    panel: null,
  };
}

const SPECIALS: Record<InterestField, InterestSpecialState[]> = {
  leisure_interest: ["unsure", "no_fixed_hobby", "generic"],
  taste: ["unsure", "no_preference"],
};

/** 特殊作答状态的名称与含义：按 (answer, state) 从能力清单的显示信封取（交接单第 13.3 节）；没有信封时用界面字典的名称。 */
export function SpecialStateText({ field, state }: { field: InterestField; state: string }) {
  const { t } = useTranslation();
  const a = useAnswerState(field === "taste" ? "taste" : "leisure", state);
  if (!a) return <>{t(`more.special_${state}` as "more.special_unsure")}</>;
  return (
    <>
      <strong>
        <DText text={a.name} />
      </strong>
      ：<DText text={a.meaning} />
    </>
  );
}

function InterestSection({ field, title }: { field: InterestField; title: string }) {
  const { t } = useTranslation();
  const { caps, state, dispatch } = useDraftEditor();
  const cur = state.draft[field];
  const curState = cur?.state ?? "unanswered";
  const selected = curState === "selected" ? (cur?.selected ?? []) : [];
  const { items, groups, panel } = panelOf(field, caps, t);
  const [pendingSpecial, setPendingSpecial] = useState<InterestSpecialState | "unanswered" | null>(null);
  const labelOf = (k: string) => items.find((i) => i.key === k)?.label ?? k;
  const isSpecial = curState !== "unanswered" && curState !== "selected";
  const setSpecial = (s: InterestSpecialState | "unanswered") => {
    if (selected.length) setPendingSpecial(s);
    else dispatch({ type: "interest/state", field, state: s });
  };
  return (
    <Section title={title}>
      {panel ? <Zh as="p">{panel.note}</Zh> : null}
      <p className="small muted">{t(field === "leisure_interest" ? "more.leisureHelp" : "more.tasteHelp")}</p>
      {curState === "described" && cur?.text ? (
        <div className="box warn" data-legacy-interest-text>
          <p>{t("more.legacyText", { text: cur.text })}</p>
          <button type="button" onClick={() => dispatch({ type: "interest/clear", field })}>
            {t("more.legacyClear")}
          </button>
        </div>
      ) : null}
      {isSpecial ? (
        <p className="box note" data-interest-state={curState}>
          <SpecialStateText field={field} state={curState} />{" "}
          <button type="button" className="link" onClick={() => dispatch({ type: "interest/state", field, state: "unanswered" })}>
            {t("more.backToPanel")}
          </button>
        </p>
      ) : (
        <ChoicePanel
          name={field}
          items={items}
          groups={groups}
          selected={selected}
          notes={panel?.search_notes ?? []}
          onToggle={(k, on) => dispatch({ type: "interest/toggle", field, key: k, selected: on })}
        />
      )}
      <details data-field={field}>
        <summary>{t("more.otherCases")}</summary>
        {SPECIALS[field].map((s) => (
          <label key={s} className="choice">
            <input type="radio" name={`${field}.state`} value={s} checked={curState === s} onChange={() => setSpecial(s)} />
            {t(`more.special_${s}` as "more.special_unsure")}
          </label>
        ))}
        <label className="choice">
          <input type="radio" name={`${field}.state`} value="unanswered" checked={curState === "unanswered"} onChange={() => setSpecial("unanswered")} />
          {t("states.unanswered")}
        </label>
      </details>
      <Dialog open={pendingSpecial !== null} onClose={() => setPendingSpecial(null)} title={t("more.otherCases")}>
        <p>{t("more.specialClears", { n: selected.length, list: selected.map(labelOf).join("、") })}</p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              if (pendingSpecial) dispatch({ type: "interest/state", field, state: pendingSpecial });
              setPendingSpecial(null);
            }}
          >
            {t("more.specialClearConfirm")}
          </button>
          <button type="button" onClick={() => setPendingSpecial(null)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>
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

      <InterestSection field="leisure_interest" title={t("more.leisureTitle")} />
      <InterestSection field="taste" title={t("more.tasteTitle")} />
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

      <StepNav draftId={draftId} prev="priorities" next="review" />
    </div>
  );
}
