// /intents/:intentId —— 运行前说明、确认开始与进度。
// * 按钮只看服务给的 allowed_actions；waiting_for 说明在等谁。阶段是网页流程（不是 C / E / M），“结束”不等于每个分身都成功。
// * 确认开始：用户这一次明确确认生成一个幂等键，绑定这份运行前检查（confirm_with）；在途时不能再点；超时或断线不自动重发——
//   先重新查询状态，用户再点时沿用同一个键与同一份内容（服务按键与意图去重，不会开第二次模拟）。检查换了（preflight_stale）就要
//   重新阅读、再次确认、换新键。
// * 取消、恢复、删除都要用户在对话框里确认，不自动重试；轮询（checking / queued / running / deleting）只读，从不提交或恢复。
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { hasCode, isApiError } from "../api/errors";
import { useCapabilities, useIntent, useKeys } from "../api/queries";
import { useSession } from "../api/session";
import type { RunIntentView, RunReceipt } from "../contract/types";
import { Dialog } from "../components/Dialog";
import { PageTitle } from "../components/Layout";
import { PreflightView, ProgressView, SettingsView } from "../components/RunViews";
import { ApiErrorView, ExplanationView, Loading, Section } from "../components/common";
import { newIdempotencyKey } from "../draft/draft";
import { BizNotice, Zh } from "../i18n/biz";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";

type Confirming = "cancel" | "resume" | "delete" | null;

export function IntentPage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { intentId = "" } = useParams();
  const { api, refresh } = useSession();
  const keys = useKeys();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const intent = useIntent(intentId);
  const caps = useCapabilities();
  const [accept, setAccept] = useState(false);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  const keyRef = useRef<{ confirmWith: string; key: string } | null>(null);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [unknownDelivery, setUnknownDelivery] = useState(false);
  const [receipt, setReceipt] = useState<RunReceipt | null>(null);
  const [stale, setStale] = useState(false);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [actionBusy, setActionBusy] = useState(false);

  if (intent.isPending) return <Loading />;
  if (intent.error && !intent.data) return <ApiErrorView error={intent.error} onRetry={() => void intent.refetch()} />;
  const v = intent.data as RunIntentView;
  const allowed = new Set(v.allowed_actions ?? []);
  const capsData = caps.data ?? null;
  const gone = v.stage === "deleted" || v.stage === "deleting" || v.stage === "expired";

  const setView = (nv: RunIntentView) => queryClient.setQueryData(keys.intent(intentId), nv);

  const submit = async () => {
    if (inFlight.current || !v.confirm_with || !accept) return;
    inFlight.current = true;
    setSending(true);
    setSubmitError(null);
    setUnknownDelivery(false);
    setStale(false);
    if (!keyRef.current || keyRef.current.confirmWith !== v.confirm_with) {
      keyRef.current = { confirmWith: v.confirm_with, key: newIdempotencyKey() };
    }
    try {
      const r = await api.submit(intentId, { confirm_with: v.confirm_with, idempotency_key: keyRef.current.key, accept_scope: true });
      setReceipt(r.data.receipt);
      setView(r.data.intent);
      void refresh();
    } catch (e) {
      if (isApiError(e) && e.kind === "network") {
        // 不知道有没有送到：先查询，不自动重发
        setUnknownDelivery(true);
      } else if (hasCode(e, "preflight_stale")) {
        setStale(true);
        setAccept(false);
        keyRef.current = null;
      } else {
        setSubmitError(e);
      }
      void intent.refetch();
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  const runAction = async (kind: Exclude<Confirming, null>) => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      if (kind === "cancel") setView(await api.cancel(intentId));
      else if (kind === "resume" && v.run_id) setView(await api.resume(v.run_id));
      else if (kind === "delete" && v.run_id) {
        await api.deleteRun(v.run_id);
        void intent.refetch();
      }
      setConfirming(null);
      void refresh();
      void queryClient.invalidateQueries({ queryKey: keys.runs });
    } catch (e) {
      setActionError(e);
      void intent.refetch();
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <div data-stage={v.stage}>
      <PageTitle>{t("intent.title")}</PageTitle>
      <BizNotice />
      <Section title={t("intent.stage")}>
        <p>
          <strong>
            <Zh>{v.stage_zh}</Zh>
          </strong>
        </p>
        <p data-waiting={v.waiting_for}>{t(`intent.waiting.${v.waiting_for}`)}</p>
        {v.stage === "checking" ? <p className="small muted">{t("intent.checkingNote")}</p> : null}
        <p className="small muted">{t("common.lastUpdated", { time: formatTime(v.updated_at, locale) })}</p>
        {intent.error ? <ApiErrorView error={intent.error} onRetry={() => void intent.refetch()} /> : null}
        <button type="button" onClick={() => void intent.refetch()}>
          {t("common.refresh")}
        </button>
        <ExplanationView explanation={v.explanation} />
        {v.support_ref ? <p className="small muted">{t("common.supportRef", { ref: v.support_ref })}</p> : null}
        {gone ? <p className="box warn">{t("intent.goneNote")}</p> : null}
      </Section>

      <SettingsView settings={v.settings} caps={capsData} />

      {v.preflight && (v.stage === "awaiting_confirmation" || v.stage === "blocked" || v.stage === "checking") ? (
        <PreflightView preflight={v.preflight} caps={capsData} />
      ) : null}

      {allowed.has("submit") && v.confirm_with ? (
        <Section title={t("intent.submit")} id="start">
          {stale ? <p className="box warn">{t("intent.stale")}</p> : null}
          <label className="choice top">
            <input type="checkbox" name="accept_scope" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
            {t("intent.acceptScope")}
          </label>
          <button
            type="button"
            className="primary"
            data-action="submit"
            disabled={!accept || sending}
            aria-busy={sending || undefined}
            onClick={() => void submit()}
          >
            {sending ? t("intent.sending") : t("intent.submit")}
          </button>
          {unknownDelivery ? <p className="box warn">{t("intent.submitUnknown")}</p> : null}
          <ApiErrorView error={submitError} />
        </Section>
      ) : null}
      {receipt?.status === "duplicate_same_run" ? <p className="box note">{t("intent.submitDuplicate")}</p> : null}

      {v.progress ? <ProgressView progress={v.progress} caps={capsData} /> : null}

      {v.stage === "finished" ? (
        <Section title={t("intent.viewResult")}>
          <p>{t("intent.finishedNote")}</p>
          {v.result_available && allowed.has("view_result") ? (
            <Link className="button primary" to={`/results/${encodeURIComponent(intentId)}`}>
              {t("intent.viewResult")}
            </Link>
          ) : (
            <p className="muted">{t("intent.noResultYet")}</p>
          )}
        </Section>
      ) : null}

      <div className="row">
        {allowed.has("cancel") ? (
          <button type="button" data-action="cancel" onClick={() => setConfirming("cancel")}>
            {t("intent.cancel")}
          </button>
        ) : null}
        {allowed.has("resume") ? (
          <button type="button" data-action="resume" onClick={() => setConfirming("resume")}>
            {t("intent.resume")}
          </button>
        ) : null}
        {allowed.has("delete") ? (
          <button type="button" className="danger" data-action="delete" onClick={() => setConfirming("delete")}>
            {t("intent.deleteRun")}
          </button>
        ) : null}
        {allowed.has("create_new_intent") ? (
          <button type="button" onClick={() => navigate("/")}>
            {t("intent.newIntent")}
          </button>
        ) : null}
      </div>
      <ApiErrorView error={actionError} />

      <Dialog
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        title={confirming === "cancel" ? t("intent.cancelTitle") : confirming === "resume" ? t("intent.resumeTitle") : t("runs.deleteTitle")}
      >
        <p>{confirming === "cancel" ? t("intent.cancelBody") : confirming === "resume" ? t("intent.resumeBody") : t("runs.deleteBody")}</p>
        <div className="row">
          <button
            type="button"
            className={confirming === "delete" ? "danger primary" : "primary"}
            disabled={actionBusy}
            onClick={() => confirming && void runAction(confirming)}
          >
            {confirming === "cancel" ? t("intent.cancel") : confirming === "resume" ? t("intent.resume") : t("runs.delete")}
          </button>
          <button type="button" onClick={() => setConfirming(null)}>
            {t("common.cancel")}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
