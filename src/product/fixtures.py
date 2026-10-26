"""E3-A 的合成用户草稿（公共样例、测试与证据共用；**全部是合成人物**，不是任何真实的人）。

每个草稿只是一份 ``UserInputDraft``：答案照用户会填的样子写（明确 0、未答、无所谓、不确定照实区分）。审阅、确认、编译与运行都走
正式入口，这里不预先写任何“正确结果”。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.product_contracts import (
    AbilityClaim,
    AgeAnswer,
    AllocationAnswer,
    AttributePreferenceAnswer,
    CashAnswer,
    CertificatesAnswer,
    CitiesAnswer,
    EducationAnswer,
    ExperienceAnswer,
    ExperienceEntry,
    FreeTextAnswer,
    HardRequirementAnswer,
    HealthAnswers,
    HealthItemAnswer,
    LeisureInterestAnswer,
    OccupationAnswer,
    PointEntry,
    SampleLabel,
    TasteAnswer,
    UserInputDraft,
)
from product.pack import DataPack
from product.workspace import WORKSPACE_FILES as WORKSPACE_FILES
from product.workspace import prepare_workspace as _prepare

K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"
HEALTH_IDS = (
    "hc_more_pay_more_sick_risk",
    "hc_reserve_for_health_costs",
    "hc_wait_for_clear_risk_info",
)
NORMAL_SIX = {
    "material": "30",
    "safety": "20",
    "opportunity": "20",
    "leisure": "10",
    "environment": "10",
    "commute": "10",
}
NORMAL_MATERIAL = {
    "housing_condition": "25",
    "food_life": "25",
    "daily_convenience": "30",
    "personal_enjoyment": "20",
}
NORMAL_TEXT = "我在写字楼做全职文员。下班以后不想再把很多时间花在洗衣服、打扫屋子上，愿意为省事花一些钱；但也不想因为花钱让自己手头太紧。"


def points(values: dict[str, str | None]) -> dict[str, PointEntry]:
    """``"0"``、``"16.5"`` 等是已提供的数值；``None`` 是未答；``"indifferent"`` / ``"unsure"`` 是对应状态。"""
    out: dict[str, PointEntry] = {}
    for k, v in values.items():
        if v is None:
            out[k] = PointEntry(state="unanswered")
        elif v in ("indifferent", "unsure"):
            out[k] = PointEntry(state=v)  # type: ignore[arg-type]
        else:
            out[k] = PointEntry(state="provided", value_text=v)
    return out


def health(choices: dict[str, str | None] | None = None) -> HealthAnswers:
    choices = choices or {
        "hc_more_pay_more_sick_risk": "disagree",
        "hc_reserve_for_health_costs": "agree",
        "hc_wait_for_clear_risk_info": "unsure",
    }
    return HealthAnswers(
        questionnaire_version="e3a-health-coping-q-v0.1-draft",
        items=tuple(
            HealthItemAnswer(question_id=q, choice=choices.get(q))  # type: ignore[arg-type]
            for q in HEALTH_IDS
        ),
    )


def make_draft(
    pack: DataPack,
    *,
    draft_id: str = "draft.u501",
    revision: int = 1,
    cities: tuple[str, ...] | None = None,
    occupation_text: str | None = "我在写字楼做全职文员，平时处理文件和表格",
    selected: str | None = "occ.office_clerk",
    age: int | None = 35,
    cash: tuple[str | None, str | None, str | None] = ("3000", "USD", "2024"),
    six: dict[str, str | None] | None = None,
    six_mode: str | None = "ratings_normalized_v1",
    material: dict[str, str | None] | None = None,
    material_mode: str | None = "ratings_normalized_v1",
    health_choices: dict[str, str | None] | None = None,
    free_text: str | None = NORMAL_TEXT,
    attributes: tuple[dict[str, Any], ...] = (),
    abilities: tuple[str, ...] = (),
    hard_requirements: tuple[dict[str, Any], ...] = (),
    sample: SampleLabel | None = None,
    leisure: dict[str, Any] | None = None,
    taste: dict[str, Any] | None = None,
    occupation_mode: str = "text",
    education: str | None = None,
    certificates: tuple[str, ...] | None = None,
    experience: tuple[tuple[str, int], ...] | None = None,
) -> UserInputDraft:
    """一份合成草稿；默认就是主故事里的 u501（全职文员、看重省事、健康问卷一题暂不确定）。

    v3.5：六维与物质四项默认是独立评分（``ratings_normalized_v1``），评分就是 ``NORMAL_SIX`` / ``NORMAL_MATERIAL``——它们正好合计
    100，换算出的权重与原来的百分比逐项相同（E1 的输入不变）。``six_mode`` / ``material_mode`` 写 ``manual`` / ``equal_split`` 是旧的
    百分比分配（只给读回旧对象的测试用；新问卷审阅会阻断）。

    v3.4：新问卷不再收集硬要求与属性偏好，默认两者都为空（``hard_requirements`` / ``attributes`` 只给“旧草稿”的测试用）。
    ``occupation_mode = "catalog"`` 时按资料包的输入目录选 ``selected``（不带文字）；``education`` 是学历选项编号（或
    ``"unsure"``），``certificates`` / ``experience`` 不给就是未答，给空元组就是明确的“没有”。"""
    amount, currency, period = cash
    if cities is None:  # 默认：资料包里的全部城市（合成资料包就是 K51、M52）
        cities = tuple(c.city_ref for c in pack.body.cities)
    if occupation_mode == "catalog":
        from product.input_catalog import input_catalog

        catalog = input_catalog(pack)
        assert catalog is not None, "这个资料包没有输入目录"
        occupation = OccupationAnswer(
            state="provided" if selected else "unanswered",
            mode="catalog",
            selected_candidate=selected,
            catalog_version=catalog.decl.catalog_version,
            catalog_fingerprint=catalog.fingerprint,
        )
    else:
        occupation = OccupationAnswer(
            state="provided" if occupation_text else "unanswered",
            raw_text=occupation_text,
            selected_candidate=selected,
        )
    return UserInputDraft(
        meta=pack.meta(sample),
        draft_id=draft_id,
        revision=revision,
        cities=CitiesAnswer(state="provided" if cities else "unanswered", city_refs=cities),
        occupation=occupation,
        age=AgeAnswer(state="provided" if age is not None else "unanswered", value=age),
        cash=CashAnswer(
            state="provided" if amount is not None else "unanswered",
            amount_text=amount,
            currency=currency,
            price_period=period,
        ),
        six_dimensions=AllocationAnswer(
            mode=six_mode,  # type: ignore[arg-type]
            entries={} if six_mode == "equal_split" else points(NORMAL_SIX if six is None else six),
        ),
        material=AllocationAnswer(
            mode=material_mode,  # type: ignore[arg-type]
            entries={}
            if material_mode in ("equal_split", None)
            else points(NORMAL_MATERIAL if material is None else material),
        ),
        health=health(health_choices),
        free_text=FreeTextAnswer(state="provided" if free_text else "unanswered", text=free_text),
        attribute_preferences=tuple(AttributePreferenceAnswer(**a) for a in attributes),
        abilities=tuple(AbilityClaim(ability_id=a, state="provided") for a in abilities),
        hard_requirements=tuple(HardRequirementAnswer(**h) for h in hard_requirements),
        leisure_interest=LeisureInterestAnswer(**(leisure or {})),
        taste=TasteAnswer(**(taste or {})),
        education=EducationAnswer()
        if education is None
        else EducationAnswer(state="unsure")
        if education == "unsure"
        else EducationAnswer(state="provided", option_id=education),
        certificates=CertificatesAnswer()
        if certificates is None
        else CertificatesAnswer(state="provided", selected=certificates),
        experience=ExperienceAnswer()
        if experience is None
        else ExperienceAnswer(
            state="provided",
            entries=tuple(ExperienceEntry(concept_id=c, years=y) for c, y in experience),
        ),
    )


def prepare_workspace(work: Path, repo: Path) -> Path:
    """把资料包与基础样例按原相对路径复制进 ``work``（只复制，不修改）。正式实现在 ``product.workspace``（E3C 提取，生产服务与测试
    共用）；这里沿用旧的返回值（工作目录本身）。"""
    _prepare(work, repo)
    return work
