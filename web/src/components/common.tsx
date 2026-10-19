// 小的展示组件：问题卡、用途与支持标签、公开说明、样例出处、精确值、临时文字。
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { ExactNumber, ProductIssue, ProductMeta, PublicExplanation } from "../contract/types";
import type { ResolvedLabel } from "../contract/pending-labels";
import { SUPPORT_TEXT, T, USAGE_TEXT } from "../copy/zh";
import { stepForField } from "../flow/fieldSteps";

export function UsageChips({ usage }: { usage: readonly string[] | undefined }) {
  if (!usage?.length) return null;
  return (
    <ul className="chips" aria-label={T.usage}>
      {usage.map((u) => (
        <li key={u} className="chip">
          {T.usage}：{USAGE_TEXT[u] ?? u}
        </li>
      ))}
    </ul>
  );
}

export function SupportChip({ support }: { support: string | undefined }) {
  if (!support) return null;
  return <span className={support === "supported" ? "chip accent" : "chip warn"}>{SUPPORT_TEXT[support] ?? support}</span>;
}

const SEVERITY_TEXT: Record<string, string> = { error: "要处理", warning: "请注意", info: "说明" };

export function IssueCard({ issue, showEditLink = true }: { issue: ProductIssue; showEditLink?: boolean }) {
  const step = showEditLink ? stepForField(issue.field) : null;
  return (
    <div className={issue.severity === "error" ? "error-box" : issue.severity === "warning" ? "warn" : "note"}>
      <p>
        <strong>{SEVERITY_TEXT[issue.severity] ?? issue.severity}：</strong>
        {issue.message_zh}
      </p>
      <p className="small">影响：{issue.impact_zh}</p>
      <p className="small">下一步：{issue.next_action_zh}</p>
      {issue.audit_ref ? <p className="small muted">审计编号：{issue.audit_ref}</p> : null}
      {step ? (
        <p className="small">
          <Link className="tap" to={`/fill/${step}`} state={{ focusField: issue.field }}>
            去修改（第 {step} 步）
          </Link>
        </p>
      ) : null}
    </div>
  );
}

export function IssueList({ issues, showEditLink = true }: { issues: readonly ProductIssue[] | undefined; showEditLink?: boolean }) {
  if (!issues?.length) return null;
  return (
    <div className="stack">
      {issues.map((i, n) => (
        <IssueCard key={`${i.code}.${i.field}.${n}`} issue={i} showEditLink={showEditLink} />
      ))}
    </div>
  );
}

export function ExplanationView({ explanation }: { explanation: PublicExplanation }) {
  return (
    <div className="note">
      <p>{explanation.reason_zh}</p>
      <p className="small">影响：{explanation.impact_zh}</p>
      <p className="small">下一步：{explanation.next_action_zh}</p>
      {explanation.audit_ref ? <p className="small muted">审计编号：{explanation.audit_ref}</p> : null}
    </div>
  );
}

const ORIGIN_TEXT: Record<string, string> = {
  offline_run: "离线运行（mock 规划器）实际生成",
  controlled_fixture: "受控测试夹具",
  contract_example: "标明为示例的契约响应，不是已实现的线上接口",
};

/** 消息里的样例出处标签（只有样例才有）。 */
export function SampleOrigin({ meta }: { meta: ProductMeta | undefined }) {
  const s = meta?.sample;
  if (!s) return null;
  return (
    <p className="small muted">
      数据出处：E3 公共样例（{ORIGIN_TEXT[s.origin] ?? s.origin}）——{s.note}
    </p>
  );
}

/** 显示 E3 给的显示值；精确值放在提示里（不参与任何计算）。 */
export function Num({ value }: { value: ExactNumber | null | undefined }) {
  if (!value) return <>{T.none}</>;
  return (
    <span title={`${T.exactValue} ${value.exact}`} data-exact={value.exact}>
      {value.display}
    </span>
  );
}

/** 待 E3 提供中文的临时文字：开发模式下加“临时文字”标记。 */
export function PendingText({ label }: { label: ResolvedLabel }) {
  return (
    <>
      {label.text}
      {import.meta.env.DEV && label.pending ? (
        <span className="chip warn" title={`来源：${label.source}`} style={{ marginLeft: "0.3em" }}>
          {T.pendingMarker}
        </span>
      ) : null}
    </>
  );
}

export function Section({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section className="card" aria-labelledby={id}>
      <h2 id={id} style={{ marginTop: 0 }}>
        {title}
      </h2>
      {children}
    </section>
  );
}
