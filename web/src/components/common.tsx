// 小的展示组件：问题卡、公开说明、服务错误、精确值、金额、用途与支持标签、临时文字。
// E3 的中文原样显示（<Zh>），界面自己的文字走字典。
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { type ApiError, isApiError } from "../api/errors";
import type { ResolvedLabel } from "../contract/pending-labels";
import type { ExactNumber, ProductIssue, ProductMoney, PublicExplanation } from "../contract/types";
import { stepForField } from "../draft/fieldSteps";
import { Zh } from "../i18n/biz";

export function Section({ title, children, id, className }: { title: ReactNode; children: ReactNode; id?: string; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`} aria-labelledby={id}>
      <h2 id={id} className="card-title">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** 窄屏上可以横向滚动的表格外框：键盘能聚焦进去左右滚动，读屏软件读得出它是哪张表。 */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

export function UsageChips({ usage }: { usage: readonly string[] | undefined }) {
  const { t } = useTranslation();
  if (!usage?.length) return null;
  return (
    <ul className="chips" aria-label={t("usage.title")}>
      {usage.map((u) => (
        <li key={u} className="chip">
          {t("usage.title")}：{t(`usage.${u}` as "usage.planning", { defaultValue: u })}
        </li>
      ))}
    </ul>
  );
}

export function SupportChip({ support }: { support: string | undefined }) {
  const { t } = useTranslation();
  if (!support) return null;
  return (
    <span className={support === "supported" ? "chip accent" : "chip warn"}>
      {t(`support.${support}` as "support.supported", { defaultValue: support })}
    </span>
  );
}

export function IssueCard({ issue, draftId }: { issue: ProductIssue; draftId?: string | null }) {
  const { t } = useTranslation();
  const step = draftId ? stepForField(issue.field) : null;
  const cls = issue.severity === "error" ? "box error" : issue.severity === "warning" ? "box warn" : "box note";
  return (
    <div className={cls} data-issue={issue.code}>
      <p>
        <strong>{t(`severity.${issue.severity}`)}：</strong>
        <Zh>{issue.message_zh}</Zh>
      </p>
      <p className="small">
        {t("common.impact")}：<Zh>{issue.impact_zh}</Zh>
      </p>
      <p className="small">
        {t("common.nextAction")}：<Zh>{issue.next_action_zh}</Zh>
      </p>
      {issue.audit_ref ? <p className="small muted">{t("common.auditRef", { ref: issue.audit_ref })}</p> : null}
      {step && draftId ? (
        <p className="small">
          <Link className="tap" to={`/draft/${encodeURIComponent(draftId)}/${step}`} state={{ focusField: issue.field }}>
            {t("common.edit")}
          </Link>
        </p>
      ) : null}
    </div>
  );
}

export function IssueList({ issues, draftId }: { issues: readonly ProductIssue[] | undefined; draftId?: string | null }) {
  if (!issues?.length) return null;
  return (
    <div className="stack">
      {issues.map((i, n) => (
        <IssueCard key={`${i.code}.${i.field}.${n}`} issue={i} draftId={draftId} />
      ))}
    </div>
  );
}

export function ExplanationView({ explanation }: { explanation: PublicExplanation | null | undefined }) {
  const { t } = useTranslation();
  if (!explanation) return null;
  return (
    <div className="box note" data-explanation={explanation.category}>
      <p>
        <Zh>{explanation.reason_zh}</Zh>
      </p>
      <p className="small">
        {t("common.impact")}：<Zh>{explanation.impact_zh}</Zh>
      </p>
      <p className="small">
        {t("common.nextAction")}：<Zh>{explanation.next_action_zh}</Zh>
      </p>
      {explanation.audit_ref ? <p className="small muted">{t("common.auditRef", { ref: explanation.audit_ref })}</p> : null}
    </div>
  );
}

/** 接口错误：服务的三句中文与支持编号；断线、结构不符各有固定说明。不显示原始异常。 */
export function ApiErrorView({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  if (!error) return null;
  const e: ApiError | null = isApiError(error) ? error : null;
  if (e?.kind === "stale") return null;
  let body: ReactNode;
  if (e?.kind === "service" && e.service) {
    const s = e.service;
    body = (
      <>
        <p>
          <Zh>{s.message_zh}</Zh>
        </p>
        <p className="small">
          {t("common.impact")}：<Zh>{s.impact_zh}</Zh>
        </p>
        <p className="small">
          {t("common.nextAction")}：<Zh>{s.next_action_zh}</Zh>
        </p>
        {s.retry_after_seconds ? <p className="small">{t("errors.rateLimited", { s: s.retry_after_seconds })}</p> : null}
        {s.support_ref ? <p className="small muted">{t("common.supportRef", { ref: s.support_ref })}</p> : null}
      </>
    );
  } else if (e?.kind === "network") {
    body = <p>{e.status && e.status >= 500 ? t("errors.unavailable", { status: e.status }) : t("errors.network")}</p>;
  } else if (e?.kind === "contract") {
    body = (
      <p>
        {e.direction === "request"
          ? t("errors.requestContract", { name: e.messageName ?? "" })
          : t("errors.contract", { name: e.messageName ?? "" })}
      </p>
    );
  } else {
    body = <p>{t("errors.unknown")}</p>;
  }
  return (
    <div className="box error" role="alert" data-error={e?.code ?? e?.kind ?? "unknown"}>
      {body}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          {t("common.retry")}
        </button>
      ) : null}
    </div>
  );
}

/** E3 给的显示值；精确值放在提示里（不参与任何计算）。null 显示“没有”，不显示 0。 */
export function Num({ value }: { value: ExactNumber | null | undefined }) {
  const { t } = useTranslation();
  if (!value) return <span className="muted">{t("common.none")}</span>;
  return (
    <span title={`${t("common.exact")} ${value.exact}`} data-exact={value.exact}>
      {value.display}
    </span>
  );
}

/** 一笔钱：显示值与计价口径（不同口径不放在一起比较，也不换算）。 */
export function Money({ value }: { value: ProductMoney | null | undefined }) {
  const { t } = useTranslation();
  if (!value) return <span className="muted">{t("common.none")}</span>;
  return (
    <span data-numeraire={value.numeraire_id} data-minor={value.amount_minor}>
      {t("clone.money", { display: value.display, numeraire: value.numeraire_id })}
    </span>
  );
}

/** 待 E3 提供的临时文字：开发模式下加“临时文字”标记。 */
export function PendingText({ label }: { label: ResolvedLabel }) {
  const { t } = useTranslation();
  return (
    <>
      <Zh>{label.text}</Zh>
      {import.meta.env.DEV && label.pending ? (
        <span className="chip warn" title={label.source} style={{ marginLeft: "0.3em" }}>
          {t("common.pending")}
        </span>
      ) : null}
    </>
  );
}

export function Loading({ label }: { label?: string }) {
  const { t } = useTranslation();
  return (
    <p className="muted" role="status">
      {label ?? t("common.loading")}
    </p>
  );
}
