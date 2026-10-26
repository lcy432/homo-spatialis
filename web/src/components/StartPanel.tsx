// 本次设置与一次“确认并开始”（DECISIONS/0048，交接单第 17 节）。
// * 确认画像之后，页面按当前选项取一份只读的设置预览（POST /run-start-preview：不排队、不占次数、不调用模型）：有效设置、
//   规划器与费用上限（停机上限，不是预计费用）、范围说明、必要知悉项、同意身份、此刻能不能开始。选项一变就取新预览，
//   旧预览与旧勾选都作废；语言切换只换文字（同一份回复自带中英），同意身份不变。
// * 用户勾选全部知悉项、点一次“确认并开始”：POST /run-intents 带 start = {start_when_ready, consent_fingerprint, acknowledged,
//   accept, idempotency_key}。发请求之前先把键与原始请求存进本地记录；回复丢了就重发同一个请求、同一个键找回同一个意图。
// * 之后只读等待：服务检查通过后按授权自动开始，页面不发 submit、不恢复、不重开。start_allowed = false 时没有可点的开始按钮。
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { hasCode, isApiError } from "../api/errors";
import { queryRetry, queryRetryDelay, useKeys } from "../api/queries";
import { useSession } from "../api/session";
import type { ProductCapabilities, RunIntentCreateRequest, RunStartPreview, ServiceRules, StartWhenReady } from "../contract/types";
import { Fraction } from "../draft/exact";
import { newIdempotencyKey } from "../draft/draft";
import { type PendingStart, clearPendingStart, loadPendingStart, savePendingStart } from "../draft/startRecord";
import { DisplayScope, at, envelopeOfAttachment } from "../display/scope";
import { BizNotice, Zh, ZhList } from "../i18n/biz";
import { useLocale } from "../i18n/index";
import { CityName } from "./RunViews";
import { ApiErrorView, Section } from "./common";

const CENTS = /^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$/;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function StartPanel({
  confirmedId,
  draftId,
  caps,
  rules,
  onStarted,
  again = false,
}: {
  confirmedId: string;
  draftId: string | null;
  caps: ProductCapabilities | null;
  rules: ServiceRules | null;
  onStarted(intentId: string): void;
  /** 授权作废之后的再次确认（allowed_actions 含 start_again）：新预览、新明确操作、新键。 */
  again?: boolean;
}) {
  const { t } = useTranslation();
  const locale = useLocale();
  const { api, status, view } = useSession();
  const keys = useKeys();
  const opts = rules?.options ?? null;
  const [clones, setClones] = useState<string>(opts ? String(opts.clones_per_city.default) : "");
  const [years, setYears] = useState<string>(opts ? String(opts.years_default) : "");
  const [maxCost, setMaxCost] = useState<string>(opts ? opts.max_cost.default : "");
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState<PendingStart | null>(() => loadPendingStart(confirmedId));
  const [storeFailed, setStoreFailed] = useState(false);
  const inFlight = useRef(false);

  const costOk = useMemo(() => {
    if (!opts) return false;
    if (!CENTS.test(maxCost.trim())) return false;
    const v = new Fraction(maxCost.trim());
    return v.compare(new Fraction(opts.max_cost.minimum)) >= 0 && v.compare(new Fraction(opts.max_cost.maximum)) <= 0;
  }, [maxCost, opts]);
  const clonesN = Number(clones);
  const clonesOk = !!opts && Number.isInteger(clonesN) && clonesN >= opts.clones_per_city.minimum && clonesN <= opts.clones_per_city.maximum;
  const yearsN = Number(years);
  const optionsValid = !!opts && costOk && clonesOk && Number.isInteger(yearsN);

  // 请求里只写用户改过的选项；省略就用服务默认值
  const request = useMemo<RunIntentCreateRequest | null>(() => {
    if (!opts || !optionsValid) return null;
    const req: RunIntentCreateRequest = { confirmed_id: confirmedId };
    if (clonesN !== opts.clones_per_city.default) req.clones_per_city = clonesN;
    if (yearsN !== opts.years_default) req.years = yearsN;
    if (maxCost.trim() !== opts.max_cost.default) req.max_cost_cny = maxCost.trim();
    return req;
  }, [opts, optionsValid, confirmedId, clonesN, yearsN, maxCost]);
  const debounced = useDebounced(request, 400);

  const preview = useQuery({
    queryKey: keys.startPreview(confirmedId, debounced?.clones_per_city ?? null, debounced?.years ?? null, debounced?.max_cost_cny ?? null),
    queryFn: () =>
      api.startPreview({
        confirmed_id: confirmedId,
        clones_per_city: debounced?.clones_per_city ?? null,
        years: debounced?.years ?? null,
        max_cost_cny: debounced?.max_cost_cny ?? null,
      }),
    enabled: status === "active" && !!debounced && !pending,
    staleTime: 60_000,
    retry: queryRetry,
    retryDelay: queryRetryDelay,
  });
  const p: RunStartPreview | undefined = preview.data;
  const fingerprint = p?.consent_fingerprint ?? null;

  // 同意只对这一份预览有效：同意身份变了（设置、报价、知悉规则……）就清掉勾选，让用户重新看过
  useEffect(() => {
    setAcks({});
  }, [fingerprint]);

  const ackIds = (p?.acknowledgements ?? []).map((a) => a.ack_id);
  const allAcked = ackIds.length > 0 && ackIds.every((id) => acks[id]);
  const current = !!p && !!debounced && debounced === request;
  const canStart = !!p && p.start_allowed && current && allAcked && !busy && !pending && !!request;

  const handleReply = async (req: RunIntentCreateRequest) => {
    const r = await api.createIntent(req);
    clearPendingStart(confirmedId);
    setPending(null);
    onStarted(r.data.intent_id);
  };

  const start = async () => {
    if (!canStart || !p || !request || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    const startReq: StartWhenReady = {
      mode: "start_when_ready",
      consent_fingerprint: p.consent_fingerprint,
      acknowledged: ackIds as StartWhenReady["acknowledged"],
      accept: true,
      idempotency_key: newIdempotencyKey(),
    };
    const req: RunIntentCreateRequest = { ...request, start: startReq };
    // 先存记录，再发送：回复丢了也找得回同一次操作
    const rec: PendingStart = { confirmed_id: confirmedId, draft_id: draftId, request: req, created_at: new Date().toISOString() };
    const stored = savePendingStart(rec);
    setStoreFailed(!stored);
    try {
      await handleReply(req);
    } catch (e) {
      if (isApiError(e) && e.kind === "network") {
        // 不知道有没有送到：保留记录，让用户用同一个键找回
        setPending(stored ? rec : null);
        setError(e);
      } else {
        // 服务明确拒绝（同意身份对不上、知悉项不全、付费关着、次数用完、意图冲突）：什么都没建，记录作废
        clearPendingStart(confirmedId);
        setPending(null);
        setError(e);
        if (hasCode(e, "start_consent_stale")) void preview.refetch();
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const recover = async () => {
    if (!pending || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await handleReply(pending.request);
    } catch (e) {
      if (!(isApiError(e) && e.kind === "network") && !hasCode(e, "idempotency_conflict")) {
        clearPendingStart(confirmedId);
        setPending(null);
      }
      setError(e);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  if (!opts) return null;
  const conflictIntent = isApiError(error) && error.code === "intent_conflict" ? view?.open_intent_id : null;

  return (
    <Section title={again ? t("startPanel.againTitle") : t("startPanel.title")} id="start">
      <p>{again ? t("startPanel.againIntro") : t("startPanel.intro")}</p>
      <BizNotice />

      {pending ? (
        <div className="box warn" data-pending-start>
          <p>{t("startPanel.pendingIntro", { time: pending.created_at })}</p>
          <p className="small">{t("startPanel.pendingNote")}</p>
          <div className="row">
            <button type="button" className="primary" data-action="recover-start" disabled={busy} onClick={() => void recover()}>
              {busy ? t("startPanel.recovering") : t("startPanel.recover")}
            </button>
            {hasCode(error, "idempotency_conflict") ? (
              <button
                type="button"
                data-action="discard-start-record"
                onClick={() => {
                  clearPendingStart(confirmedId);
                  setPending(null);
                  setError(null);
                }}
              >
                {t("startPanel.discardRecord")}
              </button>
            ) : null}
          </div>
          <ApiErrorView error={error} />
        </div>
      ) : (
        <>
          <fieldset data-start-options>
            <legend>{t("startPanel.options")}</legend>
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
                <ZhList items={opts.notes_zh} pointer="/rules/options/notes_zh" />
              </details>
            ) : null}
          </fieldset>

          {!optionsValid ? null : preview.isPending || !current ? (
            <p className="small muted" role="status" data-preview="loading">
              {t("startPanel.previewLoading")}
            </p>
          ) : preview.error && !p ? (
            <ApiErrorView error={preview.error} onRetry={() => void preview.refetch()} />
          ) : p ? (
            <DisplayScope envelope={envelopeOfAttachment(p, p.display, locale)}>
              <div data-start-preview={p.consent_fingerprint} data-start-allowed={p.start_allowed}>
                <h3>{t("startPanel.settings")}</h3>
                <dl className="facts" data-settings={p.settings.settings_fingerprint}>
                  <dt>{t("prepare.cities")}</dt>
                  <dd>
                    {p.settings.cities.map((c, i) => (
                      <span key={c}>
                        {i ? "、" : ""}
                        <CityName caps={caps} cityRef={c} />
                      </span>
                    ))}
                  </dd>
                  <dt>{t("prepare.clones")}</dt>
                  <dd className="num">{p.settings.clones_per_city}</dd>
                  <dt>{t("prepare.years")}</dt>
                  <dd className="num">{p.settings.years}</dd>
                  <dt>{t("intent.clonesTotal")}</dt>
                  <dd className="num">{p.settings.clones_total}</dd>
                  <dt>{t("intent.planner")}</dt>
                  <dd>{p.planner.planner_kind === "mock" ? t("intent.plannerMock") : t("intent.plannerLlm")}</dd>
                  <dt>{t("intent.maxCost")}</dt>
                  <dd className="num" data-max-cost={p.settings.max_cost ?? ""}>
                    {p.settings.max_cost ? `${p.settings.max_cost} ${p.settings.currency ?? ""}` : t("common.none")}
                  </dd>
                  <dt>{t("intent.callLimit")}</dt>
                  <dd className="num">{p.settings.call_limit}</dd>
                </dl>
                <p className="small">
                  <Zh pointer="/planner/description_zh">{p.planner.description_zh}</Zh>
                </p>
                <p className="small muted">{p.planner.paid ? t("startPanel.paidNote") : t("preflight.notPaid")}</p>

                <h3>{t("startPanel.scope")}</h3>
                <ZhList items={p.scope_zh} pointer="/scope_zh" />

                <h3>{t("startPanel.acks")}</h3>
                <p className="small muted">{t("startPanel.acksNote")}</p>
                {p.acknowledgements.map((a, i) => (
                  <label key={a.ack_id} className="choice top" data-start-ack={a.ack_id}>
                    <input type="checkbox" name={`start.ack.${a.ack_id}`} checked={!!acks[a.ack_id]} onChange={(e) => setAcks((s) => ({ ...s, [a.ack_id]: e.target.checked }))} />
                    <Zh pointer={at("/acknowledgements", i, "text_zh")}>{a.text_zh}</Zh>
                  </label>
                ))}

                <p className="small" data-quota={`${p.quota.total}/${p.quota.used}/${p.quota.reserved}/${p.quota.available}`}>
                  {t("home.quota", { total: p.quota.total, used: p.quota.used, reserved: p.quota.reserved, available: p.quota.available })}
                </p>
                {!p.start_allowed ? (
                  <p className="box error" data-start-refusal={p.start_refusal ?? ""}>
                    {t(`startPanel.refusal.${p.start_refusal ?? "unknown"}` as "startPanel.refusal.unknown")}
                  </p>
                ) : (
                  <>
                    {!allAcked ? <p className="small muted">{t("startPanel.needAcks")}</p> : null}
                    <button type="button" className="primary" data-action="start" disabled={!canStart} aria-busy={busy || undefined} onClick={() => void start()}>
                      {busy ? t("startPanel.starting") : t("startPanel.start")}
                    </button>
                    <p className="small muted">{t("startPanel.afterStart")}</p>
                  </>
                )}
                {storeFailed ? <p className="small box warn">{t("startPanel.storeFailed")}</p> : null}
              </div>
            </DisplayScope>
          ) : null}
          <ApiErrorView error={error} />
          {conflictIntent ? (
            <p>
              <Link className="tap" to={`/intents/${encodeURIComponent(conflictIntent)}`}>
                {t("home.openIntentAction")}
              </Link>
            </p>
          ) : null}
        </>
      )}
    </Section>
  );
}

/** 首页等处：有没有收到回复的“确认并开始”记录时提示找回（重发同一个请求、同一个键）。 */
export function PendingStartNotice({ records, onStarted }: { records: PendingStart[]; onStarted(intentId: string): void }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gone, setGone] = useState<string[]>([]);
  const shown = records.filter((r) => !gone.includes(r.confirmed_id));
  if (!shown.length) return null;
  const recover = async (rec: PendingStart) => {
    if (busy) return;
    setBusy(rec.confirmed_id);
    setError(null);
    try {
      const r = await api.createIntent(rec.request);
      clearPendingStart(rec.confirmed_id);
      setGone((g) => [...g, rec.confirmed_id]);
      onStarted(r.data.intent_id);
    } catch (e) {
      if (!(isApiError(e) && e.kind === "network") && !hasCode(e, "idempotency_conflict")) {
        clearPendingStart(rec.confirmed_id);
        setGone((g) => [...g, rec.confirmed_id]);
      }
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="box warn" data-pending-start-notice>
      <p>{t("startPanel.pendingHome")}</p>
      <ul className="plain stack">
        {shown.map((rec) => (
          <li key={rec.confirmed_id} className="row">
            <span className="small">{t("startPanel.pendingIntro", { time: rec.created_at })}</span>
            <button type="button" className="primary" data-action="recover-start" disabled={busy !== null} onClick={() => void recover(rec)}>
              {busy === rec.confirmed_id ? t("startPanel.recovering") : t("startPanel.recover")}
            </button>
          </li>
        ))}
      </ul>
      <ApiErrorView error={error} />
    </div>
  );
}
