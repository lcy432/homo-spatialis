// /intents/:intentId —— 同一个等待页：后台检查 → 排队 → 生活模拟 → 整理结果 → 完成（DECISIONS/0048、0049）。
// * 进度条与一句当前状态直接照服务的 progress_summary 画：不定长（检查、排队、整理）或按已结算的分身年度数；
//   不按调用次数、不按时钟增长；新的一句覆盖上一句，不累积执行历史。断线时保留最后一份摘要与它的时间。
// * 轮询只读：页面从不自己提交确认、恢复或重开。开始授权在创建意图时就给了，没有第二个开始按钮（服务也不接受 submit）。
//   旧客户端留下的、没有授权的意图（allowed_actions 含 submit）只说明一句并给“取消”，当前版本不再实现两步确认。
// * 授权作废（start.state = void）时显示原因与下一步；allowed_actions 含 start_again 才重新取预览、用户再确认一次（新键）。
// * 取消、恢复、删除都要用户在对话框里确认，只按 allowed_actions；deletion.state 区分删除中与已删除。
// * 文字按位置取这次意图的显示信封（运行意图与进度用这次运行预检查时绑定的版本）。
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { isApiError } from "../api/errors";
import { useCapabilities, useDisplay, useIntent, useKeys } from "../api/queries";
import { useSession } from "../api/session";
import type { ProgressSummary, RunIntentView } from "../contract/types";
import { Dialog } from "../components/Dialog";
import { PageTitle } from "../components/Layout";
import { PreflightView, ProgressView, SettingsView } from "../components/RunViews";
import { StartPanel } from "../components/StartPanel";
import { ApiErrorView, ExplanationView, Loading, Section } from "../components/common";
import { DisplayScope } from "../display/scope";
import { BizNotice, Zh } from "../i18n/biz";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";

type Confirming = "cancel" | "resume" | "delete" | null;

const BUSY_PHASES: ReadonlySet<ProgressSummary["phase"]> = new Set(["checking", "queued", "simulating", "assembling_result"]);

/** 进度条与一句当前状态：全部照服务的摘要画。 */
export function ProgressSummaryView({ summary, offlineSince }: { summary: ProgressSummary; offlineSince: string | null }) {
  const { t } = useTranslation();
  const locale = useLocale();
  const busy = BUSY_PHASES.has(summary.phase) && !offlineSince;
  const counted = summary.mode === "clone_years" || (summary.mode === "finished" && summary.settled_clone_years != null && summary.planned_clone_years != null);
  const settled = summary.settled_clone_years ?? 0;
  const planned = summary.planned_clone_years ?? 0;
  const pct = counted && planned > 0 ? Math.min(100, Math.round((settled / planned) * 100)) : null;
  return (
    <div className="progress-summary" data-phase={summary.phase} data-progress-mode={summary.mode} data-busy={busy}>
      <p className="progress-phase">
        <span className="chip accent" data-phase-chip>
          {t(`wait.phase.${summary.phase}`)}
        </span>
      </p>
      <p className="progress-sentence" role="status" aria-live="polite">
        <strong>
          <Zh pointer="/progress_summary/sentence_zh">{summary.sentence_zh}</Zh>
        </strong>
      </p>
      {summary.mode === "indeterminate" ? (
        <div className={`progress-bar ${busy ? "progress-bar-busy" : ""}`} role="progressbar" aria-label={t("wait.title")} aria-valuetext={t("wait.indeterminate")} data-bar="indeterminate">
          <span className="progress-fill progress-fill-indeterminate" />
        </div>
      ) : (
        <div
          className="progress-bar"
          role="progressbar"
          aria-label={t("wait.title")}
          aria-valuemin={0}
          aria-valuemax={planned || undefined}
          aria-valuenow={counted ? settled : undefined}
          aria-valuetext={counted ? t("wait.cloneYears", { settled, planned }) : t("wait.indeterminate")}
          data-bar={summary.mode}
          data-settled={counted ? settled : undefined}
          data-planned={counted ? planned : undefined}
        >
          <span className="progress-fill" style={{ width: `${pct ?? 0}%` }} />
        </div>
      )}
      {counted ? <p className="small">{t("wait.cloneYears", { settled, planned })}</p> : null}
      <p className="small muted">{t("wait.updated", { time: formatTime(summary.updated_at, locale) })}</p>
      {offlineSince ? (
        <p className="box warn small" data-offline>
          {t("wait.offline", { time: formatTime(offlineSince, locale) })}
        </p>
      ) : null}
      {!busy && !offlineSince && summary.phase !== "finished" ? <p className="small muted">{t("wait.stopped")}</p> : null}
    </div>
  );
}

export function IntentPage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const { intentId = "" } = useParams();
  const { api, refresh, view: session } = useSession();
  const keys = useKeys();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const intent = useIntent(intentId);
  const caps = useCapabilities();
  // 显示信封：运行意图与进度的文字用这次运行预检查时绑定的版本（还没绑定时服务给空）；只读，不续期
  const display = useDisplay("intent", intentId, locale, intent.data?.updated_at ?? "", !!intent.data);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [actionBusy, setActionBusy] = useState(false);

  if (intent.isPending) return <Loading />;
  if (intent.error && !intent.data) return <ApiErrorView error={intent.error} onRetry={() => void intent.refetch()} />;
  const v = intent.data as RunIntentView;
  const allowed = new Set(v.allowed_actions ?? []);
  const capsData = caps.data ?? null;
  const gone = v.stage === "deleted" || v.stage === "deleting" || v.stage === "expired";
  // 轮询失败而手里有上一份：按“最后已知状态”显示，不假装实时
  const offlineSince = intent.error && isApiError(intent.error) && intent.error.kind === "network" ? v.progress_summary.updated_at : null;
  const resultReadable = v.result_available && v.progress_summary.phase === "finished";

  const setView = (nv: RunIntentView) => queryClient.setQueryData(keys.intent(intentId), nv);

  const runAction = async (kind: Exclude<Confirming, null>) => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      if (kind === "cancel") setView(await api.cancel(intentId));
      else if (kind === "resume" && v.run_id) setView(await api.resume(v.run_id));
      else if (kind === "delete" && v.run_id) {
        await api.deleteRun(v.run_id);
        // 删掉的内容不能从缓存里复活：结果与显示信封一起清掉，再重新读意图
        queryClient.removeQueries({ queryKey: ["s"], predicate: (q) => q.queryKey.includes(v.run_id as string) });
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
    <DisplayScope envelope={display.data}>
      <div data-stage={v.stage} data-display-version={display.data ? (display.data.resource_version ?? "unbound") : undefined} data-start-state={v.start?.state ?? "none"}>
        <PageTitle>{t("wait.title")}</PageTitle>
        <BizNotice />

        <Section title={t("intent.stage")}>
          <ProgressSummaryView summary={v.progress_summary} offlineSince={offlineSince} />
          <p className="small muted" data-waiting={v.waiting_for}>
            {t(`intent.waiting.${v.waiting_for}`)}
          </p>
          <p className="small muted">
            <Zh pointer="/stage_zh">{v.stage_zh}</Zh>
          </p>
          <p className="small" data-expiry-basis={v.expiry_basis}>
            {v.expires_at ? t("intent.expiresLine", { time: formatTime(v.expires_at, locale) }) : t("intent.expiresNone")}{" "}
            <span className="muted">（{t(`intent.expiryBasis.${v.expiry_basis}`)}）</span>
          </p>
          {intent.error && !offlineSince ? <ApiErrorView error={intent.error} onRetry={() => void intent.refetch()} /> : null}
          <ExplanationView explanation={v.explanation} pointer="/explanation" />
          {v.support_ref ? <p className="small muted">{t("common.supportRef", { ref: v.support_ref })}</p> : null}
          {v.deletion.state === "deleting" ? (
            <p className="box warn" data-deletion="deleting">
              {t("wait.deleting")}
            </p>
          ) : v.deletion.state === "deleted" ? (
            <p className="box note" data-deletion="deleted">
              {t("wait.deleted")}
            </p>
          ) : gone ? (
            <p className="box warn">{t("intent.goneNote")}</p>
          ) : null}
          {v.stage === "finished" && !v.result_available ? (
            <p className="box warn" data-result-unreadable>
              {t("wait.resultNotReadable")}
            </p>
          ) : null}
          <div className="row">
            <Link className="button" to="/">
              {t("wait.leave")}
            </Link>
            <button type="button" onClick={() => void intent.refetch()}>
              {t("common.refresh")}
            </button>
          </div>
          <p className="small muted">{t("wait.leaveNote")}</p>
        </Section>

        {v.start ? (
          <Section title={t("wait.authorization")} id="authorization">
            <p data-authorization={v.start.state}>
              <strong>
                {v.start.state === "pending"
                  ? t("wait.authPending")
                  : v.start.state === "used"
                    ? t("wait.authUsed", { time: formatTime(v.start.used_at ?? v.start.authorized_at, locale) })
                    : t("wait.authVoid")}
              </strong>
            </p>
            {v.start.state === "void" && v.start.void_reason ? (
              <p className="small" data-void-reason={v.start.void_reason}>
                {t(`wait.voidReason.${v.start.void_reason}`)}
              </p>
            ) : null}
            {v.start.state === "pending" ? <p className="small muted">{t("wait.noSubmitNote")}</p> : null}
          </Section>
        ) : null}

        {allowed.has("start_again") && v.start?.state === "void" ? (
          <StartPanel
            confirmedId={v.confirmed.confirmed_id}
            draftId={null}
            caps={capsData}
            rules={session?.rules ?? null}
            again
            onStarted={(id) => {
              void refresh();
              if (id === intentId) void intent.refetch();
              else navigate(`/intents/${encodeURIComponent(id)}`);
            }}
          />
        ) : null}

        {resultReadable && allowed.has("view_result") ? (
          <Section title={t("intent.viewResult")}>
            <p>{t("intent.finishedNote")}</p>
            <Link className="button primary" to={`/results/${encodeURIComponent(intentId)}`}>
              {t("intent.viewResult")}
            </Link>
          </Section>
        ) : null}

        {allowed.has("submit") ? (
          <p className="box warn" data-legacy-intent>
            {t("wait.legacyIntent")}
          </p>
        ) : null}

        <details className="card" data-inputs>
          <summary>{t("wait.inputs")}</summary>
          <SettingsView settings={v.settings} caps={capsData} />
          {v.preflight ? <PreflightView preflight={v.preflight} caps={capsData} pointer="/preflight" /> : null}
          {v.progress ? <ProgressView progress={v.progress} caps={capsData} pointer="/progress" /> : null}
        </details>

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
              data-action="confirm-action"
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
    </DisplayScope>
  );
}
