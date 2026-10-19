"""E1-4A 月度时间与精力的离线演示（`DECISIONS/0020`，执行修订 r2）。零大模型调用。

    uv run python scripts/demo_life_e1_4a.py --out runs/<新目录>

读 E1-4A 合成生活输入，按月结算每份第一年计划（钱、时间、精力走同一条实际执行路径），并输出：

* 每个分身的逐月资源账（时间预算 / 已安排 / 未安排（月底作废），精力额度 / 负担 / 剩余，按类别的贡献，
  实际住房、往返分钟与到岗次数，检查类型：完整 / 只查已知下限 / 未发生）；
* 计划不可行时的资源诊断（月份、资源、额度、用量、超出量、主要贡献项；出局当月写明是已知下限）；
* 由原样例**派生**的修复验证情景（出局月超限、资金对照、远程出局月、两份临时工、两份工作超限），派生
  配置原样存进运行目录，文件头写明改了什么；原样例文件不改；
* 标准产品观察与研究隐藏精力观察的实际 payload（全文与精简摘录）；
* 与执行修订 r1 历史结果（``--compare-with``）的逐项比较：哪些数值保持、哪些结构或行为改变；
* 旧协议对照（E1-2 样例照旧）、确定性重放与"不可行试算不提交"的检查；
* 手算核对。

把配置副本、源码指纹、实际命令、协议、结果与核对写进一个**新的**运行目录——目录已存在就拒绝，
不覆盖旧结果。代码没有提交时，记录 dirty 状态与关键源文件指纹，不只写一个旧 HEAD。

**合成设定：** 负担系数、净可分配小时与额度都是手工设定，不是现实生活的可行性判断，也不排序城市。

退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    ObservationPermission,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    RESOURCE_RULES_REVISION,
    LifeContractError,
    MonthResources,
    SettlementResult,
    exact_minor_units,
)
from econ.life_settlement import SETTLEMENT_CONVENTION, binding_from_policy, settle_year
from ledger.life_catalog import LifeBundle, cross_validate, load_life_bundle, parse_bundle

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_4a.yaml")
OLD_LIFE = Path("configs/life/synthetic_e1_2.yaml")
R1_RESULTS = Path("runs/2026-10-07-e1-4a-resources/demo/results.json")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_billing.py",
    "src/ledger/life_catalog.py",
    "src/econ/life_resources.py",
    "src/econ/life_settlement.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_4a.py",
    "tests/life/test_resources_rules.py",
    "tests/life/test_resources_settlement.py",
    "tests/life/test_resources_compat.py",
)
CATEGORIES = ("work", "commute", "food_prep", "household_task", "service", "experience", "training")
LABEL = {
    "work": "工作",
    "commute": "通勤",
    "food_prep": "做饭",
    "household_task": "家务",
    "service": "服务",
    "experience": "体验",
    "training": "培训",
}
CHECK = {"complete": "完整检查", "known_lower_bound": "只查已知下限", "not_reached": "未发生"}
#: r1 的月份状态 → r2 的检查类型（字段改名，含义按 ADR0020 第十二节）
R1_STATUS = {"checked": "complete", "exit_month": "known_lower_bound", "not_reached": "not_reached"}
SECOND = "a51.job.parttime_second"
F = Fraction


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment(life: Path) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, str(life), str(OLD_LIFE)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "resource_rules_revision": RESOURCE_RULES_REVISION,
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _settle(bundle: LifeBundle, clone_id: str) -> SettlementResult:
    state = bundle.initial_state(clone_id)
    assert state is not None
    catalog = bundle.catalog_for(str(state.city))
    assert catalog is not None
    policy = bundle.tax_policy(catalog.tax_policy_id)
    assert policy is not None
    plan = next(p for p in bundle.plans if p.clone_id == clone_id and p.year_index == 1)
    return settle_year(
        protocol=bundle.protocol,
        state=state,
        action=plan,
        catalog=catalog,
        tax=binding_from_policy(policy),
    )


# ---------------------------------------------------------------------- 派生情景


def _opp(raw: dict, oid: str) -> dict:
    return next(
        o for c in raw["catalogs"] for o in c["opportunities"] if o["opportunity_id"] == oid
    )


def _state(raw: dict, clone: str) -> dict:
    return next(s for s in raw["initial_states"] if s["clone_id"] == clone)


def _plan(raw: dict, clone: str) -> dict:
    return next(p for p in raw["plans"] if p["clone_id"] == clone and p["year_index"] == 1)


def _remote_parttime(raw: dict) -> str:
    job = _opp(raw, "a51.job.parttime")
    job["spec"]["work_mode"] = "remote"
    job["resource"]["job"].update(work_mode="remote", onsite_days_per_month=0)
    return "a51.job.parttime 改成全远程（work_mode remote、到岗 0 次；工时 80、负担 1.0、月薪 1,000 不变）"


def _second_parttime(raw: dict) -> str:
    second = copy.deepcopy(_opp(raw, "a51.job.parttime"))
    second["opportunity_id"] = SECOND
    second["label"] = "第二份零售兼职（同一个到岗地点）"
    next(c for c in raw["catalogs"] if c["city"]["code"] == "A51")["opportunities"].append(second)
    return f"A51 加一份与 a51.job.parttime 完全相同的兼职 {SECOND}（每月 80 工时、到岗 10 次、月薪 1,000）"


def _derive(raw0: dict, name: str) -> tuple[dict, str, list[str]]:
    """由原样例派生一个修复验证情景。返回（派生配置，要结算的分身，改动清单）。"""
    raw = copy.deepcopy(raw0)
    changes: list[str] = []
    if name in ("exit_time_over", "exit_energy_over", "exit_cash_control", "exit_remote_ok"):
        clone = "u25.a51.c0"
        changes.append(_remote_parttime(raw))
        st = _state(raw, clone)
        st["cash"]["amount"] = 10000 if name == "exit_cash_control" else 0
        changes.append(f"{clone} 开局现金 {st['cash']['amount']}")
        if name in ("exit_time_over", "exit_cash_control"):
            st["time_budget_hours"] = 720
            changes.append(f"{clone} 年度净可分配小时 720（每月 60 小时）")
        if name == "exit_energy_over":
            st["energy"] = {**st["energy"], "level": 60}
            changes.append(f"{clone} 每月精力额度 60 负担点")
        return raw, clone, changes
    if name in ("two_jobs", "two_jobs_energy_over"):
        clone = "u21.a51.near"
        plan = _plan(raw, clone)
        if name == "two_jobs":
            changes.append(_second_parttime(raw))
            plan["items"] = [i for i in plan["items"] if ".job." not in i["opportunity_id"]]
            for oid in ("a51.job.parttime", SECOND):
                plan["items"].append(
                    {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
                )
            _state(raw, clone)["cash"]["amount"] = 10000
            changes.append(
                f"{clone} 的计划把全职文员换成两份兼职（与独立审核反例相同），开局现金 10,000"
            )
        else:
            plan["items"].append(
                {
                    "opportunity_id": "a51.job.parttime",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                }
            )
            changes.append(f"{clone} 的计划在全职文员之外再加 a51.job.parttime（全年）")
        return raw, clone, changes
    raise KeyError(name)


DERIVED = (
    "exit_time_over",
    "exit_energy_over",
    "exit_cash_control",
    "exit_remote_ok",
    "two_jobs",
    "two_jobs_energy_over",
)


# ---------------------------------------------------------------------- 人读表格


def _q(text: str) -> str:
    """精确分数的读法：整数照写，分数写成"分数（≈ 小数）"。"""
    q = F(text)
    return str(q) if q.denominator == 1 else f"{q}（≈{float(q):.2f}）"


def _by_cat(m: MonthResources, attr: str) -> dict[str, F]:
    out = {c: F(0) for c in CATEGORIES}
    for c in m.contributions:
        out[c.category] += getattr(c, attr).fraction()
    return out


def _month_table(months: tuple[MonthResources, ...]) -> list[str]:
    head = (
        "| 月 | 检查 | 实际住房 | 往返分钟 | 到岗次数 | "
        + " | ".join(f"{LABEL[c]} h" for c in CATEGORIES)
        + " | 已安排 / 预算 h | 未安排 h（月底作废） | 负担 / 额度 | 工作负担/额度 |"
    )
    rows = [head, "|" + "---|" * (head.count("|") - 1)]
    for m in months:
        h = _by_cat(m, "hours")
        lower = m.resource_check == "known_lower_bound"
        cells = [
            str(m.month),
            CHECK[m.resource_check],
            m.housing_opportunity_id or ("没有获得" if lower else "—"),
            "—" if m.commute_roundtrip_minutes is None else str(m.commute_roundtrip_minutes),
            str(m.onsite_visits),
            *(
                # 未发生的月份、出局当月没有获得的住房与食品等：写"—"，不写成零负担
                "—"
                if m.resource_check == "not_reached" or (lower and c not in ("work", "commute"))
                else _q(f"{h[c].numerator}/{h[c].denominator}")
                for c in CATEGORIES
            ),
            "—"
            if m.time_used is None
            else ("至少 " if lower else "")
            + f"{_q(m.time_used.exact)} / {_q(m.time_budget.exact)}",
            "—" if m.time_remaining is None else _q(m.time_remaining.exact),
            "—"
            if m.energy_used is None
            else ("至少 " if lower else "")
            + f"{_q(m.energy_used.exact)} / {_q(m.energy_quota.exact)}",
            "—" if m.work_relative_burden is None else _q(m.work_relative_burden.exact),
        ]
        if m.commute_status == "unknown_no_housing":
            cells[CATEGORIES.index("commute") + 5] = "未知"
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _problem_lines(r: SettlementResult) -> list[str]:
    out = []
    for p in r.problems:
        out.append(f"- `{p.code}`（第 {p.month} 月）：{p.message}")
        x = p.resource
        if x is not None:
            scope = "已知用量至少" if x.resource_check == "known_lower_bound" else "使用"
            out.append(
                f"  - 检查：{CHECK[x.resource_check]}（{x.rules_revision}）；额度 {_q(x.limit.exact)} {x.unit}，"
                f"{scope} {_q(x.used.exact)}，超出 {_q(x.excess.exact)}"
            )
            for c in x.top_contributors:
                v = c.hours if x.resource == "time" else c.burden
                out.append(
                    f"  - {LABEL[c.category]}：{c.opportunity_id or c.task} {_q(v.exact)}（{c.basis}）"
                )
    return out


def _result_json(r: SettlementResult, clone_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "clone_id": clone_id,
        "status": r.status,
        "problems": [p.model_dump(mode="json") for p in r.problems],
    }
    if r.outcome is not None:
        o = r.outcome
        out.update(
            energy_feasibility=o.energy_feasibility,
            time_used_hours_old_field=o.time_used_hours,
            energy_burden_recorded_old_field=o.energy_burden_recorded,
            cash_month_end=[m.cash_end.amount for m in o.months],
            income_gross=[m.income_gross.amount for m in o.months],
            income_tax=[m.income_tax.amount for m in o.months],
            spent=[m.spent.amount for m in o.months],
            exit=None if o.exit is None else o.exit.model_dump(mode="json"),
            items=[
                {
                    "opportunity_id": i.opportunity_id,
                    "status": i.status,
                    "covered_months": i.covered_months,
                }
                for i in o.items
            ],
            resources=None if o.resources is None else o.resources.model_dump(mode="json"),
            notes=list(o.notes),
            next_state_year=r.next_state.year_index if r.next_state else None,
        )
    return out


# ---------------------------------------------------------------------- 与 r1 比较


_NUMERIC = (
    "time_budget",
    "energy_quota",
    "time_used",
    "time_remaining",
    "energy_used",
    "energy_remaining",
    "work_relative_burden",
    "commute_relative_burden",
    "total_relative_burden",
)


def _contrib_key(c: dict) -> tuple:
    # r2 把通勤行归到岗位（r1 归到住房）：比较数值时通勤行不比 opportunity_id
    oid = None if c["category"] == "commute" else c["opportunity_id"]
    return (c["category"], oid, c["task"], c["hours"]["exact"], c["burden"]["exact"])


def _compare_with_r1(old: list[dict], new: list[dict]) -> dict[str, Any]:
    """逐个分身比较：状态、现金、问题、逐月资源数值。数值不同的逐项列出；结构变化单独列出。"""
    by_old = {x["clone_id"]: x for x in old}
    value_changes: list[str] = []
    expected_changes: list[str] = []
    same: list[str] = []
    for x in new:
        cid, y = x["clone_id"], by_old.get(x["clone_id"])
        if y is None:
            value_changes.append(f"{cid}: r1 没有这个分身")
            continue
        diffs: list[str] = []
        if x["status"] != y["status"]:
            diffs.append(f"状态 {y['status']} → {x['status']}")
        if x.get("cash_month_end") != y.get("cash_month_end"):
            diffs.append("月末现金不同")
        if [(p["code"], p["month"]) for p in x["problems"]] != [
            (p["code"], p["month"]) for p in y["problems"]
        ]:
            diffs.append("问题清单不同")
        for px, py in zip(x["problems"], y["problems"], strict=False):
            rx, ry = px.get("resource"), py.get("resource")
            if rx and ry:
                for k in ("limit", "used", "excess"):
                    if rx[k]["exact"] != ry[k]["exact"]:
                        diffs.append(f"诊断 {k} {ry[k]['exact']} → {rx[k]['exact']}")
        for mx, my in zip(
            (x.get("resources") or {}).get("months", []),
            (y.get("resources") or {}).get("months", []),
            strict=False,
        ):
            if mx["resource_check"] != R1_STATUS[my["month_status"]]:
                diffs.append(f"第 {mx['month']} 月检查类型改变")
            for k in _NUMERIC:
                a, b = mx.get(k), my.get(k)
                if (a and a["exact"]) != (b and b["exact"]):
                    diffs.append(
                        f"第 {mx['month']} 月 {k}: {b and b['exact']} → {a and a['exact']}"
                    )
            if (mx["job_workdays_sum"], mx["onsite_visits"]) != (my["workdays"], my["onsite_days"]):
                diffs.append(f"第 {mx['month']} 月到岗计数不同")
            if Counter(map(_contrib_key, mx["contributions"])) != Counter(
                map(_contrib_key, my["contributions"])
            ):
                diffs.append(f"第 {mx['month']} 月贡献项数值不同")
        if not diffs:
            same.append(cid)
        elif cid == "u25.a51.c0":
            expected_changes.extend(f"{cid}: {d}" for d in diffs)
        else:
            value_changes.extend(f"{cid}: {d}" for d in diffs)
    return {
        "same_values": same,
        "expected_rule_changes": expected_changes,
        "unexpected_value_changes": value_changes,
        "structural_changes": [
            "MonthResources.month_status → resource_check（checked → complete，exit_month → known_lower_bound，"
            "not_reached 不变）",
            "MonthResources.workdays → job_workdays_sum，onsite_days → onsite_visits（各岗位累计，不是日历天数）",
            "ResourceYear 增加 rules_revision；ResourceShortfall 增加 resource_check 与 rules_revision",
            "通勤贡献行的 opportunity_id 由住房改为对应岗位；住房写在月度记录与依据文字里",
            "出局当月 time_used / energy_used 由空改为已知用量（下限），剩余与 within_limits 仍为空",
            "出局年份的 energy_feasibility 由 monthly_quota_checked 改为 monthly_quota_checked_until_exit",
            "结果说明（notes）的文字写明检查范围与执行修订；VisibleResourceBudget 增加四条规则说明与修订",
        ],
    }


# ---------------------------------------------------------------------- 产品观察


def _payload(bundle: LifeBundle, permission: ObservationPermission) -> dict[str, Any]:
    """u21.a51.near 过完第一年、第二年做计划时拿到的 payload。"""
    r = _settle(bundle, "u21.a51.near")
    nxt = r.next_state
    assert nxt is not None
    profile = next(p for p in bundle.stated_profiles if p.person_id == nxt.person_id)
    obs = build_observation(
        permission=permission,
        profile=profile,
        state=nxt,
        catalog=bundle.catalog_for(str(nxt.city)),  # type: ignore[arg-type]
        last_feedback=visible_feedback(r),
        resource_rules=bundle.protocol.resource_rules,
    )
    return observation_payload(obs)


def _excerpt(payload: dict[str, Any]) -> dict[str, Any]:
    keep = ("a51.job.office", "a51.job.parttime", "a51.service.gym_plan", "a51.durable.washer")
    fb = payload["last_feedback"]
    return {
        "information_condition": payload["information_condition"],
        "dynamic_feedback_channel": payload["dynamic_feedback_channel"],
        "year_index": payload["year_index"],
        "energy_level": payload["energy_level"],
        "resources": payload["resources"],
        "opportunities_time_energy (节选)": {
            o["opportunity_id"]: o["time_energy"]
            for o in payload["opportunities"]
            if o["opportunity_id"] in keep
        },
        "last_feedback.resources[第 1 月]": fb["resources"][0],
        "（省略）": "完整 payload 见同目录的 product_payload_*.json：用户陈述、全部机会、持有物、最低生活声明、余额等",
    }


# ---------------------------------------------------------------------- 手算核对


def _checks(
    bundle: LifeBundle,
    res: dict[str, SettlementResult],
    derived: dict[str, SettlementResult],
    old: SettlementResult,
    payloads: dict[str, dict[str, Any]],
    compare: dict[str, Any] | None,
) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    def months(r: SettlementResult) -> tuple[MonthResources, ...]:
        o = r.outcome
        assert o is not None and o.resources is not None
        return o.resources.months

    def used(m: MonthResources) -> tuple[F, F]:
        assert m.time_used is not None and m.energy_used is not None
        return m.time_used.fraction(), m.energy_used.fraction()

    def problem(r: SettlementResult):
        return r.problems[0] if r.status == "plan_infeasible" and len(r.problems) == 1 else None

    near, far = months(res["u21.a51.near"]), months(res["u21.a51.far"])
    check(
        "住近处每月",
        all(used(m) == (235, 207) for m in near),
        "160 + 10 + 40 + 3 + 10 + 12 = 235 小时；207 负担点",
    )
    check(
        "未安排时间月底作废",
        all(m.time_remaining.fraction() == 130 for m in near)  # type: ignore[union-attr]
        and all(m.time_budget.fraction() == 365 for m in near),
        "每月 235 已安排 + 130 未安排 = 365；每月都是 130，不累积、不回血、不要求安排满",
    )
    check(
        "住远处每月",
        all(used(m) == (F(745, 3), 217) for m in far),
        "通勤 70×20/60 = 70/3 → 248⅓ 小时；负担 207 + 17.5 − 7.5 = 217",
    )
    rent = {
        cid: sorted(
            {
                exact_minor_units(-f.amount.amount, 2)
                for f in res[cid].outcome.cash_flows  # type: ignore[union-attr]
                if f.kind == "expense" and f.opportunity_id and ".housing." in f.opportunity_id
            }
        )
        for cid in ("u21.a51.near", "u21.a51.far")
    }
    check(
        "租金差同时保留",
        rent == {"u21.a51.near": [140000], "u21.a51.far": [100000]},
        f"每月房租（分）{rent}",
    )
    check(
        "便利方案每月",
        all(used(m) == (197, F(936, 5)) for m in months(res["u21.a51.convenience"])),
        "做饭 10（现成餐）+ 打扫 2（保洁）→ 197 小时、187.2 负担点；比自己做少 38 小时",
    )
    p = problem(res["u21.a51.volunteer"])
    check(
        "只有时间超限",
        p is not None
        and (p.code, p.month, p.resource.excess.fraction()) == ("time_over_limit", 6, 10),  # type: ignore[union-attr]
        "第 6 月 235 + 140 = 375 > 365，超 10；负担 221 ≤ 250 不报",
    )
    check(
        "远程不通勤",
        all(
            m.commute_status == "no_onsite_work" and used(m) == (213, F(343, 2))
            for m in months(res["u21.b52.remote"])
        ),
        "160 + 40 + 3 + 10 = 213 小时；144 + 20 + 1.5 + 6 = 171.5",
    )
    check(
        "混合办公按 8 次到岗",
        all(used(m) == (F(671, 3), F(391, 2)) for m in months(res["u21.b52.hybrid"])),
        "通勤 80×8/60 = 32/3 → 223⅔ 小时；160 + 8 + 20 + 1.5 + 6 = 195.5",
    )
    p = problem(res["u22.a51.c0"])
    check(
        "只有精力超限",
        p is not None
        and (p.code, p.month, p.resource.used.fraction(), p.resource.excess.fraction())  # type: ignore[union-attr]
        == ("energy_over_limit", 3, 253, 23),
        "第 3 月 217 + 30×1.2 = 253 > 230，超 23；时间 278⅓ ≤ 365 不报",
    )
    p = problem(res["u23.a51.c0"])
    check(
        "降级后长通勤不可行",
        p is not None
        and (p.code, p.month, p.resource.used.fraction()) == ("time_over_limit", 11, F(431, 3)),  # type: ignore[union-attr]
        "第 11 月退回远处，137 − 5 + 35/3 = 143⅔ > 140",
    )
    u24 = months(res["u24.a51.c0"])
    check(
        "降级后仍可行的对照",
        res["u24.a51.c0"].status == "completed"
        and all(used(m)[0] == 137 for m in u24[:10])
        and all(
            used(m)[0] == F(431, 3) and m.housing_opportunity_id == "a51.housing.far"
            for m in u24[10:]
        ),
        "第 1–10 月 137 小时，第 11–12 月 143⅔ ≤ 150，过完全年",
    )
    u25 = res["u25.a51.c0"]
    m25 = months(u25)
    check(
        "出局当月只查已知下限",
        u25.status == "exited"
        and u25.outcome.exit.month == 4  # type: ignore[union-attr]
        and m25[3].resource_check == "known_lower_bound"
        and used(m25[3]) == (80, 80)
        and m25[3].within_limits is None
        and m25[3].commute_status == "unknown_no_housing"
        and all(m.resource_check == "not_reached" for m in m25[4:])
        and u25.outcome.energy_feasibility == "monthly_quota_checked_until_exit",  # type: ignore[union-attr]
        "第 4 月工资 1,000 入账、对应 80 工时照记；80 ≤ 150 没超，但通勤未知，不说这个月通过了完整检查",
    )
    # ------------------------------------------------------------ F1
    for name, resource in (("exit_time_over", "time"), ("exit_energy_over", "energy")):
        p = problem(derived[name])
        x = p.resource if p is not None else None
        check(
            f"出局月{'时间' if resource == 'time' else '精力'}超限（F1）",
            x is not None
            and (p.code, p.month, x.resource_check)
            == (f"{resource}_over_limit", 1, "known_lower_bound")  # type: ignore[union-attr]
            and (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (60, 80, 20)
            and derived[name].outcome is None
            and derived[name].next_state is None,
            "远程兼职 80 小时 / 80 点，预算或额度 60；存款 0，第 1 月入账 1,000 < 1,380 本会出局——已知劳动超了，"
            "判计划不可行，不留工资账本",
        )
    p = problem(derived["exit_cash_control"])
    check(
        "资金对照（F1）",
        p is not None
        and (p.code, p.month, p.resource.resource_check) == ("time_over_limit", 1, "complete"),  # type: ignore[union-attr]
        "同样 60 小时预算、存款 10,000：第 1 月完整检查 80 + 30 + 12 + 10 = 132 > 60，同样不可行",
    )
    r = derived["exit_remote_ok"]
    m1 = months(r)[0] if r.outcome is not None else None
    check(
        "远程出局月通勤确定为零",
        r.status == "exited"
        and m1 is not None
        and (m1.resource_check, m1.commute_status) == ("known_lower_bound", "no_onsite_work")
        and used(m1) == (80, 80)
        and all(m.resource_check == "not_reached" for m in months(r)[1:]),
        "默认预算：已知 80 ≤ 150，照常出局；全远程没有到岗需求，通勤为零而不是未知；之后未发生",
    )
    # ------------------------------------------------------------ F2
    r = derived["two_jobs"]
    ok = r.status == "completed"
    if ok:
        ms = months(r)
        gross = [exact_minor_units(m.income_gross.amount, 2) for m in r.outcome.months]  # type: ignore[union-attr]
        tax = [exact_minor_units(m.income_tax.amount, 2) for m in r.outcome.months]  # type: ignore[union-attr]
        commute = {
            c.opportunity_id: c.hours.fraction()
            for c in ms[0].contributions
            if c.category == "commute"
        }
        ok = (
            all(used(m) == (235, 207) for m in ms)
            and commute == {"a51.job.parttime": 5, SECOND: 5}
            and ms[0].onsite_visits == 20
            and gross == [200000] * 12
            and tax == [0] * 6 + [40000] * 6
        )
    check(
        "两份临时工（F2）",
        ok,
        "工作 80 + 80；通勤 30×10/60 + 30×10/60 = 5 + 5；+ 做饭 40、洗衣 3、打扫 10、健身 12 = 235 小时、207 点；"
        "工资每月 2,000，全年 24,000，第 7 月起每月合成税 400",
    )
    p = problem(derived["two_jobs_energy_over"])
    check(
        "两份工作精力超限（F2）",
        p is not None
        and (p.code, p.month, p.resource.used.fraction(), p.resource.excess.fraction())  # type: ignore[union-attr]
        == ("energy_over_limit", 1, F(1163, 4), F(163, 4)),
        "文员 + 兼职：时间 235 + 80 + 5 = 320 ≤ 365；负担 207 + 80 + 3.75 = 290.75 > 250，超 40.75——计划不可行，不是输入错误",
    )
    # ------------------------------------------------------------ 可见与兼容
    std, hid = payloads["standard"], payloads["research_hidden"]
    check(
        "产品观察有规划信息",
        std["resources"]["monthly_energy_quota"]["exact"] == "250"
        and std["resources"]["energy_unit"] == "burden_point"
        and std["resources"]["monthly_time_budget"]["exact"] == "365"
        and "月底作废" in std["resources"]["time_rule"]
        and std["last_feedback"]["resources"][0]["energy_used"]["exact"] == "207"
        and hid["resources"]["monthly_energy_quota"] is None
        and hid["last_feedback"]["resources"][0]["energy_used"] is None,
        "标准产品：每月 365 小时、250 负担点、规则说明、上年逐月时间与负担；研究对照显式隐藏后额度与负担汇总为空",
    )
    check(
        "旧协议照旧",
        old.status == "completed"
        and old.outcome is not None
        and old.outcome.energy_feasibility == "not_implemented"
        and old.outcome.resources is None
        and old.outcome.time_used_hours == 2306.0,
        "E1-2 样例 u01.a11.c0：年度时间 2,306 小时、精力只记录、没有月度资源账",
    )
    if compare is not None:
        check(
            "与 r1 历史结果的数值比较",
            not compare["unexpected_value_changes"]
            and len(compare["same_values"]) == len(res) - 1
            and bool(compare["expected_rule_changes"]),
            f"{len(compare['same_values'])} 个分身的状态、现金、诊断与逐月资源数值与 r1 相同；u25 只在出局当月按新规则"
            "改变（检查类型、已知用量）",
        )
    check(
        "不可行试算不提交",
        all(
            x.outcome is None and x.next_state is None
            for x in (
                res["u21.a51.volunteer"],
                res["u22.a51.c0"],
                res["u23.a51.c0"],
                derived["exit_time_over"],
                derived["exit_energy_over"],
                derived["two_jobs_energy_over"],
            )
        ),
        "六份不可行计划都没有账本，也没有下一年状态",
    )
    again = {cid: _settle(bundle, cid).model_dump(mode="json") for cid in res}
    check(
        "确定性重放",
        all(again[cid] == r.model_dump(mode="json") for cid, r in res.items()),
        "同一输入再结算一遍，全部结果逐字相同",
    )
    return checks


def _summary(
    bundle: LifeBundle,
    res: dict[str, SettlementResult],
    derived: dict[str, SettlementResult],
    changes: dict[str, list[str]],
) -> list[str]:
    rules = bundle.protocol.resource_rules
    assert rules is not None
    lines = [
        "# E1-4A 月度时间与精力演示（执行修订 r2）",
        "",
        "**合成设定**：负担系数、净可分配小时与额度都是手工设定，不是现实生活的可行性判断，也不排序城市。",
        f"协议 `{bundle.protocol.protocol_id}`，生活规则 `{bundle.protocol.life_rules_version}`，"
        f"资源规则 `{rules.version}`（执行修订 `{RESOURCE_RULES_REVISION}`），精力尺度 `{rules.energy_scale_version}`。",
        "",
        f"- 预留：{rules.reserved_time}",
        f"- 通勤：{rules.commute_reference}；每小时通勤负担 {rules.commute_burden_per_hour:g}；"
        "每个岗位每次到岗算一趟完整往返",
        "- 家务基准："
        + "；".join(
            f"{t.task} 每月 {t.baseline_hours_per_month:g} 小时、每小时负担 {t.burden_per_hour:g}"
            for t in rules.household_tasks
        ),
        "- 表里“未安排 h”是当月没安排的时间，月底作废：不结转、不回血、不加满足感；“只查已知下限”的月份，"
        "用量写“至少”，不是完整检查。",
        "",
        "## 结果一览",
        "",
        "| 情景 | 分身 | 结果 | 说明 |",
        "|---|---|---|---|",
    ]

    def note(r: SettlementResult) -> str:
        if r.status == "plan_infeasible":
            p = r.problems[0]
            x = p.resource
            scope = (
                "（已知下限）" if x is not None and x.resource_check == "known_lower_bound" else ""
            )
            return f"第 {p.month} 月{'时间' if p.code.startswith('time') else '精力'}超限{scope}"
        if r.status == "exited":
            return f"第 {r.outcome.exit.month} 月付不起最低生活出局；出局当月只查已知下限"  # type: ignore[union-attr]
        return "每月时间与精力都在额度内（完整检查）"

    for cid, r in res.items():
        lines.append(f"| 原样例 | {cid} | {r.status} | {note(r)} |")
    for name, r in derived.items():
        lines.append(f"| 派生 {name} | {_derive_clone(name)} | {r.status} | {note(r)} |")
    for cid, r in res.items():
        lines += ["", f"## 原样例 {cid}（{r.status}）", ""]
        if r.outcome is not None and r.outcome.resources is not None:
            lines += _month_table(r.outcome.resources.months)
            cash = [f"{m.cash_end.amount:,.2f}" for m in r.outcome.months]
            lines += ["", f"月末现金：{'、'.join(cash)}"]
        lines += _problem_lines(r)
    for name, r in derived.items():
        lines += ["", f"## 派生情景 {name}（{r.status}）", "", "改动："]
        lines += [f"- {c}" for c in changes[name]]
        lines += [""]
        if r.outcome is not None and r.outcome.resources is not None:
            lines += _month_table(r.outcome.resources.months)
            o = r.outcome
            lines += [
                "",
                "税前工资：" + "、".join(f"{m.income_gross.amount:,.2f}" for m in o.months),
                "合成税：" + "、".join(f"{m.income_tax.amount:,.2f}" for m in o.months),
                "月末现金：" + "、".join(f"{m.cash_end.amount:,.2f}" for m in o.months),
            ]
        lines += _problem_lines(r)
    return lines


def _derive_clone(name: str) -> str:
    return "u25.a51.c0" if name.startswith("exit") else "u21.a51.near"


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-4A 月度时间与精力的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    ap.add_argument("--life", type=Path, default=LIFE)
    ap.add_argument(
        "--compare-with",
        type=Path,
        default=R1_RESULTS,
        help="执行修订 r1 的历史 results.json（只读）；不存在就跳过比较",
    )
    args = ap.parse_args()

    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, report = load_life_bundle(args.life, require_settlement_ready=True)
        old_bundle, _ = load_life_bundle(OLD_LIFE, require_settlement_ready=True)
    except LifeContractError as exc:
        print(exc)
        return 1
    if bundle.protocol.resource_rules is None:
        print(f"{args.life} 不是按月核算时间与精力的协议")
        return 1

    clones = [p.clone_id for p in bundle.plans if p.year_index == 1]
    res = {cid: _settle(bundle, cid) for cid in clones}
    old = _settle(old_bundle, "u01.a11.c0")

    raw0 = yaml.safe_load(args.life.read_text(encoding="utf-8"))
    derived: dict[str, SettlementResult] = {}
    derived_raw: dict[str, dict] = {}
    changes: dict[str, list[str]] = {}
    for name in DERIVED:
        raw, clone, change = _derive(raw0, name)
        b = parse_bundle(raw)
        errors = cross_validate(b).errors
        if errors:
            print(f"派生情景 {name} 的配置不合法：{errors}")
            return 1
        derived[name] = _settle(b, clone)
        derived_raw[name], changes[name] = raw, change

    permission_version = bundle.protocol.info_permission_version
    hidden = ObservationPermission(
        condition="product_stated_profile",
        show_stated_dimension_points=True,
        show_soft_preferences=True,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=False,
        show_stated_material_points=True,
        permission_version=permission_version,
    )
    payloads = {
        "standard": _payload(bundle, standard_product_permission(permission_version)),
        "research_hidden": _payload(bundle, hidden),
    }

    new_json = [_result_json(r, cid) for cid, r in res.items()]
    compare = None
    if args.compare_with.exists():
        compare = _compare_with_r1(
            json.loads(args.compare_with.read_text(encoding="utf-8")), new_json
        )
    checks = _checks(bundle, res, derived, old, payloads, compare)
    check_lines = [
        "# 手算核对",
        "",
        "数值按精确分数比较，不加容差；金额按整数分比较。",
        "",
        *(f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks),
    ]

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg_dir = args.out / "configs"
    (cfg_dir / "derived").mkdir(parents=True)
    shutil.copyfile(args.life, cfg_dir / args.life.name)
    shutil.copyfile(OLD_LIFE, cfg_dir / OLD_LIFE.name)
    for name, raw in derived_raw.items():
        header = "".join(f"#   - {c}\n" for c in changes[name])
        (cfg_dir / "derived" / f"{name}.yaml").write_text(
            f"# 派生情景 {name}：由 {args.life.as_posix()} 原样例派生（原文件不改），改动：\n{header}"
            "# 注释在派生时丢失，含义见原样例。\n"
            + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def dump(name: str, data: Any) -> None:
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    dump("environment.json", _environment(args.life))
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": f"uv run python scripts/demo_life_e1_4a.py --out <新目录> --life {args.life.as_posix()}",
        },
    )
    dump(
        "protocol.json",
        {
            "protocol": bundle.protocol.model_dump(mode="json"),
            "resource_rules_revision": RESOURCE_RULES_REVISION,
            "settlement_convention": SETTLEMENT_CONVENTION,
            "formal_scoring_ready": bundle.protocol.scoring_ready,
            "old_protocol_for_comparison": old_bundle.protocol.model_dump(mode="json"),
        },
    )
    dump(
        "inputs.json",
        {
            "life_file": str(args.life),
            "life_sha256": _sha256(args.life),
            "old_life_file": str(OLD_LIFE),
            "old_life_sha256": _sha256(OLD_LIFE),
            "compare_with": str(args.compare_with) if compare is not None else None,
            "compare_with_sha256": _sha256(args.compare_with) if compare is not None else None,
            "derived": {name: changes[name] for name in DERIVED},
            "life_report": report.model_dump(mode="json"),
        },
    )
    dump("results.json", new_json)
    dump("derived_results.json", [_result_json(r, name) for name, r in derived.items()])
    dump("old_protocol_result.json", _result_json(old, "u01.a11.c0"))
    dump("product_payload_standard.json", payloads["standard"])
    dump("product_payload_research_hidden.json", payloads["research_hidden"])
    dump(
        "product_payload_excerpt.json",
        {k: _excerpt(v) for k, v in payloads.items()},
    )
    if compare is not None:
        dump("diff_vs_r1.json", compare)
    (args.out / "monthly_resources.md").write_text(
        "\n".join(_summary(bundle, res, derived, changes)) + "\n", encoding="utf-8"
    )
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-4A 月度时间与精力（运行证据，执行修订 r2）",
                "",
                "零大模型调用的离线演示（`DECISIONS/0020`）。全部数值是合成设定下的结果，**不是现实生活的",
                "可行性判断，也不是城市排行**。",
                "",
                "- `command.json`：实际命令（argv）、解释器与工作目录",
                "- `environment.json`：git HEAD、是否有未提交改动、执行修订、关键源文件、测试与配置的 SHA-256",
                "- `configs/`：E1-4A 生活输入副本、作对照的 E1-2 旧样例副本；`configs/derived/`：派生情景的完整配置",
                "- `protocol.json`：新协议（含资源规则与执行修订）、结算约定与旧协议",
                "- `inputs.json`：输入文件、指纹、派生改动清单与加载检查报告",
                "- `results.json` / `derived_results.json`：原样例与派生情景的结算状态、问题与资源诊断、现金与逐月资源账",
                "- `old_protocol_result.json`：旧协议对照（年度时间、精力只记录）",
                "- `product_payload_standard.json` / `product_payload_research_hidden.json`：两种许可下的实际 payload；"
                "`product_payload_excerpt.json`：精简摘录",
                "- `diff_vs_r1.json`：与执行修订 r1 历史结果的比较（数值保持 / 按新规则改变 / 结构变化）",
                "- `monthly_resources.md`：逐月资源表与不可行诊断（人读）",
                "- `checks.md` / `checks.json`：手算核对",
                "",
                "复现：`uv run python scripts/demo_life_e1_4a.py --out <新目录>`。代码未提交时，以",
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
