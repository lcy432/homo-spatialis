/* eslint-disable */
/**
 * 由 web/scripts/contract-gen.mjs 从 contracts/product/v3/*.schema.json 生成（结构版本 e3a-product-schema-v3.5）。
 * 不要手改：共享结构由 E3 维护（src/common/product_contracts.py），结构变了就重新运行 npm run contract:gen。
 */

/**
 * 全部 36 份共享消息（只为生成类型而合成的根，不是一份真实消息）
 */
export interface ProductContracts {
  CatalogCompilation: CatalogCompilation;
  ConfirmedProfile: ConfirmedProfile;
  ConfirmedProfileView: ConfirmedProfileView;
  DeletionReceipt: DeletionReceipt;
  DisplayAttachment: DisplayAttachment;
  DisplayEnvelope: DisplayEnvelope;
  DraftMigrateRequest: DraftMigrateRequest;
  DraftSaveRequest: DraftSaveRequest;
  DraftView: DraftView;
  GeographyDisplayBundle: GeographyDisplayBundle;
  LifeItemCompilation: LifeItemCompilation;
  OpportunityCandidate: OpportunityCandidate;
  OptionInventoryCoverage: OptionInventoryCoverage;
  ProductCapabilities: ProductCapabilities;
  ProductPreflight: ProductPreflight;
  ProductResult: ProductResult;
  ProfileConfirmReply: ProfileConfirmReply;
  ProfileConfirmRequest: ProfileConfirmRequest;
  ProfileList: ProfileList;
  ProfileReview: ProfileReview;
  ReviewReply: ReviewReply;
  RunIntentCreateRequest: RunIntentCreateRequest;
  RunIntentView: RunIntentView;
  RunList: RunList;
  RunProgress: RunProgress;
  RunReceipt: RunReceipt;
  RunStartPreview: RunStartPreview;
  RunStartPreviewRequest: RunStartPreviewRequest;
  RunSubmission: RunSubmission;
  RunSubmitReply: RunSubmitReply;
  RunSubmitRequest: RunSubmitRequest;
  ServiceError: ServiceError;
  SessionExchangeRequest: SessionExchangeRequest;
  SessionView: SessionView;
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
  schema_version: "e3a-product-schema-v3.3" | "e3a-product-schema-v3.4" | "e3a-product-schema-v3.5";
  vocabulary_version: string;
}
/**
 * 一份资料包的身份：编号、内容指纹与状态（合成测试资料，或冻结的真实资料包）。
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
  interests: ConfirmedInterests;
  material: ConfirmedMaterial;
  meta: ProductMeta;
  money: ConfirmedMoney;
  occupation: ConfirmedOccupation;
  person_ref: string;
  qualifications?: ConfirmedQualifications | null;
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
 * 确认后的兴趣与口味（DECISIONS/0041）。只有 ``confirmed`` 带类别；其余状态分开保存、都按中性规则评分。
 *
 * 类别只进入共同、有限的匹配规则（系数是公开登记的模型假设，不是对个人效用的测量）；不改变六维点数、现金、资格或硬要求。
 * 用户写的原文不管选了哪种作答状态都原样保留（``leisure_text`` / ``taste_text``）；没认出的片段另列在 ``unrecognized_text``
 * （本版未支持），不交给评分。``scope_zh`` 逐项说明这次按什么个性化、哪些没有个性化（未答、暂不确定、没有固定爱好、通用评价
 * 分开写，不宣称已经个性化）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedInterests".
 */
export interface ConfirmedInterests {
  cuisines?: string[];
  leisure_categories?: string[];
  leisure_status: "unanswered" | "unsure" | "no_fixed_hobby" | "generic" | "confirmed";
  leisure_text?: string | null;
  matching_note_zh: string;
  /**
   * @minItems 2
   */
  scope_zh: [string, string, ...string[]];
  taste_status: "unanswered" | "unsure" | "no_preference" | "confirmed";
  taste_text?: string | null;
  unrecognized_text?: string[];
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
 * 确认的定和分配：规范精确分数（参与计算）与显示文字（只供阅读）。独立评分（v3.5）另外保存原始评分、原始合计与换算规则；
 * E1 编译只读 ``points``（换算后的最终权重）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedAllocation".
 */
export interface ConfirmedAllocation {
  display: {
    [k: string]: string;
  };
  mode: "manual" | "equal_split" | "ratings_normalized_v1";
  points: {
    [k: string]: string;
  };
  rating_total?: string | null;
  ratings?: {
    [k: string]: string;
  } | null;
  rule?: "ratings_normalized_v1" | null;
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
 * 确认的职业。``catalog_selected``：从输入目录直接选中，没有原文，记下目录的版本与内容身份；``explicit`` / ``ambiguous``：
 * 兼容的文字匹配，保留原文。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedOccupation".
 */
export interface ConfirmedOccupation {
  catalog_fingerprint?: string | null;
  catalog_version?: string | null;
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
  match_status: "explicit" | "ambiguous" | "catalog_selected";
  raw_text?: string | null;
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
 * 确认的学历、证书与相关经验答案（v3.4）。``ConfirmedProfile.abilities`` 由它按 ``rule`` 换算；``source = ability_claims``
 * 表示用的是兼容的逐项资格确认，``none`` 表示什么都没答（不授予任何资格）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedQualifications".
 */
export interface ConfirmedQualifications {
  certificates?: string[];
  certificates_state: "unanswered" | "provided" | "unsure";
  education_option?: string | null;
  education_state: "unanswered" | "provided" | "unsure";
  experience?: ExperienceEntry[];
  experience_state: "unanswered" | "provided" | "unsure";
  rule?: "confirmed_experience_mapping_v1";
  source: "structured_answers" | "ability_claims" | "none";
}
/**
 * 一段相关工作经历：职业概念（输入目录里经验问题列出的相关职业）与整年数。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ExperienceEntry".
 */
export interface ExperienceEntry {
  concept_id: string;
  years: number;
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
 * 读取本人的一份确认画像（``GET /api/v1/profiles/{confirmed_id}``）：服务端摘要（日期）与原确认画像。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedProfileView".
 */
export interface ConfirmedProfileView {
  confirmed: ConfirmedProfile;
  meta: ProductMeta;
  summary: ConfirmedProfileSummary;
}
/**
 * 一份确认画像的服务端摘要（E3C 接口补齐）：首次确认时间与可以用来准备新模拟的截止时间都读服务库，重复确认、查询都不续期。
 * 它不是确认画像本体的一部分，不进入画像的内容身份；画像到期只影响准备新的模拟，已经准备的意图用自己的副本与期限。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConfirmedProfileSummary".
 */
export interface ConfirmedProfileSummary {
  confirmed_id: string;
  content_fingerprint: string;
  draft_id: string;
  draft_revision: number;
  expires_at: string;
  first_confirmed_at: string;
}
/**
 * 删除回执。运行中删除：立即不可查询、标为删除中、不再批准新的模型请求；已经发出的请求可能完成并记账，不能撤回。收尾之后清掉
 * 内容，标为已删除。保留的最小记录只有不透明身份、次数事件、必要的费用摘要、删除时间与防重记录，不含答案、模型正文或生活故事。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DeletionReceipt".
 */
export interface DeletionReceipt {
  kept_zh?: string[];
  meta: ProductMeta;
  note_zh: string;
  quota: QuotaSummary;
  scope: "run" | "draft" | "all_data";
  status: "deleting" | "deleted";
  target_ref?: string | null;
}
/**
 * 这个专属空间的使用次数：一共 ``total`` 次启动模拟的机会；``used`` 已开始的、``reserved`` 已确认开始但还没有启动的、
 * ``available`` 还能用的。次数不自动重置；删除结果、换设备、重发链接都不改变它。3 次是启动模拟的机会，不保证每次所有分身都完整。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "QuotaSummary".
 */
export interface QuotaSummary {
  available: number;
  reserved: number;
  total: number;
  used: number;
}
/**
 * 即时回复（错误、确认结果）的显示附层：这类回复可能没有可以再读的对象，所以随回复一起给中英两种文字。
 * ``source_view_fingerprint`` 是去掉 ``display`` 之后的原回复的规范 JSON 摘要；``pointer`` 都相对那份原回复。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayAttachment".
 */
export interface DisplayAttachment {
  display_schema_version: "e3-display-v2";
  resource_version?: string | null;
  source_view_fingerprint: string;
  translations: {
    [k: string]: DisplayTranslation;
  };
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayTranslation".
 */
export interface DisplayTranslation {
  fields?: DisplayField[];
  status: "complete" | "partial" | "unavailable";
}
/**
 * 原消息里一个可展示文字位置的显示文字。``pointer`` 是相对原消息（或原回复）的 JSON Pointer，只指向含中文的业务说明文字——
 * 不指向数值、权重、编号、动作枚举、秘密，也不指向用户原文（用户原文保持原文，不翻译）。``key`` 是稳定的文案 / 实体键。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayField".
 */
export interface DisplayField {
  key: string;
  missing_reason?:
    | (
        | "no_binding"
        | "translation_missing"
        | "translation_stale"
        | "source_changed"
        | "ambiguous"
        | "untranslated_param"
        | "resources_unavailable"
        | "resources_invalid"
        | "binding_stale"
        | "binding_unverified"
      )
    | null;
  pointer: string;
  resource_version?: string | null;
  source_locale: "zh" | "en";
  status: "provided" | "missing";
  text?: string | null;
}
/**
 * 一次显示读取（``GET /api/v1/display``）：原公共消息 ``source`` 与它的显示文字同次返回（避免进度更新后错配旧翻译）。页面把
 * ``source`` 当作原业务消息使用，``fields`` 只用来换文字；数字、权限与身份永远取 ``source``。读取只鉴权、读取与纯函数渲染：
 * 不保存答案、不预检查、不恢复、不重放、不调用模型。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayEnvelope".
 */
export interface DisplayEnvelope {
  acknowledgements?: DisplayAcknowledgement[];
  answer_states?: DisplayAnswerState[];
  display_schema_version: "e3-display-v2";
  fields?: DisplayField[];
  id?: string | null;
  kind: "capabilities" | "session" | "draft" | "review" | "profile" | "intent" | "progress" | "result" | "runs";
  locale: "zh" | "en";
  metrics?: MetricDisplaySpec[];
  resource_version?: string | null;
  source:
    | ProductCapabilities
    | SessionView
    | DraftView
    | ReviewReply
    | ConfirmedProfileView
    | RunIntentView
    | RunProgress
    | ProductResult
    | RunList;
  source_view_fingerprint: string;
  status: "complete" | "partial" | "unavailable";
}
/**
 * 审阅里一项必须知悉的事项的说明（只是显示）。``ack_id`` 就是原审阅 ``pending_confirmations`` 里的编号，确认时照旧提交这个编号；
 * 这里不新增、不删除、不代替任何必须知悉的事项，也不表示已经勾选。``statement_pointer`` 指向原审阅里相关的那条陈述（只对
 * “留存的陈述”这一类，相对 ``source``）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayAcknowledgement".
 */
export interface DisplayAcknowledgement {
  ack_id: string;
  statement_pointer?: string | null;
  text: DisplayText;
}
/**
 * 一段同时给出中英两种的显示文字（知悉项、作答状态用）。``zh`` 是中文原文（旧对象没有保存时为空）；``en`` 有译文才给，``status``
 * 与 ``missing_reason`` 说英文有没有、为什么没有。``key`` 是稳定的模板 / 实体键，``resource_version`` 是渲染用的资源版本。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayText".
 */
export interface DisplayText {
  en?: string | null;
  key: string;
  missing_reason?:
    | (
        | "no_binding"
        | "translation_missing"
        | "translation_stale"
        | "source_changed"
        | "ambiguous"
        | "untranslated_param"
        | "resources_unavailable"
        | "resources_invalid"
        | "binding_stale"
        | "binding_unverified"
      )
    | null;
  resource_version?: string | null;
  status: "provided" | "missing";
  zh?: string | null;
}
/**
 * 兴趣（``leisure``）或口味（``taste``）的一种作答状态的名称与含义，按正式状态 ``state`` 给出（不靠列表顺序、不拆冒号）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DisplayAnswerState".
 */
export interface DisplayAnswerState {
  answer: "leisure" | "taste";
  meaning: DisplayText;
  name: DisplayText;
  state:
    | ("unanswered" | "unsure" | "no_fixed_hobby" | "generic" | "described" | "selected")
    | ("unanswered" | "unsure" | "no_preference" | "described" | "selected");
}
/**
 * 结果色阶的显示约定：只声明已有分数的合法数值域与读法，不计算新分数、不公开评分系数。``mean_j`` 是同一城市“完成且评价完整”
 * 分身的 J 的算术平均，当前评价规则下有效值在 [0, 1]（``minimum`` / ``maximum`` 是这个数值域，不是图例窗口）。图例窗口由页面按
 * ADR 0045 第 4 条在同一份结果之内适度放大：同一结果的所有国家、两种地图共用一个窗口，图例写明窗口两端，不暗示最低的城市是零分、
 * 最高的是满分；分数、分母与比较结论都不变。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MetricDisplaySpec".
 */
export interface MetricDisplaySpec {
  comparison_scope: "within_result_only";
  denominator_field: "mean_denominator";
  direction: "higher_model_evaluation";
  evaluation_refs?: string[];
  key: "mean_j";
  maximum: "1";
  minimum: "0";
  notes?: LocalizedText[];
  unit: "model_score";
}
/**
 * 一段中英对照的显示文字；没有英文时 ``en`` 为空（页面显示中文原文并标明英文缺失）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LocalizedText".
 */
export interface LocalizedText {
  en?: string | null;
  zh: string;
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
  input_catalog?: InputCatalog | null;
  interests: InterestCapability;
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
 * 输入目录（v3.4）：职业的分类索引与搜索词、学历 / 证书 / 相关经验问题、兴趣与菜系的分组，以及新流程启用的板块。
 *
 * 它是一份独立的小配置，引用资料包里已有的职业、资格与兴趣键，不新增职业、资格、活动或评分系数。``content_fingerprint`` 是这份
 * 配置与所引用资料包的内容身份：按目录选职业的草稿要带上它，目录换了就要重新选（服务端拒绝过期的目录）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InputCatalog".
 */
export interface InputCatalog {
  catalog_id: string;
  catalog_version: string;
  certificates?: CertificateOption[];
  content_fingerprint: string;
  education?: EducationOption[];
  experience?: ExperienceQuestion[];
  /**
   * @minItems 1
   */
  groups: [CatalogGroup, ...CatalogGroup[]];
  leisure: InterestPanel;
  notes?: string[];
  /**
   * @minItems 1
   */
  occupations: [CatalogOccupation, ...CatalogOccupation[]];
  pack_id: string;
  qualification_states?: QualificationStateText[];
  search: CatalogSearchRules;
  sections: InputSections;
  taste: InterestPanel;
}
/**
 * 一张可以多选的职业证书（中学后非学位证书）：资格编号、名称，以及要求它的职业。学历不会授予它。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CertificateOption".
 */
export interface CertificateOption {
  ability_id: string;
  label: string;
  /**
   * @minItems 1
   */
  occupations: [string, ...string[]];
}
/**
 * 最高学历的一个选项（单选，按从低到高的顺序）。职业证书不在这里，另有证书多选。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "EducationOption".
 */
export interface EducationOption {
  label: string;
  option_id: string;
  order: number;
}
/**
 * 一个相关工作经验问题：只在选中的职业属于 ``applies_to`` 时问；用户在 ``related`` 列出的职业里填做过几年。年数门槛只写在
 * ``prompt`` 的说明里，由 E3 判断（E5 不判断）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ExperienceQuestion".
 */
export interface ExperienceQuestion {
  ability_id: string;
  /**
   * @minItems 1
   */
  applies_to: [string, ...string[]];
  prompt: string;
  question_id: string;
  /**
   * @minItems 1
   */
  related: [string, ...string[]];
}
/**
 * 职业目录的一个浏览分组。分组只用于浏览，不改变职业映射，也不进确认画像。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CatalogGroup".
 */
export interface CatalogGroup {
  group_id: string;
  label: string;
  order: number;
}
/**
 * 兴趣（``leisure``）或口味（``taste``）的多选面板。可以跳过（未答）；选了类别就直接是确认画像里的类别，不需要写文字。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InterestPanel".
 */
export interface InterestPanel {
  /**
   * @minItems 1
   */
  entries: [InterestEntry, ...InterestEntry[]];
  /**
   * @minItems 1
   */
  groups: [InterestGroup, ...InterestGroup[]];
  kind: "leisure" | "taste";
  note: string;
  search_notes?: SearchNote[];
}
/**
 * 面板里的一个类别：能力清单词表里已有的键（不新增类别）、唯一的分组、名称与搜索词（类别名、词表别名与目录补充的叫法，照原文）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InterestEntry".
 */
export interface InterestEntry {
  group_id: string;
  key: string;
  label: string;
  order: number;
  /**
   * @minItems 1
   */
  search_terms: [string, ...string[]];
}
/**
 * 兴趣或菜系面板的一个浏览分组。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InterestGroup".
 */
export interface InterestGroup {
  group_id: string;
  label: string;
  order: number;
}
/**
 * 一组搜索词的说明：它们对应哪些已支持的键（可以为空：本版不支持），以及要告诉用户的话。搜索词照原文，不翻译。
 * 例如“川菜”只找到已支持的“中餐”，并说明本版不区分地方菜；“徒步”找不到任何类别，并说明本版还没有、不会算成健身。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SearchNote".
 */
export interface SearchNote {
  note: string;
  note_id: string;
  points_to?: string[];
  /**
   * @minItems 1
   */
  terms: [string, ...string[]];
}
/**
 * 目录里的一个职业：概念编号（与文字匹配、确认画像用的是同一个概念）、唯一的主分组、名称、职责、搜索词与相关资格。
 *
 * ``search_terms`` 是明确登记的中英文搜索词（名称、资料包别名与目录补充的叫法，例如“程序员”），照原文给出、不翻译，只用来筛选；
 * 搜索框里的文字不保存、不进画像。``qualification_refs`` 是这个职业的开局岗位要求的资格（能力清单 ``abilities`` 里的编号），只供
 * 显示；学历包含关系、证书授予与经验年数的判断都在 E3。分类代码只在内部保留，不在这里。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CatalogOccupation".
 */
export interface CatalogOccupation {
  concept_id: string;
  /**
   * @minItems 1
   */
  duties: [string, ...string[]];
  experience_question?: string | null;
  group_id: string;
  label: string;
  order: number;
  qualification_refs?: string[];
  /**
   * @minItems 1
   */
  search_terms: [string, ...string[]];
}
/**
 * 学历、证书或经验的一种作答状态的名称与含义。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "QualificationStateText".
 */
export interface QualificationStateText {
  meaning: string;
  name: string;
  section: "education" | "certificates" | "experience";
  state: "unanswered" | "provided" | "unsure";
}
/**
 * 目录搜索的规则（E5 在完整的小目录里筛选显示；服务端只认提交的编号，不认搜索词）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CatalogSearchRules".
 */
export interface CatalogSearchRules {
  method: "catalog_search_nfkc_casefold_all_terms_v1";
  no_match: string;
  /**
   * @minItems 1
   */
  rules: [string, ...string[]];
}
/**
 * 新输入流程启用哪些板块。职业按目录直接选（文字描述只为兼容保留，不是必经入口）；兴趣与口味用面板多选；硬要求、属性偏好
 * 与兴趣的文字解释不在新流程里。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InputSections".
 */
export interface InputSections {
  attribute_preferences?: false;
  certificates: boolean;
  education: boolean;
  experience: boolean;
  hard_requirements?: false;
  interest_text?: false;
  leisure?: "panel_select";
  occupation?: "catalog_select";
  occupation_text?: "compatibility_only";
  taste?: "panel_select";
}
/**
 * 兴趣与口味（DECISIONS/0041）：可选的文字描述先形成待确认的解释；用户确认后的类别才进入共同、有限的匹配规则。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InterestCapability".
 */
export interface InterestCapability {
  /**
   * @minItems 1
   */
  cuisines: [VocabularyTerm, ...VocabularyTerm[]];
  /**
   * @minItems 1
   */
  leisure_categories: [VocabularyTerm, ...VocabularyTerm[]];
  /**
   * @minItems 1
   */
  leisure_states_zh: [string, ...string[]];
  matching_note_zh: string;
  /**
   * @minItems 1
   */
  taste_states_zh: [string, ...string[]];
}
/**
 * 有限词表里的一项（休闲活动类别或菜系）：键、显示名与用来解释文字的别名（明确映射，不调用模型猜）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "VocabularyTerm".
 */
export interface VocabularyTerm {
  /**
   * @minItems 1
   */
  aliases: [string, ...string[]];
  key: string;
  label: string;
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
 * 定和量表的规则：合计、手工输入的小数位、显示小数位、精确分数的规范写法与均分的精确值。v3.5 起新问卷用 ``ratings``。
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
  ratings?: RatingsRules | null;
  rules: string[];
  total: 100;
}
/**
 * 独立评分（v3.5，``ratings_normalized_v1``）：每项 0–100，滑条步长 1，数字精调最多 ``max_decimals`` 位小数；不必凑满 100。
 * 全部要回答的项都回答了、原始合计大于 0 之后，E3 按 100 × 评分 ÷ 合计换成精确权重。“同等重视”是用户主动把每项设为 50。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RatingsRules".
 */
export interface RatingsRules {
  equal_weight_rating?: "50";
  max_decimals: number;
  maximum?: 100;
  method: "ratings_normalized_v1";
  minimum?: 0;
  /**
   * @minItems 1
   */
  rules: [string, ...string[]];
  slider_step?: 1;
}
/**
 * 当前会话（``POST /api/v1/session`` 交换邀请之后、``GET /api/v1/session``）：到期时间、本会话的 CSRF token、次数与服务规则。
 * 会话由 HttpOnly Cookie 携带，这里不返回会话秘密；``csrf_token`` 只在修改请求的 ``X-CSRF-Token`` 头里带回。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SessionView".
 */
export interface SessionView {
  csrf_token: string;
  drafts?: DraftSummary[];
  meta: ProductMeta;
  open_intent_id?: string | null;
  quota: QuotaSummary;
  rules: ServiceRules;
  session_expires_at: string;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftSummary".
 */
export interface DraftSummary {
  draft_id: string;
  expires_at: string;
  revision: number;
  updated_at: string;
}
/**
 * 服务的规则（服务端配置产生，网页只显示，不能修改）。``cities``、``clones_per_city``、``years``、``max_cost`` 是不选时的默认
 * 规模与费用上限；能选什么见 ``options``。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ServiceRules".
 */
export interface ServiceRules {
  cities: number;
  clones_per_city: number;
  currency: "CNY" | "USD";
  draft_retention_days: number;
  intent_retention_days: number;
  live_calls_enabled: boolean;
  max_cost: string;
  notes_zh?: string[];
  options: RunOptionsPolicy;
  planner_kind: "mock" | "llm";
  profile_retention_days: number;
  result_retention_days: number;
  runs_total: number;
  session_days: number;
  years: number;
}
/**
 * 服务允许用户为一次模拟选择什么（维护者在服务配置里定，网页只照着显示控件，不另做判断）。
 *
 * * 城市：来自用户确认的画像（不另交一份城市列表），必须是 ``cities_allowed`` 里不重复的非空子集；不会替用户删城或补城；
 * * 每城分身数、年数、这次运行的费用上限：创建运行意图时可以选，省略就用默认值；
 * * 年数取服务允许与资料包支持的交集（``years_allowed``）：资料包没有验证过的年数不开放，不为此生成更多年份的菜单。
 * 规划模型、报价、并发、修订与重试、请求长度上限由维护者控制，不是用户选项。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunOptionsPolicy".
 */
export interface RunOptionsPolicy {
  /**
   * @minItems 1
   */
  cities_allowed: [string, ...string[]];
  clones_per_city: IntRange;
  max_cost: MoneyRange;
  notes_zh?: string[];
  /**
   * @minItems 1
   */
  years_allowed: [number, ...number[]];
  years_default: number;
}
/**
 * 一个整数选项：不选时用 ``default``；允许 ``minimum``–``maximum``（含两端）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "IntRange".
 */
export interface IntRange {
  default: number;
  maximum: number;
  minimum: number;
}
/**
 * 一次运行的费用上限选项（应用侧按报价估算，不是供应商账单上限）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MoneyRange".
 */
export interface MoneyRange {
  currency: "CNY" | "USD";
  default: string;
  maximum: string;
  minimum: string;
}
/**
 * 服务端保存的一份草稿：草稿编号与修订号由服务器分配；``expires_at`` 是最后一次编辑后 30 天。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftView".
 */
export interface DraftView {
  draft: UserInputDraft;
  expires_at: string;
  meta: ProductMeta;
  migration?: DraftMigration | null;
  updated_at: string;
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
  certificates?: CertificatesAnswer;
  cities: CitiesAnswer;
  draft_id: string;
  education?: EducationAnswer;
  evidence?: EvidenceRef[];
  experience?: ExperienceAnswer;
  free_text: FreeTextAnswer;
  hard_requirements?: HardRequirementAnswer[];
  health: HealthAnswers;
  input_channel?: "form";
  leisure_interest?: LeisureInterestAnswer;
  material: AllocationAnswer;
  meta: ProductMeta;
  occupation: OccupationAnswer;
  revision: number;
  six_dimensions: AllocationAnswer;
  taste?: TasteAnswer;
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
 * 职业证书（多选）：``provided`` 加选中的资格编号；**明确提交空列表表示“没有”**，与未答分开保存。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CertificatesAnswer".
 */
export interface CertificatesAnswer {
  selected?: string[];
  state?: "unanswered" | "provided" | "unsure";
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
 * 最高学历（单选）：未答、暂不确定，或选了输入目录里的一个选项。只有选了才授予学历门槛。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "EducationAnswer".
 */
export interface EducationAnswer {
  option_id?: string | null;
  state?: "unanswered" | "provided" | "unsure";
}
/**
 * 相关工作经验（只在选中的职业需要时问）：``provided`` 加经历；明确提交空列表表示“没有相关经验”，与未答分开保存。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ExperienceAnswer".
 */
export interface ExperienceAnswer {
  entries?: ExperienceEntry[];
  state?: "unanswered" | "provided" | "unsure";
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
 * 休闲兴趣：未答、暂不确定、明确没有固定爱好、选择通用休闲评价（“城市有什么合适的就体验什么”），或用文字描述。
 *
 * 文字描述先由 E3 按词表形成待确认的解释；``selected`` 是用户从解释或词表里**明确选中**的类别——只有选了才进入有评分作用的类别。
 * 空白不会被当成“通用评价”的选择。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LeisureInterestAnswer".
 */
export interface LeisureInterestAnswer {
  selected?: string[];
  state?: "unanswered" | "unsure" | "no_fixed_hobby" | "generic" | "described" | "selected";
  text?: string | null;
}
/**
 * 一组定和分配的作答。``mode = equal_split`` 表示用户**主动**选择了平均分配（不预选、不默认）；``None`` 表示还没开始分配。
 * ``ratings_normalized_v1``：独立评分（未答不是 0；“同等重视”由用户主动把每项设为 50）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AllocationAnswer".
 */
export interface AllocationAnswer {
  entries?: {
    [k: string]: PointEntry;
  };
  mode?: ("manual" | "equal_split" | "ratings_normalized_v1") | null;
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
 * 职业。``mode = catalog``（新流程）：从输入目录直接选中的概念编号与那份目录的版本、内容身份，不需要也不保存任何文字（搜索框
 * 里的字只是查找条件）。``mode = text``（兼容）：原文与语言，再在少量候选里选中一个。概念编号不透明，不是职业代码。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationAnswer".
 */
export interface OccupationAnswer {
  catalog_fingerprint?: string | null;
  catalog_version?: string | null;
  language?: string;
  mode?: "text" | "catalog";
  raw_text?: string | null;
  selected_candidate?: string | null;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
}
/**
 * 口味与菜系：未答、暂不确定、明确没有偏好、从面板选中菜系，或用文字描述（兼容：先解释、再由用户选中确认）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "TasteAnswer".
 */
export interface TasteAnswer {
  selected?: string[];
  state?: "unanswered" | "unsure" | "no_preference" | "described" | "selected";
  text?: string | null;
}
/**
 * 这份草稿进入新问卷要做的迁移（``GET`` 草稿时一起给出；没有要迁移的就没有这一项）。
 *
 * 迁移把草稿的版本换成当前版本、清空新问卷不再收集的硬要求与属性偏好（``removed`` 逐条列出），其他答案原样保留；之后要重新审阅
 * 和确认。有要清空的答案时必须用户明确同意（``consent_required``）；只差版本时也要用户点一次。已经确认或已经开始的运行不受影响。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftMigration".
 */
export interface DraftMigration {
  allocation_choice_required?: boolean;
  allocations?: LegacyAllocation[];
  consent_required: boolean;
  /**
   * @minItems 1
   */
  effects_zh: [string, ...string[]];
  from_questionnaire_version: string;
  message_zh: string;
  removed?: RemovedAnswer[];
  to_questionnaire_version: string;
  version_changed: boolean;
}
/**
 * 旧草稿里的一组定和分配（v3.5）：迁移前照原样给你看；它们是当时的百分比分配，不是独立评分，迁移时由你明确选择重新评分或
 * 同等重视，系统不把它们改读成评分。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LegacyAllocation".
 */
export interface LegacyAllocation {
  group: "six_dimensions" | "material";
  lines?: LegacyAllocationLine[];
  mode?: ("manual" | "equal_split") | null;
}
/**
 * 旧草稿里一组分配的一项：原来的作答状态与原来写的数值（百分比，不是独立评分）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LegacyAllocationLine".
 */
export interface LegacyAllocationLine {
  key: string;
  label: string;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  value_text?: string | null;
}
/**
 * 旧草稿里新问卷不再收集的一条答案（迁移说明里逐条列出，不悄悄丢掉）。``summary`` 是 E3 生成的可读写法（受支持的硬要求写成
 * 选项与数值）；``raw_text`` 是用户自己写的原文（照原样，不翻译；受支持的选项没有原文）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RemovedAnswer".
 */
export interface RemovedAnswer {
  raw_text?: string | null;
  ref: string;
  section: "hard_requirements" | "attribute_preferences";
  summary: string;
}
/**
 * 审阅已保存的那一版草稿（``POST /api/v1/drafts/{id}/review``）。审阅绑定草稿编号、修订号、内容与所用资料包。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ReviewReply".
 */
export interface ReviewReply {
  meta: ProductMeta;
  review: ProfileReview;
  reviewed_revision: number;
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
  leisure_interest: InterestReview;
  material: AllocationReview;
  meta: ProductMeta;
  money: MoneyReview;
  occupation: OccupationReview;
  pending_confirmations?: string[];
  qualifications?: QualificationReview | null;
  review_id: string;
  six_dimensions: AllocationReview;
  statements?: StatementReview[];
  summary_zh?: string[];
  taste: InterestReview;
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
 * 一段兴趣或口味的审阅：作答状态、原文、按词表得到的候选、有歧义与没认出的片段、用户已选中的类别与可读说明。
 *
 * ``status``：``confirmable``（有选中的类别，或选择了不需要类别的状态）、``pending_selection``（只有文字、还没选中类别——不能
 * 确认，免得空白或没核对的解释悄悄进入评分）、``invalid``（选中了词表外的类别）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "InterestReview".
 */
export interface InterestReview {
  ambiguous?: string[];
  candidates?: string[];
  message_zh: string;
  selected?: string[];
  state: string;
  status: "confirmable" | "pending_selection" | "invalid";
  text?: string | null;
  unrecognized?: string[];
}
/**
 * 一组定和分配的审阅。独立评分时 ``rating_total`` 是原始评分的合计（回答了的那些），``total`` 是换算后权重的合计（精确 100），
 * ``rule`` 写明换算规则。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "AllocationReview".
 */
export interface AllocationReview {
  lines?: AllocationLine[];
  mode?: ("manual" | "equal_split" | "ratings_normalized_v1") | null;
  rating_total?: ExactNumber | null;
  rule?: "ratings_normalized_v1" | null;
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
  rating_display?: string | null;
  rating_exact?: string | null;
  state: "unanswered" | "provided" | "indifferent" | "unsure";
  support: "supported" | "partially_supported" | "unsupported";
  usage: ("planning" | "start_state" | "qualification" | "scoring" | "retained_only")[];
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
 * ``catalog_selected``：从输入目录直接选中（候选就是那一个职业，名称与职责供核对）；其余是兼容的文字匹配。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "OccupationReview".
 */
export interface OccupationReview {
  candidates?: OccupationCandidate[];
  message_zh: string;
  mode?: "text" | "catalog";
  raw_text?: string | null;
  selected?: string | null;
  status: "explicit" | "ambiguous" | "unknown" | "not_provided" | "catalog_selected";
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
 * 学历、证书与相关经验的审阅：E3 按资料包已有的规则换成资格（学历“至少达到”、证书只授予选中的那张、经验按相关职业与年数，
 * 规则 ``confirmed_experience_mapping_v1``）。职业、职称、自由文字与搜索词都不授予资格。``source``：结构化答案、兼容的逐项资格
 * 确认，或都没有。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "QualificationReview".
 */
export interface QualificationReview {
  ask_experience?: string[];
  certificates?: string[];
  certificates_state: "unanswered" | "provided" | "unsure";
  education_option?: string | null;
  education_state: "unanswered" | "provided" | "unsure";
  experience?: ExperienceEntry[];
  experience_state: "unanswered" | "provided" | "unsure";
  granted?: QualificationGrant[];
  message_zh: string;
  requirements?: QualificationRequirement[];
  source: "structured_answers" | "ability_claims" | "none";
}
/**
 * 一项按答案得到的资格与理由（例如“最高学历：本科 → 满足‘学士学位’及以下的学历门槛”）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "QualificationGrant".
 */
export interface QualificationGrant {
  ability_id: string;
  because: string;
  label: string;
}
/**
 * 选中职业的开局岗位要求的一项资格，以及按现在的答案是否满足（不满足时各城的开局会照实挡下，不自动补）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "QualificationRequirement".
 */
export interface QualificationRequirement {
  ability_id: string;
  label: string;
  met: boolean;
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
 * 一次运行意图的公开视图（网页流程）：阶段与中文说明、当前预检查与确认身份、进度、是否有结果、此刻允许的操作、在等谁、
 * 时间与删除状态、安全解释与支持编号。不含流程内部编号、checkpoint、文件路径、堆栈或内部诊断正文。
 *
 * ``stage = finished`` 只表示本次服务流程结束：结果里的 C / E / M、未知与部分结果照 E1 原样显示；没有结果时 ``result_available``
 * 为假，不编一个。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunIntentView".
 */
export interface RunIntentView {
  allowed_actions?: (
    "submit" | "cancel" | "resume" | "delete" | "view_progress" | "view_result" | "create_new_intent" | "start_again"
  )[];
  confirm_with?: string | null;
  confirmation_source?: ("user_confirm" | "start_authorization") | null;
  confirmed: ConfirmedRef;
  created_at: string;
  deletion: DeletionState;
  expires_at?: string | null;
  expiry_basis: "intent_activity" | "first_finished" | "active_execution" | "unavailable";
  explanation?: PublicExplanation | null;
  intent_id: string;
  meta: ProductMeta;
  preflight?: ProductPreflight | null;
  progress?: RunProgress | null;
  progress_summary: ProgressSummary;
  quota: QuotaSummary;
  result_available: boolean;
  run_id?: string | null;
  settings?: RunSettingsView | null;
  stage:
    | "checking"
    | "awaiting_confirmation"
    | "blocked"
    | "queued"
    | "running"
    | "needs_attention"
    | "finished"
    | "cancelled"
    | "deleting"
    | "deleted"
    | "expired";
  stage_zh: string;
  start?: StartAuthorizationView | null;
  support_ref?: string | null;
  updated_at: string;
  waiting_for: "user" | "maintainer" | "service" | "none";
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
 * via the `definition` "DeletionState".
 */
export interface DeletionState {
  completed_at?: string | null;
  requested_at?: string | null;
  state: "none" | "deleting" | "deleted";
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
  scope_notes?: ScopeNote[];
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
  data_nature?: CityDataNature | null;
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
 * 一座城的资料是怎么来的（DECISIONS/0042）：目录各类机会按来源性质的条数、登记的模型设定、共同先验、治疗的共同假设、
 * 模型活动设定、片区、通勤方式、菜单、税的范围，以及资料包构建报告的计数。
 *
 * 只有公开的来源性质与说明：没有评分参数（η、τ、c、背景量）、抽签或内部状态。``build_status = not_available`` 表示构建报告
 * 不在这份资料包里或与目录对不上——计数未知，不是 0。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityDataNature".
 */
export interface CityDataNature {
  build?: BuildCount[];
  build_status: "from_pack_report" | "not_available";
  city_ref: string;
  common_priors?: CommonPrior[];
  commute_modes?: CommuteModeAvailability[];
  home_areas?: number;
  label: string;
  menus?: MenuSummary[];
  model_plan_activities?: string[];
  named_assumptions?: NamedAssumption[];
  opportunities?: CategorySources[];
  pack_status: "synthetic_fixture_only" | "real_data_frozen";
  summary_zh: string;
  tax?: TaxScope | null;
  /**
   * @minItems 4
   * @maxItems 4
   */
  totals: [SourceKindCount, SourceKindCount, SourceKindCount, SourceKindCount];
  treatment_assumptions_zh?: string[];
  work_areas?: number;
}
/**
 * 资料包构建报告里的一个计数（片区、通勤线、住房候选与排除的原因、放进目录的岗位、菜单……）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "BuildCount".
 */
export interface BuildCount {
  count: number;
  key: string;
  label_zh: string;
}
/**
 * 一项比较组各城共用的背景设定（没有可比资料时登记的共同先验，例如休闲；安全按各都会区的犯罪资料换算，不在这里）：事实的
 * 名称与含义。不制造城市差异；
 * 这里不写它换算成评分输入的规则与参数。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CommonPrior".
 */
export interface CommonPrior {
  fact_id: string;
  label: string;
  meaning_zh: string;
}
/**
 * 这座城的通勤关系表里有没有一种方式：有几条片区之间的线、使用条件与封顶的说明（与给分身看的同一份公开说明）。
 *
 * ``in_relation = False``：关系表里没有这种方式（资料缺失或本城不建模），到岗时不能用它——不当 0 分钟，也不当免费。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CommuteModeAvailability".
 */
export interface CommuteModeAvailability {
  condition_zh?: string | null;
  in_relation: boolean;
  lines: number;
  mode: "walk_bike" | "transit" | "drive";
  mode_zh: string;
}
/**
 * 一类机会的年度菜单（DECISIONS/0042）：每年、每个环境重复给分身看的一份抽样。菜单是接触机会，不是成交或录用。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "MenuSummary".
 */
export interface MenuSummary {
  category: string;
  env_repeats: number;
  items_max: number;
  items_min: number;
  years: number;
}
/**
 * 一条登记的模型设定（来源性质是合成）：设定说明的原文、用到它的机会条数与几个例子。不是资料，也不是某个商品或场所的报价。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "NamedAssumption".
 */
export interface NamedAssumption {
  count: number;
  /**
   * @minItems 1
   * @maxItems 3
   */
  examples: [string] | [string, string] | [string, string, string];
  note_zh: string;
}
/**
 * 目录里一类机会（住房、岗位、食品、服务……）的条数，按来源性质分开数（四种性质都列，没有的写 0）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CategorySources".
 */
export interface CategorySources {
  /**
   * @minItems 4
   * @maxItems 4
   */
  by_kind: [SourceKindCount, SourceKindCount, SourceKindCount, SourceKindCount];
  category: string;
  total: number;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SourceKindCount".
 */
export interface SourceKindCount {
  count: number;
  kind: "observed" | "derived" | "estimated" | "synthetic";
}
/**
 * 这座城适用的税（DECISIONS/0042）：官方 2024 年规则（单身、只有工资收入、标准扣除）或合成测试替身；支持的居住与工作地、
 * 规则名称、官方说明没有的税，以及没有建模的部分（照实写，不当作已经覆盖）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "TaxScope".
 */
export interface TaxScope {
  absent_zh?: string[];
  not_modeled_zh?: string[];
  policy_id: string;
  residence_scope_zh?: string | null;
  rules_zh?: string[];
  status: "official_2024_rules" | "synthetic_test_double";
  status_zh: string;
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
 * 预检查的一条范围说明（DECISIONS/0042）：这版支持什么、不支持什么、为什么。``cities`` 为空表示对全部候选城市都适用。
 * E5 原样展示中文，不另写解释。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ScopeNote".
 */
export interface ScopeNote {
  cities?: string[];
  text_zh: string;
  title_zh: string;
  topic: "occupations" | "tax" | "commute" | "menus" | "unsupported" | "data";
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
 * 一次运行的进度（断线后用运行 ID 查询；不重发付费规划）。请求成功、计划可执行、生活完成、评分完整分别记录。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunProgress".
 */
export interface RunProgress {
  clone_years_planned?: number | null;
  clone_years_settled?: number | null;
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
  updated_at?: string | null;
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
 * ``calls_used`` / ``call_limit`` 数的是**规划候选**（每个分身每年一次正常规划、最多两次修订）；``service_attempts`` 数的是实际发给
 * 服务的尝试（每份候选最多再试一次服务请求），两者不是同一个计数。金额都是应用侧按已记录报价估算（E3C）：``settled_cost`` 是返回了
 * 用量、已核算的；``uncertain_cost`` 是请求可能已经发出、用量不确定、按预留保留的（不改成 0）；``in_flight_cost`` 是已经预留、还没有
 * 结果的；``estimated_cost`` 是三者之和。``max_cost`` 是这次运行的应用侧上限，不是供应商的账单上限。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CostStatus".
 */
export interface CostStatus {
  call_limit: number;
  calls_used: number;
  currency?: ("CNY" | "USD") | null;
  estimated_cost?: string | null;
  in_flight_cost?: string | null;
  kind: "offline_mock_zero_cost" | "estimated_from_quote";
  max_cost?: string | null;
  note: string;
  price_version?: string | null;
  service_attempt_limit?: number | null;
  service_attempts?: number | null;
  settled_cost?: string | null;
  uncertain_attempts?: number | null;
  uncertain_cost?: string | null;
}
/**
 * 等待页的一句当前状态与进度条（DECISIONS/0049）：读取运行意图时按服务库的阶段与 E1 写下的权威进度快照当场生成（只读，没有
 * ``run_id`` 的检查阶段也有）。
 *
 * * ``sentence_zh`` 是一句当前状态，新的一句覆盖上一句（不是执行历史）；``updated_at`` 是这句所依据的记录的时间（进度快照或意图
 *   本身的更新时间），断线时页面照它显示“最后已知”。
 * * ``mode``：``indeterminate`` 不定长——检查、排队、整理结果、还没有第一份进度快照，以及停下时没有快照；``clone_years`` 按已结算的
 *   分身年度数：``settled_clone_years`` 是 E1 进度快照里各分身已结算年数之和（出局或中断之后没有经历的年份不算），
 *   ``planned_clone_years`` 是这次的名单（城市 × 每城分身数，E1 的名单按它冻结）× 年数；``finished`` 只在结果真的可以读时，完成不等于
 *   所有分身都是 C。不按调用次数、不按时间增长；不含内部状态、路径、凭证或评分参数。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProgressSummary".
 */
export interface ProgressSummary {
  mode: "indeterminate" | "clone_years" | "finished";
  phase:
    | "checking"
    | "queued"
    | "simulating"
    | "assembling_result"
    | "finished"
    | "needs_attention"
    | "cancelled"
    | "deleted"
    | "expired";
  planned_clone_years?: number | null;
  sentence_zh: string;
  settled_clone_years?: number | null;
  updated_at: string;
}
/**
 * 一次运行意图的有效设置：创建时由服务端校验并保存，之后的检查、确认、执行、进度、结果与恢复都用这一份（改服务默认值不影响它）。
 * 规划候选上限与服务尝试上限按这次的规模与修订、重试规则算出。离线 mock 不花钱：费用上限照样写用户这次的选择（它进入设置的
 * 身份），服务尝试上限不出现。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunSettingsView".
 */
export interface RunSettingsView {
  call_limit: number;
  /**
   * @minItems 1
   */
  cities: [string, ...string[]];
  clones_per_city: number;
  clones_total: number;
  currency?: ("CNY" | "USD") | null;
  max_cost?: string | null;
  planner_kind: "mock" | "llm";
  service_attempt_limit?: number | null;
  settings_fingerprint: string;
  years: number;
}
/**
 * 这次准备带着的一次开始授权（DECISIONS/0048）。``pending``：检查通过后由服务按它开始；``used``：已经按它开始——确认来源是这份
 * 授权，不是用户在检查之后又点了一次；``void``：条件变了或开始前停下，授权作废，不会再自动开始（``void_reason``）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "StartAuthorizationView".
 */
export interface StartAuthorizationView {
  acknowledged: ("ack:start.one_run" | "ack:start.auto_start" | "ack:start.scope" | "ack:start.cost_cap")[];
  authorized_at: string;
  consent_fingerprint: string;
  mode: "start_when_ready";
  state: "pending" | "used" | "void";
  used_at?: string | null;
  void_reason?:
    | (
        | "conditions_changed"
        | "consent_changed"
        | "options_not_allowed"
        | "paid_calls_disabled"
        | "quota_exhausted"
        | "preflight_blocked"
        | "authorization_mismatch"
        | "data_pack_unavailable"
        | "internal_error"
        | "cancelled"
      )
    | null;
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
  data_nature?: CityDataNature[];
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
  valuation_scope?: ValuationScopeNote[];
  years?: YearFacts[];
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
 * 期末估值的范围说明（DECISIONS/0041 R8）：期末持有的一件装备，参考模板有没有考虑它的用途。公开的安全投影：只有物品、
 * 能力键、剩余月数与固定写法的说明，没有参数、方案比较或未来事件。“模板没有考虑”不是“装备没有价值”。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ValuationScopeNote".
 */
export interface ValuationScopeNote {
  /**
   * @minItems 1
   */
  capabilities: [string, ...string[]];
  kind: "equipment_use_valued" | "equipment_use_not_in_templates";
  label: string;
  months_left: number;
  opportunity_id: string;
  text_zh: string;
}
/**
 * 一个分身一年的生活事实（取自 E1 的结算记录与资源账；不含模型理由与内部评分对象）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "YearFacts".
 */
export interface YearFacts {
  activities?: ActivityLine[];
  car_access?: CarAccessFact | null;
  cash_end?: ProductMoney | null;
  cash_start: ProductMoney;
  commute?: CommuteMonths[];
  consumption?: ConsumptionLine | null;
  device_failures?: DeviceFailureFact[];
  devices?: ConvenienceDevice[];
  food?: string[];
  goods?: GoodsLine[];
  health_months?: string[];
  homes?: HomeFact[];
  household?: HouseholdHours[];
  housing?: string[];
  jobs?: string[];
  months_lived: number;
  notes?: string[];
  paid_total: ProductMoney;
  purchases_not_made?: PurchaseNotMade[];
  services?: ConvenienceService[];
  status: string;
  support?: SupportLine[];
  tax?: TaxFact | null;
  training?: string[];
  year_index: number;
}
/**
 * 一项休闲活动这一年的实际参与（DECISIONS/0041）：参加的月份、次数与参与小时（不含往返与准备），生病暂停的月份，
 * 没能进行的月份（缺少需要的装备能力：没有收费）。次数与小时是精确写法（整数或“分子/分母”）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ActivityLine".
 */
export interface ActivityLine {
  label: string;
  months_done?: number[];
  months_not_done?: number[];
  months_paused?: number[];
  not_done_zh?: string | null;
  opportunity_id: string;
  participation_hours: string;
  sessions: string;
}
/**
 * 用车权（DECISIONS/0042）：开车通勤要这个月有在用的用车权——一项按月付费的普通机会，不买就不能开车。只在这座城的通勤
 * 关系里有需要它的方式时出现。``bought`` = 今年至少有一个月用上了。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CarAccessFact".
 */
export interface CarAccessFact {
  lines?: ConvenienceService[];
  note_zh: string;
  status: "bought" | "not_bought";
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
 * 几个月相同的通勤执行事实（DECISIONS/0042）：E1 每个月唯一的通勤记录，内容完全相同的月份合成一行。
 *
 * ``monthly_paid`` 是这几个月里**每个月**付的通勤费（各方式按月封顶之后的合计）。没有需要到岗的工作、出局当月没有住房时
 * 不写行程与付款（后者是未知，不是 0）；出局或停止之后没有发生的月份不列。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CommuteMonths".
 */
export interface CommuteMonths {
  home_area_id?: string | null;
  home_area_label?: string | null;
  monthly_paid?: ProductMoney | null;
  /**
   * @minItems 1
   */
  months: [number, ...number[]];
  status: "computed" | "no_onsite_work" | "unknown_no_housing";
  status_zh: string;
  trips?: CommuteTrip[];
}
/**
 * 一个到岗岗位一个月实际执行的通勤（取自 E1 的月度通勤记录）：岗位、就业片区、方式、一次往返的分钟与这个月的到岗次数。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CommuteTrip".
 */
export interface CommuteTrip {
  job_label: string;
  job_opportunity_id: string;
  mode: "walk_bike" | "transit" | "drive";
  mode_zh: string;
  onsite_visits: number;
  roundtrip_minutes: number;
  work_area_id: string;
  work_area_label: string;
}
/**
 * 个人消费篮子这一年的事实（E3B 映射修正 §6）：每月实际获得的标准篮子数（精确写法，“0” = 没有，“—” = 那个月没有生活），
 * 计划买却没获得的月份。它是当月的消费服务流，不是可以转卖的资产。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ConsumptionLine".
 */
export interface ConsumptionLine {
  baskets_by_month: string[];
  months_not_obtained?: number[];
  opportunity_ids: string[];
}
/**
 * 一次设备故障与替换结果（DECISIONS/0042）：第几个月月末坏了（下个月起不能用，剩余寿命作废）、坏了几件，预先声明的替换
 * 有没有在下个月按普通购买买到。不含抽签的随机数、月度风险与键。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DeviceFailureFact".
 */
export interface DeviceFailureFact {
  label: string;
  month: number;
  opportunity_id: string;
  replacement_id?: string | null;
  replacement_label?: string | null;
  replacement_month?: number | null;
  replacement_status: "not_declared" | "bought" | "rejected" | "next_year" | "not_reached";
  replacement_status_zh: string;
  units_failed: number;
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
 * 一件不是省家务设备的耐用品（舒适用品、爱好装备）这一年的事实（DECISIONS/0041）：今年买到的月份、在用的月份、年末是否
 * 到期与之后还剩几个月。买到、在用与实际用它参加活动是三件事：装备放在家里不等于参加了活动（见 ``ActivityLine``）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "GoodsLine".
 */
export interface GoodsLine {
  bought_month?: number | null;
  expired: boolean;
  label: string;
  months_in_service?: number[];
  months_left_after_year: number;
  opportunity_id: string;
}
/**
 * 这一年实际住过的一处住房（DECISIONS/0042）：住了哪几个月、在哪个片区，以及这个片区的居民平时上班往返多少分钟（资料或
 * 估计；没有就为空，不当 0）。片区是资料的地理单位（例如 PUMA），不是街区或楼栋，本身不加分。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "HomeFact".
 */
export interface HomeFact {
  area_id?: string | null;
  area_label?: string | null;
  area_typical_roundtrip_minutes?: number | null;
  label: string;
  /**
   * @minItems 1
   */
  months: [number, ...number[]];
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
 * 计划里的一次性购买没有成交（DECISIONS/0041）：没有扣款，也没有生成持有物。文字是固定的公开说明，不透传内部原因。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "PurchaseNotMade".
 */
export interface PurchaseNotMade {
  label: string;
  month?: number | null;
  opportunity_id: string;
  status: "rejected" | "cancelled_when_ill" | "paused" | "not_reached";
  status_zh: string;
}
/**
 * 一项日常支持功能这一年的事实（E3B 映射修正 §5）：计划买的月份、实际获得的月份（付了钱、服务在用）与没获得的月份，由哪项
 * 服务提供。同功能买两份不多算；没获得的月份这项功能就是没有（不会因此出局、生病或少拿工资，也不节省小时）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SupportLine".
 */
export interface SupportLine {
  function: "personal_communication" | "home_connectivity" | "household_supplies";
  label_zh: string;
  months_not_obtained?: number[];
  months_obtained?: number[];
  months_planned?: number[];
  opportunity_ids?: string[];
}
/**
 * 这一年的工资按哪一份税规则预扣（DECISIONS/0042）：官方 2024 年规则，或合成测试替身。范围与没有建模的部分见资料性质。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "TaxFact".
 */
export interface TaxFact {
  policy_id: string;
  status: "official_2024_rules" | "synthetic_test_double";
  status_zh: string;
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
 * 本人的运行列表（``GET /api/v1/runs``），新的在前；已删除的只剩不含内容的条目时不列出。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunList".
 */
export interface RunList {
  meta: ProductMeta;
  quota: QuotaSummary;
  runs?: RunSummary[];
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunSummary".
 */
export interface RunSummary {
  created_at: string;
  expires_at?: string | null;
  expiry_basis: "intent_activity" | "first_finished" | "active_execution" | "unavailable";
  finished_at?: string | null;
  intent_id: string;
  result_available: boolean;
  run_id?: string | null;
  settings?: RunSettingsView | null;
  stage:
    | "checking"
    | "awaiting_confirmation"
    | "blocked"
    | "queued"
    | "running"
    | "needs_attention"
    | "finished"
    | "cancelled"
    | "deleting"
    | "deleted"
    | "expired";
  stage_zh: string;
}
/**
 * 迁移草稿（``POST /api/v1/drafts/{id}/migrate``）：``expected_revision`` 必须等于当前修订号；有要清空的旧答案时
 * ``clear_removed`` 必须为真（用户看过迁移说明并同意），否则拒绝、草稿不变。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftMigrateRequest".
 */
export interface DraftMigrateRequest {
  allocation_choice?: ("rerate" | "equal_weight") | null;
  clear_removed?: boolean;
  expected_revision: number;
}
/**
 * 保存草稿（``PUT /api/v1/drafts/{id}``）：``expected_revision`` 必须等于服务端当前的修订号，否则返回冲突，不覆盖新答案。
 * ``draft.draft_id`` 必须是路径里的编号；服务端写入时修订号加一。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "DraftSaveRequest".
 */
export interface DraftSaveRequest {
  draft: UserInputDraft;
  expected_revision: number;
}
/**
 * 一个资料包、一个显示资源版本的地理显示清单（``GET /api/v1/display/geography``）。地图底图、投影、纹理与标签由页面负责；
 * 这份清单不授予运行资格（能选哪些城市仍由能力清单与服务规则决定），缺少可靠坐标的城市锚点为空、仍可从列表访问。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "GeographyDisplayBundle".
 */
export interface GeographyDisplayBundle {
  cities?: CityGeography[];
  display_schema_version: "e3-display-v2";
  notes?: LocalizedText[];
  pack_fingerprint: string;
  resource_version: string;
  sources?: GeographySource[];
  status: "complete" | "partial" | "unavailable";
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "CityGeography".
 */
export interface CityGeography {
  anchor?: GeographyAnchor | null;
  anchor_status: "available" | "unknown" | "not_applicable";
  city_ref: string;
  code: string;
  country: string;
  country_names: LocalizedText;
  coverage_note: LocalizedText;
  geography_version: string;
  model_kind: "real" | "synthetic";
  names: LocalizedNames;
  scheme: string;
  support_scope: SupportScope;
}
/**
 * 城市的显示锚点：都会区的代表点，不是住宅、主城市中心或都会区边界。经纬度是具名字段（度），坐标系写明。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "GeographyAnchor".
 */
export interface GeographyAnchor {
  crs: "EPSG:4326";
  kind: "principal_city_point" | "metro_internal_point";
  latitude: number;
  longitude: number;
  /**
   * @minItems 1
   */
  source_refs: [string, ...string[]];
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LocalizedNames".
 */
export interface LocalizedNames {
  en?: GeographyNames | null;
  zh: GeographyNames;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "GeographyNames".
 */
export interface GeographyNames {
  official: string;
  short: string;
}
/**
 * 模拟实际支持的居住与工作范围（来自资料包），与都会区本身、资料覆盖范围不一定相同。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SupportScope".
 */
export interface SupportScope {
  note: LocalizedText;
  ref: string;
  residence: LocalizedText;
  version: string;
  work: LocalizedText;
}
/**
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "GeographySource".
 */
export interface GeographySource {
  content_sha256?: string | null;
  dataset: string;
  public_url: string;
  publisher: string;
  source_ref: string;
  version: string;
}
/**
 * 生活扩展候选的编译检查（编译器 ``e3-life-item-compiler-v1``）。编译产物有自己的内容身份 ``compilation_fingerprint``。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LifeItemCompilation".
 */
export interface LifeItemCompilation {
  city_ref: string;
  compilation_fingerprint: string;
  compilation_id: string;
  compiler_version: string;
  meta: ProductMeta;
  scope_zh: string;
  verdicts: LifeItemVerdict[];
}
/**
 * 一项生活扩展候选（完整食品方案、休闲活动、装备、享受型用品或服务）能不能交给 E1 执行（DECISIONS/0041）。
 *
 * ``executable``：放进这座城目录的副本之后，E1 的契约、目录、跨对象与可结算检查都接受，种类与 E1 的执行角色一致；
 * ``rejected``：缺必需的事实或违反规则（例如没有做饭时间、种类与事实对不上、带日期或年龄限制、代替正餐的套餐、寿命不明、
 * 金额口径不对、类别没有声明）；``unavailable``：确定不可得（保留记录，不能执行）；``unsupported``：用了本版词表没有的类别或
 * 功能；``data_unknown``：报价或评分要读的事实未知（不当零价，也不当已知）。原因是固定写法或 E1 的拒绝说明，不含本机路径。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "LifeItemVerdict".
 */
export interface LifeItemVerdict {
  candidate_id: string;
  kind: "food_plan" | "activity" | "equipment" | "enjoyment";
  label: string;
  reasons_zh?: string[];
  status: "executable" | "rejected" | "unavailable" | "unsupported" | "data_unknown";
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
 * 确认的结果：成功时是服务端生成的确认画像与它的服务端摘要（首次确认与截止时间）；被拒时列出原因（草稿改过、知悉项缺失、
 * 审阅过时……），摘要为空。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProfileConfirmReply".
 */
export interface ProfileConfirmReply {
  confirmed?: ConfirmedProfile | null;
  display?: DisplayAttachment | null;
  issues?: ProductIssue[];
  meta: ProductMeta;
  profile_summary?: ConfirmedProfileSummary | null;
  status: "confirmed" | "refused";
}
/**
 * 确认画像（``POST /api/v1/profiles/confirm``）：只带服务端审阅的引用与知悉项；服务端重新校验并生成确认画像。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProfileConfirmRequest".
 */
export interface ProfileConfirmRequest {
  acknowledged?: string[];
  draft_id: string;
  draft_revision: number;
  review_id: string;
}
/**
 * 本人还能用来准备新模拟的确认画像摘要（``GET /api/v1/profiles``），新的在前；已删除、已过期的不列出。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ProfileList".
 */
export interface ProfileList {
  meta: ProductMeta;
  profiles?: ConfirmedProfileSummary[];
}
/**
 * 准备一次模拟（``POST /api/v1/run-intents``）：引用一份确认画像，可以选每城分身数、年数与这次的费用上限（省略就用服务默认值，
 * 能选什么见 ``ServiceRules.options``）。城市来自确认画像。服务端校验后保存这次的有效设置，再排队做运行前检查（不扣次数）。
 *
 * 带 ``start``（一次开始）：检查通过之后由服务按这份授权自动开始，页面不需要、也不应该再发确认；不带就是原来的流程（检查之后由
 * 用户在 ``submit`` 确认开始）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunIntentCreateRequest".
 */
export interface RunIntentCreateRequest {
  clones_per_city?: number | null;
  confirmed_id: string;
  max_cost_cny?: string | null;
  start?: StartWhenReady | null;
  years?: number | null;
}
/**
 * 一次“确认并开始”（``RunIntentCreateRequest.start``）：用户看过设置预览、勾选了全部必要知悉项，授权“检查通过后就按这些设置开始，
 * 只开始一次”。``consent_fingerprint`` 是那份预览的同意身份（服务端重新核对，对不上就拒绝）；``idempotency_key`` 是这一次操作的
 * 幂等键：连点、回复丢失、刷新或重开后重发都带同一个键（页面要把它和这次操作一起保存），服务端返回同一个意图——即使它已经结束、
 * 取消或删除，也不另起运行；同一个键配不同请求是冲突。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "StartWhenReady".
 */
export interface StartWhenReady {
  accept: true;
  /**
   * @minItems 1
   */
  acknowledged: [
    "ack:start.one_run" | "ack:start.auto_start" | "ack:start.scope" | "ack:start.cost_cap",
    ...("ack:start.one_run" | "ack:start.auto_start" | "ack:start.scope" | "ack:start.cost_cap")[],
  ];
  consent_fingerprint: string;
  idempotency_key: string;
  mode: "start_when_ready";
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
  service: "offline_bridge" | "hosted_service" | "contract_example_only";
  status: "accepted" | "duplicate_same_run" | "rejected";
}
/**
 * 设置预览：这次会用的有效设置（城市、每城分身数、年数、规划器与模型种类、费用上限、调用上限）、范围说明、必要知悉项与同意身份。
 *
 * ``consent_fingerprint`` 绑定确认画像、资料包、有效设置（含模型、报价与费用上限）、范围说明的业务版本与知悉项编号；换语言不改它。
 * 费用上限是停机上限，不是预计费用。``start_allowed`` 为假时 ``start_refusal`` 说明此刻为什么不能开始（付费调用没开、次数用完、已经
 * 有一次条件不同的准备）。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunStartPreview".
 */
export interface RunStartPreview {
  /**
   * @minItems 1
   */
  acknowledgements: [StartAcknowledgement, ...StartAcknowledgement[]];
  confirmed: ConfirmedRef;
  consent_fingerprint: string;
  display?: DisplayAttachment | null;
  meta: ProductMeta;
  planner: PlannerAndCost;
  quota: QuotaSummary;
  scope_version: "e3c-start-scope-v1";
  scope_zh?: string[];
  settings: RunSettingsView;
  start_allowed: boolean;
  start_refusal?: ("paid_calls_disabled" | "quota_exhausted" | "intent_conflict") | null;
}
/**
 * 开始之前要知悉的一项（“确认并开始”时一起勾选）。同意身份只记 ``ack_id`` 与范围说明的业务版本，文字随语言变。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "StartAcknowledgement".
 */
export interface StartAcknowledgement {
  ack_id: "ack:start.one_run" | "ack:start.auto_start" | "ack:start.scope" | "ack:start.cost_cap";
  text_zh: string;
}
/**
 * 设置预览（``POST /api/v1/run-start-preview``）：与准备模拟同样的确认画像引用与运行选项（省略的用服务默认值）。只读：不编译生活
 * 输入、不排队、不预留次数、不调用模型、不写库。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunStartPreviewRequest".
 */
export interface RunStartPreviewRequest {
  clones_per_city?: number | null;
  confirmed_id: string;
  max_cost_cny?: string | null;
  years?: number | null;
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
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunSubmitReply".
 */
export interface RunSubmitReply {
  intent: RunIntentView;
  meta: ProductMeta;
  receipt: RunReceipt;
}
/**
 * 确认开始（``POST /api/v1/run-intents/{id}/submit``）：``confirm_with`` 是用户看到的那份预检查的身份；``accept_scope`` 表示“我知道
 * 这次范围、限制和费用保护，使用一次机会开始”。同一意图重复确认只对应一个运行，即使换了幂等键；同一个键配不同内容返回冲突。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "RunSubmitRequest".
 */
export interface RunSubmitRequest {
  accept_scope: true;
  confirm_with: string;
  idempotency_key: string;
}
/**
 * 统一的安全错误：机器代码、HTTP 状态与“发生了什么—影响是什么—下一步怎么做”的中文，加一个支持编号。不含用户输入、内部路径、
 * 堆栈、原始校验异常或秘密。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "ServiceError".
 */
export interface ServiceError {
  code:
    | "unauthenticated"
    | "not_found"
    | "revision_conflict"
    | "preflight_stale"
    | "quota_exhausted"
    | "idempotency_conflict"
    | "intent_conflict"
    | "not_resumable"
    | "cancel_not_allowed"
    | "result_unavailable"
    | "deleting"
    | "deleted"
    | "expired"
    | "data_pack_unavailable"
    | "paid_calls_disabled"
    | "run_options_invalid"
    | "run_settings_unavailable"
    | "migration_consent_required"
    | "migration_choice_required"
    | "migration_not_needed"
    | "start_consent_stale"
    | "start_acknowledgement_missing"
    | "start_already_authorized"
    | "rate_limited"
    | "csrf_failed"
    | "origin_rejected"
    | "payload_too_large"
    | "invalid_request"
    | "internal_error";
  display?: DisplayAttachment | null;
  field?: ("clones_per_city" | "years" | "max_cost_cny" | "cities") | null;
  impact_zh: string;
  message_zh: string;
  meta?: ProductMeta | null;
  next_action_zh: string;
  retry_after_seconds?: number | null;
  sample?: SampleLabel | null;
  status: number;
  support_ref?: string | null;
}
/**
 * 用专属链接换会话（``POST /api/v1/session``，同源 JSON）。``invite_token`` 是链接 ``#token=`` 后面的随机秘密：网页从地址的
 * 片段里读出、交换后立即从地址栏移除；它不进查询串、路径、localStorage 或任何模型请求，服务端只存它的摘要。
 *
 * This interface was referenced by `ProductContracts`'s JSON-Schema
 * via the `definition` "SessionExchangeRequest".
 */
export interface SessionExchangeRequest {
  invite_token: string;
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
