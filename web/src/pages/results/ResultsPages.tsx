// 结果三层：城市比较（/results/:intentId）→ 单城详情（…/city/:cityRef）→ 分身年度生活（…/clone/:cloneRef）。
// 全部只用 E3 的公共结果：不另算分数、不排序（照服务给的城市顺序）、不写“最佳城市”。C = 0、未生成、缺少评价用中性符号与文字，
// 不涂最低分的颜色；含 M 但有均值的城市保留均值颜色，另标缺失人数。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { REFERENCE_PLAN, REQUIREMENT_NEED, REQUIREMENT_SUMMARY, pendingLabel } from "../../contract/pending-labels";
import type {
  CityDataNature,
  CityResult,
  ComparisonLine,
  GeographyDisplayBundle,
  ProductCapabilities,
  ProductPreflight,
  ProductResult,
  RunSettingsView,
  ScopeNote,
} from "../../contract/types";
import { CityMapSlot } from "../../components/CityMapSlot";
import { MapStage } from "../../components/MapStage";
import { PageTitle } from "../../components/Layout";
import { useCityName } from "../../display/names";
import { resultPtr } from "../../display/pointers";
import { ApiErrorView, ExplanationView, Loading, Num, PendingText, Section, TableWrap } from "../../components/common";
import { CemBar, ColorLegend, CompareChart } from "../../components/results/CompareChart";
import { YearReport } from "../../components/results/YearReport";
import styles from "../../components/results/Results.module.css";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";
import { useMode } from "../../ui/mode";
import { colorWindow, positionIn, rampColor } from "../../view/colorScale";
import { type CityRow, cityRows, groupByCountry } from "../../view/results";
import { useGeography } from "../../api/queries";
import { DisplayScope, at } from "../../display/scope";
import { useRunResult } from "./useRunResult";

function useLoaded(intentId: string) {
  const r = useRunResult(intentId);
  const { t } = useTranslation();
  if (r.intent.isPending) return { kind: "node", node: <Loading /> } as const;
  if (r.intent.error && !r.intent.data)
    return { kind: "node", node: <ApiErrorView error={r.intent.error} onRetry={() => void r.intent.refetch()} /> } as const;
  const v = r.intent.data;
  if (!v?.result_available) {
    return {
      kind: "node",
      node: (
        <div>
          <p className="box note" data-results-unavailable>
            {t("results.unavailable")}
          </p>
          <Link className="tap" to={`/intents/${encodeURIComponent(intentId)}`}>
            {t("runs.open")}
          </Link>
        </div>
      ),
    } as const;
  }
  if (r.result.isPending) return { kind: "node", node: <Loading label={t("results.loading")} /> } as const;
  if (r.result.error || !r.result.data)
    return { kind: "node", node: <ApiErrorView error={r.result.error} onRetry={() => void r.result.refetch()} /> } as const;
  return { kind: "data", data: { ...r, intentView: v, res: r.result.data } } as const;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

function comparisonSentence(line: ComparisonLine, label: (ref: string) => string, t: Translate): string {
  return t(`results.direction.${line.direction}`, { a: label(line.city_a), b: label(line.city_b) });
}

function Comparisons({ lines, label, only }: { lines: readonly ComparisonLine[]; label(ref: string): string; only?: string }) {
  const { t } = useTranslation();
  const shown = lines.map((l, i) => [l, i] as const).filter(([l]) => !only || l.city_a === only || l.city_b === only);
  if (!shown.length) return null;
  return (
    <ul className="plain stack">
      {shown.map(([l, li]) => (
        <li key={`${l.city_a}.${l.city_b}`} data-comparison={l.direction}>
          <strong>{comparisonSentence(l, label, t as unknown as Translate)}</strong>
          {l.range_difference_low || l.range_difference_high ? (
            <span className="small muted">
              {" "}
              · {t("results.rangeDiff", { low: l.range_difference_low?.display ?? "—", high: l.range_difference_high?.display ?? "—" })}
            </span>
          ) : null}
          <Zh as="p" pointer={at("/comparisons", li, "note_zh")}>
            {l.note_zh}
          </Zh>
        </li>
      ))}
    </ul>
  );
}

function CitySummaryCard({ row, base, swatch }: { row: CityRow; base: string; swatch: string | null }) {
  const { t } = useTranslation();
  const c = row.city;
  return (
    <article className="card" data-city={c.city_ref} aria-labelledby={`city-${c.city_ref}`}>
      <h3 id={`city-${c.city_ref}`} className="card-title">
        {swatch ? <span className={styles.swatch} style={{ background: swatch }} aria-hidden="true" /> : <span className={styles.swatchNone} aria-hidden="true" />}
        <Zh pointer={row.labelPointer}>{c.label}</Zh>
      </h3>
      <p data-mean={c.mean_j?.exact ?? ""}>
        {t("results.col.j")}：<strong>
          <Num value={c.mean_j} />
        </strong>
        {c.missing ? <span className="chip warn"> {t("results.hasMissing", { m: c.missing })}</span> : null}
      </p>
      <p className="small">{t("results.cem", { planned: c.planned, c: c.complete, e: c.exits, m: c.missing })}</p>
      <p className="small">{c.mean_j ? t("results.meanOf", { mean: c.mean_j.display, c: c.mean_denominator }) : t("results.noMean")}</p>
      <CemBar planned={c.planned} c={c.complete} e={c.exits} m={c.missing} />
      <p className="small">
        <Zh pointer={`${row.pointer}/headline_zh`}>{c.headline_zh}</Zh>
      </p>
      <Link className="button" to={`${base}/city/${encodeURIComponent(c.city_ref)}`}>
        {t("results.openCity")}
      </Link>
    </article>
  );
}

function CityTable({ rows }: { rows: readonly CityRow[] }) {
  const { t } = useTranslation();
  return (
    <TableWrap label={t("results.tableTitle")}>
      <table data-table="cities">
        <caption className="visually-hidden">{t("results.tableTitle")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("results.col.city")}</th>
            <th scope="col" className="num">{t("results.col.j")}</th>
            <th scope="col" className="num">{t("results.col.l")}</th>
            <th scope="col" className="num">{t("results.col.v")}</th>
            <th scope="col" className="num">{t("results.col.planned")}</th>
            <th scope="col" className="num">{t("results.col.c")}</th>
            <th scope="col" className="num">{t("results.col.e")}</th>
            <th scope="col" className="num">{t("results.col.m")}</th>
            <th scope="col" className="num">{t("results.col.meanDen")}</th>
            <th scope="col" className="num">{t("results.col.rangeDen")}</th>
            <th scope="col">{t("results.col.range")}</th>
            <th scope="col">{t("results.col.reasons")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ city: c, ...r }) => (
            <tr key={c.city_ref} data-city={c.city_ref}>
              <th scope="row">
                <Zh pointer={r.labelPointer}>{c.label}</Zh>
              </th>
              <td className="num">
                <Num value={c.mean_j} />
              </td>
              <td className="num">
                <Num value={c.mean_l} />
              </td>
              <td className="num">
                <Num value={c.mean_v} />
              </td>
              <td className="num">{c.planned}</td>
              <td className="num">{c.complete}</td>
              <td className="num">{c.exits}</td>
              <td className="num">{c.missing}</td>
              <td className="num">{c.mean_denominator}</td>
              <td className="num">{c.range_denominator}</td>
              <td>
                {c.range_lower && c.range_upper ? (
                  <>
                    <Num value={c.range_lower} />–<Num value={c.range_upper} />
                  </>
                ) : (
                  t("results.noRange")
                )}
              </td>
              <td>
                {(c.missing_reasons ?? []).map((m, mi) => (
                  <span key={m.reason} className="small" style={{ display: "block" }}>
                    <Zh pointer={at(`${r.pointer}/missing_reasons`, mi, "reason_zh")}>{m.reason_zh}</Zh> ×{m.count}
                  </span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function ResultsPage() {
  const { intentId = "" } = useParams();
  const loaded = useLoaded(intentId);
  // 城市点：按这次结果自己的资料包指纹读地理清单（不是当前能力清单的资料包），并传这份结果显示信封里绑定的资源版本
  // （交接单第 13.2 节）。信封还没读到就先不读清单；版本为空就不传版本、不拿当前版本冒充，页面写明。清单里每座城的 city_ref
  // 自带地理版本，只配给同一个 city_ref。读不到就不画点。
  // 历史结果只在有明确绑定的版本时才读地理清单（省略版本 = 当前登记的版本，不是历史版本；E5-R4）。
  // 版本为空、信封读不到、信封还没读到：都不发不带版本的请求，也不拿当前城市点兜底——底图、列表与表格照常。
  const pack = loaded.kind === "data" ? loaded.data.res.meta.data_pack.content_fingerprint : null;
  const boundVersion = loaded.kind === "data" ? loaded.data.boundVersion : undefined;
  const geo = useGeography(boundVersion ? pack : null, boundVersion ?? null);
  if (loaded.kind === "node") return <div>{loaded.node}</div>;
  const { res, intentView: v, caps, display, intentDisplay } = loaded.data;
  return (
    <DisplayScope envelope={intentDisplay.data}>
      <DisplayScope envelope={display.data}>
        <ResultsOverview
          res={res}
          caps={caps.data ?? null}
          settings={v.settings ?? null}
          geography={boundVersion ? (geo.data ?? (geo.isError ? null : undefined)) : null}
          boundVersion={boundVersion}
          base={`/results/${encodeURIComponent(intentId)}`}
        />
      </DisplayScope>
    </DisplayScope>
  );
}

/** 第一层：本轮城市比较（只接收数据；页面与开发画廊共用）。base 是这次结果的链接前缀。 */
export function ResultsOverview({
  res,
  caps,
  settings,
  geography = null,
  boundVersion,
  base,
}: {
  res: ProductResult;
  caps: ProductCapabilities | null;
  settings: RunSettingsView | null;
  /** 地理显示清单（undefined = 还在读，null = 没有）。 */
  geography?: GeographyDisplayBundle | null;
  /** 这份结果绑定的显示资源版本（undefined = 还没读到；null = 没有绑定的历史版本）。不传就不说明。 */
  boundVersion?: string | null;
  base: string;
}) {
  const { t } = useTranslation();
  const { mode } = useMode();
  const navigate = useNavigate();
  const [country, setCountry] = useState<string | null>(null);
  const rows = useMemo(() => cityRows(res, caps), [res, caps]);
  const w = useMemo(() => colorWindow(rows.map((r) => r.mean).filter((v): v is number => v !== null)), [rows]);
  const colors = useMemo(
    () => new Map(rows.filter((r) => r.mean !== null && w).map((r) => [r.cityRef, rampColor(positionIn(r.mean as number, w as NonNullable<typeof w>), mode)])),
    [rows, w, mode],
  );
  const label = useCityName(caps, res.cities);
  const groups = groupByCountry(rows.filter((r) => country === null || r.country === country));
  const generated = res.evaluation_status === "generated";
  const mapSlot = generated ? (
    <CityMapSlot
      purpose="output"
      fill
      cities={caps?.cities ?? []}
      cityRefs={rows.map((r) => r.cityRef)}
      geography={geography}
      countries={[...new Set(rows.map((r) => r.country).filter((c): c is string => !!c))]}
      colors={colors}
      country={country}
      onCountry={setCountry}
      onOpenCity={(ref) => navigate(`${base}/city/${encodeURIComponent(ref)}`)}
    />
  ) : null;
  const versionNote =
    boundVersion === undefined ? null : (
      <p className="small muted" data-geo-version={boundVersion ?? "unbound"}>
        {boundVersion ? t("maps.boundVersion", { version: boundVersion }) : t("maps.unboundVersion")}
      </p>
    );
  const mapWithLegend = mapSlot ? (
    <>
      {mapSlot}
      {mode === "globe" ? null : <ColorLegend window={w} />}
      {mode === "globe" ? null : versionNote}
    </>
  ) : null;
  const head = (
    <>
      <PageTitle>{t("results.title")}</PageTitle>
      <BizNotice />
    </>
  );
  const body = (
    <>
      <Section title={t("results.status")}>
        <p className={generated ? "box ok" : "box warn"}>{generated ? t("results.generated") : t("results.notGenerated")}</p>
        {!generated && res.not_generated_reason ? (
          <Zh as="p" pointer="/not_generated_reason">
            {res.not_generated_reason}
          </Zh>
        ) : null}
        <p className="small">{t("results.stageLabel", { stage: res.run_stage })}</p>
        <p className="small">{t("results.realCalls", { n: res.real_model_calls })}</p>
        <p className="small">
          {t("app.dataPack")}：{res.meta.data_pack.pack_id}（{res.meta.data_pack.status}）— <Zh pointer="/meta/data_pack/note">{res.meta.data_pack.note}</Zh>
        </p>
        {settings ? (
          <p className="small">
            {t("preflight.scale", { cities: settings.cities.length, clones: settings.clones_per_city, years: settings.years })}
          </p>
        ) : null}
      </Section>

      {generated ? (
        <>
          {mode === "globe" ? (
            <Section title={t("results.mapTitle")}>
              <ColorLegend window={w} />
              {versionNote}
            </Section>
          ) : null}
          <Section title={t("results.chartTitle")}>
            <CompareChart rows={rows} window={w} />
          </Section>
          {groups.map((g) => (
            <section key={g.country} aria-label={t("results.countryGroup", { country: g.country })}>
              {groups.length > 1 ? <h2>{g.country}</h2> : null}
              <div className={styles.cityCards}>
                {g.rows.map((r) => (
                  <CitySummaryCard
                    key={r.cityRef}
                    row={r}
                    base={base}
                    swatch={r.mean !== null && w ? rampColor(positionIn(r.mean, w), mode) : null}
                  />
                ))}
              </div>
            </section>
          ))}
          <Section title={t("results.tableTitle")}>
            <CityTable rows={rows} />
          </Section>
          {res.comparisons?.length ? (
            <Section title={t("results.comparisonsTitle")}>
              <Comparisons lines={res.comparisons} label={label} />
            </Section>
          ) : null}
        </>
      ) : (
        <Section title={t("city.clonesTitle")}>
          <CloneLinks res={res} base={base} />
        </Section>
      )}

      {res.reading_guide_zh?.length ? (
        <Section title={t("results.readingGuide")}>
          <ZhList items={res.reading_guide_zh} pointer="/reading_guide_zh" />
        </Section>
      ) : null}
      {res.limitations_zh?.length ? (
        <Section title={t("results.limitations")}>
          <ZhList items={res.limitations_zh} pointer="/limitations_zh" />
        </Section>
      ) : null}
      <Section title={t("results.plannerTitle")}>
        <Zh as="p" pointer="/planner/description_zh">
          {res.planner.description_zh}
        </Zh>
        <ZhList items={res.sources_zh} pointer="/sources_zh" />
      </Section>
    </>
  );
  if (!generated || !mapSlot) {
    return (
      <div data-results={res.evaluation_status}>
        {head}
        {body}
      </div>
    );
  }
  return (
    <div data-results={res.evaluation_status}>
      <MapStage head={head} map={mapWithLegend} panelLabel={t("results.title")}>
        {body}
      </MapStage>
    </div>
  );
}

function CloneLinks({ res, base, cityRef }: { res: ProductResult; base: string; cityRef?: string }) {
  const { t } = useTranslation();
  const cityName = useCityName(null, res.cities);
  const clones = (res.clones ?? []).map((c, i) => [c, i] as const).filter(([c]) => !cityRef || c.city_ref === cityRef);
  return (
    <ul className="plain stack">
      {clones.map(([c, ci]) => {
        const months = (c.years ?? []).reduce((n, y) => n + y.months_lived, 0);
        const cp = at("/clones", ci);
        return (
          <li key={c.clone_ref} className="card" data-clone={c.clone_ref} data-category={c.category ?? "none"}>
            <p>
              <strong>{t("clone.title", { city: cityName(c.city_ref), n: c.repeat_index + 1 })}</strong>
              {" · "}
              <Zh pointer={`${cp}/category_zh`}>{c.category_zh}</Zh>
            </p>
            <p className="small">{t("city.cloneYears", { years: Math.floor(months / 12), months: months % 12 })}</p>
            <p className="small">
              <Zh pointer={`${cp}/explanation/reason_zh`}>{c.explanation.reason_zh}</Zh>
            </p>
            {c.missing_reason_zh ? (
              <p className="small">
                <Zh pointer={`${cp}/missing_reason_zh`}>{c.missing_reason_zh}</Zh>
              </p>
            ) : null}
            <Link className="button" to={`${base}/clone/${encodeURIComponent(c.clone_ref)}`}>
              {t("city.openClone")}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function DataNatureView({ nature, pointer }: { nature: CityDataNature; pointer: string }) {
  const { t } = useTranslation();
  const pp = pointer;
  return (
    <div className="stack" data-nature={nature.pack_status}>
      <Zh as="p" pointer={`${pp}/summary_zh`}>
        {nature.summary_zh}
      </Zh>
      <p className="small">
        {t("city.packStatus")}：{nature.pack_status}
      </p>
      <h3>{t("city.sourceKinds")}</h3>
      <p className="small muted">{t("city.sourceCountNote")}</p>
      <TableWrap label={t("city.sourceKinds")}>
        <table>
          <thead>
            <tr>
              <th scope="col">{t("city.category")}</th>
              {(["observed", "derived", "estimated", "synthetic"] as const).map((k) => (
                <th key={k} scope="col" className="num">
                  {t(`city.kind.${k}`)}
                </th>
              ))}
              <th scope="col" className="num">
                {t("city.total")}
              </th>
            </tr>
          </thead>
          <tbody>
            {(nature.opportunities ?? []).map((o) => (
              <tr key={o.category}>
                <th scope="row">{o.category}</th>
                {(["observed", "derived", "estimated", "synthetic"] as const).map((k) => (
                  <td key={k} className="num">
                    {o.by_kind.find((x) => x.kind === k)?.count ?? 0}
                  </td>
                ))}
                <td className="num">{o.total}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{t("city.total")}</th>
              {(["observed", "derived", "estimated", "synthetic"] as const).map((k) => (
                <td key={k} className="num">
                  {nature.totals.find((x) => x.kind === k)?.count ?? 0}
                </td>
              ))}
              <td className="num">{nature.totals.reduce((n, x) => n + x.count, 0)}</td>
            </tr>
          </tbody>
        </table>
      </TableWrap>
      {nature.named_assumptions?.length ? (
        <>
          <h3>{t("city.assumptions")}</h3>
          <ul>
            {nature.named_assumptions.map((a, ai) => (
              <li key={a.note_zh}>
                <Zh pointer={at(`${pp}/named_assumptions`, ai, "note_zh")}>{a.note_zh}</Zh>{" "}
                <span className="small muted">{t("city.assumptionCount", { n: a.count, examples: a.examples.join("、") })}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {nature.common_priors?.length ? (
        <>
          <h3>{t("city.priors")}</h3>
          <ul>
            {nature.common_priors.map((p, pi) => (
              <li key={p.fact_id}>
                <Zh pointer={at(`${pp}/common_priors`, pi, "label")}>{p.label}</Zh>：<Zh pointer={at(`${pp}/common_priors`, pi, "meaning_zh")}>{p.meaning_zh}</Zh>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {nature.treatment_assumptions_zh?.length ? (
        <>
          <h3>{t("city.treatment")}</h3>
          <ZhList items={nature.treatment_assumptions_zh} pointer={`${pp}/treatment_assumptions_zh`} />
        </>
      ) : null}
      {nature.model_plan_activities?.length ? (
        <>
          <h3>{t("city.activities")}</h3>
          <ZhList items={nature.model_plan_activities} pointer={`${pp}/model_plan_activities`} />
        </>
      ) : null}
      {nature.home_areas != null || nature.work_areas != null ? (
        <p className="small">{t("city.areas", { home: nature.home_areas ?? "—", work: nature.work_areas ?? "—" })}</p>
      ) : null}
      {nature.commute_modes?.length ? (
        <>
          <h3>{t("city.commuteModes")}</h3>
          <ul>
            {nature.commute_modes.map((m, mi) => (
              <li key={m.mode} data-mode-available={m.in_relation}>
                <Zh pointer={at(`${pp}/commute_modes`, mi, "mode_zh")}>{m.mode_zh}</Zh>：{m.in_relation ? t("city.inRelation", { n: m.lines }) : t("city.notInRelation")}
                {m.condition_zh ? (
                  <span className="small muted">
                    {" "}
                    · <Zh pointer={at(`${pp}/commute_modes`, mi, "condition_zh")}>{m.condition_zh}</Zh>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {nature.menus?.length ? (
        <>
          <h3>{t("city.menus")}</h3>
          <ul className="small">
            {nature.menus.map((m) => (
              <li key={m.category}>
                {t("city.menuLine", { category: m.category, years: m.years, repeats: m.env_repeats, min: m.items_min, max: m.items_max })}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {nature.tax ? (
        <>
          <h3>{t("city.tax")}</h3>
          <p>
            <Zh pointer={`${pp}/tax/status_zh`}>{nature.tax.status_zh}</Zh>
          </p>
          {nature.tax.residence_scope_zh ? (
            <Zh as="p" pointer={`${pp}/tax/residence_scope_zh`}>
              {nature.tax.residence_scope_zh}
            </Zh>
          ) : null}
          <ZhList items={nature.tax.rules_zh} pointer={`${pp}/tax/rules_zh`} />
          {nature.tax.absent_zh?.length ? (
            <>
              <p className="small">{t("city.taxAbsent")}</p>
              <ZhList items={nature.tax.absent_zh} pointer={`${pp}/tax/absent_zh`} />
            </>
          ) : null}
          {nature.tax.not_modeled_zh?.length ? (
            <>
              <p className="small">{t("city.taxNotModeled")}</p>
              <ZhList items={nature.tax.not_modeled_zh} pointer={`${pp}/tax/not_modeled_zh`} />
            </>
          ) : null}
        </>
      ) : null}
      <h3>{t("city.build")}</h3>
      {nature.build_status === "not_available" ? (
        <p className="small">{t("city.buildUnknown")}</p>
      ) : (
        <ul className="small">
          {(nature.build ?? []).map((b, bi) => (
            <li key={b.key}>
              <Zh pointer={at(`${pp}/build`, bi, "label_zh")}>{b.label_zh}</Zh>：{b.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ScopeNotes({ notes, cityRef }: { notes: readonly ScopeNote[]; cityRef: string }) {
  const shown = notes.map((n, i) => [n, i] as const).filter(([n]) => !n.cities?.length || n.cities.includes(cityRef));
  return (
    <ul className="plain stack">
      {shown.map(([n, i]) => (
        <li key={`${n.topic}.${i}`} data-topic={n.topic}>
          <strong>
            <Zh pointer={at("/preflight/scope_notes", i, "title_zh")}>{n.title_zh}</Zh>
          </strong>
          <Zh as="p" pointer={at("/preflight/scope_notes", i, "text_zh")}>
            {n.text_zh}
          </Zh>
        </li>
      ))}
    </ul>
  );
}

export function CityPage() {
  const { intentId = "", cityRef = "" } = useParams();
  const loaded = useLoaded(intentId);
  if (loaded.kind === "node") return <div>{loaded.node}</div>;
  const { res, boundPreflight, caps, display, intentDisplay } = loaded.data;
  return (
    <DisplayScope envelope={intentDisplay.data}>
      <DisplayScope envelope={display.data}>
        <CityDetail res={res} caps={caps.data ?? null} cityRef={cityRef} boundPreflight={boundPreflight} base={`/results/${encodeURIComponent(intentId)}`} />
      </DisplayScope>
    </DisplayScope>
  );
}

/** 第二层：单城详情。boundPreflight 是这次运行绑定的那份运行前检查（读不到就是 null，如实写缺失）。 */
export function CityDetail({
  res,
  caps,
  cityRef,
  boundPreflight,
  base,
}: {
  res: ProductResult;
  caps: ProductCapabilities | null;
  cityRef: string;
  boundPreflight: ProductPreflight | null;
  base: string;
}) {
  const { t } = useTranslation();
  const c: CityResult | undefined = res.cities?.find((x) => x.city_ref === cityRef);
  const label = useCityName(caps, res.cities);
  const cp = resultPtr.city(res.cities, cityRef).replace(/\/label$/, "");
  const natureIndex = (res.data_nature ?? []).findIndex((n) => n.city_ref === cityRef);
  const nature = natureIndex >= 0 ? res.data_nature?.[natureIndex] : undefined;
  const coverageIndex = (res.option_coverage ?? []).findIndex((o) => o.city_ref === cityRef);
  const coverage = coverageIndex >= 0 ? res.option_coverage?.[coverageIndex] : undefined;
  const back = base;
  return (
    <div data-city-page={cityRef}>
      <p>
        <Link className="tap" to={back}>
          ← {t("city.backToCompare")}
        </Link>
      </p>
      <PageTitle>{t("city.title", { city: label(cityRef) })}</PageTitle>
      <BizNotice />
      {c ? (
        <>
          <Section title={t("city.howToRead")}>
            <div className="two-col">
              <dl className="facts">
                <dt>J</dt>
                <dd>
                  <Num value={c.mean_j} />
                </dd>
                <dt>L</dt>
                <dd>
                  <Num value={c.mean_l} />
                </dd>
                <dt>V</dt>
                <dd>
                  <Num value={c.mean_v} />
                </dd>
              </dl>
              <ul className="small">
                <li>{t("city.lExplain")}</li>
                <li>{t("city.vExplain")}</li>
                <li>{t("city.jExplain")}</li>
              </ul>
            </div>
          </Section>
          <Section title={t("city.availability")}>
            <p>{t("results.cem", { planned: c.planned, c: c.complete, e: c.exits, m: c.missing })}</p>
            <p>{c.mean_j ? t("results.meanOf", { mean: c.mean_j.display, c: c.mean_denominator }) : t("results.noMean")}</p>
            <CemBar planned={c.planned} c={c.complete} e={c.exits} m={c.missing} />
            <p className="small">
              {t("results.col.range")}：
              {c.range_lower && c.range_upper ? (
                <>
                  <Num value={c.range_lower} />–<Num value={c.range_upper} />（{t("results.col.rangeDen")} {c.range_denominator}）
                </>
              ) : (
                t("results.noRange")
              )}
            </p>
            {c.missing_reasons?.length ? (
              <>
                <h3>{t("city.missingReasons")}</h3>
                <ul>
                  {c.missing_reasons.map((m, mi) => (
                    <li key={m.reason}>
                      <Zh pointer={at(`${cp}/missing_reasons`, mi, "reason_zh")}>{m.reason_zh}</Zh> ×{m.count}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {c.hard_requirements?.length ? (
              <>
                <h3>{t("city.hardTitle")}</h3>
                <p className="small muted">{t("city.hardNotChecked")}</p>
                <ul>
                  {c.hard_requirements.map((h) => (
                    <li key={h.need}>
                      <PendingText label={pendingLabel(REQUIREMENT_NEED, h.need)} />
                      <p className="small">{t("city.hardLine", { planned: h.planned, checked: h.checked, notChecked: h.not_checked })}</p>
                      <p className="small">
                        {t("city.hardCounts", {
                          every: h.satisfied_every_month,
                          lived: h.satisfied_in_lived_months,
                          violated: h.violated,
                          unknown: h.unknown,
                        })}
                      </p>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {c.notes_zh?.length ? (
              <>
                <h3>{t("city.notes")}</h3>
                <ZhList items={c.notes_zh} pointer={`${cp}/notes_zh`} />
              </>
            ) : null}
            {res.comparisons?.length ? (
              <>
                <h3>{t("city.comparisons")}</h3>
                <Comparisons lines={res.comparisons} label={label} only={cityRef} />
              </>
            ) : null}
          </Section>
        </>
      ) : null}
      <Section title={t("city.dataTitle")}>
        {nature ? <DataNatureView nature={nature} pointer={at("/data_nature", natureIndex)} /> : <p className="muted">{t("common.none")}</p>}
        {coverage ? (
          <>
            <h3>{t("city.coverageTitle")}</h3>
            <p className="small">
              {t("city.coverageLine", { surveyed: coverage.surveyed, resolved: coverage.resolved, unresolved: coverage.unresolved, excluded: coverage.excluded })}
            </p>
            <Zh as="p" pointer={at("/option_coverage", coverageIndex, "note_zh")}>
              {coverage.note_zh}
            </Zh>
          </>
        ) : null}
        <h3>{t("city.scopeTitle")}</h3>
        {boundPreflight ? <ScopeNotes notes={boundPreflight.scope_notes ?? []} cityRef={cityRef} /> : <p className="small box warn">{t("city.scopeMissing")}</p>}
      </Section>
      <Section title={t("city.clonesTitle")}>
        <CloneLinks res={res} base={base} cityRef={cityRef} />
      </Section>
    </div>
  );
}

export function ClonePage() {
  const { intentId = "", cloneRef = "" } = useParams();
  const loaded = useLoaded(intentId);
  if (loaded.kind === "node") return <div>{loaded.node}</div>;
  return (
    <DisplayScope envelope={loaded.data.intentDisplay.data}>
      <DisplayScope envelope={loaded.data.display.data}>
        <CloneDetail res={loaded.data.res} cloneRef={cloneRef} base={`/results/${encodeURIComponent(intentId)}`} />
      </DisplayScope>
    </DisplayScope>
  );
}

/** 第三层：一个分身逐年的生活记录。 */
export function CloneDetail({ res, cloneRef, base }: { res: ProductResult; cloneRef: string; base: string }) {
  const { t } = useTranslation();
  const nameOf = useCityName(null, res.cities);
  const ci = (res.clones ?? []).findIndex((x) => x.clone_ref === cloneRef);
  const c = ci >= 0 ? res.clones?.[ci] : undefined;
  if (!c) return <p className="box warn">{t("common.none")}</p>;
  const cityName = nameOf(c.city_ref);
  const cp = at("/clones", ci);
  return (
    <div data-clone-page={cloneRef}>
      <p>
        <Link className="tap" to={`${base}/city/${encodeURIComponent(c.city_ref)}`}>
          ← {t("clone.backToCity")}
        </Link>
      </p>
      <PageTitle>{t("clone.title", { city: cityName, n: c.repeat_index + 1 })}</PageTitle>
      <BizNotice />
      <Section title={t("clone.scores")}>
        <p data-category={c.category ?? "none"}>
          <strong>
            <Zh pointer={`${cp}/category_zh`}>{c.category_zh}</Zh>
          </strong>
        </p>
        <ExplanationView explanation={c.explanation} pointer={`${cp}/explanation`} />
        <dl className="facts">
          <dt>J</dt>
          <dd>
            <Num value={c.j} />
          </dd>
          <dt>L</dt>
          <dd>
            <Num value={c.l_actual} />
          </dd>
          <dt>V</dt>
          <dd>
            <Num value={c.v_reference} />
          </dd>
          {c.range_lower || c.range_upper ? (
            <>
              <dt>{t("results.col.range")}</dt>
              <dd>
                <Num value={c.range_lower} />–<Num value={c.range_upper} />
              </dd>
            </>
          ) : null}
          <dt>{t("clone.reference")}</dt>
          <dd>{c.reference_plan ? <PendingText label={pendingLabel(REFERENCE_PLAN, c.reference_plan)} /> : t("common.none")}</dd>
        </dl>
        {c.missing_reason_zh ? (
          <Zh as="p" pointer={`${cp}/missing_reason_zh`}>
            {c.missing_reason_zh}
          </Zh>
        ) : null}
        {c.valuation_scope?.length ? (
          <>
            <h3>{t("clone.valuationScope")}</h3>
            <ul>
              {c.valuation_scope.map((s, si) => (
                <li key={s.opportunity_id}>
                  <Zh pointer={at(`${cp}/valuation_scope`, si, "label")}>{s.label}</Zh>：<Zh pointer={at(`${cp}/valuation_scope`, si, "text_zh")}>{s.text_zh}</Zh>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {c.event_chain?.length ? (
          <>
            <h3>{t("clone.events")}</h3>
            <ZhList items={c.event_chain} pointer={`${cp}/event_chain`} />
          </>
        ) : null}
        {c.hard_requirements?.length ? (
          <>
            <h3>{t("clone.hard")}</h3>
            <ul>
              {c.hard_requirements.map((h) => (
                <li key={h.need}>
                  <PendingText label={pendingLabel(REQUIREMENT_NEED, h.need)} />：
                  <PendingText label={pendingLabel(REQUIREMENT_SUMMARY, h.summary)} />
                  <p className="small">
                    {t("clone.hardMonths", { ok: h.months_satisfied, bad: h.months_not_satisfied, unknown: h.months_unknown, notLived: h.months_not_lived })}
                  </p>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Section>
      <Section title={t("clone.record")}>
        <p className="small muted">{t("clone.recordNote")}</p>
        {c.years?.length ? c.years.map((y, yi) => <YearReport key={y.year_index} year={y} pointer={at(`${cp}/years`, yi)} />) : <p>{t("clone.noYears")}</p>}
      </Section>
      <Section title={t("clone.modelPlan")}>
        <p className="small muted">{t("clone.modelPlanPending")}</p>
      </Section>
    </div>
  );
}
