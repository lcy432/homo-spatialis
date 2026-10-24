"""菜单：按城市、年份、板块、环境重复生成；注册表稳定不变，菜单只引用它。

三种政策：

* ``full_catalog``（住房的目标格、食品、普通商品、常规服务、城市背景）：菜单 = 本资料包成功映射的全部合格机会，不随机删减。为了不在每一年每一份
  菜单里重复抄几万个编号，菜单只记“全部合格”与合格集合的内容身份；查询时分页读取。完整可访问 ≠ 单轮请求能容纳全部记录，两件事
  分开报告。
* ``calendar_open``（培训、活动）：目标是按事实日历开放，但**模拟年份与真实日期的对应还没定义**，所以现在不按日历筛选：菜单
  列出全部观测到的项目及其原始日历，标明 ``calendar_filter = pending_mapping``，并分开数日历已知与未知（例如 IPEDS 不记开课
  日期）。不声称已经按日历筛过；没有观测的未来日历也不编造。
* ``annual_exposure_sample``（新岗位）：抽取本年接触到的机会。只模拟接触机会，不抽成交或录用。资料不足（必需事实缺，
  例如回退之后仍没有租金）的候选不进抽样。

**抽样算法 ``weighted_keyed_sample@1``**（Efraimidis–Spirakis 加权无放回抽样）：每个合格机会的键 = u^(1/w)，u 由
SHA-256(种子, 机会编号) 映射到 (0, 1)，w 是目标权重；每层取键最大的 k 个。种子 = SHA-256(资料身份, 都会区, 年份, 板块, 环境重复,
算法版本, 配置种子)——**不含分身编号**：同一环境重复下的分身共享同一份菜单；恢复、重新查询、修改计划都得到同一份，不重抽。
抽样权重不是入选概率：无放回抽样里权重大的机会入选概率小于“权重 × k”，报告里用模拟估计实际入选概率。

住房（E3B 映射修正）：候选池是完整加权经验支持表（``housing_rent_support@1``，每个 PUMA × 卧室目标格一条，这里全部列出、不抽样）；
每年的接触菜单在 E3B 资料包里从展开后的完整可执行池抽（``product.realdata.life_pack``）。权重为 0 或未知、供给不确定、资料不足的
机会不合格。合格规则与检查器（``ledger.intake_data``）共用同一条定义：改了规则要升 ``MENU_RULES_VERSION``。
"""

from __future__ import annotations

import hashlib
import heapq
import math
from collections import defaultdict
from collections.abc import Iterable

from common.intake_contracts import (
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
#: 合格规则的版本（进目录阶段的身份）：2 = 抽样排除资料不足的候选；全国性的机会只进同一国家的都会区；日历菜单写明未筛选。
MENU_RULES_VERSION = "2"


def sample_eligible(o: Opportunity) -> bool:
    """抽取本年接触机会的合格规则：在供应、目标权重为正、资料不是“不足”。"""
    return o.supply_status == "offered" and _weight(o) > 0 and o.data_status != "insufficient"


def listing_eligible(o: Opportunity) -> bool:
    """完整目录与日历菜单的合格规则：除了确定不供应的，全部列出（资料不足的也列出并标明，不藏起来）。"""
    return o.supply_status != "not_offered"


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
    keyed = ((math.log(_u(seed, oid)) / w, oid) for oid, w in items if w > 0)
    return [oid for _, oid in heapq.nlargest(k, keyed)]


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
        ids = [o.opportunity_id for o in opportunities if listing_eligible(o)]
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
        listed = [o for o in opportunities if listing_eligible(o)]
        ids = sorted(o.opportunity_id for o in listed)
        known = sum(1 for o in listed if o.calendar is not None and o.calendar.known)
        note = (
            f"日历待适配：模拟年份与真实日期的对应还没定义，本菜单没有按日历筛选，只列出观测到的 {len(ids)} 项及其原始日历"
            f"（日历已知 {known} 项、未知 {len(ids) - known} 项）。"
        )
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
            calendar_filter="pending_mapping",
            n_calendar_known=known,
            n_calendar_unknown=len(ids) - known,
        )
    # 抽取本年接触机会
    eligible = [o for o in opportunities if sample_eligible(o)]
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
            f"抽取本年接触机会：{rule}，按目标权重加权无放回；资料不足、供给不确定、权重为 0 的不进抽样；权重不是入选概率（见菜单诊断）。只模拟接触，不抽成交或录用。"
        )[:400],
    )
