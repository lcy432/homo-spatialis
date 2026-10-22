"""菜单：按城市、年份、板块、环境重复生成；注册表稳定不变，菜单只引用它。

三种政策：

* ``full_catalog``（食品、普通商品、常规服务、城市背景）：菜单 = 本资料包成功映射的全部合格机会，不随机删减。为了不在每一年每一份
  菜单里重复抄几万个编号，菜单只记“全部合格”与合格集合的内容身份；查询时分页读取。完整可访问 ≠ 单轮请求能容纳全部记录，两件事
  分开报告。
* ``calendar_open``（培训、活动）：按事实日历开放。日历已知的列出；日历未知的（例如 IPEDS 不记开课日期）照样列出并在机会里标明
  “日历未知”，不藏起来。第 1 年对应资料观测到的那一轮日历；第 2、3 年假设同样的日历每年重复（登记为探索性设定），没有观测的
  未来日历不编造。
* ``annual_exposure_sample``（住房、新岗位）：抽取本年接触到的机会。只模拟接触机会，不抽成交或录用。

**抽样算法 ``weighted_keyed_sample@1``**（Efraimidis–Spirakis 加权无放回抽样）：每个合格机会的键 = u^(1/w)，u 由
SHA-256(种子, 机会编号) 映射到 (0, 1)，w 是目标权重；每层取键最大的 k 个。种子 = SHA-256(资料身份, 都会区, 年份, 板块, 环境重复,
算法版本, 配置种子)——**不含分身编号**：同一环境重复下的分身共享同一份菜单；恢复、重新查询、修改计划都得到同一份，不重抽。
抽样权重不是入选概率：无放回抽样里权重大的机会入选概率小于“权重 × k”，报告里用模拟估计实际入选概率。

住房：每个卧室层从 B 候选里抽；某一层没有 B 候选时才用这一层的 A 网格候选补（A 补共同覆盖）。权重为 0 或未知的机会不合格。
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable

from common.intake_contracts import (
    AssumptionSpec,
    Menu,
    MenuPolicy,
    MethodSpec,
    Opportunity,
    Sector,
    canonical_json,
    content_identity,
)

ALGORITHM = MethodSpec(
    method_id="weighted_keyed_sample",
    version="1",
    family="menu",
    summary_zh="Efraimidis–Spirakis 加权无放回抽样：键 = u^(1/w)，u = SHA-256(种子, 机会编号) → (0,1)；每层取键最大的 k 个；种子不含分身编号。",
    known_biases_zh=("权重不是入选概率：无放回抽样里权重大的机会入选概率被压低",),
)
RECURRENCE = AssumptionSpec(
    assumption_id="calendar_recurs_annually",
    kind="exploratory_proxy",
    statement_zh="第 2、3 年的活动与培训日历假设与资料观测到的那一轮相同（每年重复）。",
    applies_to=("menus.calendar_open",),
    why_not_measured_zh="未来年份的开课与活动日历还没有发布；不编造新的日历。",
)


def menu_seed(
    build_id: str, cbsa: str, year: int, sector: str, env_repeat: int, global_seed: str
) -> str:
    """抽样种子：资料身份、城市、年份、板块、环境重复、算法版本、配置种子。**不含分身编号**。"""
    return content_identity(
        {
            "build": build_id,
            "cbsa": cbsa,
            "year": year,
            "sector": sector,
            "env_repeat": env_repeat,
            "algorithm": ALGORITHM.ref,
            "seed": global_seed,
        }
    )


def _u(seed: str, opportunity_id: str) -> float:
    h = hashlib.sha256(f"{seed}|{opportunity_id}".encode()).digest()
    x = int.from_bytes(h[:8], "big")
    return (x + 0.5) / 2**64  # 严格落在 (0, 1)


def keyed_sample(items: list[tuple[str, float]], k: int, seed: str) -> list[str]:
    """加权无放回：返回键最大的 k 个编号（按键从大到小）。权重 ≤ 0 的不参加。"""
    keyed = [(math.log(_u(seed, oid)) / w, oid) for oid, w in items if w > 0]
    keyed.sort(reverse=True)
    return [oid for _, oid in keyed[:k]]


def eligible_identity(ids: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(sorted(ids)).encode("utf-8")).hexdigest()


def _weight(o: Opportunity) -> float:
    return float(o.target_weight) if o.target_weight is not None else 0.0


def build_menu(
    *,
    build_id: str,
    cbsa: str,
    year: int,
    sector: Sector,
    env_repeat: int,
    policy: MenuPolicy,
    opportunities: list[Opportunity],
    global_seed: str,
) -> Menu:
    seed = menu_seed(build_id, cbsa, year, sector, env_repeat, global_seed)
    menu_id = f"menu.{cbsa}.y{year}.{sector}.r{env_repeat}"
    if policy.kind == "full_catalog":
        ids = [o.opportunity_id for o in opportunities if o.supply_status != "not_offered"]
        return Menu(
            menu_id=menu_id,
            build_id=build_id,
            cbsa=cbsa,
            year=year,
            sector=sector,
            env_repeat=env_repeat,
            policy="full_catalog",
            algorithm=ALGORITHM.ref,
            seed=seed,
            n_eligible=len(ids),
            eligible_identity=eligible_identity(ids),
            selection="all_eligible",
            selected=(),
            inclusion_note_zh="完整目录：本资料包成功映射的全部合格机会（确定不可得的除外），分页查询；不随机删减。",
        )
    if policy.kind == "calendar_open":
        ids = sorted(o.opportunity_id for o in opportunities if o.supply_status != "not_offered")
        unknown = sum(1 for o in opportunities if o.calendar is not None and not o.calendar.known)
        note = f"按事实日历开放：{len(ids)} 项，其中日历未知 {unknown} 项（照样列出并标明）。"
        if year > 1:
            note += f"第 {year} 年沿用观测到的日历（假设 {RECURRENCE.assumption_id}）。"
        return Menu(
            menu_id=menu_id,
            build_id=build_id,
            cbsa=cbsa,
            year=year,
            sector=sector,
            env_repeat=env_repeat,
            policy="calendar_open",
            algorithm=ALGORITHM.ref,
            seed=seed,
            n_eligible=len(ids),
            eligible_identity=eligible_identity(ids),
            selection="listed",
            selected=tuple(ids),
            inclusion_note_zh=note[:400],
        )
    # 抽取本年接触机会
    eligible = [o for o in opportunities if _weight(o) > 0 and o.supply_status == "offered"]
    selected: list[str] = []
    if policy.per_stratum:
        primary = [o for o in eligible if o.origin != "mapping_a"]
        fallback = [o for o in eligible if o.origin == "mapping_a"]
        strata: dict[str, list[Opportunity]] = defaultdict(list)
        for o in primary:
            strata[o.stratum or "all"].append(o)
        fb: dict[str, list[Opportunity]] = defaultdict(list)
        for o in fallback:
            fb[(o.stratum or "a:all").removeprefix("a:")].append(o)
        for stratum in sorted(set(strata) | set(fb)):
            pool = strata.get(stratum) or fb.get(stratum) or []
            selected += keyed_sample(
                [(o.opportunity_id, _weight(o)) for o in pool],
                policy.per_stratum,
                f"{seed}|{stratum}",
            )
        rule = f"每层（{policy.strata_key}）{policy.per_stratum} 个"
    else:
        selected = keyed_sample(
            [(o.opportunity_id, _weight(o)) for o in eligible], policy.size or 0, seed
        )
        rule = f"共 {policy.size} 个（不分层抽，按层报告构成）"
    return Menu(
        menu_id=menu_id,
        build_id=build_id,
        cbsa=cbsa,
        year=year,
        sector=sector,
        env_repeat=env_repeat,
        policy="annual_exposure_sample",
        algorithm=ALGORITHM.ref,
        seed=seed,
        n_eligible=len(eligible),
        eligible_identity=eligible_identity(o.opportunity_id for o in eligible),
        selection="listed",
        selected=tuple(selected),
        inclusion_note_zh=(
            f"抽取本年接触机会：{rule}，按目标权重加权无放回；权重不是入选概率（见菜单诊断）。只模拟接触，不抽成交或录用。"
        )[:400],
    )
