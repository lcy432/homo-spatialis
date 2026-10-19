// 城市比较与分身列表：只展示 E3 的投影（原计划人数 N = C + E + M、均值分母 C、缺失范围分母 C + M、空值保持为空）。
// 不排序、不加胜负徽章；不在前端计算 J、均值、比例或范围——数字一律取 E3 的显示值，范围条只用精确值定位。
import { Link } from "react-router";
import Fraction from "fraction.js";
import { REQUIREMENT_NEED, pendingLabel } from "../contract/pending-labels";
import type { CityResult, CloneResult, ComparisonLine, ExactNumber, ProductResult } from "../contract/types";
import { CATEGORY_LETTER_TEXT, T } from "../copy/zh";
import { ExplanationView, Num, PendingText, SampleOrigin } from "./common";
import styles from "./Result.module.css";

function Units({ complete, exits, missing }: { complete: number; exits: number; missing: number }) {
  const units = [
    ...Array.from({ length: complete }, () => "C"),
    ...Array.from({ length: exits }, () => "E"),
    ...Array.from({ length: missing }, () => "M"),
  ];
  return (
    <ul className={styles.units} aria-label="每个分身的类别">
      {units.map((u, i) => (
        <li key={i} className={`${styles.unit} ${u === "C" ? styles.c : u === "E" ? styles.e : styles.m}`} title={CATEGORY_LETTER_TEXT[u]}>
          {u}
          <span className="visually-hidden">{CATEGORY_LETTER_TEXT[u]}</span>
        </li>
      ))}
    </ul>
  );
}

function toNumber(x: ExactNumber): number {
  return new Fraction(x.exact).valueOf();
}

/** 0–1 刻度上的模拟缺失范围。位置按精确值换算（只用于画图）；文字取 E3 的显示值。 */
export function RangeBar({ lower, upper }: { lower: ExactNumber; upper: ExactNumber }) {
  const lo = Math.min(Math.max(toNumber(lower), 0), 1);
  const hi = Math.min(Math.max(toNumber(upper), 0), 1);
  const x = (v: number) => 4 + v * 92;
  return (
    <svg className={styles.range} viewBox="0 0 100 32" role="img" aria-label={`${T.rangeTitle}：${lower.display} 到 ${upper.display}`}>
      <line x1={x(0)} x2={x(1)} y1={16} y2={16} stroke="#9aa0a8" strokeWidth={1} />
      <line x1={x(0)} x2={x(0)} y1={11} y2={21} stroke="#9aa0a8" strokeWidth={1} />
      <line x1={x(1)} x2={x(1)} y1={11} y2={21} stroke="#9aa0a8" strokeWidth={1} />
      <text x={x(0)} y={30} fontSize={5} textAnchor="middle" fill="#555a63">
        0
      </text>
      <text x={x(1)} y={30} fontSize={5} textAnchor="middle" fill="#555a63">
        1
      </text>
      {hi - lo < 0.004 ? (
        <circle cx={x(lo)} cy={16} r={2.2} fill="#1d5bb8" />
      ) : (
        <rect x={x(lo)} y={12} width={Math.max(x(hi) - x(lo), 1)} height={8} rx={2} fill="#1d5bb8" opacity={0.75} />
      )}
    </svg>
  );
}

export function CityResultCard({ city }: { city: CityResult }) {
  return (
    <section className="card" aria-labelledby={`city-${city.city_ref}`} data-city={city.city_ref}>
      <h3 id={`city-${city.city_ref}`} style={{ marginTop: 0 }}>
        {city.label} <span className="chip warn">{T.sampleCity}</span>
      </h3>
      <p>{city.headline_zh}</p>
      <p className="small">{T.plannedLine(city.planned, city.complete, city.exits, city.missing)}</p>
      <Units complete={city.complete} exits={city.exits} missing={city.missing} />

      <div data-testid="mean">
        {city.mean_j ? (
          <>
            <p style={{ marginBottom: 0 }}>{T.meanOf(city.complete)}</p>
            <p className={styles.mean} style={{ margin: 0 }}>
              <Num value={city.mean_j} />
            </p>
            <p className="small">{T.meanDenominator(city.planned)}</p>
            <details>
              <summary className="small">{T.lMeans}</summary>
              <p className="small">
                L：<Num value={city.mean_l} />　V：<Num value={city.mean_v} />
              </p>
            </details>
          </>
        ) : (
          <p className={styles.mean}>{T.noMean}</p>
        )}
      </div>

      <div data-testid="range">
        <h4>{T.rangeTitle}</h4>
        {city.range_lower && city.range_upper ? (
          <>
            <RangeBar lower={city.range_lower} upper={city.range_upper} />
            <p className="small">
              [<Num value={city.range_lower} />, <Num value={city.range_upper} />]　{T.rangeDenominator(city.range_denominator)}
            </p>
          </>
        ) : (
          <p>{T.noRange}</p>
        )}
      </div>

      <dl className="facts small">
        <dt>{T.exitShare}</dt>
        <dd>
          <Num value={city.exit_share} />
        </dd>
        <dt>{T.missingShare}</dt>
        <dd>
          <Num value={city.missing_share} />
        </dd>
      </dl>

      {(city.missing_reasons ?? []).length ? (
        <>
          <h4>{T.missingReasons}</h4>
          <ul className="small">
            {(city.missing_reasons ?? []).map((m) => (
              <li key={m.reason}>
                {m.reason_zh}：{m.count} 个
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {(city.hard_requirements ?? []).length ? (
        <>
          <h4>{T.hardSummary}</h4>
          <ul className="small">
            {(city.hard_requirements ?? []).map((h) => (
              <li key={h.need}>
                <PendingText label={pendingLabel(REQUIREMENT_NEED, h.need)} />：原计划 {h.planned} 个，核验了 {h.checked} 个（每个月都满足{" "}
                {h.satisfied_every_month}、生活过的月份都满足 {h.satisfied_in_lived_months}、有月份不满足 {h.violated}、无法判断 {h.unknown}），
                未核验 {h.not_checked} 个
              </li>
            ))}
          </ul>
          <p className="small muted">{T.hardNotChecked}</p>
        </>
      ) : null}

      {(city.notes_zh ?? []).length ? (
        <ul className="small">
          {(city.notes_zh ?? []).map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function ComparisonView({ line, labelOf }: { line: ComparisonLine; labelOf: (ref: string) => string }) {
  return (
    <div className="card">
      <p className="small muted">{T.comparisonCities(labelOf(line.city_a), labelOf(line.city_b))}</p>
      <p>{line.note_zh}</p>
      {line.range_difference_low && line.range_difference_high ? (
        <p className="small">
          {T.rangeDiff}：[<Num value={line.range_difference_low} />, <Num value={line.range_difference_high} />]
        </p>
      ) : null}
    </div>
  );
}

export function CloneSummary({ clone, runId, labelOf }: { clone: CloneResult; runId: string; labelOf: (ref: string) => string }) {
  return (
    <li className="card" data-clone={clone.clone_ref}>
      <p style={{ marginTop: 0 }}>
        <strong>{clone.clone_ref}</strong>（{labelOf(clone.city_ref)}）——
        {clone.category ? <strong>{clone.category} · </strong> : null}
        {clone.category_zh}
      </p>
      <ExplanationView explanation={clone.explanation} />
      <p className="small">
        J：<Num value={clone.j} />　L：<Num value={clone.l_actual} />　V：<Num value={clone.v_reference} />
      </p>
      <Link className="tap" to={`/runs/${encodeURIComponent(runId)}/clones/${encodeURIComponent(clone.clone_ref)}`}>{T.viewLife}</Link>
    </li>
  );
}

export function ResultView({ result }: { result: ProductResult }) {
  const labelOf = (ref: string) => result.cities?.find((c) => c.city_ref === ref)?.label ?? ref;
  if (result.evaluation_status === "not_generated") {
    return (
      <div>
        <p className="warn">{T.notGenerated}</p>
        <p>{result.not_generated_reason}</p>
        <p className="small muted">运行阶段：{result.run_stage}</p>
        <SampleOrigin meta={result.meta} />
      </div>
    );
  }
  const byCity = new Map<string, CloneResult[]>();
  for (const c of result.clones ?? []) byCity.set(c.city_ref, [...(byCity.get(c.city_ref) ?? []), c]);
  return (
    <div>
      <h2>{T.cityCards}</h2>
      <div className={styles.cityGrid}>
        {(result.cities ?? []).map((c) => (
          <CityResultCard key={c.city_ref} city={c} />
        ))}
      </div>

      {(result.comparisons ?? []).length ? (
        <section>
          <h2>{T.comparisons}</h2>
          {(result.comparisons ?? []).map((l) => (
            <ComparisonView key={`${l.city_a}|${l.city_b}`} line={l} labelOf={labelOf} />
          ))}
        </section>
      ) : null}

      <section>
        <h2>{T.clones}</h2>
        {[...byCity.entries()].map(([city, clones]) => (
          <div key={city}>
            <h3>{labelOf(city)}</h3>
            <ul style={{ listStyle: "none", padding: 0 }}>
              {clones.map((c) => (
                <CloneSummary key={c.clone_ref} clone={c} runId={result.run_id} labelOf={labelOf} />
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.readingGuide}</h2>
        <ul>
          {(result.reading_guide_zh ?? []).map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.limitations}</h2>
        <ul>
          {(result.limitations_zh ?? []).map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
        <h3>{T.sources}</h3>
        <ul className="small">
          {(result.sources_zh ?? []).map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
        {(result.option_coverage ?? []).length ? (
          <>
            <h3>{T.optionCoverage}</h3>
            <ul className="small">
              {(result.option_coverage ?? []).map((o) => (
                <li key={o.city_ref}>
                  {labelOf(o.city_ref)}：调查 {o.surveyed} 个，已解决 {o.resolved}、未解决 {o.unresolved}、排除 {o.excluded}。{o.note_zh}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <h3>{T.planner}</h3>
        <p className="small">
          {result.planner.description_zh}　{T.realCalls(result.real_model_calls)}
        </p>
      </section>
      <SampleOrigin meta={result.meta} />
    </div>
  );
}
