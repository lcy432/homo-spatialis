"""新旧严格等价（E3-A 验收，DECISIONS/0034）：同一组六维比例在旧七维路径与新六维路径上得到逐位相同的生活评价。

两条路径用**同一份**便利版城市目录、同一份第一年开局、同一份三年计划、同一组注入的疾病事件与同一个物质比例（30/30/40）：

* 旧：便利版协议 ``life-v1-e1-convenience`` + 旧七维 ``StatedLifeProfile``，点数 27/18/18/9/9/9 + 医疗 10（健康版本里医疗点数不进权重，
  有效权重 = 27/90 = 3/10 ……）；
* 新：E3-A 派生的产品协议 ``life-v1-e3a-product`` + 六维 ``ProductLifeProfile``，点数 30/20/20/10/10/10（有效权重 = 30/100 = 3/10 ……）。

逐月比较：有效权重、各维的权重 / 分项 / 贡献、日常生活分 B、总分 S、年均分、反馈的未量化额度与量化后的额度、交给下一年的额度；
只用 ``==``，不加容差，不比较显示小数。原始画像、偏好来源、权重换算记录（旧的有七维原始权重，新的只有六维）与各种指纹本来就不同，
单独列出，不算不等价。另做一组均分对照：旧 15×6 + 医疗 10 与新 50/3×6。
"""

from __future__ import annotations

import contextlib
import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from common.life_contracts import (
    IllnessEventSource,
    LifeProtocol,
    PersonYearState,
    StatedLifeProfile,
    YearAction,
    model_fingerprint,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import load_scoring_config, parse_scoring_config
from product.answers import confirm_profile, review_draft
from product.compile import compile_inputs
from product.fixtures import K51, make_draft, prepare_workspace
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
#: 注入的疾病事件（两条路径相同）：第 1 年第 3 月小病、第 2 年第 5 月大病。
INJECTED = [
    {
        "person_id": "u301",
        "repeat_index": 0,
        "year_index": 1,
        "month": 3,
        "severity": "minor",
        "note": "等价对照：第 1 年第 3 月小病",
    },
    {
        "person_id": "u301",
        "repeat_index": 0,
        "year_index": 2,
        "month": 5,
        "severity": "major",
        "note": "等价对照：第 2 年第 5 月大病",
    },
]
EMERGENCY_TREAT = {
    "minor": "treat_if_possible",
    "major": "treat_if_possible",
    "pause_order": ["k51.service.gym"],
}


def _plans() -> dict[int, list[dict[str, Any]]]:
    def whole(oid: str) -> dict[str, Any]:
        return {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}

    return {
        1: [
            whole("k51.housing.near"),
            whole("k51.food.fresh"),
            whole("k51.job.clerk"),
            whole("k51.service.gym"),
            whole("k51.service.chores"),
        ],
        2: [
            whole("k51.housing.far"),
            whole("k51.food.basic"),
            whole("k51.job.clerk"),
            {"opportunity_id": "k51.durable.convenience_pack", "quantity": 1, "month": 1},
        ],
        3: [
            whole("k51.housing.near"),
            whole("k51.food.fresh"),
            whole("k51.job.clerk"),
            whole("k51.service.gym"),
        ],
    }


def _with_events(protocol: LifeProtocol) -> LifeProtocol:
    src = IllnessEventSource.model_validate(
        {
            "mode": "injected",
            "stream_version": "e1-5c-illness-stream-v1",
            "note": "等价对照：注入事件",
            "injected": INJECTED,
        }
    )
    return protocol.model_copy(update={"illness_events": src})


def _life(protocol, state, catalog, spec, conditions, profile) -> list[LifeYearResult]:
    tax = binding_from_policy(_TAX[0])
    out: list[LifeYearResult] = []
    for year, items in _plans().items():
        action = YearAction.model_validate(
            {
                "person_id": state.person_id,
                "clone_id": state.clone_id,
                "year_index": year,
                "items": items,
                "minimum_fallback": ["housing", "food"],
                "emergency": {
                    **EMERGENCY_TREAT,
                    "pause_order": [
                        i["opportunity_id"]
                        for i in items
                        if i["opportunity_id"] == "k51.service.gym"
                    ],
                },
                "rationale": "等价对照：预先写好的计划",
            }
        )
        r = run_life_year(
            protocol=protocol,
            state=state,
            action=action,
            catalog=catalog,
            tax=tax,
            scoring=ScoringInputs(
                spec=spec, conditions=conditions, preference_input=profile, hard_requirements=()
            ),
        )
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        assert r.next_state is not None
        state = r.next_state
    return out


_TAX: list[Any] = []


def _months(r: LifeYearResult) -> list[dict[str, Any]]:
    assert r.score is not None
    rows = []
    for m in r.score.months:
        rows.append(
            {
                "month": m.month,
                "status": m.status,
                "u": m.u,
                "daily_life": m.daily_life,
                "dimensions": [
                    (d.dimension, d.weight, d.status, d.s, d.contribution) for d in m.dimensions
                ],
                "material_terms": [
                    (t.need, t.weight, t.status, t.s, t.contribution)
                    for d in m.dimensions
                    for t in d.material_terms
                ],
                "health": None
                if m.health is None
                else (m.health.state, m.health.value, m.health.gamma),
            }
        )
    return rows


def _year(r: LifeYearResult) -> dict[str, Any]:
    assert r.score is not None and r.score.health_weighting is not None
    return {
        "year": r.year_index,
        "effective_weights": r.score.health_weighting.effective_weights,
        "annual_mean": r.score.annual_mean,
        "annual_status": r.score.annual_status,
        "raw_quota": None if r.feedback.raw_quota is None else r.feedback.raw_quota.exact,
        "new_quota": None if r.feedback.new_quota is None else r.feedback.new_quota.exact,
        "next_energy": None if r.next_state is None else r.next_state.energy.level,
        "outcome_fingerprint": None
        if r.settlement.outcome is None
        else model_fingerprint(r.settlement.outcome),
        "months": _months(r),
    }


def compare_old_new(out: Path | None = None, *, work: Path | None = None) -> dict[str, Any]:
    work = Path(work or tempfile.mkdtemp(prefix="e3a-equivalence-"))
    prepare_workspace(work, REPO)
    with contextlib.chdir(work):
        pack = load_pack()
        base = pack.body.e1_base
        bundle, _ = load_life_bundle(Path(base.life_input), require_settlement_ready=True)
        scoring_old, _ = load_scoring_config(Path(base.scoring_config), bundle)
        catalog = bundle.catalog_for(K51)
        assert catalog is not None
        _TAX[:] = [bundle.tax_policy(catalog.tax_policy_id)]
        state: PersonYearState = bundle.initial_state("u301.k51.r0")  # type: ignore[assignment]
        cases = {
            "main_30_20_20_10_10_10": (
                {
                    "material": 27,
                    "safety": 18,
                    "healthcare": 10,
                    "opportunity": 18,
                    "leisure": 9,
                    "environment": 9,
                    "commute": 9,
                },
                {
                    "material": "30",
                    "safety": "20",
                    "opportunity": "20",
                    "leisure": "10",
                    "environment": "10",
                    "commute": "10",
                },
                "manual",
            ),
            "equal_split": (
                {
                    "material": 15,
                    "safety": 15,
                    "healthcare": 10,
                    "opportunity": 15,
                    "leisure": 15,
                    "environment": 15,
                    "commute": 15,
                },
                None,
                "equal_split",
            ),
        }
        report: dict[str, Any] = {"cases": {}, "identical": True}
        for name, (old_points, new_points, mode) in cases.items():
            old_profile = StatedLifeProfile.model_validate(
                {
                    **bundle.stated_profiles[0].model_dump(mode="json"),
                    "dimension_points": old_points,
                    "material_points": {
                        "housing_space": 30,
                        "fresh_food": 30,
                        "daily_convenience": 40,
                    },
                }
            )
            draft = make_draft(
                pack,
                draft_id=f"draft.u301.{name}",
                six=new_points,
                six_mode=mode,
                hard_requirements=(),
                free_text=None,
            )
            review = review_draft(pack, draft)
            confirmed = confirm_profile(
                pack, draft, person_ref="u301", acknowledged=review.pending_confirmations
            )
            compiled = compile_inputs(
                pack, confirmed, Path(f"equivalence/{name}"), repo_root=Path(".")
            )
            new_bundle, _ = load_life_bundle(
                compiled.paths["life_input"], require_settlement_ready=True
            )
            scoring_new, _ = load_scoring_config(compiled.paths["scoring_config"], new_bundle)
            new_profile = new_bundle.product_profiles[0]
            old_protocol = _with_events(bundle.protocol)
            new_protocol = _with_events(new_bundle.protocol)
            old = _life(
                old_protocol,
                state,
                catalog,
                scoring_old.spec,
                scoring_old.conditions_for(K51),
                old_profile,
            )
            new = _life(
                new_protocol,
                state,
                catalog,
                scoring_new.spec,
                scoring_new.conditions_for(K51),
                new_profile,
            )
            old_years = [_year(r) for r in old]
            new_years = [_year(r) for r in new]
            compared = (
                "effective_weights",
                "annual_mean",
                "annual_status",
                "raw_quota",
                "new_quota",
                "next_energy",
                "months",
            )
            diffs = [
                f"第 {o['year']} 年 {k}"
                for o, n in zip(old_years, new_years, strict=True)
                for k in compared
                if o[k] != n[k]
            ]
            same_outcome = [
                o["outcome_fingerprint"] == n["outcome_fingerprint"]
                for o, n in zip(old_years, new_years, strict=True)
            ]
            case = {
                "old_points": old_points,
                "new_points": dict(confirmed.six_dimensions.points),
                "years_old": len(old_years),
                "years_new": len(new_years),
                "compared_fields": list(compared),
                "differences": diffs,
                "identical": not diffs and len(old_years) == len(new_years) == 3,
                "settlement_outcomes_byte_identical": same_outcome,
                "scoring_content_same": scoring_old.spec.content_fingerprint
                == scoring_new.spec.content_fingerprint,
                "expected_differences": {
                    "preference_source": [
                        old[0].score.preference_source,
                        new[0].score.preference_source,
                    ],  # type: ignore[union-attr]
                    "raw_dimension_weights_old": old[
                        0
                    ].score.health_weighting.raw_dimension_weights,  # type: ignore[union-attr]
                    "raw_dimension_weights_new": new[
                        0
                    ].score.health_weighting.raw_dimension_weights,  # type: ignore[union-attr]
                    "health_input_old": old[0].score.health_weighting.healthcare_semantics,  # type: ignore[union-attr]
                    "health_input_new": new[0].score.health_weighting.independent_health_input,  # type: ignore[union-attr]
                    "protocol_ids": [old_protocol.protocol_id, new_protocol.protocol_id],
                },
                "years": [{"old": o, "new": n} for o, n in zip(old_years, new_years, strict=True)],
            }
            report["cases"][name] = case
            report["identical"] &= case["identical"]
    report["rule"] = (
        "逐月逐项 ==，不加容差；只比较评价与反馈数值，原始画像、偏好来源与指纹本来就不同（单独列在 expected_differences）"
    )
    report["plans"] = _plans()
    report["injected_events"] = INJECTED
    if out is not None:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "equivalence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
            newline="\n",
        )
    return report


_ = (copy, yaml, parse_scoring_config)
