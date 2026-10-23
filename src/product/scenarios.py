"""E3-A 场景：合成用户 + 正式入口（审阅、确认、编译、E1 预检查、提交、离线运行、评价与投影）。

测试、公共样例与证据共用这里的场景，结果都是代码实际跑出来的，不是预先写好的成功 JSON。每个场景在一个自包含的工作目录里运行
（``fixtures.prepare_workspace``），所以编译产物与运行配置里的路径都是相对路径。出处标签：

* ``offline_run``：正常的离线闭环与只走审阅、编译的样例；
* ``controlled_fixture``：受控夹具——注入的疾病事件、受控的 mock 行为（格式错误、意外异常）、预先写好的计划、往开局里塞旧物；
* ``contract_example``：线上服务尚未实现，只是标明为示例的契约响应。

全部是合成人物与合成城市；规划器是可控的 mock（零网络、零费用）。
"""

from __future__ import annotations

import contextlib
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from common.life_contracts import YearAction
from common.life_planning_contracts import MockScript
from common.product_contracts import (
    CatalogCompilation,
    ConfirmedProfile,
    ConfirmedRef,
    OptionInventoryCoverage,
    PreflightRef,
    ProductCapabilities,
    ProductResult,
    ProfileReview,
    RunProgress,
    RunReceipt,
    RunSubmission,
    SampleLabel,
    UserInputDraft,
    YearFacts,
)
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import load_scoring_config
from orchestration.life_runner import RunOutcome
from product.answers import confirm_profile, review_draft
from product.bridge import progress, run_offline, submit
from product.capabilities import build_capabilities
from product.catalog import compile_catalog, inventory_coverage
from product.fixtures import K51, M52, make_draft, prepare_workspace
from product.pack import DataPack, load_pack
from product.preflight import PreflightRun, run_preflight
from product.results import project_result, year_facts

REPO = Path(__file__).resolve().parents[2]


def label(origin: str, produced_by: str, note: str) -> SampleLabel:
    return SampleLabel(origin=origin, produced_by=produced_by, note=note)  # type: ignore[arg-type]


@dataclass
class Scenario:
    """一个场景的全部产物（产品消息与 E1 运行对象）。"""

    name: str
    work: Path
    pack: DataPack
    draft: UserInputDraft | None = None
    review: ProfileReview | None = None
    confirmed: ConfirmedProfile | None = None
    pre: PreflightRun | None = None
    submission: RunSubmission | None = None
    receipt: RunReceipt | None = None
    duplicate: RunReceipt | None = None
    outcome: RunOutcome | None = None
    progress: RunProgress | None = None
    result: ProductResult | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def workspace(work: Path) -> Path:
    return prepare_workspace(Path(work), REPO)


def capabilities(work: Path) -> ProductCapabilities:
    with contextlib.chdir(workspace(work)):
        pack = load_pack()
        return build_capabilities(
            pack,
            sample=label(
                "offline_run", "product.capabilities.build_capabilities", "合成资料包发布的能力清单"
            ),
        )


def _submission(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    pre: PreflightRun,
    key: str,
    sample: SampleLabel | None = None,
) -> RunSubmission:
    return RunSubmission(
        meta=pack.meta(sample),
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id, content_fingerprint=confirmed.content_fingerprint
        ),
        preflight=PreflightRef(
            preflight_id=pre.preflight.preflight_id,
            preflight_fingerprint=pre.preflight.preflight_fingerprint,
        ),
        idempotency_key=key,
    )


def closed_loop(
    work: Path,
    name: str,
    *,
    person: str = "u501",
    draft_kwargs: dict[str, Any] | None = None,
    origin: str = "offline_run",
    note: str = "离线闭环实际执行的结果（mock 规划器，零网络）",
    scripts: tuple[MockScript, ...] = (),
    illness_events: dict[str, Any] | None = None,
    repeat_indexes: tuple[int, ...] | None = None,
    stop_after_calls: int | None = None,
    acknowledged: tuple[str, ...] | None = None,
) -> Scenario:
    """原始答案 → 审阅 → 确认 → 编译 → E1 预检查 → 提交 → 离线三年 → E1 评价与汇总 → 产品结果。"""
    work = workspace(work)
    sample = label(origin, f"product.scenarios.closed_loop({name})", note)
    with contextlib.chdir(work):
        pack = load_pack()
        if repeat_indexes is not None:
            raw = copy.deepcopy(pack.raw)
            raw["pack"]["run"]["repeat_indexes"] = list(repeat_indexes)
            pack = DataPack(raw, pack.path)
        draft = make_draft(pack, draft_id=f"draft.{person}", sample=sample, **(draft_kwargs or {}))
        review = review_draft(pack, draft, sample=sample)
        confirmed = confirm_profile(
            pack,
            draft,
            person_ref=person,
            acknowledged=review.pending_confirmations if acknowledged is None else acknowledged,
            sample=sample,
        )
        pre = run_preflight(
            pack,
            confirmed,
            Path(f"compiled/{name}"),
            draft=draft,
            repo_root=Path("."),
            scripts=scripts,
            illness_events=illness_events,
            sample=sample,
        )
        s = Scenario(
            name=name,
            work=work,
            pack=pack,
            draft=draft,
            review=review,
            confirmed=confirmed,
            pre=pre,
        )
        if pre.preflight.status != "ready":
            return s
        s.submission = _submission(pack, confirmed, pre, f"idem-{person}-{name}", sample=sample)
        s.receipt = submit(
            pack, confirmed, pre.preflight, s.submission, Path("registry"), sample=sample
        )
        s.duplicate = submit(
            pack, confirmed, pre.preflight, s.submission, Path("registry"), sample=sample
        )
        assert s.receipt.run_id is not None and pre.world is not None
        s.outcome = run_offline(
            pre.world, Path(f"archive/{name}"), s.receipt.run_id, stop_after_calls=stop_after_calls
        )
        s.progress = progress(pack, Path(f"archive/{name}"), sample=sample)
        s.result = project_result(
            pack, confirmed, pre.preflight, pre.world, s.outcome, pre.inventory, sample=sample
        )
        return s


def review_only(work: Path, name: str, *, person: str = "u501", **draft_kwargs: Any) -> Scenario:
    """只走审阅（以及能确认时的确认）：展示状态、问题与可读解释。"""
    work = workspace(work)
    sample = label(
        "offline_run",
        f"product.scenarios.review_only({name})",
        "审阅与确认的实际输出（没有运行生活）",
    )
    with contextlib.chdir(work):
        pack = load_pack()
        draft = make_draft(pack, draft_id=f"draft.{person}.{name}", sample=sample, **draft_kwargs)
        review = review_draft(pack, draft, sample=sample)
        s = Scenario(name=name, work=work, pack=pack, draft=draft, review=review)
        if review.confirmable:
            s.confirmed = confirm_profile(
                pack,
                draft,
                person_ref=person,
                acknowledged=review.pending_confirmations,
                sample=sample,
            )
        return s


def preflight_only(
    work: Path,
    name: str,
    *,
    person: str = "u501",
    mutate_life_input: Any = None,
    mutate_pack: Any = None,
    origin: str = "offline_run",
    **draft_kwargs: Any,
) -> Scenario:
    """审阅 → 确认 → 编译 → E1 预检查。``mutate_life_input`` 是受控夹具：在 E1 预检查之前改编译出的生活输入（例如往开局塞旧物）。
    ``mutate_pack`` 也是受控夹具：在内存里改资料包副本（``pack`` 一节），磁盘上的资料包不动。"""
    work = workspace(work)
    sample = label(
        origin, f"product.scenarios.preflight_only({name})", "预检查的实际输出（没有运行生活）"
    )
    with contextlib.chdir(work):
        pack = load_pack()
        if mutate_pack is not None:
            raw = copy.deepcopy(pack.raw)
            mutate_pack(raw["pack"])
            pack = DataPack(raw, pack.path)
        draft = make_draft(pack, draft_id=f"draft.{person}.{name}", **draft_kwargs)
        review = review_draft(pack, draft)
        confirmed = confirm_profile(
            pack, draft, person_ref=person, acknowledged=review.pending_confirmations
        )
        out = Path(f"compiled/{name}")
        if mutate_life_input is not None:
            # 先正常编译一次，再改生活输入，最后用改过的文件重新做 E1 预检查（不改编译器本身）
            from product.compile import compile_inputs
            from product.preflight import _blocked

            compiled = compile_inputs(pack, confirmed, out, repo_root=Path("."))
            path = compiled.paths["life_input"]
            text = path.read_text(encoding="utf-8")
            header = "".join(ln + "\n" for ln in text.splitlines() if ln.startswith("#"))
            data = yaml.safe_load(text)
            mutate_life_input(data)
            path.write_text(
                header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000),
                encoding="utf-8",
                newline="\n",
            )
            from orchestration.life_runner import RunRefusedError, preflight

            try:
                preflight(compiled.paths["run_config"])
            except RunRefusedError as exc:
                from product.public_errors import e1_refusal_issue

                pre = _blocked(
                    pack,
                    confirmed,
                    [e1_refusal_issue(compiled.out_dir, str(exc))],
                    sample,
                    compiled=compiled,
                    e1_refusal=str(exc),
                )
            else:  # pragma: no cover - 夹具的意图就是被拒绝
                raise AssertionError("受控夹具改过的开局没有被 E1 拒绝")
        else:
            pre = run_preflight(
                pack, confirmed, out, draft=draft, repo_root=Path("."), sample=sample
            )
        return Scenario(
            name=name,
            work=work,
            pack=pack,
            draft=draft,
            review=review,
            confirmed=confirmed,
            pre=pre,
        )


def positive_weight_data_unknown(work: Path) -> Scenario:
    """受控夹具：工作区副本里把 M52 的安全资料改成“资料未知”（评分条件与可见事实同时改，E1 的“未知只对应未知”照旧核对），
    用户给安全 20 分。预检查必须明确报告这一缺口（这座城“可评分”只是 partial），而不是把安全删掉、把其余权重重新归一化。
    评分规格（内容身份）不变，只改城市条件。"""
    work = workspace(work)
    edits = (
        (
            "configs/life/scoring_product.yaml",
            "label: 合成城 M52",
            "      - {component_id: safety, status: known, base: 3, cap: 0.9, source: *cond}\n",
            "      - {component_id: safety, status: unknown, unknown_reason: 受控夹具：这座城的安全资料未知, "
            "source: *cond}\n",
        ),
        (
            "configs/life/city_facts_product.yaml",
            "label: 合成城 M52",
            "unit: 合成指数（0–10）, period: *period, status: known, number: 6, source: *factsrc}\n",
            "unit: 合成指数（0–10）, period: *period, status: unknown, "
            "unknown_reason: 受控夹具：这座城的安全资料未知, source: *factsrc}\n",
        ),
    )
    for rel, anchor, old, new in edits:
        path = work / rel
        text = path.read_text(encoding="utf-8")
        head, sep, tail = text.partition(anchor)
        assert sep and tail.count(old) >= 1, (rel, old)
        path.write_text(head + sep + tail.replace(old, new, 1), encoding="utf-8", newline="\n")
    sample = label(
        "controlled_fixture",
        "product.scenarios.positive_weight_data_unknown",
        "工作区副本里 M52 的安全资料改成资料未知（评分条件与可见事实一起改）；预检查的实际输出",
    )
    with contextlib.chdir(work):
        pack = load_pack()
        draft = make_draft(pack, draft_id="draft.u510.unknown_safety")
        review = review_draft(pack, draft)
        confirmed = confirm_profile(
            pack, draft, person_ref="u510", acknowledged=review.pending_confirmations
        )
        pre = run_preflight(
            pack,
            confirmed,
            Path("compiled/unknown_safety"),
            draft=draft,
            repo_root=Path("."),
            sample=sample,
        )
    return Scenario(
        name="unknown_safety",
        work=work,
        pack=pack,
        draft=draft,
        review=review,
        confirmed=confirmed,
        pre=pre,
    )


def catalog_boundary(work: Path) -> tuple[CatalogCompilation, OptionInventoryCoverage]:
    """目录编译的边界样例（受控夹具）：在资料包副本里加几个不完整或混合来源的候选，看编译怎样处理。"""
    work = workspace(work)
    with contextlib.chdir(work):
        pack = load_pack()
        raw = copy.deepcopy(pack.raw)
        body = raw["pack"]
        body["facts"] += [
            {
                "fact_id": "f.m52.washer.price.observed-fixture",
                "field": "price",
                "raw_value": "1100",
                "unit": "USD_2024_LOCAL/台",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "observed",
                "locator": "fixture://e3a/observed-quote-shape（受控夹具：只模拟一条观测报价的形状，不是真实资料）",
                "note": "受控夹具：报价按观测资料的形状写，用来检查“真实报价不会把假设效果变成观测”",
            },
            {
                "fact_id": "f.m52.washer.effect.assumed",
                "field": "effect",
                "raw_value": "laundry 5",
                "unit": "小时/月（本人残余）",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "synthetic",
                "assumption": "合成效果假设（没有观测资料）",
                "note": "减负效果是假设",
            },
            {
                "fact_id": "f.m52.washer.life.estimated",
                "field": "lifetime",
                "raw_value": "8",
                "unit": "年",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "estimated",
                "conversion": "受控夹具：按“保修 4 年 × 2”估计的寿命（方法写明，结果是估计）",
                "note": "估计的寿命",
            },
            {
                "fact_id": "f.m52.robot.life.half",
                "field": "lifetime",
                "raw_value": "2.5",
                "unit": "年",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "synthetic",
                "assumption": "合成设定：寿命 2.5 年（不是整数年）",
                "note": "非整数年",
            },
            {
                "fact_id": "f.m52.robot.price",
                "field": "price",
                "raw_value": "300",
                "unit": "USD_2024_LOCAL/台",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "synthetic",
                "assumption": "合成设定：受控夹具的报价",
                "note": "报价与效果都有登记的来源，只有寿命不是整数年",
            },
            {
                "fact_id": "f.m52.robot.effect",
                "field": "effect",
                "raw_value": "cleaning 6",
                "unit": "小时/月（本人残余）",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "synthetic",
                "assumption": "合成设定：受控夹具的减负效果",
                "note": "减负效果有登记的来源",
            },
            {
                "fact_id": "f.k51.laundry.visit.price",
                "field": "price",
                "raw_value": "25",
                "unit": "USD_2024_LOCAL/次",
                "period": "2024 年不变价",
                "geography": "合成城 K51",
                "kind": "synthetic",
                "assumption": "合成设定：按次上门的价格",
                "note": "按次计费",
            },
        ]
        price = {"currency": "USD", "base_year": 2024, "price_meaning": "local_price_base_year"}
        body["candidates"] += [
            {
                "candidate_id": "cand.m52.device.washer-mixed-sources",
                "city_ref": M52,
                "kind": "device",
                "label": "洗衣机（单件，报价按观测形状、效果是假设）",
                "quality": "受控夹具：一次购买；报价按观测资料的形状写，寿命是估计，减负效果是合成假设",
                "tasks": ["laundry"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "1100",
                    **price,
                    "billing": "one_time",
                    "per_unit": "washer",
                    "facts": ["f.m52.washer.price.observed-fixture"],
                },
                "lifetime": {
                    "status": "known",
                    "years_text": "8",
                    "effective_from": "same_year",
                    "facts": ["f.m52.washer.life.estimated"],
                },
                "effect": {
                    "status": "known",
                    "residuals": [{"task": "laundry", "residual_hours_per_month": "5"}],
                    "facts": ["f.m52.washer.effect.assumed"],
                },
                "target_opportunity_id": "m52.durable.washer",
            },
            {
                "candidate_id": "cand.m52.device.robot-half-year",
                "city_ref": M52,
                "kind": "device",
                "label": "扫地机器人（寿命 2.5 年）",
                "quality": "受控夹具：寿命不是整数年",
                "tasks": ["cleaning"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "300",
                    **price,
                    "billing": "one_time",
                    "per_unit": "robot",
                    "facts": ["f.m52.robot.price"],
                },
                "lifetime": {
                    "status": "known",
                    "years_text": "2.5",
                    "effective_from": "same_year",
                    "facts": ["f.m52.robot.life.half"],
                },
                "effect": {
                    "status": "known",
                    "residuals": [{"task": "cleaning", "residual_hours_per_month": "6"}],
                    "facts": ["f.m52.robot.effect"],
                },
                "target_opportunity_id": "m52.durable.robot",
            },
            {
                "candidate_id": "cand.k51.service.laundry-per-visit",
                "city_ref": K51,
                "kind": "service",
                "label": "上门洗衣（按次）",
                "quality": "受控夹具：按次上门计费；每月上门几次的资料缺失；减负效果也缺失",
                "tasks": ["laundry"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "25",
                    **price,
                    "billing": "per_visit",
                    "per_unit": "visit",
                    "facts": ["f.k51.laundry.visit.price"],
                },
                "lifetime": {"status": "not_applicable"},
                "effect": {"status": "unknown"},
                "participation": {
                    "status": "known",
                    "hours_text": "0",
                    "burden_per_hour_text": "0",
                    "facts": ["f.service.participation"],
                },
                "target_opportunity_id": "k51.service.laundry-visit",
            },
        ]
        variant = DataPack(raw, pack.path)
        life = yaml.safe_load(Path(variant.body.e1_base.life_input).read_text(encoding="utf-8"))
        sample = label(
            "controlled_fixture",
            "product.scenarios.catalog_boundary",
            "资料包副本里加了混合来源、非整数寿命与按次计费的候选（受控夹具），检查编译与覆盖怎样处理",
        )
        compilation, _ = compile_catalog(variant, life["protocol"], sample=sample)
        return compilation, inventory_coverage(variant, compilation, sample=sample)


def catalog_statuses(work: Path) -> tuple[CatalogCompilation, OptionInventoryCoverage]:
    """受控夹具（E3-A 收尾 F2）：在资料包副本里加六个候选，演示供给、资料与编译三种状态与逐候选原因。

    确定没有供给；供给未知但报价已知（编成资料未知，原因写供给未知）；本人参与小时为负；报价缺计价单位；报价按观测形状写而寿命与
    效果没有来源；报价引用了记录效果的事实。原有候选照常编译，分母包括全部候选。"""
    work = workspace(work)
    with contextlib.chdir(work):
        pack = load_pack()
        raw = copy.deepcopy(pack.raw)
        body = raw["pack"]
        body["facts"] += [
            {
                "fact_id": "f.m52.chores2.price",
                "field": "price",
                "raw_value": "120",
                "unit": "USD_2024_LOCAL/月",
                "period": "2024 年不变价",
                "geography": "合成城 M52",
                "kind": "synthetic",
                "assumption": "合成设定：受控夹具的月价",
                "note": "供给是否存在不知道，但手里有一个报价",
            },
            {
                "fact_id": "f.k51.helper.price",
                "field": "price",
                "raw_value": "150",
                "unit": "USD_2024_LOCAL/月",
                "period": "2024 年不变价",
                "geography": "合成城 K51",
                "kind": "synthetic",
                "assumption": "合成设定：受控夹具的月价",
                "note": "本人参与小时写成了负数",
            },
            {
                "fact_id": "f.k51.washer2.price.observed-fixture",
                "field": "price",
                "raw_value": "900",
                "unit": "USD_2024_LOCAL/台",
                "period": "2024 年不变价",
                "geography": "合成城 K51",
                "kind": "observed",
                "locator": "fixture://e3a/observed-quote-shape（受控夹具：只模拟一条观测报价的形状，不是真实资料）",
                "note": "只有报价有来源，寿命与效果没有",
            },
        ]
        price = {"currency": "USD", "base_year": 2024, "price_meaning": "local_price_base_year"}
        chores_effect = {
            "status": "known",
            "residuals": [
                {"task": "laundry", "residual_hours_per_month": "2"},
                {"task": "cleaning", "residual_hours_per_month": "2"},
            ],
            "facts": ["f.chores.effect"],
        }
        participation = {
            "status": "known",
            "hours_text": "0",
            "burden_per_hour_text": "0",
            "facts": ["f.service.participation"],
        }
        body["candidates"] += [
            {
                "candidate_id": "cand.k51.service.ironing-not-offered",
                "city_ref": K51,
                "kind": "service",
                "label": "上门熨烫洗衣（这座城确定没有）",
                "quality": "受控夹具：资料显示 K51 确定没有这项服务",
                "tasks": ["laundry"],
                "market_status": "not_offered",
                "price": {"status": "unknown"},
                "lifetime": {"status": "not_applicable"},
                "effect": {"status": "unknown"},
                "target_opportunity_id": "k51.service.ironing",
            },
            {
                "candidate_id": "cand.m52.service.chores-supply-unknown",
                "city_ref": M52,
                "kind": "service",
                "label": "另一家日常杂务服务（不知道这座城有没有）",
                "quality": "受控夹具：有一个报价，但不知道这座城是否真有供给",
                "tasks": ["laundry", "cleaning"],
                "market_status": "unknown",
                "price": {
                    "status": "known",
                    "amount_text": "120",
                    **price,
                    "billing": "per_month",
                    "per_unit": "service-month",
                    "facts": ["f.m52.chores2.price"],
                },
                "lifetime": {"status": "not_applicable"},
                "effect": chores_effect,
                "participation": participation,
                "target_opportunity_id": "m52.service.chores2",
            },
            {
                "candidate_id": "cand.k51.service.helper-negative-participation",
                "city_ref": K51,
                "kind": "service",
                "label": "家务帮手（本人参与小时写成负数）",
                "quality": "受控夹具：本人参与小时是 -1",
                "tasks": ["laundry", "cleaning"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "150",
                    **price,
                    "billing": "per_month",
                    "per_unit": "service-month",
                    "facts": ["f.k51.helper.price"],
                },
                "lifetime": {"status": "not_applicable"},
                "effect": chores_effect,
                "participation": {**participation, "hours_text": "-1"},
                "target_opportunity_id": "k51.service.helper",
            },
            {
                "candidate_id": "cand.m52.device.pack-no-unit",
                "city_ref": M52,
                "kind": "device",
                "label": "便利设备包（报价缺计价单位）",
                "quality": "受控夹具：报价没有写计价单位",
                "tasks": ["laundry", "cleaning"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "6000",
                    **price,
                    "billing": "one_time",
                    "facts": ["f.m52.pack.price"],
                },
                "lifetime": {
                    "status": "known",
                    "years_text": "5",
                    "effective_from": "same_year",
                    "facts": ["f.pack.life"],
                },
                "effect": {
                    "status": "known",
                    "residuals": [
                        {"task": "laundry", "residual_hours_per_month": "4"},
                        {"task": "cleaning", "residual_hours_per_month": "6"},
                    ],
                    "facts": ["f.pack.effect"],
                },
                "target_opportunity_id": "m52.durable.pack_no_unit",
            },
            {
                "candidate_id": "cand.k51.device.washer-observed-price-only",
                "city_ref": K51,
                "kind": "device",
                "label": "洗衣机（只有报价有来源）",
                "quality": "受控夹具：报价按观测资料的形状写；寿命与减负效果有数值但没有来源",
                "tasks": ["laundry"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "900",
                    **price,
                    "billing": "one_time",
                    "per_unit": "washer",
                    "facts": ["f.k51.washer2.price.observed-fixture"],
                },
                "lifetime": {"status": "known", "years_text": "8", "effective_from": "same_year"},
                "effect": {
                    "status": "known",
                    "residuals": [{"task": "laundry", "residual_hours_per_month": "5"}],
                },
                "target_opportunity_id": "k51.durable.washer2",
            },
            {
                "candidate_id": "cand.m52.service.chores-wrong-field-source",
                "city_ref": M52,
                "kind": "service",
                "label": "日常杂务服务（报价引用了效果事实）",
                "quality": "受控夹具：报价的来源引用的是一条记录减负效果的事实",
                "tasks": ["laundry", "cleaning"],
                "market_status": "offered",
                "price": {
                    "status": "known",
                    "amount_text": "108",
                    **price,
                    "billing": "per_month",
                    "per_unit": "service-month",
                    "facts": ["f.chores.effect"],
                },
                "lifetime": {"status": "not_applicable"},
                "effect": chores_effect,
                "participation": participation,
                "target_opportunity_id": "m52.service.chores3",
            },
        ]
        variant = DataPack(raw, pack.path)
        life = yaml.safe_load(Path(variant.body.e1_base.life_input).read_text(encoding="utf-8"))
        sample = label(
            "controlled_fixture",
            "product.scenarios.catalog_statuses",
            "资料包副本里加了确定没有、供给未知、参与小时为负、缺计价单位、只有报价有来源、来源字段不对的候选（受控夹具）",
        )
        compilation, _ = compile_catalog(variant, life["protocol"], sample=sample)
        return compilation, inventory_coverage(variant, compilation, sample=sample)


def duplicate_answers(work: Path) -> Scenario:
    """受控夹具（E3-A 收尾 F4）：同一健康题两个答案、同一资格两条（状态冲突）、两条通勤上限（60 与 45）。只走审阅：
    确认前必须被拒绝，不按最后一个算、不取更严格的上限。"""
    work = workspace(work)
    sample = label(
        "controlled_fixture",
        "product.scenarios.duplicate_answers",
        "草稿里故意写了重复与冲突的答案（受控夹具）；审阅的实际输出",
    )
    with contextlib.chdir(work):
        pack = load_pack()
        raw = make_draft(
            pack,
            draft_id="draft.u511.duplicates",
            occupation_text="我在事务所做会计",
            selected="occ.accountant",
            sample=sample,
        ).model_dump(mode="json")
        qid = raw["health"]["items"][0]["question_id"]
        raw["health"]["items"].append({"question_id": qid, "choice": "agree"})
        raw["abilities"] = [
            {"ability_id": "accounting_cert", "state": "provided"},
            {"ability_id": "accounting_cert", "state": "unsure"},
        ]
        raw["hard_requirements"] = [
            {
                "requirement_id": rid,
                "option_id": "housing.max_commute_roundtrip",
                "parameter_value": v,
            }
            for rid, v in (("hr.commute", 60), ("hr.commute2", 45))
        ]
        draft = UserInputDraft.model_validate(raw)
        review = review_draft(pack, draft, sample=sample)
        return Scenario(name="duplicates", work=work, pack=pack, draft=draft, review=review)


def partial_service_months(work: Path) -> tuple[YearFacts, dict[str, Any]]:
    """受控夹具：在编译出的产品输入上执行一份写好的计划，演示“计划全年买杂务服务，但有的月份钱不够、没有买到”。

    计划（K51）：远处住房、基本食品、门店兼职（月薪 1,500）、全年杂务服务（每月 216）；开局现金改成 300（只为演示，不是标准开局，
    也不进城市比较）。每月付完住房与食品只剩约 120，钱不够 216 的月份没有服务、家务回到本人自己做（E1 先付住房与食品，再付按月服务）。
    走 E1 的正式年度入口 ``run_life_year``（结算 → 评分 → 反馈），结果投影成产品的生活事实。"""
    s = preflight_only(work, "partial_service", person="u505", hard_requirements=())
    assert s.pre is not None and s.pre.compiled is not None
    with contextlib.chdir(s.work):
        bundle, _ = load_life_bundle(
            s.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
        scoring, _ = load_scoring_config(s.pre.compiled.paths["scoring_config"], bundle)
        state = bundle.initial_state("u505.k51.r0")
        assert state is not None
        state = state.model_copy(update={"cash": state.cash.__class__(300.0, state.cash.numeraire)})
        catalog = bundle.catalog_for(K51)
        assert catalog is not None
        profile = bundle.product_profiles[0]
        action = YearAction.model_validate(
            {
                "person_id": "u505",
                "clone_id": "u505.k51.r0",
                "year_index": 1,
                "items": [
                    {
                        "opportunity_id": "k51.housing.far",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                    {
                        "opportunity_id": "k51.food.basic",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                    {
                        "opportunity_id": "k51.job.shop_parttime",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                    {
                        "opportunity_id": "k51.service.chores",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                ],
                "minimum_fallback": ["housing", "food"],
                "emergency": {"minor": "decline", "major": "decline", "pause_order": []},
                "rationale": "受控夹具：预先写好的计划（不是规划器的输出）",
            }
        )
        result = run_life_year(
            protocol=bundle.protocol,
            state=state,
            action=action,
            catalog=catalog,
            tax=binding_from_policy(bundle.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
            scoring=ScoringInputs(
                spec=scoring.spec,
                conditions=scoring.conditions_for(K51),  # type: ignore[arg-type]
                preference_input=profile,
                hard_requirements=profile.hard_requirements,
            ),
        )
        from orchestration.life_runner import preflight

        world = preflight(s.pre.compiled.paths["run_config"])
        facts = year_facts(world, K51, result)
        info = {
            "settlement_status": result.settlement.status,
            "start_cash_override": "300（受控夹具：把开局现金改成 300，只为演示买不到服务的月份；不是标准开局，也不进城市比较）",
            "score_annual_status": None if result.score is None else result.score.annual_status,
            "convenience_q_by_month": None
            if result.score is None
            else [
                next(
                    c for c in m.components if c.component_id == "daily_convenience"
                ).convenience.relief_hours.exact  # type: ignore[union-attr]
                for m in result.score.months
                if m.components
            ],
        }
        return facts, info


def life_extension_year_facts(work: Path) -> dict[str, YearFacts]:
    """受控夹具（DECISIONS/0041）：在编译出的产品输入上执行几份写好的计划，演示买到、在用、实际参加、生病暂停与取消、套餐与
    到期。走 E1 的正式年度入口 ``run_life_year``，结果投影成产品的生活事实；计划不是规划器的输出，不代表模型会这样选。"""
    from common.life_contracts import Holding, IllnessEventSource, InjectedIllness

    s = preflight_only(work, "life_extension", person="u506", hard_requirements=())
    assert s.pre is not None and s.pre.compiled is not None
    with contextlib.chdir(s.work):
        bundle, _ = load_life_bundle(
            s.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
        scoring, _ = load_scoring_config(s.pre.compiled.paths["scoring_config"], bundle)
        start = bundle.initial_state("u506.k51.r0")
        catalog = bundle.catalog_for(K51)
        assert start is not None and catalog is not None
        profile = bundle.product_profiles[0]
        from orchestration.life_runner import preflight

        world = preflight(s.pre.compiled.paths["run_config"])

        def whole(oid: str) -> dict[str, Any]:
            return {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}

        base = [whole("k51.housing.far"), whole("k51.food.basic"), whole("k51.job.clerk")]

        def live(
            items: list[dict[str, Any]],
            *,
            state: Any = None,
            protocol: Any = None,
            emergency: dict[str, Any] | None = None,
        ) -> YearFacts:
            st = state or start
            action = YearAction.model_validate(
                {
                    "person_id": st.person_id,
                    "clone_id": st.clone_id,
                    "year_index": st.year_index,
                    "items": [*base, *items],
                    "minimum_fallback": ["housing", "food"],
                    "emergency": emergency
                    or {"minor": "decline", "major": "decline", "pause_order": []},
                    "rationale": "受控夹具：预先写好的计划（不是规划器的输出）",
                }
            )
            result = run_life_year(
                protocol=protocol or bundle.protocol,
                state=st,
                action=action,
                catalog=catalog,
                tax=binding_from_policy(bundle.tax_policy(catalog.tax_policy_id)),  # type: ignore[arg-type]
                scoring=ScoringInputs(
                    spec=scoring.spec,
                    conditions=scoring.conditions_for(K51),  # type: ignore[arg-type]
                    preference_input=profile,
                    hard_requirements=profile.hard_requirements,
                ),
            )
            return year_facts(world, K51, result)

        practice = "k51.experience.guitar_practice"
        ill = IllnessEventSource(
            mode="injected",
            stream_version="e1-5c-illness-stream-v1",
            injected=(
                InjectedIllness(
                    person_id="u506",
                    repeat_index=0,
                    year_index=1,
                    month=3,
                    severity="major",
                    note="受控夹具：第 3 月月初大病",
                ),
            ),
            note="受控夹具：注入大病",
        )
        expiring = start.model_copy(
            update={
                "holdings": (
                    Holding(
                        opportunity_id="k51.durable.guitar",
                        quantity=1,
                        acquired_year=0,
                        remaining_months=3,
                    ),
                )
            }
        )
        return {
            "bought_and_played": live(
                [
                    {"opportunity_id": "k51.durable.guitar", "quantity": 1, "month": 2},
                    {"opportunity_id": practice, "quantity": 2, "months": [2, 5, 9]},
                    {"opportunity_id": "k51.durable.comfort_chair", "quantity": 1, "month": 1},
                ]
            ),
            "bought_not_played": live(
                [{"opportunity_id": "k51.durable.guitar", "quantity": 1, "month": 2}]
            ),
            "paused_cancelled_kit": live(
                [
                    {"opportunity_id": "k51.durable.guitar", "quantity": 1, "month": 3},
                    {"opportunity_id": "k51.experience.cooking_class", "quantity": 1, "month": 3},
                    {
                        "opportunity_id": "k51.experience.tennis_with_rental",
                        "quantity": 1,
                        "month": 7,
                    },
                    {"opportunity_id": practice, "quantity": 1, "months": [8]},
                ],
                protocol=bundle.protocol.model_copy(update={"illness_events": ill}),
                emergency={
                    "minor": "decline",
                    "major": "treat_if_possible",
                    "pause_order": [],
                    "cancel_purchases_when_ill": ["k51.durable.guitar"],
                },
            ),
            "expired": live(
                [{"opportunity_id": practice, "quantity": 1, "months": [2, 6]}], state=expiring
            ),
        }


def life_item_compilation(work: Path) -> Any:
    """受控夹具（DECISIONS/0041）：生活扩展候选的编译检查。通过的例子（食品、装备、享受服务、免费活动）取自合成样例目录、改了
    编号；被拒或标为缺口的例子按独立审核的反例与真实资料的典型缺口改写（不是真实资料）。"""
    from product.life_items import LifeItemCandidate, compile_life_items

    s = preflight_only(work, "life_items", person="u508", hard_requirements=())
    assert s.pre is not None and s.pre.compiled is not None
    with contextlib.chdir(s.work):
        bundle, _ = load_life_bundle(
            s.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
        catalog = bundle.catalog_for(K51)
        assert catalog is not None

        def item(oid: str, new_id: str, **change: Any) -> dict[str, Any]:
            o = catalog.opportunity(oid)
            assert o is not None
            data = o.model_dump(mode="json")
            data["opportunity_id"] = new_id
            data.update(change)
            return data

        basket = item("k51.food.sichuan_home", "k51.food.basket_regional")
        for key in ("prep_hours_per_month", "dining_hours_per_month"):
            del basket["food_plan"][key]
        fresh_unknown = item("k51.food.sichuan_home", "k51.food.sichuan_fresh_unknown")
        fresh_unknown["food_plan"]["parts"][0]["fresh_meals_per_week"] = None
        tasting = item("k51.experience.cooking_class", "k51.experience.tasting_dinner")
        tasting["activity"]["replaces_meals_per_session"] = 1
        no_facts = item("k51.experience.gallery_tour", "k51.experience.gallery_no_facts")
        del no_facts["activity"]
        gear = item(
            "k51.durable.guitar",
            "k51.durable.racket_unknown_life",
            duration={"kind": "durable_good"},
            equipment={"provides": ["racket_playable"], "note": "球拍；资料没有写能用几年"},
        )
        spa = item("k51.service.massage", "k51.service.luxury_spa")
        spa["enjoyment"][0]["function"] = "luxury_spa"
        candidates = (
            # 通过的例子：每种一项（合唱免费，价格明确为 0）
            LifeItemCandidate(
                "food.sichuan_home",
                "food_plan",
                "川味家常方案",
                item("k51.food.sichuan_home", "k51.food.sichuan_home_cand"),
            ),
            LifeItemCandidate(
                "equipment.guitar",
                "equipment",
                "吉他",
                item("k51.durable.guitar", "k51.durable.guitar_cand"),
            ),
            LifeItemCandidate(
                "enjoyment.massage",
                "enjoyment",
                "按摩（每月两次）",
                item("k51.service.massage", "k51.service.massage_cand"),
            ),
            LifeItemCandidate(
                "activity.choir",
                "activity",
                "社区合唱排练（免费）",
                item("k51.experience.community_choir", "k51.experience.choir_cand"),
            ),
            # 独立审核的四个反例
            LifeItemCandidate(
                "activity.choir_unavailable",
                "activity",
                "社区合唱（本城确定没有）",
                item(
                    "k51.experience.community_choir",
                    "k51.experience.choir_unavailable",
                    availability="unavailable",
                    price=None,
                ),
            ),
            LifeItemCandidate(
                "activity.no_facts", "activity", "美术馆导览（没有活动事实）", no_facts
            ),
            LifeItemCandidate(
                "food.choir_mislabeled",
                "food_plan",
                "社区合唱（标成了食品方案）",
                item("k51.experience.community_choir", "k51.experience.choir_as_food"),
            ),
            LifeItemCandidate(
                "activity.age_limited",
                "activity",
                "少儿合唱（带年龄限制）",
                item(
                    "k51.experience.community_choir",
                    "k51.experience.choir_kids",
                    attributes={"age_range": "At least 6 but less than 12"},
                ),
            ),
            # 绕不过 E1 的其他检查
            LifeItemCandidate(
                "food.wrong_currency",
                "food_plan",
                "川味家常方案（报价口径不对）",
                item(
                    "k51.food.sichuan_home",
                    "k51.food.sichuan_wrong_currency",
                    price={
                        "amount": {"amount": 400, "numeraire": "USD_2023_LOCAL"},
                        "billing": "per_month",
                        "per_unit": "plan-month",
                    },
                ),
            ),
            LifeItemCandidate(
                "activity.undeclared_category",
                "activity",
                "合唱（类别没有声明）",
                item(
                    "k51.experience.community_choir",
                    "k51.experience.choir_mystery",
                    category="mystery_class",
                ),
            ),
            # 真实资料的典型缺口
            LifeItemCandidate(
                "food.basket_regional", "food_plan", "地区食材篮子（只有食材价格）", basket
            ),
            LifeItemCandidate(
                "food.fresh_unknown", "food_plan", "川味家常方案（新鲜餐数未知）", fresh_unknown
            ),
            LifeItemCandidate(
                "activity.park_class",
                "activity",
                "公园活动课（调用方转来的日期与年龄限制）",
                item("k51.experience.community_choir", "k51.experience.park_class"),
                ("2025-06-02 至 2025-08-15，每周一 from 9 to 10", "Ages 6-12"),
            ),
            LifeItemCandidate(
                "activity.tasting_dinner", "activity", "品鉴晚餐（代替一顿晚餐）", tasting
            ),
            LifeItemCandidate("equipment.racket", "equipment", "球拍（寿命不明）", gear),
            LifeItemCandidate("enjoyment.luxury_spa", "enjoyment", "高级水疗", spa),
            LifeItemCandidate(
                "enjoyment.massage_unknown",
                "enjoyment",
                "按摩（报价未知）",
                item(
                    "k51.service.massage",
                    "k51.service.massage_unknown_price",
                    availability="unknown",
                    price=None,
                ),
            ),
        )
        sample = label(
            "controlled_fixture",
            "product.scenarios.life_item_compilation",
            "合成样例的候选与按审核反例、真实资料典型缺口改写的候选（不是真实资料）",
        )
        return compile_life_items(
            bundle,
            candidates,
            meta=s.pack.meta(sample),
            city_ref=K51,
            scope_zh=(
                "生活扩展候选：完整食品方案、休闲活动、装备与享受型用品或服务；检查能不能交给 E1 执行，不补价格、寿命、时间或限制；"
                "只有受控样例，还没有接真实资料导出"
            ),
        )
