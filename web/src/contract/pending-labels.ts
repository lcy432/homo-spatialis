// 待 E3 提供正式中文的临时显示文字（需求 A1，docs/handoffs/e5-to-e3-interface-requests.md）。
//
// 结果里有一些字段只有英文键。在 E3 给出正式文字之前，界面用这里的转写：每条注明来源，只转写来源里写明的含义，不新增解释。
// 没有登记的键显示“未登记的状态（代码：xxx）”，不隐藏。E3 提供正式文字后删除这份文件，改读消息里的中文。

export interface PendingLabel {
  text: string;
  /** 这段文字转写自哪里（仓库里的文件与位置）。 */
  source: string;
}

type Table = Record<string, PendingLabel>;

const HEALTH_SOURCE = "src/common/life_contracts.py 的 HEALTH_STATUS_LABEL";
/** YearFacts.health_months 的每个月。 */
export const HEALTH_MONTH: Table = {
  healthy: { text: "健康", source: HEALTH_SOURCE },
  minor: { text: "小病", source: HEALTH_SOURCE },
  major: { text: "大病", source: HEALTH_SOURCE },
};

const SETTLEMENT_SOURCE = "src/common/life_contracts.py 的 EnvironmentOutcome.settlement_status 注释";
/** YearFacts.status（这一年的结算状态）。 */
export const YEAR_STATUS: Table = {
  completed: { text: "正常过完这一年", source: SETTLEMENT_SOURCE },
  exited: { text: "这一年出局（出局之前真实发生的账目保留，之后不再有收入与消费）", source: SETTLEMENT_SOURCE },
  stopped: { text: "这一年模拟停止（停止之前的月份保留，停止月与之后没有账目）", source: SETTLEMENT_SOURCE },
};

const SUMMARY_SOURCE =
  "src/common/life_evaluation_contracts.py 的 RequirementTrajectory.requirement_summary 与 src/econ/life_terminal_value.py 的 requirement_trajectories 注释";
/** 硬要求在整段生活里的汇总（RequirementLine.summary）。 */
export const REQUIREMENT_SUMMARY: Table = {
  satisfied_every_month: { text: "每个月都满足", source: SUMMARY_SOURCE },
  violated: { text: "有月份不满足", source: SUMMARY_SOURCE },
  unknown: { text: "没有不满足的月份，但有月份无法判断", source: SUMMARY_SOURCE },
  satisfied_in_lived_months: { text: "生活过的月份都满足（有没有生活的月份）", source: SUMMARY_SOURCE },
};

/** 硬要求的需求键（RequirementLine.need、RequirementSummaryLine.need）。能力清单没有给出它与选项的对应，资料包里有。 */
export const REQUIREMENT_NEED: Table = {
  commute_limit: {
    text: "住处到就业中心的往返时间不超过（分钟）",
    source: "configs/product/e3a_synthetic_pack.yaml 的 hard_requirement_options（need: commute_limit 那一项的 label）",
  },
};

const PLAN_SOURCE = "configs/life/evaluation_e1_convenience.yaml 里各模板的 label";
/** 期末估值选中的参考方案（CloneResult.reference_plan）。 */
export const REFERENCE_PLAN: Table = {
  p1_current_basic: { text: "现职 + 基本生活", source: PLAN_SOURCE },
  p2_current_device: { text: "现职 + 较舒适的生活 + 便利设备", source: PLAN_SOURCE },
  p3_current_service: { text: "现职 + 较舒适的生活 + 人工杂务服务", source: PLAN_SOURCE },
  p4_qualified_basic: { text: "改做已具资格的岗位 + 基本生活", source: PLAN_SOURCE },
  p5_qualified_device: { text: "改做已具资格的岗位 + 较舒适的生活 + 便利设备", source: PLAN_SOURCE },
  p6_qualified_service: { text: "改做已具资格的岗位 + 较舒适的生活 + 人工杂务服务", source: PLAN_SOURCE },
};

const TASK_SOURCE = "configs/life/synthetic_e1_convenience.yaml 的 household_tasks[].description（冒号前的部分）";
/** 家务任务（HouseholdHours.task）。 */
export const HOUSEHOLD_TASK: Table = {
  laundry: { text: "洗衣", source: TASK_SOURCE },
  cleaning: { text: "打扫", source: TASK_SOURCE },
};

export interface ResolvedLabel {
  text: string;
  pending: boolean;
  known: boolean;
  source?: string;
}

/** 查一个键的临时显示文字；查不到就如实显示代码。 */
export function pendingLabel(table: Table, key: string | null | undefined): ResolvedLabel {
  if (key == null) return { text: "（没有）", pending: false, known: false };
  const hit = table[key];
  if (hit) return { text: hit.text, pending: true, known: true, source: hit.source };
  return { text: `未登记的状态（代码：${key}）`, pending: false, known: false };
}

export const ALL_PENDING_TABLES: Record<string, Table> = {
  "YearFacts.health_months": HEALTH_MONTH,
  "YearFacts.status": YEAR_STATUS,
  "RequirementLine.summary": REQUIREMENT_SUMMARY,
  "RequirementLine.need / RequirementSummaryLine.need": REQUIREMENT_NEED,
  "CloneResult.reference_plan": REFERENCE_PLAN,
  "HouseholdHours.task": HOUSEHOLD_TASK,
};
