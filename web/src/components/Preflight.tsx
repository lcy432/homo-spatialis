// 运行前说明：E3 预检查原样展示。三种能力（能执行 / 能完整评分 / 能更新精力）各写状态与原因；不设覆盖率阈值；
// 不写死任何模型名，只显示消息里的规划器信息。
import type { CapabilityStatus, CityPreflight, PlannerAndCost, ProductPreflight } from "../contract/types";
import { CAPABILITY_NAMES, CAPABILITY_TEXT, T } from "../copy/zh";
import { IssueList, Num } from "./common";

export function CapabilityLine({ name, status }: { name: string; status: CapabilityStatus }) {
  return (
    <div>
      <p style={{ margin: "0.3em 0" }}>
        <strong>{name}：</strong>
        <span className={status.status === "yes" ? "chip accent" : "chip warn"}>{CAPABILITY_TEXT[status.status] ?? status.status}</span>
      </p>
      {(status.reasons ?? []).length ? (
        <ul className="small" style={{ marginTop: 0 }}>
          {(status.reasons ?? []).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const INVENTORY_TEXT: Record<string, string> = {
  complete_for_declared_scope: "在声明的调查范围内齐全",
  partial: "部分齐全",
  not_assessed: "没有调查",
};

function CityCard({ city }: { city: CityPreflight }) {
  const unknown = city.coverage ? city.coverage.unknown_weight.exact !== "0" : false;
  return (
    <section className="card" aria-label={city.label}>
      <h3 style={{ marginTop: 0 }}>
        {city.label} <span className="chip warn">{T.sampleCity}</span>
      </h3>
      {city.start_job_label ? <p>开局岗位：{city.start_job_label}</p> : null}
      <CapabilityLine name={CAPABILITY_NAMES.executable} status={city.executable} />
      <CapabilityLine name={CAPABILITY_NAMES.scorable} status={city.scorable} />
      <CapabilityLine name={CAPABILITY_NAMES.feedback} status={city.feedback} />
      {city.coverage ? (
        <details>
          <summary>偏好覆盖（E1 覆盖报告的摘要）</summary>
          <dl className="facts small">
            <dt>整组共同覆盖的权重</dt>
            <dd>
              <Num value={city.coverage.common_weight} />
            </dd>
            <dt>这座城资料未知的正权重</dt>
            <dd>
              <Num value={city.coverage.unknown_weight} />
            </dd>
            <dt>本版没有评分分项的正权重</dt>
            <dd>
              <Num value={city.coverage.no_component_weight} />
            </dd>
          </dl>
        </details>
      ) : null}
      {unknown ? (
        <p className="warn">你给了正分数的某些方面，这座城资料未知：该维度在本次比较中无数据支持，不知道不等于没差别。</p>
      ) : null}
      <IssueList issues={city.data_gaps} showEditLink={false} />
      <p className="small muted">选项目录覆盖：{INVENTORY_TEXT[city.option_inventory_status] ?? city.option_inventory_status}</p>
    </section>
  );
}

export function PlannerBox({ planner }: { planner: PlannerAndCost }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{T.planner}与费用</h3>
      <p>{planner.description_zh}</p>
      <dl className="facts small">
        <dt>规划器</dt>
        <dd>
          {planner.planner_kind === "mock" ? "离线 mock，不是大语言模型" : "大语言模型"}（{planner.planner_id}）
        </dd>
        <dt>调用上限</dt>
        <dd>{planner.call_limit} 次</dd>
        <dt>费用</dt>
        <dd>{planner.paid ? (planner.max_cost ?? "付费（上限未给出）") : "不付费（费用 0）"}</dd>
      </dl>
    </div>
  );
}

export function PreflightView({ preflight }: { preflight: ProductPreflight }) {
  return (
    <div>
      <p className={preflight.status === "ready" ? "note" : "error-box"}>
        {preflight.status === "ready" ? "运行前检查通过。" : "运行前检查没有通过，不能运行。"}
      </p>
      <IssueList issues={preflight.issues} showEditLink={false} />
      <div className="card">
        <h3 style={{ marginTop: 0 }}>整体</h3>
        <CapabilityLine name={CAPABILITY_NAMES.executable} status={preflight.executable} />
        <CapabilityLine name={CAPABILITY_NAMES.scorable} status={preflight.scorable} />
        <CapabilityLine name={CAPABILITY_NAMES.feedback} status={preflight.feedback} />
        <p>
          每座城 {preflight.clones_per_city} 个分身，生活 {preflight.years} 年。
        </p>
      </div>
      {preflight.start ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>标准开局</h3>
          <ul>
            {preflight.start.scenario_zh.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <dl className="facts small">
            <dt>年龄</dt>
            <dd>{preflight.start.age_years}</dd>
            <dt>开局现金</dt>
            <dd>
              {preflight.start.cash.display} {preflight.start.cash.currency}
            </dd>
            <dt>职业</dt>
            <dd>{preflight.start.occupation_label}</dd>
            <dt>时间</dt>
            <dd>{preflight.start.monthly_time_note}</dd>
            <dt>精力</dt>
            <dd>{preflight.start.monthly_energy_note}</dd>
          </dl>
        </div>
      ) : null}
      {preflight.cities?.map((c) => (
        <CityCard key={c.city_ref} city={c} />
      ))}
      <PlannerBox planner={preflight.planner} />
      {(preflight.scope_zh ?? []).length ? (
        <details className="card">
          <summary>支持范围与限制</summary>
          <ul>
            {(preflight.scope_zh ?? []).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
