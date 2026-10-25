"""确认画像 → E1 输入（E3-A 模块 C，DECISIONS/0034）。

编译做四件事，全部经过 E1 已有的加载、名单与运行前检查，不另写一套宽松校验：

1. **显式采用产品协议的基础样例**：读资料包 ``e1_base`` 指向的生活输入、评分、评价与城市事实，逐项核对协议、评分版本与评分
   内容身份；对不上就拒绝编译。不依赖 CLI 默认。
2. **协议原样沿用**：生活规则、资源、职业、储蓄与疾病规则、反馈政策与健康输入声明都来自基础样例；只把健康输入声明的问卷版本
   写成确认画像用的那一版（版本不同就拒绝，不改读旧答案）。评分规格原样沿用。
3. **六维产品画像**（``ProductLifeProfile``）：确认的六维与物质精确分数原样带过去，健康回答带题目、状态、选项文字与读法；不补
   ``healthcare = 0``，不伪造研究真值。
4. **标准开局**（E1 标准开局 v1）：同一用户跨城、跨重复序号相同的年龄、现金、能力、基础时间与精力；健康、没有已有物品、病史、
   在学课程与资金目标；开局岗位按当地目录里与确认职业同一分类代码的岗位匹配（城市可以不同，个人条件不能随城变）；能力只来自
   用户确认的事实，不凭职业文字推断。不随机生成家具、病史或财富。

便利设备与服务由 ``product.catalog`` 从资料候选编入城市目录（替换基础样例里的同类机会）；其余目录原样复制。

正式运行设置（E3C，``product.execution.ExecutionSpec``）：给了就按它写运行配置——规划器（离线 mock 或真实模型）、规划候选上限、
服务重试、付费预算（每次运行一本新账，在工作目录的 ``budget/`` 里）。规模（城市、每城分身、年数）必须与设置一致，不一致就拒绝，
不偷偷删城或减分身。没有给时沿用资料包声明的离线 mock（与之前逐字节相同）。
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from agentlayer.life_mock import PRODUCT_MOCK_IDENTITY
from common.life_planning_contracts import MockScript
from common.life_product_profile import (
    PRODUCT_PROFILE_VERSION,
    ProductHealthAnswer,
    ProductLifeProfile,
)
from common.product_contracts import CatalogCompilation, ConfirmedProfile, ProductIssue
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import load_scoring_config
from product.answers import health_meaning, issue
from product.catalog import compile_catalog
from product.display import Code, Ent, Sub, t
from product.pack import DataPack
from product.public_errors import profile_contract_issue

if TYPE_CHECKING:
    from product.execution import ExecutionSpec

COMPILER = "e3a-compile-v1"


class CompileRefusedError(ValueError):
    def __init__(self, issues: tuple[ProductIssue, ...]) -> None:
        super().__init__("；".join(i.message_zh for i in issues))
        self.issues = issues


@dataclass
class CompiledInputs:
    """一次编译的产物：写好的 E1 输入文件（用途 → 路径）、画像、目录编译与开局岗位（名称与机会编号）。"""

    out_dir: Path
    paths: dict[str, Path]
    profile: ProductLifeProfile
    catalog: CatalogCompilation
    start_jobs: dict[str, str]
    clones: tuple[str, ...]
    issues: tuple[ProductIssue, ...] = field(default_factory=tuple)
    #: 城市 → 开局岗位的机会编号（显示附层按它绑定岗位名称）
    start_job_ids: dict[str, str] = field(default_factory=dict)

    def file_fingerprints(self) -> dict[str, str]:
        return {
            k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in sorted(self.paths.items())
        }


def _load_raw(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def base_problems(pack: DataPack) -> list[ProductIssue]:
    """资料包显式采用的基础样例还是不是声明的那一套（协议、评分版本与评分内容身份）。"""
    base = pack.body.e1_base
    bundle, _ = load_life_bundle(Path(base.life_input), require_settlement_ready=True)
    scoring, _ = load_scoring_config(Path(base.scoring_config), bundle)
    out = []
    for name, what, got, want in (
        (
            "协议",
            Sub("preflight.base_item.protocol", "协议"),
            bundle.protocol.protocol_id,
            base.protocol_id,
        ),
        (
            "评分版本",
            Sub("preflight.base_item.scoring_version", "评分版本"),
            scoring.spec.scoring_version,
            base.scoring_version,
        ),
        (
            "评分内容身份",
            Sub("preflight.base_item.scoring_fingerprint", "评分内容身份"),
            scoring.spec.content_fingerprint,
            base.scoring_content_fingerprint,
        ),
    ):
        if got != want:
            out.append(
                issue(
                    "base_config_changed",
                    f"pack.e1_base.{name}",
                    t(
                        "preflight.base_changed.message",
                        "资料包声明采用的基础样例{what}是 {want}，实际读到 {got}",
                        what=what,
                        want=want,
                        got=got,
                    ),
                    t(
                        "preflight.base_changed.impact",
                        "不能把另一套规则当成声明的基础样例来运行",
                    ),
                    t("preflight.base_changed.next", "核对资料包与 E1 配置，换新的资料包版本"),
                )
            )
    return out


def product_profile(
    pack: DataPack, confirmed: ConfirmedProfile, hard: list[dict[str, Any]]
) -> ProductLifeProfile:
    """确认画像 → 六维产品画像（E1 侧）。精确分数原样带过去；健康回答逐题带题目、状态、选项文字与给分身的读法。"""
    answers = {a.question_id: a for a in confirmed.health.answers}
    health = tuple(
        ProductHealthAnswer(
            question_id=a.question_id,
            question_text=a.question_text,
            answer_state=a.answer_state,
            option=a.option,
            option_label=a.option_label,
            planning_note=health_meaning(
                pack,
                a.question_id,
                None
                if a.answer_state == "unanswered"
                else ("unsure" if a.answer_state == "unsure" else a.option),
            )[1],
        )
        for a in answers.values()
    )
    mapping = confirmed.occupation.internal_mappings[0]
    return ProductLifeProfile.model_validate(
        {
            "profile_version": PRODUCT_PROFILE_VERSION,
            "person_id": confirmed.person_ref,
            "confirmed_profile_fingerprint": confirmed.content_fingerprint,
            "dimension_points": dict(confirmed.six_dimensions.points),
            "material_status": confirmed.material.status,
            "material_points": None
            if confirmed.material.allocation is None
            else dict(confirmed.material.allocation.points),
            "health_questionnaire_version": confirmed.health.questionnaire_version,
            "health_answers": [h.model_dump(mode="json") for h in health],
            "hard_requirements": hard,
            "free_text": confirmed.free_text,
            "occupation": {"scheme": mapping.scheme, "code": mapping.code},
            "leisure_interest_status": confirmed.interests.leisure_status,
            "leisure_interests": list(confirmed.interests.leisure_categories),
            "taste_status": confirmed.interests.taste_status,
            "cuisines": list(confirmed.interests.cuisines),
            "unsupported_requests": [
                *(
                    f"{r.kind}：{r.raw_text}"[:200]
                    for r in confirmed.retained
                    if r.kind in ("brand",)
                ),
                *(
                    f"未认出的兴趣或口味描述：{text}"[:200]
                    for text in confirmed.interests.unrecognized_text
                ),
            ],
        }
    )


def _planner_identity(pack: DataPack) -> dict[str, Any]:
    """资料包声明的离线规划器必须就是已登记的产品 mock（身份逐字段相同，含说明文字）；不认识的身份拒绝编译。"""
    decl = pack.body.run.planner
    ident = PRODUCT_MOCK_IDENTITY
    if (decl.planner_id, decl.version, decl.rules_version) != (
        ident.planner_id,
        ident.version,
        ident.rules_version,
    ):
        raise CompileRefusedError(
            (
                issue(
                    "planner_unknown",
                    "pack.run.planner",
                    t(
                        "preflight.planner_unknown.message",
                        "资料包声明的规划器 {planner} / {rules} 不是已登记的产品 mock",
                        planner=Code(f"{decl.planner_id}"),
                        rules=Code(f"{decl.rules_version}"),
                    ),
                    t("preflight.no_run_config", "不能编译运行配置"),
                    t("preflight.planner_unknown.next", "用 e3b-product-mock-rules-v2"),
                ),
            )
        )
    return ident.model_dump(mode="json", exclude_none=True)


def _execution_problems(
    pack: DataPack, confirmed: ConfirmedProfile, execution: ExecutionSpec
) -> list[ProductIssue]:
    """规模与资料包：确认的城市、每城分身与资料包身份都必须是正式运行设置写的那一份（不删城、不减分身）。"""
    out: list[ProductIssue] = []
    if execution.pack_fingerprint != pack.fingerprint:
        out.append(
            issue(
                "execution_pack_mismatch",
                "pack",
                t("preflight.execution_pack.message", "资料包的内容与正式运行设置记下的不同"),
                t("preflight.no_run_config", "不能编译运行配置"),
                t("preflight.execution_pack.next", "重新做运行前检查"),
            )
        )
    repeats = tuple(execution.repeat_indexes)
    if not repeats or len(set(repeats)) != len(repeats) or min(repeats) < 0:
        out.append(
            issue(
                "run_scale_mismatch",
                "execution.repeat_indexes",
                t(
                    "preflight.repeats_invalid.message",
                    "正式运行设置的分身编号 {repeats} 不合法",
                    repeats=Code(f"{list(repeats)}"),
                ),
                t("preflight.no_run_config", "不能编译运行配置"),
                t("preflight.check_service_config", "维护者核对服务配置"),
            )
        )
    if set(confirmed.cities) != set(execution.cities):
        out.append(
            issue(
                "run_scale_mismatch",
                "cities",
                t("preflight.cities_mismatch.message", "确认的城市与这次服务固定比较的城市不同"),
                t("preflight.cities_mismatch.impact", "不能编译运行配置（不删城、不补城）"),
                t("preflight.cities_mismatch.next", "选择服务规定的全部城市后重新确认"),
            )
        )
    return out


def _apply_years(pack: DataPack, protocol: dict[str, Any], years: int) -> None:
    """这次运行的年数（E3C 收尾）：与协议相同就原样；资料包在 ``run.years_supported`` 里声明过的其他年数，写进这次编译出的协议
    （是另一份协议内容身份）；没有声明的年数拒绝——不为此生成更多年份的菜单。"""
    base = int(protocol.get("simulation_years", 3))
    if years == base:
        return
    supported = pack.body.run.years_supported or (base,)
    if years not in supported:
        raise CompileRefusedError(
            (
                issue(
                    "run_scale_mismatch",
                    "execution.years",
                    t(
                        "preflight.years_unsupported.message",
                        "这次运行设置是 {years} 年，资料包只配套验证了 {supported} 年",
                        years=years,
                        supported=Code(f"{sorted(supported)}"),
                    ),
                    t("preflight.no_run_config", "不能编译运行配置"),
                    t("preflight.years_unsupported.next", "选择资料包支持的年数后重新准备"),
                ),
            )
        )
    protocol["simulation_years"] = years


def _apply_execution(
    run: dict[str, Any],
    execution: ExecutionSpec,
    scripts: tuple[MockScript, ...],
    *,
    computed: int | None,
) -> None:
    """按正式运行设置写运行配置的规划器、调用上限与付费预算。mock 设置写出来与资料包声明的离线 mock 相同。"""
    if computed is not None and execution.global_call_limit != computed:
        raise CompileRefusedError(
            (
                issue(
                    "run_scale_mismatch",
                    "execution.global_call_limit",
                    t(
                        "preflight.call_limit_mismatch.message",
                        "正式运行设置的规划候选上限 {limit} 与编译出的规模 {computed} 不同",
                        limit=execution.global_call_limit,
                        computed=computed,
                    ),
                    t("preflight.no_run_config", "不能编译运行配置"),
                    t("preflight.check_service_config", "维护者核对服务配置"),
                ),
            )
        )
    planning = run["planning"]
    planning["global_call_limit"] = execution.global_call_limit
    planning["transport_retries"] = execution.transport_retries
    if execution.planner_kind == "mock":
        run["planner"] = {
            **execution.run_planner_block(),
            "scripts": [s.model_dump(mode="json", exclude_none=True) for s in scripts],
        }
        return
    if scripts:
        raise CompileRefusedError(
            (
                issue(
                    "scripts_need_mock",
                    "scripts",
                    t("preflight.scripts_need_mock.message", "受控的 mock 行为只能配离线规划器"),
                    t("preflight.no_run_config", "不能编译运行配置"),
                    t("preflight.scripts_need_mock.next", "去掉受控行为"),
                ),
            )
        )
    run["version"] = "e1-7b-run-v1"
    run["planner"] = execution.run_planner_block()
    run["paid_budget"] = execution.paid_budget_block()
    planning["note"] = (
        f"真实模型（{execution.planner_identity['planner_id']}）：每个分身每年一次规划、最多 "
        f"{execution.max_revisions_per_year} 次修订；每份候选最多再试 {execution.transport_retries} 次服务请求；"
        f"规划候选上限 {execution.global_call_limit}、服务尝试上限 {execution.max_requests}"
    )
    run["note"] = (
        f"E3C 托管服务的正式运行：费用上限 {execution.max_cost} {execution.currency}（应用侧按报价 "
        f"{execution.price_version} 估算，不是供应商账单上限）；本次运行自己的新账本在工作目录的 budget/。"
    )


def _relative(p: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return Path(p).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return Path(p).resolve().as_posix()


def _dump(path: Path, header: str, data: dict[str, Any]) -> None:
    text = header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(text, encoding="utf-8", newline="\n")


def _amount(minor: int, digits: int) -> float:
    return float(Decimal(minor).scaleb(-digits))


def compile_inputs(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    out_dir: Path,
    *,
    repo_root: Path | None = None,
    scripts: tuple[MockScript, ...] = (),
    illness_events: dict[str, Any] | None = None,
    run_label: str | None = None,
    execution: ExecutionSpec | None = None,
) -> CompiledInputs:
    """把一份确认画像编译成 E1 输入文件（写进 ``out_dir``）。开局岗位匹配不到等问题收进 ``issues``（预检查据此阻断）；配置不是
    声明的基础样例、确认画像被改写等全局问题直接拒绝（``CompileRefusedError``）。

    ``illness_events`` 只给受控夹具用（例如注入一次大病来演示出局与停止）：它换掉派生协议里的疾病事件来源，因而是另一份协议身份。
    """
    if confirmed.content_fingerprint != confirmed.expected_fingerprint():
        raise CompileRefusedError(
            (
                issue(
                    "confirmation_tampered",
                    "content_fingerprint",
                    t(
                        "preflight.confirmation_tampered.message",
                        "确认画像的内容与它记下的身份不一致",
                    ),
                    t("preflight.confirmation_tampered.impact", "不能编译"),
                    t("preflight.confirmation_tampered.next", "重新确认"),
                ),
            )
        )
    problems = base_problems(pack)
    if execution is not None:
        problems += _execution_problems(pack, confirmed, execution)
    if problems:
        raise CompileRefusedError(tuple(problems))
    b = pack.body
    base = b.e1_base
    life = _load_raw(base.life_input)
    scoring_raw = _load_raw(base.scoring_config)
    issues: list[ProductIssue] = []

    # ---- 协议原样沿用基础样例；健康输入声明写成确认画像用的问卷版本
    protocol = copy.deepcopy(life["protocol"])
    protocol["health_input"]["questionnaire_version"] = confirmed.health.questionnaire_version
    if illness_events is not None:
        protocol["illness_events"] = illness_events
    if execution is not None:
        _apply_years(pack, protocol, execution.years)

    # ---- 城市目录：便利设备与服务换成资料候选编入的那几项，其余原样
    compilation, compiled_by_city = compile_catalog(pack, protocol)
    # 资料包为这座城声明了便利候选，才把基础目录里的便利方案换成编入的候选；真实资料包（DECISIONS/0042）不声明候选，
    # 设备与服务原样来自它的目录
    declared = {c.city_ref for c in b.candidates}
    catalogs = []
    for cat in copy.deepcopy(life["catalogs"]):
        ref = f"{cat['city']['country']}:{cat['city']['scheme']}:{cat['city']['code']}"
        if ref in declared:
            kept = [
                o for o in cat["opportunities"] if not (o.get("resource") or {}).get("substitutes")
            ]
            cat["opportunities"] = kept + compiled_by_city.get(ref, [])
        catalogs.append(cat)
    catalog_by_ref = {
        f"{c['city']['country']}:{c['city']['scheme']}:{c['city']['code']}": c for c in catalogs
    }

    # ---- 硬要求：受支持的选项编成 E1 的结构化条件（单独报告，不拦截）
    options = {h.option_id: h for h in b.hard_requirement_options}
    hard: list[dict[str, Any]] = []
    needs = list(life["consumption_needs"])
    for h in confirmed.hard_requirements:
        opt = options[h.option_id]
        hard.append(
            {
                "need": opt.need,
                "requirement": {"category": "housing", opt.parameter: h.parameter_value},
            }
        )
        if opt.need not in needs:
            needs.append(opt.need)
    # E3-A 收尾 F4：审阅本应已经拦下会违反 E1 画像契约的答案（例如同一需求两条硬要求）；万一漏过，转成结构化拒绝，
    # 原文进编译目录的内部诊断——不让 ValidationError 原样逃出预检查，也不把它当普通资料缺失吞掉（编号可追到原文）
    try:
        profile = product_profile(pack, confirmed, hard)
    except ValidationError as exc:
        raise CompileRefusedError((profile_contract_issue(Path(out_dir), str(exc)),)) from exc

    # ---- 标准开局
    mapping = confirmed.occupation.internal_mappings[0]
    s = b.start_model_settings
    rules_version = protocol["resource_rules"]["version"]
    digits = b.money_basis.minor_unit_digits
    numeraire = confirmed.money.converted.numeraire_id
    real = b.status == "real_data_frozen"
    start_source = {
        "kind": "synthetic",
        "observed_on": b.observed_on.isoformat(),
        "geography_scope": b.catalog_scope.geography_scope,
        "mapping_version": COMPILER,
        "synthetic_note": "E3-A 标准开局的模型设定（每月时间与负担额度）：本版统一的合成设定，不是对用户的测量",
    }
    states: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    start_jobs: dict[str, str] = {}
    start_job_ids: dict[str, str] = {}
    clones: list[str] = []
    occupation = Ent("occupation", confirmed.occupation.concept_id, confirmed.occupation.label)
    for ref in confirmed.cities:
        cat = catalog_by_ref.get(ref)
        if cat is None:
            issues.append(
                issue(
                    "city_not_in_base",
                    "cities",
                    t("preflight.city_not_in_base.message", "城市 {city} 不在基础样例里", city=ref),
                    t("preflight.city_not_in_base.impact", "不能运行这座城"),
                    t("preflight.city_not_in_base.next", "从能力清单选择"),
                )
            )
            continue
        city = Ent("city", ref, cat["label"])
        jobs = [
            o
            for o in cat["opportunities"]
            if o["category"] == "job"
            and o.get("availability") == "available"
            and (o.get("occupation") or {}).get("scheme") == mapping.scheme
            and str((o.get("occupation") or {}).get("code")) == mapping.code
        ]
        usable = [
            o
            for o in jobs
            if all(
                tag.split(":", 1)[1] in confirmed.abilities
                for tag in o.get("eligibility", [])
                if tag.startswith("ability:")
            )
        ]
        if not jobs:
            issues.append(
                issue(
                    "start_job_unmatched",
                    f"cities.{ref}",
                    t(
                        "preflight.start_job_unmatched.message",
                        "{city} 的目录里没有与“{occupation}”同一分类的岗位",
                        city=city,
                        occupation=occupation,
                    ),
                    t(
                        "preflight.start_job_unmatched.impact",
                        "开局岗位无法匹配，这座城不能生成标准开局（不自动换成别的职业）",
                    ),
                    t(
                        "preflight.start_job_unmatched.next",
                        "换一座有对应岗位的城市，或核对职业描述",
                    ),
                )
            )
            continue
        if not usable:
            needed = sorted(
                {
                    tag
                    for o in jobs
                    for tag in o.get("eligibility", [])
                    if tag.startswith("ability:")
                }
            )
            issues.append(
                issue(
                    "start_job_requires_unconfirmed_ability",
                    f"cities.{ref}",
                    t(
                        "preflight.start_job_ability.message",
                        "{city} 与你职业对应的岗位要求 {needed}，你还没有确认持有这项资格",
                        city=city,
                        needed=Code(f"{needed}"),
                    ),
                    t(
                        "preflight.start_job_ability.impact",
                        "系统不从职业称呼自动授予资格，这座城不能生成开局",
                    ),
                    t(
                        "preflight.start_job_ability.next",
                        "如果你确实持有，请在资格一栏明确确认",
                    ),
                )
            )
            continue
        # 开局岗位：同一分类、资格已确认的岗位里取中位工资档（DECISIONS/0042；真实资料包的岗位写着 wage_position）；
        # 没有工资档的（合成样例）按编号取第一个
        job = sorted(
            usable,
            key=lambda o: (
                (o.get("spec") or {}).get("wage_position") != "p50",
                o["opportunity_id"],
            ),
        )[0]
        envs = max((m["env_repeat"] for m in cat.get("menus", [])), default=0)
        start_jobs[ref] = job["label"]
        start_job_ids[ref] = job["opportunity_id"]
        # 分身编号：正式运行设置写的（E3C 收尾：每城分身数是这次运行的设置，不改资料包的内容身份）；没有就用资料包的运行声明
        for k in execution.repeat_indexes if execution is not None else b.run.repeat_indexes:
            clone = f"{confirmed.person_ref}.{cat['city']['code'].lower()}.r{k}"
            clones.append(clone)
            states.append(
                {
                    "person_id": confirmed.person_ref,
                    "clone_id": clone,
                    "city": dict(cat["city"]),
                    "year_index": 1,
                    "occupation": {"scheme": mapping.scheme, "code": mapping.code},
                    "money_basis_id": numeraire,
                    "cash": {
                        "amount": _amount(confirmed.money.converted.amount_minor, digits),
                        "numeraire": numeraire,
                    },
                    "time_budget_hours": s.time_budget_hours_per_year,
                    "energy": {"level": s.energy_quota, "scale_version": s.energy_scale_version},
                    "resource_budget_basis": {
                        "rules_version": rules_version,
                        "reserved": s.reserved_text,
                        "source": start_source,
                    },
                    "energy_quota_basis": {
                        "base_level": s.energy_quota,
                        "status": "base_first_year",
                    },
                    "abilities": [
                        {"ability_id": a, "source": "initial", "usable_from_year": 1}
                        for a in confirmed.abilities
                    ],
                    "employment": {
                        "stated_occupation": {"scheme": mapping.scheme, "code": mapping.code},
                        "as_of": "start_declaration",
                        "positions": [
                            {
                                "opportunity_id": job["opportunity_id"],
                                "occupation": dict(job["occupation"]),
                                **(
                                    {"employer_id": job["employer_id"]}
                                    if job.get("employer_id")
                                    else {}
                                ),
                            }
                        ],
                        "current_position_ids": [job["opportunity_id"]],
                    },
                    "savings_goals": [],
                    "health": {"age_years": confirmed.age_years, "repeat_index": k},
                    # 年度菜单的环境重复（DECISIONS/0042）：重复序号轮流分到各个环境重复，同编号的分身看同一份菜单
                    **({"env_repeat": k % envs + 1} if envs else {}),
                    "goals": [],
                    "experience_summary": [],
                }
            )
            entries.append(
                {
                    "user_id": f"user.{confirmed.person_ref}",
                    "clone_id": clone,
                    "city": dict(cat["city"]),
                    "repeat_index": k,
                }
            )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "life_input": out_dir / "life_input.yaml",
        "scoring_config": out_dir / "scoring.yaml",
        "roster": out_dir / "roster.yaml",
        "run_config": out_dir / "run.yaml",
    }
    rel = {k: _relative(p, repo_root) for k, p in paths.items()}
    notice = (
        "exploratory_synthetic：E3B 编译产物。城市资料来自公开来源与登记的模型设定，生活规则与评分是合成探索参数；"
        "六维产品画像是用户确认的陈述，不是心理真值；不排序真实城市。"
        if real
        else "exploratory_synthetic：E3-A 编译产物。合成城市、合成参数、六维产品画像（用户确认的陈述，不是心理真值）；"
        "不是现实城市评价，不排序真实城市。"
    )
    bundle = {
        "fixture_notice": ("real_data_frozen：" if real else "synthetic_fixture_only：") + notice,
        "protocol": protocol,
        "consumption_needs": needs,
        "categories": life["categories"],
        "functions": life.get("functions", []),
        "attribute_keys": life.get("attribute_keys", []),
        "function_kinds": life.get("function_kinds", {}),
        # 生活扩展（DECISIONS/0041）的公开词表原样沿用基础样例；确认的兴趣与菜系必须在这里面（加载器核对）
        "cuisines": life.get("cuisines", []),
        "leisure_categories": life.get("leisure_categories", []),
        "capabilities": life.get("capabilities", []),
        "enjoyment_functions": life.get("enjoyment_functions", []),
        "tax_policies": life["tax_policies"],
        "catalogs": catalogs,
        "product_profiles": [profile.model_dump(mode="json")],
        "initial_states": states,
    }
    header = (
        f"# E3-A 编译产物（{COMPILER}；DECISIONS/0034）。由确认画像 {confirmed.confirmed_id} 与资料包 {b.pack_id} 生成，不要手改：\n"
        f"# 改答案或资料要重新确认、重新编译（内容身份会变）。生活规则、评分与评价沿用基础样例（{base.protocol_id}）。\n"
    )
    _dump(paths["life_input"], header, bundle)
    scoring = copy.deepcopy(scoring_raw)
    scoring["scoring_notice"] = (
        f"exploratory_synthetic：基础样例的评分规格（协议 {base.protocol_id}），原样沿用。全部是合成探索参数。"
    )
    _dump(paths["scoring_config"], header, scoring)
    roster = {
        "roster_notice": notice,
        "roster": {
            "roster_id": f"roster.e3a.{confirmed.person_ref}",
            "status": "exploratory_synthetic",
            "aggregation_version": "e1-6b-aggregation-v1",
            "life_input": rel["life_input"],
            "scoring_config": rel["scoring_config"],
            "comparison_group_id": scoring_raw["comparison_group"]["group_id"],
            "note": (run_label or "E3-A 离线运行：同一位用户，每座候选城一个分身，固定生活三年")[
                :800
            ],
            "users": [
                {
                    "user_id": f"user.{confirmed.person_ref}",
                    "person_id": confirmed.person_ref,
                    "label": f"{confirmed.person_ref}（{confirmed.occupation.label}）",
                }
            ],
            "arms": [
                {
                    "arm_id": "arm.main",
                    "role": "main",
                    "evaluation_config": base.evaluation_config,
                    "note": "沿用基础样例的评价规格（主设定 δ = 1、六个基础生活模板）",
                }
            ],
            "entries": entries,
        },
    }
    _dump(paths["roster"], header, roster)
    years = int(protocol.get("simulation_years", 3))
    attempts = 1 + b.run.max_revisions_per_year
    run = {
        "run_notice": notice,
        "run": {
            "run_config_id": f"run.e3a.{confirmed.person_ref}",
            "status": "exploratory_synthetic",
            "version": "e1-7a-run-v1",
            "roster": rel["roster"],
            "city_facts": base.city_facts,
            "information": {
                "condition": "product_stated_profile",
                "show_stated_dimension_points": True,
                "show_hard_requirements": True,
                "show_free_text": True,
                "show_energy_level": True,
                "show_stated_material_points": True,
                "permission_version": b.run.permission_version,
                "show_health_answers": b.run.show_health_answers,
                "show_interests": True,
            },
            "planning": {
                "plans_per_year": 1,
                "max_revisions_per_year": b.run.max_revisions_per_year,
                "global_call_limit": max(1, len(entries) * years * attempts),
                "run_order": "roster_order",
                "transport_retries": 0,
                "note": "离线 mock：没有网络、不重试；调用上限 = 分身数 × 年数 × 每年候选上限",
            },
            "planner": {
                "identity": _planner_identity(pack),
                "scripts": [s.model_dump(mode="json", exclude_none=True) for s in scripts],
            },
            "prompt_template": b.run.prompt_template,
            "memory_policy": "e1-life-memory-v2",
            "note": "E3-A 离线闭环：新增真实模型调用 0 次、费用 0。",
        },
    }
    if execution is not None:
        # 有阻断问题（某座城不能开局）时预检查本来就不通过，规模不另比，不让它盖住具体原因
        blocked = any(i.severity == "error" for i in issues)
        computed = None if blocked else len(entries) * years * attempts
        _apply_execution(run["run"], execution, scripts, computed=computed)
    _dump(paths["run_config"], header, run)
    return CompiledInputs(
        out_dir=out_dir,
        paths=paths,
        profile=profile,
        catalog=compilation,
        start_jobs=start_jobs,
        clones=tuple(clones),
        issues=tuple(issues),
        start_job_ids=start_job_ids,
    )
