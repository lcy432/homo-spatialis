// 运行进度：阶段、每个分身已提交的年度、暂停说明、费用、能否恢复。外层流程结束不等于所有分身都完成：分身状态逐个显示。
import { Link } from "react-router";
import type { ProductCapabilities, RunProgress } from "../contract/types";
import { T } from "../copy/zh";
import { ExplanationView, SampleOrigin } from "./common";

const COST_KIND_TEXT: Record<string, string> = {
  offline_mock_zero_cost: "离线 mock：没有网络调用，费用 0",
  estimated_from_quote: "按报价估算（以供应商账单为准）",
};

export function ProgressView({
  progress,
  caps,
  years,
}: {
  progress: RunProgress;
  caps: ProductCapabilities;
  years: number | null;
}) {
  const cityLabel = (ref: string) => caps.cities.find((c) => c.city_ref === ref)?.label ?? ref;
  return (
    <div>
      <p className={progress.stage === "completed" ? "note" : progress.stage === "running" || progress.stage === "queued" ? "note" : "warn"}>
        <strong>{progress.stage_zh}</strong>
        {progress.stage === "queued" ? `　${T.queued}` : progress.stage === "running" ? `　${T.runningNow}` : ""}
      </p>
      {progress.pause ? <ExplanationView explanation={progress.pause} /> : null}

      {(progress.clones ?? []).length ? (
        <div className="table-wrap">
          <table>
            <caption className="small" style={{ textAlign: "left" }}>
              每个分身的状态
            </caption>
            <thead>
              <tr>
                <th scope="col">城市</th>
                <th scope="col">分身</th>
                <th scope="col">状态</th>
                <th scope="col">年度</th>
              </tr>
            </thead>
            <tbody>
              {(progress.clones ?? []).map((c) => (
                <tr key={c.clone_ref} data-clone={c.clone_ref}>
                  <td>{cityLabel(c.city_ref)}</td>
                  <td>{c.clone_ref}</td>
                  <td style={{ textAlign: "left" }}>{c.status_zh}</td>
                  <td>{T.yearsOf(c.years_committed, years)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="small muted">进度记录里还没有分身的状态。</p>
      )}

      <dl className="facts small">
        <dt>费用</dt>
        <dd>
          {COST_KIND_TEXT[progress.cost.kind] ?? progress.cost.kind}；已用调用 {progress.cost.calls_used} / {progress.cost.call_limit} 次
          {progress.cost.estimated_cost ? `；${progress.cost.estimated_cost}` : ""}
        </dd>
        <dt>说明</dt>
        <dd>{progress.cost.note}</dd>
        <dt>能否恢复</dt>
        <dd>
          {progress.resumable ? "可以恢复" : "不能恢复"}：{progress.resume_note}
        </dd>
      </dl>
      {progress.resumable ? <p className="note">{T.noResume}</p> : null}
      {progress.report_available ? (
        <p>
          <Link className="button primary" to={`/runs/${encodeURIComponent(progress.run_id)}/result`}>
            {T.viewResult}
          </Link>
        </p>
      ) : null}
      <SampleOrigin meta={progress.meta} />
    </div>
  );
}
