"""资料包的公开事实（DECISIONS/0042）：逐城的资料性质与预检查的范围说明。预检查与产品结果共用这里的投影。

* **资料性质**（``CityDataNature``）：目录各类机会按来源性质（观测 / 派生 / 估计 / 合成）的条数、登记的模型设定、比较组的共同先验、
  治疗的共同假设、模型活动设定、片区、通勤方式、菜单与税的范围，以及冻结资料包构建报告（``report.json``，与生活输入放在同一个
  目录）的计数。全部取自 E1 加载过的目录、城市事实表与构建报告——不写死数字；构建报告读不到或与生活输入对不上时计数是未知。
* **范围说明**（``ScopeNote``）：真实资料包才有——支持的职业、税的范围、通勤怎么算与缺的方式、年度菜单的含义、本版不支持的内容。

只投影公开的来源性质与说明：不读评分参数（η、τ、c、背景量）、抽签、研究真值或分身的内部状态。

显示附层（``e3-display-v1``）：这里生成的说明都经过 ``t()``；资料里的文字（城市、机会、税规则、通勤条件、登记的模型设定……）按
稳定编号绑定成实体（``label(kind, 编号, 原文)``），中文照原文，英文在 ``configs/product/display/`` 里按资料包查。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from common.life_contracts import COMMUTE_MODE_LABEL, COMMUTE_MODES, RealTaxPolicy
from common.product_contracts import (
    BuildCount,
    CategorySources,
    CityDataNature,
    CommonPrior,
    CommuteModeAvailability,
    MenuSummary,
    NamedAssumption,
    ProductIssue,
    ScopeNote,
    SourceKindCount,
    TaxScope,
)
from orchestration.life_runner import RunWorld
from product.answers import issue
from product.display import Code, Ent, Join, Num, Raw, Sub, label, say, t
from product.display.text import has_cjk
from product.pack import DataPack

SOURCE_KINDS = ("observed", "derived", "estimated", "synthetic")
TAX_STATUS_ZH = {
    "official_2024_rules": "官方 2024 年规则（单身、只有工资收入、标准扣除）",
    "synthetic_test_double": "合成测试替身（不是真实税）",
}
CATEGORY_ZH = {"housing": "住房", "job": "岗位"}
#: 构建报告（``e3b-life-pack-report-v1``）里计数的中文名；报告里新出现的键原样用键名，不丢弃。
BUILD_LABEL_ZH = {
    "areas": "片区（居住与就业）",
    "commute_legs": "通勤关系表的线（住处片区 × 就业片区 × 方式）",
    "housing_candidates": "完整可执行住房池的候选（目标格 × 经验支持点）",
    "housing_in_catalog": "放进目录的住房（菜单上出现过的，加最低住房与参考住房）",
    "job_candidates": "完整可执行岗位池的候选（职业 × 年薪支持点 × 工时与就业片区）",
    "jobs_in_catalog": "放进目录的岗位（菜单上出现过的）",
    "start_jobs": "各职业的开局岗位",
    "menus": "年度菜单",
    "items_by_domain.activity": "生活物品候选：活动",
    "items_by_domain.device": "生活物品候选：省家务设备",
    "items_by_domain.enjoyment": "生活物品候选：享受型用品与服务",
    "items_by_domain.equipment": "生活物品候选：爱好装备",
    "items_by_domain.food": "生活物品候选：食品方案",
    "items_by_domain.job": "生活物品候选：岗位",
    "items_by_domain.service": "生活物品候选：服务",
    "items_by_domain.training": "生活物品候选：培训",
    "excluded.housing.outside_tax_scope": "没有进住房池的目标格：在税务支持范围之外",
    "excluded.housing.no_commute_area": "没有进住房池的目标格：通勤资料里没有居住片区",
    "excluded.job.employment_unknown": "没有进岗位池的职业：就业人数未知",
    "excluded.job.no_start_job": "没有开局岗位的职业",
}


def _counts(counter: Counter[str]) -> tuple[SourceKindCount, ...]:
    return tuple(SourceKindCount(kind=k, count=counter.get(k, 0)) for k in SOURCE_KINDS)  # type: ignore[arg-type]


def city_ent(city: str, zh: str) -> Ent:
    return Ent("city", city, zh)


def plain(value: str) -> Code | Raw:
    """资料里的一段原样文字（代码、单位、编号……）：不含中文就是各语言相同的代码，含中文就是没有译文的原文片段。"""
    return Raw(value) if has_cjk(value) else Code(value)


def pack_lines(section: str, lines: tuple[str, ...]) -> tuple[str, ...]:
    """资料包里的一组固定说明（``limits`` / ``start_scenario`` / ``unsupported``）：与能力清单同一份实体，按
    ``capability/<节>/item.<序号>`` 绑定（资料包各有各的资源）。"""
    return tuple(label("capability", section, line, f"item.{i}") for i, line in enumerate(lines))


def tax_status_label(status: str) -> str:
    """税的状态说明（资料性质与结果共用同一份中文；按 ``tax_status`` 实体绑定）。"""
    return label("tax_status", status, TAX_STATUS_ZH[status])


def tax_scope(world: RunWorld, city: str) -> TaxScope | None:
    cat = world.bundle.catalog_for(city)
    policy = None if cat is None else world.bundle.tax_policy(cat.tax_policy_id)
    if policy is None:
        return None
    if isinstance(policy, RealTaxPolicy):
        pid = policy.policy_id
        return TaxScope(
            policy_id=pid,
            status=policy.status,
            status_zh=tax_status_label(policy.status),
            residence_scope_zh=label("tax_policy", pid, policy.residence_scope, "residence_scope"),
            rules_zh=tuple(label("tax_rule", r.rule_id, r.label) for r in policy.rules),
            absent_zh=tuple(
                label("tax_policy", pid, a.label, f"absent.{i}")
                for i, a in enumerate(policy.absent_taxes)
            ),
            not_modeled_zh=tuple(
                label("tax_policy", pid, line, f"not_modeled.{i}")
                for i, line in enumerate(policy.not_modeled)
            ),
        )
    return TaxScope(
        policy_id=policy.policy_id,
        status=policy.status,
        status_zh=tax_status_label(policy.status),
    )


def commute_modes(world: RunWorld, city: str) -> tuple[CommuteModeAvailability, ...]:
    cat = world.bundle.catalog_for(city)
    rel = None if cat is None else cat.commute
    if rel is None:
        return ()
    out = []
    for mode in COMMUTE_MODES:
        rule = rel.mode_rule(mode)
        out.append(
            CommuteModeAvailability(
                mode=mode,  # type: ignore[arg-type]
                mode_zh=label("commute_mode", mode, COMMUTE_MODE_LABEL[mode]),
                in_relation=rule is not None,
                lines=sum(1 for g in rel.legs if g.mode == mode),
                condition_zh=None
                if rule is None
                else label("commute_rule", f"{city}.{mode}", rule.note),
            )
        )
    return tuple(out)


def _common_priors(world: RunWorld, city: str) -> tuple[CommonPrior, ...]:
    """城市事实表里比较组共用的合成设定：来源是合成，而且比较组每座城的这条事实都相同（没有可比资料时登记的共同先验）。"""
    sheet = world.facts.sheet_for(city)
    if sheet is None:
        return ()
    out = []
    for f in sheet.facts:
        if f.source.kind != "synthetic":
            continue
        same = [s.fact(f.fact_id) for s in world.facts.sheets]
        if all(
            x is not None and (x.status, x.number, x.category) == (f.status, f.number, f.category)
            for x in same
        ):
            out.append(
                CommonPrior(
                    fact_id=f.fact_id,
                    label=label("common_prior", f.fact_id, f.label),
                    meaning_zh=label("common_prior", f.fact_id, f.meaning, "meaning"),
                )
            )
    return tuple(out)


def _pack_report(pack: DataPack) -> dict[str, Any] | None:
    """冻结资料包的构建报告；缺失或记下的生活输入身份与这份资料包的不同时为 None（未知）。"""
    life = Path(pack.body.e1_base.life_input)
    path = life.parent / "report.json"
    if not path.is_file() or not life.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    if (report.get("files") or {}).get(life.name) != hashlib.sha256(life.read_bytes()).hexdigest():
        return None
    return report


def safety_notes(pack: DataPack, world: RunWorld, cities: tuple[str, ...]) -> list[ScopeNote]:
    """治安背景的范围说明（DECISIONS/0042 第 9 条）：一条总的说明，加上每座城用了什么资料、覆盖到哪里、与模拟居住范围的关系
    （来自资料包构建报告）。只有资料与覆盖，没有换算参数；报告读不到时只写总的说明。"""
    out = [
        ScopeNote(
            topic="data",
            title_zh=t("datanature.scope.safety.title", "治安背景怎么来"),
            text_zh=t(
                "datanature.scope.safety.text",
                "每个都会区一个治安背景（城内所有居住片区相同），由这个都会区 2024 年暴力犯罪与财产犯罪的报案人口发生率得到，"
                "犯罪率越高背景越低。它是统计代理，不是个人遭遇犯罪或伤亡的概率；不随住处片区或交通方式变化，模拟里也不抽犯罪"
                "事件。休闲背景是六城共同的登记设定（住在城里就有的少量基本条件），主要的休闲来自实际参加的活动",
            ),
        )
    ]
    report = _pack_report(pack)
    inputs = (report or {}).get("safety_inputs") or {}
    for ref in cities:
        cat = world.bundle.catalog_for(ref)
        if cat is None:
            continue
        key = next(iter({o.opportunity_id.split(".")[0] for o in cat.opportunities}), None)
        s = inputs.get(key) if key else None
        if not isinstance(s, dict):
            continue
        # 构建报告里这座城的治安资料说明：按报告里的城市编号（``safety_input/<编号>``）与字段绑定
        if s.get("missing"):
            text = t(
                "datanature.scope.safety_city.missing",
                "{city}：{missing}",
                city=city_ent(ref, cat.label),
                missing=Ent("safety_input", key, s["missing"], "missing"),
            )
        else:
            text = t(
                "datanature.scope.safety_city.text",
                "{city}：{scope}。{coverage}。{residence}。2024 年每 10 万人暴力犯罪 {violent}、财产犯罪 {property}"
                "（报案数，不是全部发生的犯罪）",
                city=city_ent(ref, cat.label),
                scope=Ent("safety_input", key, s["scope"], "scope"),
                coverage=Ent("safety_input", key, s["coverage"], "coverage"),
                residence=Ent("safety_input", key, s["residence"], "residence"),
                violent=Num(f"{s['violent_rate']}"),
                property=Num(f"{s['property_rate']}"),
            )
        out.append(
            ScopeNote(
                topic="data",
                title_zh=t("datanature.scope.safety_city.title", "治安资料的覆盖"),
                text_zh=text[:600],
                cities=(ref,),
            )
        )
    return out


def _report_counts(pack: DataPack, world: RunWorld, city: str) -> tuple[BuildCount, ...]:
    """冻结资料包的构建报告（与生活输入同一个目录）里这座城的计数。报告缺失、记下的生活输入身份与这份资料包的不同，或认不出
    这座城时返回空（未知，不是 0）。城市在报告里按编号前缀（``{key}.area.*``、``{key}.food.*`` 的共同约定）对上。"""
    report = _pack_report(pack)
    if report is None:
        return ()
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    prefixes = {a.area_id.split(".")[0] for a in cat.areas} | {
        o.opportunity_id.split(".")[0] for o in cat.opportunities
    }
    entry = (report.get("cities") or {}).get(next(iter(prefixes))) if len(prefixes) == 1 else None
    if not isinstance(entry, dict):
        return ()
    flat: dict[str, int] = {}
    for k, v in (entry.get("counts") or {}).items():
        if isinstance(v, dict):
            flat.update({f"{k}.{d}": n for d, n in v.items()})
        else:
            flat[k] = v
    flat.update({f"excluded.{k}": n for k, n in (entry.get("excluded") or {}).items()})
    return tuple(
        BuildCount(
            key=k,
            label_zh=(label("build_category", k, BUILD_LABEL_ZH[k]) if k in BUILD_LABEL_ZH else k)[
                :80
            ],
            count=int(n),
        )
        for k, n in sorted(flat.items())
    )


def city_data_nature(pack: DataPack, world: RunWorld, city: str) -> CityDataNature:
    """一座城的资料性质（公开投影）。"""
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    by_cat: dict[str, Counter[str]] = {}
    totals: Counter[str] = Counter()
    #: 登记的模型设定 → 用到它的机会（编号、名称）。设定按第一个用到它的机会绑定（``assumption/<机会编号>``）
    notes: dict[str, list[tuple[str, str]]] = {}
    for o in cat.opportunities:
        by_cat.setdefault(o.category, Counter())[o.source.kind] += 1
        totals[o.source.kind] += 1
        if o.source.kind == "synthetic" and o.source.synthetic_note:
            notes.setdefault(o.source.synthetic_note, []).append((o.opportunity_id, o.label))
    #: 治疗的共同假设（截到 400 字后去重）→ 第一个写着它的治疗条目
    treatment_notes: dict[str, str] = {}
    for tr in cat.treatments:
        if tr.source.kind == "synthetic" and tr.source.synthetic_note:
            treatment_notes.setdefault(tr.source.synthetic_note[:400], tr.treatment_id)
    menus: dict[str, list[Any]] = {}
    for m in cat.menus:
        menus.setdefault(m.category, []).append(m)
    build = _report_counts(pack, world, city)
    n = sum(totals.values())
    if pack.body.status == "real_data_frozen":
        summary = t(
            "datanature.summary.real",
            "{city}：目录 {n} 项机会里，观测 {observed}、派生 {derived}、估计 {estimated}、合成设定 {synthetic}。"
            "城市资料来自公开来源与登记的模型设定；生活规则、评分与评价参数是合成探索设定，不据此排序真实城市",
            city=city_ent(city, cat.label),
            n=n,
            observed=totals["observed"],
            derived=totals["derived"],
            estimated=totals["estimated"],
            synthetic=totals["synthetic"],
        )
    else:
        summary = t(
            "datanature.summary.synthetic",
            "{city}：合成资料包，目录 {n} 项机会全部是手工合成的模型设定或测试资料，不是真实城市资料",
            city=city_ent(city, cat.label),
            n=n,
        )
    return CityDataNature(
        city_ref=city,
        label=label("city", city, cat.label),
        pack_status=pack.body.status,
        summary_zh=summary,
        opportunities=tuple(
            CategorySources(category=c, total=sum(k.values()), by_kind=_counts(k))
            for c, k in by_cat.items()
        ),
        totals=_counts(totals),
        named_assumptions=tuple(
            NamedAssumption(
                note_zh=label("assumption", opps[0][0], note[:400], "note"),
                count=len(opps),
                examples=tuple(label("opportunity", oid, name) for oid, name in opps[:3]),
            )
            for note, opps in notes.items()
        ),
        common_priors=_common_priors(world, city),
        treatment_assumptions_zh=tuple(
            label("assumption", tid, note, "note") for note, tid in treatment_notes.items()
        ),
        model_plan_activities=tuple(
            label("opportunity", o.opportunity_id, o.label)
            for o in cat.opportunities
            if o.activity is not None and o.source.kind == "synthetic"
        ),
        home_areas=sum(1 for a in cat.areas if a.kind in ("home", "home_work")),
        work_areas=sum(1 for a in cat.areas if a.kind in ("work", "home_work")),
        commute_modes=commute_modes(world, city),
        menus=tuple(
            MenuSummary(
                category=c,
                years=len({m.year for m in ms}),
                env_repeats=len({m.env_repeat for m in ms}),
                items_min=min(len(m.opportunity_ids) for m in ms),
                items_max=max(len(m.opportunity_ids) for m in ms),
            )
            for c, ms in menus.items()
        ),
        tax=tax_scope(world, city),
        build_status="from_pack_report" if build else "not_available",
        build=build,
    )


def _commute_gap_texts(world: RunWorld, city: str) -> list[tuple[Sub, Sub, Sub]]:
    """这座城的通勤关系表里缺、而比较组别的城市有的方式：每种一条（原因，影响，下一步），预检查的问题与范围说明共用同一句话。"""
    cat = world.bundle.catalog_for(city)
    if cat is None or cat.commute is None:
        return []
    elsewhere = {
        r.mode
        for c in world.bundle.catalogs
        if c.commute is not None and str(c.city) != city
        for r in c.commute.modes
    }
    here = {r.mode for r in cat.commute.modes}
    return [
        (
            Sub(
                "datanature.commute_gap.message",
                "{city} 的通勤关系表里没有{mode}（比较组里别的城市有）",
                (
                    ("city", city_ent(city, cat.label)),
                    ("mode", Ent("commute_mode", mode, COMMUTE_MODE_LABEL[mode])),
                ),
            ),
            Sub(
                "datanature.commute_gap.impact",
                "这座城的分身到岗时没有这种方式可用，只能用表里有的方式；缺的方式不当 0 分钟，也不当免费",
            ),
            Sub(
                "datanature.commute_gap.next",
                "知悉这一限制（原因见范围说明里本版不支持的内容）",
            ),
        )
        for mode in COMMUTE_MODES
        if mode in elsewhere and mode not in here
    ]


def commute_gaps(world: RunWorld, city: str) -> list[ProductIssue]:
    """这座城的通勤关系表里缺、而比较组别的城市有的方式（例如旧金山没有公交票价）：照实报告，不当 0 分钟、也不当免费。"""
    return [
        issue(
            "commute_mode_missing",
            f"cities.{city}.commute",
            say(message),
            say(impact),
            say(action),
            severity="warning",
        )
        for message, impact, action in _commute_gap_texts(world, city)
    ]


def _occupation_note(pack: DataPack) -> ScopeNote:
    schemes = sorted({f"{m.country} {m.scheme}" for o in pack.body.occupations for m in o.mappings})
    return ScopeNote(
        topic="occupations",
        title_zh=t("datanature.scope.occupations.title", "支持的职业"),
        text_zh=t(
            "datanature.scope.occupations.text",
            "这份资料包支持 {n} 种职业（受控词表；每种在内部对应一个职业分类：{schemes}）。"
            "词表外的职业在审阅时就会被拒绝并说明原因，系统不换成别的职业；某座城的目录里没有你这一职业的岗位时，那座城报错，"
            "不自动换岗。岗位要求的学历、证书或经验只认你在资格一栏明确确认的，职称与自由文字都不授予资格",
            n=len(pack.body.occupations),
            schemes=Join(tuple(plain(s) for s in schemes)),
        ),
    )


def scope_notes(
    pack: DataPack, world: RunWorld | None = None, cities: tuple[str, ...] = ()
) -> tuple[ScopeNote, ...]:
    """真实资料包的范围说明。合成资料包没有（它的范围写在 ``limits`` 里，含义不变）。``world`` 为空（预检查没有走到 E1 加载）
    时只写不依赖目录的几条：职业与本版不支持的内容。"""
    if pack.body.status != "real_data_frozen":
        return ()
    out = [_occupation_note(pack)]
    if world is not None:
        years = world.bundle.protocol.simulation_years
        by_policy: dict[str, list[str]] = {}
        for ref in cities:
            tax = tax_scope(world, ref)
            if tax is not None:
                by_policy.setdefault(tax.policy_id, []).append(ref)
        for refs in by_policy.values():
            tax = tax_scope(world, refs[0])
            assert tax is not None
            if tax.status == "official_2024_rules":
                assert tax.residence_scope_zh is not None  # 官方规则必写支持的居住与工作地
                text = t(
                    "datanature.scope.tax.official",
                    "按官方 2024 年规则逐月预扣（按全年累计工资），模拟的 {years} 年都用 2024 年规则；只支持单身、只有工资收入、"
                    "按标准扣除。支持的居住与工作地：{residence}。没有建模的 {n} 项逐条列在这座城"
                    "的资料性质（data_nature.tax）里——它们让结果与真实税单有差别",
                    years=years,
                    residence=Ent(
                        "tax_policy", tax.policy_id, tax.residence_scope_zh, "residence_scope"
                    ),
                    n=len(tax.not_modeled_zh),
                )
            else:
                text = t("datanature.scope.tax.synthetic", "这座城的税是合成测试替身，不是真实税")
            out.append(
                ScopeNote(
                    topic="tax",
                    title_zh=t("datanature.scope.tax.title", "税：{policy}", policy=tax.policy_id),
                    text_zh=text,
                    cities=tuple(refs),
                )
            )
        out.append(
            ScopeNote(
                topic="commute",
                title_zh=t("datanature.scope.commute.title", "通勤怎么算"),
                text_zh=t(
                    "datanature.scope.commute.text",
                    "通勤按每座城的片区关系表执行：住处片区 × 岗位的就业片区 × 方式 → 一次往返的分钟与花费（片区级资料或估计，"
                    "不是路线模拟）。每个月在可用的方式里取往返最少的一种，分钟相同取花费低的；开车要这个月有在用的用车权（另一项"
                    "按月付费的机会）。关系表里没有的线或方式不能走，不当 0 分钟；停车与过路费没有建模",
                ),
            )
        )
        out += safety_notes(pack, world, cities)
        for ref in cities:
            for message, impact, _action in _commute_gap_texts(world, ref):
                out.append(
                    ScopeNote(
                        topic="commute",
                        title_zh=t("datanature.scope.commute_gap.title", "缺少的通勤方式"),
                        text_zh=t(
                            "datanature.scope.commute_gap.text",
                            "{message}：{impact}",
                            message=message,
                            impact=impact,
                        ),
                        cities=(ref,),
                    )
                )
        menu_cities = {
            ref: sorted({m.category for m in cat.menus})
            for ref in cities
            if (cat := world.bundle.catalog_for(ref)) is not None and cat.menus
        }
        if menu_cities:
            cats = sorted({c for cs in menu_cities.values() for c in cs})
            names = Join(
                tuple(
                    Ent("menu_category", c, CATEGORY_ZH[c]) if c in CATEGORY_ZH else plain(c)
                    for c in cats
                ),
                zh_sep="与",
                en_sep=" and ",
            )
            out.append(
                ScopeNote(
                    topic="menus",
                    title_zh=t("datanature.scope.menus.title", "年度菜单"),
                    text_zh=t(
                        "datanature.scope.menus.text",
                        "每年给分身看的{names}不是整个目录，而是一份冻结的抽样菜单（每类、每年、每个环境重复一份；同城、同年、"
                        "同一环境重复的分身看同一份，抽法与种子里没有分身编号）。菜单是接触机会，不是成交或录用。现住的房子可以"
                        "续租、现在的岗位可以延续，最低生活的保底方案总在；菜单外的选择会被计划检查拒绝",
                        names=names,
                    ),
                    cities=tuple(menu_cities),
                )
            )
    out += [
        ScopeNote(
            topic="unsupported",
            title_zh=t("datanature.scope.unsupported.title", "本版不支持"),
            text_zh=line,
        )
        for line in pack_lines("unsupported", pack.body.unsupported)
    ]
    return tuple(out)
