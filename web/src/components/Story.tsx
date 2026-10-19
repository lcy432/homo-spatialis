// 一个分身的生活：公开说明、事件链（环境记录）、逐年事实、硬要求逐月、L / V / J 的说明，以及“分身自己的说法”的占位。
import {
  HEALTH_MONTH,
  HOUSEHOLD_TASK,
  REFERENCE_PLAN,
  REQUIREMENT_NEED,
  REQUIREMENT_SUMMARY,
  YEAR_STATUS,
  pendingLabel,
} from "../contract/pending-labels";
import type { CloneResult, ProductResult, YearFacts } from "../contract/types";
import { T } from "../copy/zh";
import { ExplanationView, Num, PendingText } from "./common";
import { monthsText } from "./months";
import styles from "./Result.module.css";

const NOT_LIVED = "—";

export function YearFactsView({ year }: { year: YearFacts }) {
  return (
    <section className="card" aria-labelledby={`year-${year.year_index}`}>
      <h3 id={`year-${year.year_index}`} style={{ marginTop: 0 }}>
        {T.yearTitle(year.year_index)}：<PendingText label={pendingLabel(YEAR_STATUS, year.status)} />
      </h3>
      <dl className="facts">
        <dt>生活的月数</dt>
        <dd>{year.months_lived}</dd>
        <dt>现金</dt>
        <dd>
          年初 {year.cash_start.display} → 年末 {year.cash_end ? year.cash_end.display : T.none}
        </dd>
        <dt>这一年付出</dt>
        <dd>{year.paid_total.display}</dd>
        <dt>岗位</dt>
        <dd>{year.jobs?.length ? year.jobs.join("；") : T.none}</dd>
        <dt>住房</dt>
        <dd>{year.housing?.length ? year.housing.join("；") : T.none}</dd>
        <dt>食品</dt>
        <dd>{year.food?.length ? year.food.join("；") : T.none}</dd>
        <dt>实际参加的课程</dt>
        <dd>{year.training?.length ? year.training.join("；") : T.none}</dd>
        <dt>在用的设备</dt>
        <dd>
          {year.devices?.length ? year.devices.map((d) => `${d.label}（${monthsText(d.months_in_service)}）`).join("；") : T.none}
        </dd>
        <dt>人工服务</dt>
        <dd>
          {year.services?.length
            ? year.services
                .map(
                  (s) =>
                    `${s.label}：计划 ${monthsText(s.months_planned)}，实际 ${monthsText(s.months_covered)}，没买到 ${monthsText(s.months_not_obtained)}`,
                )
                .join("；")
            : T.none}
        </dd>
      </dl>
      {year.household?.length ? (
        <div className="table-wrap">
          <table className={styles.storyTable}>
            <caption className="small" style={{ textAlign: "left" }}>
              每月本人做家务的小时数（环境的资源账）
            </caption>
            <thead>
              <tr>
                <th scope="col">家务</th>
                {Array.from({ length: 12 }, (_, i) => (
                  <th key={i} scope="col">
                    {i + 1} 月
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {year.household.map((h) => (
                <tr key={h.task}>
                  <th scope="row">
                    <PendingText label={pendingLabel(HOUSEHOLD_TASK, h.task)} />
                  </th>
                  {h.monthly_hours.map((v, i) => (
                    <td key={i}>
                      {v === NOT_LIVED ? (
                        <>
                          <span aria-hidden="true">—</span>
                          <span className="visually-hidden">{T.noLivedRecord}</span>
                        </>
                      ) : (
                        v
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {year.health_months?.length ? (
        <p className="small">
          健康：
          {year.health_months.map((m, i) => (
            <span key={i}>
              {i ? "、" : ""}
              {i + 1} 月 <PendingText label={pendingLabel(HEALTH_MONTH, m)} />
            </span>
          ))}
        </p>
      ) : null}
      {year.notes?.length ? (
        <ul className="small">
          {year.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function CloneStoryView({ clone, result }: { clone: CloneResult; result: ProductResult }) {
  const lvjLines = (result.reading_guide_zh ?? []).filter((l) => /L 是|J /.test(l));
  return (
    <div>
      <p>
        <strong>{clone.category ? `${clone.category} · ` : ""}</strong>
        {clone.category_zh}
      </p>
      <ExplanationView explanation={clone.explanation} />
      <p>
        J：<Num value={clone.j} />　L：<Num value={clone.l_actual} />　V：<Num value={clone.v_reference} />
      </p>
      {clone.reference_plan ? (
        <p className="small">
          期末估值选中的参考方案：<PendingText label={pendingLabel(REFERENCE_PLAN, clone.reference_plan)} />
        </p>
      ) : null}
      <details className="card">
        <summary>L、V、J 是什么</summary>
        <ul className="small">
          {(lvjLines.length ? lvjLines : (result.reading_guide_zh ?? [])).map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.envRecord}</h2>
        <p className="small muted">{T.envRecordNote}</p>
        {clone.event_chain?.length ? (
          <ol>
            {clone.event_chain.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ol>
        ) : (
          <p>没有需要单独列出的事件。</p>
        )}
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>{T.agentClaims}</h2>
        <p className="muted">{T.agentClaimsPending}</p>
      </section>

      {clone.hard_requirements?.length ? (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>{T.hardSummary}</h2>
          <ul>
            {clone.hard_requirements.map((h) => (
              <li key={h.need}>
                <PendingText label={pendingLabel(REQUIREMENT_NEED, h.need)} />：
                <PendingText label={pendingLabel(REQUIREMENT_SUMMARY, h.summary)} />（满足 {h.months_satisfied} 个月、不满足{" "}
                {h.months_not_satisfied}、无法判断 {h.months_unknown}、没有生活 {h.months_not_lived}）
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {(clone.years ?? []).map((y) => (
        <YearFactsView key={y.year_index} year={y} />
      ))}
    </div>
  );
}
