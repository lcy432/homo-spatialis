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
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from product.pack import DataPack
from product.public_errors import profile_contract_issue

COMPILER = "e3a-compile-v1"


class CompileRefusedError(ValueError):
    def __init__(self, issues: tuple[ProductIssue, ...]) -> None:
        super().__init__("；".join(i.message_zh for i in issues))
        self.issues = issues


@dataclass
class CompiledInputs:
    """一次编译的产物：写好的 E1 输入文件（用途 → 路径）、画像、目录编译与开局岗位。"""

    out_dir: Path
    paths: dict[str, Path]
    profile: ProductLifeProfile
    catalog: CatalogCompilation
    start_jobs: dict[str, str]
    clones: tuple[str, ...]
    issues: tuple[ProductIssue, ...] = field(default_factory=tuple)

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
    for name, got, want in (
        ("协议", bundle.protocol.protocol_id, base.protocol_id),
        ("评分版本", scoring.spec.scoring_version, base.scoring_version),
        ("评分内容身份", scoring.spec.content_fingerprint, base.scoring_content_fingerprint),
    ):
        if got != want:
            out.append(
                issue(
                    "base_config_changed",
                    f"pack.e1_base.{name}",
                    f"资料包声明采用的基础样例{name}是 {want}，实际读到 {got}",
                    "不能把另一套规则当成声明的基础样例来运行",
                    "核对资料包与 E1 配置，换新的资料包版本",
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
                    f"资料包声明的规划器 {decl.planner_id} / {decl.rules_version} 不是已登记的产品 mock",
                    "不能编译运行配置",
                    "用 e3a-product-mock-rules-v1",
                ),
            )
        )
    return ident.model_dump(mode="json", exclude_none=True)


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
                    "确认画像的内容与它记下的身份不一致",
                    "不能编译",
                    "重新确认",
                ),
            )
        )
    problems = base_problems(pack)
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

    # ---- 城市目录：便利设备与服务换成资料候选编入的那几项，其余原样
    compilation, compiled_by_city = compile_catalog(pack, protocol)
    catalogs = []
    for cat in copy.deepcopy(life["catalogs"]):
        ref = f"{cat['city']['country']}:{cat['city']['scheme']}:{cat['city']['code']}"
        kept = [o for o in cat["opportunities"] if not (o.get("resource") or {}).get("substitutes")]
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
    start_source = {
        "kind": "synthetic",
        "observed_on": b.observed_on.isoformat(),
        "geography_scope": "synthetic city (no real geography)",
        "mapping_version": COMPILER,
        "synthetic_note": "E3-A 标准开局的模型设定（每月时间与负担额度）：本版统一的合成设定，不是对用户的测量",
    }
    states: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    start_jobs: dict[str, str] = {}
    clones: list[str] = []
    for ref in confirmed.cities:
        cat = catalog_by_ref.get(ref)
        if cat is None:
            issues.append(
                issue(
                    "city_not_in_base",
                    "cities",
                    f"城市 {ref} 不在基础样例里",
                    "不能运行这座城",
                    "从能力清单选择",
                )
            )
            continue
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
                    f"{cat['label']} 的目录里没有与“{confirmed.occupation.label}”同一分类的岗位",
                    "开局岗位无法匹配，这座城不能生成标准开局（不自动换成别的职业）",
                    "换一座有对应岗位的城市，或核对职业描述",
                )
            )
            continue
        if not usable:
            needed = sorted(
                {t for o in jobs for t in o.get("eligibility", []) if t.startswith("ability:")}
            )
            issues.append(
                issue(
                    "start_job_requires_unconfirmed_ability",
                    f"cities.{ref}",
                    f"{cat['label']} 与你职业对应的岗位要求 {needed}，你还没有确认持有这项资格",
                    "系统不从职业称呼自动授予资格，这座城不能生成开局",
                    "如果你确实持有，请在资格一栏明确确认",
                )
            )
            continue
        job = sorted(usable, key=lambda o: o["opportunity_id"])[0]
        start_jobs[ref] = job["label"]
        for k in b.run.repeat_indexes:
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
        "exploratory_synthetic：E3-A 编译产物。合成城市、合成参数、六维产品画像（用户确认的陈述，不是心理真值）；"
        "不是现实城市评价，不排序真实城市。"
    )
    bundle = {
        "fixture_notice": "synthetic_fixture_only：" + notice,
        "protocol": protocol,
        "consumption_needs": needs,
        "categories": life["categories"],
        "functions": life.get("functions", []),
        "function_kinds": life.get("function_kinds", {}),
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
            "memory_policy": "e1-7a-memory-v1",
            "note": "E3-A 离线闭环：新增真实模型调用 0 次、费用 0。",
        },
    }
    _dump(paths["run_config"], header, run)
    return CompiledInputs(
        out_dir=out_dir,
        paths=paths,
        profile=profile,
        catalog=compilation,
        start_jobs=start_jobs,
        clones=tuple(clones),
        issues=tuple(issues),
    )
