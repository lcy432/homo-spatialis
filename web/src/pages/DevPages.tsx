// 开发模式的样例面板：切换样例场景、模拟网络条件，并直达各场景的页面。另有单条样例的展示页（用确认页、结果页同一组组件呈现）。
import { useSyncExternalStore } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useApiContext } from "../api/context";
import { SCENARIOS, type Scenario } from "../api/scenarios";
import { getTransport, setTransport, subscribeTransport } from "../api/transport";
import { PageTitle } from "../components/Layout";
import { PreflightView } from "../components/Preflight";
import { ProgressView } from "../components/Progress";
import { ResultView } from "../components/Result";
import { ConfirmedView, ReviewView } from "../components/Review";
import { YearFactsView } from "../components/Story";
import { IssueList, SampleOrigin } from "../components/common";
import { SAMPLE_IDS, sample } from "../contract/samples";
import type {
  ConfirmedProfile,
  ProductPreflight,
  ProductResult,
  ProfileReview,
  RunProgress,
  RunReceipt,
  YearFacts,
} from "../contract/types";
import { assertMessage } from "../contract/validate";
import { useDraft } from "../draft/DraftContext";
import { useFlow } from "../flow/FlowContext";

function useTransport() {
  return useSyncExternalStore(subscribeTransport, getTransport, getTransport);
}

function entryPath(s: Scenario): string {
  switch (s.entry) {
    case "fill":
      return "/fill/1";
    case "progress":
      return `/runs/${encodeURIComponent(s.runId ?? "")}`;
    case "result":
      return `/runs/${encodeURIComponent(s.runId ?? "")}/result`;
    case "fragment":
      return `/dev/samples/${s.fragment}`;
    default:
      return `/dev/samples/${s.review ?? s.preflight ?? s.confirmed ?? ""}`;
  }
}

export function DevSamples() {
  const { scenario, setScenario } = useApiContext();
  const { loadSampleDraft, clearDraft } = useDraft();
  const { resetFlow } = useFlow();
  const navigate = useNavigate();
  const transport = useTransport();
  const open = (s: Scenario) => {
    setScenario(s.id);
    resetFlow();
    if (s.draft) loadSampleDraft(s.draft);
    else if (s.entry === "fill") clearDraft();
    navigate(entryPath(s));
  };
  return (
    <div>
      <PageTitle>样例面板（开发用）</PageTitle>
      <p className="note">
        这里的每个场景都只用 E3 的公共样例（contracts/product/v2/samples/）。只有“正常闭环”和“答案改过之后提交”能从第 1 步走到运行；其余场景直接打开对应的页面。
        当前场景：<strong>{scenario.title}</strong>
      </p>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>网络条件（只模拟网络，不改变回复内容）</h2>
        <p className="row">
          <label htmlFor="latency">每次请求的延迟</label>
          <select id="latency" value={transport.latencyMs} onChange={(e) => setTransport({ latencyMs: Number(e.target.value) })}>
            {[0, 200, 800, 2000].map((v) => (
              <option key={v} value={v}>
                {v} 毫秒
              </option>
            ))}
          </select>
        </p>
        <label className="choice">
          <input type="checkbox" checked={transport.offline} onChange={(e) => setTransport({ offline: e.target.checked })} />
          模拟断网（所有请求失败）
        </label>
        <label className="choice">
          <input
            type="checkbox"
            checked={transport.dropNextReceipt}
            onChange={(e) => setTransport({ dropNextReceipt: e.target.checked })}
          />
          下一次提交“已送达但回执丢失”
        </label>
      </section>

      <section>
        <h2>场景</h2>
        <ul style={{ listStyle: "none", padding: 0 }}>
          {SCENARIOS.map((s) => (
            <li key={s.id} className="card">
              <p style={{ marginTop: 0 }}>
                <strong>{s.title}</strong>
                {s.id === scenario.id ? <span className="chip accent"> 当前 </span> : null}
              </p>
              <p className="small">{s.note}</p>
              <button type="button" onClick={() => open(s)} data-scenario={s.id}>
                打开这个场景
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>全部公共样例</h2>
        <ul className="small">
          {SAMPLE_IDS.map((id) => (
            <li key={id}>
              <Link to={`/dev/samples/${id}`}>
                {id}：{sample(id).message}
              </Link>{" "}
              —— {sample(id).description_zh}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function SampleGallery() {
  const { id = "" } = useParams();
  const { caps } = useDraft();
  let s;
  try {
    s = sample(id);
  } catch {
    return <p className="error-box">没有编号为 {id} 的公共样例。</p>;
  }
  const payload = assertMessage<unknown>(s.message, s.payload);
  let body;
  switch (s.message) {
    case "ProfileReview": {
      const r = payload as ProfileReview;
      body = (
        <>
          <p className="note">这是确认页“系统怎样理解”同一个组件对这份样例审阅的呈现（这个样例没有原始草稿，所以不显示“你的答案”）。</p>
          <ReviewView review={r} />
          {(r.pending_confirmations ?? []).length ? (
            <p className="small">待知悉：{(r.pending_confirmations ?? []).join("、")}</p>
          ) : null}
          <p>{r.confirmable ? "可以确认" : "还不能确认"}</p>
        </>
      );
      break;
    }
    case "ProductPreflight":
      body = <PreflightView preflight={payload as ProductPreflight} />;
      break;
    case "ConfirmedProfile":
      body = <ConfirmedView profile={payload as ConfirmedProfile} caps={caps} />;
      break;
    case "YearFacts":
      body = <YearFactsView year={payload as YearFacts} />;
      break;
    case "RunReceipt": {
      const r = payload as RunReceipt;
      body = (
        <div>
          <p>
            回执：<strong>{r.status}</strong>
            {r.run_id ? `，运行 ${r.run_id}` : ""}（{r.service === "offline_bridge" ? "本地离线桥接，不是托管服务" : "契约示例"}）
          </p>
          <IssueList issues={r.reasons} showEditLink={false} />
        </div>
      );
      break;
    }
    case "RunProgress":
      body = <ProgressView progress={payload as RunProgress} caps={caps} years={null} />;
      break;
    case "ProductResult":
      body = <ResultView result={payload as ProductResult} />;
      break;
    default:
      body = (
        <>
          <p className="note">这是资料侧的消息（{s.message}），E5-A 不单独做页面；下面是原文。</p>
          <pre className="card" style={{ overflowX: "auto", fontSize: "0.8rem" }}>
            {JSON.stringify(payload, null, 2)}
          </pre>
        </>
      );
  }
  const meta = (payload as { meta?: Parameters<typeof SampleOrigin>[0]["meta"] }).meta;
  return (
    <div>
      <PageTitle>
        样例 {s.id}：{s.message}
      </PageTitle>
      <p className="muted">{s.description_zh}</p>
      {body}
      <SampleOrigin meta={meta} />
      <p>
        <Link className="tap" to="/dev/samples">回到样例面板</Link>
      </p>
    </div>
  );
}
