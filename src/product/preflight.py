"""产品预检查（``ProductPreflight``，E3-A）：编译 → E1 加载、名单冻结与运行前检查 → 覆盖与资料缺口 → 运行绑定。

“可执行 / 可评分 / 可反馈”只描述**当前输入与声明的支持范围**：通过预检查不保证未来每份计划都合法、每个月都能完整评分，年度计划
仍由 E1 逐次检查，真实发生的出局与模拟问题照实分类。运行前检查不只校验前端字段：名单、标准开局与跨对象检查全部复用 E1 的入口
（``orchestration.life_runner.preflight``）。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.life_contracts import canonical_fingerprint, to_fraction
from common.life_planning_contracts import MockScript
from common.product_contracts import (
    CapabilityStatus,
    CityCoverageSummary,
    CityPreflight,
    ConfirmedProfile,
    ConfirmedRef,
    ExactNumber,
    OptionInventoryCoverage,
    PlannerAndCost,
    ProductIssue,
    ProductPreflight,
    RunBinding,
    SampleLabel,
    StartSummary,
    UserInputDraft,
    content_fingerprint,
)
from econ.life_coverage import coverage_report
from econ.life_scoring import preference_from_confirmed_profile
from orchestration.life_runner import RunRefusedError, RunWorld, preflight
from product.answers import confirmation_problems, issue
from product.catalog import inventory_coverage
from product.compile import CompiledInputs, CompileRefusedError, compile_inputs
from product.exact import canonical, display
from product.pack import DataPack
from product.pack_facts import city_data_nature, commute_gaps, scope_notes
from product.public_errors import e1_refusal_issue


@dataclass
class PreflightRun:
    """一次预检查的全部产物：产品消息，以及（通过时）编译产物、E1 运行世界与选项目录覆盖。"""

    preflight: ProductPreflight
    compiled: CompiledInputs | None = None
    world: RunWorld | None = None
    inventory: OptionInventoryCoverage | None = None
    e1_refusal: str | None = None


def _blocked(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    issues: list[ProductIssue],
    sample: SampleLabel | None,
    years: int = 3,
    compiled: CompiledInputs | None = None,
    inventory: OptionInventoryCoverage | None = None,
    e1_refusal: str | None = None,
) -> PreflightRun:
    no = CapabilityStatus(status="no", reasons=tuple(i.message_zh for i in issues[:4]))
    fields: dict[str, Any] = dict(
        meta=pack.meta(sample),
        preflight_id=f"preflight.{confirmed.confirmed_id}",
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id, content_fingerprint=confirmed.content_fingerprint
        ),
        status="blocked",
        executable=no,
        scorable=CapabilityStatus(
            status="unknown", reasons=("运行前检查没有通过，没有评估评分能力",)
        ),
        feedback=CapabilityStatus(status="unknown", reasons=("运行前检查没有通过",)),
        clones_per_city=0,
        years=years,
        planner=_planner(pack, 0),
        issues=tuple(issues),
        scope_zh=pack.body.limits,
        scope_notes=scope_notes(pack),
    )
    unsealed = ProductPreflight.model_construct(**fields, preflight_fingerprint="0" * 64)
    pre = ProductPreflight(**fields, preflight_fingerprint=content_fingerprint(unsealed.content()))
    return PreflightRun(pre, compiled=compiled, inventory=inventory, e1_refusal=e1_refusal)


def _planner(pack: DataPack, calls: int) -> PlannerAndCost:
    p = pack.body.run.planner
    return PlannerAndCost(
        planner_kind="mock",
        planner_id=p.planner_id,
        description_zh="可控的确定性离线规划器（mock）：只检验接线、状态传递与信息隔离，不代表真实大语言模型的能力；没有网络调用",
        call_limit=calls,
        paid=False,
        max_cost=None,
    )


def run_preflight(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    out_dir: Path,
    *,
    draft: UserInputDraft | None = None,
    repo_root: Path | None = None,
    scripts: tuple[MockScript, ...] = (),
    illness_events: dict[str, Any] | None = None,
    sample: SampleLabel | None = None,
    run_label: str | None = None,
) -> PreflightRun:
    """确认画像 → 编译 → E1 运行前检查 → 产品预检查。任何一步不通过都返回 ``blocked``（带原因），不偷偷删城市或分身。"""
    problems = confirmation_problems(pack, confirmed, draft)
    if problems:
        return _blocked(pack, confirmed, problems, sample)
    try:
        compiled = compile_inputs(
            pack,
            confirmed,
            out_dir,
            repo_root=repo_root,
            scripts=scripts,
            illness_events=illness_events,
            run_label=run_label,
        )
    except CompileRefusedError as exc:
        return _blocked(pack, confirmed, list(exc.issues), sample)
    inventory = inventory_coverage(pack, compiled.catalog, sample=sample)
    if any(i.severity == "error" for i in compiled.issues):
        return _blocked(
            pack, confirmed, list(compiled.issues), sample, compiled=compiled, inventory=inventory
        )
    cwd = contextlib.chdir(repo_root) if repo_root is not None else contextlib.nullcontext()
    try:
        with cwd:
            world = preflight(compiled.paths["run_config"])
    except RunRefusedError as exc:
        message = str(exc)
        return _blocked(
            pack,
            confirmed,
            # E3-A 收尾 F5：E1 的拒绝原文只进编译目录的内部诊断，公开的是类别说明与审计编号（不靠正则删路径）
            [e1_refusal_issue(compiled.out_dir, message)],
            sample,
            compiled=compiled,
            inventory=inventory,
            e1_refusal=message,
        )
    protocol = world.bundle.protocol
    years = protocol.simulation_years
    pref = preference_from_confirmed_profile(compiled.profile)
    group = world.scoring.comparison_group
    assert group is not None and not isinstance(pref, type(None))
    report = coverage_report(
        spec=world.scoring.spec,
        group=group,
        conditions=world.scoring.city_conditions,
        catalogs=world.bundle.catalogs,
        preference=pref,  # type: ignore[arg-type]
    )
    digits = pack.body.points.display_decimals + 2
    effective = protocol.energy_feedback.effective
    inv = {c.city_ref: c for c in inventory.cities}
    names = {d.key: d.label for d in pack.body.dimensions} | {
        m.key: m.label for m in pack.body.material_leaves
    }
    cities: list[CityPreflight] = []
    for ref in confirmed.cities:
        cat = world.bundle.catalog_for(ref)
        assert cat is not None
        gaps: list[ProductIssue] = []
        for leaf in report.leaves:
            if leaf.weight <= 0:
                continue
            name = names.get(leaf.leaf, leaf.leaf)
            if leaf.support == "no_component":
                gaps.append(
                    issue(
                        "positive_weight_no_component",
                        f"preferences.{leaf.leaf}",
                        f"你给“{name}”的权重为正，但本版评分没有这一项",
                        "这座城每个月的总分都会不完整（不补 0、不把它删掉再重新归一化）",
                        "知悉这一限制，或调整分配",
                    )
                )
            elif ref in leaf.cities_unknown:
                gaps.append(
                    issue(
                        "positive_weight_data_unknown",
                        f"preferences.{leaf.leaf}",
                        f"{cat.label} 缺少“{name}”评分要读的资料（你给它的权重为正）",
                        "这座城相关月份的总分会不完整：不补 0，也不把这一项删掉再把其余权重重新归一化",
                        "等资料补齐，或知悉这一限制",
                    )
                )
        for t in cat.treatments:
            if t.availability == "unknown":
                gaps.append(
                    issue(
                        "treatment_data_unknown",
                        f"cities.{ref}.treatments",
                        f"{cat.label} 的{t.label}资料未知（不是免费，也不是没有）",
                        "分身计划要治而遇到资料未知时，那个分身会停在那个月并记为资料缺口",
                        "知悉这一限制",
                        severity="warning",
                    )
                )
        # DECISIONS/0042：通勤关系表里缺、比较组别的城市有的方式（不当 0 分钟、不当免费）
        gaps += commute_gaps(world, ref)
        scor = (
            CapabilityStatus(
                status="yes",
                reasons=(
                    "按当前输入，正权重的评分资料在声明范围内齐全；不保证未来每个月都能完整评分",
                ),
            )
            if not any(g.code.startswith("positive_weight") for g in gaps)
            else CapabilityStatus(status="partial", reasons=tuple(g.message_zh for g in gaps)[:4])
        )
        feed = (
            CapabilityStatus(
                status=scor.status, reasons=("上一年 12 个月都完整评分时才更新下一年的精力额度",)
            )
            if effective
            else CapabilityStatus(status="no", reasons=("反馈政策关闭或 α = 0：额度固定为基础值",))
        )
        # E1 的覆盖报告是比较组层面的：共同覆盖与“没有分项”各城相同；“资料未知”要按这座城算（别的城缺资料不算这座城的缺口）
        unknown_here = [x for x in report.leaves if x.weight > 0 and ref in x.cities_unknown]
        unknown_leaves = [
            x.leaf
            for x in report.leaves
            if x.weight > 0 and (x.support == "no_component" or ref in x.cities_unknown)
        ]
        cities.append(
            CityPreflight(
                city_ref=ref,
                label=cat.label,
                start_job_label=compiled.start_jobs.get(ref),
                executable=CapabilityStatus(
                    status="yes",
                    reasons=(
                        "标准开局通过、名单已冻结；年度计划仍由环境逐次检查，不保证每份计划都合法",
                    ),
                ),
                scorable=scor,
                feedback=feed,
                coverage=CityCoverageSummary(
                    common_weight=_num(report.common_weight, digits),
                    unknown_weight=_num(sum((x.weight for x in unknown_here), 0.0), digits),
                    no_component_weight=_num(report.no_component_weight, digits),
                    unsupported_positive_leaves=tuple(unknown_leaves),
                    label=report.coverage_label,
                ),
                option_inventory_status=inv[ref].status if ref in inv else "not_assessed",
                data_gaps=tuple(gaps),
                data_nature=city_data_nature(pack, world, ref),
            )
        )
    s = pack.body.start_model_settings
    overall_scor = "yes" if all(c.scorable.status == "yes" for c in cities) else "partial"
    calls = world.config.planning.global_call_limit
    binding = RunBinding(
        protocol_id=protocol.protocol_id,
        scoring_version=world.scoring.spec.scoring_version,
        scoring_content_fingerprint=world.scoring.spec.content_fingerprint,
        evaluation_spec_id=next(iter(world.specs.values())).spec_id,
        prompt_template=world.config.prompt_template,
        planner_kind=world.config.planner.identity.kind,
        planner_id=world.config.planner.identity.planner_id,
        compiled_inputs=compiled.file_fingerprints(),
        identities_fingerprint=canonical_fingerprint(world.identities),
    )
    fields: dict[str, Any] = dict(
        meta=pack.meta(sample),
        preflight_id=f"preflight.{confirmed.confirmed_id}",
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id, content_fingerprint=confirmed.content_fingerprint
        ),
        status="ready",
        start=StartSummary(
            rule="v1_standard_start_2026_10_12",
            age_years=confirmed.age_years,
            cash=confirmed.money.converted,
            abilities=confirmed.abilities,
            occupation_label=confirmed.occupation.label,
            monthly_time_note=s.readable_time_note,
            monthly_energy_note=s.readable_energy_note,
            scenario_zh=pack.body.start_scenario,
        ),
        cities=tuple(cities),
        executable=CapabilityStatus(
            status="yes", reasons=("全部候选城市的标准开局与名单检查通过",)
        ),
        scorable=CapabilityStatus(
            status=overall_scor,  # type: ignore[arg-type]
            reasons=("逐城说明见 cities；不保证未来每个月都能完整评分",),
        ),
        feedback=CapabilityStatus(
            status=overall_scor if effective else "no",  # type: ignore[arg-type]
            reasons=("上一年完整评分才更新下一年额度；不完整时这个分身的下一年会受阻",),
        ),
        clones_per_city=len(pack.body.run.repeat_indexes),
        years=years,
        planner=_planner(pack, calls),
        binding=binding,
        issues=tuple(compiled.issues) + tuple(g for c in cities for g in c.data_gaps),
        # 收尾 R7：这次按什么个性化、哪些没有（未答、暂不确定、没有固定爱好、通用评价分开写）
        scope_zh=tuple(world.scope["数据与参数"]) + pack.body.limits + confirmed.interests.scope_zh,
        scope_notes=scope_notes(pack, world, confirmed.cities),
    )
    unsealed = ProductPreflight.model_construct(**fields, preflight_fingerprint="0" * 64)
    pre = ProductPreflight(**fields, preflight_fingerprint=content_fingerprint(unsealed.content()))
    return PreflightRun(pre, compiled=compiled, world=world, inventory=inventory)


def _num(x: float, digits: int) -> ExactNumber:
    """覆盖报告里的浮点权重：按它的十进制写法精确换成分数（与 E1 的 ``to_fraction`` 同一口径），显示另行舍入。"""
    q = to_fraction(x)
    return ExactNumber(exact=canonical(q), display=display(q, digits))


def preflight_problems(
    pack: DataPack, confirmed: ConfirmedProfile, pre: ProductPreflight
) -> list[ProductIssue]:
    """一份预检查现在还能不能用来提交：内容身份、引用的确认画像与状态。"""
    out: list[ProductIssue] = []
    if pre.preflight_fingerprint != pre.expected_fingerprint():
        out.append(
            issue(
                "preflight_tampered",
                "preflight_fingerprint",
                "预检查的内容与它记下的身份不一致",
                "不能提交",
                "重新预检查",
            )
        )
    if (pre.confirmed.confirmed_id, pre.confirmed.content_fingerprint) != (
        confirmed.confirmed_id,
        confirmed.content_fingerprint,
    ):
        out.append(
            issue(
                "preflight_stale",
                "confirmed",
                "预检查对应的不是这份确认画像（答案改过、重新确认过，或引用错了）",
                "旧预检查已经失效",
                "用当前确认画像重新预检查",
            )
        )
    if pre.status != "ready":
        out.append(
            issue(
                "preflight_blocked",
                "status",
                "预检查没有通过",
                "不能提交运行",
                "先解决预检查列出的问题",
            )
        )
    if pre.meta.data_pack.content_fingerprint != pack.fingerprint:
        out.append(
            issue(
                "data_pack_changed",
                "meta.data_pack",
                "预检查之后资料包变了",
                "旧预检查已经失效",
                "重新预检查",
            )
        )
    return out
