// 确认页的三块：你的答案（本地回显）、系统怎样理解（E3 审阅）、已确认的画像。审阅与画像里的中文原样显示。
import { Link } from "react-router";
import type {
  AllocationReview,
  ConfirmedProfile,
  ProductCapabilities,
  ProfileReview,
  UserInputDraft,
} from "../contract/types";
import { SUPPORT_TEXT, T } from "../copy/zh";
import { IssueList, Num, SupportChip, UsageChips } from "./common";

const STATE_TEXT: Record<string, string> = {
  unanswered: T.stateUnanswered,
  indifferent: T.stateIndifferent,
  unsure: T.stateUnsure,
  provided: "已提供",
};

const HEALTH_STATE_TEXT: Record<string, string> = { unanswered: T.healthSkipped, unsure: T.stateUnsure, provided: "已回答" };

function EditLink({ step }: { step: number }) {
  return (
    <Link to={`/fill/${step}`} className="small tap">
      {T.edit}
    </Link>
  );
}

/** 你的答案：按步骤分组的可读回显（不是系统的理解）。每组一个“修改”，跳回那一步。 */
export function DraftEcho({ draft, caps, ageText }: { draft: UserInputDraft; caps: ProductCapabilities; ageText: string }) {
  const cityLabel = (ref: string) => caps.cities.find((c) => c.city_ref === ref)?.label ?? ref;
  const basis = caps.money_bases.find((b) => b.currency === draft.cash.currency && b.price_period === draft.cash.price_period);
  const pointLine = (group: "six_dimensions" | "material", key: string, label: string) => {
    const a = draft[group];
    if (a.mode === "equal_split") return `${label}：平均分配`;
    const e = a.entries?.[key];
    if (!e) return `${label}：${T.stateUnanswered}`;
    if (e.state === "provided") return `${label}：${e.value_text}`;
    return `${label}：${STATE_TEXT[e.state] ?? e.state}`;
  };
  const healthAnswer = (qid: string) => {
    const item = draft.health.items?.find((i) => i.question_id === qid);
    if (!item || item.choice == null) return T.healthSkipped;
    return caps.health_questionnaire.options.find((o) => o.option_id === item.choice)?.label ?? item.choice;
  };
  const freeLen = [...(draft.free_text.text ?? "")].length;
  return (
    <div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>1 · {T.steps[0]}</h3>
          <EditLink step={1} />
        </div>
        <dl className="facts">
          <dt>城市</dt>
          <dd>{draft.cities.city_refs?.length ? draft.cities.city_refs.map(cityLabel).join("、") : T.stateUnanswered}</dd>
          <dt>职业</dt>
          <dd>{draft.occupation.raw_text ?? T.stateUnanswered}</dd>
          <dt>年龄</dt>
          <dd>{ageText || T.stateUnanswered}</dd>
          <dt>开局现金</dt>
          <dd>
            {draft.cash.amount_text
              ? `${draft.cash.amount_text}（${basis ? `${basis.price_period} 年 ${basis.currency}` : `${draft.cash.currency ?? ""} ${draft.cash.price_period ?? ""}`}）`
              : T.stateUnanswered}
          </dd>
          <dt>资格</dt>
          <dd>
            {draft.abilities?.length
              ? draft.abilities
                  .map((a) => `${(caps.abilities ?? []).find((x) => x.ability_id === a.ability_id)?.label ?? a.ability_id}：${a.state === "provided" ? T.abilityHeld : T.abilityUnsure}`)
                  .join("；")
              : T.none}
          </dd>
        </dl>
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>2 · {T.steps[1]}</h3>
          <EditLink step={2} />
        </div>
        <ul>
          {caps.dimensions.map((d) => (
            <li key={d.key}>{pointLine("six_dimensions", d.key, d.label)}</li>
          ))}
        </ul>
        <p className="small muted">物质内部：</p>
        <ul>
          {caps.material_leaves.map((m) => (
            <li key={m.key}>{pointLine("material", m.key, m.label)}</li>
          ))}
        </ul>
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>3 · {T.steps[2]}</h3>
          <EditLink step={3} />
        </div>
        <ul>
          {caps.health_questionnaire.questions.map((q) => (
            <li key={q.question_id}>
              {q.text} —— <strong>{healthAnswer(q.question_id)}</strong>
            </li>
          ))}
        </ul>
        <p>补充文字：{freeLen ? T.freeTextCount(freeLen, caps.free_text_max_chars) : T.none}</p>
        <p>
          硬要求：
          {draft.hard_requirements?.length
            ? draft.hard_requirements
                .map((h) => {
                  const o = (caps.hard_requirement_options ?? []).find((x) => x.option_id === h.option_id);
                  return o ? `${o.label} ${h.parameter_value} ${o.unit}` : (h.raw_text ?? h.requirement_id);
                })
                .join("；")
            : T.none}
        </p>
        <p>
          品牌、菜系等：
          {draft.attribute_preferences?.length
            ? draft.attribute_preferences.map((a) => `“${a.raw_text}”${a.as_hard_requirement ? "（不可妥协）" : ""}`).join("；")
            : T.none}
        </p>
      </div>
    </div>
  );
}

function AllocationView({ title, review }: { title: string; review: AllocationReview }) {
  return (
    <div>
      <h4>{title}</h4>
      {review.status === "not_applicable" ? <p>不适用（父项为 0）</p> : null}
      {review.mode === "equal_split" ? <p className="small">平均分配</p> : null}
      <ul>
        {(review.lines ?? []).map((l) => (
          <li key={l.key}>
            {l.label}：
            {l.state === "provided" ? (
              <strong title={l.exact ? `${T.exactValue} ${l.exact}` : undefined}>{l.display}</strong>
            ) : (
              <strong>{STATE_TEXT[l.state] ?? l.state}</strong>
            )}{" "}
            <SupportChip support={l.support} />
          </li>
        ))}
      </ul>
      {review.total ? (
        <p className="small">
          合计 <Num value={review.total} />（{review.status === "complete" ? "完整" : "还不完整"}）
        </p>
      ) : null}
    </div>
  );
}

/** 系统怎样理解：E3 的审阅原样展示。 */
export function ReviewView({ review }: { review: ProfileReview }) {
  const blocking = (review.issues ?? []).filter((i) => i.severity === "error").length;
  return (
    <div>
      {(review.summary_zh ?? []).map((s) => (
        <p key={s} className="note">
          {s}
        </p>
      ))}
      {blocking ? <p className="error-box">{T.notConfirmable(blocking)}</p> : null}
      <IssueList issues={review.issues} />

      <h3>职业</h3>
      <p>{review.occupation.message_zh}</p>
      {(review.occupation.candidates ?? []).length ? (
        <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
          {(review.occupation.candidates ?? []).map((c) => (
            <li key={c.candidate_id} className="card" aria-current={review.occupation.selected === c.candidate_id ? "true" : undefined}>
              <strong>{c.label}</strong>
              {review.occupation.selected === c.candidate_id ? <span className="chip accent"> 已选 </span> : null}
              <ul>
                {c.duties.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : null}

      <h3>开局现金</h3>
      <p>
        {review.money.converted ? <strong>{review.money.converted.display}</strong> : null} {review.money.currency ?? ""}{" "}
        {review.money.price_period ? `（${review.money.price_period} 年口径）` : ""}
      </p>
      <p className="small muted">{review.money.meaning}</p>

      <AllocationView title="六维" review={review.six_dimensions} />
      <AllocationView title="物质内部" review={review.material} />

      <h3>健康风险应对（独立问卷）</h3>
      <ul>
        {review.health.map((h) => (
          <li key={h.question_id}>
            {h.text} —— <strong>{h.option_label ?? HEALTH_STATE_TEXT[h.answer_state] ?? h.answer_state}</strong>
            <br />
            <span className="small muted">{h.meaning}</span>
          </li>
        ))}
      </ul>

      {(review.statements ?? []).length ? (
        <>
          <h3>硬要求与其他陈述</h3>
          <ul>
            {(review.statements ?? []).map((s) => (
              <li key={s.ref}>
                “{s.raw_text}” <SupportChip support={s.support} />
                {s.execution_guaranteed === false ? <span className="chip warn">{T.notGuaranteed}</span> : null}
                <UsageChips usage={s.usage} />
                <span className="small">{s.message_zh}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>每项输入的用途</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">输入</th>
              <th scope="col">回答</th>
              <th scope="col">{T.support}</th>
              <th scope="col">{T.usage}</th>
              <th scope="col">说明</th>
            </tr>
          </thead>
          <tbody>
            {review.items.map((it) => (
              <tr key={it.item}>
                <th scope="row">{it.label}</th>
                <td>{STATE_TEXT[it.answer_state] ?? it.answer_state}</td>
                <td>{SUPPORT_TEXT[it.support] ?? it.support}</td>
                <td>
                  <UsageChips usage={it.usage} />
                  {it.execution_guaranteed === false ? <span className="chip warn">{T.notGuaranteed}</span> : null}
                </td>
                <td style={{ textAlign: "left" }}>{it.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 已确认的画像（不显示内部职业映射里的任何内容）。 */
export function ConfirmedView({ profile, caps }: { profile: ConfirmedProfile; caps: ProductCapabilities }) {
  const label = (key: string) =>
    caps.dimensions.find((d) => d.key === key)?.label ?? caps.material_leaves.find((m) => m.key === key)?.label ?? key;
  return (
    <div>
      <dl className="facts">
        <dt>职业</dt>
        <dd>
          {profile.occupation.label}（{profile.occupation.duties.join("、")}）
        </dd>
        <dt>年龄</dt>
        <dd>{profile.age_years}</dd>
        <dt>开局现金</dt>
        <dd>
          {profile.money.converted.display} {profile.money.currency}（{profile.money.price_period} 年口径）
        </dd>
        <dt>六维</dt>
        <dd>
          {Object.entries(profile.six_dimensions.display)
            .map(([k, v]) => `${label(k)} ${v}`)
            .join("、")}
          {profile.six_dimensions.mode === "equal_split" ? "（平均分配）" : ""}
        </dd>
        <dt>物质内部</dt>
        <dd>
          {profile.material.status === "allocated" && profile.material.allocation
            ? Object.entries(profile.material.allocation.display)
                .map(([k, v]) => `${label(k)} ${v}`)
                .join("、")
            : "不适用（物质生活为 0）"}
        </dd>
        <dt>健康问卷</dt>
        <dd>
          {profile.health.answers
            .map((a) => `${a.option_label ?? HEALTH_STATE_TEXT[a.answer_state] ?? a.answer_state}`)
            .join(" / ")}
        </dd>
        <dt>硬要求</dt>
        <dd>{profile.hard_requirements?.length ? profile.hard_requirements.map((h) => h.label_zh).join("；") : T.none}</dd>
        <dt>只留存的陈述</dt>
        <dd>{profile.retained?.length ? profile.retained.map((r) => `“${r.raw_text}”：${r.message_zh}`).join("；") : T.none}</dd>
      </dl>
      <p className="small muted">确认画像的内容身份：{profile.content_fingerprint.slice(0, 12)}…（只核对内容，不是登录或授权）</p>
    </div>
  );
}
