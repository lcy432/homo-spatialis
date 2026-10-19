/* eslint-disable */
/**
 * 由 web/scripts/contract-gen.mjs 从 contracts/product/v2/*.schema.json 生成（结构版本 e3a-product-schema-v2）。
 * 不要手改：共享结构由 E3 维护（src/common/product_contracts.py），结构变了就重新运行 npm run contract:gen。
 */

/**
 * 全部 13 份共享消息（只为生成类型而合成的根，不是一份真实消息）
 */
export interface ProductContracts {
  CatalogCompilation: CatalogCompilation;
  ConfirmedProfile: ConfirmedProfile;
  OpportunityCandidate: OpportunityCandidate;
  OptionInventoryCoverage: OptionInventoryCoverage;
  ProductCapabilities: ProductCapabilities;
  ProductPreflight: ProductPreflight;
  ProductResult: ProductResult;
  ProfileReview: ProfileReview;
  RunProgress: RunProgress;
  RunReceipt: RunReceipt;
  RunSubmission: RunSubmission;
  SourceFact: SourceFact;
  UserInputDraft: UserInputDraft;
}
/**
 * 一次目录编译：合法机会、未编入候选与原因、版本、范围与来源映射。过滤之后的分母仍保留在覆盖报告里。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CatalogCompilation".
 */
export interface CatalogCompilation {
  cities: string[];
  compilation_fingerprint: string;
  compilation_id: string;
  compiled?: CompiledOpportunity[];
  compiler_version: string;
  facts?: SourceFact[];
  meta: ProductMeta;
  scope_zh: string;
  tasks: TaskDefinition[];
  uncompiled?: UncompiledCandidate[];
}
/**
 * 编入 E1 的一项合法机会：E1 的 ``availability``、逐字段来源映射与最弱来源性质（整条机会的来源标签取它）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CompiledOpportunity".
 */
export interface CompiledOpportunity {
  availability: "available" | "unknown";
  candidate_id: string;
  city_ref: string;
  field_sources: {
    [k: string]: string[];
  };
  note_zh: string;
  opportunity_id: string;
  unknown_reason?: ("price_unknown" | "supply_unknown" | "supply_and_price_unknown") | null;
  weakest_source_kind: "observed" | "derived" | "estimated" | "synthetic";
}
/**
 * 一条来源事实：原始值、单位、时期、地理、来源性质，以及换算或假设的引用。报价、寿命与减负效果分别保留来源。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SourceFact".
 */
export interface SourceFact {
  assumption?: string | null;
  conversion?: string | null;
  fact_id: string;
  field: "price" | "lifetime" | "effect" | "participation" | "availability";
  geography: string;
  kind: "observed" | "derived" | "estimated" | "synthetic";
  locator?: string | null;
  note: string;
  period: string;
  raw_value?: string | null;
  unit: string;
}
/**
 * 每条产品消息都带的共同元数据：结构、问卷、词表、健康问卷、转换的版本与资料包身份。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductMeta".
 */
export interface ProductMeta {
  conversion_version: string;
  data_pack: DataPackRef;
  health_questionnaire_version: string;
  questionnaire_version: string;
  sample?: SampleLabel | null;
  schema_version: "e3a-product-schema-v2";
  vocabulary_version: string;
}
/**
 * 一份资料包的身份：编号与内容指纹。本轮只有合成资料包（``synthetic_fixture_only``）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DataPackRef".
 */
export interface DataPackRef {
  content_fingerprint: string;
  note: string;
  pack_id: string;
  status: "synthetic_fixture_only" | "real_data_frozen";
}
/**
 * 公共样例的出处标签（只出现在样例里；真实运行的消息不写）。
 *
 * * ``offline_run``：由离线闭环实际执行产生；
 * * ``controlled_fixture``：受控测试夹具（例如写好的计划、注入的事件、构造的资料缺口），标明来源；
 * * ``contract_example``：线上服务（幂等提交、查询、断线恢复）尚未实现，这里只是**标明为示例**的契约响应。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SampleLabel".
 */
export interface SampleLabel {
  note: string;
  origin: "offline_run" | "controlled_fixture" | "contract_example";
  produced_by: string;
  synthetic?: true;
}
/**
 * 全组共用的家务任务定义：基准小时、单位与说明（与 E1 协议的家务基准同一单位）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "TaskDefinition".
 */
export interface TaskDefinition {
  baseline_hours_per_month: string;
  description: string;
  task: string;
  unit: "小时/月";
}
/**
 * 没有编入 E1 的候选：原因（机器代码 + 中文），真实供给状态原样保留（有卖但寿命未知，不改写成确定买不到）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "UncompiledCandidate".
 */
export interface UncompiledCandidate {
  candidate_id: string;
  city_ref: string;
  market_status: "offered" | "not_offered" | "unknown";
  /**
   * @minItems 1
   */
  reasons: [ProductIssue, ...ProductIssue[]];
}
/**
 * 一条可展示的问题：机器代码、字段位置、中文原因、影响与下一步动作。E5 原样展示中文，不另写解释。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductIssue".
 */
export interface ProductIssue {
  audit_ref?: string | null;
  code: string;
  field: string;
  impact_zh: string;
  message_zh: string;
  next_action_zh: string;
  severity: "error" | "warning" | "info";
}
/**
 * 用户确认的画像。``content_fingerprint`` 覆盖除它本身以外的全部内容（含版本与草稿引用）：改了相关答案、词表、问卷或转换
 * 规则，旧确认就对不上；内容被改写，指纹核对不通过。指纹只核对内容，不代替线上认证与授权。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedProfile".
 */
export interface ConfirmedProfile {
  abilities?: string[];
  age_years: number;
  /**
   * @minItems 1
   */
  cities: [string, ...string[]];
  confirmation_source: "user_form";
  confirmed_id: string;
  content_fingerprint: string;
  draft: DraftRef;
  free_text?: string | null;
  hard_requirements?: ConfirmedHardRequirement[];
  health: ConfirmedHealth;
  material: ConfirmedMaterial;
  meta: ProductMeta;
  money: ConfirmedMoney;
  occupation: ConfirmedOccupation;
  person_ref: string;
  retained?: RetainedStatement[];
  review_id: string;
  six_dimensions: ConfirmedAllocation;
  sources?: FieldSource[];
}
/**
 * 引用一份草稿：编号、修订号与内容指纹（草稿改了，指纹就不同）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftRef".
 */
export interface DraftRef {
  draft_fingerprint: string;
  draft_id: string;
  revision: number;
}
/**
 * 一条受支持、已确认的硬要求：编译成 E1 的结构化条件；**只单独报告，环境不拦截计划**。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedHardRequirement".
 */
export interface ConfirmedHardRequirement {
  enforced_by_execution?: false;
  label_zh: string;
  option_id: string;
  parameter_value: number;
  requirement_id: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedHealth".
 */
export interface ConfirmedHealth {
  /**
   * @minItems 1
   */
  answers: [ConfirmedHealthAnswer, ...ConfirmedHealthAnswer[]];
  questionnaire_version: string;
  status: "draft_wording_not_validated";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedHealthAnswer".
 */
export interface ConfirmedHealthAnswer {
  answer_state: "provided" | "unsure" | "unanswered";
  meaning: string;
  option?: ("agree" | "disagree" | "no_clear_tendency") | null;
  option_label?: string | null;
  question_id: string;
  question_text: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedMaterial".
 */
export interface ConfirmedMaterial {
  allocation?: ConfirmedAllocation | null;
  status: "allocated" | "not_applicable_parent_zero";
}
/**
 * 确认的定和分配：规范精确分数（参与计算）与显示文字（只供阅读）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedAllocation".
 */
export interface ConfirmedAllocation {
  display: {
    [k: string]: string;
  };
  mode: "manual" | "equal_split";
  points: {
    [k: string]: string;
  };
}
/**
 * 确认的开局现金：保留原始金额、币种、时期、口径与换算依据，以及换算后的整数最小单位。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedMoney".
 */
export interface ConfirmedMoney {
  amount_text: string;
  conversion_rule: string;
  converted: ProductMoney;
  currency: string;
  meaning: "liquid_funds_v1";
  price_period: string;
}
/**
 * 一笔钱：整数最小单位（美元是分）、币种与计价口径；``display`` 只供显示。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductMoney".
 */
export interface ProductMoney {
  amount_minor: number;
  currency: string;
  display: string;
  numeraire_id: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedOccupation".
 */
export interface ConfirmedOccupation {
  concept_id: string;
  /**
   * @minItems 1
   */
  duties: [string, ...string[]];
  /**
   * @minItems 1
   */
  internal_mappings: [OccupationMapping, ...OccupationMapping[]];
  label: string;
  language: string;
  match_status: "explicit" | "ambiguous";
  raw_text: string;
}
/**
 * 职业概念在某国分类体系里的映射（内部保留，**不展示给用户**，也不是用户的永久身份）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationMapping".
 */
export interface OccupationMapping {
  basis: string;
  code: string;
  country: string;
  scheme: string;
  version: string;
}
/**
 * 只留存的陈述：未支持的属性偏好、未获执行保障的“不可妥协”要求等。保留原文与支持状态，不挪进已有权重。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RetainedStatement".
 */
export interface RetainedStatement {
  as_hard_requirement: boolean;
  kind: string;
  message_zh: string;
  need?: string | null;
  raw_text: string;
  ref: string;
  support: "unsupported" | "partially_supported";
  usage: ("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[];
}
/**
 * 一项确认值的来源（表单字段，或未来的访谈证据引用）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "FieldSource".
 */
export interface FieldSource {
  /**
   * @minItems 1
   */
  evidence: [EvidenceRef, ...EvidenceRef[]];
  field: string;
}
/**
 * 答案的来源证据：本轮只有表单字段；未来访谈的记录引用在这里接入（访谈 → 候选解释 → 确认 → 同一编译器）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "EvidenceRef".
 */
export interface EvidenceRef {
  kind: "form_field" | "interview_turn_reserved";
  note?: string | null;
  ref: string;
}
/**
 * 一个可能不完整的设备 / 服务候选，逐字段带状态。``market_status`` 是真实供给状态，与能否编译分开记。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OpportunityCandidate".
 */
export interface OpportunityCandidate {
  candidate_id: string;
  city_ref: string;
  effect: EffectField;
  kind: "device" | "service";
  label: string;
  lifetime: LifetimeField;
  market_status: "offered" | "not_offered" | "unknown";
  participation?: ParticipationField;
  price: PriceField;
  quality: string;
  service_period?: string | null;
  target_opportunity_id: string;
  /**
   * @minItems 1
   */
  tasks: [string, ...string[]];
}
/**
 * 减负效果：每项代表性家务的“本人处理整项家务后仍需的月小时”（不是机器运行或服务人员工作时间）。未知不等于没有效果。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "EffectField".
 */
export interface EffectField {
  facts?: string[];
  residuals?: TaskRelief[];
  status: "known" | "unknown";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "TaskRelief".
 */
export interface TaskRelief {
  residual_hours_per_month: string;
  task: string;
}
/**
 * 设备寿命与生效时点。当前 E1 只接受 1–30 的整数年；未知或非整数年**不补值、不取整**。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LifetimeField".
 */
export interface LifetimeField {
  effective_from?: ("same_year" | "next_year") | null;
  facts?: string[];
  status: "known" | "unknown" | "not_applicable";
  years_text?: string | null;
}
/**
 * 服务需要本人每月花多少小时参与（不是服务人员的工作时间）。设备不适用；服务未知时不能编入（不当成 0）。
 */
export interface ParticipationField {
  burden_per_hour_text?: string | null;
  facts?: string[];
  hours_text?: string | null;
  status: "known" | "unknown" | "not_applicable";
}
/**
 * 报价字段：金额、币种、基年、当地价口径、计费周期与计价单位；未知时 ``status = unknown``，不填 0。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PriceField".
 */
export interface PriceField {
  amount_text?: string | null;
  base_year?: number | null;
  billing?: ("one_time" | "per_month" | "per_visit" | "per_year") | null;
  currency?: string | null;
  facts?: string[];
  per_unit?: string | null;
  price_meaning?: "local_price_base_year" | null;
  status: "known" | "unknown";
}
/**
 * 选项目录覆盖（``option_inventory_coverage``）：在**声明的调查范围**内，便利设备与服务的资料齐不齐。
 *
 * 它与“实际轨迹能否评分”、分身的 C / E / M 是三个不同的判断：目录不完整不改写 E1 已发生轨迹的评分；没有采用方案且资源账完整时，
 * E1 照样给出确定的 q = 0。不保证穷尽整个城市市场。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OptionInventoryCoverage".
 */
export interface OptionInventoryCoverage {
  /**
   * @minItems 1
   */
  categories: [string, ...string[]];
  cities: CityInventory[];
  coverage_id: string;
  declared_scope_zh: string;
  meta: ProductMeta;
  not_exhaustive_zh: string;
  scope_version: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityInventory".
 */
export interface CityInventory {
  city_ref: string;
  excluded: number;
  items?: InventoryItem[];
  resolved: number;
  status: "complete_for_declared_scope" | "partial" | "not_assessed";
  surveyed: number;
  unresolved: number;
}
/**
 * 一个调查过的候选在覆盖报告里的结论。``outcome`` 决定分母里的归属：已解决（可购买，或已证实不可得）、未解决（资料未知或
 * 缺口）、排除（不在声明范围）。三种状态分开写，确定不可得是已确定的调查结果，但不是可购买的方案。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InventoryItem".
 */
export interface InventoryItem {
  candidate_id: string;
  compile_status: "compiled_available" | "compiled_unknown" | "not_compiled";
  data_status: "complete" | "price_unknown" | "incomplete" | "not_needed" | "not_assessed";
  outcome: "resolved" | "unresolved" | "excluded";
  reason_zh: string;
  supply_status: "offered" | "not_offered" | "unknown";
}
/**
 * E3 发布的能力清单（E5 只能呈现这里有的选择）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductCapabilities".
 */
export interface ProductCapabilities {
  abilities?: AbilityOption[];
  age_max: number;
  age_min: number;
  attribute_kinds?: AttributeKindDef[];
  capabilities_id: string;
  /**
   * @minItems 1
   */
  cities: [CityOption, ...CityOption[]];
  /**
   * @minItems 6
   * @maxItems 6
   */
  dimensions: [
    DimensionDefinition,
    DimensionDefinition,
    DimensionDefinition,
    DimensionDefinition,
    DimensionDefinition,
    DimensionDefinition,
  ];
  free_text_max_chars: 2000;
  hard_requirement_options?: HardRequirementOption[];
  health_questionnaire: HealthQuestionnaire;
  interview?: "reserved_not_implemented";
  limits?: string[];
  /**
   * @minItems 1
   */
  material_leaves: [MaterialLeafDefinition, ...MaterialLeafDefinition[]];
  meta: ProductMeta;
  /**
   * @minItems 1
   */
  money_bases: [MoneyBasisOption, ...MoneyBasisOption[]];
  money_input_rule_zh: string;
  occupation: OccupationCapability;
  points: PointsRules;
  /**
   * @minItems 1
   */
  start_scenario: [string, ...string[]];
  unsupported?: string[];
}
/**
 * 一项可以由用户确认的资格事实（不从职称自动授予）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AbilityOption".
 */
export interface AbilityOption {
  ability_id: string;
  ask_when: string;
  label: string;
}
/**
 * 属性偏好的种类（品牌、菜系、制作方式、档次、服务质量、上门顾虑……）：本版的支持状态与用途。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AttributeKindDef".
 */
export interface AttributeKindDef {
  kind: string;
  label: string;
  note: string;
  support: "supported" | "partially_supported" | "unsupported";
  /**
   * @minItems 1
   */
  usage: [
    "planning" | "start_state" | "qualification" | "scoring" | "retained_only",
    ...("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[],
  ];
}
/**
 * 一个候选城市：完整身份（国家 / 地理体系 / 代码）与地理边界版本；不用中文名拼接资料。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityOption".
 */
export interface CityOption {
  city_ref: string;
  code: string;
  country: string;
  data_status: "synthetic_fixture" | "real_data";
  geography_version: string;
  label: string;
  note: string;
  scheme: string;
  selectable: boolean;
}
/**
 * 六维之一的统一定义：中性题干、具体的参考改善范围与当前覆盖。全比较组共用，不逐城重新标定用户权重。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DimensionDefinition".
 */
export interface DimensionDefinition {
  definition: string;
  key: "material" | "safety" | "opportunity" | "leisure" | "environment" | "commute";
  label: string;
  reference_improvement: string;
  support: "supported" | "partially_supported" | "unsupported";
  support_note: string;
}
/**
 * 一个受支持的硬要求选项：有定义、有资料、能按月核验。**核验只单独报告，环境不拦截计划**（``enforced_by_execution``）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HardRequirementOption".
 */
export interface HardRequirementOption {
  compiles_to: string;
  enforced_by_execution?: false;
  label: string;
  maximum: number;
  minimum: number;
  option_id: string;
  parameter: string;
  step: number;
  unit: string;
  verification: string;
}
/**
 * 独立健康问卷（不占六维 100 分，不是第七个评分权重）。题目是暂定文案，**未经量表验证**。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthQuestionnaire".
 */
export interface HealthQuestionnaire {
  common_prompt: string;
  /**
   * @minItems 4
   * @maxItems 4
   */
  options: [HealthOptionDef, HealthOptionDef, HealthOptionDef, HealthOptionDef];
  /**
   * @minItems 1
   */
  questions: [HealthQuestion, ...HealthQuestion[]];
  rules: string[];
  status: "draft_wording_not_validated";
  unanswered_rule: string;
  version: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthOptionDef".
 */
export interface HealthOptionDef {
  label: string;
  meaning: string;
  option_id: "agree" | "disagree" | "no_clear_tendency" | "unsure";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthQuestion".
 */
export interface HealthQuestion {
  question_id: string;
  text: string;
}
/**
 * 物质细项之一：含义、支持状态与说明。细分是父项内部的相对重要性，不是开支比例。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MaterialLeafDefinition".
 */
export interface MaterialLeafDefinition {
  definition: string;
  key: string;
  label: string;
  note: string;
  support: "supported" | "partially_supported" | "unsupported";
}
/**
 * 一种受支持的现金口径：币种、金额时期、含义与换算规则。没有列出的组合没有合法换算，明确阻断。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MoneyBasisOption".
 */
export interface MoneyBasisOption {
  conversion_rule: string;
  currency: string;
  meaning: "liquid_funds_v1";
  note: string;
  numeraire_id: string;
  price_period: string;
}
/**
 * 职业匹配能力：受控词表与明确规则，用户确认少量可读候选，不填职业代码。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationCapability".
 */
export interface OccupationCapability {
  concept_count: number;
  /**
   * @minItems 1
   */
  languages: [string, ...string[]];
  max_candidates: number;
  method: "controlled_vocabulary_alias_match_v1";
  note: string;
  users_enter_codes?: false;
  vocabulary_version: string;
}
/**
 * 定和量表的规则：合计、手工输入的小数位、显示小数位、精确分数的规范写法与均分的精确值。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PointsRules".
 */
export interface PointsRules {
  canonical_form: string;
  display_decimals: number;
  equal_split_material: string;
  equal_split_six: string;
  manual_entry: string;
  manual_max_decimals: number;
  rules: string[];
  total: 100;
}
/**
 * 一次运行前检查。``preflight_fingerprint`` 覆盖除它本身以外的全部内容；确认画像或运行身份变了，旧预检查失效。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductPreflight".
 */
export interface ProductPreflight {
  binding?: RunBinding | null;
  cities?: CityPreflight[];
  clones_per_city: number;
  confirmed: ConfirmedRef;
  executable: CapabilityStatus;
  feedback: CapabilityStatus;
  issues?: ProductIssue[];
  meta: ProductMeta;
  planner: PlannerAndCost;
  preflight_fingerprint: string;
  preflight_id: string;
  scope_zh?: string[];
  scorable: CapabilityStatus;
  start?: StartSummary | null;
  status: "ready" | "blocked";
  years: number;
}
/**
 * 预检查绑定的运行身份：编译出的 E1 输入、协议、评分、评价、提示模板、规划器与运行清单的内容身份。变了就要重新预检查。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunBinding".
 */
export interface RunBinding {
  compiled_inputs: {
    [k: string]: string;
  };
  evaluation_spec_id: string;
  identities_fingerprint: string;
  planner_id: string;
  planner_kind: "mock" | "llm";
  prompt_template: string;
  protocol_id: string;
  scoring_content_fingerprint: string;
  scoring_version: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityPreflight".
 */
export interface CityPreflight {
  city_ref: string;
  coverage?: CityCoverageSummary | null;
  data_gaps?: ProductIssue[];
  executable: CapabilityStatus;
  feedback: CapabilityStatus;
  label: string;
  option_inventory_status: "complete_for_declared_scope" | "partial" | "not_assessed";
  scorable: CapabilityStatus;
  start_job_label?: string | null;
}
/**
 * 一座城的偏好覆盖摘要（E1 覆盖报告的投影）。不是阈值，也不解锁不完整的总分。
 *
 * * ``common_weight``：整个比较组**共同**覆盖的权重（每座城资料都齐、规则都有的叶子之和；各城相同，城市比较只在这部分上是同口径的）；
 * * ``unknown_weight``：**这座城**资料未知的正权重之和（这些月份的总分会不完整，不补 0、不删掉再重新归一化）；
 * * ``no_component_weight``：本版评分没有分项的正权重之和（各城相同）；
 * * ``unsupported_positive_leaves``：**这座城**资料未知或没有评分分项的正权重叶子。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityCoverageSummary".
 */
export interface CityCoverageSummary {
  common_weight: ExactNumber;
  label: string;
  no_component_weight: ExactNumber;
  unknown_weight: ExactNumber;
  unsupported_positive_leaves?: string[];
}
/**
 * 一个精确数：``exact`` 是规范分数写法（参与计算与比较），``display`` 只供显示（四舍五入，不回流计算）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ExactNumber".
 */
export interface ExactNumber {
  display: string;
  exact: string;
}
/**
 * 一种能力在当前输入与声明范围内的状态。**不保证**未来每份计划都合法、每个月都能完整评分。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CapabilityStatus".
 */
export interface CapabilityStatus {
  reasons?: string[];
  status: "yes" | "no" | "partial" | "unknown";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedRef".
 */
export interface ConfirmedRef {
  confirmed_id: string;
  content_fingerprint: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PlannerAndCost".
 */
export interface PlannerAndCost {
  call_limit: number;
  description_zh: string;
  max_cost?: string | null;
  paid: boolean;
  planner_id: string;
  planner_kind: "mock" | "llm";
}
/**
 * 标准开局摘要：同一用户跨城、跨重复序号相同的个人条件。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "StartSummary".
 */
export interface StartSummary {
  abilities?: string[];
  age_years: number;
  cash: ProductMoney;
  monthly_energy_note: string;
  monthly_time_note: string;
  occupation_label: string;
  rule: string;
  /**
   * @minItems 1
   */
  scenario_zh: [string, ...string[]];
}
/**
 * 面向产品的结果：只投影 E1 已算好的报告与生活事实，不另算“更好看”的分数，不排序，不给现实推荐结论。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProductResult".
 */
export interface ProductResult {
  cities?: CityResult[];
  clones?: CloneResult[];
  comparisons?: ComparisonLine[];
  confirmed: ConfirmedRef;
  evaluation_status: "generated" | "not_generated";
  limitations_zh?: string[];
  meta: ProductMeta;
  not_generated_reason?: string | null;
  option_coverage?: OptionCoverageLine[];
  planner: PlannerAndCost;
  preflight: PreflightRef;
  reading_guide_zh?: string[];
  real_model_calls: number;
  result_fingerprint: string;
  result_id: string;
  run_id: string;
  run_stage: string;
  sources_zh?: string[];
}
/**
 * 一座城的结果：原计划 N = C + E + M；完整者均值的分母是 C；模拟缺失范围的分母是 C + M（出局者不进）。空值保持为空。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityResult".
 */
export interface CityResult {
  city_ref: string;
  complete: number;
  exit_share: ExactNumber;
  exits: number;
  hard_requirements?: RequirementSummaryLine[];
  headline_zh: string;
  label: string;
  mean_denominator: number;
  mean_j?: ExactNumber | null;
  mean_l?: ExactNumber | null;
  mean_v?: ExactNumber | null;
  missing: number;
  missing_reasons?: MissingReasonLine[];
  missing_share: ExactNumber;
  notes_zh?: string[];
  planned: number;
  range_denominator: number;
  range_lower?: ExactNumber | null;
  range_upper?: ExactNumber | null;
}
/**
 * 一条硬要求在一座城的核验人数。未核验不等于核验过、没有问题。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RequirementSummaryLine".
 */
export interface RequirementSummaryLine {
  checked: number;
  need: string;
  not_checked: number;
  planned: number;
  satisfied_every_month: number;
  satisfied_in_lived_months: number;
  unknown: number;
  violated: number;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MissingReasonLine".
 */
export interface MissingReasonLine {
  count: number;
  reason: string;
  reason_zh: string;
}
/**
 * 一个分身：C / E / M 分类与原因、L / V / J（精确值与显示值）、事件链与硬要求核验、生活事实。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CloneResult".
 */
export interface CloneResult {
  category?: ("C" | "E" | "M") | null;
  category_zh: string;
  city_ref: string;
  clone_ref: string;
  event_chain?: string[];
  explanation: PublicExplanation;
  hard_requirements?: RequirementLine[];
  j?: ExactNumber | null;
  l_actual?: ExactNumber | null;
  missing_reason?: string | null;
  missing_reason_zh?: string | null;
  range_lower?: ExactNumber | null;
  range_upper?: ExactNumber | null;
  reference_plan?: string | null;
  repeat_index: number;
  v_reference?: ExactNumber | null;
  years?: YearFacts[];
}
/**
 * 给用户看的状态说明（E3-A 收尾 F5）：只由结构化类别生成——类别代码、中文原因、影响与下一步，加一个不含内部内容的审计编号。
 *
 * 不透传异常原文、内部路径或参数；完整原文留在运行归档或内部诊断里，``audit_ref`` 指向它（运行编号、分身与记录文件）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PublicExplanation".
 */
export interface PublicExplanation {
  audit_ref?: string | null;
  category: string;
  impact_zh: string;
  next_action_zh: string;
  reason_zh: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RequirementLine".
 */
export interface RequirementLine {
  months_not_lived: number;
  months_not_satisfied: number;
  months_satisfied: number;
  months_unknown: number;
  need: string;
  summary: string;
}
/**
 * 一个分身一年的生活事实（取自 E1 的结算记录与资源账；不含模型理由与内部评分对象）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "YearFacts".
 */
export interface YearFacts {
  cash_end?: ProductMoney | null;
  cash_start: ProductMoney;
  devices?: ConvenienceDevice[];
  food?: string[];
  health_months?: string[];
  household?: HouseholdHours[];
  housing?: string[];
  jobs?: string[];
  months_lived: number;
  notes?: string[];
  paid_total: ProductMoney;
  services?: ConvenienceService[];
  status: string;
  training?: string[];
  year_index: number;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConvenienceDevice".
 */
export interface ConvenienceDevice {
  label: string;
  months_in_service: number[];
  opportunity_id: string;
}
/**
 * 本人每月实际做的家务小时（资源账的事实，精确值）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HouseholdHours".
 */
export interface HouseholdHours {
  monthly_hours: string[];
  task: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConvenienceService".
 */
export interface ConvenienceService {
  label: string;
  months_covered: number[];
  months_not_obtained: number[];
  months_planned: number[];
  opportunity_id: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ComparisonLine".
 */
export interface ComparisonLine {
  city_a: string;
  city_b: string;
  direction: "a_higher" | "b_higher" | "undetermined" | "not_available";
  note_zh: string;
  range_difference_high?: ExactNumber | null;
  range_difference_low?: ExactNumber | null;
}
/**
 * 选项目录覆盖的摘要（与实际轨迹能否评分是两件事）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OptionCoverageLine".
 */
export interface OptionCoverageLine {
  city_ref: string;
  excluded: number;
  note_zh: string;
  resolved: number;
  status: "complete_for_declared_scope" | "partial" | "not_assessed";
  surveyed: number;
  unresolved: number;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PreflightRef".
 */
export interface PreflightRef {
  preflight_fingerprint: string;
  preflight_id: string;
}
/**
 * 一份草稿的审阅：E3 给出的可读解释与全部问题。``confirmable`` 为假时，``issues`` 里有阻断项。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProfileReview".
 */
export interface ProfileReview {
  abilities?: string[];
  age?: number | null;
  cities?: string[];
  confirmable: boolean;
  draft: DraftRef;
  free_text_chars?: number;
  health: HealthReviewItem[];
  issues?: ProductIssue[];
  items: ItemStatus[];
  material: AllocationReview;
  meta: ProductMeta;
  money: MoneyReview;
  occupation: OccupationReview;
  pending_confirmations?: string[];
  review_id: string;
  six_dimensions: AllocationReview;
  statements?: StatementReview[];
  summary_zh?: string[];
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthReviewItem".
 */
export interface HealthReviewItem {
  answer_state: "provided" | "unsure" | "unanswered";
  meaning: string;
  option?: ("agree" | "disagree" | "no_clear_tendency") | null;
  option_label?: string | null;
  question_id: string;
  text: string;
}
/**
 * 一项输入的四类状态汇总：答案、支持、确认与用途（必要时写执行是否保障）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ItemStatus".
 */
export interface ItemStatus {
  answer_state: "unanswered" | "provided" | "indifferent" | "unsure";
  confirmation: "pending" | "confirmed" | "invalidated";
  execution_guaranteed?: boolean | null;
  item: string;
  label: string;
  note: string;
  support: "supported" | "partially_supported" | "unsupported";
  usage: ("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[];
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AllocationReview".
 */
export interface AllocationReview {
  lines?: AllocationLine[];
  mode?: ("manual" | "equal_split") | null;
  status: "complete" | "incomplete" | "not_applicable";
  total?: ExactNumber | null;
}
/**
 * 一个维度或细项：状态、精确值与显示值（显示只供阅读）、支持状态与用途。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AllocationLine".
 */
export interface AllocationLine {
  display?: string | null;
  exact?: string | null;
  key: string;
  label: string;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  support: "supported" | "partially_supported" | "unsupported";
  usage: ("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[];
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MoneyReview".
 */
export interface MoneyReview {
  amount_text?: string | null;
  conversion_rule?: string | null;
  converted?: ProductMoney | null;
  currency?: string | null;
  meaning: string;
  price_period?: string | null;
  status: "convertible" | "blocked" | "missing";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationReview".
 */
export interface OccupationReview {
  candidates?: OccupationCandidate[];
  message_zh: string;
  raw_text?: string | null;
  selected?: string | null;
  status: "explicit" | "ambiguous" | "unknown" | "not_provided";
}
/**
 * 一个可读的职业候选：概念 ID（不透明）、名称、职责说明与匹配到的词。不展示分类代码。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationCandidate".
 */
export interface OccupationCandidate {
  candidate_id: string;
  /**
   * @minItems 1
   */
  duties: [string, ...string[]];
  label: string;
  matched_terms?: string[];
}
/**
 * 一条属性偏好或硬要求的审阅结果：原文、支持状态、用途与说明（未支持的不挪进已有权重）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "StatementReview".
 */
export interface StatementReview {
  as_hard_requirement: boolean;
  execution_guaranteed: boolean;
  kind: string;
  message_zh: string;
  raw_text: string;
  ref: string;
  support: "supported" | "partially_supported" | "unsupported";
  usage: ("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[];
}
/**
 * 一次运行的进度（断线后用运行 ID 查询；不重发付费规划）。请求成功、计划可执行、生活完成、评分完整分别记录。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunProgress".
 */
export interface RunProgress {
  clones?: CloneProgress[];
  cost: CostStatus;
  meta: ProductMeta;
  pause?: PublicExplanation | null;
  report_available: boolean;
  resumable: boolean;
  resume_note: string;
  run_id: string;
  stage: "queued" | "running" | "completed" | "paused" | "halted" | "interrupted" | "budget_exhausted";
  stage_zh: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CloneProgress".
 */
export interface CloneProgress {
  city_ref: string;
  clone_ref: string;
  status: string;
  status_zh: string;
  years_committed: number;
}
/**
 * 费用状态：离线 mock 为零；真实模型只能写“按报价估算”，以供应商账单为准。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CostStatus".
 */
export interface CostStatus {
  call_limit: number;
  calls_used: number;
  estimated_cost?: string | null;
  kind: "offline_mock_zero_cost" | "estimated_from_quote";
  note: string;
}
/**
 * 提交回执：接受、重复提交（返回原运行 ID，不再生成新的一批分身）或拒绝（附原因）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunReceipt".
 */
export interface RunReceipt {
  idempotency_key: string;
  meta: ProductMeta;
  reasons?: ProductIssue[];
  run_id?: string | null;
  service: "offline_bridge" | "contract_example_only";
  status: "accepted" | "duplicate_same_run" | "rejected";
}
/**
 * 提交一次运行：引用确认画像与预检查，带幂等键。相同确认的重复点击只生成同一运行；输入或配置变了必须重查。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunSubmission".
 */
export interface RunSubmission {
  confirmed: ConfirmedRef;
  idempotency_key: string;
  meta: ProductMeta;
  preflight: PreflightRef;
}
/**
 * 一份原始答案草稿（逐项保存状态）。原始答案、候选解释与确认画像分层保存：这里只有原始答案。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "UserInputDraft".
 */
export interface UserInputDraft {
  abilities?: AbilityClaim[];
  age: AgeAnswer;
  attribute_preferences?: AttributePreferenceAnswer[];
  cash: CashAnswer;
  cities: CitiesAnswer;
  draft_id: string;
  evidence?: EvidenceRef[];
  free_text: FreeTextAnswer;
  hard_requirements?: HardRequirementAnswer[];
  health: HealthAnswers;
  input_channel?: "form";
  material: AllocationAnswer;
  meta: ProductMeta;
  occupation: OccupationAnswer;
  revision: number;
  six_dimensions: AllocationAnswer;
}
/**
 * 用户自己确认的一项资格事实（只在相关岗位需要时追问；不从职称推断）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AbilityClaim".
 */
export interface AbilityClaim {
  ability_id: string;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AgeAnswer".
 */
export interface AgeAnswer {
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  value?: number | null;
}
/**
 * 一条属性偏好（品牌、菜系、上门顾虑……）：原文与它挂在哪项需求下；``as_hard_requirement`` 表示用户想把它当不可妥协的要求。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AttributePreferenceAnswer".
 */
export interface AttributePreferenceAnswer {
  as_hard_requirement?: boolean;
  kind: string;
  need: string;
  preference_id: string;
  raw_text: string;
}
/**
 * 开始时可用于生活的流动资金：金额文字、币种、金额时期与口径（不是总资产、信用额度或月薪）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CashAnswer".
 */
export interface CashAnswer {
  amount_text?: string | null;
  currency?: string | null;
  meaning?: "liquid_funds_v1";
  price_period?: string | null;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CitiesAnswer".
 */
export interface CitiesAnswer {
  city_refs?: string[];
  state: "unanswered" | "provided" | "indifferent" | "unsure";
}
/**
 * 可选文字。超过 2,000 字符**不截断**：校验时报错，由用户自己删减。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "FreeTextAnswer".
 */
export interface FreeTextAnswer {
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  text?: string | null;
}
/**
 * 一条硬要求：选了受支持的选项（带参数），或只写了原文（本版未支持，只留存）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HardRequirementAnswer".
 */
export interface HardRequirementAnswer {
  option_id?: string | null;
  parameter_value?: number | null;
  raw_text?: string | null;
  requirement_id: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthAnswers".
 */
export interface HealthAnswers {
  items?: HealthItemAnswer[];
  questionnaire_version: string;
}
/**
 * 一题的作答：四个选项之一；``None`` = 未答（与“暂不确定”分开保存）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HealthItemAnswer".
 */
export interface HealthItemAnswer {
  choice?: ("agree" | "disagree" | "no_clear_tendency" | "unsure") | null;
  question_id: string;
}
/**
 * 一组定和分配的作答。``mode = equal_split`` 表示用户**主动**选择了平均分配（不预选、不默认）；``None`` 表示还没开始分配。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AllocationAnswer".
 */
export interface AllocationAnswer {
  entries?: {
    [k: string]: PointEntry;
  };
  mode?: ("manual" | "equal_split") | null;
}
/**
 * 一个维度或细项的作答：已提供时写数值文字（精确十进制或规范分数），其余状态不写数值。明确 0 写 ``"0"``。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PointEntry".
 */
export interface PointEntry {
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  value_text?: string | null;
}
/**
 * 职业：原文与语言；用户在候选里选中的概念（不透明 ID，不是职业代码）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationAnswer".
 */
export interface OccupationAnswer {
  language?: string;
  raw_text?: string | null;
  selected_candidate?: string | null;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
}
/**
 * 服务需要本人每月花多少小时参与（不是服务人员的工作时间）。设备不适用；服务未知时不能编入（不当成 0）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ParticipationField".
 */
export interface ParticipationField1 {
  burden_per_hour_text?: string | null;
  facts?: string[];
  hours_text?: string | null;
  status: "known" | "unknown" | "not_applicable";
}
