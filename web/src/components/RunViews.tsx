// 运行意图的展示：这次保存的有效设置、运行前检查、进度与费用。全部照服务的公开字段显示：
// * 停机上限（max_cost）是“到了就停下”的上限，单独成行，不叫“费用”，也不当预计花费；
// * 费用分已核算、用量不确定、在途预留，缺项不当 0；规划候选数与服务尝试数分开；
// * 服务没给可靠的总进度，就不显示百分比或剩余时间。
import { useTranslation } from "react-i18next";
import type { CapabilityStatus, CostStatus, ProductCapabilities, ProductPreflight, RunProgress, RunSettingsView } from "../contract/types";
import { useCityName } from "../display/names";
import { capsPtr } from "../display/pointers";
import { at } from "../display/scope";
import { BizNotice, Zh, ZhList } from "../i18n/biz";
import { formatTime } from "../i18n/format";
import { useLocale } from "../i18n/index";
import { ExplanationView, IssueList, Num, Section } from "./common";

export function cityLabelOf(caps: ProductCapabilities | null | undefined, ref: string): string {
  return caps?.cities.find((c) => c.city_ref === ref)?.label ?? ref;
}

/** 城市名：按能力清单（或传入的结果城市表）的位置取当前语言的名称。 */
export function CityName({ caps, cityRef, cities }: { caps: ProductCapabilities | null | undefined; cityRef: string; cities?: readonly { city_ref: string; label: string }[] | null }) {
  const inResult = cities?.find((c) => c.city_ref === cityRef);
  if (inResult) {
    const i = cities?.indexOf(inResult) ?? -1;
    return <Zh pointer={at("/cities", i, "label")}>{inResult.label}</Zh>;
  }
  return <Zh pointer={capsPtr.city(caps, cityRef)}>{cityLabelOf(caps, cityRef)}</Zh>;
}

export function SettingsView({ settings, caps }: { settings: RunSettingsView | null | undefined; caps: ProductCapabilities | null }) {
  const { t } = useTranslation();
  if (!settings) return null;
  return (
    <Section title={t("intent.settings")}>
      <p className="small muted">{t("intent.settingsNote")}</p>
      <dl className="facts" data-settings={settings.settings_fingerprint}>
        <dt>{t("prepare.cities")}</dt>
        <dd>
          {settings.cities.map((c, i) => (
            <span key={c}>
              {i ? "、" : ""}
              <CityName caps={caps} cityRef={c} />
            </span>
          ))}
        </dd>
        <dt>{t("prepare.clones")}</dt>
        <dd className="num">{settings.clones_per_city}</dd>
        <dt>{t("prepare.years")}</dt>
        <dd className="num">{settings.years}</dd>
        <dt>{t("intent.clonesTotal")}</dt>
        <dd className="num">{settings.clones_total}</dd>
        <dt>{t("intent.planner")}</dt>
        <dd>{settings.planner_kind === "mock" ? t("intent.plannerMock") : t("intent.plannerLlm")}</dd>
        <dt>{t("intent.maxCost")}</dt>
        <dd className="num" data-max-cost={settings.max_cost ?? ""}>
          {settings.max_cost ? `${settings.max_cost} ${settings.currency ?? ""}` : t("common.none")}
        </dd>
        <dt>{t("intent.callLimit")}</dt>
        <dd className="num">{settings.call_limit}</dd>
        {settings.service_attempt_limit != null ? (
          <>
            <dt>{t("intent.attemptLimit")}</dt>
            <dd className="num">{settings.service_attempt_limit}</dd>
          </>
        ) : null}
      </dl>
    </Section>
  );
}

function Capability({ name, status, pointer }: { name: "executable" | "scorable" | "feedback"; status: CapabilityStatus; pointer: string }) {
  const { t } = useTranslation();
  return (
    <li data-capability={name} data-status={status.status}>
      {t(`capabilityName.${name}`)}：<strong>{t(`capability.${status.status}`)}</strong>
      {status.reasons?.length ? <ZhList items={status.reasons} pointer={`${pointer}/${name}/reasons`} /> : null}
    </li>
  );
}

/** 运行前检查。pointer 是它在业务对象里的位置（运行意图里是 /preflight）。 */
export function PreflightView({ preflight, caps, pointer = "/preflight" }: { preflight: ProductPreflight; caps: ProductCapabilities | null; pointer?: string }) {
  const { t } = useTranslation();
  const p = preflight;
  const pp = pointer;
  const cityName = useCityName(caps);
  const topics = [...new Set((p.scope_notes ?? []).map((n) => n.topic))];
  return (
    <Section title={t("preflight.title")} id="preflight">
      <BizNotice />
      <p className={p.status === "ready" ? "box ok" : "box error"} data-preflight={p.status}>
        <strong>{p.status === "ready" ? t("preflight.ready") : t("preflight.blocked")}</strong>
        <br />
        {t("preflight.scale", { cities: p.cities?.length ?? 0, clones: p.clones_per_city, years: p.years })}
      </p>
      <IssueList issues={p.issues} pointer={`${pp}/issues`} />
      <h3>{t("preflight.capabilities")}</h3>
      <ul>
        <Capability name="executable" status={p.executable} pointer={pp} />
        <Capability name="scorable" status={p.scorable} pointer={pp} />
        <Capability name="feedback" status={p.feedback} pointer={pp} />
      </ul>
      {p.start ? (
        <details>
          <summary>{t("preflight.start")}</summary>
          <ZhList items={p.start.scenario_zh} pointer={`${pp}/start/scenario_zh`} />
          <dl className="facts">
            <dt>{t("review.occupation")}</dt>
            <dd>
              <Zh pointer={`${pp}/start/occupation_label`}>{p.start.occupation_label}</Zh>
            </dd>
            <dt>{t("review.age")}</dt>
            <dd>{p.start.age_years}</dd>
            <dt>{t("review.cash")}</dt>
            <dd>
              {p.start.cash.display}（{p.start.cash.numeraire_id}）
            </dd>
          </dl>
          <p className="small">
            <Zh pointer={`${pp}/start/monthly_time_note`}>{p.start.monthly_time_note}</Zh>
          </p>
          <p className="small">
            <Zh pointer={`${pp}/start/monthly_energy_note`}>{p.start.monthly_energy_note}</Zh>
          </p>
          <p className="small muted">
            <Zh pointer={`${pp}/start/rule`}>{p.start.rule}</Zh>
          </p>
        </details>
      ) : null}
      <h3>{t("preflight.cities")}</h3>
      <ul className="plain stack">
        {(p.cities ?? []).map((c, ci) => (
          <li key={c.city_ref} className="card" data-city={c.city_ref}>
            <strong>
              <Zh pointer={at(`${pp}/cities`, ci, "label")}>{c.label}</Zh>
            </strong>
            {c.start_job_label ? (
              <p className="small">
                {t("preflight.startJob", { label: "" })}
                <Zh pointer={at(`${pp}/cities`, ci, "start_job_label")}>{c.start_job_label}</Zh>
              </p>
            ) : null}
            <ul className="small">
              <Capability name="executable" status={c.executable} pointer={at(`${pp}/cities`, ci)} />
              <Capability name="scorable" status={c.scorable} pointer={at(`${pp}/cities`, ci)} />
              <Capability name="feedback" status={c.feedback} pointer={at(`${pp}/cities`, ci)} />
            </ul>
            {c.coverage ? (
              <dl className="facts small">
                <dt>{t("preflight.commonWeight")}</dt>
                <dd>
                  <Num value={c.coverage.common_weight} />
                </dd>
                <dt>{t("preflight.unknownWeight")}</dt>
                <dd>
                  <Num value={c.coverage.unknown_weight} />
                </dd>
                <dt>{t("preflight.noComponentWeight")}</dt>
                <dd>
                  <Num value={c.coverage.no_component_weight} />
                </dd>
              </dl>
            ) : null}
            {c.data_nature ? (
              <p className="small">
                <Zh pointer={at(`${pp}/cities`, ci, "data_nature", "summary_zh")}>{c.data_nature.summary_zh}</Zh>
              </p>
            ) : null}
            {c.data_gaps?.length ? (
              <>
                <p className="small">{t("preflight.dataGaps")}</p>
                <IssueList issues={c.data_gaps} pointer={at(`${pp}/cities`, ci, "data_gaps")} />
              </>
            ) : null}
          </li>
        ))}
      </ul>
      {topics.length ? (
        <>
          <h3>{t("preflight.scopeNotes")}</h3>
          {topics.map((topic) => (
            <details key={topic} data-topic={topic}>
              <summary>{t(`scopeTopic.${topic}` as "scopeTopic.data", { defaultValue: topic })}</summary>
              <ul>
                {(p.scope_notes ?? [])
                  .map((n, ni) => [n, ni] as const)
                  .filter(([n]) => n.topic === topic)
                  .map(([n, ni]) => (
                    <li key={`${topic}.${ni}`}>
                      <strong>
                        <Zh pointer={at(`${pp}/scope_notes`, ni, "title_zh")}>{n.title_zh}</Zh>
                      </strong>
                      {n.cities?.length ? (
                        <span className="small muted">（{n.cities.map((c) => cityName(c)).join("、")}）</span>
                      ) : (
                        <span className="small muted">（{t("preflight.scopeAll")}）</span>
                      )}
                      <Zh as="p" pointer={at(`${pp}/scope_notes`, ni, "text_zh")}>
                        {n.text_zh}
                      </Zh>
                    </li>
                  ))}
              </ul>
            </details>
          ))}
        </>
      ) : null}
      {p.scope_zh?.length ? (
        <details>
          <summary>{t("preflight.scope")}</summary>
          <ZhList items={p.scope_zh} pointer={`${pp}/scope_zh`} />
        </details>
      ) : null}
      <h3>{t("preflight.plannerTitle")}</h3>
      <p>
        <Zh pointer={`${pp}/planner/description_zh`}>{p.planner.description_zh}</Zh>
      </p>
      <ul className="small">
        <li>{p.planner.paid ? t("preflight.plannerPaid", { v: t("common.yes") }) : t("preflight.notPaid")}</li>
        <li>{t("preflight.callLimit", { n: p.planner.call_limit })}</li>
        <li data-max-cost={p.planner.max_cost ?? ""}>
          {p.planner.max_cost ? t("preflight.maxCost", { v: p.planner.max_cost }) : t("preflight.maxCostNone")}
        </li>
      </ul>
    </Section>
  );
}

export function CostView({ cost, pointer }: { cost: CostStatus; pointer: string }) {
  const { t } = useTranslation();
  if (cost.kind === "offline_mock_zero_cost") {
    return (
      <div data-cost="mock">
        <p className="small">{t("progress.costMock")}</p>
        <p className="small muted">
          <Zh pointer={`${pointer}/note`}>{cost.note}</Zh>
        </p>
        <p className="small">{t("progress.calls", { used: cost.calls_used, limit: cost.call_limit })}</p>
      </div>
    );
  }
  const money = (v: string | null | undefined) => (v == null ? t("common.unknown") : `${v} ${cost.currency ?? ""}`);
  return (
    <div data-cost="estimated">
      <p className="small">{t("progress.cost")}</p>
      <dl className="facts small">
        <dt>{t("progress.settled")}</dt>
        <dd className="num">{money(cost.settled_cost)}</dd>
        <dt>{t("progress.uncertain")}</dt>
        <dd className="num">{money(cost.uncertain_cost)}</dd>
        <dt>{t("progress.inFlight")}</dt>
        <dd className="num">{money(cost.in_flight_cost)}</dd>
        <dt>{t("progress.estimated")}</dt>
        <dd className="num">{money(cost.estimated_cost)}</dd>
        <dt>{t("progress.maxCost")}</dt>
        <dd className="num">{money(cost.max_cost)}</dd>
      </dl>
      <p className="small">{t("progress.calls", { used: cost.calls_used, limit: cost.call_limit })}</p>
      {cost.service_attempts != null ? (
        <p className="small">{t("progress.attempts", { used: cost.service_attempts, limit: cost.service_attempt_limit ?? "—" })}</p>
      ) : null}
      {cost.uncertain_attempts ? <p className="small">{t("progress.uncertainAttempts", { n: cost.uncertain_attempts })}</p> : null}
      {cost.price_version ? <p className="small muted">{t("progress.priceVersion", { v: cost.price_version })}</p> : null}
      <p className="small muted">
        <Zh pointer={`${pointer}/note`}>{cost.note}</Zh>
      </p>
    </div>
  );
}

/** 公开进度（各分身、费用、能否恢复）。pointer 是它在运行意图里的位置（/progress）。 */
export function ProgressView({ progress, caps, pointer = "/progress" }: { progress: RunProgress; caps: ProductCapabilities | null; pointer?: string }) {
  const { t } = useTranslation();
  const locale = useLocale();
  const pp = pointer;
  return (
    <Section title={t("progress.title")} id="progress">
      <BizNotice />
      <p data-progress-stage={progress.stage}>
        <strong>
          <Zh pointer={`${pp}/stage_zh`}>{progress.stage_zh}</Zh>
        </strong>
      </p>
      {progress.updated_at ? <p className="small muted">{t("common.lastUpdated", { time: formatTime(progress.updated_at, locale) })}</p> : null}
      <p className="small muted">{t("progress.noPercent")}</p>
      <ExplanationView explanation={progress.pause} pointer={`${pp}/pause`} />
      <h3>{t("progress.clones")}</h3>
      <ul className="plain">
        {(progress.clones ?? []).map((c, ci) => (
          <li key={c.clone_ref} data-clone={c.clone_ref} data-status={c.status}>
            <CityName caps={caps} cityRef={c.city_ref} /> · {c.clone_ref}：<Zh pointer={at(`${pp}/clones`, ci, "status_zh")}>{c.status_zh}</Zh>（{t("progress.cloneLine", { n: c.years_committed })}）
          </li>
        ))}
      </ul>
      <CostView cost={progress.cost} pointer={`${pp}/cost`} />
      <p className="small">
        {t("progress.resumable")}：{progress.resumable ? t("common.yes") : t("common.no")} — <Zh pointer={`${pp}/resume_note`}>{progress.resume_note}</Zh>
      </p>
    </Section>
  );
}
