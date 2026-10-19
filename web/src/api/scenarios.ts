// 样例场景：每个场景用到哪几份 E3 公共样例（contracts/product/v2/samples/）。适配器只按这里挑选并原样返回，不编造回复。
// 只有正常场景有原始草稿样例（02）；其余审阅场景没有草稿（已请 E3 补，需求 A7），直接从确认页开始演示。

export type ScenarioEntry = "fill" | "confirm" | "progress" | "result" | "fragment";

export interface Scenario {
  id: string;
  title: string;
  note: string;
  entry: ScenarioEntry;
  draft?: string;
  review?: string;
  confirmed?: string;
  preflight?: string;
  /** 提交的回执：first 是第一次提交，repeat 是同一个幂等键的重复提交。 */
  receipts?: { first: string; repeat?: string };
  /** 这个场景要打开的运行（进度或结果页）。 */
  runId?: string;
  /** 片段样例（不是完整消息）。 */
  fragment?: string;
}

export const CAPABILITIES_SAMPLE = "01";

export const SCENARIOS: Scenario[] = [
  {
    id: "normal",
    title: "正常闭环",
    note: "合成用户 u501（文员）比较 K51 与 M52：填写 → 确认 → 预检查 → 提交（含重复提交）→ 进度 → 结果",
    entry: "fill",
    draft: "02",
    review: "03",
    confirmed: "04",
    preflight: "05",
    receipts: { first: "07", repeat: "08" },
    runId: "run-e3a-33299bbc42cf707b",
  },
  { id: "occupation_ambiguous", title: "职业歧义", note: "一段描述对应三个可读候选，等用户选", entry: "confirm", review: "11" },
  {
    id: "unanswered_zero",
    title: "未答、0 与无所谓",
    note: "物质给 0 是已提供的数值；休闲未答、环境选了无所谓，都不会被换成 0",
    entry: "confirm",
    review: "12",
  },
  { id: "equal_split", title: "主动均分", note: "六维每项 50/3（显示 16.7）、物质每项 100/3（显示 33.3）", entry: "confirm", review: "13", confirmed: "14" },
  {
    id: "health_unsure",
    title: "健康暂不确定",
    note: "健康问卷的暂不确定、未答、无明显倾向照实保存（这个样例只有确认画像）",
    entry: "confirm",
    confirmed: "15",
  },
  { id: "legacy_laundry", title: "旧问卷“洗衣”", note: "旧问卷的洗衣点数不能自动当作日常生活便利，需要重新分配", entry: "confirm", review: "16" },
  {
    id: "attributes",
    title: "品牌、菜系与上门顾虑",
    note: "品牌、菜系只留存不评分；“不让陌生人进家”被标为不可妥协，但本版没有执行保障",
    entry: "confirm",
    review: "17",
  },
  { id: "money_blocked", title: "金额口径不支持", note: "“当前 10,000 美元”没有合法的时期换算依据", entry: "confirm", review: "30" },
  { id: "money_separator", title: "金额写法错误", note: "“3000,50”的逗号不能当小数点，系统不猜", entry: "confirm", review: "37" },
  { id: "duplicates", title: "重复与冲突答案", note: "同一健康题两个答案、同一资格两条、两条通勤上限，确认前全部拒绝", entry: "confirm", review: "38" },
  { id: "start_rejected", title: "预检查被拒：标准开局", note: "往开局塞一件旧设备，E1 的标准开局检查拒绝", entry: "confirm", preflight: "28" },
  { id: "job_unmatched", title: "预检查被拒：职业没有对应岗位", note: "两座合成城都没有收银员岗位，不自动换职业", entry: "confirm", preflight: "29" },
  {
    id: "weight_unknown",
    title: "正权重资料未知",
    note: "M52 的安全资料未知而用户给安全 20 分：预检查报告缺口、M52 可评分 partial",
    entry: "confirm",
    preflight: "34",
  },
  {
    id: "stale_submission",
    title: "答案改过之后提交",
    note: "服务端拒绝过期的确认与预检查。回执 31 来自另一个合成用户 u508 的样例，这里只演示拒绝怎样显示",
    entry: "fill",
    draft: "02",
    review: "03",
    confirmed: "04",
    preflight: "05",
    receipts: { first: "31" },
  },
  { id: "no_purchase", title: "没有购买", note: "没买任何便利方案：便利分是确定的 0，轨迹照常评分", entry: "result", runId: "run-e3a-5884ccd92ff1d359" },
  { id: "partial_service", title: "部分月份没买到服务", note: "逐年生活事实片段（受控夹具）", entry: "fragment", fragment: "22" },
  {
    id: "mixed_cem",
    title: "C / E / M 混合",
    note: "K51 一个完整、一个出局；M52 一个模拟问题、一个出局，所以 M52 的 C = 0",
    entry: "result",
    runId: "run-e3a-f3ca55e83e2949ee",
  },
  { id: "all_exit", title: "全部出局", note: "均值与模拟缺失范围都为空（不补 0）", entry: "result", runId: "run-e3a-d0fd8111df059813" },
  { id: "interrupted", title: "中断，评价还没生成", note: "运行在两次规划调用之间中断；还没有评价，不擅自分类", entry: "progress", runId: "run-e3a-38d99f75a43e2889" },
  { id: "paused", title: "程序错误暂停", note: "出错的分身记程序错误，没轮到的分身保留为未运行；都是模拟问题", entry: "progress", runId: "run-e3a-16acfe6b35dbda3e" },
  { id: "halted", title: "服务失败停下（契约示例）", note: "模型服务暂时不可用，批次停下；手写的契约示例，不是运行结果", entry: "progress", runId: "run-e3a-example-halted" },
];

/** 运行编号 → 进度样例（第一个是平时的回复；断线之后再查询返回第二个，即契约示例 32）。 */
export const PROGRESS_BY_RUN: Record<string, string[]> = {
  "run-e3a-33299bbc42cf707b": ["09", "32"],
  "run-e3a-38d99f75a43e2889": ["26"],
  "run-e3a-16acfe6b35dbda3e": ["27"],
  "run-e3a-example-halted": ["33"],
};

/** 运行编号 → 结果样例。 */
export const RESULT_BY_RUN: Record<string, string> = {
  "run-e3a-33299bbc42cf707b": "10",
  "run-e3a-5884ccd92ff1d359": "21",
  "run-e3a-f3ca55e83e2949ee": "23",
  "run-e3a-d0fd8111df059813": "24",
  "run-e3a-38d99f75a43e2889": "25",
  "run-e3a-16acfe6b35dbda3e": "27b",
};

export const DEFAULT_SCENARIO = "normal";

export function scenarioById(id: string | null | undefined): Scenario {
  return SCENARIOS.find((s) => s.id === id) ?? (SCENARIOS[0] as Scenario);
}
