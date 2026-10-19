// 进度、结果与分身故事三页。网址里带 run_id：刷新或断线之后只查询这次运行，不会重新提交。
import { onlineManager, useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { Link, useParams } from "react-router";
import { useApiContext } from "../api/context";
import { isNetworkError } from "../api/types";
import { PageTitle } from "../components/Layout";
import { ProgressView } from "../components/Progress";
import { ResultView } from "../components/Result";
import { CloneStoryView } from "../components/Story";
import { T } from "../copy/zh";
import { useDraft } from "../draft/DraftContext";
import { useFlow } from "../flow/FlowContext";

function useOnline(): boolean {
  return useSyncExternalStore(
    (cb) => onlineManager.subscribe(cb),
    () => onlineManager.isOnline(),
    () => true,
  );
}

const LIVE_STAGES = new Set(["queued", "running"]);

export function ProgressPage() {
  const { runId = "" } = useParams();
  const { api } = useApiContext();
  const { caps } = useDraft();
  const { flow } = useFlow();
  const online = useOnline();
  const progress = useQuery({
    queryKey: ["progress", runId],
    queryFn: () => api.progress(runId),
    // 正在排队或执行时定时重查；停下、暂停或结束之后不自动重查（有“刷新状态”按钮）
    refetchInterval: (q) => (q.state.data && LIVE_STAGES.has(q.state.data.stage) ? 3000 : false),
    retry: (n, e) => isNetworkError(e) && n < 3,
    retryDelay: (n) => Math.min(1000 * 2 ** n, 8000),
  });
  const known = flow.runs.find((r) => r.runId === runId);
  const years = known && flow.preflight?.preflight.preflight_fingerprint === known.preflightFingerprint ? flow.preflight.preflight.years : null;
  return (
    <div>
      <PageTitle>{T.progressTitle}</PageTitle>
      <p className="small muted">运行编号：{runId}</p>
      {!online ? (
        <p className="warn" role="status">
          {T.offline}
        </p>
      ) : null}
      {progress.isPending ? <p role="status">正在查询进度……</p> : null}
      {progress.isError ? (
        <div className={isNetworkError(progress.error) ? "warn" : "error-box"} role="alert">
          <p>{isNetworkError(progress.error) ? T.offline : `查不到这次运行的进度：${progress.error.message}`}</p>
        </div>
      ) : null}
      {progress.data ? <ProgressView progress={progress.data} caps={caps} years={years} /> : null}
      <p>
        <button type="button" onClick={() => progress.refetch()} disabled={progress.isFetching}>
          {progress.isFetching ? "查询中……" : T.refresh}
        </button>
      </p>
      <p>
        <Link className="tap" to="/fill/4">
          {T.back}到确认页
        </Link>
      </p>
    </div>
  );
}

export function ResultPage() {
  const { runId = "" } = useParams();
  const { api } = useApiContext();
  const result = useQuery({ queryKey: ["result", runId], queryFn: () => api.result(runId), staleTime: Infinity });
  return (
    <div>
      <PageTitle>{T.resultTitle}</PageTitle>
      <p className="small muted">运行编号：{runId}</p>
      {result.isPending ? <p role="status">正在读取结果……</p> : null}
      {result.isError ? (
        <p className={isNetworkError(result.error) ? "warn" : "error-box"} role="alert">
          {isNetworkError(result.error) ? T.offline : `读不到结果：${result.error.message}`}
        </p>
      ) : null}
      {result.data ? <ResultView result={result.data} /> : null}
      <p>
        <Link className="tap" to={`/runs/${encodeURIComponent(runId)}`}>{T.back}到运行进度</Link>
      </p>
    </div>
  );
}

export function ClonePage() {
  const { runId = "", cloneRef = "" } = useParams();
  const { api } = useApiContext();
  const result = useQuery({ queryKey: ["result", runId], queryFn: () => api.result(runId), staleTime: Infinity });
  const clone = result.data?.clones?.find((c) => c.clone_ref === cloneRef);
  const cityLabel = clone ? (result.data?.cities?.find((c) => c.city_ref === clone.city_ref)?.label ?? clone.city_ref) : "";
  return (
    <div>
      <PageTitle>
        {T.storyTitle}：{cloneRef}
        {cityLabel ? `（${cityLabel}）` : ""}
      </PageTitle>
      {result.isPending ? <p role="status">正在读取……</p> : null}
      {result.isError ? <p className="error-box">读不到结果：{result.error.message}</p> : null}
      {result.data && !clone ? <p className="error-box">这次运行的结果里没有分身 {cloneRef}。</p> : null}
      {result.data && clone ? <CloneStoryView clone={clone} result={result.data} /> : null}
      <p>
        <Link className="tap" to={`/runs/${encodeURIComponent(runId)}/result`}>{T.back}到城市比较</Link>
      </p>
    </div>
  );
}
