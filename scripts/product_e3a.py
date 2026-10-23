"""E3-A 产品共享结构与离线闭环的入口（DECISIONS/0034）。零网络、零费用，不调用任何真实模型。

    uv run python scripts/product_e3a.py export-schema [--out contracts/product/v3]
        从权威结构（src/common/product_contracts.py）导出 JSON Schema 与 index.json
    uv run python scripts/product_e3a.py check-schema [--dir contracts/product/v3]
        一致性检查：现在的结构导出的 schema 与目录里的文件逐字节相同；全部公共样例按结构校验、带合成出处标签、不含内部路径或凭证
    uv run python scripts/product_e3a.py samples [--out contracts/product/v3/samples] [--work <新目录>]
        用真实代码路径重新生成全部公共样例（合成人物；离线闭环、受控夹具与标明为示例的契约响应）
    uv run python scripts/product_e3a.py run-offline --out <新目录>
        一条正常闭环：原始答案 → 审阅 → 确认 → 编译 → E1 预检查与冻结 → 提交 → mock 三年 → E1 评价与汇总 → 产品结果
    uv run python scripts/product_e3a.py reject-example --out <新目录>
        被正确拒绝的例子：设备有报价但寿命未知（不编入 E1）、职业在本地没有岗位、金额没有合法换算

``--out`` 与 ``--work`` 必须是还不存在的目录（不覆盖已有证据）。
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.product_contracts import (
    CONVERSION_VERSION,
    HEALTH_QUESTIONNAIRE_VERSION,
    PRODUCT_MESSAGES,
    PRODUCT_SCHEMA_VERSION,
    QUESTIONNAIRE_VERSION,
    VOCABULARY_VERSION,
    CostStatus,
    ProductModel,
    RunProgress,
    YearFacts,
)
from common.run_store import RunStore

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO / "contracts/product/v3"
#: 样例文件可以装的结构：共享消息，加上结果里的一年生活事实（受控夹具展示部分月份没有买到服务）。
SAMPLE_TYPES: dict[str, type[ProductModel]] = {**PRODUCT_MESSAGES, "YearFacts": YearFacts}
#: 公共样例不能出现的东西：绝对路径、用户目录、凭证。
FORBIDDEN_IN_SAMPLES = (
    re.compile(r"\b[A-Za-z]:[\\/]"),
    re.compile(r"\\Users\\|/Users/|/home/"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"DASHSCOPE_API_KEY|Authorization|Bearer "),
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _fresh(path: Path) -> Path:
    if path.exists():
        raise SystemExit(f"{path} 已经存在：不覆盖已有目录，请换一个新目录")
    return path


# ---------------------------------------------------------------------- schema


def schema_texts() -> dict[str, str]:
    """权威结构导出的全部 schema 文件（文件名 → 文字）与 index.json。"""
    out = {
        f"{name}.schema.json": _json(cls.model_json_schema())
        for name, cls in PRODUCT_MESSAGES.items()
    }
    out["index.json"] = _json(
        {
            "schema_version": PRODUCT_SCHEMA_VERSION,
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "vocabulary_version": VOCABULARY_VERSION,
            "health_questionnaire_version": HEALTH_QUESTIONNAIRE_VERSION,
            "conversion_version": CONVERSION_VERSION,
            "source": "src/common/product_contracts.py（唯一权威结构；这些文件由 scripts/product_e3a.py export-schema 导出，不要手改）",
            "messages": {
                name: {
                    "file": f"{name}.schema.json",
                    "sha256": hashlib.sha256(
                        out[f"{name}.schema.json"].encode("utf-8")
                    ).hexdigest(),
                }
                for name in PRODUCT_MESSAGES
            },
        }
    )
    return out


def export_schema(out: Path) -> None:
    for name, text in schema_texts().items():
        _write(out / name, text)
    print(f"导出 {len(PRODUCT_MESSAGES)} 份 schema 与 index.json 到 {out}")


def sample_problems(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    cls = SAMPLE_TYPES.get(data.get("message", ""))
    if cls is None:
        return [f"{path.name}: 不认识的消息类型 {data.get('message')!r}"]
    try:
        obj = cls.model_validate(data["payload"])
    except Exception as exc:
        return [f"{path.name}: 不符合 {cls.__name__} 的结构：{exc}"[:600]]
    meta = getattr(obj, "meta", None)
    label = data.get("sample") if meta is None else meta.sample
    if label is None:
        problems.append(f"{path.name}: 没有样例出处标签（origin / synthetic）")
    if not data.get("description_zh"):
        problems.append(f"{path.name}: 没有中文说明")
    text = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_IN_SAMPLES:
        if pattern.search(text):
            problems.append(f"{path.name}: 含有不该公开的内容（{pattern.pattern}）")
    return problems


def check_schema(directory: Path) -> int:
    problems: list[str] = []
    for name, text in schema_texts().items():
        path = directory / name
        if not path.exists():
            problems.append(f"缺少 {name}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"{name} 与现在的权威结构导出的不同（结构改了要重新导出并升级版本）")
    extra = {p.name for p in directory.glob("*.schema.json")} - set(schema_texts())
    problems += [f"多出 {n}（权威结构里没有这份消息）" for n in sorted(extra)]
    samples = sorted((directory / "samples").glob("*.json"))
    if not samples:
        problems.append("没有公共样例")
    for s in samples:
        problems += sample_problems(s)
    for p in problems:
        print("✗", p)
    print(
        f"schema {len(PRODUCT_MESSAGES)} 份、样例 {len(samples)} 份：{'通过' if not problems else f'{len(problems)} 个问题'}"
    )
    return 1 if problems else 0


# ---------------------------------------------------------------------- samples


def _sample_file(
    out: Path, name: str, message: str, description: str, payload: Any, **extra: Any
) -> None:
    body = {"message": message, "description_zh": description, **extra, "payload": payload}
    _write(out / f"{name}.json", _json(body))


def generate_samples(out: Path, work: Path) -> None:
    """全部公共样例：每一份都由真实代码路径产生（出处写在消息的 meta.sample 里）。"""
    from product import scenarios as sc
    from product.scenarios import label

    out.mkdir(parents=True, exist_ok=True)
    dump = lambda m: m.model_dump(mode="json")  # noqa: E731

    caps = sc.capabilities(work / "caps")
    _sample_file(
        out,
        "01_capabilities",
        "ProductCapabilities",
        "合成资料包发布的能力清单：城市、职业匹配、六维与物质题干、健康问卷、硬要求与属性偏好的支持状态",
        dump(caps),
    )

    n = sc.closed_loop(work / "normal", "normal")
    assert n.result is not None and n.receipt is not None and n.duplicate is not None
    _sample_file(
        out,
        "02_normal_draft",
        "UserInputDraft",
        "正常填写：全职文员、六维 30/20/20/10/10/10、物质四项 25/25/30/20、健康问卷一题暂不确定、通勤上限 60 分钟",
        dump(n.draft),
    )
    _sample_file(
        out,
        "03_normal_review",
        "ProfileReview",
        "正常填写的审阅：可以确认；只需知悉标准开局",
        dump(n.review),
    )
    _sample_file(
        out,
        "04_normal_confirmed",
        "ConfirmedProfile",
        "用户确认的画像：精确分数、来源与内容身份",
        dump(n.confirmed),
    )
    _sample_file(
        out,
        "05_normal_preflight",
        "ProductPreflight",
        "运行前检查通过：两座合成城、每城一个分身、三年；逐城的可执行 / 可评分 / 可反馈与运行绑定",
        dump(n.pre.preflight),
    )
    _sample_file(
        out,
        "06_normal_submission",
        "RunSubmission",
        "提交：引用确认与预检查、带幂等键",
        dump(n.submission),
    )
    _sample_file(
        out,
        "07_normal_receipt_accepted",
        "RunReceipt",
        "本地离线桥接接受提交（不是托管服务）",
        dump(n.receipt),
    )
    _sample_file(
        out,
        "08_duplicate_receipt",
        "RunReceipt",
        "同一幂等键、同一预检查的重复提交：返回原运行 ID，不再生成一批分身",
        dump(n.duplicate),
    )
    _sample_file(
        out,
        "09_normal_progress_completed",
        "RunProgress",
        "运行完成的进度（从运行归档投影）",
        dump(n.progress),
    )
    _sample_file(
        out,
        "10_normal_result",
        "ProductResult",
        "正常闭环的产品结果：K51 买设备包、M52 按月买杂务服务；两座城各 1 个完整分身",
        dump(n.result),
    )

    amb = sc.review_only(
        work / "ambiguous",
        "ambiguous",
        occupation_text="我在餐厅做店员，有时收银，有时做饮品",
        selected=None,
    )
    _sample_file(
        out,
        "11_occupation_ambiguous_review",
        "ProfileReview",
        "职业歧义：给出三个可读候选（不展示分类代码），等用户选择",
        dump(amb.review),
    )
    zero = sc.review_only(
        work / "zero",
        "unanswered_zero",
        six={
            "material": "0",
            "safety": "40",
            "opportunity": "30",
            "leisure": None,
            "environment": "indifferent",
            "commute": "30",
        },
        material={
            "housing_space": "50",
            "food_life": "50",
            "daily_convenience": "0",
            "personal_enjoyment": "0",
        },
    )
    _sample_file(
        out,
        "12_unanswered_and_zero_review",
        "ProfileReview",
        "未答与明确 0：物质给 0 是已提供的数值（细分不适用、不伪造回答）；休闲未答、环境选了无所谓，都不会被换成 0",
        dump(zero.review),
    )
    eq = sc.review_only(
        work / "equal", "equal_split", six_mode="equal_split", material_mode="equal_split"
    )
    _sample_file(
        out,
        "13_equal_split_review",
        "ProfileReview",
        "主动均分：六维每项 50/3（显示 16.7）、物质每项 25（显示 25.0）；精确合计 100，显示值不回流计算",
        dump(eq.review),
    )
    _sample_file(
        out,
        "14_equal_split_confirmed",
        "ConfirmedProfile",
        "主动均分确认后的精确分数",
        dump(eq.confirmed),
    )
    unsure = sc.review_only(
        work / "unsure",
        "health_unsure",
        health_choices={
            "hc_more_pay_more_sick_risk": "unsure",
            "hc_reserve_for_health_costs": None,
            "hc_wait_for_clear_risk_info": "no_clear_tendency",
        },
    )
    _sample_file(
        out,
        "15_health_unsure_confirmed",
        "ConfirmedProfile",
        "健康问卷：一题暂不确定、一题未答、一题无明显倾向；照实保存，不补中间值，不计算总分",
        dump(unsure.confirmed),
    )
    laundry = sc.review_only(
        work / "laundry",
        "legacy_laundry",
        material={"housing_space": "30", "food_life": "30", "laundry": "40"},
    )
    _sample_file(
        out,
        "16_legacy_laundry_review",
        "ProfileReview",
        "旧问卷的洗衣点数：不能自动当作日常生活便利，需要重新确认",
        dump(laundry.review),
    )
    attrs = sc.review_only(
        work / "attrs",
        "attributes",
        attributes=(
            {
                "preference_id": "pref.brand",
                "need": "daily_convenience",
                "kind": "brand",
                "raw_text": "家电更喜欢国产品牌",
            },
            {
                "preference_id": "pref.cuisine",
                "need": "food_life",
                "kind": "cuisine",
                "raw_text": "喜欢清淡的江浙菜",
            },
            {
                "preference_id": "pref.no_strangers",
                "need": "daily_convenience",
                "kind": "home_entry_concern",
                "raw_text": "不让陌生人进家",
                "as_hard_requirement": True,
            },
        ),
    )
    _sample_file(
        out,
        "17_attributes_and_home_entry_review",
        "ProfileReview",
        "品牌、菜系未评分（只留存）；“不让陌生人进家”被标为不可妥协，但本版没有执行保障，明确报告不支持",
        dump(attrs.review),
    )

    relabel = label(
        "offline_run",
        "product.catalog.compile_catalog / inventory_coverage",
        "正常闭环编译的便利目录与它的选项目录覆盖",
    )
    comp = n.pre.compiled.catalog.model_copy(
        update={"meta": n.pre.compiled.catalog.meta.model_copy(update={"sample": relabel})}
    )
    inv = n.pre.inventory
    _sample_file(
        out,
        "18_catalog_compilation",
        "CatalogCompilation",
        "资料包的便利候选编译：合法设备与服务进入 E1；报价未知编成资料未知；寿命未知、效果未知不编入并写明原因",
        dump(comp),
    )
    _sample_file(
        out,
        "19_option_inventory_coverage",
        "OptionInventoryCoverage",
        "选项目录覆盖：声明范围内两城都是 partial（有未解决的候选）；做饭设备在范围之外记为排除；分母不因过滤变小",
        dump(inv),
    )
    bcomp, _binv = sc.catalog_boundary(work / "catalog_boundary")
    _sample_file(
        out,
        "20_catalog_boundary_compilation",
        "CatalogCompilation",
        "受控夹具：观测形状的报价＋估计寿命＋合成效果（编入，整条按最弱来源标合成）、2.5 年寿命（不编入、不取整）、按次上门（不编入）",
        dump(bcomp),
    )

    nop = sc.closed_loop(
        work / "no_purchase",
        "no_purchase",
        person="u502",
        draft_kwargs={
            "material": {
                "housing_space": "50",
                "food_life": "50",
                "daily_convenience": "0",
                "personal_enjoyment": "0",
            },
            "hard_requirements": (),
        },
    )
    _sample_file(
        out,
        "21_no_purchase_result",
        "ProductResult",
        "日常生活便利 0 分：没有购买设备或服务，资源账完整，每月 q = 0（确定的 0，不是未知），轨迹照常评分",
        dump(nop.result),
    )
    facts, info = sc.partial_service_months(work / "partial")
    _sample_file(
        out,
        "22_partial_service_months",
        "YearFacts",
        "受控夹具：门店兼职的收入付完住房与食品只剩约 120，计划全年买每月 216 的杂务服务；钱不够的月份没有买到服务（家务回到本人自己做）",
        dump(facts),
        sample=dump(
            label(
                "controlled_fixture",
                "product.scenarios.partial_service_months",
                "写好的计划（不是规划器输出）；开局现金改成 300 只为演示",
            )
        ),
        e1_facts=info,
    )

    major = {
        "mode": "injected",
        "stream_version": "e1-5c-illness-stream-v1",
        "note": "受控夹具：注入大病",
        "injected": [],
    }
    mixed_events = copy.deepcopy(major)
    mixed_events["injected"] = [
        {
            "person_id": "u503",
            "repeat_index": 1,
            "year_index": 1,
            "month": 1,
            "severity": "major",
            "note": "受控夹具：重复序号 1 第 1 年第 1 月大病",
        }
    ]
    mixed = sc.closed_loop(
        work / "mixed",
        "mixed_cem",
        person="u503",
        origin="controlled_fixture",
        note="受控夹具：重复序号 1 注入大病（两城配对）；M52 的重复序号 0 的 mock 第 1 年只回不是 JSON 的文字",
        repeat_indexes=(0, 1),
        illness_events=mixed_events,
        scripts=(_script("u503.m52.r0", "malformed_output", (1,)),),
    )
    _sample_file(
        out,
        "23_mixed_cem_result",
        "ProductResult",
        "C / E / M 混合：K51 一个完整、一个出局；M52 一个模拟问题（规划修订耗尽，不是出局）、一个出局，所以 M52 的 C = 0、均值为空；"
        "通勤硬要求在 M52 只有 1 人可核验、1 人没有可信记录（未核验，不当作满足）",
        dump(mixed.result),
    )
    allx_events = copy.deepcopy(major)
    allx_events["injected"] = [
        {
            "person_id": "u504",
            "repeat_index": 0,
            "year_index": 1,
            "month": 1,
            "severity": "major",
            "note": "受控夹具：第 1 年第 1 月大病",
        }
    ]
    allx = sc.closed_loop(
        work / "all_exit",
        "all_exit",
        person="u504",
        origin="controlled_fixture",
        note="受控夹具：重复序号 0 注入大病（两城配对）",
        draft_kwargs={"hard_requirements": (), "cash": ("500", "USD", "2024")},
        illness_events=allx_events,
    )
    _sample_file(
        out,
        "24_all_exit_result",
        "ProductResult",
        "全部出局：均值与模拟缺失范围都为空（不补 0）；出局的事件链取自环境记录；出局之后的月份没有资源记录（家务小时显示 —）",
        dump(allx.result),
    )
    stop = sc.closed_loop(
        work / "interrupted",
        "interrupted",
        person="u506",
        origin="controlled_fixture",
        note="受控夹具：第 2 次规划调用之后中断",
        stop_after_calls=2,
    )
    _sample_file(
        out,
        "25_not_generated_result",
        "ProductResult",
        "还没有生成评价：不擅自给城市分类或分数",
        dump(stop.result),
    )
    _sample_file(
        out,
        "26_interrupted_progress",
        "RunProgress",
        "中断之后的进度：可以恢复，还没有报告",
        dump(stop.progress),
    )
    paused = sc.closed_loop(
        work / "paused",
        "paused",
        person="u507",
        origin="controlled_fixture",
        note="受控夹具：mock 在第 1 个分身第 1 年抛出意外异常",
        scripts=(_script("u507.k51.r0", "raise_error", (1,)),),
    )
    _sample_file(
        out,
        "27_paused_progress",
        "RunProgress",
        "程序错误暂停批次：出错的分身记程序错误，没轮到的分身保留为未运行",
        dump(paused.progress),
    )
    _sample_file(
        out,
        "27b_paused_result",
        "ProductResult",
        "暂停批次的结果：程序错误是模拟问题（M），不写成生活出局",
        dump(paused.result),
    )

    def add_holding(data: dict[str, Any]) -> None:
        data["initial_states"][0]["holdings"] = [
            {
                "opportunity_id": "k51.durable.convenience_pack",
                "quantity": 1,
                "acquired_year": 0,
                "remaining_months": 24,
            }
        ]

    rejected = sc.preflight_only(
        work / "start_rejected",
        "start_rejected",
        origin="controlled_fixture",
        mutate_life_input=add_holding,
    )
    _sample_file(
        out,
        "28_standard_start_rejected_preflight",
        "ProductPreflight",
        "受控夹具：往开局塞一件旧设备，E1 的标准开局检查拒绝（不删人、不改状态），产品预检查报告阻断（内部路径已隐去）",
        dump(rejected.pre.preflight),
    )
    cashier = sc.preflight_only(
        work / "cashier", "cashier", occupation_text="我在超市做收银", selected="occ.cashier"
    )
    _sample_file(
        out,
        "29_start_job_unmatched_preflight",
        "ProductPreflight",
        "职业在两座合成城都没有同一分类的目录岗位：不能生成开局，不自动换成别的职业",
        dump(cashier.pre.preflight),
    )
    money = sc.review_only(work / "money", "money_blocked", cash=("10000", "USD", "2026-10"))
    _sample_file(
        out,
        "30_money_conversion_blocked_review",
        "ProfileReview",
        "“当前 10,000 美元”：没有合法的时期换算依据，不能直接改标签成 2024 年当地美元",
        dump(money.review),
    )
    stale = _stale_submission(work / "stale")
    _sample_file(
        out,
        "31_stale_submission_receipt",
        "RunReceipt",
        "答案在预检查之后改过：旧确认与旧预检查失效，提交被拒绝",
        dump(stale),
    )
    req = {
        "method": "GET",
        "path": f"/runs/{n.receipt.run_id}/progress",
        "note": "示例：线上查询接口尚未实现（E3-C/E5-B）；离线等价物是 product.bridge.progress(运行归档)",
    }
    disconnect = n.progress.model_copy(
        update={
            "meta": n.progress.meta.model_copy(
                update={
                    "sample": label(
                        "contract_example",
                        "product.bridge.progress",
                        "断线后按运行 ID 查询同一运行：不重发付费规划、不生成新的一批分身。HTTP 层是示例，未实现",
                    )
                }
            )
        }
    )
    _sample_file(
        out,
        "32_contract_example_disconnect_query",
        "RunProgress",
        "断线后查询同一运行（契约示例；响应内容来自真实归档的进度投影）",
        dump(disconnect),
        request_example=req,
    )
    # 服务失败让批次停下：离线 mock 遇不到，形状手写；公开说明照样由白名单函数从结构化状态生成（不写原因原文）
    from common.life_planning_contracts import RunProgress as E1RunProgress
    from product.public_errors import progress_pause

    e1_halted = E1RunProgress(
        run_id="run-e3a-example-halted",
        status="halted",
        halt_kind="service_unavailable",
        calls_used=3,
        call_limit=18,
        order=(),
        clones=(),
        pause_reason="（内部原因原文只留在运行归档里，这里是手写示例）",
    )
    halted = RunProgress(
        meta=n.pack.meta(
            label(
                "contract_example",
                "hand-written",
                "服务失败让批次停下的契约示例：离线 mock 不会遇到服务失败，这份响应是手写的形状示例，不是运行结果",
            )
        ),
        run_id="run-e3a-example-halted",
        stage="halted",
        stage_zh="批次停下（服务、预算或请求检查）",
        pause=progress_pause(e1_halted, report_available=False, resumable=True),
        cost=CostStatus(
            kind="estimated_from_quote",
            calls_used=3,
            call_limit=18,
            estimated_cost="示例：按报价估算 0.03 元",
            note="真实模型运行只写“按报价估算”，以供应商账单为准",
        ),
        resumable=True,
        resume_note="所有者决定之后可以恢复（尝试次数与预算不清零）",
        report_available=False,
    )
    _sample_file(
        out,
        "33_contract_example_halted_progress",
        "RunProgress",
        "服务失败暂停（契约示例，手写形状，不是运行结果）",
        dump(halted),
    )
    unknown = sc.positive_weight_data_unknown(work / "unknown_safety")
    _sample_file(
        out,
        "34_positive_weight_data_unknown_preflight",
        "ProductPreflight",
        "受控夹具：M52 的安全资料未知而用户给安全 20 分——预检查明确报告缺口（M52 可评分 partial），不删掉安全、不把其余权重重新归一化",
        dump(unknown.pre.preflight),
    )
    # E3-A 收尾 F2 / F3 / F4 的拒绝与状态样例
    scomp, sinv = sc.catalog_statuses(work / "catalog_statuses")
    _sample_file(
        out,
        "35_catalog_statuses_compilation",
        "CatalogCompilation",
        "受控夹具：确定没有供给、供给未知、参与小时为负、缺计价单位、只有报价有来源、来源字段不对——逐候选给出原因，其余候选照常编译",
        dump(scomp),
    )
    _sample_file(
        out,
        "36_catalog_statuses_coverage",
        "OptionInventoryCoverage",
        "受控夹具的覆盖报告：供给、资料与编译三种状态分开；确定没有是已确定的结果（已解决）但不能购买；分母包括全部调查过的候选",
        dump(sinv),
    )
    sep = sc.review_only(work / "money_sep", "money_separator", cash=("3000,50", "USD", "2024"))
    _sample_file(
        out,
        "37_money_separator_rejected_review",
        "ProfileReview",
        "“3000,50”：逗号不能当小数点，系统不猜你的意思（以前会被当成 300,050）——请按 3000.50 或 3,000.50 重填",
        dump(sep.review),
    )
    dup = sc.duplicate_answers(work / "duplicates")
    _sample_file(
        out,
        "38_duplicate_answers_rejected_review",
        "ProfileReview",
        "受控夹具：同一健康题两个答案、同一资格两条、两条通勤上限——确认前全部拒绝，不按最后一个算，也不自动取更严格的上限",
        dump(dup.review),
    )
    print(f"写出 {len(list(out.glob('*.json')))} 份样例到 {out}")


def _script(clone: str, behavior: str, years: tuple[int, ...]) -> Any:
    from common.life_planning_contracts import MockScript

    return MockScript(clone_id=clone, behavior=behavior, years=years, note="受控夹具")  # type: ignore[arg-type]


def _stale_submission(work: Path) -> Any:
    """答案改过之后用旧预检查提交：被拒绝。"""
    from product import scenarios as sc
    from product.answers import confirm_profile, review_draft
    from product.bridge import submit
    from product.fixtures import make_draft
    from product.pack import load_pack
    from product.scenarios import label

    s = sc.preflight_only(work, "stale", person="u508")
    with contextlib.chdir(s.work):
        pack = load_pack()
        edited = make_draft(
            pack,
            draft_id="draft.u508.stale",
            revision=2,
            six={
                "material": "40",
                "safety": "10",
                "opportunity": "20",
                "leisure": "10",
                "environment": "10",
                "commute": "10",
            },
        )
        review = review_draft(pack, edited)
        confirmed2 = confirm_profile(
            pack, edited, person_ref="u508", acknowledged=review.pending_confirmations
        )
        old = sc._submission(pack, s.confirmed, s.pre, "idem-u508-stale")
        return submit(
            pack,
            confirmed2,
            s.pre.preflight,
            old,
            Path("registry"),
            sample=label("offline_run", "product.bridge.submit", "答案改过之后用旧预检查提交"),
        )


# ---------------------------------------------------------------------- 正常闭环与拒绝例子（证据）


def run_offline_cmd(out: Path) -> None:
    from product import scenarios as sc

    out = _fresh(out)
    n = sc.closed_loop(out / "workspace", "normal")
    assert n.result is not None
    for name, obj in (
        ("draft", n.draft),
        ("review", n.review),
        ("confirmed", n.confirmed),
        ("preflight", n.pre.preflight),
        ("submission", n.submission),
        ("receipt", n.receipt),
        ("duplicate_receipt", n.duplicate),
        ("progress", n.progress),
        ("catalog_compilation", n.pre.compiled.catalog),
        ("option_inventory_coverage", n.pre.inventory),
        ("result", n.result),
    ):
        _write(out / "messages" / f"{name}.json", _json(obj.model_dump(mode="json")))
    summary = {
        "run_id": n.outcome.run_id,
        "status": n.outcome.status,
        "planner_calls": n.outcome.calls_used,
        "real_model_calls": 0,
        "provider_calls_recorded": RunStore(n.work / "archive/normal").has("provider_calls"),
        "clones": [
            {"clone": r.clone_id, "status": r.status, "years": r.years_committed}
            for r in n.outcome.records
        ],
        "cities": [c.headline_zh for c in n.result.cities],
    }
    _write(out / "summary.json", _json(summary))
    print(_json(summary))


def reject_example_cmd(out: Path) -> None:
    from product import scenarios as sc

    out = _fresh(out)
    n = sc.preflight_only(out / "workspace", "reject_examples")
    comp = n.pre.compiled.catalog
    robot = next(
        u for u in comp.uncompiled if u.candidate_id == "cand.k51.device.robot-lifetime-unknown"
    )
    cashier = sc.preflight_only(
        out / "workspace", "cashier", occupation_text="我在超市做收银", selected="occ.cashier"
    )
    money = sc.review_only(out / "workspace", "money", cash=("10000", "USD", "2026-10"))
    k51 = next(c for c in n.pre.inventory.cities if c.city_ref == "US:SYNTH2026:K51")
    data = {
        "device_lifetime_unknown": robot.model_dump(mode="json"),
        "device_in_e1_catalog": any(
            o.opportunity_id == "k51.durable.robot"
            for o in n.pre.world.bundle.catalog_for("US:SYNTH2026:K51").opportunities
        ),
        "k51_inventory_item": next(
            i.model_dump(mode="json") for i in k51.items if i.candidate_id == robot.candidate_id
        ),
        "start_job_unmatched": [i.model_dump(mode="json") for i in cashier.pre.preflight.issues],
        "money_conversion_missing": [
            i.model_dump(mode="json")
            for i in money.review.issues
            if i.code == "money_conversion_missing"
        ],
    }
    _write(out / "rejections.json", _json(data))
    print(_json(data))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export-schema")
    p.add_argument("--out", type=Path, default=SCHEMA_DIR)
    p = sub.add_parser("check-schema")
    p.add_argument("--dir", type=Path, default=SCHEMA_DIR)
    p = sub.add_parser("samples")
    p.add_argument("--out", type=Path, default=SCHEMA_DIR / "samples")
    p.add_argument("--work", type=Path, default=None)
    for name in ("run-offline", "reject-example"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.cmd == "export-schema":
        export_schema(args.out)
    elif args.cmd == "check-schema":
        return check_schema(args.dir)
    elif args.cmd == "samples":
        if args.work is None:
            work = Path(tempfile.mkdtemp(prefix="e3a-samples-"))
        else:
            work = _fresh(args.work)
        if args.out.exists():
            shutil.rmtree(args.out)  # 样例目录由这个命令整份生成：先清空，避免留下旧文件
        generate_samples(args.out, work)
    elif args.cmd == "run-offline":
        run_offline_cmd(args.out)
    elif args.cmd == "reject-example":
        reject_example_cmd(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
