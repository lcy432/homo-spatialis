// 第 5 步：系统怎样理解我 → 知悉 → 确认画像 → 本次设置与一次“确认并开始”。
// * 审阅的永远是服务器上已保存的那一版；进入这一步先把改动存完。改过答案，旧审阅与旧确认都失效。
// * 文字按位置取译文：审阅对象的位置以 /review 开头（这份审阅的信封），目录与词表的名称按能力清单的位置。
// * 知悉项的说明按 ack_id 从这份审阅的显示信封取（acknowledgements）。确认时照旧提交 pending_confirmations 的编号；页面不自动勾选。
// * 六维与物质：左边是原始评分（或状态），右边是 E3 的原始评分、原始合计与精确权重（两类数值分开，没生成权重就不补 0）。
// * 确认成功之后是本次设置与一次“确认并开始”（StartPanel）；没有第二个正常开始按钮。
import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useDisplay } from "../../api/queries";
import { useSession } from "../../api/session";
import type {
  ConfirmedProfile,
  ConfirmedProfileSummary,
  InputCatalog,
  ProductIssue,
  QualificationReview,
  UserInputDraft,
} from "../../contract/types";
import { ApiErrorView, IssueCard, IssueList, Loading, Section, SupportChip, TableWrap, UsageChips } from "../../components/common";
import { PageTitle, StepNav } from "../../components/Layout";
import { CityName } from "../../components/RunViews";
import { PendingStartNotice, StartPanel } from "../../components/StartPanel";
import { capsPtr } from "../../display/pointers";
import { DisplayScope, at, useAcknowledgement } from "../../display/scope";
import { occupationById } from "../../draft/catalog";
import { useDraftEditor } from "../../draft/DraftEditor";
import { useDraftReview } from "../../draft/review";
import { allPendingStarts } from "../../draft/startRecord";
import { BizNotice, DText, Zh, ZhList } from "../../i18n/biz";
import { formatTime } from "../../i18n/format";
import { useLocale } from "../../i18n/index";
import { SpecialStateText } from "./Steps";

const R = "/review";

/** 知悉项的说明：按 ack_id 取这份审阅显示信封里的文字（中英都在信封里）；信封没有这一项时只显示编号对应的提示。 */
function AckContent({ ackKey }: { ackKey: string }) {
  const { t } = useTranslation();
  const fromService = useAcknowledgement(ackKey);
  if (!fromService || !(fromService.text.zh || fromService.text.en)) return <p className="small muted">{t("review.ackUnknown", { key: ackKey })}</p>;
  return (
    <div data-ack-source="service">
      <DText text={fromService.text} as="p" />
    </div>
  );
}

/** 资格的“我填的”：学历、证书、经验按目录名称显示（没有目录时只显示状态）。 */
function MyQualifications({ d, catalog }: { d: UserInputDraft; catalog: InputCatalog | null }) {
  const { t } = useTranslation();
  const stateText = (st: string | undefined) => t(`states.${st ?? "unanswered"}` as "states.unanswered");
  const edu = d.education;
  const eduOption = edu?.state === "provided" ? (catalog?.education?.find((o) => o.option_id === edu.option_id) ?? null) : null;
  const cert = d.certificates;
  const certIds = cert?.state === "provided" ? (cert.selected ?? []) : null;
  const exp = d.experience;
  const expItems = exp?.state === "provided" ? (exp.entries ?? []) : null;
  return (
    <ul data-my-qualifications>
      <li>
        {t("review.eduOf", { label: "" })}
        {edu?.state === "provided" ? (
          <Zh pointer={capsPtr.education(catalog, edu.option_id ?? "")}>{eduOption?.label ?? edu.option_id ?? ""}</Zh>
        ) : (
          stateText(edu?.state)
        )}
      </li>
      <li>
        {certIds === null ? (
          t("review.certsOf", { list: stateText(cert?.state) })
        ) : certIds.length === 0 ? (
          t("review.certsNone")
        ) : (
          <>
            {t("review.certsOf", { list: "" })}
            {certIds.map((id, i) => (
              <span key={id}>
                {i ? "、" : ""}
                <Zh pointer={capsPtr.certificate(catalog, id)}>{catalog?.certificates?.find((c) => c.ability_id === id)?.label ?? id}</Zh>
              </span>
            ))}
          </>
        )}
      </li>
      <li>
        {expItems === null ? (
          t("review.expOf", { list: stateText(exp?.state) })
        ) : expItems.length === 0 ? (
          t("review.expNone")
        ) : (
          <>
            {t("review.expOf", { list: "" })}
            {expItems.map((e, i) => (
              <span key={e.concept_id}>
                {i ? "、" : ""}
                <Zh pointer={capsPtr.occupation(catalog, e.concept_id)}>{occupationById(catalog, e.concept_id)?.label ?? e.concept_id}</Zh>{" "}
                {t("review.expYears", { years: e.years })}
              </span>
            ))}
          </>
        )}
      </li>
    </ul>
  );
}

/** 资格的“系统将采用的理解”：来源、换算出的资格与理由、选中职业的开局要求是否满足、还要问的经验题。 */
function SystemQualifications({ q, catalog }: { q: QualificationReview; catalog: InputCatalog | null }) {
  const { t } = useTranslation();
  const Q = `${R}/qualifications`;
  return (
    <div data-qualifications-source={q.source}>
      <p>
        <Zh pointer={`${Q}/message_zh`}>{q.message_zh}</Zh>
      </p>
      <p className="small muted">{t(`review.qualSource_${q.source}` as "review.qualSource_none")}</p>
      {q.granted?.length ? (
        <>
          <p className="small">{t("review.qualGranted")}</p>
          <ul>
            {q.granted.map((g, i) => (
              <li key={g.ability_id} data-granted={g.ability_id}>
                <Zh pointer={at(`${Q}/granted`, i, "label")}>{g.label}</Zh>
                <span className="small muted">
                  {" "}
                  — <Zh pointer={at(`${Q}/granted`, i, "because")}>{g.because}</Zh>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {q.requirements?.length ? (
        <>
          <p className="small">{t("review.qualRequirements")}</p>
          <ul>
            {q.requirements.map((r, i) => (
              <li key={r.ability_id} data-requirement={r.ability_id} data-met={r.met}>
                <Zh pointer={at(`${Q}/requirements`, i, "label")}>{r.label}</Zh>：
                <span className={r.met ? "chip accent" : "chip warn"}>{r.met ? t("review.qualMet") : t("review.qualUnmet")}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {q.ask_experience?.length ? (
        <div className="box note" data-ask-experience>
          <p>{t("review.qualAsk")}</p>
          <ul>
            {q.ask_experience.map((id) => (
              <Zh as="li" key={id} pointer={capsPtr.experiencePrompt(catalog, id)}>
                {catalog?.experience?.find((x) => x.question_id === id)?.prompt ?? id}
              </Zh>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
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

export function ReviewStep() {
  const { t } = useTranslation();
  const { caps, state, draftId, save, flush } = useDraftEditor();
  const { api, view: session, refresh } = useSession();
  const navigate = useNavigate();
  const locale = useLocale();
  const d = state.draft;
  const { reply, review, current, query } = useDraftReview(true);
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<unknown>(null);
  const [refused, setRefused] = useState<ProductIssue[] | null>(null);
  const [confirmed, setConfirmed] = useState<ConfirmedProfile | null>(null);
  const [summary, setSummary] = useState<ConfirmedProfileSummary | null>(null);
  // 这份审阅的显示信封：知悉项说明、作答状态、英文文字都用它绑定的版本（同一 review_id 不变，长时间缓存）
  const reviewDisplay = useDisplay("review", review?.review_id, locale, review?.review_id ?? "", !!review);
  const catalog = caps.input_catalog ?? null;
  // 这份草稿上次“确认并开始”没有收到回复的本地记录（刷新之后确认状态丢了，记录还在）
  const [pendingStarts] = useState(() => allPendingStarts().filter((r) => r.draft_id === draftId));

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
  const canConfirm = !!review && current && review.confirmable && allAcked && !confirming;
  const allIssues = review?.issues ?? [];

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
      if (r.status === "confirmed" && r.confirmed) {
        setConfirmed(r.confirmed);
        setSummary(r.profile_summary ?? null);
      } else setRefused(r.issues ?? []);
    } catch (e) {
      setConfirmError(e);
    } finally {
      setConfirming(false);
    }
  };

  const occIndex = (id: string | null | undefined) => (review?.occupation.candidates ?? []).findIndex((c) => c.candidate_id === id);

  return (
    <DisplayScope envelope={reviewDisplay.data}>
      <div data-review-display={reviewDisplay.data ? (reviewDisplay.data.resource_version ?? "unbound") : undefined}>
        <PageTitle>{t("review.title")}</PageTitle>
        <p>{t("review.intro")}</p>
        <BizNotice />
        {!confirmed ? (
          <PendingStartNotice
            records={pendingStarts}
            onStarted={(id) => {
              void refresh();
              navigate(`/intents/${encodeURIComponent(id)}`);
            }}
          />
        ) : null}

        {!review ? (
          query.error ? (
            <ApiErrorView error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <Loading label={t("review.reviewing")} />
          )
        ) : (
          <>
            {!current ? (
              <p className="small muted" role="status">
                {t("review.staleReview")}
              </p>
            ) : null}
            <p className="small muted">{t("review.reviewedRevision", { rev: reply?.reviewed_revision })}</p>

            <div data-blockers={blockers.length}>
              {blockers.length ? <h2>{t("review.blockers", { n: blockers.length })}</h2> : <p className="box ok">{t("review.noBlockers")}</p>}
              <div className="stack">
                {allIssues.map((i, n) => (
                  <IssueCard key={`${i.code}.${i.field}.${n}`} issue={i} draftId={draftId} pointer={at(`${R}/issues`, n)} />
                ))}
              </div>
            </div>

            {review.summary_zh?.length ? (
              <Section title={t("review.summary")}>
                <ZhList items={review.summary_zh} pointer={`${R}/summary_zh`} />
              </Section>
            ) : null}

            <Compare
              id="cmp-cities"
              title={t("review.cities")}
              mine={
                <ul>
                  {(d.cities.city_refs ?? []).map((r) => (
                    <li key={r}>
                      <CityName caps={caps} cityRef={r} />
                    </li>
                  ))}
                </ul>
              }
              system={
                <ul>
                  {(review.cities ?? []).map((r) => (
                    <li key={r}>
                      <CityName caps={caps} cityRef={r} />
                    </li>
                  ))}
                </ul>
              }
            />
            <Compare
              id="cmp-occupation"
              title={t("review.occupation")}
              mine={
                d.occupation.mode === "catalog" && d.occupation.selected_candidate ? (
                  <div data-my-occupation={d.occupation.selected_candidate}>
                    <p>
                      <strong>
                        <Zh pointer={capsPtr.occupation(catalog, d.occupation.selected_candidate)}>
                          {occupationById(catalog, d.occupation.selected_candidate)?.label ?? d.occupation.selected_candidate}
                        </Zh>
                      </strong>
                    </p>
                    <ul className="small">
                      {(occupationById(catalog, d.occupation.selected_candidate)?.duties ?? []).map((x, j) => (
                        <Zh as="li" key={x} pointer={capsPtr.duty(catalog, d.occupation.selected_candidate ?? "", j)}>
                          {x}
                        </Zh>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p>{t("states.unanswered")}</p>
                )
              }
              system={
                <div data-occupation-status={review.occupation.status}>
                  <p>
                    <Zh pointer={`${R}/occupation/message_zh`}>{review.occupation.message_zh}</Zh>
                  </p>
                  {review.occupation.selected && occIndex(review.occupation.selected) >= 0
                    ? (() => {
                        const ci = occIndex(review.occupation.selected);
                        const c = review.occupation.candidates?.[ci];
                        return (
                          <div>
                            <p>
                              <strong>
                                <Zh pointer={at(`${R}/occupation/candidates`, ci, "label")}>{c?.label ?? review.occupation.selected ?? ""}</Zh>
                              </strong>
                            </p>
                            {c?.duties?.length ? (
                              <>
                                <p className="small muted">{t("review.occupationDuties")}</p>
                                <ul className="small">
                                  {c.duties.map((x, j) => (
                                    <Zh as="li" key={x} pointer={at(`${R}/occupation/candidates`, ci, "duties", String(j))}>
                                      {x}
                                    </Zh>
                                  ))}
                                </ul>
                              </>
                            ) : null}
                          </div>
                        );
                      })()
                    : null}
                </div>
              }
            />
            {review.qualifications || catalog ? (
              <Compare
                id="cmp-qualifications"
                title={t("review.qualifications")}
                mine={<MyQualifications d={d} catalog={catalog} />}
                system={review.qualifications ? <SystemQualifications q={review.qualifications} catalog={catalog} /> : <p>{t("common.none")}</p>}
              />
            ) : null}
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
                    <Zh pointer={`${R}/money/meaning`}>{review.money.meaning}</Zh>
                  </p>
                </div>
              }
            />
            {(["six_dimensions", "material"] as const).map((g) => {
              const r = review[g];
              const entries = d[g].entries ?? {};
              const defs = g === "six_dimensions" ? caps.dimensions : caps.material_leaves;
              const defPtr = (key: string) => (g === "six_dimensions" ? capsPtr.dimension(caps, key) : capsPtr.materialLeaf(caps, key));
              return (
                <Compare
                  key={g}
                  id={`cmp-${g}`}
                  title={g === "six_dimensions" ? t("review.six") : t("review.material")}
                  mine={
                    <div data-my-allocation-mode={d[g].mode ?? "ratings_normalized_v1"}>
                      <p className="small muted">{t("review.ratingsMine")}</p>
                      <ul>
                        {defs.map((def) => {
                          const e = entries[def.key];
                          const v = e?.state === "provided" ? (e.value_text ?? "") : t(`states.${e?.state ?? "unanswered"}` as "states.unanswered");
                          return (
                            <li key={def.key} data-my-rating={def.key}>
                              <Zh pointer={defPtr(def.key)}>{def.label}</Zh>：{v}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  }
                  system={
                    <div data-allocation-status={r.status} data-allocation-rule={r.rule ?? ""}>
                      {r.status === "not_applicable" ? <p className="small muted">{t("review.materialNotApplicable")}</p> : null}
                      {r.rule === "ratings_normalized_v1" ? <p className="small muted">{t("review.ratingsRule")}</p> : null}
                      <ul>
                        {(r.lines ?? []).map((l, li) => (
                          <li key={l.key} data-line={l.key} data-exact={l.exact ?? undefined} data-rating={l.rating_exact ?? undefined}>
                            <Zh pointer={at(`${R}/${g}/lines`, li, "label")}>{l.label}</Zh>：
                            {l.state !== "provided" ? (
                              t(`states.${l.state}` as "states.unanswered")
                            ) : (
                              <>
                                {l.rating_display != null ? <span>{t("review.ratingOf", { r: l.rating_display })}</span> : null}
                                {l.rating_display != null && l.display != null ? " · " : null}
                                {l.display != null ? (
                                  <span>
                                    {t("review.weightOf", { w: l.display })}
                                    {l.exact && l.exact !== l.display ? <span className="small muted">（{t("common.exact")} {l.exact}）</span> : null}
                                  </span>
                                ) : l.rating_display != null ? (
                                  <span className="small muted">{t("review.weightPending")}</span>
                                ) : null}
                              </>
                            )}
                          </li>
                        ))}
                      </ul>
                      {r.rating_total ? (
                        <p className="small" data-rating-total={r.rating_total.exact}>
                          {t("review.ratingTotal", { x: r.rating_total.display })}
                        </p>
                      ) : null}
                      {r.total ? (
                        <p className="small" data-total={r.total.exact}>
                          {t("review.total", { x: r.total.display })}
                        </p>
                      ) : r.status === "incomplete" ? (
                        <p className="small muted">{t("review.weightsIncomplete")}</p>
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
                    return <li key={q.question_id}>{o ? <Zh pointer={capsPtr.healthOption(caps, o.option_id)}>{o.label}</Zh> : t("states.unanswered")}</li>;
                  })}
                </ul>
              }
              system={
                <ul>
                  {review.health.map((h, hi) => (
                    <li key={h.question_id}>
                      {h.option_label ? <Zh pointer={at(`${R}/health`, hi, "option_label")}>{h.option_label}</Zh> : t(`states.${h.answer_state}` as "states.unanswered")}：
                      <Zh pointer={at(`${R}/health`, hi, "meaning")}>{h.meaning}</Zh>
                    </li>
                  ))}
                </ul>
              }
            />
            {(["leisure_interest", "taste"] as const).map((f) => {
              const mine = d[f];
              const r = review[f];
              const panel = f === "taste" ? catalog?.taste : catalog?.leisure;
              const vocab = f === "taste" ? caps.interests.cuisines : caps.interests.leisure_categories;
              const label = (k: string) => panel?.entries.find((e) => e.key === k)?.label ?? vocab.find((v) => v.key === k)?.label ?? k;
              const ptr = (k: string) => (panel ? capsPtr.panelEntry(catalog, f, k) : capsPtr.vocab(caps, f, k));
              const st = mine?.state ?? "unanswered";
              return (
                <Compare
                  key={f}
                  id={`cmp-${f}`}
                  title={f === "taste" ? t("review.taste") : t("review.leisure")}
                  mine={
                    <div data-my-interest-state={st}>
                      {st === "selected" && mine?.selected?.length ? (
                        <ul>
                          {mine.selected.map((k) => (
                            <Zh as="li" key={k} pointer={ptr(k)}>
                              {label(k)}
                            </Zh>
                          ))}
                        </ul>
                      ) : st === "unanswered" ? (
                        <p>{t("states.unanswered")}</p>
                      ) : (
                        <p>
                          <SpecialStateText field={f} state={st} />
                        </p>
                      )}
                    </div>
                  }
                  system={
                    <div data-interest-status={r.status}>
                      <p>
                        <Zh pointer={`${R}/${f}/message_zh`}>{r.message_zh}</Zh>
                      </p>
                      {r.selected?.length ? (
                        <ul>
                          {r.selected.map((k) => (
                            <Zh as="li" key={k} pointer={ptr(k)}>
                              {label(k)}
                            </Zh>
                          ))}
                        </ul>
                      ) : null}
                      {r.unrecognized?.length ? <ZhList items={r.unrecognized} pointer={`${R}/${f}/unrecognized`} /> : null}
                    </div>
                  }
                />
              );
            })}
            {review.statements?.length ? (
              <Section title={t("review.statements")}>
                <ul className="plain stack">
                  {review.statements.map((s, si) => (
                    <li key={s.ref}>
                      “{s.raw_text}”：<Zh pointer={at(`${R}/statements`, si, "message_zh")}>{s.message_zh}</Zh> <SupportChip support={s.support} />
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
                    {review.items.map((it, ii) => (
                      <tr key={it.item}>
                        <th scope="row">
                          <Zh pointer={at(`${R}/items`, ii, "label")}>{it.label}</Zh>
                        </th>
                        <td>{t(`states.${it.answer_state}` as "states.unanswered")}</td>
                        <td>
                          <SupportChip support={it.support} />
                        </td>
                        <td>
                          <UsageChips usage={it.usage} />
                        </td>
                        <td>
                          <Zh pointer={at(`${R}/items`, ii, "note")}>{it.note}</Zh>
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
                  <AckContent ackKey={k} />
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
                  <IssueList issues={refused} draftId={draftId} pointer="/issues" />
                </div>
              ) : null}
            </Section>

            {confirmed && confirmedStillValid && summary ? (
              <p className="box ok" data-profile-expires={summary.expires_at}>
                {t("review.profileSummary", { first: formatTime(summary.first_confirmed_at, locale), expires: formatTime(summary.expires_at, locale) })}
              </p>
            ) : null}
            {confirmed && confirmedStillValid ? (
              <StartPanel
                confirmedId={confirmed.confirmed_id}
                draftId={draftId}
                caps={caps}
                rules={session?.rules ?? null}
                onStarted={(id) => {
                  void refresh();
                  navigate(`/intents/${encodeURIComponent(id)}`);
                }}
              />
            ) : null}
          </>
        )}
        <StepNav draftId={draftId} prev="more" />
      </div>
    </DisplayScope>
  );
}
