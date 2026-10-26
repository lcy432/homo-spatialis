"""测试站的总额控制（所有者 2026-10-25：按“人数 × 次数 × 每次上限”控制，总额暂定 300 元）。

服务本身只有每次运行的费用上限（``max_cost_cny_max``）与每个身份的次数（``runs_total``），没有跨身份的总额。这里用最坏情况
把总额管住：

    已记账的花费（所有运行账本里的已核算 + 不确定 + 还没核算的预留）
  + 每个没撤销的身份还没用掉的次数（剩余 + 预留中）× 每次上限
  ≤ 总额

新建身份前先算：加上这个新身份的全部次数之后还在总额以内才签发，否则拒绝。只读服务库与账本；签发走服务自己的
``create_principal``（链接只显示一次）。不调用任何模型。

    uv run python scripts/site_budget.py status --config <服务配置> [--cap 300]
    uv run python scripts/site_budget.py invite --label <备注> --config <服务配置> [--cap 300]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from orchestration.life_budget import LEDGER_DB, ledger_state, read_ledger
from product.service.actions import ServiceContext
from product.service.config import load_service_config

DEFAULT_CAP = Decimal("300")


def committed_cost(events: list[dict[str, Any]], currency: str = "cny") -> Decimal:
    """一本账已经占用的金额：已核算的按实际，不确定的按保留的预留，还没核算的按预留。"""
    reserves, closes = ledger_state(events)
    total = Decimal(0)
    for key, r in reserves.items():
        c = closes.get(key)
        total += (
            Decimal(c[f"cost_{currency}"]) if c is not None else Decimal(r[f"reserved_{currency}"])
        )
    return total


@dataclass(frozen=True)
class Holder:
    principal_id: str
    label: str
    revoked: bool
    total: int
    used: int
    reserved: int

    @property
    def open_runs(self) -> int:
        """还可能花钱的次数：没用的 + 预留中的（预留中的已花部分也在账本里，这里按整次再算一遍，宁可多算）。"""
        return 0 if self.revoked else max(0, self.total - self.used)


@dataclass(frozen=True)
class Position:
    spent: Decimal
    ledgers: int
    holders: list[Holder]
    per_run: Decimal
    runs_per_holder: int
    cap: Decimal

    @property
    def open_exposure(self) -> Decimal:
        return sum((h.open_runs for h in self.holders), 0) * self.per_run

    @property
    def worst_case(self) -> Decimal:
        return self.spent + self.open_exposure

    @property
    def room(self) -> Decimal:
        return self.cap - self.worst_case

    def can_add(self) -> bool:
        return self.worst_case + self.runs_per_holder * self.per_run <= self.cap


def position(ctx: ServiceContext, cap: Decimal) -> Position:
    cfg = ctx.config
    data = cfg.data_root()
    spent = Decimal(0)
    n = 0
    for db in sorted(data.rglob(LEDGER_DB)):
        got = read_ledger(db.parent)
        if got is None:
            continue
        header, events = got
        cur = str(header.get("currency", "CNY")).lower()
        if cur != "cny":
            raise SystemExit(f"{db} 不是人民币账本（{cur}）：总额按人民币控制，不做换算")
        spent += committed_cost(events, cur)
        n += 1
    with ctx.store.read() as con:
        rows = con.execute(
            "SELECT principal_id, label, revoked_at FROM principals ORDER BY created_at"
        ).fetchall()
    holders = []
    for r in rows:
        q = ctx.store.quota(r["principal_id"])
        holders.append(
            Holder(
                r["principal_id"],
                r["label"],
                r["revoked_at"] is not None,
                q.total,
                q.used,
                q.reserved,
            )
        )
    _low, _default, high = cfg.cost_range
    return Position(spent, n, holders, high, cfg.runs_total, cap)


def _print(p: Position) -> None:
    print(f"总额上限：{p.cap} 元；每次运行上限 {p.per_run} 元；每个身份 {p.runs_per_holder} 次")
    print(f"已记账花费：{p.spent} 元（{p.ledgers} 本运行账本）")
    print("身份：")
    for h in p.holders:
        state = (
            "已撤销"
            if h.revoked
            else f"已用 {h.used}、预留 {h.reserved}、还能用 {max(0, h.total - h.used - h.reserved)}"
        )
        print(f"  {h.principal_id}  {h.label}  {state}")
    print(f"没用掉的次数最多还能花：{p.open_exposure} 元")
    print(f"最坏情况合计：{p.worst_case} 元；离总额还有 {p.room} 元")
    print("还能再签发一个身份" if p.can_add() else "不能再签发新身份（会超过总额）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "invite"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--cap", default=str(DEFAULT_CAP), help="总额上限（元）")
        if name == "invite":
            p.add_argument("--label", required=True)
    args = ap.parse_args(argv)
    ctx = ServiceContext.open(load_service_config(Path(args.config)))
    p = position(ctx, Decimal(args.cap))
    if args.cmd == "status":
        _print(p)
        return 0
    if not p.can_add():
        _print(p)
        print(
            "拒绝签发：加上这个身份的全部次数会超过总额。撤销不用的身份或提高总额（所有者决定）。",
            file=sys.stderr,
        )
        return 2
    principal, secret = ctx.store.create_principal(args.label)
    print(f"服务身份：{principal}")
    print("专属链接（只显示这一次，请直接交给对方；不要贴进日志或工单）：")
    print(f"{ctx.config.site_origin}/invite#token={secret}")
    after = position(ctx, Decimal(args.cap))
    print(f"签发后最坏情况合计 {after.worst_case} 元；离总额还有 {after.room} 元")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
