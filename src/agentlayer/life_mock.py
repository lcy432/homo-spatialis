"""可控的 mock 规划器（`DECISIONS/0030`；产品版规则 ``e3b-product-mock-rules-v2``，DECISIONS/0033、0034、0042）。确定性、规则简单，
只读收到的请求 JSON。

**它不是模型能力的证据**：只用来检查接口、状态传递、错误处理与信息隔离。它读的是 ``PlannerCall.payload``——与渲染进提示文字的是
同一份投影，没有任何内部对象。读观察里的六维精确点数（``six_dimension_points``）与物质分配（``material_allocation``）：

* M1 住房：本城合格住房（``minimum_living`` 的合格名单、明确可得、按月计价）里，能满足用户硬要求（往返分钟上限）的最便宜的
  一项——前提是按"岗位月收入 − 住房 − 最便宜食品"推算的年末现金不少于一个月最低生活费；否则选最便宜的合格住房，并在理由里
  写明硬要求今年满足不了。推算不含税。
* M1b 用车（DECISIONS/0042，独立审核 F5）：不用车的合格住房都到不了所选岗位、或都满足不了往返分钟硬要求时，看本城公开的用车
  机会（按月付费、提供通勤方式要求的功能）：开车能到岗（并满足硬要求）的最便宜住房，按"岗位月收入 − 住房 − 最便宜食品 − 通勤费
  （含油费）− 用车月费"推算的年末现金不少于一个月最低生活费，就买用车权（1–12 月）、住那一处；买不起或开车也到不了就不买，
  照 M1 选，并在理由里写明。只读请求里的通勤表、报价与功能，不读评分参数；不用车就有合格住房时不买车。
* M2 食品：最便宜的合格食品。
* M3 岗位：用户的物质或发展点数大于 0（点数不可见时照做）才考虑换岗：年初"现在能选"的岗位里工资最高的一份，比现职高才换；
  否则保留年初的当前岗位。
* M4 课程：有在学而没学完的课程就接着学；否则（同样要求物质或发展点数大于 0）如果本城有岗位要求某项我还没有的能力、有课程授予
  它、那份岗位比选中的岗位工资高，并且 1 月付完住房与食品后的现金付得起学费、付学费后推算的年末现金不少于一个月最低生活费，
  就从 1 月起报名。
* M5 便利：物质分配里 daily_convenience 大于 0、手上没有在服务期内能替代家务的物品时，在本城可得、按公开事实能替代家务的设备
  （一次性购买）与人工服务（按月购买）里，按"每月少做 1 小时家务要花多少钱"从低到高挑第一个付得起的：设备的每月花费 = 价格 ÷
  （寿命年数 × 12），服务的每月花费 = 月价；每月少做的小时 = Σ（家务基准 − 残余）。付得起：设备在 1 月付完住房、食品与学费后的
  现金付得起，服务 12 个月的花费付完后，推算的年末现金都不少于三个月最低生活费。只读请求里的公开报价、寿命与资源事实，不读评分
  参数，不是评分最优化器。
* M6 应急：**固定测试政策**——小病、大病都写 treat_if_possible；暂停顺序是本年计划里有参与时间的主动活动（体验 → 服务 → 课程）；
  生病的月份取消本年计划里的一次性耐用品购买。这是 mock 的测试设定，**不是**从用户健康回答推断出的治疗态度；健康回答只出现在
  需求摘要里，不改变任何行动。
* M7 兜底：住房与食品都声明 ``minimum_fallback``。M8 目标储蓄：不设。

修订（第 2 份起）：读修订块。上一份输出能解析就在它的基础上按诊断改——资源超限：在诊断列出的占用最多的项目里，按"额外的第二份
工作 → 有参与时间的服务 → 体验 → 课程 → 耐用品"的顺序删掉一项；资格不足：删掉那一项，岗位缺了就补回现职；要治疗而本城资料
未知：资料未知的那种病改成不治；其他问题：按观察重新规划。上一份不能解析就按观察重新规划。

受控行为（``MockScript``，进入运行身份）只用于演示与测试：第一份草稿多做一份工作、选一个还不合格的岗位、返回不是 JSON 的文字、
抛出意外异常、只换需求摘要与理由的措辞。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from fractions import Fraction
from typing import Any

from agentlayer.life_planning import PlannerCall
from common.life_planning_contracts import MockScript, PlannerIdentity, PlannerSpec

PRODUCT_MOCK_RULES_VERSION = "e3b-product-mock-rules-v2"
#: 固定测试政策的名字（写进身份说明与需求摘要，便于审计：它不是从健康回答推断的）。
PRODUCT_MOCK_EMERGENCY_POLICY = "fixed_treat_if_possible_test_policy_v1"
PRODUCT_MOCK_IDENTITY = PlannerIdentity(
    kind="mock",
    planner_id="mock.rules.e3a-product",
    version="e3b-product-mock-v2",
    rules_version=PRODUCT_MOCK_RULES_VERSION,
    note=(
        "可控的确定性模拟代理（六维产品画像版规则）：只检查接线、状态传递与信息隔离，不代表任何真实模型的能力；读六维精确点数与物质"
        "分配，M1b 不用车到不了岗位或满足不了硬要求时按公开的通勤表与报价考虑用车，M5 按公开报价、寿命与残余时间挑设备或按月服务；"
        "应急是固定测试政策（小病、大病都 treat_if_possible），不从健康回答推断"
    ),
)
#: 修订时删项目的先后（数小的先删）：额外的第二份工作 0、有参与时间的服务 1、体验 2、课程 3、耐用品 4。
_PRIORITY: dict[str, int] = {"experience": 2, "training": 3, "durable": 4}
MALFORMED_TEXT = "（受控的格式错误：这不是 JSON）今年我想多赚点钱，具体怎么安排还没想好。"


class MockPlannerError(RuntimeError):
    """受控的意外异常（``raise_error`` 行为）：用来演示程序错误怎样暂停受影响的批次。"""


def _price(o: dict[str, Any]) -> float | None:
    p = o.get("price")
    return None if p is None else float(p["amount"]["amount"])


def _monthly(o: dict[str, Any]) -> float | None:
    p = o.get("price")
    if p is None or p.get("billing") != "per_month":
        return None
    return float(p["amount"]["amount"])


def _pay(o: dict[str, Any]) -> float:
    c = o.get("compensation")
    if c is None:
        return 0.0
    amount = float(c["amount"]["amount"])
    return {"per_year": amount / 12, "per_month": amount}.get(c.get("billing"), 0.0)


def _available(o: dict[str, Any]) -> bool:
    return o.get("availability") == "available"


def _qualified(o: dict[str, Any]) -> bool:
    q = o.get("qualification")
    return q is None or q.get("status") == "available_now"


def _period(oid: str, start: int, end: int) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def _once(oid: str, month: int) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "month": month}


def _exact_points(raw: object) -> dict[str, Fraction]:
    """观察里精确分数写法的点数（"30"、"50/3"）。点数不可见时为空。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k): Fraction(str(v)) for k, v in raw.items()}


def _points_text(q: Fraction) -> str:
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


class _View:
    """请求 JSON 的只读视图（只读 payload，不碰任何内部对象）。点数与物质分配读精确分数。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.p = payload
        self.obs = payload["observation"]
        self.opps = {o["opportunity_id"]: o for o in self.obs["opportunities"]}
        profile = self.obs.get("profile") or {}
        six = profile.get("six_dimension_points")
        self.exact = _exact_points(six)
        #: 点数不可见时为 None（照常考虑换岗）
        self.points = None if six is None else {k: float(v) for k, v in self.exact.items()}
        self.material_points = {
            k: float(v) for k, v in _exact_points(profile.get("material_allocation")).items()
        }
        self.health_answers = list(profile.get("health_answers") or [])
        self.hard = profile.get("hard_requirements") or []
        self.cash = float(self.obs["cash"]["amount"])

    def of(self, category: str) -> list[dict[str, Any]]:
        return [o for o in self.obs["opportunities"] if o["category"] == category]

    def qualifying(self, need: str) -> list[dict[str, Any]]:
        spec = next((m for m in self.obs["minimum_living"] if m["need"] == need), None)
        ids = set(spec["qualifying_opportunity_ids"]) if spec else set()
        out = [
            self.opps[i]
            for i in sorted(ids)
            if i in self.opps and _available(self.opps[i]) and _monthly(self.opps[i]) is not None
        ]
        return sorted(out, key=lambda o: (_monthly(o), o["opportunity_id"]))

    def min_living(self) -> float:
        total = 0.0
        for need in ("housing", "food"):
            spec = next(m for m in self.obs["minimum_living"] if m["need"] == need)
            base = self.opps[spec["baseline_opportunity_id"]]
            total += _monthly(base) or 0.0
        return total

    def current_jobs(self) -> list[str]:
        emp = self.obs.get("employment") or {}
        return [
            p["opportunity_id"] for p in emp.get("current", []) if p["opportunity_id"] in self.opps
        ]

    def career_minded(self) -> bool:
        if self.points is None:  # 点数不可见：不靠点数分辨，照常考虑
            return True
        return self.points.get("material", 0) + self.points.get("opportunity", 0) > 0

    def treat(self) -> str:
        # M6：固定测试政策。不读健康回答，也不因为没有 healthcare 点数就默认不治疗
        return "treat_if_possible"

    def max_commute(self) -> float | None:
        limits = [
            float(h["requirement"]["max_commute_roundtrip_minutes"])
            for h in self.hard
            if h.get("requirement", {}).get("category") == "housing"
            and "max_commute_roundtrip_minutes" in h.get("requirement", {})
        ]
        return min(limits) if limits else None

    def car_service(self) -> tuple[dict[str, Any], str] | None:
        """本城公开的用车机会：可得、按月付费、提供通勤表里某种方式要求的功能（例如 car_access）。没有就是 None。"""
        rel = self.obs.get("commute")
        if rel is None:
            return None
        needed = {m["requires_function"] for m in rel["modes"] if m.get("requires_function")}
        for o in sorted(self.obs["opportunities"], key=lambda x: x["opportunity_id"]):
            fn = next((f for f in o.get("functions") or () if f in needed), None)
            if fn is not None and _available(o) and _monthly(o) is not None:
                return o, fn
        return None

    def commute_for(
        self, house: dict[str, Any], job_ids: list[str], functions: frozenset[str] = frozenset()
    ) -> tuple[float, float] | None:
        """mock 的通勤推算（DECISIONS/0042），只读公开的通勤表：每个到岗岗位取最快的可用方式——没有条件的方式，加上 ``functions``
        里有的功能所允许的方式（例如买了用车权才能开车）；任一岗位走不通就是 None。返回（最长往返分钟，每月通勤费，按封顶）。"""
        onsite = []
        for j in job_ids:
            o = self.opps.get(j, {})
            days = ((o.get("time_energy") or {}).get("job") or {}).get("onsite_days_per_month", 0)
            if days:
                onsite.append((o, days))
        if not onsite:
            return 0.0, 0.0
        rel = self.obs.get("commute")
        if rel is None:
            return None
        conditional = {
            m["mode"]
            for m in rel["modes"]
            if m.get("requires_function") and m["requires_function"] not in functions
        }
        caps = {m["mode"]: m.get("monthly_cap") for m in rel["modes"]}
        longest, by_mode = 0.0, {}
        for o, days in onsite:
            legs = [
                g
                for g in rel["legs"]
                if g["home_area"] == house.get("area")
                and g["work_area"] == o.get("area")
                and g["mode"] not in conditional
            ]
            if not legs:
                return None
            g = min(legs, key=lambda x: (x["roundtrip_minutes"], x["cost_per_roundtrip"]["amount"]))
            longest = max(longest, float(g["roundtrip_minutes"]))
            by_mode[g["mode"]] = by_mode.get(g["mode"], 0.0) + days * float(
                g["cost_per_roundtrip"]["amount"]
            )
        cost = sum(
            min(total, float(caps[m]["amount"])) if caps.get(m) else total
            for m, total in by_mode.items()
        )
        return longest, cost

    def held_ids(self) -> set[str]:
        return {h["opportunity_id"] for h in self.obs.get("holdings", [])}

    def abilities(self) -> set[str]:
        return {a["ability_id"] for a in (self.obs.get("abilities") or [])}

    def household_tasks(self) -> list[dict[str, Any]]:
        """公开的家务基准（observation.resources.household_tasks）。"""
        return list(self.obs["resources"].get("household_tasks") or [])


def _relief(v: _View, o: dict[str, Any]) -> float:
    """这个方案在用时，本人每月少做的家务小时（按公开的家务基准与这个方案的残余；不超过基准）。"""
    te = o.get("time_energy") or {}
    tasks = {t["task"]: float(t["baseline_hours_per_month"]) for t in v.household_tasks()}
    total = 0.0
    for sub in te.get("substitutes") or []:
        base = tasks.get(sub["task"])
        if base is not None:
            total += max(0.0, base - min(base, float(sub["residual_hours_per_month"])))
    return total


class ProductMockPlanner:
    """按 M1–M8 规则读请求、给出年度计划的 mock（``Planner`` 接口）。"""

    identity = PRODUCT_MOCK_IDENTITY

    def __init__(self, scripts: Sequence[MockScript] = ()) -> None:
        self.scripts = tuple(scripts)
        self.calls = 0
        self._calls_lock = threading.Lock()  # 并行运行时几个分身同时调用

    def effective_spec(self) -> PlannerSpec:
        """实际生效的设定：身份与这个对象真正持有的受控行为（运行器拿它与运行配置的声明比较，E1-7A 收尾 F2）。"""
        return PlannerSpec(identity=self.identity, scripts=self.scripts)

    def _behaviors(self, clone: str, year: int) -> set[str]:
        return {s.behavior for s in self.scripts if s.clone_id == clone and year in s.years}

    def plan(self, call: PlannerCall) -> str:
        with self._calls_lock:
            self.calls += 1
        p = call.payload
        behaviors = self._behaviors(p["clone_id"], p["year_index"])
        if "raise_error" in behaviors:
            raise MockPlannerError(
                f"受控的意外异常（{p['clone_id']} 第 {p['year_index']} 年；演示程序错误怎样暂停批次）"
            )
        if "malformed_output" in behaviors:
            return MALFORMED_TEXT
        v = _View(p)
        revision = p.get("revision")
        notes: list[str] = []
        if revision is None:
            action = self._decide(v, notes)
            if "overcommit_first_draft" in behaviors:
                self._overcommit(v, action, notes)
            if "unqualified_first_draft" in behaviors:
                self._unqualified(v, action, notes)
            if "buy_hobby_gear" in behaviors:
                self._hobby_gear(v, action, notes)
        else:
            action = self._revise(v, revision, notes)
        alt = "alternate_wording" in behaviors
        return json.dumps(
            {
                "need_summary": self._need(v, alt),
                "rationale": self._rationale(notes, alt),
                "action": action,
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------ M1–M8

    def _decide(self, v: _View, notes: list[str]) -> dict[str, Any]:
        jobs = [self.opps_job(v, j) for j in v.current_jobs()]
        current_pay = max((_pay(o) for o in jobs), default=0.0)
        chosen_jobs = [o["opportunity_id"] for o in jobs]
        if v.career_minded():
            options = [o for o in v.of("job") if _available(o) and _qualified(o)]
            best = max(options, key=lambda o: (_pay(o), o["opportunity_id"]), default=None)
            if best is not None and _pay(best) > current_pay:
                chosen_jobs = [best["opportunity_id"]]
                notes.append(
                    f"M3 换岗：{best['opportunity_id']} 现在能选、月薪约 {_pay(best):,.0f}，高于现职 {current_pay:,.0f}"
                )
            else:
                notes.append("M3 保留现职：没有现在能选、工资更高的岗位")
        else:
            notes.append("M3 保留现职：用户的物质与发展点数都是 0，不为多挣钱换岗")
        income = sum(_pay(v.opps[j]) for j in chosen_jobs)
        foods = v.qualifying("food")
        food = foods[0]
        floor = v.min_living()
        houses = v.qualifying("housing")
        cap = v.max_commute()
        car = self._car(v, houses, chosen_jobs, income, food, floor, cap, notes)
        if car is not None:
            home, car_item, fare_car = car
            monthly_net = income - _monthly(home) - _monthly(food) - fare_car - _monthly(car_item)  # type: ignore[operator]
            items = [
                _period(home["opportunity_id"], 1, 12),
                _period(food["opportunity_id"], 1, 12),
                *(_period(j, 1, 12) for j in chosen_jobs),
                _period(car_item["opportunity_id"], 1, 12),
            ]
            notes.append(f"M2 食品：最便宜的合格食品 {food['opportunity_id']}")
            return self._finish(v, items, chosen_jobs, monthly_net, notes)
        commute = {h["opportunity_id"]: v.commute_for(h, chosen_jobs) for h in houses}
        reachable = [h for h in houses if commute[h["opportunity_id"]] is not None]
        if reachable:
            houses = reachable
        else:
            notes.append(
                "M1 通勤：合格住房到所选岗位都没有可用的通勤方式（通勤表里没有这条线），计划会被退回"
            )
        home = houses[0]

        def fare(h: dict[str, Any]) -> float:
            c = commute.get(h["opportunity_id"])
            return 0.0 if c is None else c[1]

        if cap is not None:
            ok = [h for h in houses if (commute.get(h["opportunity_id"]) or (1e9, 0.0))[0] <= cap]
            if ok:
                end = v.cash + 12 * (income - _monthly(ok[0]) - _monthly(food) - fare(ok[0]))  # type: ignore[operator]
                if end >= floor:
                    home = ok[0]
                    notes.append(
                        f"M1 住房：{home['opportunity_id']} 满足往返 ≤ {cap:g} 分钟的硬要求，推算年末现金约 {end:,.0f}"
                    )
                else:
                    notes.append(
                        f"M1 住房：满足硬要求的 {ok[0]['opportunity_id']} 推算年末现金只有约 {end:,.0f}，不到一个月最低"
                        f"生活费 {floor:,.0f}；今年选最便宜的 {home['opportunity_id']}，硬要求满足不了"
                    )
            else:
                notes.append("M1 住房：本城没有满足往返分钟硬要求的合格住房，选最便宜的")
        else:
            notes.append(f"M1 住房：没有住房硬要求，选最便宜的合格住房 {home['opportunity_id']}")
        notes.append(f"M2 食品：最便宜的合格食品 {food['opportunity_id']}")
        monthly_net = income - _monthly(home) - _monthly(food) - fare(home)  # type: ignore[operator]
        items = [
            _period(home["opportunity_id"], 1, 12),
            _period(food["opportunity_id"], 1, 12),
            *(_period(j, 1, 12) for j in chosen_jobs),
        ]
        return self._finish(v, items, chosen_jobs, monthly_net, notes)

    def _car(
        self,
        v: _View,
        houses: list[dict[str, Any]],
        jobs: list[str],
        income: float,
        food: dict[str, Any],
        floor: float,
        cap: float | None,
        notes: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], float] | None:
        """M1b：不用车的合格住房都到不了岗位（或都满足不了硬要求）时，按公开的通勤表与报价看买用车权能不能解决、付不付得起。
        返回（住房，用车机会，每月通勤费）；不需要、到不了或买不起是 None（理由写进 notes）。"""
        no_car = {h["opportunity_id"]: v.commute_for(h, jobs) for h in houses}
        ok = [
            h
            for h in houses
            if no_car[h["opportunity_id"]] is not None
            and (cap is None or no_car[h["opportunity_id"]][0] <= cap)  # type: ignore[index]
        ]
        if ok:
            return None  # 不用车就有合格住房：不买车
        found = v.car_service()
        if found is None:
            return None
        car, fn = found
        with_car = {h["opportunity_id"]: v.commute_for(h, jobs, frozenset({fn})) for h in houses}
        fit = [
            h
            for h in houses
            if with_car[h["opportunity_id"]] is not None
            and (cap is None or with_car[h["opportunity_id"]][0] <= cap)  # type: ignore[index]
        ]
        if not fit:
            notes.append(
                f"M1b 用车：不用车到不了所选岗位或满足不了硬要求，买了 {car['opportunity_id']} 开车也不行——不买车"
            )
            return None
        home = fit[0]
        minutes, fare = with_car[home["opportunity_id"]]  # type: ignore[misc]
        fee = _monthly(car) or 0.0
        end = v.cash + 12 * (
            income - (_monthly(home) or 0.0) - (_monthly(food) or 0.0) - fare - fee
        )
        if end < floor:
            notes.append(
                f"M1b 用车：开车能从 {home['opportunity_id']} 到岗（最长往返 {minutes:g} 分钟），但加上用车月费 {fee:,.2f} 与油费"
                f"每月 {fare:,.2f}，推算年末现金只有约 {end:,.0f}，不到一个月最低生活费 {floor:,.0f}——买不起，不买车"
            )
            return None
        notes.append(
            f"M1b 用车：不用车的合格住房到不了所选岗位或满足不了硬要求；买 {car['opportunity_id']}（月费 {fee:,.2f}，不含油费），"
            f"住 {home['opportunity_id']}，开车最长往返 {minutes:g} 分钟、每月油费约 {fare:,.2f}，推算年末现金约 {end:,.0f}"
        )
        return home, car, fare

    def _finish(
        self,
        v: _View,
        items: list[dict[str, Any]],
        chosen_jobs: list[str],
        monthly_net: float,
        notes: list[str],
    ) -> dict[str, Any]:
        spend = 0.0
        course = self._course(v, chosen_jobs, monthly_net, notes)
        if course is not None:
            oid, months, fee = course
            items.append(_period(oid, 1, months))
            spend += fee
        items += self._extras(v, monthly_net, spend, notes)
        # DECISIONS/0042：会随机坏的持续物品（持有的与今年要买的）预先声明坏了买同一款
        durable_ids = v.held_ids() | {it["opportunity_id"] for it in items}
        replacements = [
            {"held": oid, "replacement": oid}
            for oid in sorted(durable_ids)
            if ((v.opps.get(oid) or {}).get("failure") or {}).get("kind") == "annual_hazard"
        ]
        out = {
            "items": items,
            "minimum_fallback": ["housing", "food"],
            "savings_goal": None,
            "emergency": self._emergency(v, items),
        }
        if replacements:
            out["replacements"] = replacements
        return out

    @staticmethod
    def opps_job(v: _View, oid: str) -> dict[str, Any]:
        return v.opps[oid]

    def _course(
        self, v: _View, jobs: list[str], monthly_net: float, notes: list[str]
    ) -> tuple[str, int, float] | None:
        for t in v.obs.get("training", []):
            if not t["completed"]:
                left = t["months_required"] - t["months_participated"]
                fee = 0.0 if t.get("fee_paid") else (_price(v.opps[t["opportunity_id"]]) or 0.0)
                notes.append(f"M4 课程：接着学 {t['opportunity_id']}（还差 {left} 个月）")
                return t["opportunity_id"], min(12, left), fee
        if not v.career_minded():
            return None
        pay = max((_pay(v.opps[j]) for j in jobs), default=0.0)
        have = v.abilities()
        wanted = set()
        for o in v.of("job"):
            if not _available(o) or _pay(o) <= pay:
                continue
            for tag in o.get("eligibility", []):
                if tag.startswith("ability:") and tag[len("ability:") :] not in have:
                    wanted.add(tag[len("ability:") :])
        done = {t["opportunity_id"] for t in v.obs.get("training", []) if t["completed"]}
        floor = v.min_living()
        for o in sorted(v.of("training"), key=lambda x: x["opportunity_id"]):
            if (
                not _available(o)
                or o["opportunity_id"] in done
                or not set(o.get("grants", [])) & wanted
            ):
                continue
            fee = _price(o) or 0.0
            end = v.cash + 12 * monthly_net - fee
            month1 = v.cash + monthly_net  # 1 月领到工资、付完住房与食品之后
            months = 12 * int(o["duration"].get("completion_years") or 1)
            if month1 >= fee and end >= floor:
                notes.append(
                    f"M4 课程：{o['opportunity_id']} 授予本城更高薪岗位要求的能力，学费 {fee:,.0f}，推算年末现金约 {end:,.0f}"
                )
                return o["opportunity_id"], min(12, months), fee
            notes.append(
                f"M4 课程：{o['opportunity_id']} 学费 {fee:,.0f}；1 月付完住房与食品后约 {month1:,.0f}、付学费后推算年末"
                f"现金约 {end:,.0f}，今年不报名"
            )
        return None

    def _extras(
        self, v: _View, monthly_net: float, spend: float, notes: list[str]
    ) -> list[dict[str, Any]]:
        """M5 便利：住房、食品、岗位与课程之外的物质安排。"""
        if v.material_points.get("daily_convenience", 0) <= 0:
            return []
        substitutes = {
            oid for oid, o in v.opps.items() if (o.get("time_energy") or {}).get("substitutes")
        }
        held = [
            h["opportunity_id"]
            for h in v.obs.get("holdings", [])
            if h["opportunity_id"] in substitutes and (h.get("remaining_months") or 0) > 0
        ]
        if held:
            notes.append(f"M5c 便利：已有 {held} 在服务期内，今年不再买")
            return []
        options: list[tuple[float, str, str, float]] = []
        for oid in sorted(substitutes):
            o = v.opps[oid]
            relief = _relief(v, o)
            if not _available(o) or relief <= 0:
                continue
            price = _price(o)
            if price is None:
                continue
            if o["category"] == "durable":
                years = int((o.get("duration") or {}).get("lasts_years") or 0)
                if years <= 0:
                    continue
                per_month = price / (12 * years)
                options.append((per_month / relief, oid, "durable", price))
            elif _monthly(o) is not None:
                options.append((price / relief, oid, "service", price))
        floor = 3 * v.min_living()
        for cost, oid, kind, price in sorted(options):
            if kind == "durable":
                end = v.cash + 12 * monthly_net - spend - price
                if v.cash + monthly_net - spend >= price and end >= floor:
                    notes.append(
                        f"M5c 便利：买 {oid}（{price:,.0f}，每月少做 1 小时家务约 {cost:,.1f}），推算年末现金约 {end:,.0f}"
                    )
                    return [_once(oid, 1)]
            else:
                end = v.cash + 12 * (monthly_net - price) - spend
                if end >= floor:
                    notes.append(
                        f"M5c 便利：按月买 {oid}（每月 {price:,.0f}，每月少做 1 小时家务约 {cost:,.1f}），推算年末现金约 {end:,.0f}"
                    )
                    return [_period(oid, 1, 12)]
        if options:
            notes.append("M5c 便利：能替代家务的方案今年都付不起（按推算的年末现金），自己做")
        return []

    def _emergency(self, v: _View, items: list[dict[str, Any]]) -> dict[str, Any]:
        def kind(oid: str) -> str:
            return v.opps[oid]["category"]

        def active(oid: str) -> bool:
            te = v.opps[oid].get("time_energy") or {}
            return kind(oid) in ("training", "experience") or (
                kind(oid) == "service" and (te.get("hours") or 0) > 0
            )

        order = {"experience": 0, "service": 1, "training": 2}
        pause = sorted(
            (i["opportunity_id"] for i in items if active(i["opportunity_id"])),
            key=lambda oid: (order.get(kind(oid), 9), oid),
        )
        cancel = [i["opportunity_id"] for i in items if kind(i["opportunity_id"]) == "durable"]
        plan: dict[str, Any] = {"minor": v.treat(), "major": v.treat(), "pause_order": pause}
        if cancel:
            plan["cancel_purchases_when_ill"] = cancel
        return plan

    # ------------------------------------------------------------------ 受控行为

    def _overcommit(self, v: _View, action: dict[str, Any], notes: list[str]) -> None:
        jobs = [
            i["opportunity_id"]
            for i in action["items"]
            if v.opps[i["opportunity_id"]]["category"] == "job"
        ]
        extra = [j for j in v.current_jobs() if j not in jobs]
        if not extra:
            extra = [
                o["opportunity_id"]
                for o in sorted(v.of("job"), key=lambda o: (-_pay(o), o["opportunity_id"]))
                if _available(o) and _qualified(o) and o["opportunity_id"] not in jobs
            ][:1]
        for j in extra:
            action["items"].append(_period(j, 1, 12))
            notes.append(f"草稿：再做一份 {j}，想多挣一点（受控行为：第一份草稿多做一份工作）")
        action["emergency"] = self._emergency(v, action["items"])

    def _hobby_gear(self, v: _View, action: dict[str, Any], notes: list[str]) -> None:
        """受控行为：第 1 月买下目录里每一件可得的爱好装备（观察里有 ``equipment`` 事实的耐用品）。"""
        held = {i["opportunity_id"] for i in action["items"]}
        gear = sorted(
            oid
            for oid, o in v.opps.items()
            if o.get("equipment") and _available(o) and oid not in held
        )
        for oid in gear:
            action["items"].append({"opportunity_id": oid, "quantity": 1, "month": 1})
        if gear:
            notes.append(f"草稿：第 1 月买下 {gear}（受控行为：演示期末持有的爱好装备）")

    def _unqualified(self, v: _View, action: dict[str, Any], notes: list[str]) -> None:
        jobs = [o for o in v.of("job") if _available(o) and not _qualified(o)]
        if not jobs:
            return
        best = max(jobs, key=lambda o: (_pay(o), o["opportunity_id"]))
        action["items"] = [
            i for i in action["items"] if v.opps[i["opportunity_id"]]["category"] != "job"
        ] + [_period(best["opportunity_id"], 1, 12)]
        notes.append(f"草稿：选 {best['opportunity_id']}（受控行为：选一个年初还不合格的岗位）")

    # ------------------------------------------------------------------ 修订

    def _rank(self, v: _View, oid: str, top_job: str | None) -> int | None:
        o = v.opps.get(oid)
        if o is None:
            return None
        c = o["category"]
        if c == "job":
            return 0 if oid != top_job else None
        if c == "service":
            te = o.get("time_energy") or {}
            return 1 if (te.get("hours") or 0) > 0 else None
        return _PRIORITY.get(c)

    def _revise(self, v: _View, revision: dict[str, Any], notes: list[str]) -> dict[str, Any]:
        try:
            prev = json.loads(revision["previous_output_text"])
            action = prev["action"]
            assert isinstance(action, dict) and isinstance(action.get("items"), list)
        except (ValueError, KeyError, TypeError, AssertionError):
            notes.append("修订：上一份输出不能解析，按观察重新规划")
            return self._decide(v, notes)
        diags = revision["diagnostics"]
        codes = {d["code"] for d in diags}
        handled = {
            "time_over_limit",
            "energy_over_limit",
            "qualification_missing",
            "treatment_data_unknown",
        }
        if not codes <= handled:
            notes.append(f"修订：诊断 {sorted(codes - handled)} 需要重新规划")
            return self._decide(v, notes)
        items = [dict(i) for i in action["items"]]
        jobs = [
            i["opportunity_id"]
            for i in items
            if v.opps.get(i["opportunity_id"], {}).get("category") == "job"
        ]
        top_job = max(jobs, key=lambda j: (_pay(v.opps[j]), j), default=None)
        for d in diags:
            if d["code"] in ("time_over_limit", "energy_over_limit"):
                named = [c["item"] for c in d.get("contributors", [])]
                ranked = [
                    (r, oid)
                    for oid in named
                    if any(i["opportunity_id"] == oid for i in items)
                    and (r := self._rank(v, oid, top_job)) is not None
                ]
                if not ranked:
                    ranked = [
                        (r, i["opportunity_id"])
                        for i in items
                        if (r := self._rank(v, i["opportunity_id"], top_job)) is not None
                    ]
                if ranked:
                    _, drop = min(ranked)
                    items = [i for i in items if i["opportunity_id"] != drop]
                    notes.append(
                        f"修订：第 {d.get('month')} 月{'时间' if d['code'].startswith('time') else '精力'}超限，删掉 {drop}"
                    )
            elif d["code"] == "qualification_missing":
                drop = d.get("opportunity_id")
                items = [i for i in items if i["opportunity_id"] != drop]
                notes.append(f"修订：{drop} 年初还不合格，删掉")
                if not any(
                    v.opps.get(i["opportunity_id"], {}).get("category") == "job" for i in items
                ):
                    for j in v.current_jobs():
                        items.append(_period(j, 1, 12))
                        notes.append(f"修订：补回现职 {j}")
        previous = action.get("emergency") or {}
        # 暂停与取消清单按改过的计划重新列（只能列计划里的项目）；治不治沿用上一份的选择
        emergency = self._emergency(v, items)
        for k in ("minor", "major"):
            emergency[k] = previous.get(k, emergency[k])
        if "treatment_data_unknown" in codes:
            for t in (v.obs.get("health") or {}).get("treatments", []):
                if t.get("availability") == "unknown":
                    emergency[t["severity"]] = "decline"
                    notes.append(f"修订：本城{t['label']}资料未知，{t['severity']} 改成不治")
        return {
            "items": items,
            "minimum_fallback": action.get("minimum_fallback", ["housing", "food"]),
            "savings_goal": None,
            "emergency": emergency,
        }

    # ------------------------------------------------------------------ 文字

    @staticmethod
    def _need(v: _View, alt: bool) -> str:
        y = v.p["year_index"]
        if v.exact:
            top = sorted(v.exact.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            focus = "、".join(f"{k} {_points_text(n)}" for k, n in top if n > 0)
            basis = f"用户六维点数最高的是 {focus}"
        else:
            basis = "点数在这个信息条件下不可见，按文字与处境安排"
        unclear = [
            a["question_id"] for a in v.health_answers if a.get("answer_state") != "provided"
        ]
        health = (
            f"；健康问卷有 {len(unclear)} 题没有明确倾向，不替用户补"
            if unclear
            else ("；健康问卷的回答只作参考" if v.health_answers else "")
        )
        jobs = "、".join(v.current_jobs()) or "没有岗位"
        cash = f"{v.cash:,.2f}"
        policy = f"；应急按固定测试政策 {PRODUCT_MOCK_EMERGENCY_POLICY}"
        if alt:
            return (
                f"（换一种说法）第 {y} 年开头手上 {cash}，现在做 {jobs}；{basis}{health}{policy}。"[
                    :400
                ]
            )
        return f"第 {y} 年：现金 {cash}，现职 {jobs}；{basis}{health}{policy}。"[:400]

    @staticmethod
    def _rationale(notes: list[str], alt: bool) -> str:
        text = "；".join(notes) or "按规则安排"
        if alt:
            text = "（同样的安排，换一种说法）" + text
        return text[:1000]


#: 规则版本 → mock 规划器类。入口脚本按运行配置声明的身份取对应的类；不认识的规则版本直接报错，不回退。
MOCK_PLANNERS: dict[str, type[ProductMockPlanner]] = {
    PRODUCT_MOCK_RULES_VERSION: ProductMockPlanner,
}


def mock_planner_for(spec: PlannerSpec) -> ProductMockPlanner:
    """按运行配置声明的规划器设定（身份与受控行为）构造对应的 mock。身份对不上任何已知规则就报错。"""
    if spec.identity.kind != "mock":
        raise ValueError(f"规划器 {spec.identity.planner_id} 不是 mock")
    cls = MOCK_PLANNERS.get(spec.identity.rules_version or "")
    if cls is None or cls.identity != spec.identity:
        raise ValueError(
            f"不认识的 mock 身份 {spec.identity.planner_id} / {spec.identity.rules_version}：只有 "
            f"{sorted(MOCK_PLANNERS)}"
        )
    return cls(spec.scripts)
