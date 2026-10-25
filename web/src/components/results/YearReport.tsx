// 一个分身一年的生活记录：全部来自 E3 公开的 YearFacts（结算记录与资源账），不含模型理由，不写心理活动或因果解释。
// * 年初 / 年末现金与年度已支付总额按计价口径写；年度总额不是月度流水，不画月度现金曲线。
// * 住处片区的典型通勤与实际通勤分开写；“每月通勤费”是每个月付的，不是整组合计。
// * 消费篮子的 “0” 与 “—”（那个月没有生活）分开显示；消费是当月的，不是期末资产。
// * 买到、在用、实际参加是三件事；没成交的购买没有扣款。没有公开的内容不补。
import { useTranslation } from "react-i18next";
import { HEALTH_MONTH, HOUSEHOLD_TASK, YEAR_STATUS, pendingLabel } from "../../contract/pending-labels";
import type { ConvenienceService, YearFacts } from "../../contract/types";
import { Zh, ZhList } from "../../i18n/biz";
import { formatMonths } from "../../i18n/format";
import { useLocale } from "../../i18n/index";
import { Money, PendingText } from "../common";
import { MonthStrip, monthStates } from "./MonthStrip";

function ServiceLines({ lines, monthsLived }: { lines: readonly ConvenienceService[]; monthsLived: number }) {
  const { t } = useTranslation();
  const locale = useLocale();
  return (
    <ul className="plain stack">
      {lines.map((s) => (
        <li key={s.opportunity_id} data-service={s.opportunity_id}>
          <strong>
            <Zh>{s.label}</Zh>
          </strong>
          <p className="small">
            {t("clone.planned")}：{formatMonths(s.months_planned, locale) || t("common.monthsNone")} · {t("clone.obtained")}：
            {formatMonths(s.months_covered, locale) || t("common.monthsNone")} · {t("clone.notObtained")}：
            {formatMonths(s.months_not_obtained, locale) || t("common.monthsNone")}
          </p>
          <MonthStrip label={s.label} states={monthStates(s.months_covered, monthsLived, s.months_planned)} />
        </li>
      ))}
    </ul>
  );
}

export function YearReport({ year }: { year: YearFacts }) {
  const { t } = useTranslation();
  const locale = useLocale();
  const y = year;
  const lived = y.months_lived;
  const status = pendingLabel(YEAR_STATUS, y.status);
  return (
    <article className="card" data-year={y.year_index} aria-labelledby={`year-${y.year_index}`}>
      <h3 id={`year-${y.year_index}`} className="card-title hand">
        {t("clone.year", { n: y.year_index })}
      </h3>
      <p>
        {t("clone.yearStatus")}：<PendingText label={status} /> · {t("clone.monthsLived", { n: lived })}
      </p>

      <h4>{t("clone.cash")}</h4>
      <dl className="facts">
        <dt>{t("clone.cashStart")}</dt>
        <dd>
          <Money value={y.cash_start} />
        </dd>
        <dt>{t("clone.cashEnd")}</dt>
        <dd>{y.cash_end ? <Money value={y.cash_end} /> : t("clone.cashEndNone")}</dd>
        <dt>{t("clone.paidTotal")}</dt>
        <dd>
          <Money value={y.paid_total} />
        </dd>
      </dl>

      {y.jobs?.length || y.training?.length ? (
        <>
          <h4>{t("clone.jobs")}</h4>
          <ZhList items={y.jobs} />
          {y.training?.length ? (
            <>
              <p className="small">{t("clone.training")}</p>
              <ZhList items={y.training} />
            </>
          ) : null}
        </>
      ) : null}

      {y.housing?.length || y.homes?.length ? (
        <>
          <h4>{t("clone.housing")}</h4>
          <ZhList items={y.housing} />
          {y.homes?.length ? (
            <ul className="plain stack">
              {y.homes.map((h) => (
                <li key={`${h.opportunity_id}.${h.months[0]}`} data-home={h.opportunity_id}>
                  <Zh>{h.label}</Zh>：{formatMonths(h.months, locale)}
                  {h.area_label ? (
                    <p className="small">
                      {t("clone.homeArea", { area: "" })}
                      <Zh>{h.area_label}</Zh>
                    </p>
                  ) : null}
                  <p className="small muted">
                    {h.area_typical_roundtrip_minutes != null
                      ? t("clone.homeTypical", { min: h.area_typical_roundtrip_minutes })
                      : t("clone.homeTypicalUnknown")}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}

      {y.commute?.length ? (
        <>
          <h4>{t("clone.commute")}</h4>
          <ul className="plain stack">
            {y.commute.map((c) => (
              <li key={c.months.join(",")} data-commute={c.status}>
                <strong>{formatMonths(c.months, locale)}</strong>：<Zh>{c.status_zh}</Zh>
                {c.home_area_label ? (
                  <span className="small muted">
                    {" "}
                    · <Zh>{c.home_area_label}</Zh>
                  </span>
                ) : null}
                {c.trips?.length ? (
                  <ul className="small">
                    {c.trips.map((tr) => (
                      <li key={`${tr.job_opportunity_id}.${tr.mode}`}>
                        {t("clone.commuteTrip", {
                          job: tr.job_label,
                          mode: tr.mode_zh,
                          min: tr.roundtrip_minutes,
                          visits: tr.onsite_visits,
                          area: tr.work_area_label,
                        })}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {c.monthly_paid ? (
                  <p className="small">
                    {t("clone.commutePaid", { v: "" })}
                    <Money value={c.monthly_paid} />
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.car_access ? (
        <>
          <h4>{t("clone.carAccess")}</h4>
          <p className="small">
            <Zh>{y.car_access.note_zh}</Zh>
          </p>
          {y.car_access.lines?.length ? <ServiceLines lines={y.car_access.lines} monthsLived={lived} /> : null}
        </>
      ) : null}

      {y.food?.length ? (
        <>
          <h4>{t("clone.food")}</h4>
          <ZhList items={y.food} />
        </>
      ) : null}

      {y.goods?.length ? (
        <>
          <h4>{t("clone.goods")}</h4>
          <ul className="plain stack">
            {y.goods.map((g) => (
              <li key={g.opportunity_id} data-goods={g.opportunity_id}>
                <strong>
                  <Zh>{g.label}</Zh>
                </strong>
                <span className="small">
                  {g.bought_month ? ` · ${t("clone.goodsBought", { m: g.bought_month })}` : ""}
                  {g.months_in_service?.length ? ` · ${t("clone.goodsInService", { months: formatMonths(g.months_in_service, locale) })}` : ""}
                  {g.expired ? ` · ${t("clone.goodsExpired")}` : ""} · {t("clone.goodsLeft", { n: g.months_left_after_year })}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.devices?.length ? (
        <>
          <h4>{t("clone.devices")}</h4>
          {y.devices.map((dv) => (
            <MonthStrip key={dv.opportunity_id} label={dv.label} states={monthStates(dv.months_in_service, lived)} />
          ))}
        </>
      ) : null}

      {y.services?.length ? (
        <>
          <h4>{t("clone.services")}</h4>
          <ServiceLines lines={y.services} monthsLived={lived} />
        </>
      ) : null}

      {y.support?.length ? (
        <>
          <h4>{t("clone.support")}</h4>
          <ul className="plain stack">
            {y.support.map((s) => (
              <li key={s.function} data-support={s.function}>
                <strong>
                  <Zh>{s.label_zh}</Zh>
                </strong>
                <p className="small">
                  {t("clone.planned")}：{formatMonths(s.months_planned, locale) || t("common.monthsNone")} · {t("clone.obtained")}：
                  {formatMonths(s.months_obtained, locale) || t("common.monthsNone")} · {t("clone.notObtained")}：
                  {formatMonths(s.months_not_obtained, locale) || t("common.monthsNone")}
                </p>
                <MonthStrip label={s.label_zh} states={monthStates(s.months_obtained, lived, s.months_planned)} />
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.consumption ? (
        <>
          <h4>{t("clone.consumption")}</h4>
          <MonthStrip label={t("clone.consumption")} values={y.consumption.baskets_by_month} />
          <p className="small muted">{t("clone.consumptionNote")}</p>
          {y.consumption.months_not_obtained?.length ? (
            <p className="small">
              {t("clone.notObtained")}：{formatMonths(y.consumption.months_not_obtained, locale)}
            </p>
          ) : null}
        </>
      ) : null}

      {y.activities?.length ? (
        <>
          <h4>{t("clone.activities")}</h4>
          <ul className="plain stack">
            {y.activities.map((a) => (
              <li key={a.opportunity_id} data-activity={a.opportunity_id}>
                <strong>{t("clone.activityLine", { label: a.label, sessions: a.sessions, hours: a.participation_hours })}</strong>
                <p className="small">
                  {t("clone.done")}：{formatMonths(a.months_done, locale) || t("common.monthsNone")} · {t("clone.paused")}：
                  {formatMonths(a.months_paused, locale) || t("common.monthsNone")} · {t("clone.notDone")}：
                  {formatMonths(a.months_not_done, locale) || t("common.monthsNone")}
                </p>
                {a.not_done_zh ? (
                  <p className="small">
                    <Zh>{a.not_done_zh}</Zh>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.purchases_not_made?.length ? (
        <>
          <h4>{t("clone.notMade")}</h4>
          <ul>
            {y.purchases_not_made.map((p) => (
              <li key={`${p.opportunity_id}.${p.month ?? ""}`}>
                <Zh>{p.label}</Zh>
                {p.month ? `（${p.month}）` : ""}：<Zh>{p.status_zh}</Zh>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.device_failures?.length ? (
        <>
          <h4>{t("clone.failures")}</h4>
          <ul>
            {y.device_failures.map((f) => (
              <li key={`${f.opportunity_id}.${f.month}`}>
                <Zh>{f.label}</Zh>（{f.month} · ×{f.units_failed}）：<Zh>{f.replacement_status_zh}</Zh>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {y.health_months?.length ? (
        <>
          <h4>{t("clone.health")}</h4>
          <MonthStrip
            label={t("clone.health")}
            values={y.health_months.map((h, i) => (i + 1 > lived ? "—" : pendingLabel(HEALTH_MONTH, h).text))}
          />
        </>
      ) : null}

      {y.household?.length ? (
        <>
          <h4>{t("clone.household")}</h4>
          {y.household.map((h) => (
            <MonthStrip key={h.task} label={pendingLabel(HOUSEHOLD_TASK, h.task).text} values={h.monthly_hours} />
          ))}
        </>
      ) : null}

      {y.tax ? (
        <p className="small">
          {t("clone.tax")}：<Zh>{y.tax.status_zh}</Zh>
        </p>
      ) : null}

      {y.notes?.length ? (
        <>
          <h4>{t("clone.notes")}</h4>
          <ZhList items={y.notes} />
        </>
      ) : null}
    </article>
  );
}
