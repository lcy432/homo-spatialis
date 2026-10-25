"""结果、年度事实、进度与提交回执的显示附层（E3 → E5 接口补齐）：中文与提交的样例逐字相同，英文按两个资料包的源资源核对。

* **中文不变。** 按 ``scripts/product_e3a.py samples`` 的同一组场景与参数重建结果、进度、年度事实与提交回执样例，与
  ``contracts/product/v3/samples`` 里的 payload 逐字比较（``meta.sample`` 是样例出处标签，不比）。
* **英文覆盖。** 每份消息只带它自己那一次生成里记下的文字（投影函数本身那一段，与服务写显示绑定的方式相同），按当前的源资源
  （``source_resources``，不冻结）渲染：不允许 ``translation_stale`` / ``source_changed`` / ``ambiguous``；还缺英文的只允许下面
  ``ALLOWED_MISSING`` 列出的几类——从别的消息照抄过来的文字（确认画像里的兴趣范围、预检查里的规划器说明），和 E1 事件链里的
  环境记录原文。
* **资源。** 由 E1 表格生成的 key（缺失原因、分身运行状态）的中文副本与表格逐字相同；E3B 实体资源收录了冻结生活输入里的每一项
  机会、片区与治疗，中文与冻结标签逐字相同（``scripts/_display_entities_e3b.py`` 生成）。
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from product import bridge
from product import scenarios as sc
from product.display import bind_json, recording
from product.display.catalog import load_entities, load_templates, source_resources
from product.display.render import BindingSet, coverage, display_fields
from product.display.text import iter_text, pointer_pattern
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "contracts/product/v3/samples"
SYNTHETIC = REPO / "configs/product/e3a_synthetic_pack.yaml"
E3B = REPO / "configs/product/e3b_us_v1_pack.yaml"

#: 还没有英文、也不由这里生成的文字：按 JSON Pointer 模式 → 允许的缺失原因。照抄自确认画像（兴趣范围的几句）与预检查（规划器
#: 说明）的文字绑定在那两份消息里，结果的这次生成没有重新生成它们（测试还核对没有绑定的正是照抄的那几段）；E1 事件链的环境记录
#: 是 E1 写的中文原文：外层年月按模板绑定，原文本身没有译文。
ALLOWED_MISSING = {
    "/limitations_zh/*": {"no_binding"},
    "/planner/description_zh": {"no_binding"},
    "/clones/*/event_chain/*": {"untranslated_param"},
}
FORBIDDEN = {"translation_stale", "source_changed", "ambiguous", "binding_stale"}


def _cli() -> Any:
    spec = importlib.util.spec_from_file_location(
        "product_e3a_cli", REPO / "scripts/product_e3a.py"
    )
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(REPO / "scripts"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _recorded() -> Iterator[list[tuple[Any, Any]]]:
    """场景里的投影函数改成“在自己的记录器里调用”：每份消息只带它那一次生成里记下的文字。"""
    seen: list[tuple[Any, Any]] = []

    def wrap(fn: Any) -> Any:
        def inner(*args: Any, **kwargs: Any) -> Any:
            with recording() as rec:
                out = fn(*args, **kwargs)
            seen.append((out, rec))
            return out

        return inner

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "project_result", wrap(sc.project_result))
        mp.setattr(sc, "progress", wrap(sc.progress))
        mp.setattr(sc, "year_facts", wrap(sc.year_facts))
        mp.setattr(bridge, "submit", wrap(bridge.submit))
        yield seen


def _rec(seen: list[tuple[Any, Any]], message: Any) -> Any:
    return next(rec for out, rec in seen if out is message)


def _result(seen: list[tuple[Any, Any]], s: Any) -> tuple[Any, Any, frozenset[str]]:
    """一个场景的产品结果、它的记录器，与它从确认画像、预检查照抄过来的文字。"""
    copied = {*s.confirmed.interests.scope_zh, s.pre.preflight.planner.description_zh}
    return s.result, _rec(seen, s.result), frozenset(copied)


def _plain(seen: list[tuple[Any, Any]], message: Any) -> tuple[Any, Any, frozenset[str]]:
    return message, _rec(seen, message), frozenset()


def _illness(person: str, repeat: int, note: str) -> dict[str, Any]:
    return {
        "mode": "injected",
        "stream_version": "e1-5c-illness-stream-v1",
        "note": "受控夹具：注入大病",
        "injected": [
            {
                "person_id": person,
                "repeat_index": repeat,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": note,
            }
        ],
    }


@pytest.fixture(scope="module")
def cli() -> Any:
    return _cli()


@pytest.fixture(scope="module")
def synthetic(
    tmp_path_factory: pytest.TempPathFactory, cli: Any
) -> dict[str, tuple[Any, Any, frozenset[str]]]:
    """合成资料包的样例（与 ``generate_samples`` 同一组场景与参数）：名字 → （消息，记录器，照抄的文字）。"""
    from common.life_planning_contracts import MockScript

    work = tmp_path_factory.mktemp("display_result")
    out: dict[str, tuple[Any, Any]] = {}
    with _recorded() as seen:
        n = sc.closed_loop(work / "normal", "normal")
        out["09_normal_progress_completed"] = _plain(seen, n.progress)
        out["10_normal_result"] = _result(seen, n)
        nop = sc.closed_loop(
            work / "no_purchase",
            "no_purchase",
            person="u502",
            draft_kwargs={
                "material": {
                    "housing_condition": "50",
                    "food_life": "50",
                    "daily_convenience": "0",
                    "personal_enjoyment": "0",
                },
                "hard_requirements": (),
            },
        )
        out["21_no_purchase_result"] = _result(seen, nop)
        facts, _ = sc.partial_service_months(work / "partial")
        out["22_partial_service_months"] = _plain(seen, facts)
        mixed = sc.closed_loop(
            work / "mixed",
            "mixed_cem",
            person="u503",
            origin="controlled_fixture",
            repeat_indexes=(0, 1),
            illness_events=_illness("u503", 1, "受控夹具：重复序号 1 第 1 年第 1 月大病"),
            scripts=(cli._script("u503.m52.r0", "malformed_output", (1,)),),
        )
        out["23_mixed_cem_result"] = _result(seen, mixed)
        allx = sc.closed_loop(
            work / "all_exit",
            "all_exit",
            person="u504",
            origin="controlled_fixture",
            draft_kwargs={"hard_requirements": (), "cash": ("500", "USD", "2024")},
            illness_events=_illness("u504", 0, "受控夹具：第 1 年第 1 月大病"),
        )
        out["24_all_exit_result"] = _result(seen, allx)
        stop = sc.closed_loop(
            work / "interrupted",
            "interrupted",
            person="u506",
            origin="controlled_fixture",
            stop_after_calls=2,
        )
        out["25_not_generated_result"] = _result(seen, stop)
        out["26_interrupted_progress"] = _plain(seen, stop.progress)
        paused = sc.closed_loop(
            work / "paused",
            "paused",
            person="u507",
            origin="controlled_fixture",
            scripts=(cli._script("u507.k51.r0", "raise_error", (1,)),),
        )
        out["27_paused_progress"] = _plain(seen, paused.progress)
        out["27b_paused_result"] = _result(seen, paused)
        stale = cli._stale_submission(work / "stale")
        out["31_stale_submission_receipt"] = _plain(seen, stale)
        life = sc.life_extension_year_facts(work / "life_extension")
        for name, key in (
            ("44_bought_and_played_year_facts", "bought_and_played"),
            ("45_bought_not_played_year_facts", "bought_not_played"),
            ("46_paused_cancelled_kit_year_facts", "paused_cancelled_kit"),
            ("47_expired_year_facts", "expired"),
        ):
            out[name] = _plain(seen, life[key])
        hobby = sc.closed_loop(
            work / "hobby_gear",
            "hobby_gear",
            person="u512",
            origin="controlled_fixture",
            scripts=(MockScript(clone_id="u512.k51.r0", behavior="buy_hobby_gear", years=(3,)),),
        )
        out["50_valuation_scope_result"] = _result(seen, hobby)
    return out


def _e3b(cli: Any, work: Path, name: str, cities: tuple[str, ...], repeats: tuple[int, ...]) -> Any:
    with _recorded() as seen, pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "PACK_PATH", cli.E3B_PACK)
        s = sc.closed_loop(
            work,
            name,
            draft_kwargs={"abilities": ("edu_at_least_hs",), "cities": cities},
            repeat_indexes=repeats,
        )
        return _result(seen, s)


@pytest.fixture(scope="module")
def e3b_two_cities(tmp_path_factory: pytest.TempPathFactory, cli: Any) -> Any:
    """52：休斯敦与西雅图各一个分身（冻结的 E3B 真实资料包）。"""
    return _e3b(cli, tmp_path_factory.mktemp("display_e3b"), "e3b", (cli.HOU, cli.SEA), (0,))


@pytest.fixture(scope="module")
def e3b_sf_car(tmp_path_factory: pytest.TempPathFactory, cli: Any) -> Any:
    """55：旧金山两个分身（没有公交资料，一个分身那两年买用车权开车上班）。"""
    work = tmp_path_factory.mktemp("display_e3b_car")
    return _e3b(cli, work, "e3b_sf_car", (cli.SF,), (0, 1))


SYNTHETIC_SAMPLES = (
    "09_normal_progress_completed",
    "10_normal_result",
    "21_no_purchase_result",
    "22_partial_service_months",
    "23_mixed_cem_result",
    "24_all_exit_result",
    "25_not_generated_result",
    "26_interrupted_progress",
    "27_paused_progress",
    "27b_paused_result",
    "31_stale_submission_receipt",
    "44_bought_and_played_year_facts",
    "45_bought_not_played_year_facts",
    "46_paused_cancelled_kit_year_facts",
    "47_expired_year_facts",
    "50_valuation_scope_result",
)


def _without_sample(payload: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(payload)
    if isinstance(body.get("meta"), dict):
        body["meta"].pop("sample", None)
    return body


def _problems(cli: Any, name: str, built: tuple[Any, Any, frozenset[str]], path: Path) -> list[str]:
    """一份重建的样例：中文（含指纹）与提交的样例逐字相同；英文覆盖（当前源资源）没有过时、改动或歧义的绑定，缺失只在
    ``ALLOWED_MISSING`` 的位置、按允许的原因，而且没有绑定的正是从确认画像与预检查照抄过来的那几段。"""
    message, rec, copied = built
    body = message.model_dump(mode="json")
    committed = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))["payload"]
    out = []
    if cli._json(_without_sample(body)) != cli._json(_without_sample(committed)):
        out.append(f"{name}: 中文或指纹与提交的样例不同")
    pack = load_pack(path)
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    cov = coverage(body, rec, res)
    if not cov["displayable"] or FORBIDDEN & set(cov["missing"]):
        out.append(f"{name}: {cov['missing_patterns']}")
    for pattern, reasons in cov["missing_patterns"].items():
        if pattern not in ALLOWED_MISSING or not set(reasons) <= ALLOWED_MISSING[pattern]:
            out.append(f"{name}: {pattern} 缺英文 {reasons}")
    text = dict(iter_text(body))
    bset = BindingSet(tuple(bind_json(body, rec)), res, res.resource_version)
    fields, _status, _stats = display_fields(body, [bset], "en")
    for f in fields:
        if f.get("missing_reason") == "no_binding" and text[f["pointer"]] not in copied:
            out.append(f"{name}: {pointer_pattern(f['pointer'])} 没有绑定：{text[f['pointer']]}")
    if "_result" not in name and cov["status"] != "complete":
        out.append(f"{name}: 不是结果的消息应当全部有英文：{cov['missing_patterns']}")
    return out


def test_synthetic_samples_keep_their_chinese_and_have_english(cli, synthetic):
    """合成资料包的 16 份样例（结果、进度、年度事实、提交回执）：显示绑定只是并行记下 key 与参数，中文不变；英文齐（见上）。"""
    assert set(synthetic) == set(SYNTHETIC_SAMPLES)
    problems = [
        x for name in SYNTHETIC_SAMPLES for x in _problems(cli, name, synthetic[name], SYNTHETIC)
    ]
    assert not problems, problems


def test_e3b_two_city_result_keeps_its_chinese_and_has_english(cli, e3b_two_cities):
    """52：冻结的 E3B 真实资料包，休斯敦与西雅图（片区、通勤、税与 SOC 岗位名称都按编号绑定）。"""
    assert not _problems(cli, "52_e3b_result", e3b_two_cities, E3B)


def test_e3b_car_result_keeps_its_chinese_and_has_english(cli, e3b_sf_car):
    """55：旧金山两个分身，其中一个那两年买用车权开车上班（用车权的说明与用车的通勤也有英文）。"""
    assert not _problems(cli, "55_e3b_sf_car_result", e3b_sf_car, E3B)


def test_every_public_explanation_and_progress_text_has_english():
    """公开说明的每一种类别、暂停与停下的每一种组合、两种费用说明：英文都有（不只是样例里出现过的）。"""
    from common.life_aggregation_contracts import EntryRecord
    from common.life_planning_contracts import RunProgress as E1RunProgress
    from product import public_errors as pe

    pack = load_pack(SYNTHETIC)
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    messages = []
    with recording() as rec:
        for key in pe.CLONE_TEXT:
            cat = key if key in ("C", "E") else "M"
            entry = EntryRecord.model_construct(
                clone_id="u1.k51.r0", category=cat, missing_reason=None if cat != "M" else key
            )
            messages.append(pe.clone_explanation(entry, "run-x"))
        for status in ("paused", "budget_exhausted", "interrupted", "halted"):
            for kind in pe._HALT_TEXT if status == "halted" else (None,):
                for report in (True, False):
                    for resumable in (True, False):
                        p = E1RunProgress.model_construct(
                            run_id="run-x", status=status, halt_kind=kind
                        )
                        messages.append(
                            pe.progress_pause(p, report_available=report, resumable=resumable)
                        )
        execution = SimpleNamespace(
            planner_kind="llm",
            currency="CNY",
            price_version="quote-v1",
            max_cost="20",
            max_requests=54,
        )
        messages.append(bridge.cost_status(None, None, calls_used=0, call_limit=18))
        messages.append(bridge.cost_status(execution, None, calls_used=3, call_limit=18))
    body = {"messages": [m.model_dump(mode="json") for m in messages]}
    cov = coverage(body, rec, res)
    assert cov["status"] == "complete", cov["missing_patterns"]


def test_keys_generated_from_e1_tables_carry_the_same_chinese():
    """缺失原因与分身运行状态的中文只在 E1 的表格里：模板里的中文副本逐字相同，英文都有（表格改了就是译文过时）。"""
    from common.life_aggregation_contracts import MISSING_REASON_TEXT
    from common.life_planning_contracts import CLONE_RUN_STATUS_TEXT

    templates = load_templates()
    tables = {f"result.missing_reason.{k}": v for k, v in MISSING_REASON_TEXT.items()} | {
        f"progress.clone_status.{k}": v for k, v in CLONE_RUN_STATUS_TEXT.items()
    }
    for key, zh in tables.items():
        assert templates[key]["zh"] == zh, key
        assert templates[key]["en"], key


def test_e3b_entity_resource_covers_the_frozen_life_input_with_the_frozen_labels():
    """E3B 的每一项机会、片区与治疗都有英文名称，资源里的中文与冻结生活输入的标签逐字相同（资料包变了就要重新生成）。"""
    life = json.loads(
        (REPO / "data/frozen/life/us-v1/pack/life_input.json").read_text(encoding="utf-8")
    )
    entities = load_entities("pack.e3b.us-v1")
    expected = {
        ("opportunity", o["opportunity_id"]): o["label"]
        for c in life["catalogs"]
        for o in c["opportunities"]
    }
    expected |= {("area", a["area_id"]): a["label"] for c in life["catalogs"] for a in c["areas"]}
    expected |= {
        ("treatment", t["treatment_id"]): t["label"]
        for c in life["catalogs"]
        for t in c["treatments"]
    }
    found = {
        (kind, ident): fields["label"]
        for kind in ("opportunity", "area", "treatment")
        for ident, fields in entities.get(kind, {}).items()
    }
    assert set(found) == set(expected)
    assert all(found[k]["zh"] == zh and found[k]["en"] for k, zh in expected.items())
