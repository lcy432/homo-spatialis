"""E1-7B 付费预算账本（`DECISIONS/0031` 第五节）：本轮所有真实服务请求共用一本账——先导、主试跑、诊断与服务重试都记在这里。

账本放在运行配置的 ``paid_budget.ledger_dir``（只在本机、不进 git：Flash 小规模测试配置用 ``runs/ledgers/product-flash``；
产品流程与托管服务每次运行在自己的工作目录里新建一本），是目录里的一个
SQLite 文件 ``ledger.sqlite``（2026-10-19 起取代原来的 budget.json 与 ledger.jsonl），两张表：

    header      一行：额度（请求次数、费用）与报价，第一次建立之后不改；配置与它不同就拒绝打开
    events      逐条事件（按写入顺序编号，事务提交即落盘）：reserve 发送之前的预留；settle 按实际用量核算；uncertain 没有用量

每行的 ``body`` 是整条记录的 JSON（与原来 budget.json / ledger.jsonl 里的一条内容相同），其余列只为查询方便，读取以 body 为准。

规则：

* 每次实际服务请求（含服务重试）都先预留：次数加 1，费用按"输入估计 × 输入单价 + 输出上限 × 输出单价"向上取到 0.000001 元。
  已占用（已核算的按核算值，未核算或不确定的按预留值）加上这次预留超过额度，就不发送。
* 有实际用量时按用量核算（向上取整），没有用量（超时、断连、服务错误、回复里没有用量）保留预留值、记为"费用不确定"，不记零。
* 额度属于账本、不属于运行：换运行目录、重启进程或换 run_id 都读同一本账，不会得到新的额度；每次预留之前重新读账，不缓存。
* 预留时"读已占用 → 判断额度 → 写入预留"在同一个写事务里（``BEGIN IMMEDIATE``），几个进程同时用一本账也不会合计超额。

这是按已记录报价执行的应用侧控制，不是供应商级的账单上限：费用是按报价与返回用量的估算，以供应商账单为准；实际输入 token 高于
估计时，最后一次请求的核算值可能超过它的预留，账本会如实记下并标出超额，但不能撤回已经发出的请求。

E1-7B 收尾 F2（``DECISIONS/0031`` 第八节）：

* ``budget_identity_problems``：规划器实际持有的账本必须就是运行声明的那一本——额度、报价全文、解析后的账本绝对路径与磁盘上的头文件
  逐项相同（不只比名字或报价版本）。运行器在建归档与第一次调用之前、恢复在读归档之前调用。
* ``ledger_history_problems``：恢复之前，按"运行 / 请求 / 服务尝试"的完整身份核对本运行的服务请求明细与账本：每次发送意图都有
  预留，每个结果都有对应的核算；缺预留、缺核算、金额矛盾都是问题，不当作空白预算、不猜测补回。共享账本里其他运行的条目照常存在。
* ``read_ledger``：只读读取（不存在就返回 None，不创建）；只读重放与付费对账用它，不改动任何账本。

2026-10-18（``DECISIONS/0035``）：账本按报价的币种记账。人民币报价照旧写 ``max_cost_cny``、``reserved_cny``、``cost_cny``（旧账本与旧记录
逐字节不变）；美元报价写 ``max_cost_usd``、``reserved_usd``、``cost_usd``。一本账只有一种币种，不换算。报价写了分档上限
（``max_input_tokens_at_price``）时，输入估计超过它就不预留、不发送；实际输入超过它由规划器按"超出报价档"停下。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from agentlayer.life_llm import BudgetDuplicate, BudgetRefused
from common.life_contracts import LifeContractError
from common.life_planning_contracts import PaidBudget
from common.runlog import ProviderCallRecord, utc_now_iso

BUDGET_FORMAT = "e1-7b-budget-v1"
LEDGER_DB = "ledger.sqlite"
BUSY_TIMEOUT_SECONDS = 30.0
_MICRO = Decimal("0.000001")
_MILLION = Decimal(1_000_000)


def _cur(spec: PaidBudget) -> str:
    """账本里金额栏的后缀：cny 或 usd（与报价同一币种）。"""
    return spec.currency.lower()


def _unit(spec: PaidBudget) -> str:
    return "元" if spec.currency == "CNY" else "美元"


def _amount(row: Any, stem: str) -> Any:
    """一条账本事件（dict）或请求明细记录（ProviderCallRecord）里的金额，不论币种栏是 cny 还是 usd。"""
    if isinstance(row, dict):
        return row.get(f"{stem}_cny", row.get(f"{stem}_usd"))
    value = getattr(row, f"{stem}_cny", None)
    return value if value is not None else getattr(row, f"{stem}_usd", None)


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def budget_header(spec: PaidBudget) -> dict[str, Any]:
    """账本头文件的内容（不含建立时间）：格式、额度与报价全文。建账与核对共用这一份。人民币账本的内容与之前逐字节相同。"""
    return {
        "format": BUDGET_FORMAT,
        "max_requests": spec.max_requests,
        f"max_cost_{_cur(spec)}": None
        if spec.max_cost is None
        else str(Decimal(str(spec.max_cost))),
        "price": spec.price.model_dump(mode="json"),
        "note": "应用侧按已记录报价执行的预算控制，不是供应商级账单上限；费用以供应商账单为准",
    }


def ledger_state(
    events: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """账本事件 → (预留, 核算)，按键。重复预留、没有预留的核算、重复核算、看不懂的事件都拒绝（``LifeContractError``）。"""
    reserves: dict[str, dict[str, Any]] = {}
    closes: dict[str, dict[str, Any]] = {}
    for e in events:
        key = e.get("key")
        if e.get("event") == "reserve":
            if key in reserves:
                raise LifeContractError(f"账本里 {key} 有两条预留：账本不一致")
            reserves[key] = e
        elif e.get("event") in ("settle", "uncertain"):
            if key not in reserves or key in closes:
                raise LifeContractError(f"账本里 {key} 的核算没有对应的预留或重复核算：账本不一致")
            closes[key] = e
        else:
            raise LifeContractError(f"账本里有看不懂的事件：{e.get('event')!r}")
    return reserves, closes


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """每次读写各开一个连接、用完就关；不用 WAL，账本始终是一个文件。"""
    with closing(sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)) as con:
        con.execute("PRAGMA synchronous = FULL")
        yield con


def _ensure(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS header (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT,"
        " event TEXT, run_id TEXT, body TEXT NOT NULL)"
    )


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
    )


def _read_header(con: sqlite3.Connection) -> dict[str, Any] | None:
    if not _has_table(con, "header"):
        return None
    row = con.execute("SELECT body FROM header WHERE id = 1").fetchone()
    return None if row is None else json.loads(row[0])


def _read_events(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _has_table(con, "events"):
        return []
    return [json.loads(b) for (b,) in con.execute("SELECT body FROM events ORDER BY seq")]


def _insert_event(con: sqlite3.Connection, event: dict[str, Any]) -> None:
    con.execute(
        "INSERT INTO events (key, event, run_id, body) VALUES (?, ?, ?, ?)",
        (
            event.get("key"),
            event.get("event"),
            event.get("run_id"),
            json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def read_ledger(ledger_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """只读读取一本账（头与事件）。不存在就返回 None——不创建、不写任何东西。"""
    db = Path(ledger_dir) / LEDGER_DB
    if not db.is_file():
        return None
    with _connect(db) as con:
        header = _read_header(con)
        return None if header is None else (header, _read_events(con))


def budget_identity_problems(declared: PaidBudget | None, planner: object) -> list[str]:
    """规划器实际持有的预算是不是运行声明的那一本（E1-7B 收尾 F2）。mock（声明为空）不该带付费账本；真实模型必须带，并且额度、
    报价全文、解析后的账本绝对路径与磁盘上的头文件都与声明相同。不只比名字或报价版本。"""
    actual = getattr(planner, "budget", None)
    if declared is None:
        return [] if actual is None else ["运行配置没有声明付费预算，规划器却带着一本账：不一致"]
    if actual is None:
        return ["运行配置声明了付费预算，规划器没有带账本：不能发送付费请求"]
    spec, where = getattr(actual, "spec", None), getattr(actual, "dir", None)
    if type(spec) is not PaidBudget or not isinstance(where, Path):
        return ["规划器的预算报不出它的声明与账本位置：无法与运行配置核对，不能发送付费请求"]
    problems: list[str] = []
    for name, got, want in (
        ("请求次数上限", spec.max_requests, declared.max_requests),
        ("费用上限", _dec(spec.max_cost), _dec(declared.max_cost)),
        ("报价", spec.price.model_dump(mode="json"), declared.price.model_dump(mode="json")),
    ):
        if got != want:
            problems.append(f"规划器实际持有的预算{name}与运行配置的声明不同")
    want_dir = Path(declared.ledger_dir).resolve()
    if where.resolve() != want_dir:
        problems.append(
            f"规划器实际持有的账本在 {where}，运行配置声明的是 {want_dir}：不能用另一本账"
        )
    found = read_ledger(want_dir)
    if found is not None:
        on_disk = {k: v for k, v in found[0].items() if k != "created_at"}
        if on_disk != budget_header(declared):
            problems.append(f"{want_dir / LEDGER_DB} 的额度或报价与运行配置的声明不同")
    else:
        problems.append(f"声明的账本 {want_dir} 还没有建立：规划器没有用这一本账")
    return problems


def ledger_history_problems(
    events: list[dict[str, Any]], *, run_id: str, records: list[ProviderCallRecord]
) -> list[str]:
    """本运行的服务请求明细与共享账本是否对得上（E1-7B 收尾 F2，恢复之前）。

    * 每次发送意图（键 ``运行/请求编号#s序号``）都有预留，运行、请求、服务尝试、输入估计、预留金额与报价版本相同；
    * 每个结果都有核算：有用量的是 settle，用量与金额与结果记录相同；没有用量的是 uncertain，金额等于预留；
    * 有意图、没有结果：预留可以还没核算（可能已发出、结果不明），也可以已经核算（费用已核算、回复没落盘）——都是合法中断；
    * 本运行的预留没有对应的发送意图：只允许一条、并且还没核算（已预留、发送意图没落盘——确定没有发出）；
    * 其他运行的条目不在这里判断（共享账本合法地包含先导、主试跑与其他授权运行）。"""
    try:
        reserves, closes = ledger_state(events)
    except LifeContractError as exc:
        return [str(exc)]
    problems: list[str] = []
    seen: set[str] = set()
    for r in records:
        if r.run_id != run_id:
            continue
        key = f"{run_id}/{r.request_id}#s{r.attempt}"
        res = reserves.get(key)
        if r.event == "intent":
            seen.add(key)
            if res is None:
                problems.append(
                    f"{key}：请求明细里有发送意图，账本里没有预留——费用历史缺失，不能当成空白预算"
                )
                continue
            want = (
                run_id,
                r.request_id,
                r.attempt,
                r.input_tokens_estimate,
                _amount(r, "reserved"),
                r.price_version,
            )
            got = (
                res.get("run_id"),
                res.get("request_id"),
                res.get("service_attempt"),
                res.get("input_tokens_estimate"),
                _amount(res, "reserved"),
                res.get("price_version"),
            )
            if got != want:
                problems.append(f"{key}：账本预留与请求明细的发送意图不一致")
            continue
        close = closes.get(key)
        if res is None:
            continue  # 已在发送意图那一条报过
        if close is None:
            problems.append(f"{key}：请求明细里有结果，账本里没有核算——费用历史缺失")
        elif r.cost_status == "estimated_from_usage":
            if (
                close.get("event"),
                close.get("prompt_tokens"),
                close.get("completion_tokens"),
                _amount(close, "cost"),
            ) != (
                "settle",
                r.prompt_tokens,
                r.completion_tokens,
                _amount(r, "cost"),
            ):
                problems.append(f"{key}：账本核算的用量或金额与请求明细不同")
        elif r.cost_status == "uncertain_no_usage":
            if (close.get("event"), _amount(close, "cost")) != (
                "uncertain",
                _amount(r, "cost"),
            ) or _amount(r, "cost") != _amount(res, "reserved"):
                problems.append(f"{key}：没有用量的请求在账本里没有按预留金额记为不确定")
        else:
            problems.append(f"{key}：结果记录的费用状态 {r.cost_status} 看不懂")
    orphans = [k for k, v in reserves.items() if v.get("run_id") == run_id and k not in seen]
    if any(k in closes for k in orphans):
        problems.append(
            f"本运行有已经核算、却没有发送意图的预留：{sorted(k for k in orphans if k in closes)[:3]}"
        )
    if len(orphans) > 1:
        problems.append(
            f"本运行有 {len(orphans)} 条没有发送意图的预留（合法的中断最多一条）：{sorted(orphans)[:3]}"
        )
    return problems


def _money(x: Decimal) -> str:
    return str(x.quantize(_MICRO, rounding=ROUND_CEILING))


@dataclass(frozen=True)
class BudgetTotals:
    """账本的合计（金额都是账本自己的币种 ``currency``）。``*_cny`` 是人民币账本的旧名字，只在人民币账本上可用。"""

    requests: int
    committed: Decimal  # 已核算 + 不确定（保留预留）+ 还没核算的预留
    settled: Decimal
    uncertain: Decimal
    outstanding: Decimal
    uncertain_requests: int
    overruns: int  # 核算值超过预留值的请求数
    currency: str = "CNY"

    def _cny(self, value: Decimal) -> Decimal:
        if self.currency != "CNY":
            raise AttributeError(f"这是 {self.currency} 账本，没有人民币金额")
        return value

    @property
    def committed_cny(self) -> Decimal:
        return self._cny(self.committed)

    @property
    def settled_cny(self) -> Decimal:
        return self._cny(self.settled)

    @property
    def uncertain_cny(self) -> Decimal:
        return self._cny(self.uncertain)

    @property
    def outstanding_cny(self) -> Decimal:
        return self._cny(self.outstanding)

    def as_dict(self) -> dict[str, Any]:
        c = self.currency.lower()
        return {
            "requests": self.requests,
            f"committed_{c}": _money(self.committed),
            f"settled_{c}": _money(self.settled),
            f"uncertain_{c}": _money(self.uncertain),
            f"outstanding_{c}": _money(self.outstanding),
            "uncertain_requests": self.uncertain_requests,
            "settled_above_reservation": self.overruns,
        }


class RoundBudget:
    """一本付费预算账本。``budget`` 是运行配置里的声明；账本已存在时，头文件必须与声明逐项相同。"""

    def __init__(self, budget: PaidBudget, *, root: Path | None = None) -> None:
        if type(budget) is not PaidBudget:
            raise LifeContractError(f"budget 必须是 PaidBudget，得到 {type(budget).__name__}")
        self.spec = budget
        # 建立时就定成绝对路径：之后工作目录变了，账本也不会被写到别处
        self.dir = (
            Path(budget.ledger_dir) if root is None else Path(root) / budget.ledger_dir
        ).resolve()
        self.price = budget.price
        self.price_version = budget.price.price_version
        self._in = Decimal(str(budget.price.input_per_million))
        self._out = Decimal(str(budget.price.output_per_million))
        #: None = 不设费用上限（DECISIONS/0035：额度可选，照常记账、不拦截）
        self._max_cost = _dec(budget.max_cost)
        self.currency = budget.currency
        self._c, self._u = _cur(budget), _unit(budget)
        header = budget_header(budget)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._write() as con:
            old = _read_header(con)
            if old is None:
                body = {**header, "created_at": utc_now_iso()}
                con.execute(
                    "INSERT INTO header (id, body) VALUES (1, ?)",
                    (json.dumps(body, ensure_ascii=False, separators=(",", ":")),),
                )
            elif {k: v for k, v in old.items() if k != "created_at"} != header:
                raise LifeContractError(
                    f"{self.path} 的额度或报价与运行配置不同：账本第一次建立之后不改，不能靠改配置得到新的额度"
                )

    @property
    def path(self) -> Path:
        """账本文件 ``<ledger_dir>/ledger.sqlite``。"""
        return self.dir / LEDGER_DB

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """一个写事务：进入时就拿写锁，读到的状态到提交为止不会被别的进程改。出错整体回滚。"""
        with _connect(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                _ensure(con)
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def header(self) -> dict[str, Any]:
        """账本里存的头（含建立时间）。"""
        with _connect(self.path) as con:
            found = _read_header(con)
        if found is None:
            raise LifeContractError(f"{self.path} 没有头：账本不完整")
        return found

    def events(self) -> list[dict[str, Any]]:
        with _connect(self.path) as con:
            return _read_events(con)

    def replace_events(self, rows: list[dict[str, Any]]) -> None:
        """整表改写成 ``rows``（按顺序重新编号）。只供测试模拟"账本被改动"；正常运行从不改写已有事件。"""
        with self._write() as con:
            con.execute("DELETE FROM events")
            con.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
            for r in rows:
                _insert_event(con, r)

    def _append(self, con: sqlite3.Connection, event: dict[str, Any]) -> None:
        _insert_event(con, {"ts": utc_now_iso(), **event})

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        raw = (
            Decimal(prompt_tokens) * self._in + Decimal(completion_tokens) * self._out
        ) / _MILLION
        return raw.quantize(_MICRO, rounding=ROUND_CEILING)

    def _state(
        self, con: sqlite3.Connection | None = None
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        return ledger_state(self.events() if con is None else _read_events(con))

    def reservation(self, key: str) -> dict[str, Any] | None:
        """一次服务尝试在账本里的状态：{"reserve": 预留, "close": 核算或 None}；没有预留就是 None。"""
        reserves, closes = self._state()
        if key not in reserves:
            return None
        return {"reserve": reserves[key], "close": closes.get(key)}

    def totals(self, con: sqlite3.Connection | None = None) -> BudgetTotals:
        reserves, closes = self._state(con)
        settled = uncertain = outstanding = Decimal(0)
        n_uncertain = overruns = 0
        for key, r in reserves.items():
            reserved = Decimal(r[f"reserved_{self._c}"])
            c = closes.get(key)
            if c is None:
                outstanding += reserved
            elif c["event"] == "settle":
                cost = Decimal(c[f"cost_{self._c}"])
                settled += cost
                overruns += cost > reserved
            else:
                uncertain += Decimal(c[f"cost_{self._c}"])
                n_uncertain += 1
        return BudgetTotals(
            requests=len(reserves),
            committed=settled + uncertain + outstanding,
            settled=settled,
            uncertain=uncertain,
            outstanding=outstanding,
            uncertain_requests=n_uncertain,
            overruns=overruns,
            currency=self.currency,
        )

    def above_price_tier(self, prompt_tokens: int) -> bool:
        """实际输入是否超过报价这一档的适用上限（报价不分档时永远是 False）。"""
        limit = self.price.max_input_tokens_at_price
        return limit is not None and prompt_tokens > limit

    def reserve(
        self,
        *,
        key: str,
        run_id: str,
        request_id: str,
        service_attempt: int,
        input_tokens_estimate: int,
        max_output_tokens: int,
    ) -> str:
        with self._write() as con:
            return self._reserve(
                con,
                key=key,
                run_id=run_id,
                request_id=request_id,
                service_attempt=service_attempt,
                input_tokens_estimate=input_tokens_estimate,
                max_output_tokens=max_output_tokens,
            )

    def _reserve(
        self,
        con: sqlite3.Connection,
        *,
        key: str,
        run_id: str,
        request_id: str,
        service_attempt: int,
        input_tokens_estimate: int,
        max_output_tokens: int,
    ) -> str:
        reserves, _ = self._state(con)
        if key in reserves:
            raise BudgetDuplicate(
                f"账本里已经有 {key} 的预留（可能是上次预留之后、发送记录落盘之前中断）"
            )
        limit = self.price.max_input_tokens_at_price
        if limit is not None and input_tokens_estimate > limit:
            raise BudgetRefused(
                f"输入估计 {input_tokens_estimate} token 超过报价 {self.price_version} 这一档的适用上限 {limit}：不按低档冒充"
            )
        t = self.totals(con)
        if self.spec.max_requests is not None and t.requests + 1 > self.spec.max_requests:
            raise BudgetRefused(
                f"付费请求次数已用 {t.requests} 次，再发一次会超过本轮上限 {self.spec.max_requests} 次"
            )
        amount = self.cost(input_tokens_estimate, max_output_tokens)
        if self._max_cost is not None and t.committed + amount > self._max_cost:
            raise BudgetRefused(
                f"已占用 {_money(t.committed)} {self._u}，这次预留 {_money(amount)} {self._u}，合计会超过本轮上限 "
                f"{_money(self._max_cost)} {self._u}"
            )
        self._append(
            con,
            {
                "event": "reserve",
                "key": key,
                "run_id": run_id,
                "request_id": request_id,
                "service_attempt": service_attempt,
                "input_tokens_estimate": input_tokens_estimate,
                "max_output_tokens": max_output_tokens,
                f"reserved_{self._c}": _money(amount),
                "price_version": self.price_version,
            },
        )
        return _money(amount)

    def settle(self, *, key: str, prompt_tokens: int, completion_tokens: int) -> str:
        with self._write() as con:
            reserves, closes = self._state(con)
            if key not in reserves or key in closes:
                raise LifeContractError(f"{key} 没有未核算的预留：不能核算")
            amount = self.cost(prompt_tokens, completion_tokens)
            self._append(
                con,
                {
                    "event": "settle",
                    "key": key,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    f"cost_{self._c}": _money(amount),
                    "above_reservation": amount > Decimal(reserves[key][f"reserved_{self._c}"]),
                },
            )
        return _money(amount)

    def settle_uncertain(self, *, key: str, reason: str) -> str:
        with self._write() as con:
            reserves, closes = self._state(con)
            if key not in reserves or key in closes:
                raise LifeContractError(f"{key} 没有未核算的预留：不能记为不确定")
            kept = reserves[key][f"reserved_{self._c}"]
            self._append(
                con,
                {"event": "uncertain", "key": key, "reason": reason[:80], f"cost_{self._c}": kept},
            )
        return kept

    def summary(self) -> dict[str, Any]:
        t = self.totals()
        return {
            "format": BUDGET_FORMAT,
            "ledger_dir": self.spec.ledger_dir,
            "max_requests": self.spec.max_requests,
            f"max_cost_{self._c}": None if self._max_cost is None else _money(self._max_cost),
            "price_version": self.price_version,
            **t.as_dict(),
            "remaining_requests": None
            if self.spec.max_requests is None
            else self.spec.max_requests - t.requests,
            f"remaining_{self._c}": None
            if self._max_cost is None
            else str((self._max_cost - t.committed).quantize(_MICRO, rounding=ROUND_FLOOR)),
            "note": "费用是按已记录报价与返回用量的应用侧估算，以供应商账单为准；不确定的请求保留预留值",
        }
