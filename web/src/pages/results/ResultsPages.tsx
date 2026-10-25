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
import { cityLabelOf } from "../../components/RunViews";
import { ApiErrorView, ExplanationView, Loading, Num, PendingText, Section, TableWrap } from "../../components/common";
import { CemBar, ColorLegend, CompareChart } from "../../components/results/CompareChart";
import { YearReport } from "../../components/results/YearReport";
import styles from "../../components/results/Results.module.css";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";
import { useMode } from "../../ui/mode";
import { colorWindow, positionIn, rampColor } from "../../view/colorScale";
import { type CityRow, cityRows, groupByCountry } from "../../view/results";
import { useGeography } from "../../api/queries";
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
          <p className="box note">{t("results.unavailable")}</p>
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

function comparisonSentence(line: ComparisonLine, label: (ref: string) => string, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`results.direction.${line.direction}`, { a: label(line.city_a), b: label(line.city_b) });
}

function Comparisons({ lines, label, only }: { lines: readonly ComparisonLine[]; label(ref: string): string; only?: string }) {
  const { t } = useTranslation();
  const shown = only ? lines.filter((l) => l.city_a === only || l.city_b === only) : lines;
  if (!shown.length) return null;
  return (
    <ul className="plain stack">
      {shown.map((l) => (
        <li key={`${l.city_a}.${l.city_b}`} data-comparison={l.direction}>
          <strong>{comparisonSentence(l, label, t)}</strong>
          {l.range_difference_low || l.range_difference_high ? (
            <span className="small muted">
              {" "}
              · {t("results.rangeDiff", { low: l.range_difference_low?.display ?? "—", high: l.range_difference_high?.display ?? "—" })}
            </span>
          ) : null}
          <Zh as="p">{l.note_zh}</Zh>
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
        <Zh>{c.label}</Zh>
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
        <Zh>{c.headline_zh}</Zh>
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
          {rows.map(({ city: c }) => (
            <tr key={c.city_ref} data-city={c.city_ref}>
              <th scope="row">
                <Zh>{c.label}</Zh>
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
                {(c.missing_reasons ?? []).map((m) => (
                  <span key={m.reason} className="small" style={{ display: "block" }}>
                    <Zh>{m.reason_zh}</Zh> ×{m.count}
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
  // 城市点：按这次结果自己的资料包指纹读地理清单（不是当前能力清单的资料包）。清单里每座城的 city_ref 自带地理版本，
  // 只配给同一个 city_ref。E3C 把历史对象绑定的资源版本修好之前，不传“当前碰巧登记”的版本；读不到就不画点。
  const pack = loaded.kind === "data" ? loaded.data.res.meta.data_pack.content_fingerprint : null;
  const geo = useGeography(pack);
  if (loaded.kind === "node") return <div>{loaded.node}</div>;
  const { res, intentView: v, caps } = loaded.data;
  return (
    <ResultsOverview
      res={res}
      caps={caps.data ?? null}
      settings={v.settings ?? null}
      geography={geo.data ?? (geo.isError ? null : undefined)}
      base={`/results/${encodeURIComponent(intentId)}`}
    />
  );
}

/** 第一层：本轮城市比较（只接收数据；页面与开发画廊共用）。base 是这次结果的链接前缀。 */
export function ResultsOverview({
  res,
  caps,
  settings,
  geography = null,
  base,
}: {
  res: ProductResult;
  caps: ProductCapabilities | null;
  settings: RunSettingsView | null;
  /** 地理显示清单（undefined = 还在读，null = 没有）。 */
  geography?: GeographyDisplayBundle | null;
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
  const label = (ref: string) => res.cities?.find((c) => c.city_ref === ref)?.label ?? cityLabelOf(caps, ref);
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
        {!generated && res.not_generated_reason ? <Zh as="p">{res.not_generated_reason}</Zh> : null}
        <p className="small">{t("results.stageLabel", { stage: res.run_stage })}</p>
        <p className="small">{t("results.realCalls", { n: res.real_model_calls })}</p>
        <p className="small">
          {t("app.dataPack")}：{res.meta.data_pack.pack_id}（{res.meta.data_pack.status}）— <Zh>{res.meta.data_pack.note}</Zh>
        </p>
        {settings ? (
          <p className="small">
            {t("preflight.scale", { cities: settings.cities.length, clones: settings.clones_per_city, years: settings.years })}
          </p>
        ) : null}
      </Section>

      {generated ? (
        <>
          <Section title={t("results.mapTitle")}>
            {mode === "globe" ? null : mapSlot}
            <ColorLegend window={w} />
          </Section>
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
          <ZhList items={res.reading_guide_zh} />
        </Section>
      ) : null}
      {res.limitations_zh?.length ? (
        <Section title={t("results.limitations")}>
          <ZhList items={res.limitations_zh} />
        </Section>
      ) : null}
      <Section title={t("results.plannerTitle")}>
        <Zh as="p">{res.planner.description_zh}</Zh>
        <ZhList items={res.sources_zh} />
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
      <MapStage head={head} map={mapSlot} panelLabel={t("results.title")}>
        {body}
      </MapStage>
    </div>
  );
}

function CloneLinks({ res, base, cityRef }: { res: ProductResult; base: string; cityRef?: string }) {
  const { t } = useTranslation();
  const clones = (res.clones ?? []).filter((c) => !cityRef || c.city_ref === cityRef);
  return (
    <ul className="plain stack">
      {clones.map((c) => {
        const months = (c.years ?? []).reduce((n, y) => n + y.months_lived, 0);
        return (
          <li key={c.clone_ref} className="card" data-clone={c.clone_ref} data-category={c.category ?? "none"}>
            <p>
              <strong>{t("clone.title", { city: res.cities?.find((x) => x.city_ref === c.city_ref)?.label ?? c.city_ref, n: c.repeat_index + 1 })}</strong>
              {" · "}
              <Zh>{c.category_zh}</Zh>
            </p>
            <p className="small">{t("city.cloneYears", { years: Math.floor(months / 12), months: months % 12 })}</p>
            <p className="small">
              <Zh>{c.explanation.reason_zh}</Zh>
            </p>
            {c.missing_reason_zh ? (
              <p className="small">
                <Zh>{c.missing_reason_zh}</Zh>
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

function DataNatureView({ nature }: { nature: CityDataNature }) {
  const { t } = useTranslation();
  return (
    <div className="stack" data-nature={nature.pack_status}>
      <Zh as="p">{nature.summary_zh}</Zh>
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
            {nature.named_assumptions.map((a) => (
              <li key={a.note_zh}>
                <Zh>{a.note_zh}</Zh>{" "}
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
            {nature.common_priors.map((p) => (
              <li key={p.fact_id}>
                <Zh>{p.label}</Zh>：<Zh>{p.meaning_zh}</Zh>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {nature.treatment_assumptions_zh?.length ? (
        <>
          <h3>{t("city.treatment")}</h3>
          <ZhList items={nature.treatment_assumptions_zh} />
        </>
      ) : null}
      {nature.model_plan_activities?.length ? (
        <>
          <h3>{t("city.activities")}</h3>
          <ZhList items={nature.model_plan_activities} />
        </>
      ) : null}
      {nature.home_areas != null || nature.work_areas != null ? (
        <p className="small">{t("city.areas", { home: nature.home_areas ?? "—", work: nature.work_areas ?? "—" })}</p>
      ) : null}
      {nature.commute_modes?.length ? (
        <>
          <h3>{t("city.commuteModes")}</h3>
          <ul>
            {nature.commute_modes.map((m) => (
              <li key={m.mode} data-mode-available={m.in_relation}>
                <Zh>{m.mode_zh}</Zh>：{m.in_relation ? t("city.inRelation", { n: m.lines }) : t("city.notInRelation")}
                {m.condition_zh ? (
                  <span className="small muted">
                    {" "}
                    · <Zh>{m.condition_zh}</Zh>
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
            <Zh>{nature.tax.status_zh}</Zh>
          </p>
          {nature.tax.residence_scope_zh ? <Zh as="p">{nature.tax.residence_scope_zh}</Zh> : null}
          <ZhList items={nature.tax.rules_zh} />
          {nature.tax.absent_zh?.length ? (
            <>
              <p className="small">{t("city.taxAbsent")}</p>
              <ZhList items={nature.tax.absent_zh} />
            </>
          ) : null}
          {nature.tax.not_modeled_zh?.length ? (
            <>
              <p className="small">{t("city.taxNotModeled")}</p>
              <ZhList items={nature.tax.not_modeled_zh} />
            </>
          ) : null}
        </>
      ) : null}
      <h3>{t("city.build")}</h3>
      {nature.build_status === "not_available" ? (
        <p className="small">{t("city.buildUnknown")}</p>
      ) : (
        <ul className="small">
          {(nature.build ?? []).map((b) => (
            <li key={b.key}>
              <Zh>{b.label_zh}</Zh>：{b.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ScopeNotes({ notes, cityRef }: { notes: readonly ScopeNote[]; cityRef: string }) {
  const shown = notes.filter((n) => !n.cities?.length || n.cities.includes(cityRef));
  return (
    <ul className="plain stack">
      {shown.map((n, i) => (
        <li key={`${n.topic}.${i}`} data-topic={n.topic}>
          <strong>
            <Zh>{n.title_zh}</Zh>
          </strong>
          <Zh as="p">{n.text_zh}</Zh>
        </li>
      ))}
    </ul>
  );
}

export function CityPage() {
  const { intentId = "", cityRef = "" } = useParams();
  const loaded = useLoaded(intentId);
  if (loaded.kind === "node") return <div>{loaded.node}</div>;
  const { res, boundPreflight, caps } = loaded.data;
  return (
    <CityDetail res={res} caps={caps.data ?? null} cityRef={cityRef} boundPreflight={boundPreflight} base={`/results/${encodeURIComponent(intentId)}`} />
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
  const label = (ref: string) => res.cities?.find((x) => x.city_ref === ref)?.label ?? cityLabelOf(caps, ref);
  const nature = res.data_nature?.find((n) => n.city_ref === cityRef);
  const coverage = res.option_coverage?.find((o) => o.city_ref === cityRef);
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
                  {c.missing_reasons.map((m) => (
                    <li key={m.reason}>
                      <Zh>{m.reason_zh}</Zh> ×{m.count}
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
                <ZhList items={c.notes_zh} />
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
        {nature ? <DataNatureView nature={nature} /> : <p className="muted">{t("common.none")}</p>}
        {coverage ? (
          <>
            <h3>{t("city.coverageTitle")}</h3>
            <p className="small">
              {t("city.coverageLine", { surveyed: coverage.surveyed, resolved: coverage.resolved, unresolved: coverage.unresolved, excluded: coverage.excluded })}
            </p>
            <Zh as="p">{coverage.note_zh}</Zh>
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
  return <CloneDetail res={loaded.data.res} cloneRef={cloneRef} base={`/results/${encodeURIComponent(intentId)}`} />;
}

/** 第三层：一个分身逐年的生活记录。 */
export function CloneDetail({ res, cloneRef, base }: { res: ProductResult; cloneRef: string; base: string }) {
  const { t } = useTranslation();
  const c = res.clones?.find((x) => x.clone_ref === cloneRef);
  if (!c) return <p className="box warn">{t("common.none")}</p>;
  const cityName = res.cities?.find((x) => x.city_ref === c.city_ref)?.label ?? c.city_ref;
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
            <Zh>{c.category_zh}</Zh>
          </strong>
        </p>
        <ExplanationView explanation={c.explanation} />
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
        {c.missing_reason_zh ? <Zh as="p">{c.missing_reason_zh}</Zh> : null}
        {c.valuation_scope?.length ? (
          <>
            <h3>{t("clone.valuationScope")}</h3>
            <ul>
              {c.valuation_scope.map((s) => (
                <li key={s.opportunity_id}>
                  <Zh>{s.label}</Zh>：<Zh>{s.text_zh}</Zh>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {c.event_chain?.length ? (
          <>
            <h3>{t("clone.events")}</h3>
            <ZhList items={c.event_chain} />
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
        {c.years?.length ? c.years.map((y) => <YearReport key={y.year_index} year={y} />) : <p>{t("clone.noYears")}</p>}
      </Section>
      <Section title={t("clone.modelPlan")}>
        <p className="small muted">{t("clone.modelPlanPending")}</p>
      </Section>
    </div>
  );
}
