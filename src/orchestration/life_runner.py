"""E1-7A 多分身年度循环（`DECISIONS/0030`）。

    运行前：加载并检查运行配置、原计划名单（冻结；标准开局 v1、每个运行臂的跨城配置）、城市事实与评分条件的对应、信息许可与
            规划器身份 → 支持范围说明 → 运行清单（全部内容身份）。比较组不合法就拒绝启动，不偷偷删城市。
    每个分身（按名单顺序）、每一年：
        安全请求（观察 + 城市事实 + 记忆 + 公开规则 [+ 上一份候选的安全诊断]）→ 规划器 → 解析
        → 执行前检查（econ.life_settlement.check_year_plan：没有新随机事件的试算，什么都不提交）
            通过           → run_life_year 真实执行（结算 → 评分 → 反馈 → 交接）→ 下一年
            可修改的问题   → 安全诊断 → 再问一次（每年最多 1 + 修订上限 份候选）；用完就停在规划阶段
            城市资料问题   → 这个分身停在规划阶段，标明影响范围；其他分身照常
            内部配置或程序错误 → 显式暂停整个批次；没轮到的分身保留为未运行
    全部之后：E1-6A 单分身评价（部分轨迹带规划停止）→ E1-6B 城市汇总（完整者均值、出局、模拟缺失范围）。

能不能修订按**检查阶段与问题代码**判断，不按一个状态字符串：同是 ``data_error``，计划选了资料未知的治疗可以改，城市资料损坏不能。
真实执行之后因随机事件出现的停止、出局都是已经发生的生活——不回到年初重问、不重抽、不改写已提交的月份。

编排器持有内部对象；交给规划器的只有 ``agentlayer.life_planning`` 的安全投影。L、V、J、城市平均与缺失范围都由 econ 里已有的
``evaluate_clone`` / ``aggregate_arm`` 计算，这里不重写。

E1-7B（``DECISIONS/0031``）：同一个运行器接真实模型规划器（``agentlayer.life_llm.QwenPlanner``）。发送之前，请求里的税费说明
必须等于执行用的税务绑定的投影，最终请求体再查一遍（研究真值、评分参数、未来事件、凭证、写给开发者的说明）；规划器的
``PlannerHalt``（服务失败、预算、输入超限、发送状态不确定等）让整个批次停在 ``halted``——不是生活结果、不消耗计划修订、
不替分身生成计划，可以在所有者决定之后恢复。第一次调用之前把全部输入文件冻结进归档（``inputs/``），重放与恢复可以只用归档。

E1-7B 收尾（执行修订 ``e1-7b-r2``）：服务请求明细与规划记录由同一份检查 ``agentlayer.life_llm.service_history`` 核对（新回复、缓存、
恢复与重放共用），包括服务回复已落盘、规划记录还没写的那一份；规划器实际持有的预算与声明逐项核对；恢复之前核对本运行的服务
明细与共享账本的历史。任何一项对不上，都在追加记录、执行生活或调用规划器之前拒绝。
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlayer.life_llm import (
    ProviderCallLog,
    ServiceRecordMismatchError,
    expected_provider_body,
    final_request_problems,
    service_history,
)
from agentlayer.life_planning import (
    MemoryYear,
    Planner,
    PlannerCall,
    PlannerHalt,
    PlannerReply,
    action_rules,
    build_memory,
    build_request,
    incomplete_output_diagnostics,
    parse_output,
    payload_fingerprint,
    plan_diagnostics,
    prompt_template_fingerprint,
    render_prompt,
    request_payload,
    revision_block,
    visible_tax_rules,
)
from agentlayer.life_views import ObservationPermission, visible_feedback
from common.life_aggregation_contracts import (
    MISSING_REASON_TEXT,
    PLANNING_STOP_REASON,
    ArmReport,
    FrozenRoster,
    RosterConfig,
    RunFailure,
)
from common.life_city_facts import CityFactsConfig
from common.life_contracts import (
    LifeContractError,
    PersonYearState,
    PlanCheck,
    SyntheticTaxPolicy,
    VisibleFeedback,
    YearAction,
    canonical_fingerprint,
    model_fingerprint,
)
from common.life_evaluation_contracts import (
    PLANNING_STOP_TEXT,
    CloneEvaluation,
    EvaluationSpec,
    PlanningStop,
)
from common.life_feedback_contracts import LifeYearResult
from common.life_planning_contracts import (
    CLONE_RUN_STATUS_TEXT,
    EXECUTION_REVISION,
    MEMORY_POLICY_VERSION,
    PLANNING_VERSION,
    AcceptedYear,
    AttemptRecord,
    CloneRunRecord,
    LifeRunConfig,
    PlannerIdentity,
    PlannerSpec,
    RunManifest,
    RunProgress,
    VisibleDiagnostic,
)
from common.life_product_profile import ProductLifeProfile
from common.life_scoring_contracts import ScoringPreference
from common.runlog import new_run_id, utc_now_iso
from econ.life_aggregation import aggregate_arm, freeze_roster
from econ.life_coverage import coverage_report
from econ.life_evaluation import evaluate_clone
from econ.life_scoring import preference_from_confirmed_profile, preference_from_stated_profile
from econ.life_settlement import LifeTaxBinding, binding_from_policy, check_year_plan
from econ.life_year import ScoringInputs, run_life_year, tax_binding_fingerprint
from ledger.life_catalog import LifeBundle
from ledger.life_city_facts import alignment_report, load_city_facts
from ledger.life_roster import load_roster
from ledger.life_run_config import MODELS_CONFIG, load_run_config, run_config_problems
from ledger.life_scoring_config import ScoringConfig
from orchestration.life_archive import RunArchive
from orchestration.life_budget import (
    budget_identity_problems,
    ledger_history_problems,
    read_ledger,
)

#: 运行清单里记指纹的关键代码（规则在代码里）：改了它们，就不能冒充原运行继续。
_SRC = Path(__file__).resolve().parent.parent
KEY_SOURCES: tuple[str, ...] = (
    "econ/life_settlement.py",
    "econ/life_resources.py",
    "econ/life_health.py",
    "econ/life_career.py",
    "econ/life_savings.py",
    "econ/life_scoring.py",
    "econ/life_feedback.py",
    "econ/life_year.py",
    "econ/life_evaluation.py",
    "econ/life_terminal_value.py",
    "econ/life_aggregation.py",
    "agentlayer/life_views.py",
    "agentlayer/life_planning.py",
    "agentlayer/life_mock.py",
    "agentlayer/life_llm.py",
    "orchestration/life_runner.py",
    "orchestration/life_budget.py",
    "orchestration/life_archive.py",
)

#: E3-A 收尾 F1（DECISIONS/0034 第九节）：六维产品画像的运行另外记下产品规则代码的身份。只加在产品运行上（名单里有六维产品
#: 画像）；旧协议、旧输入的运行清单一个字段都不多，历史归档照旧核对。
#: 绑定边界：这里只列**运行时**把画像变成评分输入的代码——``common/life_product_profile.py`` 的 ``dimension_fractions`` /
#: ``material_fractions`` 与精确点数的规范化（偏好转换本身在 ``econ/life_scoring.py``，已在 ``KEY_SOURCES``；观察、提示与 mock
#: 也已在里面）。产品层的答案审阅、编译与资料编译（``src/product/``）只产生冻结进归档的输入文件，它们一变，冻结输入、协议、目录或
#: 名单的身份就变，不需要另记源码；结果投影在生活之后，不影响运行语义。
PRODUCT_SOURCE_SET = "e3a-product-sources-r1"
PRODUCT_KEY_SOURCES: tuple[str, ...] = ("common/life_product_profile.py",)
#: 执行前检查在 plan 阶段发现的问题里，身份与年份由运行器填写——出错是运行器的程序错误，不能交给规划器修订。
RUNNER_OWNED_CODES: frozenset[str] = frozenset({"identity", "year_mismatch", "year_beyond_horizon"})
#: 没有新随机事件的试算里，规划器能改的资料问题：要治疗而本城治疗资料未知（改成不治即可；资料本身仍是缺口）。
REVISABLE_TRIAL_DATA_CODES: frozenset[str] = frozenset({"treatment_data_unknown"})


class RunRefusedError(LifeContractError):
    """运行前检查（或恢复、重放前的身份核对）不通过：拒绝启动，列出全部原因。"""


class RunInterrupted(Exception):  # noqa: N818 - 这是一个控制信号，不是错误
    """在两次规划调用之间停下（测试与演示用的中断信号；真实中断——进程被杀——也只会停在这样的边界上）。"""


class _ProgramError(Exception):
    def __init__(self, stage: str, kind: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.kind, self.message = stage, kind, message


class _BudgetExhausted(Exception):  # noqa: N818
    pass


# ---------------------------------------------------------------------- 运行前


@dataclass(frozen=True)
class RunWorld:
    """开跑前加载并检查过的全部内部对象。编排器持有它们；交给规划器的只有安全投影。"""

    config: LifeRunConfig
    config_path: Path
    roster_config: RosterConfig
    bundle: LifeBundle
    scoring: ScoringConfig
    specs: dict[str, EvaluationSpec]
    roster: FrozenRoster
    facts: CityFactsConfig
    permission: ObservationPermission
    identities: dict[str, str]
    scope: dict[str, Any]
    alignment: dict[str, Any]
    #: 运行读到的全部输入文件：用途 → (相对路径, 内容 sha256)。身份只记用途与内容（路径写法不参与）；冻结快照按路径复制。
    input_files: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(e.clone_id for e in self.roster.entries)


def _source_fingerprint(rel: str) -> str:
    return hashlib.sha256((_SRC / rel).read_bytes()).hexdigest()


def _tax_policy(bundle: LifeBundle, city: str) -> SyntheticTaxPolicy:
    catalog = bundle.catalog_for(city)
    assert catalog is not None
    policy = bundle.tax_policy(catalog.tax_policy_id)
    assert policy is not None
    return policy


def _tax(bundle: LifeBundle, city: str) -> LifeTaxBinding:
    return binding_from_policy(_tax_policy(bundle, city))


def _input_files(
    config_path: Path, config: LifeRunConfig, roster_config: RosterConfig
) -> dict[str, tuple[str, str]]:
    """运行读到的全部输入文件（E1-7B 第六节）：用途 → (路径, 内容 sha256)。"""
    roles: list[tuple[str, Path]] = [
        ("run_config", Path(config_path)),
        ("roster", Path(config.roster)),
        ("life_input", Path(roster_config.life_input)),
        ("scoring_config", Path(roster_config.scoring_config)),
        ("city_facts", Path(config.city_facts)),
    ]
    roles += [(f"evaluation:{a.arm_id}", Path(a.evaluation_config)) for a in roster_config.arms]
    if config.planner.identity.kind == "llm":
        roles.append(("models", MODELS_CONFIG))
    return {role: (p.as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for role, p in roles}


def _forbidden_texts(
    world_bundle: LifeBundle, scoring: ScoringConfig, specs: dict[str, Any]
) -> tuple[str, ...]:
    """发送前在最终请求体里查找的内部文字（E1-7B 第 4.5 节）：评分与评价规格的编号、说明与内容身份，疾病事件来源的设置与
    指定事件的说明。只是兜底：主要的保证是请求只由安全投影产生。太短的文字不查（避免与正常文字偶然相同）。"""
    out: list[str] = [scoring.spec.spec_id, scoring.spec.content_fingerprint]
    if scoring.spec.note:
        out.append(scoring.spec.note)
    for spec in specs.values():
        out += [spec.spec_id, spec.content_fingerprint]
    src = world_bundle.protocol.illness_events
    if src is not None:
        out.append(src.note)
        if src.pairing_id:
            out.append(src.pairing_id)
        out += [e.note for e in src.injected]
    return tuple(sorted({s for s in out if isinstance(s, str) and len(s) >= 8}))


def _identities(
    config: LifeRunConfig,
    bundle: LifeBundle,
    scoring: ScoringConfig,
    specs: dict[str, EvaluationSpec],
    roster: FrozenRoster,
    facts: CityFactsConfig,
    permission: ObservationPermission,
    input_files: dict[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    protocol = bundle.protocol
    ids = {
        "planning_version": PLANNING_VERSION,
        "run_config": canonical_fingerprint(config.model_dump(mode="json")),
        "roster": roster.content_fingerprint,
        "protocol": model_fingerprint(protocol),
        "scoring_config": model_fingerprint(scoring),
        "scoring_content": scoring.spec.content_fingerprint,
        "city_facts_visible": facts.visible_fingerprint,
        "city_facts_links": facts.links_fingerprint,
        "permission": model_fingerprint(permission),
        "prompt_template": prompt_template_fingerprint(config.prompt_template),
        "memory_policy": MEMORY_POLICY_VERSION,
        "planner": canonical_fingerprint(config.planner.model_dump(mode="json")),
        # 影响规划行为的内容（身份去掉说明 + 受控行为的分身、行为与年份）；实际规划器必须报出同一份（E1-7A 收尾 F2）
        "planner_behavior": config.planner.behavior_fingerprint,
        "execution_revision": EXECUTION_REVISION,
        "illness_events": "none"
        if protocol.illness_events is None
        else model_fingerprint(protocol.illness_events),
    }
    for c in bundle.catalogs:
        ids[f"catalog:{c.city}"] = model_fingerprint(c)
        ids[f"tax:{c.city}"] = tax_binding_fingerprint(_tax(bundle, str(c.city)))
    for arm_id, spec in specs.items():
        ids[f"evaluation:{arm_id}"] = spec.content_fingerprint
    for rel in KEY_SOURCES:
        ids[f"source:{rel}"] = _source_fingerprint(rel)
    if bundle.product_profiles:
        ids["source_set"] = PRODUCT_SOURCE_SET
        for rel in PRODUCT_KEY_SOURCES:
            ids[f"source:{rel}"] = _source_fingerprint(rel)
    if input_files:
        ids["input_files"] = canonical_fingerprint({k: v[1] for k, v in input_files.items()})
    return ids


def _planner_scope(config: LifeRunConfig) -> str:
    ident, llm = config.planner.identity, config.planner.llm
    if llm is None:
        return (
            f"规划器：{ident.planner_id}（mock：可控的确定性模拟代理）。它只检验接口、状态传递、错误处理与信息隔离，"
            "不代表真实大语言模型的能力；没有网络调用"
        )
    budget = config.paid_budget
    if llm.provider == "claude_agent_sdk":
        decoding = (
            f"Claude Agent SDK 单轮调用（无工具、不读设置、隔离环境）；不设温度，思考由命令行固定为自适应，effort {llm.effort}，"
            "只靠提示要求一个 JSON 对象"
        )
    else:
        decoding = f"温度 {llm.temperature}，思考关闭，{llm.response_format} 输出"
    if budget is None:
        billing = (
            f"本运行声明不记账（{config.unmetered_reason}）：费用记为未计量，只受全局调用上限约束"
        )
    else:
        unit = "元" if budget.currency == "CNY" else "美元"
        caps = [
            *([f"{budget.max_requests} 次"] if budget.max_requests is not None else []),
            *([f"{budget.max_cost} {unit}"] if budget.max_cost is not None else []),
        ]
        billing = (
            f"每次服务请求都按已记录报价（{budget.price.price_version}）计入本轮付费预算（"
            + ("、".join(caps) if caps else "照常记账，不设次数与费用上限")
            + "，应用侧控制，以供应商账单为准）"
        )
    return (
        f"规划器：{ident.planner_id}（真实模型 {llm.model}，{llm.provider}；{decoding}，"
        f"输出上限 {llm.max_output_tokens} token，超时 {llm.timeout_seconds} 秒，SDK 自动重试关闭，"
        f"每份候选最多再试 {llm.service_retries} 次服务请求）。结果只说明这个模型在这一次合成试跑里的表现，不代表模型的一般"
        f"能力；{billing}"
    )


def _scope(
    config: LifeRunConfig,
    bundle: LifeBundle,
    scoring: ScoringConfig,
    roster: FrozenRoster,
    facts: CityFactsConfig,
) -> dict[str, Any]:
    """运行前的支持范围说明（给读报告的人，不给分身）。"""
    p = bundle.protocol
    policy = config.planning
    cities = [f"{c.label}（{c.city}）" for c in bundle.catalogs]
    sections: dict[str, Any] = {
        "运行方式": [
            f"固定城市：{'、'.join(cities)}；分身不搬家、不交流",
            f"每个分身每年一次正常规划，最多再修订 {policy.max_revisions_per_year} 次（每年最多 {policy.attempts_per_year} 份候选）；"
            "环境按月执行；模拟 "
            f"{p.simulation_years} 个行动年",
            f"全局规划调用上限 {policy.global_call_limit} 次；运行顺序 = 原计划名单的顺序（顺序调度，不并发）",
            _planner_scope(config),
        ],
        "数据与参数": [
            f"生活协议 {p.protocol_id}（状态 {p.status}）、评分 {scoring.spec.spec_id}（探索版）：城市、报价、工资、疾病、治疗与"
            "评分参数全部是合成设定，不是现实城市，不排序真实城市",
            f"城市背景事实 {facts.version}：每座城 {len(facts.sheets[0].facts)} 条合成指数，换算到评分条件的规则是内部合成设定",
            "评价沿用 E1-6：完整者均值只用完整评价的分身；生活出局另报比例与事件链；模拟问题进入缺失范围（不是置信区间）",
        ],
        "支持的选择与组合限制": list(action_rules(p, policy.attempts_per_year)),
    }
    coverage: list[str] = []
    group = scoring.comparison_group
    for u in roster.users:
        profile = bundle.profile_for(u.person_id)
        pref = (
            preference_from_confirmed_profile(profile)
            if type(profile) is ProductLifeProfile
            else preference_from_stated_profile(profile)  # type: ignore[arg-type]
        )
        if group is None or not isinstance(pref, ScoringPreference):
            coverage.append(f"{u.label}：偏好信息不足或没有比较组，不能做覆盖报告")
            continue
        rep = coverage_report(
            spec=scoring.spec,
            group=group,
            conditions=scoring.city_conditions,
            catalogs=bundle.catalogs,
            preference=pref,
        )
        weak = [
            f"{leaf.leaf}（{leaf.support}）"
            for leaf in rep.leaves
            if leaf.weight > 0 and leaf.support != "supported"
        ]
        coverage.append(
            f"{u.label}：全部候选城市都支持的偏好权重 C = {rep.common_weight:.3f}；资料未知的权重 {rep.unknown_weight:.3f}，"
            f"没有评分分项的权重 {rep.no_component_weight:.3f}；"
            + ("没有正权重的未支持叶子" if not weak else "正权重但未完整支持：" + "、".join(weak))
        )
    for c in bundle.catalogs:
        tr = "、".join(f"{t.label}={t.availability}" for t in c.treatments) or "没有治疗目录"
        coverage.append(f"{c.label} 的治疗目录：{tr}（资料未知不是免费，也不是没有）")
    sections["用户偏好与城市资料覆盖"] = coverage
    sections["可能出现的结局"] = [
        "正常完成：交给 E1-6 评价；确认的生活出局（付不起最低住房与食品）：保留事件链，不进完整者均值",
        "E1-5C 停止（疾病之后应急安排不足或资料缺口）、精力反馈受阻：保留已有轨迹，作为模拟缺失进入范围",
        "规划没有完成（修订耗尽、城市资料问题）：保留已提交的年份，作为模拟缺失进入范围；不补分、不换人",
        "全局调用上限用尽、共享配置或未知程序错误：没轮到的分身保留为未运行；程序错误显式暂停批次，不假定只影响一个分身",
    ]
    return sections


def preflight(run_config_path: Path) -> RunWorld:
    """开跑前的全部检查。任何一条不通过就拒绝启动（``RunRefusedError``），不偷偷删掉城市或分身再声称执行了原比较。"""
    config = load_run_config(run_config_path)
    try:
        roster_config, bundle, scoring, specs = load_roster(Path(config.roster))
    except LifeContractError as exc:
        raise RunRefusedError(f"原计划名单或它引用的配置不合法，拒绝启动：{exc}") from exc
    try:
        facts = load_city_facts(Path(config.city_facts), bundle, scoring)
    except LifeContractError as exc:
        facts = None
        facts_problem: str | None = f"城市事实表与评分条件对不上：{exc}"
    else:
        facts_problem = None
    problems = run_config_problems(config, roster_config, bundle, facts)
    if facts_problem:
        problems.append(facts_problem)
    try:
        roster = freeze_roster(roster_config, bundle, scoring, specs)
    except LifeContractError as exc:
        problems.append(f"名单冻结失败：{exc}")
        roster = None
    info = config.information
    try:
        permission = ObservationPermission(**info.model_dump())
    except (ValueError, TypeError) as exc:
        problems.append(f"信息许可不合法：{exc}")
        permission = None
    if problems or roster is None or facts is None or permission is None:
        raise RunRefusedError("运行前检查没有通过，拒绝启动：\n  " + "\n  ".join(problems))
    input_files = _input_files(Path(run_config_path), config, roster_config)
    identities = _identities(config, bundle, scoring, specs, roster, facts, permission, input_files)
    return RunWorld(
        config=config,
        config_path=Path(run_config_path),
        roster_config=roster_config,
        bundle=bundle,
        scoring=scoring,
        specs=dict(specs),
        roster=roster,
        facts=facts,
        permission=permission,
        identities=identities,
        scope=_scope(config, bundle, scoring, roster, facts),
        alignment=alignment_report(facts, bundle, scoring),
        input_files=input_files,
    )


#: 归档里冻结的输入文件（E1-7B 第六节）：第一次规划调用之前写好，之后不改。
SNAPSHOT_DIR = "inputs"


def snapshot_inputs(world: RunWorld, archive: RunArchive) -> dict[str, Any]:
    """把运行读到的全部输入文件按原相对路径复制进 ``inputs/``，复制后再核对内容（与开跑前算的 sha256 相同）。

    真实模型运行的路径必须是仓库内的相对路径，否则拒绝（快照必须能在归档里单独重建同一个运行）。mock 运行（例如测试在临时
    目录里写的变体配置）路径不是相对路径时只记下内容身份、不复制，并标明不能只用归档重建。"""
    index: dict[str, Any] = {
        "note": "第一次规划调用之前冻结的输入；重放与恢复可以只用这里",
        "replayable_from_snapshot": True,
        "files": {},
    }
    root = archive.path / SNAPSHOT_DIR
    outside = [
        rel
        for rel, _sha in world.input_files.values()
        if Path(rel).is_absolute() or ".." in Path(rel).parts
    ]
    if outside:
        if world.config.planner.identity.kind == "llm":
            raise RunRefusedError(
                f"输入文件 {'、'.join(outside[:3])} 不是仓库内的相对路径：真实模型运行必须能只用归档重建，不能冻结就不开跑"
            )
        index["replayable_from_snapshot"] = False
        index["note"] = "mock 运行的输入不全在仓库内：只记内容身份、不复制，不能只用归档重建"
        index["files"] = {
            role: {"path": rel, "sha256": sha}
            for role, (rel, sha) in sorted(world.input_files.items())
        }
        archive.write_json(f"{SNAPSHOT_DIR}/index.json", index)
        return index
    for role, (rel, sha) in sorted(world.input_files.items()):
        p = Path(rel)
        dst = root / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        if hashlib.sha256(dst.read_bytes()).hexdigest() != sha:
            raise RunRefusedError(f"输入文件 {rel} 在开跑前检查之后变了：不冻结、不开跑")
        index["files"][role] = {"path": rel, "sha256": sha}
    archive.write_json(f"{SNAPSHOT_DIR}/index.json", index)
    return index


def preflight_from_snapshot(run_dir: Path) -> RunWorld:
    """只用归档里冻结的输入重建运行世界（恢复与零调用重放用）。仓库里的配置后来改了也不影响；关键代码仍按运行清单核对。"""
    root = (Path(run_dir) / SNAPSHOT_DIR).resolve()
    index = RunArchive(Path(run_dir), create=False).read_json(f"{SNAPSHOT_DIR}/index.json")
    if not index.get("replayable_from_snapshot"):
        raise RunRefusedError("这次运行的输入没有完整冻结进归档：不能只用归档重建")
    files = index["files"]
    for role, item in files.items():
        if hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise RunRefusedError(
                f"归档里冻结的输入 {item['path']}（{role}）被改过：不能冒充原运行"
            )
    with contextlib.chdir(root):
        return preflight(Path(files["run_config"]["path"]))


# ---------------------------------------------------------------------- 实际规划器与声明（E1-7A 收尾 F2）


def _script_view(spec: PlannerSpec) -> dict[tuple[str, str], tuple[int, ...]]:
    return {(s.clone_id, s.behavior): tuple(sorted(set(s.years))) for s in spec.scripts}


def _script_text(key: tuple[str, str], years: tuple[int, ...]) -> str:
    return f"{key[0]} {key[1]}（第 {'、'.join(str(y) for y in years)} 年）"


def planner_setting_problems(declared: PlannerSpec, planner: object) -> list[str]:
    """实际交给运行器的规划器与运行配置的声明是否一致（E1-7A 收尾 F2）。

    比较身份（逐字段）与规划器 ``effective_spec()`` 报出的行为内容（``PlannerSpec.behavior_fingerprint``：身份去掉说明，加上每段受控
    行为的分身、行为与年份）。同名同版本不等于同一设定：漏传、替换或多出受控行为都不通过。不报设定的对象不能作为正式运行的
    规划器。这是工程一致性检查，不是防恶意伪造。"""
    problems: list[str] = []
    identity = getattr(planner, "identity", None)
    if identity != declared.identity:
        got = (
            f"{identity.planner_id}/{identity.version}"
            if isinstance(identity, PlannerIdentity)
            else "（没有身份）"
        )
        problems.append(
            f"规划器身份 {got} 与运行配置声明的 {declared.identity.planner_id}/{declared.identity.version} 不同："
            "不暗中换规划器"
        )
    effective = getattr(planner, "effective_spec", None)
    if not callable(effective):
        problems.append(
            "规划器没有报出实际生效的设定（effective_spec）：无法与运行配置的声明核对，不能作为正式运行的规划器"
        )
        return problems
    try:
        spec = effective()
    except ValueError as exc:
        problems.append(f"规划器报出的设定不合法：{exc}"[:400])
        return problems
    if type(spec) is not PlannerSpec:
        problems.append(f"effective_spec() 必须返回 PlannerSpec，得到 {type(spec).__name__}")
        return problems
    if spec.behavior_fingerprint == declared.behavior_fingerprint:
        return problems
    want, got_scripts = _script_view(declared), _script_view(spec)
    parts: list[str] = []
    missing = [k for k in want if k not in got_scripts]
    extra = [k for k in got_scripts if k not in want]
    changed = [k for k in want if k in got_scripts and want[k] != got_scripts[k]]
    if missing:
        parts.append("声明了但实际没有：" + "；".join(_script_text(k, want[k]) for k in missing))
    if extra:
        parts.append("实际多出：" + "；".join(_script_text(k, got_scripts[k]) for k in extra))
    if changed:
        parts.append(
            "年份不同："
            + "；".join(
                f"{k[0]} {k[1]} 声明第 {'、'.join(map(str, want[k]))} 年、实际第 {'、'.join(map(str, got_scripts[k]))} 年"
                for k in changed
            )
        )
    if spec.identity.model_dump(mode="json", exclude={"note"}) != declared.identity.model_dump(
        mode="json", exclude={"note"}
    ):
        parts.append("身份字段不同")
    problems.append(
        "实际生效的设定与运行配置的声明不同（"
        + "；".join(parts or ["行为内容不同"])
        + "）：同名同版本不等于同一设定"
    )
    return problems


def _check_planner(world: RunWorld, planner: object) -> None:
    problems = planner_setting_problems(world.config.planner, planner)
    # E1-7B 收尾 F2：实际持有的预算必须就是声明的那一本（额度、报价、账本位置与头文件）
    problems += budget_identity_problems(world.config.paid_budget, planner)
    if problems:
        raise RunRefusedError(
            "规划器与运行配置的声明不一致，拒绝运行（在第一次规划调用与任何生活执行之前）：\n  "
            + "\n  ".join(problems)
        )


# ---------------------------------------------------------------------- 检查结果怎样处置


def classify_check(check: PlanCheck) -> str:
    """执行前检查 → ``accept`` / ``revise``（规划器可以改）/ ``city_data``（城市资料问题）/ ``internal``（运行器或配置的错误）。

    按**阶段与问题代码**判断：``plan`` 阶段的问题都关于计划本身，可以修订（身份与年份由运行器填写，出错算程序错误）；无事件试算里
    资源超限、所选住房或食品给不了是计划问题，要治疗而治疗资料未知可以改成不治，其他资料错误是城市资料问题；参数、状态与城市资料
    阶段的问题不交给规划器。"""
    if check.passed:
        return "accept"
    codes = {p.code for p in check.problems}
    if check.stage == "plan":
        return "internal" if codes & RUNNER_OWNED_CODES else "revise"
    if check.stage == "no_event_trial":
        if check.status == "plan_infeasible":
            return "revise"
        if check.status == "data_error":
            return "revise" if codes <= REVISABLE_TRIAL_DATA_CODES else "city_data"
        return "internal"
    if check.stage == "city_data":
        return "city_data"
    return "internal"


# ---------------------------------------------------------------------- 运行


@dataclass
class _Clone:
    clone_id: str
    user_id: str
    person_id: str
    city: str
    repeat_index: int
    state: PersonYearState
    results: list[LifeYearResult] = field(default_factory=list)
    accepted: list[AcceptedYear] = field(default_factory=list)
    attempts: list[int] = field(default_factory=list)
    memory: list[MemoryYear] = field(default_factory=list)
    last_feedback: VisibleFeedback | None = None


@dataclass(frozen=True)
class _YearPlan:
    action: YearAction | None = None
    need_summary: str | None = None
    attempt: int = 0
    request_id: str | None = None
    stop: PlanningStop | None = None


@dataclass(frozen=True)
class RunOutcome:
    """一次运行（或恢复后的运行）的结果。中断时没有评价与汇总。"""

    run_id: str
    status: str
    calls_used: int
    records: tuple[CloneRunRecord, ...]
    lives: dict[str, tuple[LifeYearResult, ...]]
    evaluations: dict[str, tuple[CloneEvaluation, ...]]
    failures: dict[str, tuple[RunFailure, ...]]
    reports: dict[str, ArmReport]
    archive_path: Path
    pause_reason: str | None = None
    halt_kind: str | None = None


class LifeRunner:
    """顺序调度一个批次。一次运行一个实例。

    恢复（``resume_run``）时先装好核对过的归档记录，再从第一个分身起按同一套代码重走已经记录的路径：已记录的调用直接复用原始
    响应，解析、执行前检查、处置与执行都重新做一遍，得到的检查记录、已接受的行动与分身结局逐项和归档比较（E1-7A 收尾 F1）。
    重放阶段不写任何文件、不调用规划器；第一次需要写新记录或调用规划器之前，所有已有记录都必须已经重放到（``_leave_replay``）。"""

    def __init__(
        self,
        world: RunWorld,
        planner: Planner,
        archive: RunArchive,
        run_id: str,
        *,
        stop_after_calls: int | None = None,
        stop_after_commits: int | None = None,
        records: ArchiveRecords | None = None,
        expected: Sequence[CloneRunRecord] = (),
    ) -> None:
        _check_planner(world, planner)
        self.world, self.planner, self.archive, self.run_id = world, planner, archive, run_id
        self.stop_after_calls = stop_after_calls
        self.stop_after_commits = stop_after_commits
        self.commits_made = 0
        self.calls_used = 0
        self.status = "running"
        self.pause_reason: str | None = None
        self.halt_kind: str | None = None
        self.forbidden = _forbidden_texts(world.bundle, world.scoring, world.specs)
        self.records: dict[str, CloneRunRecord] = {}
        self.lives: dict[str, tuple[LifeYearResult, ...]] = {}
        self.stops: dict[str, PlanningStop] = {}
        self.failures: dict[str, RunFailure] = {}
        # 恢复用：核对过的调用、检查记录、已接受的年度行动与进度里已经结束的分身结局（逐项比较，不覆盖）
        self.recorded: dict[tuple[str, int, int], dict[str, Any]] = (
            dict(records.calls) if records else {}
        )
        self.recorded_attempts: dict[tuple[str, int, int], dict[str, Any]] = (
            dict(records.attempts) if records else {}
        )
        self.accepted_archive: dict[tuple[str, int], AcceptedYear] = (
            dict(records.accepted) if records else {}
        )
        self.expected_records: dict[str, CloneRunRecord] = {r.clone_id: r for r in expected}
        self._used_calls: set[tuple[str, int, int]] = set()
        self._used_attempts: set[tuple[str, int, int]] = set()
        self._used_accepted: set[tuple[str, int]] = set()
        self._verified_records: set[str] = set()
        self._replaying = bool(
            self.recorded
            or self.recorded_attempts
            or self.accepted_archive
            or self.expected_records
        )
        self._pending: tuple[str, int, int] | None = None

    # ------------------------------------------------------------------ 小工具

    def _profile(self, person_id: str) -> Any:
        # 旧七维陈述或 E3-A 六维产品画像（名单冻结时已核对类型）
        profile = self.world.bundle.profile_for(person_id)
        assert profile is not None
        return profile

    def _inputs(self, c: _Clone) -> ScoringInputs:
        profile = self._profile(c.person_id)
        conditions = self.world.scoring.conditions_for(c.city)
        assert conditions is not None
        return ScoringInputs(
            spec=self.world.scoring.spec,
            conditions=conditions,
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )

    def _leave_replay(self, what: str) -> None:
        """第一次写新记录或调用规划器之前：已有的调用、检查记录、已接受的行动与分身结局必须都已经按确定的路径重放到。剩下的记录
        不在这次运行的路径上（多出、错位或属于别的分支），拒绝，不能冒充原运行继续。"""
        if not self._replaying:
            return
        left = {
            "调用": [_where(k) for k in self.recorded if k not in self._used_calls],
            "检查记录": [_where(k) for k in self.recorded_attempts if k not in self._used_attempts],
            "已接受的行动": [
                f"{k[0]} 第 {k[1]} 年"
                for k in self.accepted_archive
                if k not in self._used_accepted
            ],
            "分身结局": [k for k in self.expected_records if k not in self._verified_records],
        }
        if any(left.values()):
            shown = "；".join(f"{name} {'、'.join(v[:4])}" for name, v in left.items() if v)
            raise RunRefusedError(
                f"恢复时在{what}之前，归档里还有没有重放到的记录（{shown}）：它们不在这次运行的路径上，"
                "不能冒充原运行继续"
            )
        self._replaying = False

    def _write_progress(self) -> None:
        if self._replaying:  # 重放已有记录时不写进度：拒绝时原归档保持不变
            return
        pending_clone, pending_year, pending_attempts = self._pending or (None, None, 0)
        progress = RunProgress(
            run_id=self.run_id,
            status=self.status,  # type: ignore[arg-type]
            calls_used=self.calls_used,
            call_limit=self.world.config.planning.global_call_limit,
            order=self.world.order,
            clones=tuple(self.records[c] for c in self.world.order if c in self.records),
            pending_clone=pending_clone,
            pending_year=pending_year,
            pending_attempts=pending_attempts,
            pause_reason=self.pause_reason,
            halt_kind=self.halt_kind,  # type: ignore[arg-type]
        )
        self.archive.write_json("progress.json", progress.model_dump(mode="json"))

    def _record(
        self,
        c: _Clone,
        status: str,
        detail: str,
        *,
        stop: PlanningStop | None = None,
        failure: RunFailure | None = None,
        keep_lives: bool = True,
    ) -> None:
        record = CloneRunRecord(
            clone_id=c.clone_id,
            user_id=c.user_id,
            city=self.world.bundle.catalog_for(c.city).city,  # type: ignore[union-attr]
            repeat_index=c.repeat_index,
            status=status,  # type: ignore[arg-type]
            years_committed=len(c.results),
            attempts_by_year=tuple(c.attempts),
            planning_stop=stop,
            failure=failure,
            detail=detail[:600],
        )
        expected = self.expected_records.get(c.clone_id)
        if expected is not None:  # 恢复：进度里已经结束的分身，重放得到的结局必须相同
            old, new = expected.model_dump(mode="json"), record.model_dump(mode="json")
            if old != new:
                diff = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
                raise RunRefusedError(
                    f"恢复时重放 {c.clone_id} 得到的结局与进度记录不同（字段：{'、'.join(diff)}）："
                    "不能冒充原运行继续"
                )
            self._verified_records.add(c.clone_id)
        self.records[c.clone_id] = record
        if c.results and keep_lives:
            self.lives[c.clone_id] = tuple(c.results)
        if stop is not None and c.results and keep_lives:
            self.stops[c.clone_id] = stop
        if failure is not None:
            self.failures[c.clone_id] = failure

    def _attempt(self, record: AttemptRecord) -> None:
        """写一条检查记录；恢复时归档里已有这一条，就与重新得出的记录逐字段比较（不追加、不覆盖）。"""
        key = (record.clone_id, record.year_index, record.attempt)
        new = record.model_dump(mode="json")
        old = self.recorded_attempts.get(key)
        if old is not None:
            if old != new:
                diff = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
                raise RunRefusedError(
                    f"恢复时重放 {_where(key)}：用原始响应重新解析、检查与处置的结果与归档的检查记录不同"
                    f"（字段：{'、'.join(diff)}；归档 {old.get('disposition')}，重新得出 {new.get('disposition')}）："
                    "不能冒充原运行继续"
                )
            self._used_attempts.add(key)
            return
        self._leave_replay(f"写 {_where(key)} 的检查记录")
        self.archive.append("attempts", new)

    # ------------------------------------------------------------------ 主循环

    def run(self) -> RunOutcome:
        bundle = self.world.bundle
        for entry in self.world.roster.entries:
            if entry.clone_id in self.records:
                continue
            state = bundle.initial_state(entry.clone_id)
            assert state is not None
            c = _Clone(
                clone_id=entry.clone_id,
                user_id=entry.user_id,
                person_id=entry.person_id,
                city=str(entry.city),
                repeat_index=entry.repeat_index,
                state=state,
            )
            if self.status in ("paused", "budget_exhausted"):
                reason = (
                    "全局调用上限已经用完，没有轮到"
                    if self.status == "budget_exhausted"
                    else f"批次已暂停（{self.pause_reason}），没有轮到"
                )
                self._record(c, "not_run", reason)
                continue
            try:
                self._run_clone(c)
            except RunInterrupted:
                self.status = "interrupted"
                self._write_progress()
                return self._outcome(evaluate=False)
            except PlannerHalt as exc:
                # 服务失败、预算、输入超限、发送状态不确定、请求检查没有通过：整个批次停下，保存现场。不是生活结果，
                # 不记成这个分身的程序错误，也不替它生成计划；所有者决定之后可以恢复（尝试次数与预算都不清零）。
                pending = self._pending
                self.status = "halted"
                self.halt_kind = exc.kind
                where = (
                    ""
                    if pending is None
                    else f"{pending[0]} 第 {pending[1]} 年第 {pending[2] + 1} 份候选："
                )
                self.pause_reason = f"{where}{exc.message}"[:600]
                self._write_progress()
                return self._outcome(evaluate=False)
            except _BudgetExhausted:
                self.status = "budget_exhausted"
                self._pending = None
            except _ProgramError as exc:
                self._program_error(c, exc.stage, exc.kind, exc.message)
            except RunRefusedError:
                raise
            except (
                LifeContractError
            ) as exc:  # 配置在运行前都检查过：到这里是没想到的契约冲突，同样暂停批次
                self._program_error(c, "life", "contract", f"{type(exc).__name__}: {exc}")
            except Exception as exc:
                self._program_error(c, "life", "unexpected", f"{type(exc).__name__}: {exc}")
            self._pending = None
            self._write_progress()
        self._leave_replay("结束这次运行")
        if self.status == "running":
            self.status = "completed"
        self._write_progress()
        return self._outcome(evaluate=True)

    def _program_error(self, c: _Clone, stage: str, kind: str, message: str) -> None:
        # 重放已有记录时出错：归档与现在的执行对不上，不记成这个分身的程序错误，也不写任何东西
        if self._replaying:
            raise RunRefusedError(
                f"恢复时重放 {c.clone_id} 的已有记录出错（{stage}：{message}）：归档与现在的执行对不上，"
                "不能冒充原运行继续"
            )
        self.status = "paused"
        self.pause_reason = (
            f"{c.clone_id} 遇到{'意外程序错误' if kind == 'unexpected' else '内部配置或契约错误'}（{stage}）：{message}"
        )[:600]
        failure = RunFailure(
            clone_id=c.clone_id,
            stage=stage,  # type: ignore[arg-type]
            error_kind="unexpected" if kind == "unexpected" else "contract",
            message=message[:600],
        )
        # 程序错误：不提取任何已知月份（不假定之前的轨迹仍然可信）；已提交的年份保留在归档里
        self._record(
            c,
            "program_error",
            f"{message}——批次暂停；之前提交的 {len(c.results)} 年保留在归档里，但不进入评价",
            failure=failure,
            keep_lives=False,
        )

    def _guard_city(self, c: _Clone, catalog: Any) -> None:
        """代理看到的与环境执行的必须是同一份城市资料：目录与城市事实都要等于运行清单记下的身份（开跑后被换掉就停下）。"""
        w = self.world
        if model_fingerprint(catalog) != w.identities.get(f"catalog:{c.city}"):
            raise _ProgramError(
                "planning",
                "contract",
                f"{c.city} 的目录与运行清单记下的身份不同：分身看到的资料与执行用的资料必须是同一份，不能沿用旧配置的身份",
            )
        if w.facts.visible_fingerprint != w.identities.get("city_facts_visible"):
            raise _ProgramError(
                "planning", "contract", "城市事实表与运行清单记下的身份不同：不能沿用旧配置的身份"
            )

    def _run_clone(self, c: _Clone) -> None:
        w = self.world
        n = w.bundle.protocol.simulation_years
        catalog = w.bundle.catalog_for(c.city)
        assert catalog is not None
        self._guard_city(c, catalog)
        tax = _tax(w.bundle, c.city)
        inputs = self._inputs(c)
        # 已经记录的年份与新的年份走同一条路径：恢复时 _plan_year 复用原始响应，_commit 与已接受的行动比较
        for year in range(len(c.results) + 1, n + 1):
            try:
                plan = self._plan_year(c, year, catalog, tax)
            except _BudgetExhausted:
                used = c.attempts[-1] if len(c.attempts) == year else 0
                if not c.results and used == 0:  # 一次调用都没轮到：保留为未运行
                    self._record(
                        c,
                        "not_run",
                        f"全局调用上限 {w.config.planning.global_call_limit} 次已经用完，没有轮到",
                    )
                    raise
                stop = PlanningStop(
                    year_index=year,
                    kind="budget_exhausted",
                    attempts=used,
                    detail=f"全局调用上限 {w.config.planning.global_call_limit} 次已经用完，第 {year} 年的计划还没有被接受",
                )
                self._stop_planning(c, stop)
                raise
            if plan.stop is not None:
                self._stop_planning(c, plan.stop)
                return
            assert plan.action is not None and plan.need_summary is not None
            self._commit(
                c,
                plan.action,
                plan.need_summary,
                plan.attempt,
                plan.request_id or "",
                catalog,
                tax,
                inputs,
            )
            if c.results[-1].handoff != "next_year_ready":
                self._finish(c, c.results[-1])
                return
        raise AssertionError(f"{c.clone_id}: 第 {n} 年之后应当是终态")  # pragma: no cover

    def _finish(self, c: _Clone, last: LifeYearResult) -> None:
        h = last.handoff
        status, text = {
            "terminal": ("completed", f"生活完 {len(c.results)} 年，进入终态"),
            "exited": ("life_exit", f"第 {last.year_index} 年付不起最低住房与食品而出局"),
            "stopped": (
                "stopped",
                f"第 {last.year_index} 年 E1-5C 停止（疾病之后应急安排不足或资料缺口）",
            ),
            "feedback_blocked": (
                "feedback_blocked",
                f"第 {last.year_index} 年之后精力反馈受阻，下一年额度无法确定",
            ),
        }.get(h, (None, None))
        if status is None:
            raise _ProgramError(
                "life",
                "unexpected",
                f"{c.clone_id} 第 {last.year_index} 年的交接是 {h}：通过执行前检查的计划不该结算失败",
            )
        self._record(c, status, text)  # type: ignore[arg-type]

    def _stop_planning(self, c: _Clone, stop: PlanningStop) -> None:
        detail = f"第 {stop.year_index} 年{PLANNING_STOP_TEXT[stop.kind]}：{stop.detail}"
        failure = None
        if not c.results:
            failure = RunFailure(
                clone_id=c.clone_id,
                stage="planning",
                error_kind="contract",
                message=detail[:600],
                planning_stop=stop,
            )
        self._record(c, "planning_stopped", detail, stop=stop, failure=failure)

    # ------------------------------------------------------------------ 发送之前的检查（E1-7B）

    def _check_request(self, tax: LifeTaxBinding, request: Any, call: PlannerCall) -> None:
        """发送之前：请求里的税费说明等于执行用的税务绑定的投影；真实模型的最终请求体再查一遍（研究真值、评分参数、未来事件、
        凭证、写给开发者的说明）。不通过就停下（``request_check``），什么都不发送。"""
        problems: list[str] = []
        policy = getattr(tax.function, "policy", None)
        if not isinstance(policy, SyntheticTaxPolicy) or visible_tax_rules(policy) != request.tax:
            problems.append(
                "请求里的税费说明不是由执行用的税务绑定投影出来的：分身看到的税与环境扣的税必须是同一份"
            )
        body_fn = getattr(self.planner, "provider_body", None)
        if callable(body_fn):
            problems += final_request_problems(
                body_fn(call), call.payload, forbidden_texts=self.forbidden
            )
        if problems:
            raise PlannerHalt(
                "request_check",
                f"{call.request_id} 发送之前的请求检查没有通过，没有发送："
                + "；".join(problems)[:450],
            )

    # ------------------------------------------------------------------ 一年的规划

    def _plan_year(self, c: _Clone, year: int, catalog: Any, tax: LifeTaxBinding) -> _YearPlan:
        w = self.world
        policy = w.config.planning
        allowed = policy.attempts_per_year
        facts = w.facts.sheet_for(c.city)
        assert facts is not None
        profile = self._profile(c.person_id)
        revision = None
        c.attempts.append(0)
        for attempt in range(1, allowed + 1):
            request_id = f"{self.run_id}/{c.clone_id}/y{year}/a{attempt}"
            memory = build_memory(
                clone_id=c.clone_id, city=c.city, current_year=year, years=c.memory
            )
            request = build_request(
                request_id=request_id,
                attempt=attempt,
                attempts_allowed=allowed,
                permission=w.permission,
                profile=profile,
                state=c.state,
                catalog=catalog,
                protocol=w.bundle.protocol,
                facts=facts,
                memory=memory,
                last_feedback=c.last_feedback,
                revision=revision,
                tax_policy=_tax_policy(w.bundle, c.city),
                prompt_template=w.config.prompt_template,
            )
            payload = request_payload(request)
            fp = payload_fingerprint(payload)
            recorded = self.recorded.get((c.clone_id, year, attempt))
            if recorded is not None:
                if recorded["request_fingerprint"] != fp:
                    raise RunRefusedError(
                        f"恢复时重建的 {request_id} 与归档里的请求不同：不能冒充原运行继续"
                    )
                text, error = recorded["response_text"], recorded["error"]
                finish_reason = recorded.get("finish_reason")
                transport_attempts = int(recorded.get("transport_attempts", 1))
                self._used_calls.add((c.clone_id, year, attempt))
            else:
                self._leave_replay(f"第 {self.calls_used + 1} 次规划调用")
                if self.calls_used >= policy.global_call_limit:
                    self._pending = (c.clone_id, year, attempt - 1)
                    raise _BudgetExhausted()
                if self.stop_after_calls is not None and self.calls_used >= self.stop_after_calls:
                    self._pending = (c.clone_id, year, attempt - 1)
                    raise RunInterrupted(f"在第 {self.calls_used} 次规划调用之后停下")
                system, user = render_prompt(payload)
                call = PlannerCall(request_id, system, user, payload)
                self._pending = (c.clone_id, year, attempt - 1)
                self._check_request(tax, request, call)
                text, error, finish_reason, transport_attempts = None, None, None, 1
                usage: dict[str, Any] | None = None
                try:
                    reply = self.planner.plan(call)
                except (RunInterrupted, PlannerHalt):
                    raise
                except ServiceRecordMismatchError as exc:
                    # 服务明细与这次请求对不上：归档问题，不是规划器异常——不写调用记录、不执行生活，拒绝继续
                    raise RunRefusedError(
                        f"{request_id}：服务请求明细与这次请求对不上，不能冒充原运行继续：{exc}"[
                            :900
                        ]
                    ) from exc
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:600]
                else:
                    if isinstance(reply, PlannerReply):
                        text, finish_reason = reply.text, reply.finish_reason
                        transport_attempts, usage = reply.transport_attempts, reply.usage
                    elif isinstance(reply, str):
                        text = reply
                    else:
                        error = f"规划器返回了 {type(reply).__name__}，不是文字或 PlannerReply"
                self.calls_used += 1
                self.archive.append(
                    "calls",
                    {
                        "request_id": request_id,
                        "clone_id": c.clone_id,
                        "year_index": year,
                        "attempt": attempt,
                        "planner": self.planner.identity.model_dump(mode="json"),
                        "transport_attempts": transport_attempts,
                        "usage": usage,
                        "finish_reason": finish_reason,
                        "request_fingerprint": fp,
                        "system_prompt_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                        "user_prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                        "request": payload,
                        "response_text": text,
                        "error": error,
                    },
                )
            c.attempts[-1] = attempt
            self._pending = (c.clone_id, year, attempt)
            base = dict(
                request_id=request_id,
                clone_id=c.clone_id,
                year_index=year,
                attempt=attempt,
                phase="plan" if attempt == 1 else "revise",
                planner_id=self.planner.identity.planner_id,
                planner_kind=self.planner.identity.kind,
                transport_attempts=transport_attempts,
                finish_reason=finish_reason,
                request_fingerprint=fp,
            )
            if error is not None:
                self._attempt(
                    AttemptRecord(
                        **base,
                        response_kind="planner_error",
                        parse="not_applicable",
                        disposition="internal_error",
                        detail=error,
                    )
                )
                raise _ProgramError("planning", "unexpected", f"规划器抛出异常：{error}")
            assert text is not None
            response_fp = hashlib.sha256(text.encode("utf-8")).hexdigest()
            parsed = parse_output(text, person_id=c.person_id, clone_id=c.clone_id, year_index=year)
            output = parsed.output
            texts = dict(
                need_summary=None if output is None else output.need_summary,
                rationale=None if output is None else output.rationale,
            )
            incomplete = finish_reason not in (None, "stop")
            if incomplete or parsed.action is None:
                # 回答没有正常结束（例如达到输出上限被截断）：即使碰巧能解析也不接受半截行动，按格式问题进入修订
                diags: tuple[VisibleDiagnostic, ...] = (
                    incomplete_output_diagnostics(str(finish_reason))
                    if incomplete
                    else parsed.diagnostics
                )
                check_fields: dict[str, Any] = {"parse": "format_error"}
                codes = tuple(d.code for d in diags)
            else:
                check = check_year_plan(
                    protocol=w.bundle.protocol,
                    state=c.state,
                    action=parsed.action,
                    catalog=catalog,
                    tax=tax,
                )
                kind = classify_check(check)
                codes = tuple(p.code for p in check.problems)
                check_fields = {
                    "parse": "parsed",
                    "check_stage": check.stage,
                    "check_status": check.status,
                    "action_fingerprint": model_fingerprint(parsed.action),
                }
                if kind == "accept":
                    self._attempt(
                        AttemptRecord(
                            **base,
                            **check_fields,
                            **texts,
                            response_fingerprint=response_fp,
                            response_kind="text",
                            disposition="accepted",
                        )
                    )
                    assert output is not None
                    return _YearPlan(
                        action=parsed.action,
                        need_summary=output.need_summary,
                        attempt=attempt,
                        request_id=request_id,
                    )
                if kind == "city_data":
                    self._attempt(
                        AttemptRecord(
                            **base,
                            **check_fields,
                            **texts,
                            response_fingerprint=response_fp,
                            response_kind="text",
                            disposition="city_data",
                            problem_codes=codes,
                            detail=f"{c.city} 的资料问题："
                            + "；".join(p.message for p in check.problems)[:500],
                        )
                    )
                    return _YearPlan(
                        stop=PlanningStop(
                            year_index=year,
                            kind="city_data",
                            attempts=attempt,
                            problem_codes=codes,
                            detail=(
                                f"影响范围：{c.city}（这座城的资料，不扩散到其他城市）。"
                                + "；".join(p.message for p in check.problems)
                            )[:400],
                        )
                    )
                if kind == "internal":
                    self._attempt(
                        AttemptRecord(
                            **base,
                            **check_fields,
                            **texts,
                            response_fingerprint=response_fp,
                            response_kind="text",
                            disposition="internal_error",
                            problem_codes=codes,
                        )
                    )
                    raise _ProgramError(
                        "planning",
                        "contract",
                        f"执行前检查在 {check.stage} 阶段发现运行器或配置的问题（{sorted(set(codes))}）："
                        + "；".join(p.message for p in check.problems)[:400],
                    )
                diags = plan_diagnostics(check, parsed.action, w.permission)
            last = attempt == allowed
            self._attempt(
                AttemptRecord(
                    **base,
                    **check_fields,
                    **texts,
                    response_fingerprint=response_fp,
                    response_kind="text",
                    disposition="revisions_exhausted" if last else "revise",
                    problem_codes=codes,
                    diagnostics=diags,
                )
            )
            if last:
                return _YearPlan(
                    stop=PlanningStop(
                        year_index=year,
                        kind="revisions_exhausted",
                        attempts=attempt,
                        problem_codes=codes,
                        detail=(
                            f"{allowed} 份候选都没有通过执行前检查；最后一份的问题："
                            + "；".join(d.text for d in diags)
                        )[:400],
                    )
                )
            revision = revision_block(
                attempt=attempt + 1,
                attempts_allowed=allowed,
                previous_output=text,
                diagnostics=diags,
            )
        raise AssertionError("修订循环必须在上限内返回")  # pragma: no cover

    # ------------------------------------------------------------------ 执行

    def _commit(
        self,
        c: _Clone,
        action: YearAction,
        need_summary: str,
        attempt: int,
        request_id: str,
        catalog: Any,
        tax: LifeTaxBinding,
        inputs: ScoringInputs,
    ) -> None:
        """把被接受的年度行动交给现有的年度交接执行（结算 → 评分 → 反馈 → 交接）。恢复时归档里已有这一年的已接受行动，
        就与重新执行得到的记录（行动、指纹、绑定、交接）逐字段比较，不追加。"""
        archived = self.accepted_archive.get((c.clone_id, action.year_index))
        result = run_life_year(
            protocol=self.world.bundle.protocol,
            state=c.state,
            action=action,
            catalog=catalog,
            tax=tax,
            scoring=inputs,
        )
        sealed = result.scored_year
        if sealed is None or sealed.catalog_fingerprint != self.world.identities.get(
            f"catalog:{c.city}"
        ):
            raise _ProgramError(
                "life",
                "contract",
                f"{c.clone_id} 第 {action.year_index} 年：执行用的目录不是请求里给分身看的那一份（与运行清单的身份不同）",
            )
        if result.settlement.status not in ("completed", "exited", "stopped"):
            raise _ProgramError(
                "life",
                "unexpected",
                f"{c.clone_id} 第 {action.year_index} 年：通过执行前检查的计划在真实执行里没有提交（{result.settlement.status}）",
            )
        nxt = result.next_state
        record = AcceptedYear(
            clone_id=c.clone_id,
            year_index=action.year_index,
            attempt=attempt,
            request_id=request_id,
            action=action,
            action_fingerprint=model_fingerprint(action),
            need_summary=need_summary,
            binding_fingerprint=result.binding_fingerprint,
            handoff=result.handoff,
            next_state_fingerprint=None if nxt is None else model_fingerprint(nxt),
        )
        if archived is not None:
            if archived.model_dump(mode="json") != record.model_dump(mode="json"):
                raise RunRefusedError(
                    f"重放 {c.clone_id} 第 {action.year_index} 年得到的绑定与归档不同：不能冒充原运行继续"
                )
            self._used_accepted.add((c.clone_id, action.year_index))
        else:
            self._leave_replay(f"写 {c.clone_id} 第 {action.year_index} 年的已接受行动")
            self.archive.append("accepted", record.model_dump(mode="json"))
        c.results.append(result)
        c.accepted.append(record)
        feedback = visible_feedback(result.settlement)
        c.memory.append(
            MemoryYear(
                clone_id=c.clone_id,
                city=c.city,
                year_index=action.year_index,
                feedback=feedback,
                need_summary=need_summary,
                rationale=action.rationale or "",
            )
        )
        c.last_feedback = feedback
        if result.handoff == "next_year_ready":
            assert nxt is not None
            c.state = nxt
        if archived is None:  # 重放归档的年份不改写进度文件（拒绝时原归档保持不变）
            self._pending = None
            self._write_progress()
            self.commits_made += 1
            if self.stop_after_commits is not None and self.commits_made >= self.stop_after_commits:
                # 先导试跑：提交够了就在年度边界停下（之后可以恢复，也可以只读重放已提交的年份）
                raise RunInterrupted(f"提交 {self.commits_made} 个年度之后停下")

    # ------------------------------------------------------------------ 评价与汇总

    def _outcome(self, *, evaluate: bool) -> RunOutcome:
        evaluations: dict[str, tuple[CloneEvaluation, ...]] = {}
        failures: dict[str, tuple[RunFailure, ...]] = {}
        reports: dict[str, ArmReport] = {}
        if evaluate:
            evaluations, failures, reports = evaluate_batch(
                self.world, self.lives, self.stops, self.failures
            )
            for arm_id, evs in evaluations.items():
                self.archive.write_json(
                    f"evaluations/{arm_id}.json", [e.model_dump(mode="json") for e in evs]
                )
                self.archive.write_json(
                    f"reports/{arm_id}.json", reports[arm_id].model_dump(mode="json")
                )
            self.archive.write_json(
                "failures.json",
                {a: [f.model_dump(mode="json") for f in fs] for a, fs in failures.items()},
            )
        outcome = RunOutcome(
            run_id=self.run_id,
            status=self.status,
            calls_used=self.calls_used,
            records=tuple(self.records[c] for c in self.world.order if c in self.records),
            lives=dict(self.lives),
            evaluations=evaluations,
            failures=failures,
            reports=reports,
            archive_path=self.archive.path,
            pause_reason=self.pause_reason,
            halt_kind=self.halt_kind,
        )
        if evaluate:
            self.archive.write_text("report.md", run_report(self.world, outcome))
        return outcome


def evaluate_batch(
    world: RunWorld,
    lives: dict[str, tuple[LifeYearResult, ...]],
    stops: dict[str, PlanningStop],
    run_failures: dict[str, RunFailure],
) -> tuple[
    dict[str, tuple[CloneEvaluation, ...]], dict[str, tuple[RunFailure, ...]], dict[str, ArmReport]
]:
    """E1-6A 单分身评价 + E1-6B 城市汇总（只调用已有入口）。部分轨迹带规划停止；没有轨迹的规划或程序失败记成 RunFailure。"""
    evaluations: dict[str, list[CloneEvaluation]] = {a: [] for a in world.specs}
    failures: dict[str, list[RunFailure]] = {a: [] for a in world.specs}
    bundle = world.bundle
    for entry in world.roster.entries:
        clone = entry.clone_id
        if clone in run_failures:
            for a in failures:
                failures[a].append(run_failures[clone])
            continue
        results = lives.get(clone)
        if not results:
            continue
        city = str(entry.city)
        catalog = bundle.catalog_for(city)
        profile = bundle.profile_for(entry.person_id)
        assert profile is not None
        conditions = world.scoring.conditions_for(city)
        assert catalog is not None and conditions is not None
        inputs = ScoringInputs(
            spec=world.scoring.spec,
            conditions=conditions,
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )
        stop = stops.get(clone)
        for arm_id, spec in world.specs.items():
            try:
                evaluations[arm_id].append(
                    evaluate_clone(
                        spec=spec,
                        protocol=bundle.protocol,
                        catalog=catalog,
                        tax=_tax(bundle, city),
                        scoring=inputs,
                        results=results,
                        planning_stop=stop,
                    )
                )
            except LifeContractError as exc:
                failures[arm_id].append(
                    RunFailure(
                        clone_id=clone,
                        stage="evaluation",
                        error_kind="contract",
                        message=str(exc)[:600],
                    )
                )
    reports = {a: aggregate_arm(world.roster, a, evaluations[a], failures[a]) for a in world.specs}
    return (
        {a: tuple(v) for a, v in evaluations.items()},
        {a: tuple(v) for a, v in failures.items()},
        reports,
    )


# ---------------------------------------------------------------------- 归档记录的核对（E1-7A 收尾 F1）


@dataclass(frozen=True)
class ArchiveRecords:
    """核对过的归档记录：调用与检查记录（按 分身、年份、第几份候选）、已接受的年度行动（按 分身、年份）。"""

    calls: dict[tuple[str, int, int], dict[str, Any]]
    attempts: dict[tuple[str, int, int], dict[str, Any]]
    accepted: dict[tuple[str, int], AcceptedYear]


def _where(key: tuple[str, int, int]) -> str:
    return f"{key[0]} 第 {key[1]} 年第 {key[2]} 份候选"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_archive_records(
    world: RunWorld,
    archive: RunArchive,
    run_id: str,
    *,
    pending_ok: bool,
    exempt: frozenset[str] = frozenset(),
) -> ArchiveRecords:
    """恢复与重放共用的一份归档核对，在继续执行、调用规划器或追加任何记录之前读一遍（E1-7A 收尾 F1）。

    1. 键不重复：调用与检查记录按（分身、年份、第几份候选），已接受的行动按（分身、年份）。重复就拒绝，不用后一条覆盖前一条。
    2. 调用记录：请求编号与身份；存下的请求重算的指纹（``payload_fingerprint``）等于 ``request_fingerprint``；按存下的请求重新
       渲染的提示文字指纹相同；规划器身份与运行清单相同；原始响应与规划器错误恰好有一个。
    3. 检查记录：有对应的调用；请求编号与请求指纹相同；原始响应重算的指纹等于 ``response_fingerprint``——检查记录说的就是这份响应。
    4. 已接受的行动：指向处置为 accepted 的那一份候选；行动、它记下的行动指纹与检查记录的行动指纹相同；请求编号与需求摘要相同；
       用已有的解析器（``parse_output``）解析那份原始响应，得到的就是这份行动。
    5. 中断位置：只有最后一次调用可以还没有检查记录（响应已落盘、检查尚未落盘），也只有它的 accepted 检查可以还没有已接受的
       行动（检查已落盘、年度尚未执行）。其他位置的缺口不是本程序支持的中断状态，拒绝。``pending_ok=False``（重放已经结束的
       运行）时一处缺口也不允许，``exempt`` 里的分身（记为程序错误、本来就停在半途）除外。

    这里只核对记录彼此一致，不另写计划判断：检查结果与处置在恢复时由 ``LifeRunner`` 用同一份解析、执行前检查与处置代码重新得出、
    逐项比较。冲突是运行归档的问题，不是生活出局，也不靠重新询问规划器掩盖。"""
    problems: list[str] = []
    persons = {e.clone_id: e.person_id for e in world.roster.entries}
    horizon = world.bundle.protocol.simulation_years
    planner = world.config.planner.identity.model_dump(mode="json")

    calls: dict[tuple[str, int, int], dict[str, Any]] = {}
    for i, row in enumerate(archive.records("calls"), start=1):
        try:
            key = (str(row["clone_id"]), int(row["year_index"]), int(row["attempt"]))
            request_id, payload = row["request_id"], row["request"]
            fp, sys_fp, user_fp = (
                row["request_fingerprint"],
                row["system_prompt_sha256"],
                row["user_prompt_sha256"],
            )
            who, text, error = row["planner"], row["response_text"], row["error"]
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"calls 表第 {i} 条缺少或写错了字段（{exc}）")
            continue
        where = _where(key)
        if key in calls:
            problems.append(
                f"{where}：调用记录出现了两次（calls 表第 {i} 条）——不用后一条覆盖前一条"
            )
            continue
        calls[key] = row
        if key[0] not in persons or not 1 <= key[1] <= horizon or key[2] < 1:
            problems.append(f"{where}：不在原计划名单或模拟年数之内")
            continue
        want_id = f"{run_id}/{key[0]}/y{key[1]}/a{key[2]}"
        if request_id != want_id:
            problems.append(f"{where}：请求编号是 {request_id}，应当是 {want_id}")
        if not isinstance(payload, dict) or (
            payload.get("request_id"),
            payload.get("clone_id"),
            payload.get("year_index"),
            payload.get("attempt"),
        ) != (want_id, key[0], key[1], key[2]):
            problems.append(f"{where}：存下的请求的编号、分身、年份或第几份候选与调用记录不同")
            continue
        if payload_fingerprint(payload) != fp:
            problems.append(
                f"{where}：存下的请求重算的指纹与调用记录的 request_fingerprint 不同——请求被改过或记错了"
            )
        system, user = render_prompt(payload)
        if _sha(system) != sys_fp or _sha(user) != user_fp:
            problems.append(f"{where}：按存下的请求重新渲染的提示文字与记录的提示指纹不同")
        if who != planner:
            problems.append(f"{where}：调用记录里的规划器身份与运行清单不同")
        if (text is None) == (error is None) or not isinstance(
            text if text is not None else error, str
        ):
            problems.append(f"{where}：原始响应与规划器错误必须恰好有一个")

    attempts: dict[tuple[str, int, int], dict[str, Any]] = {}
    for i, raw in enumerate(archive.records("attempts"), start=1):
        try:
            rec = AttemptRecord.model_validate(raw)
        except ValueError as exc:
            problems.append(f"attempts 表第 {i} 条不合法：{exc}"[:300])
            continue
        key = (rec.clone_id, rec.year_index, rec.attempt)
        where = _where(key)
        if key in attempts:
            problems.append(
                f"{where}：检查记录出现了两次（attempts 表第 {i} 条）——不用后一条覆盖前一条"
            )
            continue
        attempts[key] = raw
        call = calls.get(key)
        if call is None:
            problems.append(f"{where}：有检查记录但没有对应的调用记录")
            continue
        if (
            rec.request_id != call["request_id"]
            or rec.request_fingerprint != call["request_fingerprint"]
        ):
            problems.append(f"{where}：检查记录的请求编号或请求指纹与调用记录不同")
        text = call["response_text"]
        if rec.response_kind == "text":
            if not isinstance(text, str):
                problems.append(f"{where}：检查记录说有原始响应，调用记录里却没有")
            elif rec.response_fingerprint != _sha(text):
                problems.append(
                    f"{where}：原始响应重算的指纹 {_sha(text)[:12]}… 与检查记录的 response_fingerprint "
                    f"{str(rec.response_fingerprint)[:12]}… 不同——检查记录（处置 {rec.disposition}）说的不是这份响应"
                )
        elif call["error"] is None:
            problems.append(f"{where}：检查记录说规划器出错，调用记录里却没有错误")

    accepted: dict[tuple[str, int], AcceptedYear] = {}
    for i, raw in enumerate(archive.records("accepted"), start=1):
        try:
            y = AcceptedYear.model_validate(raw)
        except ValueError as exc:
            problems.append(f"accepted 表第 {i} 条不合法：{exc}"[:300])
            continue
        key = (y.clone_id, y.year_index, y.attempt)
        where = _where(key)
        if key[:2] in accepted:
            problems.append(
                f"{y.clone_id} 第 {y.year_index} 年：已接受的行动出现了两次（accepted 表第 {i} 条）"
                "——不用后一条覆盖前一条"
            )
            continue
        accepted[(y.clone_id, y.year_index)] = y
        rec = attempts.get(key)
        if rec is None:
            problems.append(f"{where}：已接受的行动没有对应的检查记录")
            continue
        if rec["disposition"] != "accepted":
            problems.append(
                f"{where}：已接受的行动指向的检查记录处置是 {rec['disposition']}——不能把没有被接受的候选当成已接受，"
                "也不能借用别的候选的行动"
            )
        action_fp = model_fingerprint(y.action)
        if not action_fp == y.action_fingerprint == rec.get("action_fingerprint"):
            problems.append(f"{where}：已接受的行动、它记下的行动指纹与检查记录的行动指纹不一致")
        if y.request_id != rec["request_id"] or y.need_summary != rec.get("need_summary"):
            problems.append(f"{where}：已接受的行动的请求编号或需求摘要与检查记录不同")
        call = calls.get(key)
        text = None if call is None else call["response_text"]
        if isinstance(text, str) and y.clone_id in persons:
            parsed = parse_output(
                text, person_id=persons[y.clone_id], clone_id=y.clone_id, year_index=y.year_index
            )
            if parsed.action is None or model_fingerprint(parsed.action) != action_fp:
                problems.append(f"{where}：已接受的行动不是解析这份原始响应得到的行动")

    if world.config.planner.identity.kind == "llm":
        problems += service_archive_problems(world, archive, run_id, calls, pending_ok=pending_ok)

    keys = list(calls)
    last = keys[-1] if keys else None
    for key in keys:
        if key[0] in exempt:
            continue
        allowed = pending_ok and key == last
        rec = attempts.get(key)
        if rec is None and not allowed:
            problems.append(
                f"{_where(key)}：有原始响应但没有检查记录——只有最后一次调用可以停在“响应已落盘、检查尚未落盘”"
            )
        if (
            rec is not None
            and rec["disposition"] == "accepted"
            and key[:2] not in accepted
            and not allowed
        ):
            problems.append(
                f"{_where(key)}：检查记录说已接受，却没有已接受的行动——只有最后一次调用可以停在“检查已落盘、年度尚未执行”"
            )
    if problems:
        shown = problems[:20] + ([f"……共 {len(problems)} 处"] if len(problems) > 20 else [])
        raise RunRefusedError(
            "归档记录彼此不一致或停在不支持的中断位置，不能冒充原运行继续或重放（这是运行归档的问题，不是生活结果；"
            "不会靠重新询问规划器掩盖）：\n  " + "\n  ".join(shown)
        )
    return ArchiveRecords(calls=calls, attempts=attempts, accepted=accepted)


def service_archive_problems(
    world: RunWorld,
    archive: RunArchive,
    run_id: str,
    calls: dict[tuple[str, int, int], dict[str, Any]],
    *,
    pending_ok: bool,
) -> list[str]:
    """真实模型运行（E1-7B；收尾 F1 起与新回复、缓存共用 ``service_history``）：服务请求明细与规划记录是否一致。

    * 每份规划记录对应的服务历史：按规划记录里存下的载荷与当前设定重新渲染请求体，逐字节等于每次发送意图的正文（改了正文、温度、
      模型或输出上限都对不上）；运行、分身、年份、候选、设定指纹与报价版本相同；意图与结果按顺序配对；有原始回复的规划记录必须
      对应一份**可用**的成功回复（返回模型符合 ``returned_model_accepted`` 的规则），原文、服务尝试序号与结束原因相同。
    * 没有规划记录的服务历史（回复已落盘而规划记录还没写、发送状态不确定、服务失败或返回模型不同而停下）：只在允许中断时存在、
      最多一份，同样逐项核对（请求正文里取回的载荷必须就是这份候选）。
    * 明细里不能有别的运行的记录。"""
    llm, budget = world.config.planner.llm, world.config.paid_budget
    assert llm is not None
    try:
        records = ProviderCallLog(archive.path).records()
    except (ValueError, LifeContractError) as exc:
        return [f"provider_calls 表读不了：{exc}"[:300]]
    problems: list[str] = []
    foreign = sorted({r.run_id for r in records if r.run_id != run_id})
    if foreign:
        problems.append(f"服务请求明细里有别的运行的记录：{foreign[:3]}")
    price_version = None if budget is None else budget.price.price_version
    covered: set[str] = set()
    for key, row in calls.items():
        rid, payload = row.get("request_id"), row.get("request")
        if not isinstance(rid, str) or not isinstance(payload, dict):
            continue
        covered.add(rid)
        hist = service_history(
            records,
            request_id=rid,
            run_id=run_id,
            settings=llm,
            price_version=price_version,
            expected_body=expected_provider_body(llm, payload),
        )
        where = _where(key)
        problems += [f"{where}：{p}" for p in hist.problems]
        text = row.get("response_text")
        if not isinstance(text, str) or hist.problems:
            continue
        if hist.usable is None:
            problems.append(
                f"{where}：规划记录里有原始回复，服务请求明细里却没有可用的成功回复（返回模型不同的回复不能用来规划）"
            )
            continue
        o = hist.usable.outcome
        assert o is not None
        if (o.response or {}).get("text") != text:
            problems.append(
                f"{where}：规划记录里的原始回复与服务请求明细里的回复原文不同——回复被改过"
            )
        if hist.usable.number != row.get("transport_attempts") or o.finish_reason != row.get(
            "finish_reason"
        ):
            problems.append(f"{where}：规划记录的服务尝试次数或结束原因与服务请求明细不同")
    leftover = sorted({r.request_id for r in records if r.run_id == run_id} - covered)
    if leftover and not pending_ok:
        problems.append(
            f"服务请求明细里有没有规划记录的请求（这次运行已经结束，不该有）：{leftover[:3]}"
        )
    if len(leftover) > 1:
        problems.append(
            f"服务请求明细里有 {len(leftover)} 个请求没有规划记录（合法的中断最多一个）：{leftover[:3]}"
        )
    for rid in leftover:
        hist = service_history(
            records, request_id=rid, run_id=run_id, settings=llm, price_version=price_version
        )
        problems += [f"{rid}（还没有规划记录）：{p}" for p in hist.problems]
    return problems


def ledger_problems_for_run(
    world: RunWorld, archive: RunArchive, run_id: str, events: list[dict[str, Any]]
) -> list[str]:
    """本运行的服务请求明细与一本账的历史是否对得上（E1-7B 收尾 F2；规则在 ``orchestration.life_budget.ledger_history_problems``）。"""
    try:
        records = ProviderCallLog(archive.path).records()
    except (ValueError, LifeContractError) as exc:
        return [f"provider_calls 表读不了：{exc}"[:300]]
    return ledger_history_problems(events, run_id=run_id, records=records)


def budget_reconciliation(world: RunWorld, run_dir: Path) -> dict[str, Any]:
    """只读的付费对账（E1-7B 收尾 F2）：读声明的账本（不存在就报"没有账本"，不创建、不写），核对本运行的服务明细与账本历史。
    与生活重放分开报告。"""
    budget = world.config.paid_budget
    if budget is None:
        if world.config.unmetered_reason is not None:
            return {
                "applies": False,
                "note": f"真实模型运行声明不记账（{world.config.unmetered_reason}），没有付费账本",
            }
        return {"applies": False, "note": "mock 运行没有付费账本"}
    archive = RunArchive(run_dir, create=False)
    run_id = RunManifest.model_validate(archive.read_json("manifest.json")).run_id
    found = read_ledger(Path(budget.ledger_dir))
    if found is None:
        return {
            "applies": True,
            "ledger_found": False,
            "ledger_dir": budget.ledger_dir,
            "problems": ["没有找到账本"],
        }
    _header, events = found
    records = ProviderCallLog(archive.path).records()
    problems = ledger_problems_for_run(world, archive, run_id, events)
    mine = [e for e in events if e.get("run_id") == run_id and e.get("event") == "reserve"]
    return {
        "applies": True,
        "ledger_found": True,
        "ledger_dir": budget.ledger_dir,
        "run_id": run_id,
        "service_intents": sum(1 for r in records if r.event == "intent"),
        "ledger_reservations_for_run": len(mine),
        "ledger_events_total": len(events),
        "problems": problems,
        "consistent": not problems,
    }


# ---------------------------------------------------------------------- 入口：新运行、恢复、重放


def _manifest(world: RunWorld, run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        planning_version=PLANNING_VERSION,
        run_config_fingerprint=world.identities["run_config"],
        identities=world.identities,
        planner=world.config.planner.identity,
        planning=world.config.planning,
        information=world.config.information,
        order=world.order,
    )


def start_run(
    world: RunWorld,
    planner: Planner,
    out_dir: Path,
    *,
    run_id: str | None = None,
    stop_after_calls: int | None = None,
    stop_after_commits: int | None = None,
) -> RunOutcome:
    """新运行：先核对实际规划器与运行配置的声明（不一致就拒绝，不建归档），再建归档（目录已存在就拒绝）、写运行清单与运行前
    说明、冻结输入文件（第一次规划调用之前），然后顺序调度。"""
    _check_planner(world, planner)
    archive = RunArchive(out_dir, create=True)
    rid = run_id or new_run_id()
    archive.write_json("manifest.json", _manifest(world, rid).model_dump(mode="json"))
    if world.input_files:
        snapshot_inputs(world, archive)
    _attach(planner, archive, rid)
    profile = model_profile(planner, world)
    if profile is not None:
        archive.write_json("model_profile.json", profile)
    archive.write_json("support_scope.json", world.scope)
    archive.write_text("support_scope.md", scope_markdown(world))
    archive.write_json("frozen_roster.json", world.roster.model_dump(mode="json"))
    archive.write_json("alignment.json", world.alignment)
    runner = LifeRunner(
        world,
        planner,
        archive,
        rid,
        stop_after_calls=stop_after_calls,
        stop_after_commits=stop_after_commits,
    )
    runner._write_progress()
    return runner.run()


def model_profile(planner: object, world: RunWorld) -> dict[str, Any] | None:
    """真实模型运行的调用档案（2026-10-13，DECISIONS/0032），开跑时写进归档：请求的型号、模型角色、版本策略、返回型号的核对规则、
    实际生效的调用参数（规划器 ``effective_spec`` 报出的设定渲染出的请求体，去掉消息）、报价版本与写入时间。每次请求实际返回的型号与
    时间另在服务请求明细里逐条记。mock 没有这一份。"""
    spec_fn = getattr(planner, "effective_spec", None)
    spec = spec_fn() if callable(spec_fn) else None
    llm = getattr(spec, "llm", None)
    if llm is None:
        return None
    params = {k: v for k, v in expected_provider_body(llm, {}).items() if k != "messages"}
    budget = world.config.paid_budget
    rolling = llm.effective_version_policy == "rolling"
    return {
        "written_at_utc": utc_now_iso(),
        "requested_model": llm.model,
        "model_role": llm.model_role,
        "provider": llm.provider,
        "base_url": llm.base_url,
        "version_policy": llm.effective_version_policy,
        "returned_model_rule": (
            "返回名等于请求名，或请求名后接一个快照日期（-YYYY-MM-DD / -YYYYMMDD / -MMDD）；其他名字都不接受"
            if rolling
            else "返回名必须与请求名完全相同"
        ),
        "reproducibility": (
            "滚动型号：同一个名字背后的模型可能被供应商更换；复现以请求明细里每次实际返回的型号与时间为准，不能保证以后得到同样的回复"
            if rolling
            else "固定版本的快照"
        ),
        "effective_call_parameters": params,
        "timeout_seconds": llm.timeout_seconds,
        "sdk_max_retries": llm.sdk_max_retries,
        "service_retries": llm.service_retries,
        "price_version": None if budget is None else budget.price.price_version,
        "ledger_dir": None if budget is None else budget.ledger_dir,
    }


def _attach(planner: object, archive: RunArchive, run_id: str) -> None:
    """真实模型规划器把服务请求明细写进这次运行的归档（归档库的 ``provider_calls`` 表）；mock 没有这一步。"""
    attach = getattr(planner, "attach", None)
    if callable(attach):
        attach(archive_path=archive.path, run_id=run_id)


def _check_manifest(world: RunWorld, archive: RunArchive) -> RunManifest:
    manifest = RunManifest.model_validate(archive.read_json("manifest.json"))
    want = _manifest(world, manifest.run_id)
    # 开发期（所有者 2026-10-18）：源码指纹照常记进运行清单，但不再拦截续跑或重放——改了代码，旧运行仍可接着跑；配置、规则、
    # 事实、初态、许可、模型等内容身份照旧逐项核对。研究定稿时再恢复按源码拦截。
    diff = sorted(
        k
        for k in set(manifest.identities) | set(want.identities)
        if not (k.startswith("source:") or k == "source_set")
        and manifest.identities.get(k) != want.identities.get(k)
    )
    for name in ("planner", "planning", "information", "order"):
        if getattr(manifest, name) != getattr(want, name):
            diff.append(name)
    if diff:
        raise RunRefusedError(
            "运行清单与现在的配置、规则、事实、初态、许可或代码不同（"
            + "、".join(diff[:12])
            + (" 等" if len(diff) > 12 else "")
            + "）：不能冒充原运行继续或重放"
        )
    return manifest


def resume_run(
    world: RunWorld,
    planner: Planner,
    run_dir: Path,
    *,
    stop_after_calls: int | None = None,
    stop_after_commits: int | None = None,
) -> RunOutcome:
    """从年度边界或两份候选之间恢复（E1-7A 收尾 F1）。

    依次：核对运行清单（全部内容身份）→ 核对实际规划器与声明 → 核对进度 → 核对归档记录（``load_archive_records``：重复键、
    请求 / 提示 / 响应重算的指纹、检查记录与调用、已接受的行动与检查记录的关联、支持的中断位置）→ 从第一个分身起按同一套代码
    重走已经记录的路径：复用原始响应，重新解析、检查、处置与执行，逐项与归档比较（不重问规划器、不重复累计生活结果）→ 所有已有
    记录都重放到之后，才从下一个还没有记录的地方继续。任何一步不一致都拒绝：不写任何文件、不调用规划器，原归档保持不变。
    尝试次数与已用调用次数不清零。"""
    archive = RunArchive(run_dir, create=False)
    manifest = _check_manifest(world, archive)
    _check_planner(world, planner)
    progress = RunProgress.model_validate(archive.read_json("progress.json"))
    if progress.status not in ("running", "interrupted", "halted"):
        raise RunRefusedError(f"这次运行已经是 {progress.status}：没有可以恢复的部分")
    records = load_archive_records(world, archive, manifest.run_id, pending_ok=True)
    if progress.run_id != manifest.run_id or progress.calls_used > len(records.calls):
        raise RunRefusedError(
            f"进度记录（运行 {progress.run_id}，{progress.calls_used} 次调用）与运行清单或调用记录（{len(records.calls)} 次）"
            "对不上：不能冒充原运行继续"
        )
    if world.config.paid_budget is not None:
        # E1-7B 收尾 F2：本运行花过的钱必须都还在账上——缺预留、缺核算、金额矛盾都在新增请求之前拒绝
        events = planner.budget.events()  # type: ignore[attr-defined]
        problems = ledger_problems_for_run(world, archive, manifest.run_id, events)
        if problems:
            raise RunRefusedError(
                "本运行的服务请求明细与付费账本的历史对不上，拒绝恢复（不当作空白预算、不猜测补回费用）：\n  "
                + "\n  ".join(problems[:12])
            )
    runner = LifeRunner(
        world,
        planner,
        archive,
        manifest.run_id,
        stop_after_calls=stop_after_calls,
        stop_after_commits=stop_after_commits,
        records=records,
        expected=progress.clones,
    )
    runner.calls_used = len(records.calls)
    _attach(planner, archive, manifest.run_id)
    return runner.run()


def replay_run(world: RunWorld, run_dir: Path) -> dict[str, Any]:
    """只读重放：从同一个初态，用归档里已接受的行动重新调用现有执行路径，重建生活、评价与汇总，与归档逐项比较。不调用
    规划器，也不反序列化结果后手工设置封存标记——封存的年度绑定由真实执行入口重新产生。"""
    archive = RunArchive(run_dir, create=False)
    manifest = _check_manifest(world, archive)
    progress = RunProgress.model_validate(archive.read_json("progress.json"))
    # 还没有结束的运行（先导试跑在年度边界停下、批次停下）：只重放已提交的年份、核对绑定，不评价
    partial = progress.status in ("running", "interrupted", "halted")
    archived = load_archive_records(
        world,
        archive,
        manifest.run_id,
        pending_ok=partial,
        exempt=frozenset(r.clone_id for r in progress.clones if r.status == "program_error"),
    )
    accepted: dict[str, list[AcceptedYear]] = {}
    for y in archived.accepted.values():
        accepted.setdefault(y.clone_id, []).append(y)
    records = {r.clone_id: r for r in progress.clones}
    lives: dict[str, tuple[LifeYearResult, ...]] = {}
    stops: dict[str, PlanningStop] = {}
    failures: dict[str, RunFailure] = {}
    rows = []
    for entry in world.roster.entries:
        r = records.get(entry.clone_id)
        years = accepted.get(entry.clone_id, [])
        state = world.bundle.initial_state(entry.clone_id)
        assert state is not None
        city = str(entry.city)
        catalog = world.bundle.catalog_for(city)
        profile = world.bundle.profile_for(entry.person_id)
        assert profile is not None
        conditions = world.scoring.conditions_for(city)
        assert catalog is not None and conditions is not None
        inputs = ScoringInputs(
            spec=world.scoring.spec,
            conditions=conditions,
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )
        results: list[LifeYearResult] = []
        same = True
        for y in sorted(years, key=lambda x: x.year_index):
            result = run_life_year(
                protocol=world.bundle.protocol,
                state=state,
                action=y.action,
                catalog=catalog,
                tax=_tax(world.bundle, city),
                scoring=inputs,
            )
            same &= (
                result.binding_fingerprint == y.binding_fingerprint and result.handoff == y.handoff
            )
            results.append(result)
            if result.next_state is not None and result.handoff == "next_year_ready":
                state = result.next_state
        if r is not None and r.status != "program_error" and results:
            lives[entry.clone_id] = tuple(results)
        if r is not None and r.planning_stop is not None and results:
            stops[entry.clone_id] = r.planning_stop
        if r is not None and r.failure is not None:
            failures[entry.clone_id] = r.failure
        rows.append(
            {
                "clone_id": entry.clone_id,
                "status": None if r is None else r.status,
                "years_replayed": len(results),
                "bindings_identical": same,
            }
        )
    if partial:
        return {
            "planner_calls": 0,
            "partial": True,
            "status": progress.status,
            "clones": rows,
            "years_replayed": sum(x["years_replayed"] for x in rows),
            "bindings_identical": all(x["bindings_identical"] for x in rows),
            "evaluations_identical": {},
            "reports_identical": {},
            "report_fingerprints": {},
        }
    evaluations, _fails, reports = evaluate_batch(world, lives, stops, failures)
    eval_same = {}
    for arm_id, evs in evaluations.items():
        stored = archive.read_json(f"evaluations/{arm_id}.json")
        eval_same[arm_id] = [e.model_dump(mode="json") for e in evs] == stored
    report_same = {
        a: reports[a].model_dump(mode="json") == archive.read_json(f"reports/{a}.json")
        for a in reports
    }
    return {
        "planner_calls": 0,
        "partial": False,
        "status": progress.status,
        "clones": rows,
        "years_replayed": sum(x["years_replayed"] for x in rows),
        "bindings_identical": all(x["bindings_identical"] for x in rows),
        "evaluations_identical": eval_same,
        "reports_identical": report_same,
        "report_fingerprints": {a: reports[a].report_fingerprint for a in reports},
    }


# ---------------------------------------------------------------------- 中文说明


def scope_markdown(world: RunWorld) -> str:
    lines = ["# 运行前的支持范围说明（E1-7）", ""]
    for title, items in world.scope.items():
        lines += [f"## {title}", ""]
        lines += [f"- {x}" for x in items]
        lines.append("")
    return "\n".join(lines)


def _f(x: Any) -> str:
    return "—" if x is None else f"{x:.6f}"


def run_report(world: RunWorld, outcome: RunOutcome) -> str:
    """中文运行报告：每个分身的结局与每年用了几次规划调用；每个运行臂的城市汇总（完整者均值、出局、缺失范围）。"""
    llm = world.config.planner.llm
    kind = (
        "mock 规划器，不代表真实模型的能力"
        if llm is None
        else f"真实模型 {llm.model}，只说明这一次合成试跑"
    )
    lines = [
        f"# E1-7 运行报告（合成探索，{'mock 规划器' if llm is None else '真实模型规划器'}）",
        "",
        f"运行 {outcome.run_id}：状态 {outcome.status}；规划调用 {outcome.calls_used} / "
        f"{world.config.planning.global_call_limit} 次；规划器 {world.config.planner.identity.planner_id}（{kind}）。",
        "全部数字来自合成样例，不是现实城市评价，不排序真实城市；缺失范围不是置信区间。",
        "",
        "## 每个分身",
        "",
        "| 分身 | 城市 | 结局 | 提交年数 | 每年规划调用 | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    for r in outcome.records:
        lines.append(
            f"| {r.clone_id} | {r.city.code} | {CLONE_RUN_STATUS_TEXT[r.status]} | {r.years_committed} | "
            f"{'、'.join(str(a) for a in r.attempts_by_year) or '—'} | {r.detail.replace('|', '/')} |"
        )
    missing = [c for c in world.order if c not in {r.clone_id for r in outcome.records}]
    if missing:
        lines.append(f"| （没有记录）{'、'.join(missing)} | | 没有运行 | 0 | — | |")
    if outcome.pause_reason:
        lines += ["", f"**批次暂停：** {outcome.pause_reason}"]
    for arm_id, report in outcome.reports.items():
        lines += ["", f"## 城市汇总（运行臂 {arm_id}）", ""]
        lines += [
            "| 用户 | 城市 | 原计划 | 完整 C | 出局 E | 缺失 M | 完整者平均 J | 缺失范围（不是置信区间） | 缺失原因 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for cr in report.city_reports:
            reasons = "、".join(
                f"{MISSING_REASON_TEXT[x.reason]} {x.count}" for x in cr.missing_reasons
            )
            rng = (
                "—"
                if cr.range_lower_display is None
                else f"[{cr.range_lower_display:.6f}, {cr.range_upper_display:.6f}]"
            )
            lines.append(
                f"| {cr.user_id} | {cr.city.code} | {cr.planned} | {cr.complete} | {cr.exits} | {cr.missing} | "
                f"{_f(cr.mean_j_display)} | {rng} | {reasons or '—'} |"
            )
    lines += [
        "",
        "说明：完整者平均 J 只用完整评价的分身，不代表原计划全部分身；确认的生活出局不进均值，另报人数与事件链；"
        "规划没有完成、停止、反馈受阻与程序问题作为模拟缺失进入范围（"
        + "、".join(
            f"{PLANNING_STOP_TEXT[k]} → {MISSING_REASON_TEXT[v]}"
            for k, v in PLANNING_STOP_REASON.items()
        )
        + "）。",
    ]
    return "\n".join(lines) + "\n"


def planner_payloads(archive_path: Path) -> list[dict[str, Any]]:
    """归档里全部可见请求（测试与审计用）。"""
    return [c["request"] for c in RunArchive(archive_path, create=False).records("calls")]
