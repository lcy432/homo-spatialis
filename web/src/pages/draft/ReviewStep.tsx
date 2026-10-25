// 第 5 步：系统怎样理解我 → 知悉 → 确认画像 → 这次模拟的规模 → 准备模拟。
// * 审阅的永远是服务器上已保存的那一版；进入这一步先把改动存完。改过答案，旧审阅与旧确认都失效。
// * 知悉项的说明用公开内容拼：标准开局取能力清单的 start_scenario，留存陈述取审阅里的原文与说明，兴趣与口味取审阅的说明。
// * 运行选项的范围只读 SessionView.rules.options（不写死）；省略就用服务默认值；城市来自确认画像，不另传。
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { isApiError } from "../../api/errors";
import { useSession } from "../../api/session";
import type {
  ConfirmedProfile,
  ProductCapabilities,
  ProductIssue,
  ProfileReview,
  RunIntentCreateRequest,
  ServiceRules,
} from "../../contract/types";
import { ApiErrorView, IssueList, Loading, Section, SupportChip, TableWrap, UsageChips } from "../../components/common";
import { PageTitle, StepNav } from "../../components/Layout";
import { useDraftEditor } from "../../draft/DraftEditor";
import { Fraction } from "../../draft/exact";
import { useDraftReview } from "../../draft/review";
import { BizNotice, Zh, ZhList } from "../../i18n/biz";

const CENTS = /^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$/;

function cityLabel(caps: ProductCapabilities, ref: string): string {
  return caps.cities.find((c) => c.city_ref === ref)?.label ?? ref;
}

function AckContent({ ackKey, review, caps }: { ackKey: string; review: ProfileReview; caps: ProductCapabilities }) {
  const { t } = useTranslation();
  if (ackKey === "ack:standard_start") {
    return (
      <div>
        <strong>{t("review.ackStart")}</strong>
        <ZhList items={caps.start_scenario} />
      </div>
    );
  }
  if (ackKey.startsWith("ack:retained:")) {
    const ref = ackKey.slice("ack:retained:".length);
    const st = review.statements?.find((s) => s.ref === ref);
    return (
      <div>
        <strong>{t("review.ackRetained")}</strong>
        {st ? (
          <p>
            “{st.raw_text}”：<Zh>{st.message_zh}</Zh>
          </p>
        ) : (
          <p className="small muted">{t("review.ackUnknown", { key: ackKey })}</p>
        )}
      </div>
    );
  }
  if (ackKey === "ack:interests_not_personalized") {
    return (
      <div>
        <strong>{t("review.ackInterests")}</strong>
        <ul>
          <Zh as="li">{review.leisure_interest.message_zh}</Zh>
          <Zh as="li">{review.taste.message_zh}</Zh>
        </ul>
      </div>
    );
  }
  return <p className="small muted">{t("review.ackUnknown", { key: ackKey })}</p>;
}

function Compare({ title, mine, system, id }: { title: string; mine: ReactNode; system: ReactNode; id: string }) {
  const { t } = useTranslation();
  return (
    <section className="card" aria-labelledby={id} data-compare={id}>
      <h3 id={id} className="card-title">
        {title}
      </h3>
      <div className="two-col">
        <div>
          <p className="small muted">{t("review.mine")}</p>
          {mine}
        </div>
        <div>
          <p className="small muted">{t("review.system")}</p>
          {system}
        </div>
      </div>
    </section>
  );
}

function versionIssues(issues: readonly ProductIssue[] | undefined): ProductIssue[] {
  return (issues ?? []).filter((i) => i.field.startsWith("meta.") || i.code === "version_changed");
}

export function ReviewStep() {
  const { t } = useTranslation();
  const { caps, state, dispatch, draftId, save, flush } = useDraftEditor();
  const { api, view: session, refresh } = useSession();
  const navigate = useNavigate();
  const d = state.draft;
  const { reply, review, current, query } = useDraftReview(true);
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<unknown>(null);
  const [refused, setRefused] = useState<ProductIssue[] | null>(null);
  const [confirmed, setConfirmed] = useState<ConfirmedProfile | null>(null);
  const [versionAccepted, setVersionAccepted] = useState(false);

  // 进这一步先把改动存完：审阅只针对已保存的那一版
  useEffect(() => {
    void flush();
  }, [flush]);

  // 审阅换了（答案改过），旧的知悉与确认都作废
  const reviewId = review?.review_id ?? null;
  useEffect(() => {
    setAcks({});
    setRefused(null);
  }, [reviewId]);
  const confirmedStillValid = !!confirmed && confirmed.draft.revision === save.revision && save.kind === "saved";

  const pending = review?.pending_confirmations ?? [];
  const allAcked = pending.every((k) => acks[k]);
  const blockers = (review?.issues ?? []).filter((i) => i.severity === "error");
  const vIssues = versionIssues(review?.issues);
  const canConfirm = !!review && current && review.confirmable && allAcked && !confirming;

  const doConfirm = async () => {
    if (!review || !reply || !canConfirm) return;
    setConfirming(true);
    setConfirmError(null);
    setRefused(null);
    try {
      const r = await api.confirm({
        review_id: review.review_id,
        draft_id: draftId,
        draft_revision: reply.reviewed_revision,
        acknowledged: pending,
      });
      if (r.status === "confirmed" && r.confirmed) setConfirmed(r.confirmed);
      else setRefused(r.issues ?? []);
    } catch (e) {
      setConfirmError(e);
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div>
      <PageTitle>{t("review.title")}</PageTitle>
      <p>{t("review.intro")}</p>
      <BizNotice />

      {!review ? (
        query.error ? (
          <ApiErrorView error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <Loading label={t("review.reviewing")} />
        )
      ) : (
        <>
          {!current ? <p className="small muted" role="status">{t("review.staleReview")}</p> : null}
          <p className="small muted">{t("review.reviewedRevision", { rev: reply?.reviewed_revision })}</p>

          {vIssues.length ? (
            <div className="box warn">
              <p>{t("review.versionChanged")}</p>
              <IssueList issues={vIssues} draftId={draftId} />
              <label className="choice">
                <input type="checkbox" checked={versionAccepted} onChange={(e) => setVersionAccepted(e.target.checked)} />
                {t("review.versionAccept")}
              </label>
              <button type="button" disabled={!versionAccepted} onClick={() => dispatch({ type: "meta/adopt", meta: caps.meta })}>
                {t("review.versionAccept")}
              </button>
            </div>
          ) : null}

          <div data-blockers={blockers.length}>
            {blockers.length ? <h2>{t("review.blockers", { n: blockers.length })}</h2> : <p className="box ok">{t("review.noBlockers")}</p>}
            <IssueList issues={(review.issues ?? []).filter((i) => !vIssues.includes(i))} draftId={draftId} />
          </div>

          {review.summary_zh?.length ? (
            <Section title={t("review.summary")}>
              <ZhList items={review.summary_zh} />
            </Section>
          ) : null}

          <Compare
            id="cmp-cities"
            title={t("review.cities")}
            mine={<ul>{(d.cities.city_refs ?? []).map((r) => <Zh as="li" key={r}>{cityLabel(caps, r)}</Zh>)}</ul>}
            system={<ul>{(review.cities ?? []).map((r) => <Zh as="li" key={r}>{cityLabel(caps, r)}</Zh>)}</ul>}
          />
          <Compare
            id="cmp-occupation"
            title={t("review.occupation")}
            mine={<p lang={d.occupation.language === "en" ? "en" : "zh-CN"}>{d.occupation.raw_text ?? t("states.unanswered")}</p>}
            system={
              <div>
                <p>
                  <Zh>{review.occupation.message_zh}</Zh>
                </p>
                {review.occupation.selected ? (
                  <p>
                    <strong>
                      <Zh>{review.occupation.candidates?.find((c) => c.candidate_id === review.occupation.selected)?.label ?? review.occupation.selected}</Zh>
                    </strong>
                  </p>
                ) : null}
              </div>
            }
          />
          <Compare id="cmp-age" title={t("review.age")} mine={<p>{state.ageText || t("states.unanswered")}</p>} system={<p>{review.age ?? t("common.none")}</p>} />
          <Compare
            id="cmp-cash"
            title={t("review.cash")}
            mine={<p>{d.cash.amount_text ? `${d.cash.amount_text} ${d.cash.currency ?? ""} (${d.cash.price_period ?? ""})` : t("states.unanswered")}</p>}
            system={
              <div>
                {review.money.converted ? (
                  <p>{t("review.moneyConverted", { display: review.money.converted.display, numeraire: review.money.converted.numeraire_id })}</p>
                ) : review.money.status === "blocked" ? (
                  <p>{t("review.moneyBlocked")}</p>
                ) : (
                  <p>{t("common.none")}</p>
                )}
                <p className="small muted">
                  <Zh>{review.money.meaning}</Zh>
                </p>
              </div>
            }
          />
          {(["six_dimensions", "material"] as const).map((g) => {
            const r = review[g];
            const entries = d[g].entries ?? {};
            return (
              <Compare
                key={g}
                id={`cmp-${g}`}
                title={g === "six_dimensions" ? t("review.six") : t("review.material")}
                mine={
                  <ul>
                    {(g === "six_dimensions" ? caps.dimensions : caps.material_leaves).map((def) => {
                      const e = entries[def.key];
                      const v =
                        d[g].mode === "equal_split"
                          ? `${t("priorities.equalSplit")}（${g === "six_dimensions" ? caps.points.equal_split_six : caps.points.equal_split_material}）`
                          : e?.state === "provided"
                            ? (e.value_text ?? "")
                            : t(`states.${e?.state ?? "unanswered"}` as "states.unanswered");
                      return (
                        <li key={def.key}>
                          <Zh>{def.label}</Zh>：{v}
                        </li>
                      );
                    })}
                  </ul>
                }
                system={
                  <div>
                    <ul>
                      {(r.lines ?? []).map((l) => (
                        <li key={l.key} data-line={l.key} data-exact={l.exact ?? undefined}>
                          <Zh>{l.label}</Zh>：{l.display ?? t(`states.${l.state}` as "states.unanswered")}
                          {l.exact && l.exact !== l.display ? <span className="small muted">（{t("common.exact")} {l.exact}）</span> : null}
                        </li>
                      ))}
                    </ul>
                    {r.total ? (
                      <p className="small" data-total={r.total.exact}>
                        {t("review.total", { x: r.total.display })}
                      </p>
                    ) : null}
                  </div>
                }
              />
            );
          })}
          <Compare
            id="cmp-health"
            title={t("review.health")}
            mine={
              <ul>
                {caps.health_questionnaire.questions.map((q) => {
                  const c = d.health.items?.find((i) => i.question_id === q.question_id)?.choice;
                  const o = caps.health_questionnaire.options.find((x) => x.option_id === c);
                  return <li key={q.question_id}>{o ? <Zh>{o.label}</Zh> : t("states.unanswered")}</li>;
                })}
              </ul>
            }
            system={
              <ul>
                {review.health.map((h) => (
                  <li key={h.question_id}>
                    {h.option_label ? <Zh>{h.option_label}</Zh> : t(`states.${h.answer_state}` as "states.unanswered")}：<Zh>{h.meaning}</Zh>
                  </li>
                ))}
              </ul>
            }
          />
          {(["leisure_interest", "taste"] as const).map((f) => {
            const mine = d[f];
            const r = review[f];
            const vocab = f === "taste" ? caps.interests.cuisines : caps.interests.leisure_categories;
            const label = (k: string) => vocab.find((v) => v.key === k)?.label ?? k;
            return (
              <Compare
                key={f}
                id={`cmp-${f}`}
                title={f === "taste" ? t("review.taste") : t("review.leisure")}
                mine={
                  <div>
                    <p>{mine?.text ?? t("states.unanswered")}</p>
                    {mine?.selected?.length ? (
                      <ul>
                        {mine.selected.map((k) => (
                          <Zh as="li" key={k}>
                            {label(k)}
                          </Zh>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                }
                system={
                  <div data-interest-status={r.status}>
                    <p>
                      <Zh>{r.message_zh}</Zh>
                    </p>
                    {r.selected?.length ? (
                      <ul>
                        {r.selected.map((k) => (
                          <Zh as="li" key={k}>
                            {label(k)}
                          </Zh>
                        ))}
                      </ul>
                    ) : null}
                    {r.unrecognized?.length ? <ZhList items={r.unrecognized} /> : null}
                  </div>
                }
              />
            );
          })}
          {review.statements?.length ? (
            <Section title={t("review.statements")}>
              <ul className="plain stack">
                {review.statements.map((s) => (
                  <li key={s.ref}>
                    “{s.raw_text}”：<Zh>{s.message_zh}</Zh> <SupportChip support={s.support} />
                    <UsageChips usage={s.usage} />
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
          <details className="card">
            <summary>{t("review.items")}</summary>
            <TableWrap label={t("review.items")}>
              <table>
                <tbody>
                  {review.items.map((it) => (
                    <tr key={it.item}>
                      <th scope="row">
                        <Zh>{it.label}</Zh>
                      </th>
                      <td>{t(`states.${it.answer_state}` as "states.unanswered")}</td>
                      <td>
                        <SupportChip support={it.support} />
                      </td>
                      <td>
                        <UsageChips usage={it.usage} />
                      </td>
                      <td>
                        <Zh>{it.note}</Zh>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </details>

          <Section title={t("review.acksTitle")}>
            {pending.map((k) => (
              <div key={k} className="card" data-ack={k}>
                <AckContent ackKey={k} review={review} caps={caps} />
                <label className="choice">
                  <input type="checkbox" checked={!!acks[k]} onChange={(e) => setAcks((a) => ({ ...a, [k]: e.target.checked }))} />
                  {t("review.ackLabel")}
                </label>
              </div>
            ))}
            {!canConfirm && !confirmedStillValid ? <p className="small muted">{t("review.notConfirmable")}</p> : null}
            <button type="button" className="primary" disabled={!canConfirm} onClick={() => void doConfirm()} aria-busy={confirming || undefined}>
              {confirming ? t("review.confirming") : t("review.confirm")}
            </button>
            <ApiErrorView error={confirmError} />
            {refused ? (
              <div className="box error">
                <p>{t("review.refused")}</p>
                <IssueList issues={refused} draftId={draftId} />
              </div>
            ) : null}
          </Section>

          {confirmed && confirmedStillValid ? (
            <PreparePanel
              confirmed={confirmed}
              caps={caps}
              onCreated={(id) => {
                void refresh();
                navigate(`/intents/${encodeURIComponent(id)}`);
              }}
              rules={session?.rules ?? null}
            />
          ) : null}
        </>
      )}
      <StepNav draftId={draftId} prev="more" />
    </div>
  );
}

function PreparePanel({
  confirmed,
  caps,
  rules,
  onCreated,
}: {
  confirmed: ConfirmedProfile;
  caps: ProductCapabilities;
  rules: ServiceRules | null;
  onCreated(intentId: string): void;
}) {
  const { t } = useTranslation();
  const { api, view } = useSession();
  const opts = rules?.options ?? null;
  const [clones, setClones] = useState<string>(opts ? String(opts.clones_per_city.default) : "");
  const [years, setYears] = useState<string>(opts ? String(opts.years_default) : "");
  const [maxCost, setMaxCost] = useState<string>(opts ? opts.max_cost.default : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const costOk = useMemo(() => {
    if (!opts) return false;
    if (!CENTS.test(maxCost.trim())) return false;
    const v = new Fraction(maxCost.trim());
    return v.compare(new Fraction(opts.max_cost.minimum)) >= 0 && v.compare(new Fraction(opts.max_cost.maximum)) <= 0;
  }, [maxCost, opts]);
  const clonesN = Number(clones);
  const clonesOk = !!opts && Number.isInteger(clonesN) && clonesN >= opts.clones_per_city.minimum && clonesN <= opts.clones_per_city.maximum;
  const outside = !!opts && confirmed.cities.some((c) => !opts.cities_allowed.includes(c));

  if (!opts) return null;
  const submit = async () => {
    if (busy || !costOk || !clonesOk) return;
    setBusy(true);
    setError(null);
    const req: RunIntentCreateRequest = { confirmed_id: confirmed.confirmed_id };
    if (clonesN !== opts.clones_per_city.default) req.clones_per_city = clonesN;
    if (Number(years) !== opts.years_default) req.years = Number(years);
    if (maxCost.trim() !== opts.max_cost.default) req.max_cost_cny = maxCost.trim();
    try {
      const r = await api.createIntent(req);
      onCreated(r.data.intent_id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const conflictIntent = isApiError(error) && error.code === "intent_conflict" ? view?.open_intent_id : null;
  return (
    <Section title={t("prepare.title")} id="prepare">
      <p>{t("prepare.intro")}</p>
      <BizNotice />
      <p>{t("prepare.cities")}：</p>
      <ul>
        {confirmed.cities.map((c) => (
          <Zh as="li" key={c}>
            {cityLabel(caps, c)}
          </Zh>
        ))}
      </ul>
      {outside ? <p className="box error">{t("prepare.outsideAllowed")}</p> : null}
      <p>
        <label htmlFor="opt-clones">{t("prepare.clones")}</label>{" "}
        <input
          id="opt-clones"
          name="clones_per_city"
          type="number"
          min={opts.clones_per_city.minimum}
          max={opts.clones_per_city.maximum}
          step={1}
          value={clones}
          onChange={(e) => setClones(e.target.value)}
          style={{ width: "6em" }}
          aria-describedby="opt-clones-help"
          aria-invalid={!clonesOk || undefined}
        />
      </p>
      <p className="small muted" id="opt-clones-help">
        {t("prepare.clonesHelp", { min: opts.clones_per_city.minimum, max: opts.clones_per_city.maximum, def: opts.clones_per_city.default })}
      </p>
      <p>
        <label htmlFor="opt-years">{t("prepare.years")}</label>{" "}
        <select id="opt-years" name="years" value={years} onChange={(e) => setYears(e.target.value)}>
          {opts.years_allowed.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>{" "}
        <span className="small muted">{t("prepare.yearsHelp", { list: opts.years_allowed.join(", ") })}</span>
      </p>
      <p>
        <label htmlFor="opt-cost">{t("prepare.maxCost", { currency: opts.max_cost.currency })}</label>{" "}
        <input
          id="opt-cost"
          name="max_cost_cny"
          type="text"
          inputMode="decimal"
          value={maxCost}
          onChange={(e) => setMaxCost(e.target.value)}
          style={{ width: "8em" }}
          aria-describedby="opt-cost-help"
          aria-invalid={!costOk || undefined}
        />
      </p>
      <p className="small muted" id="opt-cost-help">
        {t("prepare.maxCostHelp", { min: opts.max_cost.minimum, max: opts.max_cost.maximum, def: opts.max_cost.default })}
      </p>
      {!costOk ? <p className="small box warn">{t("prepare.maxCostInvalid")}</p> : null}
      {opts.notes_zh?.length ? (
        <details>
          <summary>{t("prepare.serviceNotes")}</summary>
          <ZhList items={opts.notes_zh} />
        </details>
      ) : null}
      <ApiErrorView error={error} />
      {conflictIntent ? (
        <p>
          <a className="tap" href={`/intents/${encodeURIComponent(conflictIntent)}`}>
            {t("home.openIntentAction")}
          </a>
        </p>
      ) : null}
      <button type="button" className="primary" disabled={busy || !costOk || !clonesOk || outside} onClick={() => void submit()}>
        {busy ? t("prepare.preparing") : t("prepare.prepare")}
      </button>
    </Section>
  );
}
