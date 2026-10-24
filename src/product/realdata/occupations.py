"""E3B 六城真实生活资料包的职业与岗位（DECISIONS/0042）：支持范围、能力词表、岗位、培训与开局规则。

输入是 ``life_inputs`` 的切片与配置 ``configs/intake/occupations_us_v1.yaml``：

* **能力**只有三类来源，都是结构化的：学历（“至少达到”某级，用户确认）、职业专属的中学后非学位证书（``soc_<职业>_award``，用户确认或
  CIP → SOC 对应的 IPEDS 项目授予）、相关职业经验（``soc_<职业>_experience``，确认的经历按相关职业名单与年数）。不从职称或自由文字推
  学位、证书或执照。
* **岗位**（E3B 映射修正，``job_wage_support_v1``）：每个支持的职业 × 都会区写一份岗位模板——OEWS p10、p25、p50、p75、p90 之间的分段
  线性分位函数在 p10–p90 里切 20 个等概率区间、取每个区间中点的年薪（每点概率 0.04）；两端共 20% 是未识别，不生成尾部。工时与
  就业片区不在这里定：整合时与通勤资料里同一批人员记录的（通常每周工时 × 就业片区）联合分布拼起来（给定职业后条件独立，登记的假设
  ``job_wage_independent_of_hours_area_v1``），薪酬沿用“年薪 × 每周工时 ÷ 40”，每周工作日与混合到岗比例不变（低工时 = 每天工作短）。
  职业与行业分开：只按职业记，不写行业。
* **开局**：确认职业的中位工资（OEWS p50）；工时与片区取这一职业大类联合分布里权重最大的一点（只用于统一开局，不压缩候选池）；不在范围、
  本城没有资料就拒绝并说明原因，不替换成别的职业。
"""

from __future__ import annotations

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from common.ids import OccupationId
from common.life_contracts import (
    AbilityScope,
    AbilitySpec,
    DurationSpec,
    JobArrangement,
    LifeOpportunity,
    PriceQuote,
    ResourceProfile,
    SourceRecord,
)
from product.realdata.life_items import (
    WEEKS_PER_MONTH,
    Ctx,
    Item,
    Report,
    dec,
    money,
    quote,
    round_to,
)

OCCUPATIONS_VERSION = "e3b-occupations-v2"


def _key(soc: str) -> str:
    return soc.replace("-", "_")


def award_ability(soc: str) -> str:
    return f"soc_{_key(soc)}_award"


def experience_ability(soc: str) -> str:
    return f"soc_{_key(soc)}_experience"


def _half_up(q: Fraction) -> int:
    return int(
        (Decimal(q.numerator) / Decimal(q.denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


class Scope:
    """支持的职业范围与每个职业的入职要求（EP 表 5.4 → 能力）。"""

    def __init__(self, occ: dict[str, Any], sl: dict[str, Any]) -> None:
        self.cfg = occ
        self.ep: dict[str, dict[str, str]] = sl["ep"]
        self.supported = {s["soc"]: s for s in occ["supported"]}
        self.education = {e["ep"]: e for e in occ["education"]}
        self.levels = [e["level"] for e in occ["education"]]

    def education_of(self, soc: str) -> dict[str, Any] | None:
        ep = self.ep.get(soc)
        return None if ep is None else self.education.get(ep["typical_entry_education"])

    def experience_of(self, soc: str) -> dict[str, Any] | None:
        ep = self.ep.get(soc)
        return None if ep is None else self.cfg["experience"].get(ep["related_work_experience"])

    def problems(self) -> list[str]:
        """配置与 EP 是否对得上：每个支持的职业都要有 EP 要求，要经验的职业要列相关职业。"""
        out = []
        for soc, s in self.supported.items():
            if self.ep.get(soc) is None:
                out.append(f"{soc}: 切片里没有 EP 入职要求")
                continue
            if self.education_of(soc) is None:
                out.append(
                    f"{soc}: EP 入职教育“{self.ep[soc]['typical_entry_education']}”不在配置的教育表里"
                )
            if self.experience_of(soc) is not None and not s.get("related"):
                out.append(f"{soc}: EP 要求相关职业经验，配置没有列相关职业")
            for r in s.get("related", ()):
                if r == soc:
                    out.append(f"{soc}: 相关职业不能是它自己")
        return out

    def required(self, soc: str) -> tuple[str, ...]:
        """这个职业的岗位要求的能力（资格标签里的能力编号）。"""
        out: list[str] = []
        edu = self.education_of(soc)
        if edu is not None:
            if edu.get("ability"):
                out.append(edu["ability"])
            if edu.get("occupation_award"):
                out.append(award_ability(soc))
        if self.experience_of(soc) is not None:
            out.append(experience_ability(soc))
        return tuple(out)

    def abilities(self, observed_on: str, mapping_version: str) -> list[AbilitySpec]:
        """能力词表（给协议的 ``CareerRules.abilities``）：学历各级（通用）、范围内职业的证书与经验（只用于那个职业）。"""
        src = SourceRecord(
            kind="derived",
            observed_on=_dt.date.fromisoformat(observed_on),
            geography_scope="US national (BLS Employment Projections table 5.4)",
            mapping_version=mapping_version,
            inputs=("bls_ep_education", "configs/intake/occupations_us_v1.yaml"),
            rule="EP 典型入职教育与相关职业经验 → 能力（ep_typical_education_as_requirement_v1、related_experience_v1）",
        )
        out = [
            AbilitySpec(
                ability_id=e["ability"],
                kind="credential",
                label=e["label"],
                description=f"最高学历至少达到“{e['label']}”（用户确认的结构化学历；更高的学历也满足）",
                scope=AbilityScope(kind="general"),
                source=src,
            )
            for e in self.cfg["education"]
            if e.get("ability")
        ]
        for soc, s in sorted(self.supported.items()):
            occ = OccupationId(scheme=self.cfg["scheme"], code=soc)
            edu = self.education_of(soc)
            if edu is not None and edu.get("occupation_award"):
                out.append(
                    AbilitySpec(
                        ability_id=award_ability(soc),
                        kind="credential",
                        label=f"{s['label']}的中学后证书",
                        description=f"与 {soc} 对应的中学后非学位证书（用户确认，或完成 CIP 对应到 {soc} 的培训项目）",
                        scope=AbilityScope(kind="occupation", occupation=occ),
                        source=src,
                    )
                )
            exp = self.experience_of(soc)
            if exp is not None:
                out.append(
                    AbilitySpec(
                        ability_id=experience_ability(soc),
                        kind="knowledge",
                        label=f"{s['label']}的相关工作经验",
                        description=(
                            f"在相关职业（{', '.join(s.get('related', ()))}）工作至少 {exp['min_years']} 年（用户确认的经历；"
                            "模拟期里不会自动积累）"
                        ),
                        scope=AbilityScope(kind="occupation", occupation=occ),
                        source=src,
                    )
                )
        return out

    def from_confirmed(
        self,
        *,
        education_level: str | None,
        awards: tuple[str, ...] = (),
        experience: tuple[tuple[str, int], ...] = (),
    ) -> dict[str, Any]:
        """确认的事实 → 开局已有的能力（``confirmed_experience_mapping_v1``）。

        * 学历：确认的最高学历授予它和它以下各级的“至少达到”；
        * 证书：只有范围内要求证书的职业、用户确认持有的那张；
        * 经历：（职业，年数）——每个支持的职业 Y，若经历的职业在 Y 的相关名单里且年数达到 Y 的要求，授予 Y 的经验能力。
          确认的现职 X 本身不授予学历、证书或执照。"""
        held: list[str] = []
        notes: list[str] = []
        if education_level is not None:
            if education_level not in self.levels:
                raise ValueError(f"学历等级 {education_level!r} 不在配置的教育表里")
            top = self.levels.index(education_level)
            for e in self.cfg["education"][: top + 1]:
                if e.get("ability"):
                    held.append(e["ability"])
        for soc in awards:
            edu = self.education_of(soc)
            if soc not in self.supported or edu is None or not edu.get("occupation_award"):
                notes.append(f"{soc} 不在范围或不要求中学后证书：确认的证书不授予能力")
                continue
            held.append(award_ability(soc))
        for soc, years in experience:
            for y, s in sorted(self.supported.items()):
                exp = self.experience_of(y)
                if exp is not None and soc in s.get("related", ()) and years >= exp["min_years"]:
                    held.append(experience_ability(y))
        return {
            "abilities": sorted(set(held)),
            "notes": notes,
            "rule": "confirmed_experience_mapping_v1",
        }


def _arrangement(occ: dict[str, Any], mode: str, weekly: Fraction, burden: float) -> JobArrangement:
    a = occ["arrangement"]
    workdays = _half_up(Fraction(a["days_per_week"]) * WEEKS_PER_MONTH)
    onsite = (
        workdays if mode == "onsite" else _half_up(workdays * Fraction(a["hybrid_onsite_share"]))
    )
    return JobArrangement(
        work_mode=mode,  # type: ignore[arg-type]
        work_hours_per_month=round_to(weekly * WEEKS_PER_MONTH, 2),
        workdays_per_month=workdays,
        onsite_days_per_month=onsite,
        burden_per_work_hour=burden,
    )


#: 岗位机会的属性键（``job_opportunity`` 写的；岗位在资料包组装时展开，冻结清单要先声明这些键）
JOB_ATTRIBUTE_KEYS = (
    "soc_title",
    "employment",
    "entry_education",
    "related_experience",
    "on_the_job_training",
    "hours_area_level",
    "employer",
)

#: OEWS 年薪分位（职业 × 都会区）：分位点与切片里的字段名。
WAGE_KNOTS = (
    (Fraction(1, 10), "annual_wage_p10"),
    (Fraction(1, 4), "annual_wage_p25"),
    (Fraction(1, 2), "annual_wage_median"),
    (Fraction(3, 4), "annual_wage_p75"),
    (Fraction(9, 10), "annual_wage_p90"),
)
#: p10–p90 里等概率区间的个数：每个区间 0.04 的概率，取中点（0.12、0.16、……、0.88）。
WAGE_INTERVALS = 20


def wage_support(w: dict[str, Any]) -> dict[str, Any]:
    """OEWS 分位 → 年薪支持点（``job_wage_support_v1``）：在 (0.10, p10)、(0.25, p25)、(0.50, p50)、(0.75, p75)、(0.90, p90) 之间分段线性，
    p10–p90 切 20 个等概率区间，取每个区间中点的分位值。某一段的端点没有发布（* 或 #）时，落在这一段里的区间都是未识别；两端
    （p10 以下、p90 以上）共 20% 也是未识别——不自行生成尾部。返回支持点与未识别的概率。"""
    knots = [(q, w.get(f)) for q, f in WAGE_KNOTS]
    step = Fraction(8, 10) / WAGE_INTERVALS
    points, missing = [], Fraction(0)
    for k in range(WAGE_INTERVALS):
        mid = Fraction(1, 10) + step * k + step / 2
        i = next(j for j in range(len(knots) - 1) if knots[j][0] <= mid <= knots[j + 1][0])
        (q0, a0), (q1, a1) = knots[i], knots[i + 1]
        if a0 is None or a1 is None:
            missing += step
            continue
        annual = dec(a0) + (dec(a1) - dec(a0)) * (mid - q0) / (q1 - q0)
        points.append(
            {
                "k": k,
                "quantile": f"{float(mid):.2f}",
                "mass": str(step),
                "annual": f"{round_to(annual, 2):.2f}",
            }
        )
    return {
        "points": points,
        "unidentified_tails": "1/5",
        "unidentified_inside": str(missing),
        "rule": "job_wage_support_v1",
    }


def job_templates(
    ctx: Ctx, occ: dict[str, Any], scope: Scope, report: Report
) -> list[dict[str, Any]]:
    """每个支持的职业一份岗位模板：OEWS 年薪支持点、资格、到岗方式与来源。工时与就业片区在整合时拼上（见模块说明）。"""
    out: list[dict[str, Any]] = []
    for soc, s in scope.supported.items():
        w = ctx.wage(soc)
        if w is None:
            report.add("job", "gap", soc, "本城 OEWS 没有这个职业")
            continue
        if w["wage_basis"] != "annual_and_hourly":
            report.add("job", "gap", soc, f"OEWS 工资口径是 {w['wage_basis']}，不能按工时换算薪酬")
            continue
        support = wage_support(w)
        if not support["points"]:
            report.add("job", "gap", soc, "OEWS 没有发布能构成工资支持点的分位")
            continue
        if support["unidentified_inside"] != "0":
            report.add(
                "job",
                "gap",
                f"{soc}.wage_inside",
                f"OEWS 有分位没有发布：p10–p90 里 {support['unidentified_inside']} 的概率未识别，不补",
            )
        ep = scope.ep[soc]
        out.append(
            {
                "soc": soc,
                "soc_major": soc.split("-")[0],
                "label": s["label"],
                "title": w["title"],
                "work_mode": s["work_mode"],
                "eligibility": [f"ability:{x}" for x in scope.required(soc)],
                "requires": list(scope.required(soc)),
                "employment": int(w["employment"]) if w.get("employment") else None,
                "median_annual": w.get("annual_wage_median"),
                "wage": support,
                "record_id": w["record_id"],
                "conversion": w.get("conversion"),
                "geography_scope": ctx.scope,
                "ep": {
                    "entry_education": ep["typical_entry_education"],
                    "related_experience": ep["related_work_experience"],
                    "on_the_job_training": ep["typical_on_the_job_training"],
                },
                "assumptions": [
                    "job_scope_v1",
                    "ep_typical_education_as_requirement_v1",
                    "job_arrangement_v1",
                    "job_work_mode_v1",
                    "job_burden_v1",
                    "job_wage_support_v1",
                    "job_wage_independent_of_hours_area_v1",
                    *(("related_experience_v1",) if scope.experience_of(soc) else ()),
                ],
            }
        )
    return out


def job_opportunity(
    tpl: dict[str, Any],
    occ: dict[str, Any],
    *,
    annual: Decimal,
    weekly_hours: int,
    area: str | None,
    opportunity_id: str,
    wage_label: str,
    numeraire: str,
    mapping_version: str,
    hours_level: str,
) -> LifeOpportunity:
    """一份可执行的岗位：模板 × 年薪支持点 × （每周工时，就业片区）。薪酬 = 年薪 × 每周工时 ÷ 40（四舍五入到分）；每月工时 =
    每周 × 52 ÷ 12；每周工作日与混合到岗比例照配置（低工时 = 每天工作短，不减少到岗天数）。"""
    a = occ["arrangement"]
    weekly = Fraction(weekly_hours)
    arr = _arrangement(occ, tpl["work_mode"], weekly, a["burden_per_work_hour"])
    pay = Fraction(annual) * weekly / 40
    need = tpl["requires"]
    return LifeOpportunity(
        opportunity_id=opportunity_id,
        category="job",
        label=f"{tpl['label']}·{wage_label}"[:120],
        unit="position",
        quality=(
            f"{tpl['title']}；每周约 {weekly_hours} 小时、每月 {arr.workdays_per_month} 个工作日、到岗 "
            f"{arr.onsite_days_per_month} 天"
            + (f"；要求 {', '.join(need)}" if need else "；没有资格要求")
        )[:280],
        spec={
            "work_mode": tpl["work_mode"],
            "weekly_hours": float(weekly_hours),
            "wage_position": wage_label,
        },
        attributes={
            "soc_title": tpl["title"],
            "employment": tpl["employment"] if tpl["employment"] is not None else "unknown",
            "entry_education": tpl["ep"]["entry_education"],
            "related_experience": tpl["ep"]["related_experience"],
            "on_the_job_training": tpl["ep"]["on_the_job_training"],
            "hours_area_level": hours_level,
            "employer": "not_identifiable",
        },
        availability="available",
        compensation=quote(pay, numeraire, "per_year", "position-year"),
        occupation=OccupationId(scheme=occ["scheme"], code=tpl["soc"]),
        eligibility=tuple(tpl["eligibility"]),
        area=area,
        duration=DurationSpec(kind="ongoing_service"),
        resource=ResourceProfile(
            basis="job",
            job=arr,
            note="工资、工时与到岗日是这个岗位固定的，不能自己改；生大病的月份请病假、没有工资",
        ),
        source=SourceRecord(
            kind="estimated",
            observed_on=_dt.date(2024, 5, 31),
            geography_scope=tpl["geography_scope"],
            mapping_version=mapping_version,
            inputs=(tpl["record_id"], f"commute.hours_area_joint:{hours_level}"),
            rule=(
                f"OEWS 年薪 {annual}（{wage_label}）× 每周 {weekly_hours} 小时 ÷ 40 = {round_to(pay, 2)}；每月工时 = 每周 × 52 ÷ 12；"
                "工时与就业片区来自同一批人员记录的联合分布，与工资在给定职业后条件独立（登记的假设）"
            )[:280],
        ),
    )


def training(ctx: Ctx, occ: dict[str, Any], scope: Scope, report: Report) -> list[Item]:
    t = occ["training"]
    award_socs = {
        soc for soc in scope.supported if (scope.education_of(soc) or {}).get("occupation_award")
    }
    monthly = round_to(dec(t["weekly_hours"]) * WEEKS_PER_MONTH, 2)
    out: list[Item] = []
    for p in ctx.metro["training"]:
        grants = sorted({award_ability(s) for s in p["related_socs"] if s in award_socs})
        if not grants:
            report.add(
                "training",
                "not_applicable",
                "no_in_scope_award",
                "CIP 对应的职业不在范围、或不要求中学后证书：项目不授予这里的能力",
            )
            continue
        if p["tuition"] is None:
            report.add("training", "gap", "tuition", "IPEDS 没有这个项目的学费")
            continue
        if p["months"] is None or int(p["months"]) % 12 != 0:
            report.add(
                "training",
                "gap",
                "length_not_whole_years",
                "全日制完成月数不是整年（E1 的培训按整年计）或缺失",
            )
            continue
        years = int(p["months"]) // 12
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.training.ipeds.{p['record_id'].removeprefix('training.ipeds2023.')}",
            category="training",
            label=f"CIP {p['cip']} 证书项目·{p['institution']}"[:120],
            unit="program",
            quality=(
                f"{p['institution']}（{p['city']}）；全日制 {p['months']} 个月；完成后取得 {', '.join(grants)}（下一行动年起可用）；"
                "开课日期与每周课时资料里没有"
            )[:280],
            attributes={
                "cip": p["cip"],
                "institution": p["institution"] or "",
                "months_to_complete": int(p["months"]),
                "start_calendar": "unknown",
                "related_socs": ";".join(p["related_socs"]),
            },
            availability="available",
            price=PriceQuote(
                amount=money(dec(p["tuition"]), ctx.numeraire),
                billing="one_time",
                per_unit="program",
            ),
            duration=DurationSpec(kind="training_course", completion_years=years),
            grants=tuple(grants),
            resource=ResourceProfile(
                basis="per_service_month",
                hours=monthly,
                burden_per_hour=t["burden_per_hour"],
                quantity_scales=False,
                note=f"按实际参加的月份计；每月 {monthly:g} 小时（每周 {t['weekly_hours']} 小时的设定）",
            ),
            source=ctx.estimated(
                "2024-06-30",
                [p["record_id"], "nces_cip_soc_2020"],
                f"学费 = IPEDS 2023–24 整个项目的学费（{p['tuition_method']}），四舍五入到分；CIP {p['cip']} → SOC 对照到范围内要求证书的职业"
                "就授予那个职业的证书能力；每月小时是设定",
            ),
        )
        out.append(
            Item(
                "training",
                o,
                "executable",
                ("ipeds_training_v1", "training_burden_v1"),
                (p["record_id"],),
                ("start_dates_unknown",),
            )
        )
    return out


def start_jobs(
    ctx: Ctx, occ: dict[str, Any], scope: Scope, templates: list[dict[str, Any]]
) -> dict[str, Any]:
    """开局岗位索引（``start_job_median_v2``）：范围内每个职业 → 本城的开局岗位编号（中位工资；工时与片区整合时取这一职业大类联合
    分布里权重最大的一点），或拒绝原因。"""
    have = {t["soc"]: t for t in templates}
    out: dict[str, Any] = {}
    for soc in scope.supported:
        tpl = have.get(soc)
        if tpl is not None and tpl.get("median_annual") is not None:
            out[soc] = {
                "status": "ok",
                "opportunity_id": f"{ctx.key}.job.{soc}.start",
                "requires_confirmed_abilities": list(scope.required(soc)),
            }
        else:
            out[soc] = {
                "status": "rejected",
                "reason_zh": "本城没有这个职业的中位工资（资料缺失），不替换成别的职业",
            }
    return out


def start_job(index: dict[str, Any], occ: dict[str, Any], soc: str) -> dict[str, Any]:
    """开局岗位：在本城的索引里找确认职业的中位数档；不在范围就拒绝并说明原因。"""
    if soc in index:
        return index[soc]
    reason = occ["excluded"].get(soc)
    return {
        "status": "rejected",
        "reason_zh": f"职业 {soc} 不在支持范围"
        + (f"：{reason}" if reason else "（范围见 occupations_us_v1.supported）")
        + "；不替换成别的职业",
    }
