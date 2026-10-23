"""E3-A 的合成用户草稿（公共样例、测试与证据共用；**全部是合成人物**，不是任何真实的人）。

每个草稿只是一份 ``UserInputDraft``：答案照用户会填的样子写（明确 0、未答、无所谓、不确定照实区分）。审阅、确认、编译与运行都走
正式入口，这里不预先写任何“正确结果”。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from common.product_contracts import (
    AbilityClaim,
    AgeAnswer,
    AllocationAnswer,
    AttributePreferenceAnswer,
    CashAnswer,
    CitiesAnswer,
    FreeTextAnswer,
    HardRequirementAnswer,
    HealthAnswers,
    HealthItemAnswer,
    OccupationAnswer,
    PointEntry,
    SampleLabel,
    UserInputDraft,
)
from product.pack import DataPack

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
    "housing_space": "25",
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
    cities: tuple[str, ...] = (K51, M52),
    occupation_text: str | None = "我在写字楼做全职文员，平时处理文件和表格",
    selected: str | None = "occ.office_clerk",
    age: int | None = 35,
    cash: tuple[str | None, str | None, str | None] = ("3000", "USD", "2024"),
    six: dict[str, str | None] | None = None,
    six_mode: str | None = "manual",
    material: dict[str, str | None] | None = None,
    material_mode: str | None = "manual",
    health_choices: dict[str, str | None] | None = None,
    free_text: str | None = NORMAL_TEXT,
    attributes: tuple[dict[str, Any], ...] = (),
    abilities: tuple[str, ...] = (),
    hard_requirements: tuple[dict[str, Any], ...] = (
        {
            "requirement_id": "hr.commute",
            "option_id": "housing.max_commute_roundtrip",
            "parameter_value": 60,
        },
    ),
    sample: SampleLabel | None = None,
) -> UserInputDraft:
    """一份合成草稿；默认就是主故事里的 u501（全职文员、看重省事、健康问卷一题暂不确定、通勤上限 60 分钟）。"""
    amount, currency, period = cash
    return UserInputDraft(
        meta=pack.meta(sample),
        draft_id=draft_id,
        revision=revision,
        cities=CitiesAnswer(state="provided" if cities else "unanswered", city_refs=cities),
        occupation=OccupationAnswer(
            state="provided" if occupation_text else "unanswered",
            raw_text=occupation_text,
            selected_candidate=selected,
        ),
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
    )


#: 一个自包含的“小仓库”需要的文件：资料包与它显式采用的产品协议基础样例（相对路径与仓库里相同）。场景在这样的目录里运行，编译产物与
#: 运行配置里的路径都是相对路径，结果不依赖目录放在哪里。
WORKSPACE_FILES = (
    "configs/product/e3a_synthetic_pack.yaml",
    "configs/life/synthetic_product.yaml",
    "configs/life/scoring_product.yaml",
    "configs/life/evaluation_product.yaml",
    "configs/life/city_facts_product.yaml",
)


def prepare_workspace(work: Path, repo: Path) -> Path:
    """把资料包与基础样例按原相对路径复制进 ``work``（只复制，不修改）。"""
    work.mkdir(parents=True, exist_ok=True)
    for rel in WORKSPACE_FILES:
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, dst)
    return work
