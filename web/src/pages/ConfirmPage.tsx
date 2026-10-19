// 第 4 步：确认与运行前说明。两个分开的动作：“确认我的信息”（E3 确认画像）→ 预检查 →“查看样例运行”（E5-A 的运行按钮）。
//
// * 自由填写只回显、可以导出草稿；系统审阅、确认、预检查、运行与结果只能用配套样例演示。
// * 确认之后改了答案，确认画像与预检查立刻失效，运行按钮变灰。
// * 提交：幂等键在发送之前生成并存进本标签页；提交返回之前不能再触发新的提交；断网或回执丢失后重试用同一个键。
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useApiContext } from "../api/context";
import { ApiError, isNetworkError } from "../api/types";
import { Dialog } from "../components/Dialog";
import { PageTitle, StepNav, Stepper } from "../components/Layout";
import { PreflightView } from "../components/Preflight";
import { ConfirmedView, DraftEcho, ReviewView } from "../components/Review";
import { IssueList, SampleOrigin, Section } from "../components/common";
import type { ProductCapabilities, ProductIssue, ProfileReview, RunSubmission, UserInputDraft } from "../contract/types";
import { checkMessage } from "../contract/validate";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { metaFromCapabilities } from "../draft/draft";
import { stillBound, useFlow } from "../flow/FlowContext";

function ackText(key: string, review: ProfileReview, caps: ProductCapabilities): ReactNode {
  if (key === "ack:standard_start") {
    return (
      <ul style={{ margin: "0.2em 0" }}>
        {caps.start_scenario.map((s) => (
          <li key={s} style={{ fontWeight: 400 }}>
            {s}
          </li>
        ))}
      </ul>
    );
  }
  if (key.startsWith("ack:retained:")) {
    const ref = key.slice("ack:retained:".length);
    const st = review.statements?.find((s) => s.ref === ref);
    if (st) {
      return (
        <span style={{ fontWeight: 400 }}>
          “{st.raw_text}”：{st.message_zh}
        </span>
      );
    }
  }
  return (
    <span style={{ fontWeight: 400 }}>
      {key}（{T.ackPending}）
    </span>
  );
}

export function downloadDraft(d: UserInputDraft): string | null {
  const check = checkMessage("UserInputDraft", d);
  if (!check.ok) return check.errors.join("；");
  const blob = new Blob([`${JSON.stringify(d, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${d.draft_id}.r${d.revision}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return null;
}

export function ConfirmPage() {
  const { caps, state, loadSampleDraft } = useDraft();
  const { api, scenario, setScenario } = useApiContext();
  const { flow, setAcknowledged, setConfirmed, setPreflight, idempotencyKeyFor, recordRun, resetFlow } = useFlow();
  const navigate = useNavigate();
  const d = state.draft;
  const sampleMode = state.pristineSample && !!scenario.review && scenario.draft === state.sampleId;

  const review = useQuery({
    queryKey: ["review", d.draft_id, d.revision, scenario.id],
    queryFn: () => api.review(d),
    enabled: sampleMode,
  });
  // 过时的审阅（对应的不是当前这一版草稿）不能覆盖新答案
  const reviewData =
    review.data && review.data.draft.draft_id === d.draft_id && review.data.draft.revision === d.revision ? review.data : undefined;

  const confirmed = flow.confirmed && stillBound(flow.confirmed.boundTo, d.draft_id, d.revision) ? flow.confirmed : null;
  const confirmedStale = !!flow.confirmed && !confirmed;
  const preflight = confirmed && flow.preflight && stillBound(flow.preflight.boundTo, d.draft_id, d.revision) ? flow.preflight.preflight : null;

  const pending = reviewData?.pending_confirmations ?? [];
  const allAcked = pending.every((k) => flow.acknowledged.includes(k));
  const blocking = (reviewData?.issues ?? []).filter((i) => i.severity === "error").length;

  const [busy, setBusy] = useState<"confirm" | "submit" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<ProductIssue[] | null>(null);
  const [rejected, setRejected] = useState<ProductIssue[] | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [continueDialog, setContinueDialog] = useState(false);
  const inFlight = useRef(false);

  const doConfirm = async () => {
    setBusy("confirm");
    setProblem(null);
    setRefusal(null);
    try {
      const out = await api.confirm({ draft: d, acknowledged: flow.acknowledged.filter((k) => pending.includes(k)) });
      if (out.status === "refused") {
        setRefusal(out.issues);
        return;
      }
      const bound = { draftId: d.draft_id, revision: d.revision };
      setConfirmed(out.profile, bound);
      const pre = await api.preflight({
        confirmed_id: out.profile.confirmed_id,
        content_fingerprint: out.profile.content_fingerprint,
      });
      setPreflight(pre, bound);
    } catch (e) {
      setProblem(isNetworkError(e) ? "网络断了，确认没有完成。可以再试一次。" : (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const doSubmit = async () => {
    if (inFlight.current || !confirmed || !preflight) return; // 提交返回之前不触发新的提交
    inFlight.current = true;
    setBusy("submit");
    setProblem(null);
    setRejected(null);
    const fp = preflight.preflight_fingerprint;
    const key = idempotencyKeyFor(fp); // 发送之前就存好
    const submission: RunSubmission = {
      meta: metaFromCapabilities(caps),
      confirmed: { confirmed_id: confirmed.profile.confirmed_id, content_fingerprint: confirmed.profile.content_fingerprint },
      preflight: { preflight_id: preflight.preflight_id, preflight_fingerprint: fp },
      idempotency_key: key,
    };
    try {
      const receipt = await api.submit(submission);
      if (receipt.status === "rejected" || !receipt.run_id) {
        setRejected(receipt.reasons ?? []);
        return;
      }
      recordRun(receipt.run_id, fp);
      navigate(`/runs/${encodeURIComponent(receipt.run_id)}`);
    } catch (e) {
      setProblem(isNetworkError(e) ? T.submitRetry : e instanceof ApiError ? e.message : String(e));
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const continueWithSample = (exportFirst: boolean) => {
    if (exportFirst) {
      const err = downloadDraft(d);
      if (err) {
        setExportError(err);
        return;
      }
    }
    setContinueDialog(false);
    setScenario("normal");
    resetFlow();
    loadSampleDraft("02");
  };

  const previousRun = preflight ? flow.runs.find((r) => r.preflightFingerprint === preflight.preflight_fingerprint) : undefined;
  const canRun = !!confirmed && !!preflight && preflight.status === "ready" && !!scenario.receipts;

  return (
    <div>
      <Stepper current={4} />
      <PageTitle>4 · {T.steps[3]}</PageTitle>

      {confirmedStale ? (
        <p className="warn" role="alert">
          {T.invalidated}
        </p>
      ) : null}

      <Section title={T.yourAnswers} id="answers">
        <DraftEcho draft={d} caps={caps} ageText={state.ageText} />
      </Section>

      {!sampleMode ? (
        <section className="card">
          <p className="note">{T.echoNotice}</p>
          <div className="row">
            <button type="button" onClick={() => setExportError(downloadDraft(d))}>
              {T.exportDraft}
            </button>
            <button type="button" onClick={() => setContinueDialog(true)}>
              {T.continueWithSample}
            </button>
          </div>
          {exportError ? <p className="error-box">导出失败（草稿不符合共享结构）：{exportError}</p> : null}
          <Dialog open={continueDialog} onClose={() => setContinueDialog(false)} title={T.continueWithSample}>
            <p>{T.continueWithSampleNote}</p>
            <p>继续之前，这个标签页里你自己的答案会被样例答案替换。要先导出你的答案吗？</p>
            <div className="row">
              <button type="button" className="primary" onClick={() => continueWithSample(true)}>
                先导出，再继续
              </button>
              <button type="button" onClick={() => continueWithSample(false)}>
                直接继续
              </button>
              <button type="button" onClick={() => setContinueDialog(false)}>
                {T.cancel}
              </button>
            </div>
          </Dialog>
        </section>
      ) : (
        <>
          <Section title={T.reviewTitle} id="review">
            {review.isPending ? <p role="status">正在读取审阅……</p> : null}
            {review.isError ? <p className="error-box">读不到审阅：{review.error.message}</p> : null}
            {reviewData ? (
              <>
                <ReviewView review={reviewData} />
                <SampleOrigin meta={reviewData.meta} />
              </>
            ) : null}
          </Section>

          {reviewData ? (
            <section className="card" aria-labelledby="ack-title">
              <h2 id="ack-title" style={{ marginTop: 0 }}>
                {T.ackTitle}
              </h2>
              {pending.map((k) => (
                <label key={k} className="choice top">
                  <input
                    type="checkbox"
                    name={`ack.${k}`}
                    checked={flow.acknowledged.includes(k)}
                    onChange={(e) => setAcknowledged(k, e.target.checked)}
                  />
                  <span>{ackText(k, reviewData, caps)}</span>
                </label>
              ))}
              {blocking ? <p className="error-box">{T.notConfirmable(blocking)}</p> : !allAcked ? <p className="small">{T.ackRequired}</p> : null}
              <button
                type="button"
                className="primary"
                disabled={!reviewData.confirmable || !allAcked || blocking > 0 || busy !== null}
                onClick={doConfirm}
              >
                {busy === "confirm" ? "确认中……" : T.confirmMine}
              </button>
              {refusal ? <IssueList issues={refusal} /> : null}
            </section>
          ) : null}

          {confirmed ? (
            <Section title={T.confirmedTitle} id="confirmed">
              <ConfirmedView profile={confirmed.profile} caps={caps} />
            </Section>
          ) : null}

          {preflight ? (
            <Section title={T.preflightTitle} id="preflight">
              <PreflightView preflight={preflight} />
              <SampleOrigin meta={preflight.meta} />
              {canRun ? (
                <div className="card">
                  <button type="button" className="primary" onClick={doSubmit} disabled={busy !== null} aria-describedby="run-note">
                    {busy === "submit" ? T.submitting : T.runSample}
                  </button>
                  <p id="run-note" className="small muted">
                    E5-A 只演示样例运行：不会调用任何模型，也不会产生费用。重复点击或断网重试都用同一个幂等键，不会开第二次模拟。
                  </p>
                  {previousRun ? (
                    <p className="small">
                      这份预检查已经提交过：<Link to={`/runs/${encodeURIComponent(previousRun.runId)}`}>运行 {previousRun.runId}</Link>
                    </p>
                  ) : null}
                </div>
              ) : preflight.status === "ready" ? (
                <p className="small muted">{T.sampleOnlyHere}</p>
              ) : null}
              {rejected ? (
                <div>
                  <p className="error-box">{T.submitRejected}</p>
                  <IssueList issues={rejected} showEditLink={false} />
                </div>
              ) : null}
            </Section>
          ) : null}
        </>
      )}

      {problem ? (
        <p className="error-box" role="alert">
          {problem}
        </p>
      ) : null}

      <StepNav prev="/fill/3" />
    </div>
  );
}
