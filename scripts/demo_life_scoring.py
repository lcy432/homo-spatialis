"""E1-3 探索月度评分的离线演示（`DECISIONS/0018`）。零大模型调用。

    uv run python scripts/demo_life_scoring.py --out runs/2026-10-06-e1-3-scoring-demo

读 E1-3 合成生活输入与评分配置，按 E1-2 规则结算每份计划，对每个完成或出局的年份做
探索月度评分：研究路径用合成真值；产品路径只用用户明确给出的数值，不够就报"偏好信息
不足"。把输入指纹、结果与检查说明写进一个**新的**运行目录——目录已存在就拒绝，不覆盖
旧结果。代码没有提交时，记录 dirty 状态与关键源文件指纹，而不是只写一个旧 HEAD。

**不是现实城市的幸福估计，不输出城市排行。** 结果只按"分身 × 年 × 偏好来源"列出。

退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入/配置不合法。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.life_contracts import LifeContractError
from common.life_scoring_contracts import ExploratoryYearScore, PreferenceInsufficient
from econ.life_scoring import (
    preference_from_research_truth,
    preference_from_stated_profile,
    score_year,
    source_table,
)
from econ.life_settlement import SETTLEMENT_CONVENTION, binding_from_policy, settle_plans
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import load_scoring_config
from worlds.dimensions import ALL_DIMENSIONS

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_3.yaml")
SCORING = Path("configs/life/scoring_e1_3.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/common/life_billing.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/agentlayer/life_views.py",
    "src/worlds/dimensions.py",
    "scripts/demo_life_scoring.py",
)
TOL = 1e-12


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment(life: Path, scoring: Path) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, str(life), str(scoring)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.4f}"


def _score_table(score: ExploratoryYearScore) -> list[str]:
    head = "| 月 | 状态 | 月度分 u | " + " | ".join(ALL_DIMENSIONS) + " |"
    lines = [head, "|" + "---|" * (3 + len(ALL_DIMENSIONS))]
    for m in score.months:
        dims = {d.dimension: d.s for d in m.dimensions}
        cells = [_fmt(dims.get(d)) for d in ALL_DIMENSIONS]
        lines.append(f"| {m.month} | {m.status} | {_fmt(m.u)} | " + " | ".join(cells) + " |")
    return lines


def _checks(found: dict[tuple[str, int, str], ExploratoryYearScore]) -> list[tuple[str, bool, str]]:
    """手算核对（合成样例，见 synthetic_e1_3.yaml 文末与 ADR0018 第五节）。"""
    out: list[tuple[str, bool, str]] = []

    def close(name: str, actual: float | None, expected: float) -> None:
        ok = actual is not None and math.isclose(actual, expected, rel_tol=0, abs_tol=TOL)
        out.append((name, ok, f"实际 {actual!r}，手算 {expected!r}"))

    u01 = found[("u01.a31.c0", 1, "research_synthetic_truth")]
    close("u01 第 1 年第 2 月月度分", u01.months[1].u, 0.512)
    close("u01 第 1 年年均", u01.annual_mean, 6.0025 / 12)
    u01y2 = found[("u01.a31.c0", 2, "research_synthetic_truth")]
    close("u01 第 2 年年均（电脑与年卡跨年继续）", u01y2.annual_mean, (6 * 0.493 + 6 * 0.4805) / 12)
    (ws,) = u01.hard_requirements
    out.append(
        (
            "u01 工作空间硬要求：前 6 个月满足、后 6 个月不满足",
            ws.summary == "partially_satisfied" and ws.months_satisfied == (1, 2, 3, 4, 5, 6),
            f"{ws.summary}，满足月 {ws.months_satisfied}",
        )
    )
    close(
        "u02（A31）研究真值年均",
        found[("u02.a31.c0", 1, "research_synthetic_truth")].annual_mean,
        0.4695,
    )
    close(
        "u02（A31）用户陈述年均",
        found[("u02.a31.c0", 1, "product_stated_numeric")].annual_mean,
        0.4625,
    )
    b32 = found[("u02.b32.c0", 1, "research_synthetic_truth")]
    out.append(
        (
            "u02（B32）环境数据未知：十二个月都不完整，没有年均",
            b32.annual_status == "incomplete_months" and b32.annual_mean is None,
            f"{b32.annual_status}，年均 {b32.annual_mean}",
        )
    )
    u03 = found[("u03.a31.c0", 1, "research_synthetic_truth")]
    close("u03 第 2 月（降级后按单间评分，无失望扣分）", u03.months[1].u, 0.411)
    out.append(
        (
            "u03 第 3 月出局、之后不模拟：单列状态、没有年均",
            [m.status for m in u03.months[2:4]] == ["exit_month", "after_exit"]
            and u03.annual_mean is None,
            f"{[m.status for m in u03.months[:4]]}…，年均 {u03.annual_mean}",
        )
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-3 探索月度评分的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    ap.add_argument("--life", type=Path, default=LIFE)
    ap.add_argument("--scoring", type=Path, default=SCORING)
    args = ap.parse_args()

    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, life_report = load_life_bundle(args.life, require_settlement_ready=True)
        config, score_report = load_scoring_config(args.scoring, bundle)
    except LifeContractError as exc:
        print(exc)
        return 1

    results: list[dict[str, Any]] = []
    found: dict[tuple[str, int, str], ExploratoryYearScore] = {}
    summary = [
        "# E1-3 探索月度评分演示",
        "",
        "合成样例，参数是探索设定：**不是现实城市的幸福估计，不能据此排序城市。**",
        f"生活输入 `{args.life}`，评分配置 `{args.scoring}`（{config.spec.scoring_version}，"
        f"函数 {config.spec.function}）。记账能否执行：settlement_ready = "
        f"{str(life_report.settlement_ready).lower()}；探索评分能否执行："
        f"exploration_scoring_ready = {str(score_report.exploration_scoring_ready).lower()}；"
        f"正式评分：formal_scoring_ready = {str(score_report.formal_scoring_ready).lower()}。",
        "",
    ]
    for s in bundle.initial_states:
        plans = bundle.plans_for(s.clone_id)
        if not plans:
            continue
        cat = bundle.catalog_for(str(s.city))
        conditions = config.conditions_for(str(s.city))
        policy = bundle.tax_policy(cat.tax_policy_id) if cat else None
        if cat is None or conditions is None or policy is None:
            continue
        profile = next(p for p in bundle.stated_profiles if p.person_id == s.person_id)
        truth = next(t for t in bundle.internal_truths if t.person_id == s.person_id)
        settled = settle_plans(
            protocol=bundle.protocol,
            state=s,
            plans=plans,
            catalog=cat,
            tax=binding_from_policy(policy),
        )
        start = s
        for r in settled:
            entry: dict[str, Any] = {
                "clone_id": s.clone_id,
                "person_id": s.person_id,
                "city": str(s.city),
                "year_index": start.year_index,
                "settlement_status": r.status,
                "scores": {},
            }
            summary.append(f"## {s.clone_id} 第 {start.year_index} 年（结算：{r.status}）")
            summary.append("")
            if r.outcome is None:
                entry["problems"] = [p.model_dump(mode="json") for p in r.problems]
                results.append(entry)
                continue
            entry["cash_start"] = r.outcome.cash_start.amount if r.outcome.cash_start else None
            entry["cash_end"] = r.outcome.cash_end.amount if r.outcome.cash_end else None
            entry["exit"] = r.outcome.exit.model_dump(mode="json") if r.outcome.exit else None
            for pref in (
                preference_from_research_truth(truth),
                preference_from_stated_profile(profile),
            ):
                if isinstance(pref, PreferenceInsufficient):
                    entry["scores"][pref.source] = pref.model_dump(mode="json")
                    summary.append(f"- {pref.source}：偏好信息不足——{pref.message}")
                    summary.append("")
                    continue
                score = score_year(
                    protocol=bundle.protocol,
                    spec=config.spec,
                    conditions=conditions,
                    catalog=cat,
                    state=start,
                    outcome=r.outcome,
                    preference=pref,
                    hard_requirements=profile.hard_requirements,
                )
                found[(s.clone_id, start.year_index, pref.source)] = score
                entry["scores"][pref.source] = score.model_dump(mode="json")
                summary.append(
                    f"### {pref.source}：年度状态 {score.annual_status}，年均 {_fmt(score.annual_mean)}"
                )
                summary.append("")
                summary.append(f"_{score.explanation}_")
                summary.append("")
                summary.extend(_score_table(score))
                summary.append("")
                for h in score.hard_requirements:
                    summary.append(
                        f"- 硬要求 {h.need}：{h.summary}（满足 {list(h.months_satisfied)}，不满足 "
                        f"{list(h.months_not_satisfied)}，无法判断 {list(h.months_unknown)}，未生活 "
                        f"{list(h.months_not_lived)}）"
                    )
                for n in score.notes:
                    summary.append(f"- {n}")
                summary.append("")
            results.append(entry)
            if r.next_state is not None and r.next_state.status == "active":
                start = r.next_state

    summary += [
        "## 评分来源表",
        "",
        "| 分项 | 维度 | 来源 | 读哪个字段 | 取值 | 单位 | 合并 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in source_table(config.spec):
        summary.append(
            "| "
            + " | ".join(
                row[k]
                for k in (
                    "component",
                    "dimension",
                    "source",
                    "read_from",
                    "value",
                    "unit",
                    "combine",
                )
            )
            + " |"
        )

    checks = _checks(found)
    check_lines = [
        "# 手算核对",
        "",
        f"容差：绝对误差 ≤ {TOL}（评分是浮点运算；金额仍按整数分在结算里精确守恒）。",
        "",
    ]
    check_lines += [
        f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks
    ]

    args.out.mkdir(parents=True)
    env = _environment(args.life, args.scoring)
    (args.out / "environment.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    inputs = {
        "life_file": str(args.life),
        "life_sha256": _sha256(args.life),
        "scoring_file": str(args.scoring),
        "scoring_sha256": _sha256(args.scoring),
        "protocol_id": bundle.protocol.protocol_id,
        "spec_id": config.spec.spec_id,
        "scoring_version": config.spec.scoring_version,
        "scoring_function": config.spec.function,
        "settlement_convention": SETTLEMENT_CONVENTION,
        "life_report": life_report.model_dump(mode="json"),
        "scoring_report": score_report.model_dump(mode="json"),
    }
    (args.out / "inputs.json").write_text(
        json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-3 探索月度评分演示（运行证据）",
                "",
                "零大模型调用的离线演示。输入、结果与核对说明都在本目录；它们是合成探索设定下的",
                "结果，**不是现实城市的幸福估计，不是城市排行**。",
                "",
                "- `inputs.json`：输入文件与指纹、协议与评分规格版本、加载检查报告",
                "- `environment.json`：git HEAD、是否有未提交改动、关键源文件与配置的 SHA-256",
                "- `results.json`：每个分身每年的结算摘要与完整的逐月评分（研究 / 产品两种偏好来源）",
                "- `summary.md`：逐月表格、硬要求、说明与评分来源表",
                "- `checks.md`：手算核对",
                "",
                "复现：`uv run python scripts/demo_life_scoring.py --out <新目录>`。代码未提交时，以",
                "`environment.json` 的指纹为准，不以 HEAD 代表实际运行的代码。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for line in check_lines[4:]:
        print(line)
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
