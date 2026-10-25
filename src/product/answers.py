"""原始答案 → 审阅 → 确认画像（E3-A 模块 B，DECISIONS/0034）。

三层分开保存：``UserInputDraft``（原始答案与逐项状态）→ ``ProfileReview``（E3 的可读解释、候选、缺失、未支持与待确认）→
``ConfirmedProfile``（用户确认的语义与精确数值，带内容身份）。规则：

* 未答、已提供（明确 0 也是已提供）、明确无所谓、暂不确定四种状态互不代替；“无所谓”“不确定”不会被自动换成 0 或均分。
* 六维合计必须**精确**等于 100；手工输入按精确十进制解析；只有用户主动选择平均分配才生成均分（六维每项 50/3、物质每项 25）。
  不预填、不按比例自动扣减；合计不对就报“还差 / 多出几分”，由用户自己改。
* 物质父项为正时四项细分必须精确合计 100；父项明确为 0 时细分不适用，不伪造回答。旧问卷的 ``laundry`` 不能自动当作
  ``daily_convenience``；``healthcare`` 不在六维里（健康回答另有独立问卷）。
* 职业（v3.4）从输入目录直接选中（不需要文字；目录过期、编号不在目录里都拒绝），或用兼容的文字描述在少量可读候选里确认；
  不填职业代码，不从职称授予资格。金额保留原始金额、币种、时期与口径，没有合法换算就阻断。
* 资格（v3.4）按最高学历、职业证书与相关经验的回答换算（``product.qualifications``）；与旧的逐项资格确认冲突就拒绝，不取并集。
* 自由文字超过 2,000 字符报错，不截断。新问卷不再收集硬要求与属性偏好：草稿里还有这两类答案就阻断，按迁移说明清空后再审阅
  （``product.migration``），不在旧值仍会影响模拟时把它藏起来。

确认画像的内容身份覆盖版本、资料包与草稿引用：改了相关答案、词表、问卷或转换规则，旧确认就失效；内容被改写，指纹核对不通过。
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction
from typing import Any

from common.product_contracts import (
    PRODUCT_SCHEMA_VERSION,
    AllocationAnswer,
    AllocationLine,
    AllocationReview,
    ConfirmedAllocation,
    ConfirmedHealth,
    ConfirmedHealthAnswer,
    ConfirmedMaterial,
    ConfirmedMoney,
    ConfirmedOccupation,
    ConfirmedProfile,
    DraftRef,
    EvidenceRef,
    ExactNumber,
    FieldSource,
    HealthReviewItem,
    ItemStatus,
    MoneyReview,
    OccupationCandidate,
    OccupationMapping,
    OccupationReview,
    ProductIssue,
    ProductMoney,
    ProfileReview,
    SampleLabel,
    UserInputDraft,
    content_fingerprint,
)
from product.capabilities import matching_note
from product.display import Code, Ent, Join, Num, Raw, Sub, label, t
from product.exact import (
    ExactParseError,
    canonical,
    display,
    money_display,
    parse_money_text,
    parse_points,
)
from product.input_catalog import LoadedCatalog, input_catalog
from product.occupation import duties, match_occupation
from product.pack import DataPack
from product.qualifications import confirmed as confirmed_qualifications

TOTAL = Fraction(100)
SIX = ("material", "safety", "opportunity", "leisure", "environment", "commute")
#: 两组定和分配的名称（问题说明与审阅条目共用）
WHAT = {
    "six": Sub("input.allocation.six", "六维"),
    "material": Sub("input.allocation.material", "物质四项"),
}
#: 维度与细项的用途：用于评分，也交给分身做计划（按信息许可）。
POINT_USAGE: tuple[str, ...] = ("scoring", "planning")


class ConfirmationRefusedError(ValueError):
    """不能确认：审阅里还有阻断问题，或用户没有知悉全部待确认事项。"""

    def __init__(self, issues: tuple[ProductIssue, ...]) -> None:
        super().__init__("；".join(i.message_zh for i in issues))
        self.issues = issues


def issue(
    code: str,
    field: str,
    message: str,
    impact: str,
    action: str,
    severity: str = "error",
    *,
    audit_ref: str | None = None,
) -> ProductIssue:
    return ProductIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        field=field,
        message_zh=message[:400],
        impact_zh=impact[:300],
        next_action_zh=action[:300],
        audit_ref=audit_ref,
    )


def draft_fingerprint(draft: UserInputDraft) -> str:
    """草稿的内容身份（版本、资料包与全部答案；样例标签不参与）。改了任何答案，指纹就不同。"""
    content = draft.model_dump(mode="json")
    content["meta"].pop("sample", None)
    return content_fingerprint(content)


def draft_ref(draft: UserInputDraft) -> DraftRef:
    return DraftRef(
        draft_id=draft.draft_id, revision=draft.revision, draft_fingerprint=draft_fingerprint(draft)
    )


# ---------------------------------------------------------------------- 版本


#: 版本问题出在哪一份对象上：（说明里的名称，问题字段里的名称）
WHERE = {
    "draft": (Sub("input.where.draft", "草稿"), Sub("input.where.draft.field", "草稿")),
    "confirmed": (
        Sub("input.where.confirmed", "确认画像"),
        Sub("input.where.confirmed.field", "确认画像"),
    ),
}


def _version_issues(pack: DataPack, meta_dump: dict, where: str) -> list[ProductIssue]:
    """``where`` 是 ``WHERE`` 里的一种对象；别的写法照原样用作名称（没有译文）。"""
    want = pack.meta().model_dump(mode="json")
    name, part = WHERE.get(where, (Raw(where), Raw(where)))
    out = []
    for key in (
        "schema_version",
        "questionnaire_version",
        "vocabulary_version",
        "health_questionnaire_version",
        "conversion_version",
    ):
        if meta_dump.get(key) != want[key]:
            out.append(
                issue(
                    "version_changed",
                    t("input.version_changed.field", "{where}.meta.{key}", where=part, key=key),
                    t(
                        "input.version_changed.message",
                        "{where} 用的 {key} 是 {old}，现在是 {new}",
                        where=name,
                        key=key,
                        old=Raw(str(meta_dump.get(key))),
                        new=str(want[key]),
                    ),
                    t(
                        "input.version_changed.impact",
                        "按旧版本问卷、词表或转换规则填写的答案不能直接沿用",
                    ),
                    t("input.version_changed.next", "按当前版本重新填写或重新确认相关答案"),
                )
            )
    if (
        meta_dump.get("data_pack", {}).get("content_fingerprint")
        != want["data_pack"]["content_fingerprint"]
    ):
        out.append(
            issue(
                "data_pack_changed",
                t("input.data_pack_changed.field", "{where}.meta.data_pack", where=part),
                t(
                    "input.data_pack_changed.message",
                    "{where} 所依据的资料包与现在的资料包内容不同",
                    where=name,
                ),
                t("input.data_pack_changed.impact", "城市、词表或资料变了，旧的解释不一定还成立"),
                t("input.data_pack_changed.next", "用当前资料包重新审阅并确认"),
            )
        )
    return out


# ---------------------------------------------------------------------- 定和分配


def _allocation(
    pack: DataPack,
    answer: AllocationAnswer,
    *,
    keys: tuple[str, ...],
    kind: str,
    labels: dict[str, str],
    supports: dict[str, str],
    equal_value: str,
    where: str,
    what: Sub,
) -> tuple[AllocationReview, dict[str, Fraction] | None, list[ProductIssue]]:
    """审阅一组定和分配。返回（审阅，完整时的精确点数，问题）。``kind`` 是各项名称的显示实体种类（六维 ``dimension``、物质细项
    ``leaf``）。"""
    digits = pack.body.points.display_decimals
    max_dec = pack.body.points.manual_max_decimals
    name = {k: Ent(kind, k, labels[k]) for k in keys}
    issues: list[ProductIssue] = []
    for k in answer.entries:
        if k in keys:
            continue
        if k == "healthcare":
            issues.append(
                issue(
                    "healthcare_not_in_six_dimensions",
                    f"{where}.entries.healthcare",
                    t(
                        "input.allocation.healthcare.message",
                        "健康不在六维 100 分里：新问卷的健康风险应对是一份独立问卷，不占这 100 分",
                    ),
                    t(
                        "input.allocation.healthcare.impact",
                        "这里的医疗点数不会被使用，也不会被改读成健康回答",
                    ),
                    t("input.allocation.healthcare.next", "删掉这一项，在健康问卷里回答"),
                )
            )
        elif k == "laundry":
            issues.append(
                issue(
                    "legacy_laundry_key",
                    f"{where}.entries.laundry",
                    t(
                        "input.allocation.laundry.message",
                        "旧问卷的“洗衣”问的是有没有洗衣条件，不能自动当作“日常生活便利”（愿意花钱减少日常杂务）",
                    ),
                    t("input.allocation.laundry.impact", "这一项不会被使用，也不会被改读成新题目"),
                    t(
                        "input.allocation.laundry.next",
                        "按新题目重新分配物质四项（居住空间、饮食生活、日常生活便利、个人消费与享受）",
                    ),
                )
            )
        else:
            issues.append(
                issue(
                    "unknown_allocation_key",
                    f"{where}.entries.{k}",
                    t(
                        "input.allocation.unknown_key.message",
                        "“{key}”不是本版{what}的一项",
                        key=Raw(k),
                        what=what,
                    ),
                    t("input.allocation.unknown_key.impact", "这一项不会被使用"),
                    t(
                        "input.allocation.unknown_key.next",
                        "只在 {names} 之间分配",
                        names=Join(tuple(name[x] for x in keys)),
                    ),
                )
            )
    lines: list[AllocationLine] = []
    values: dict[str, Fraction] = {}
    if answer.mode == "equal_split":
        if answer.entries:
            issues.append(
                issue(
                    "equal_split_with_values",
                    f"{where}.entries",
                    t(
                        "input.allocation.equal_split_with_values.message",
                        "选择了“平均分配”，又提交了手工数值：两者只能选一种",
                    ),
                    t(
                        "input.allocation.equal_split_with_values.impact",
                        "不能确定你要的是哪一种分配",
                    ),
                    t(
                        "input.allocation.equal_split_with_values.next",
                        "撤销平均分配，或清空手工数值",
                    ),
                )
            )
        q = Fraction(equal_value)
        for k in keys:
            values[k] = q
            lines.append(
                AllocationLine(
                    key=k,
                    label=label(kind, k, labels[k]),
                    state="provided",
                    exact=canonical(q),
                    display=display(q, digits),
                    support=supports[k],  # type: ignore[arg-type]
                    usage=POINT_USAGE,  # type: ignore[arg-type]
                )
            )
    else:
        for k in keys:
            entry = answer.entries.get(k)
            state = "unanswered" if entry is None else entry.state
            exact = shown = None
            if entry is not None and entry.state == "provided":
                assert entry.value_text is not None
                try:
                    q = parse_points(entry.value_text, max_decimals=max_dec)
                except ExactParseError as exc:
                    issues.append(
                        issue(
                            exc.code,
                            f"{where}.entries.{k}",
                            t(
                                "input.allocation.parse_error.message",
                                "{name}：{error}",
                                name=name[k],
                                error=exc.text,
                            ),
                            t("input.allocation.no_value.impact", "这一项没有可用的数值"),
                            t(
                                "input.allocation.parse_error.next",
                                "改成最多两位小数的数，或使用平均分配",
                            ),
                        )
                    )
                else:
                    if q > TOTAL:
                        issues.append(
                            issue(
                                "points_out_of_range",
                                f"{where}.entries.{k}",
                                t(
                                    "input.allocation.out_of_range.message",
                                    "{name} 的 {value} 分超过了 100",
                                    name=name[k],
                                    value=Raw(entry.value_text),
                                ),
                                t("input.allocation.no_value.impact", "这一项没有可用的数值"),
                                t("input.allocation.out_of_range.next", "每一项在 0 到 100 之间"),
                            )
                        )
                    else:
                        values[k] = q
                        exact, shown = canonical(q), display(q, digits)
            lines.append(
                AllocationLine(
                    key=k,
                    label=label(kind, k, labels[k]),
                    state=state,  # type: ignore[arg-type]
                    exact=exact,
                    display=shown,
                    support=supports[k],  # type: ignore[arg-type]
                    usage=POINT_USAGE,  # type: ignore[arg-type]
                )
            )
        not_given = [ln for ln in lines if ln.state != "provided"]
        for ln in not_given:
            text = {
                "unanswered": Sub("input.allocation.state.unanswered", "还没有分配"),
                "indifferent": Sub("input.allocation.state.indifferent", "选了“无所谓”"),
                "unsure": Sub("input.allocation.state.unsure", "选了“暂不确定”"),
            }[ln.state]
            issues.append(
                issue(
                    f"allocation_{ln.state}",
                    f"{where}.entries.{ln.key}",
                    t(
                        "input.allocation.not_given.message",
                        "{name} {state}：系统不会把它自动换成 0 分或平均分",
                        name=name[ln.key],
                        state=text,
                    ),
                    t("input.allocation.not_given.impact", "{what}还不完整，不能确认", what=what),
                    t(
                        "input.allocation.not_given.next",
                        "给它一个明确的分数（可以是 0），或选择平均分配",
                    ),
                )
            )
    total = sum(values.values(), Fraction(0)) if values else None
    complete = len(values) == len(keys) and not any(i.severity == "error" for i in issues)
    if len(values) == len(keys) and total is not None and total != TOTAL:
        diff = TOTAL - total
        gap = (
            Sub("input.allocation.gap.short", "还差 {n} 分", (("n", Num(canonical(diff))),))
            if diff > 0
            else Sub("input.allocation.gap.over", "多出 {n} 分", (("n", Num(canonical(-diff))),))
        )
        issues.append(
            issue(
                "points_sum_not_100",
                f"{where}.entries",
                t(
                    "input.allocation.sum_not_100.message",
                    "{what}合计是 {total}（显示 {shown}），不是 100：{gap}",
                    what=what,
                    total=Num(canonical(total)),
                    shown=Num(display(total, digits)),
                    gap=gap,
                ),
                t("input.allocation.sum_not_100.impact", "系统不会按比例自动扣减或补齐任何一项"),
                t(
                    "input.allocation.sum_not_100.next",
                    "由你决定从哪一项挪到哪一项，直到精确合计 100",
                ),
            )
        )
        complete = False
    review = AllocationReview(
        mode=answer.mode,
        status="complete" if complete else "incomplete",
        total=None
        if total is None
        else ExactNumber(exact=canonical(total), display=display(total, digits)),
        lines=tuple(lines),
    )
    return review, (values if complete else None), issues


# ---------------------------------------------------------------------- 健康问卷


def health_meaning(pack: DataPack, question_id: str, choice: str | None) -> tuple[str, str]:
    """一题作答的（含义，给分身的读法）。只照题目与选项写，不推断任何数值。"""
    q = next(x for x in pack.body.health_questionnaire.questions if x.question_id == question_id)
    # 含义是显示文字（审阅与确认画像）；给分身的读法只进 E1 的产品画像，不是显示文字
    if choice is None:
        meaning = t("input.health.meaning.unanswered", "用户没有回答这一题")
        note = "没有回答：没有这一项的明确指令，不要替用户补成某种倾向"
    elif choice == "unsure":
        meaning = t("input.health.meaning.unsure", "用户暂不确定")
        note = "暂不确定：没有这一项的明确指令，不要替用户补成某种倾向"
    elif choice == "no_clear_tendency":
        meaning = t("input.health.meaning.no_clear_tendency", "用户在这一题上没有明显倾向")
        note = "用户表示没有明显倾向（只供做计划参考，不说明生病概率、病痛、疗效或愿付金额）"
    else:
        field = "agree_meaning" if choice == "agree" else "disagree_meaning"
        meaning = label("health_question", question_id, getattr(q, field), field)
        note = f"用户自我描述：{meaning}（只供做计划参考，不说明生病概率、病痛、疗效或愿付金额）"
    return meaning, note


# ---------------------------------------------------------------------- 审阅


def review_draft(
    pack: DataPack, draft: UserInputDraft, *, sample: SampleLabel | None = None
) -> ProfileReview:
    """审阅一份草稿：给出可读解释与全部问题。不修改草稿，不替用户补任何答案。"""
    b = pack.body
    issues: list[ProductIssue] = list(
        _version_issues(pack, draft.meta.model_dump(mode="json"), "draft")
    )
    pending: list[str] = []
    summary: list[str] = []
    items: list[ItemStatus] = []

    # 城市
    city_refs: list[str] = []
    if draft.cities.state != "provided" or not draft.cities.city_refs:
        issues.append(
            issue(
                "cities_missing",
                "cities",
                t("input.cities_missing.message", "还没有选择要比较的城市"),
                t("input.cities_missing.impact", "没有城市就不能安排分身"),
                t("input.cities_missing.next", "从能力清单列出的城市里至少选一座"),
            )
        )
    else:
        seen: set[str] = set()
        for ref in draft.cities.city_refs:
            if pack.city(ref) is None:
                issues.append(
                    issue(
                        "city_not_supported",
                        "cities.city_refs",
                        t(
                            "input.city_not_supported.message",
                            "城市 {ref} 不在本版能力清单里",
                            ref=Raw(ref),
                        ),
                        t("input.city_not_supported.impact", "这座城市没有资料，不能运行"),
                        t("input.city_not_supported.next", "从能力清单列出的城市里选择"),
                    )
                )
            elif ref in seen:
                issues.append(
                    issue(
                        "city_duplicated",
                        "cities.city_refs",
                        t("input.city_duplicated.message", "城市 {ref} 选了两次", ref=Code(ref)),
                        t("input.city_duplicated.impact", "同一座城不重复计数"),
                        t("input.city_duplicated.next", "删掉重复的那一项"),
                    )
                )
            else:
                seen.add(ref)
                city_refs.append(ref)
        if len(city_refs) == 1:
            summary.append(
                t("input.summary.single_city", "只选了一座城：可以运行，但没有城市之间的比较")
            )
    items.append(
        ItemStatus(
            item="cities",
            label=t("input.item.cities.label", "候选城市"),
            answer_state=draft.cities.state,
            support="supported",
            confirmation="pending",
            usage=("start_state",),
            note=t(
                "input.item.cities.note",
                "每座城一个分身固定生活三年；城市身份用完整的国家 / 体系 / 代码",
            ),
        )
    )

    # 职业
    catalog = input_catalog(pack)
    if draft.occupation.mode == "catalog":
        occupation_review = _catalog_occupation(pack, catalog, draft, issues)
    else:
        occupation_review = _text_occupation(pack, draft, issues)
    items.append(
        ItemStatus(
            item="occupation",
            label=t("input.item.occupation.label", "职业"),
            answer_state=draft.occupation.state,
            support="supported",
            confirmation="pending",
            usage=("start_state", "planning"),
            note=t(
                "input.item.occupation.note",
                "用于匹配开局时的当地目录岗位；不授予任何资格，分类代码只在内部保留",
            ),
        )
    )
    concept_id = occupation_review.selected

    # 年龄
    age = draft.age.value if draft.age.state == "provided" else None
    if age is None:
        issues.append(
            issue(
                "age_missing",
                "age",
                t("input.age_missing.message", "还没有填写年龄"),
                t(
                    "input.age_missing.impact",
                    "开局状态需要年龄（模型的疾病规则按年龄段，未经医学标定）",
                ),
                t("input.age_missing.next", "填写模拟开始时的整数年龄"),
            )
        )
    elif not b.age_min <= age <= b.age_max:
        issues.append(
            issue(
                "age_out_of_range",
                "age.value",
                t(
                    "input.age_out_of_range.message",
                    "年龄 {age} 不在本版支持的 {low}–{high} 岁之间",
                    age=age,
                    low=b.age_min,
                    high=b.age_max,
                ),
                t("input.age_out_of_range.impact", "本版只支持单个成年人的情景"),
                t("input.age_out_of_range.next", "填写支持范围内的年龄"),
            )
        )
        age = None
    items.append(
        ItemStatus(
            item="age",
            label=t("input.item.age.label", "年龄"),
            answer_state=draft.age.state,
            support="supported",
            confirmation="pending",
            usage=("start_state",),
            note=t(
                "input.item.age.note",
                "进入已有的年龄规则（合成设定，未经医学标定）；不从职业猜年龄",
            ),
        )
    )

    # 现金
    money_review, money_issues = _money(pack, draft)
    issues += money_issues
    items.append(
        ItemStatus(
            item="cash",
            label=t("input.item.cash.label", "开始时可用于生活的钱"),
            answer_state=draft.cash.state,
            support="supported",
            confirmation="pending",
            usage=("start_state", "planning"),
            note=t(
                "input.item.cash.note",
                "可动用的流动资金，不是房产总值、信用额度或月薪；没填不是 0，不静默借贷",
            ),
        )
    )

    # 六维与物质
    dim_labels = {d.key: d.label for d in b.dimensions}
    dim_support = {d.key: d.support for d in b.dimensions}
    six_review, six_values, six_issues = _allocation(
        pack,
        draft.six_dimensions,
        keys=SIX,
        kind="dimension",
        labels=dim_labels,
        supports=dim_support,
        equal_value=b.points.equal_split_six,
        where="six_dimensions",
        what=WHAT["six"],
    )
    issues += six_issues
    leaf_keys = tuple(m.key for m in b.material_leaves)
    leaf_labels = {m.key: m.label for m in b.material_leaves}
    leaf_support = {m.key: m.support for m in b.material_leaves}
    material_parent = six_values.get("material") if six_values is not None else None
    if material_parent == 0:
        material_review = AllocationReview(mode=None, status="not_applicable")
        if draft.material.entries or draft.material.mode is not None:
            issues.append(
                issue(
                    "material_parent_zero_ignored",
                    "material",
                    t(
                        "input.material_parent_zero.message",
                        "物质生活给了 0 分，物质内部的细分不适用：已填写的细分不会被使用",
                    ),
                    t(
                        "input.material_parent_zero.impact",
                        "这几项细分只留在原始答案里，不进入画像",
                    ),
                    t(
                        "input.material_parent_zero.next",
                        "如果物质生活其实很重要，先把它的分数改成正数",
                    ),
                    severity="info",
                )
            )
    else:
        material_review, _material_values, material_issues = _allocation(
            pack,
            draft.material,
            keys=leaf_keys,
            kind="leaf",
            labels=leaf_labels,
            supports=leaf_support,
            equal_value=b.points.equal_split_material,
            where="material",
            what=WHAT["material"],
        )
        if material_parent is not None:
            issues += material_issues
        elif draft.material.entries:
            issues += [
                i
                for i in material_issues
                if i.code in ("legacy_laundry_key", "unknown_allocation_key")
            ]
    for item, review in (("six", six_review), ("material", material_review)):
        items.append(
            ItemStatus(
                item="six_dimensions" if item == "six" else "material",
                label=t("input.item.six_dimensions.label", "六维")
                if item == "six"
                else t("input.item.material.label", "物质四项"),
                answer_state="provided"
                if review.status in ("complete", "not_applicable")
                else "unanswered",
                support="supported",
                confirmation="pending",
                usage=POINT_USAGE,  # type: ignore[arg-type]
                note=t(
                    "input.item.points.note",
                    "陈述的相对重要程度，不是开支比例或预算；精确分数参与计算，显示值只供阅读",
                ),
            )
        )
    if draft.six_dimensions.mode == "equal_split":
        summary.append(
            t("input.summary.equal_split_six", "六维选择了平均分配：每项 50/3 分（显示 16.7）")
        )

    # 健康问卷
    hq = b.health_questionnaire
    health_items: list[HealthReviewItem] = []
    if draft.health.questionnaire_version != hq.version:
        issues.append(
            issue(
                "health_questionnaire_version_mismatch",
                "health.questionnaire_version",
                t(
                    "input.health_version_mismatch.message",
                    "健康回答按问卷 {old} 填写，现在的问卷是 {new}",
                    old=Raw(draft.health.questionnaire_version),
                    new=hq.version,
                ),
                t("input.health_version_mismatch.impact", "旧题目的答案不能改读成新题目的答案"),
                t("input.health_version_mismatch.next", "按当前问卷重新回答"),
            )
        )
    answers = {a.question_id: a.choice for a in draft.health.items}
    for qid in answers:
        if qid not in {q.question_id for q in hq.questions}:
            issues.append(
                issue(
                    "health_question_unknown",
                    f"health.items.{qid}",
                    t(
                        "input.health_question_unknown.message",
                        "问卷里没有题目 {qid}",
                        qid=Raw(qid),
                    ),
                    t("input.health_question_unknown.impact", "这一题不会被使用"),
                    t("input.health_question_unknown.next", "按当前问卷回答"),
                )
            )
    labels = {o.option_id: o.label for o in hq.options}
    # E3-A 收尾 F4：同一题收到不止一个答案（包括两个相同的答案）一律拒绝，不按最后一个算、不替用户选
    given: dict[str, list[tuple[str, Any]]] = {}
    for a in draft.health.items:
        given.setdefault(a.question_id, []).append(
            ("未答", Sub("input.health.unanswered_short", "未答"))
            if a.choice is None
            else (labels[a.choice], Ent("health_option", a.choice, labels[a.choice]))
            if a.choice in labels
            else (a.choice, Raw(a.choice))
        )
    duplicated = {qid for qid, got in given.items() if len(got) > 1}
    for qid in sorted(duplicated):
        issues.append(
            issue(
                "health_answer_duplicated",
                f"health.items.{qid}",
                t(
                    "input.health_answer_duplicated.message",
                    "健康题 {qid} 收到了 {n} 个答案（{choices}）：同一题只能有一个答案，系统不替你选，也不按最后一个算",
                    qid=Raw(qid),
                    n=len(given[qid]),
                    choices=Join(tuple(p for _zh, p in sorted(given[qid], key=lambda x: x[0]))),
                ),
                t("input.health_answer_duplicated.impact", "这一题的倾向不能确定，画像不能确认"),
                t(
                    "input.health_answer_duplicated.next",
                    "只保留一个答案（拿不准可以选“暂不确定”）",
                ),
            )
        )
    for q in hq.questions:
        question_text = label("health_question", q.question_id, q.text, "text")
        if q.question_id in duplicated:
            health_items.append(
                HealthReviewItem(
                    question_id=q.question_id,
                    text=question_text,
                    answer_state="unanswered",
                    option=None,
                    option_label=None,
                    meaning=t(
                        "input.health.meaning.duplicated",
                        "这一题收到了多个答案：确认前请只保留一个（系统不替你选）",
                    ),
                )
            )
            continue
        choice = answers.get(q.question_id)
        meaning, _note = health_meaning(pack, q.question_id, choice)
        state = "unanswered" if choice is None else ("unsure" if choice == "unsure" else "provided")
        health_items.append(
            HealthReviewItem(
                question_id=q.question_id,
                text=question_text,
                answer_state=state,  # type: ignore[arg-type]
                option=choice if state == "provided" else None,  # type: ignore[arg-type]
                option_label=label("health_option", choice, labels[choice])
                if state == "provided"
                else None,
                meaning=meaning,
            )
        )
    unclear = [h for h in health_items if h.answer_state != "provided"]
    if unclear:
        summary.append(
            t(
                "input.summary.health_unclear",
                "健康问卷有 {n} 题没有明确倾向（未答或暂不确定）：照实保留，分身会被告知没有这几项的明确指令",
                n=len(unclear),
            )
        )
    items.append(
        ItemStatus(
            item="health",
            label=t("input.item.health.label", "健康风险应对（独立问卷）"),
            answer_state="provided"
            if not unclear
            else ("unanswered" if len(unclear) == len(health_items) else "provided"),
            support="partially_supported",
            confirmation="pending",
            usage=("planning",),
            note=t(
                "input.item.health.note",
                "不占六维 100 分，不是第七个评分权重；只供分身做计划，不改变疾病规则、账目或满足感；题目是暂定文案",
            ),
        )
    )

    # 自由文字
    text = draft.free_text.text if draft.free_text.state == "provided" else None
    chars = len(text or "")
    if chars > 2000:
        issues.append(
            issue(
                "free_text_too_long",
                "free_text.text",
                t(
                    "input.free_text_too_long.message",
                    "补充文字有 {n} 个字符，超过 2,000 的上限",
                    n=chars,
                ),
                t("input.free_text_too_long.impact", "系统不会自动截断，超出部分不能静默丢弃"),
                t("input.free_text_too_long.next", "自己删减到 2,000 字符以内"),
            )
        )
    items.append(
        ItemStatus(
            item="free_text",
            label=t("input.item.free_text.label", "可选补充文字"),
            answer_state=draft.free_text.state,
            support="partially_supported",
            confirmation="pending",
            usage=("planning",),
            note=t(
                "input.item.free_text.note",
                "只供分身做计划参考；不会自动改分数、现金、资格或硬要求，本轮不用 LLM 提取",
            ),
        )
    )

    # 新问卷不再收集硬要求与属性偏好：还有就阻断（旧值仍会影响模拟，不能藏起来），按迁移说明清空后重新审阅
    from product.migration import removed_params

    for section in ("hard_requirements", "attribute_preferences"):
        rows = removed_params(pack, draft, section)
        if not rows:
            continue
        issues.append(
            issue(
                "removed_section_present",
                section,
                t(
                    "input.removed_section.message",
                    "这一版问卷不再收集“{section}”，草稿里还有 {n} 条：{items}",
                    section=REMOVED_SECTION[section],
                    n=len(rows),
                    items=Join(rows),
                ),
                t(
                    "input.removed_section.impact",
                    "它们会改变模拟，系统不悄悄沿用、也不悄悄丢掉；画像暂时不能确认",
                ),
                t(
                    "input.removed_section.next",
                    "看过草稿的迁移说明后同意清空这些旧答案，再重新审阅",
                ),
            )
        )
    # 资格
    known = {a.ability_id for a in b.abilities}
    abilities: list[str] = []
    # E3-A 收尾 F4：同一资格出现不止一次（状态冲突或完全相同）一律拒绝，不按“出现一次已提供就算持有”处理
    claimed: dict[str, list[tuple[str, Any]]] = {}
    state_text = {
        "provided": Sub("input.ability.state.provided", "已提供"),
        "unsure": Sub("input.ability.state.unsure", "暂不确定"),
        "indifferent": Sub("input.ability.state.indifferent", "无所谓"),
        "unanswered": Sub("input.ability.state.unanswered", "未答"),
    }
    for c in draft.abilities:
        said = state_text.get(c.state)
        claimed.setdefault(c.ability_id, []).append(
            (c.state, Raw(c.state)) if said is None else (said.zh, said)
        )
    repeated = {aid for aid, got in claimed.items() if len(got) > 1}
    for aid in sorted(repeated):
        issues.append(
            issue(
                "ability_duplicated",
                f"abilities.{aid}",
                t(
                    "input.ability_duplicated.message",
                    "资格 {aid} 出现了 {n} 次（{states}）：系统不替你判断持不持有",
                    aid=Raw(aid),
                    n=len(claimed[aid]),
                    states=Join(tuple(p for _zh, p in sorted(claimed[aid], key=lambda x: x[0]))),
                ),
                t("input.ability_duplicated.impact", "这项资格不能进入开局，画像不能确认"),
                t(
                    "input.ability_duplicated.next",
                    "只保留一条：持有就选“已提供”，不确定就选“暂不确定”",
                ),
            )
        )
    for c in draft.abilities:
        if c.ability_id in repeated:
            continue
        if c.ability_id not in known:
            issues.append(
                issue(
                    "ability_unknown",
                    f"abilities.{c.ability_id}",
                    t(
                        "input.ability_unknown.message",
                        "本版没有资格 {aid} 的定义",
                        aid=Raw(c.ability_id),
                    ),
                    t("input.ability_unknown.impact", "这项资格不会进入开局"),
                    t("input.ability_unknown.next", "只确认能力清单里列出的资格"),
                )
            )
        elif c.state == "provided":
            abilities.append(c.ability_id)
    from product.qualifications import review_qualifications

    qualification_review, more, held = review_qualifications(
        pack, catalog, draft, claimed=tuple(abilities), concept_id=concept_id
    )
    issues += more
    abilities = list(held)
    items.append(
        ItemStatus(
            item="abilities",
            label=t("input.item.abilities.label", "已确认的资格"),
            answer_state="provided" if abilities else "unanswered",
            support="supported",
            confirmation="pending",
            usage=("qualification", "start_state"),
            note=t(
                "input.item.abilities.note.v2",
                "只来自你回答的最高学历、职业证书与相关经验（或兼容的逐项资格确认），不从职业称呼、自由文字或搜索词自动授予",
            ),
        )
    )
    from product.interests import Term, review_one

    leisure_terms = tuple(
        Term(term.key, term.label, term.aliases) for term in b.interests.leisure_categories
    )
    cuisine_terms = tuple(Term(term.key, term.label, term.aliases) for term in b.interests.cuisines)
    leisure_review, more, _ = review_one(
        "leisure",
        draft.leisure_interest.state,
        draft.leisure_interest.text,
        tuple(draft.leisure_interest.selected),
        leisure_terms,
    )
    issues += more
    taste_review, more, _ = review_one(
        "taste", draft.taste.state, draft.taste.text, tuple(draft.taste.selected), cuisine_terms
    )
    issues += more
    from product.interests import KIND_LABEL

    for key, kind, r in (
        ("leisure_interest", "leisure", leisure_review),
        ("taste", "taste", taste_review),
    ):
        items.append(
            ItemStatus(
                item=key,
                label=_say(KIND_LABEL[kind]),
                answer_state="provided"
                if r.state in ("described", "selected")
                else (
                    "unsure"
                    if r.state == "unsure"
                    else "unanswered"
                    if r.state == "unanswered"
                    else "provided"
                ),
                support="partially_supported",
                confirmation="pending",
                usage=("planning", "scoring") if r.selected else ("planning",),
                note=r.message_zh[:300],
            )
        )
    from product.interests import NOT_PERSONALIZED_ACK, needs_not_personalized_ack

    if needs_not_personalized_ack(leisure_review, taste_review):
        pending.append(NOT_PERSONALIZED_ACK)
    pending.append("ack:standard_start")
    confirmable = not any(i.severity == "error" for i in issues)
    if confirmable:
        summary.insert(
            0,
            t(
                "input.summary.confirmable",
                "答案可以确认：请核对下面的解释，并知悉标准开局与只留存的陈述",
            ),
        )
    review = ProfileReview(
        meta=pack.meta(sample),
        review_id=f"review.{draft.draft_id}.r{draft.revision}",
        draft=draft_ref(draft),
        confirmable=confirmable,
        cities=tuple(city_refs),
        occupation=occupation_review,
        age=age,
        money=money_review,
        six_dimensions=six_review,
        material=material_review,
        health=tuple(health_items),
        free_text_chars=chars,
        abilities=tuple(abilities),
        items=tuple(items),
        pending_confirmations=tuple(dict.fromkeys(pending)),
        issues=tuple(issues),
        summary_zh=tuple(summary),
        leisure_interest=leisure_review,
        taste=taste_review,
        qualifications=qualification_review,
    )
    return review


#: 新问卷不再收集的两类答案的名称
REMOVED_SECTION = {
    "hard_requirements": Sub("input.removed_section.hard_requirements", "硬要求"),
    "attribute_preferences": Sub(
        "input.removed_section.attribute_preferences", "品牌、菜系等属性偏好"
    ),
}


def _text_occupation(
    pack: DataPack, draft: UserInputDraft, issues: list[ProductIssue]
) -> OccupationReview:
    """兼容的文字描述：受控词表的别名匹配，再在少量候选里选中一个。"""
    match = match_occupation(
        pack, draft.occupation.raw_text if draft.occupation.state == "provided" else None
    )
    selected = draft.occupation.selected_candidate
    if match.status == "not_provided":
        issues.append(
            issue(
                "occupation_missing",
                "occupation.raw_text",
                t("input.occupation_missing.message", "还没有描述你主要做什么工作"),
                t(
                    "input.occupation_missing.impact",
                    "开局岗位要按当地目录匹配，没有职业就不能生成开局",
                ),
                t("input.occupation_missing.next", "用一两句话描述你的主要工作内容"),
            )
        )
    elif match.status == "unknown":
        issues.append(
            issue(
                "occupation_unknown",
                "occupation.raw_text",
                match.message_zh,
                t("input.occupation_unknown.impact", "开局岗位无法匹配，不能生成开局"),
                t("input.occupation_unknown.next", "换一种说法描述主要职责"),
            )
        )
    elif selected is None:
        issues.append(
            issue(
                "occupation_needs_confirmation",
                "occupation.selected_candidate",
                match.message_zh,
                t("input.occupation_needs_confirmation.impact", "职业还没有确认，不能生成开局"),
                t("input.occupation_needs_confirmation.next", "在候选里选出你主要做的那一种"),
            )
        )
    elif selected not in {c.candidate_id for c in match.candidates}:
        issues.append(
            issue(
                "occupation_selection_invalid",
                "occupation.selected_candidate",
                t(
                    "input.occupation_selection_invalid.message",
                    "选中的职业不在这段描述的候选里（描述改过之后要重新选）",
                ),
                t("input.occupation_selection_invalid.impact", "职业还没有确认"),
                t("input.occupation_selection_invalid.next", "按当前描述重新选择候选"),
            )
        )
    return OccupationReview(
        status=match.status,  # type: ignore[arg-type]
        raw_text=draft.occupation.raw_text,
        candidates=match.candidates,
        selected=selected if selected in {c.candidate_id for c in match.candidates} else None,
        message_zh=match.message_zh,
    )


def _catalog_occupation(
    pack: DataPack, catalog: LoadedCatalog | None, draft: UserInputDraft, issues: list[ProductIssue]
) -> OccupationReview:
    """从输入目录直接选中的职业：只认目录里的编号与这一版目录的身份；不需要、也不保存任何文字。"""
    o = draft.occupation

    def refused(code: str, field: str, message: str, impact: str, action: str) -> OccupationReview:
        issues.append(issue(code, field, message, impact, action))
        return OccupationReview(status="not_provided", mode="catalog", message_zh=message[:400])

    if catalog is None:
        return refused(
            "occupation_catalog_unavailable",
            "occupation.mode",
            t(
                "input.occupation.catalog_unavailable.message",
                "这个资料包没有职业目录，不能按目录选职业",
            ),
            t("input.occupation.catalog_unavailable.impact", "职业还没有确认，不能生成开局"),
            t("input.occupation.catalog_unavailable.next", "改用文字描述你的工作"),
        )
    if o.state != "provided" or o.selected_candidate is None:
        return refused(
            "occupation_missing",
            "occupation.selected_candidate",
            t("input.occupation.catalog_missing.message", "还没有从职业目录里选你主要做的工作"),
            t(
                "input.occupation_missing.impact",
                "开局岗位要按当地目录匹配，没有职业就不能生成开局",
            ),
            t(
                "input.occupation.catalog_missing.next",
                "按分类浏览或搜索职业目录，选中一个",
            ),
        )
    if (
        o.catalog_fingerprint != catalog.fingerprint
        or o.catalog_version != catalog.decl.catalog_version
    ):
        return refused(
            "occupation_catalog_expired",
            "occupation.catalog_fingerprint",
            t(
                "input.occupation.catalog_expired.message",
                "这个职业是按另一版职业目录选的，目录已经更新",
            ),
            t(
                "input.occupation.catalog_expired.impact",
                "旧目录里的选择不能直接沿用，职业还没有确认",
            ),
            t("input.occupation.catalog_expired.next", "按现在的目录重新选一次职业"),
        )
    concept = pack.occupation(o.selected_candidate)
    if concept is None or o.selected_candidate not in catalog.occupations:
        return refused(
            "occupation_selection_invalid",
            "occupation.selected_candidate",
            t(
                "input.occupation.catalog_unknown.message",
                "职业编号 {ref} 不在职业目录里：系统不替换成别的职业",
                ref=Raw(o.selected_candidate),
            ),
            t("input.occupation_selection_invalid.impact", "职业还没有确认"),
            t("input.occupation.catalog_unknown.next", "从目录列出的职业里选"),
        )
    name = label("occupation", concept.concept_id, concept.label)
    return OccupationReview(
        status="catalog_selected",
        mode="catalog",
        candidates=(
            OccupationCandidate(
                candidate_id=concept.concept_id, label=name, duties=duties(concept)
            ),
        ),
        selected=concept.concept_id,
        message_zh=t(
            "input.occupation.catalog_selected",
            "你从职业目录选了“{label}”：请核对下面的职责是不是你主要做的工作",
            label=Ent("occupation", concept.concept_id, concept.label),
        ),
    )


def _money(pack: DataPack, draft: UserInputDraft) -> tuple[MoneyReview, list[ProductIssue]]:
    c = draft.cash
    if c.state != "provided" or c.amount_text is None:
        return (
            MoneyReview(
                status="missing", meaning=t("input.money.meaning.short", "可动用的流动资金")
            ),
            [
                issue(
                    "cash_missing",
                    "cash.amount_text",
                    t("input.cash_missing.message", "还没有填写开始时可用于生活的钱（没填不是 0）"),
                    t("input.cash_missing.impact", "开局现金未知，不能生成开局"),
                    t("input.cash_missing.next", "填写金额、币种与金额时期"),
                )
            ],
        )
    issues: list[ProductIssue] = []
    digits = pack.body.money_basis.minor_unit_digits
    minor = None
    try:
        minor = parse_money_text(c.amount_text, minor_digits=digits)
    except ExactParseError as exc:
        issues.append(
            issue(
                exc.code,
                "cash.amount_text",
                str(exc),
                t("input.money.parse_error.impact", "开局现金不能确定：系统不会猜你想填的金额"),
                exc.next_action or t("input.money.parse_error.next", "填写非负金额，最多两位小数"),
            )
        )
    option = next(
        (
            m
            for m in pack.body.money_bases
            if m.currency == c.currency and m.price_period == c.price_period
        ),
        None,
    )
    if option is None:
        issues.append(
            issue(
                "money_conversion_missing",
                "cash",
                t(
                    "input.money_conversion_missing.message",
                    "{currency} / {period} 的金额在本版没有合法的换算依据",
                    currency=Raw(c.currency)
                    if c.currency
                    else Sub("input.money.no_currency", "（未写币种）"),
                    period=Raw(c.price_period)
                    if c.price_period
                    else Sub("input.money.no_period", "（未写时期）"),
                ),
                t(
                    "input.money_conversion_missing.impact",
                    "不能把这个金额直接改标签成 2024 年当地美元——那会改变它的含义",
                ),
                t(
                    "input.money_conversion_missing.next",
                    "改用能力清单支持的币种与时期（本版：2024 年美元），或等待真实换算资料接入（E3-B/C）",
                ),
            )
        )
    review = MoneyReview(
        status="convertible" if option is not None and minor is not None else "blocked",
        amount_text=c.amount_text,
        currency=c.currency,
        price_period=c.price_period,
        meaning=t("input.money.meaning", "可动用的流动资金（不是总资产、信用额度或月薪）"),
        conversion_rule=None if option is None else option.conversion_rule,
        converted=None
        if option is None or minor is None
        else ProductMoney(
            amount_minor=minor,
            currency=option.currency,
            numeraire_id=option.numeraire_id,
            display=money_display(minor, digits),
        ),
    )
    return review, issues


# ---------------------------------------------------------------------- 确认


def confirm_profile(
    pack: DataPack,
    draft: UserInputDraft,
    *,
    person_ref: str,
    acknowledged: Iterable[str],
    sample: SampleLabel | None = None,
) -> ConfirmedProfile:
    """用户确认：审阅没有阻断问题，并且用户知悉了全部待确认事项（标准开局、只留存的陈述……）。否则拒绝，不替用户确认。"""
    review = review_draft(pack, draft)
    blocking = tuple(i for i in review.issues if i.severity == "error")
    missing_ack = [k for k in review.pending_confirmations if k not in set(acknowledged)]
    if missing_ack:
        blocking += tuple(
            issue(
                "acknowledgement_missing",
                k,
                t("input.ack_missing.message", "还没有确认：{what}", what=_ack_text(k)),
                t("input.ack_missing.impact", "没有知悉这项说明就不能确认画像"),
                t("input.ack_missing.next", "阅读说明后勾选确认"),
            )
            for k in missing_ack
        )
    if blocking:
        raise ConfirmationRefusedError(blocking)
    b = pack.body
    digits = b.points.display_decimals
    six = {ln.key: Fraction(ln.exact) for ln in review.six_dimensions.lines if ln.exact is not None}
    concept = pack.occupation(review.occupation.selected or "")
    assert concept is not None and review.money.converted is not None and review.age is not None
    catalog_mode = review.occupation.status == "catalog_selected"
    assert catalog_mode or draft.occupation.raw_text is not None
    material = (
        ConfirmedMaterial(status="not_applicable_parent_zero")
        if review.material.status == "not_applicable"
        else ConfirmedMaterial(
            status="allocated",
            allocation=ConfirmedAllocation(
                mode=review.material.mode or "manual",
                points={ln.key: ln.exact for ln in review.material.lines if ln.exact is not None},  # type: ignore[misc]
                display={
                    ln.key: ln.display for ln in review.material.lines if ln.display is not None
                },  # type: ignore[misc]
            ),
        )
    )
    hq = b.health_questionnaire
    answers = {a.question_id: a.choice for a in draft.health.items}
    health = ConfirmedHealth(
        questionnaire_version=hq.version,
        status=hq.status,
        answers=tuple(
            ConfirmedHealthAnswer(
                question_id=h.question_id,
                question_text=h.text,
                answer_state=h.answer_state,
                option=h.option,
                option_label=h.option_label,
                meaning=health_meaning(pack, h.question_id, answers.get(h.question_id))[0],
            )
            for h in review.health
        ),
    )
    sources = tuple(
        FieldSource(
            field=f,
            evidence=(
                EvidenceRef(kind="form_field", ref=f"draft:{draft.draft_id}#{f}"),
                *(e for e in draft.evidence if e.ref.startswith(f)),
            ),
        )
        for f in (
            "cities",
            "occupation",
            "age",
            "cash",
            "six_dimensions",
            "material",
            "health",
            "free_text",
            "education",
            "certificates",
            "experience",
            "abilities",
            "leisure_interest",
            "taste",
        )
    )
    from product.interests import confirmed as confirmed_interests

    fields = dict(
        meta=pack.meta(sample),
        confirmed_id=f"confirmed.{draft.draft_id}.r{draft.revision}",
        person_ref=person_ref,
        draft=review.draft,
        review_id=review.review_id,
        confirmation_source="user_form",
        cities=review.cities,
        occupation=ConfirmedOccupation(
            concept_id=concept.concept_id,
            label=label("occupation", concept.concept_id, concept.label),
            duties=duties(concept),
            raw_text=None if catalog_mode else draft.occupation.raw_text,
            language=draft.occupation.language,
            match_status=review.occupation.status,  # type: ignore[arg-type]
            catalog_version=draft.occupation.catalog_version if catalog_mode else None,
            catalog_fingerprint=draft.occupation.catalog_fingerprint if catalog_mode else None,
            internal_mappings=tuple(
                OccupationMapping(
                    country=m.country,
                    scheme=m.scheme,
                    version=m.version,
                    code=m.code,
                    basis=label("occupation", concept.concept_id, m.basis, f"basis.{i}"),
                )
                for i, m in enumerate(concept.mappings)
            ),
        ),
        age_years=review.age,
        money=ConfirmedMoney(
            amount_text=draft.cash.amount_text,  # type: ignore[arg-type]
            currency=draft.cash.currency,  # type: ignore[arg-type]
            price_period=draft.cash.price_period,  # type: ignore[arg-type]
            meaning="liquid_funds_v1",
            conversion_rule=review.money.conversion_rule,  # type: ignore[arg-type]
            converted=review.money.converted,
        ),
        six_dimensions=ConfirmedAllocation(
            mode=review.six_dimensions.mode or "manual",
            points={k: canonical(v) for k, v in six.items()},
            display={k: display(v, digits) for k, v in six.items()},
        ),
        material=material,
        health=health,
        free_text=draft.free_text.text if draft.free_text.state == "provided" else None,
        abilities=review.abilities,
        sources=sources,
        interests=confirmed_interests(
            review.leisure_interest,
            review.taste,
            matching_note(pack),
            {
                "leisure": {
                    term.key: term.label for term in pack.body.interests.leisure_categories
                },
                "taste": {term.key: term.label for term in pack.body.interests.cuisines},
            },
        ),
        qualifications=None
        if review.qualifications is None
        else confirmed_qualifications(review.qualifications),
    )
    unsealed = ConfirmedProfile.model_construct(**fields, content_fingerprint="0" * 64)
    return ConfirmedProfile(**fields, content_fingerprint=unsealed.expected_fingerprint())


def _ack_text(key: str) -> Sub | Raw:
    """一项待确认的说明（显示参数）。"""
    if key == "ack:standard_start":
        return Sub(
            "input.ack.standard_start",
            "这是一次从健康、没有已有物品、没有在学课程开始的简化情景（现实中的家具与病史暂未纳入）",
        )
    if key.startswith("ack:retained:"):
        return Sub(
            "input.ack.retained",
            "陈述 {ref} 本版不评分、不保证执行，只留存原文",
            (("ref", Raw(key.split(":", 2)[2])),),
        )
    if key == "ack:interests_not_personalized":
        return Sub(
            "input.ack.interests_not_personalized",
            "本次不按具体兴趣或口味个性化的部分按中性规则评价（未答、暂不确定、没有固定爱好或通用评价各自记下）；"
            "写了但本版不支持的描述只留存原文",
        )
    return Raw(key)


def _say(s: Sub) -> str:
    """把一段固定说明（``Sub``）作为整段显示文字生成。"""
    return t(s.key, s.zh, **dict(s.params))


def ack_display(key: str) -> str:
    """一项待确认事项的整段说明（显示附层的知悉项说明）。与确认被拒时“还没有确认：……”用的是同一个来源 ``_ack_text``，不另写一套；
    认不出的编号只给编号本身（没有显示绑定，英文照实缺失）。"""
    s = _ack_text(key)
    return _say(s) if isinstance(s, Sub) else key


def confirmation_problems(
    pack: DataPack, confirmed: ConfirmedProfile, draft: UserInputDraft | None = None
) -> list[ProductIssue]:
    """一份确认画像现在还能不能用：内容身份、版本、资料包，以及（给了当前草稿时）草稿有没有改过。"""
    out: list[ProductIssue] = []
    if confirmed.content_fingerprint != confirmed.expected_fingerprint():
        out.append(
            issue(
                "confirmation_tampered",
                "content_fingerprint",
                t(
                    "input.confirmation_tampered.message",
                    "确认画像的内容与它记下的身份不一致（可能被改写过）",
                ),
                t("input.confirmation_tampered.impact", "不能用这份确认运行"),
                t("input.confirmation_tampered.next", "重新审阅并确认"),
            )
        )
    if confirmed.meta.schema_version != PRODUCT_SCHEMA_VERSION:
        out.append(
            issue(
                "version_changed",
                "meta.schema_version",
                t("input.schema_version_changed.message", "确认画像的结构版本与现在不同"),
                t("input.schema_version_changed.impact", "旧结构的确认不能直接沿用"),
                t("input.schema_version_changed.next", "重新确认"),
            )
        )
    out += _version_issues(pack, confirmed.meta.model_dump(mode="json"), "confirmed")
    if draft is not None and draft_fingerprint(draft) != confirmed.draft.draft_fingerprint:
        out.append(
            issue(
                "confirmation_invalidated_by_edit",
                "draft",
                t(
                    "input.confirmation_invalidated.message",
                    "原始答案在确认之后改过：旧确认已经失效",
                ),
                t("input.confirmation_invalidated.impact", "改过的答案还没有确认，不能运行"),
                t("input.confirmation_invalidated.next", "按修改后的答案重新审阅并确认"),
            )
        )
    return out
