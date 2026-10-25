// 第 5 步：系统怎样理解我 → 知悉 → 确认画像 → 这次模拟的规模 → 准备模拟。
// * 审阅的永远是服务器上已保存的那一版；进入这一步先把改动存完。改过答案，旧审阅与旧确认都失效。
// * 知悉项的说明按 ack_id 从这份审阅的显示信封取（acknowledgements，交接单第 13.3 节）；信封没有时用公开内容拼：标准开局取
//   能力清单的 start_scenario，留存陈述取审阅里的原文与说明。确认时照旧提交 pending_confirmations 的编号；页面不自动勾选。
// * 职业、资格、兴趣的“我填的”按目录与面板的名称显示（不显示搜索词）；“系统将采用的理解”只读审阅对象。
// * 旧版本草稿（version_changed / removed_section_present）：提示先在草稿页完成服务端迁移，不在前端改写版本字段。
// * 运行选项的范围只读 SessionView.rules.options（不写死）；省略就用服务默认值；城市来自确认画像，不另传。
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { isApiError } from "../../api/errors";
import { useDisplay } from "../../api/queries";
import { useSession } from "../../api/session";
import type {
  ConfirmedProfile,
  ConfirmedProfileSummary,
  InputCatalog,
  ProductCapabilities,
  ProductIssue,
  ProfileReview,
  QualificationReview,
  RunIntentCreateRequest,
  ServiceRules,
  UserInputDraft,
} from "../../contract/types";
import { ApiErrorView, IssueList, Loading, Section, SupportChip, TableWrap, UsageChips } from "../../components/common";
import { PageTitle, StepNav } from "../../components/Layout";
import { DisplayScope, useAcknowledgement } from "../../display/scope";
import { occupationById } from "../../draft/catalog";
import { useDraftEditor } from "../../draft/DraftEditor";
import { Fraction } from "../../draft/exact";
import { useDraftReview } from "../../draft/review";
import { BizNotice, DText, Zh, ZhList } from "../../i18n/biz";
import { formatTime } from "../../i18n/format";
import { useLocale } from "../../i18n/index";
import { SpecialStateText } from "./Steps";

const CENTS = /^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$/;

function cityLabel(caps: ProductCapabilities, ref: string): string {
  return caps.cities.find((c) => c.city_ref === ref)?.label ?? ref;
}

function AckContent({ ackKey, review, caps }: { ackKey: string; review: ProfileReview; caps: ProductCapabilities }) {
  const { t } = useTranslation();
  const fromService = useAcknowledgement(ackKey);
  const serviceText = fromService?.text.zh || fromService?.text.en ? <DText text={fromService.text} as="p" /> : null;
  return (
    <div>
      {serviceText ? (
        <div data-ack-source="service">
          <p className="small muted">{t("review.ackFromService")}</p>
          {serviceText}
        </div>
      ) : null}
      <AckPublicContent ackKey={ackKey} review={review} caps={caps} brief={!!serviceText} />
    </div>
  );
}

/** 公开内容拼出的说明（信封没有说明时是唯一内容；有时只补充原文与开局清单）。 */
function AckPublicContent({ ackKey, review, caps, brief }: { ackKey: string; review: ProfileReview; caps: ProductCapabilities; brief: boolean }) {
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
  if (brief) return null;
  return <p className="small muted">{t("review.ackUnknown", { key: ackKey })}</p>;
}

/** 资格的“我填的”：学历、证书、经验按目录名称显示（没有目录时只显示状态）。 */
function MyQualifications({ d, catalog }: { d: UserInputDraft; catalog: InputCatalog | null }) {
  const { t } = useTranslation();
  const stateText = (st: string | undefined) => t(`states.${st ?? "unanswered"}` as "states.unanswered");
  const edu = d.education;
  const eduLabel = edu?.state === "provided" ? (catalog?.education?.find((o) => o.option_id === edu.option_id)?.label ?? edu.option_id ?? "") : null;
  const cert = d.certificates;
  const certLabels = cert?.state === "provided" ? (cert.selected ?? []).map((id) => catalog?.certificates?.find((c) => c.ability_id === id)?.label ?? id) : null;
  const exp = d.experience;
  const expItems = exp?.state === "provided" ? (exp.entries ?? []) : null;
  return (
    <ul data-my-qualifications>
      <li>{eduLabel !== null ? <>{t("review.eduOf", { label: "" })}<Zh>{eduLabel}</Zh></> : t("review.eduOf", { label: stateText(edu?.state) })}</li>
      <li>
        {certLabels === null ? (
          t("review.certsOf", { list: stateText(cert?.state) })
        ) : certLabels.length === 0 ? (
          t("review.certsNone")
        ) : (
          <>
            {t("review.certsOf", { list: "" })}
            {certLabels.map((l, i) => (
              <span key={`${i}.${l}`}>
                {i ? "、" : ""}
                <Zh>{l}</Zh>
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
                <Zh>{occupationById(catalog, e.concept_id)?.label ?? e.concept_id}</Zh> {t("review.expYears", { years: e.years })}
              </span>
            ))}
          </>
        )}
      </li>
      {(d.abilities ?? []).length ? <li className="muted">{t("facts.legacyAbilities", { n: (d.abilities ?? []).length })}</li> : null}
    </ul>
  );
}

/** 资格的“系统将采用的理解”：来源、换算出的资格与理由、选中职业的开局要求是否满足、还要问的经验题。 */
function SystemQualifications({ q, catalog }: { q: QualificationReview; catalog: InputCatalog | null }) {
  const { t } = useTranslation();
  return (
    <div data-qualifications-source={q.source}>
      <p>
        <Zh>{q.message_zh}</Zh>
      </p>
      <p className="small muted">{t(`review.qualSource_${q.source}` as "review.qualSource_none")}</p>
      {q.granted?.length ? (
        <>
          <p className="small">{t("review.qualGranted")}</p>
          <ul>
            {q.granted.map((g) => (
              <li key={g.ability_id} data-granted={g.ability_id}>
                <Zh>{g.label}</Zh>
                <span className="small muted">
                  {" "}
                  — <Zh>{g.because}</Zh>
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
            {q.requirements.map((r) => (
              <li key={r.ability_id} data-requirement={r.ability_id} data-met={r.met}>
                <Zh>{r.label}</Zh>：<span className={r.met ? "chip accent" : "chip warn"}>{r.met ? t("review.qualMet") : t("review.qualUnmet")}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {q.ask_experience?.length ? (
        <p className="box note" data-ask-experience>
          {t("review.qualAsk")}
          <ul>
            {q.ask_experience.map((id) => (
              <Zh as="li" key={id}>
                {catalog?.experience?.find((x) => x.question_id === id)?.prompt ?? id}
              </Zh>
            ))}
          </ul>
        </p>
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

function versionIssues(issues: readonly ProductIssue[] | undefined): ProductIssue[] {
  return (issues ?? []).filter((i) => i.code === "version_changed" || i.code === "removed_section_present");
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

  return (
    <DisplayScope envelope={reviewDisplay.data}>
    <div data-review-display={reviewDisplay.data ? (reviewDisplay.data.resource_version ?? "unbound") : undefined}>
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
            <div className="box warn" data-migration-hint>
              <p>{t("review.migrateHint")}</p>
              <IssueList issues={vIssues} draftId={draftId} />
              <a className="button" href={`/draft/${encodeURIComponent(draftId)}/place`}>
                {t("review.migrateLink")}
              </a>
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
            mine={
              d.occupation.mode === "catalog" && d.occupation.selected_candidate ? (
                <div data-my-occupation={d.occupation.selected_candidate}>
                  <p>
                    <strong>
                      <Zh>{occupationById(catalog, d.occupation.selected_candidate)?.label ?? d.occupation.selected_candidate}</Zh>
                    </strong>
                  </p>
                  <ul className="small">
                    {(occupationById(catalog, d.occupation.selected_candidate)?.duties ?? []).map((x) => (
                      <Zh as="li" key={x}>
                        {x}
                      </Zh>
                    ))}
                  </ul>
                </div>
              ) : (
                <p lang={d.occupation.language === "en" ? "en" : "zh-CN"}>{d.occupation.raw_text ?? t("states.unanswered")}</p>
              )
            }
            system={
              <div data-occupation-status={review.occupation.status}>
                <p>
                  <Zh>{review.occupation.message_zh}</Zh>
                </p>
                {review.occupation.selected
                  ? (() => {
                      const c = review.occupation.candidates?.find((x) => x.candidate_id === review.occupation.selected);
                      return (
                        <div>
                          <p>
                            <strong>
                              <Zh>{c?.label ?? review.occupation.selected}</Zh>
                            </strong>
                          </p>
                          {c?.duties?.length ? (
                            <>
                              <p className="small muted">{t("review.occupationDuties")}</p>
                              <ul className="small">
                                {c.duties.map((x) => (
                                  <Zh as="li" key={x}>
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
            const panel = f === "taste" ? catalog?.taste : catalog?.leisure;
            const vocab = f === "taste" ? caps.interests.cuisines : caps.interests.leisure_categories;
            const label = (k: string) => panel?.entries.find((e) => e.key === k)?.label ?? vocab.find((v) => v.key === k)?.label ?? k;
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
                          <Zh as="li" key={k}>
                            {label(k)}
                          </Zh>
                        ))}
                      </ul>
                    ) : st === "described" ? (
                      <p>{mine?.text ?? ""}</p>
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

          {confirmed && confirmedStillValid && summary ? (
            <p className="box ok" data-profile-expires={summary.expires_at}>
              {t("review.profileSummary", { first: formatTime(summary.first_confirmed_at, locale), expires: formatTime(summary.expires_at, locale) })}
            </p>
          ) : null}
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
    </DisplayScope>
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
