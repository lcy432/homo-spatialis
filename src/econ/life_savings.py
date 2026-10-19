"""目标储蓄的年度记录（E1-5B，`DECISIONS/0026`）。纯函数与一个逐月记录器：无 I/O、无随机、无 LLM。

结算（``econ.life_settlement``）是唯一调用方：

    年初   SavingsYearTracker(...)   应用年度计划里的目标指令（设定 / 保留 / 取消 / 替换 / 改时点）
    月末   tracker.month_end(...)    交给它这个完整月份的月末余额、当月实际支出与关联行动被拒的原因
    年末   tracker.finish(...)       取回这一年的目标事实（进结算结果）与目标的最新状态（进下一年状态）

它**不扣款、不付款、不锁钱**：钱只在原结算里流动，这里只读余额与已经发生的支出。它也不读偏好、评分或行动理由，
不加任何满足感。兑现只认年度行动里写了 ``goal_id`` 的那一条、在当月实际全额付清的那一笔（耐用品买成，或课程
学费付清——后者不表示学完）；只是"余额不少于报价"不算兑现。目标年月的月末仍未兑现，只记"到期未兑现"，
不出局、不自动延期。出局当月及以后结算不再调用 ``month_end``：此前的事实保留，之后的不再更新。

目录、状态与计划是否合法由 ``ledger.life_catalog`` 在结算之前检查（与加载器共用）；这里遇到违反那些检查的
输入直接报错，不猜。
"""

from __future__ import annotations

from collections.abc import Mapping

from common.life_billing import goal_quote, money_of_minor, one_time_total_minor
from common.life_contracts import (
    SAVINGS_GOAL_KIND_LABEL,
    CityLifeCatalog,
    GoalEvent,
    GoalMonth,
    GoalRealization,
    LifeContractError,
    PersonYearState,
    SavingsGoal,
    SavingsRules,
    SavingsYear,
    YearAction,
    savings_goal_kind,
)


def _update(goal: SavingsGoal, **changes: object) -> SavingsGoal:
    """改几个字段后重新走一遍校验（不用 model_copy，后者不校验）。"""
    data = goal.model_dump(mode="python")
    data.update(changes)
    return SavingsGoal.model_validate(data)


def _with_event(goal: SavingsGoal, event: GoalEvent, **changes: object) -> SavingsGoal:
    return _update(goal, events=(*goal.events, event), **changes)


class SavingsYearTracker:
    """一个分身一年的目标记录。由结算按年初 → 各月月末 → 年末的顺序驱动。"""

    def __init__(
        self,
        *,
        rules: SavingsRules,
        state: PersonYearState,
        action: YearAction,
        catalog: CityLifeCatalog,
        digits: int,
        cash_start_minor: int,
    ) -> None:
        if state.savings_goals is None:
            raise LifeContractError(f"{state.clone_id}: 状态没有 savings_goals，不能记录目标")
        self.rules = rules
        self.year = state.year_index
        self.basis = state.money_basis_id
        self.digits = digits
        self.catalog = catalog
        self.action = action
        self.goals: list[SavingsGoal] = list(state.savings_goals)
        self.events: list[GoalEvent] = []
        self.progress: list[GoalMonth] = []
        self.notes: list[str] = []
        self.realization: GoalRealization | None = None
        self.link_failure: str | None = None
        self.link_cancelled: str | None = None  # 收尾修订：关联的购买按年度应急声明因病取消的原因
        self._apply_directive()
        self.open_index = next((i for i, g in enumerate(self.goals) if g.open), None)
        self.link: tuple[int, str, int, int | None] | None = None  # (计划序号, 机会, 数量, 行动月)
        goal = self._open()
        for i, item in enumerate(action.items):
            if item.goal_id is None:
                continue
            if goal is None or item.goal_id != goal.goal_id:  # ledger 已检查
                raise LifeContractError(f"行动关联的目标 {item.goal_id} 不是这一年未结束的目标")
            when = item.month if item.month is not None else item.start_month
            self.link = (i, item.opportunity_id, int(item.quantity), when)
        self.quote_minor = self._current_quote(goal) if goal is not None else None
        if goal is not None:
            self.progress.append(self._snapshot(goal, None, cash_start_minor))

    # ------------------------------------------------------------------ 年初

    def _open(self) -> SavingsGoal | None:
        return None if self.open_index is None else self.goals[self.open_index]

    def _set_open(self, goal: SavingsGoal) -> None:
        assert self.open_index is not None
        self.goals[self.open_index] = goal

    def _event(
        self, goal_id: str, kind: str, month: int | None, note: str, **kw: object
    ) -> GoalEvent:
        ev = GoalEvent(
            goal_id=goal_id,
            kind=kind,  # type: ignore[arg-type]
            year_index=self.year,
            month=month,
            note=note[:280],
            **kw,  # type: ignore[arg-type]
        )
        self.events.append(ev)
        return ev

    def _new_goal(self) -> SavingsGoal:
        d = self.action.savings_goal
        assert d is not None and d.opportunity_id is not None and d.quantity is not None
        o = self.catalog.opportunity(d.opportunity_id)
        kind = None if o is None else savings_goal_kind(o)
        if o is None or kind is None:  # ledger 已检查
            raise LifeContractError(f"{d.opportunity_id} 不能当资金目标")
        quote = goal_quote(o, d.quantity, self.year, self.digits)
        label = SAVINGS_GOAL_KIND_LABEL[kind]
        note = (
            f"建立目标：{label} {o.label} × {d.quantity}，第 {d.target_year} 年第 {d.target_month} 月前；"
            f"建立时报价 {quote.total.amount:,.2f}（{quote.source_kind}，当前资料，不是价格预测）"
        )
        if d.replaces is not None:
            note += f"；替换旧目标 {d.replaces}"
        created = self._event(
            d.goal_id,
            "created",
            None,
            note,
            target_year=d.target_year,
            target_month=d.target_month,
            replaces=d.replaces,
        )
        return SavingsGoal(
            goal_id=d.goal_id,
            kind=kind,
            opportunity_id=d.opportunity_id,
            quantity=d.quantity,
            target_year=d.target_year,  # type: ignore[arg-type]
            target_month=d.target_month,  # type: ignore[arg-type]
            note=d.note,
            money_basis_id=self.basis,
            quote=quote,
            status="active",
            created_year=self.year,
            events=(created,),
        )

    def _apply_directive(self) -> None:
        d = self.action.savings_goal
        if d is None:
            return
        idx = next((i for i, g in enumerate(self.goals) if g.open), None)
        current = None if idx is None else self.goals[idx]
        if d.action in ("keep", "cancel", "reschedule", "replace"):
            target = d.replaces if d.action == "replace" else d.goal_id
            if current is None or idx is None or current.goal_id != target:  # ledger 已检查
                raise LifeContractError(f"{d.action} 指向的目标 {target} 不是现有的未结束目标")
            if d.action == "keep":
                ev = self._event(
                    current.goal_id, "kept", None, "年度计划明确保留目标，没有改任何字段"
                )
                self.goals[idx] = _with_event(current, ev)
            elif d.action == "cancel":
                ev = self._event(
                    current.goal_id,
                    "cancelled",
                    None,
                    "年度计划取消目标：不退款、不加钱、不发满足感",
                )
                self.goals[idx] = _with_event(current, ev, status="cancelled")
            elif d.action == "reschedule":
                ev = self._event(
                    current.goal_id,
                    "rescheduled",
                    None,
                    f"年度计划把目标时点从第 {current.target_year} 年第 {current.target_month} 月改到第 "
                    f"{d.target_year} 年第 {d.target_month} 月（不自动延期，这是计划明确的改动；原来的到期记录保留）",
                    target_year=d.target_year,
                    target_month=d.target_month,
                    previous_target_year=current.target_year,
                    previous_target_month=current.target_month,
                )
                self.goals[idx] = _with_event(
                    current,
                    ev,
                    status="active",
                    target_year=d.target_year,
                    target_month=d.target_month,
                )
            else:  # replace
                ev = self._event(
                    current.goal_id,
                    "replaced",
                    None,
                    f"被新目标 {d.goal_id} 替换：不退款、不把余额转成另一笔钱",
                    replaced_by=d.goal_id,
                )
                self.goals[idx] = _with_event(current, ev, status="replaced", replaced_by=d.goal_id)
                self.goals.append(self._new_goal())
        else:  # set
            if current is not None:  # ledger 已检查
                raise LifeContractError(f"已有未结束的目标 {current.goal_id}，不能再设定一个")
            self.goals.append(self._new_goal())

    def _current_quote(self, goal: SavingsGoal) -> int | None:
        """目录当前的报价 × 数量（最小单位）。未知、缺货或口径不同：返回 None——不补零、不比较。"""
        o = self.catalog.opportunity(goal.opportunity_id)
        if o is None or o.availability != "available" or o.price is None:
            self.notes.append(
                f"目标 {goal.goal_id} 的机会当前没有可用报价（未知或确定缺货）：差距记为未知，不补零"
            )
            return None
        if o.price.billing != "one_time" or o.price.amount.numeraire != self.basis:
            self.notes.append(
                f"目标 {goal.goal_id} 的当前报价不是同一口径的一次性报价：不比较、不改标签，差距记为未知"
            )
            return None
        return one_time_total_minor(o.price, goal.quantity, self.digits)

    def _snapshot(self, goal: SavingsGoal, month: int | None, cash_minor: int) -> GoalMonth:
        # 兑现那个月只记余额与状态：付款之后的余额再去比报价没有意义
        q = None if goal.status == "realized" else self.quote_minor
        return GoalMonth(
            goal_id=goal.goal_id,
            month=month,
            cash=money_of_minor(cash_minor, self.digits, self.basis),
            quote=None if q is None else money_of_minor(q, self.digits, self.basis),
            shortfall=None
            if q is None
            else money_of_minor(max(0, q - cash_minor), self.digits, self.basis),
            cash_covers_quote=None if q is None else cash_minor >= q,
            status=goal.status,
        )

    # ------------------------------------------------------------------ 月末

    def month_end(
        self,
        month: int,
        cash_minor: int,
        payments: Mapping[str, int],
        link_failure: str | None = None,
        *,
        link_cancelled: str | None = None,
    ) -> None:
        """一个完整过完的月份月末：按当月实际支出判断兑现，再判断是否到期，最后记一行进展。

        ``payments``：这个月每项机会的实际支出（最小单位，正数）；``link_failure``：关联行动这个月被拒或报名失败的原因；
        ``link_cancelled``（收尾修订）：关联的购买这个月按年度应急声明、因病取消的原因——取消不是被拒，原因分开写。
        """
        goal = self._open()
        if goal is None or not goal.open:
            return
        if link_failure is not None and self.link_failure is None:
            self.link_failure = link_failure
        if link_cancelled is not None and self.link_cancelled is None:
            self.link_cancelled = link_cancelled
        paid = payments.get(goal.opportunity_id, 0)
        if self.link is not None and paid > 0:
            order, oid, qty, _ = self.link
            o = self.catalog.opportunity(oid)
            if o is None or o.price is None:  # 付得出钱就一定有报价
                raise LifeContractError(f"{oid} 没有报价却有付款")
            full = one_time_total_minor(o.price, qty, self.digits)
            if paid != full:
                self.notes.append(
                    f"第 {month} 月 {oid} 付了 {paid / 10**self.digits:,.2f}，不是全额 "
                    f"{full / 10**self.digits:,.2f}：不算兑现"
                )
            else:
                durable = goal.kind == "durable_purchase"
                on_time = (self.year, month) <= (goal.target_year, goal.target_month)
                amount = money_of_minor(paid, self.digits, self.basis)
                what = (
                    f"实际买成 {o.label} × {qty}，付 {amount.amount:,.2f}；什么时候开始提供服务按原物品规则"
                    if durable
                    else f"学费全额付清 {amount.amount:,.2f}；这不表示学完或取得资格，学习进度按实际参加的月份另计"
                )
                self.realization = GoalRealization(
                    year_index=self.year,
                    month=month,
                    plan_order=order,
                    opportunity_id=oid,
                    quantity=qty,
                    amount_paid=amount,
                    evidence="durable_purchase_executed" if durable else "tuition_paid_in_full",
                    on_time=on_time,
                    note=what[:280],
                )
                timing = "按时" if on_time else "晚于目标时点"
                ev = self._event(goal.goal_id, "realized", month, f"目标兑现（{timing}）：{what}")
                goal = _with_event(goal, ev, status="realized", realization=self.realization)
                self._set_open(goal)
        elif paid > 0:
            self.notes.append(
                f"第 {month} 月实际买下或报名了目标的机会 {goal.opportunity_id}，但那一条行动没有写 goal_id："
                "不算兑现（兑现必须由行动明确关联）"
            )
        if goal.open and (self.year, month) == (goal.target_year, goal.target_month):
            ev = self._event(
                goal.goal_id,
                "due_unrealized",
                month,
                f"到第 {self.year} 年第 {month} 月月末仍未兑现：{self._why_unrealized(month)}。"
                "这不是生活出局，不自动延期，下一年可以重新安排购买或改时点",
            )
            goal = _with_event(goal, ev, status="overdue")
            self._set_open(goal)
        self.progress.append(self._snapshot(goal, month, cash_minor))

    def _why_unrealized(self, month: int) -> str:
        if self.link is None:
            return "这一年的计划没有关联目标的购买或报名"
        if self.link_cancelled is not None:
            return f"关联的购买已取消（{self.link_cancelled}）"
        if self.link_failure is not None:
            return f"关联行动被拒（{self.link_failure}）"
        when = self.link[3]
        if when is not None and when > month:
            return f"关联行动安排在第 {when} 月，晚于目标月"
        return "关联行动没有在目标月之前全额付清"

    # ------------------------------------------------------------------ 年末

    def finish(
        self, *, exit_month: int | None = None, stopped_month: int | None = None
    ) -> tuple[SavingsYear, tuple[SavingsGoal, ...]]:
        """这一年的目标事实与目标的最新状态（含已结束的，保留历史）。

        ``stopped_month``（E1-5C）：模拟在这个月停止（应急规则不足或资料缺口）——停止月的执行没有提交，之后不再更新目标。
        """
        goal = self._open()
        notes = list(self.notes)
        if exit_month is not None and goal is not None and goal.open:
            notes.append(
                f"第 {exit_month} 月付不起最低生活而出局：之后的月份不再更新目标（此前的事实保留）"
            )
        elif stopped_month is not None and goal is not None and goal.open:
            notes.append(
                f"第 {stopped_month} 月模拟停止（疾病之后应急安排不足或资料缺口）：停止月与之后不再更新目标"
                "（此前的事实保留）"
            )
        elif goal is not None and goal.open and self.link is not None and self.realization is None:
            reason = (
                f"关联的购买已取消（{self.link_cancelled}）"
                if self.link_cancelled is not None
                else self.link_failure or "关联行动没有全额付清"
            )
            notes.append(f"这一年目标 {goal.goal_id} 未兑现：{reason}")
        d = self.action.savings_goal
        record = SavingsYear(
            rules_version=self.rules.version,
            directive=None if d is None else d.action,
            directive_goal_id=None if d is None else d.goal_id,
            linked_plan_order=None if self.link is None else self.link[0],
            events=tuple(self.events),
            progress=tuple(self.progress),
            realization=self.realization,
            notes=tuple(n[:280] for n in notes),
        )
        return record, tuple(self.goals)
