"""服务端 SQLite：托管服务的权威记录（DECISIONS/0043）。

* 邀请与会话只存秘密的 SHA-256 摘要；重发链接沿用原身份与次数，旧链接与旧会话一起失效。
* 次数以不可重复的事件记账（``quota_events``）：预留、转为已使用、释放各一次（同一意图唯一），管理员修正也追加记录，不改历史。
* “接受确认、预留次数、保存稳定运行编号、创建队列任务”在一个 ``BEGIN IMMEDIATE`` 短事务里完成；事务里不做任何网络或文件操作。
* 发送许可与删除标记在同一个库里串行裁决：删除之前已经拿到许可的服务尝试算在途，删除之后不再批准。
* 删除只留最小记录（不透明身份、次数事件、必要的费用摘要、删除时间与防重记录）；个人正文从表里清掉，``secure_delete`` 打开，
  删除后做 WAL 检查点。这不是磁盘级的安全擦除。

写回规则（E3C 收尾 R1–R3）：

* **数据代次**：每个身份有一个内部的 ``data_generation``，“删除全部”把它加一。每个会产生或恢复正文的写入（新建与保存草稿、
  审阅、确认画像、创建运行意图）都带着请求开始时读到的代次，在写回事务里核对；对不上就拒绝写入（之前开始、还没写回的业务操作
  全部失效）。代次不进任何模型请求。
* **来源有效**：审阅写回时在事务里重新核对草稿还在、属于本人、没删、没过期、修订号没变；确认画像写回时同样核对审阅与草稿；创建
  运行意图时核对确认画像还在、没过期。
* **期限**：审阅随源草稿（草稿改了新修订，旧修订的审阅当场作废；草稿过期或删除，审阅一起清掉）；确认画像从首次确认起
  ``profile_days`` 天内可以用来准备新模拟（重复确认、查询不续期），过期后没有正在检查的意图引用就清掉正文；运行意图按最后一次有效
  业务操作或首次结束起算。确认开始、恢复、取消这些写入**先**核对截止时间：已经到期的就在同一个事务里按过期处理，再拒绝——不会先刷新
  业务时间再变成“有效”。worker 重启时没有执行者的“运行中”记录也按这条规则处理。

连接按调用打开、用完关闭（不跨线程共享）；只支持本机磁盘，不支持网络共享盘或多机共用。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product.service.clock import Clock, days_after, iso
from product.service.errors import ServiceFailure
from product.service.tokens import digest, new_secret, opaque

STORE_VERSION = "e3c-service-store-v4"
#: 尚未结束的意图（同一身份最多一个）
OPEN_STAGES = (
    "checking",
    "awaiting_confirmation",
    "blocked",
    "queued",
    "running",
    "needs_attention",
)
#: 不可见、不能再操作的阶段
GONE_STAGES = ("deleting", "deleted", "expired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    data_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    invite_digest TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    generation INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS invites_one_active ON invites(principal_id) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS sessions (
    session_digest TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    revision INTEGER NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    draft_fingerprint TEXT NOT NULL,
    pack_fingerprint TEXT NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    display_body TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
    confirmed_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    review_id TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT,
    display_body TEXT
);
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    confirmed_id TEXT NOT NULL,
    confirmed_fingerprint TEXT NOT NULL,
    execution_fingerprint TEXT NOT NULL,
    execution_body TEXT,
    run_options TEXT,
    workflow_key TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    waiting_for TEXT NOT NULL,
    preflight_fingerprint TEXT,
    preflight_body TEXT,
    run_id TEXT UNIQUE,
    registered_at TEXT,
    resume_allowed INTEGER NOT NULL DEFAULT 0,
    explanation TEXT,
    support_ref TEXT,
    progress_body TEXT,
    result_body TEXT,
    result_fingerprint TEXT,
    snapshot TEXT,
    cost_summary TEXT,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    first_finished_at TEXT,
    result_expires_at TEXT,
    delete_requested_at TEXT,
    deleted_at TEXT,
    preflight_display TEXT,
    progress_display TEXT,
    result_display TEXT,
    explanation_display TEXT,
    start_authorization TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS intents_one_open ON intents(principal_id)
    WHERE stage IN ('checking', 'awaiting_confirmation', 'blocked', 'queued', 'running', 'needs_attention');
CREATE TABLE IF NOT EXISTS submissions (
    key_digest TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT NOT NULL,
    intent_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('reserve', 'consume', 'release', 'admin_credit', 'admin_debit')),
    at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (intent_id, kind)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('preflight', 'run', 'resume', 'delete')),
    intent_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'active', 'done', 'cancelled')),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state, job_id);
CREATE TABLE IF NOT EXISTS send_permits (
    permit_key TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    granted_at TEXT NOT NULL
);
"""

#: 删除或过期时从意图上清掉的内容（预检查与结果里有答案与生活故事；运行选项里有用户选的城市）
#: 显示附层的私有绑定（E3 → E5 接口补齐）：跟着它所属的对象写、跟着它一起清，不是第二份个人资料
DISPLAY_CONTENT = ("preflight_display", "progress_display", "result_display", "explanation_display")
INTENT_CONTENT = (
    "preflight_body",
    "progress_body",
    "result_body",
    "explanation",
    "snapshot",
    "execution_body",
    "run_options",
    "start_authorization",
    *DISPLAY_CONTENT,
)
#: 取消时清掉的内容（取消的意图不再需要运行前说明；选的设置照样显示，直到过期或删除）
_CANCEL_CONTENT = (
    "preflight_body",
    "progress_body",
    "result_body",
    "explanation",
    "snapshot",
    *DISPLAY_CONTENT,
)
#: v2 → v3：只加显示绑定的列，原有的用户内容、次数与记录都不动（草稿的显示文字在读取时按草稿当场生成，不保存绑定）
_MIGRATE_V3 = {
    "reviews": ("display_body",),
    "profiles": ("display_body",),
    "intents": DISPLAY_CONTENT,
}
#: v3 → v4（一次开始，DECISIONS/0048）：只加开始授权的列；已有的意图没有授权，照旧走“检查之后确认开始”
_MIGRATE_V4 = {"intents": ("start_authorization",)}
#: 依次就地升级：（从哪个版本，加哪些列，升到哪个版本）
_UPGRADES = (
    ("e3c-service-store-v2", _MIGRATE_V3, "e3c-service-store-v3"),
    ("e3c-service-store-v3", _MIGRATE_V4, "e3c-service-store-v4"),
)


@dataclass(frozen=True)
class StartRequest:
    """一次“确认并开始”的授权（服务端已经按当前预览核对过同意身份与知悉项，DECISIONS/0048）：检查通过后按这次的设置开始，
    只一次。``key`` 是这次操作的幂等键（同一次操作的重试带同一个键），``request_fingerprint`` 是这次请求本身的指纹（同一个键配不同
    请求是冲突）。"""

    consent_fingerprint: str
    acknowledged: tuple[str, ...]
    key: str
    request_fingerprint: str


def start_key_digest(principal_id: str, key: str) -> str:
    """一次开始的幂等键在服务库里的写法（与确认开始的键分开命名，互不冲突）。"""
    return digest(f"start|{principal_id}|{key}")


def start_authorization(row: Any) -> dict[str, Any] | None:
    """意图上保存的开始授权（没有就是旧流程：检查之后由用户确认开始）。"""
    try:
        raw = row["start_authorization"]
    except (IndexError, KeyError):
        return None
    return None if raw is None else json.loads(raw)


def intent_deadline(row: Any, intent_days: int, *, running: bool = False) -> tuple[str | None, str]:
    """一个意图当前适用的保存截止时间与它按什么算（``ExpiryBasis``）——期限判断（``intent_due``）与公开视图共用这一条规则：
    删除中、已删除、已过期没有可用期限（``unavailable``）；运行中的、有执行者的不给固定截止时间（``active_execution``，调用方确认它
    没有执行者时传 ``running=True`` 按下面的规则）；有结果的按首次结束起的结果期限（``first_finished``）；其他按最后一次有效业务操作
    起 ``intent_days`` 天（``intent_activity``）。查看、轮询与心跳都不改这两个时间。"""
    stage = row["stage"]
    if stage in ("deleting", "deleted", "expired"):
        return None, "unavailable"
    if stage == "running" and not running:
        return None, "active_execution"
    if row["result_expires_at"] is not None:
        return row["result_expires_at"], "first_finished"
    return days_after(row["last_activity_at"], intent_days), "intent_activity"


def intent_due(row: Any, now: str, intent_days: int, *, running: bool = False) -> bool:
    """一个意图是否已经到期（API 访问、写入事务与 worker 清理用同一条规则，见 ``intent_deadline``）。运行中的只在调用方确认它没有
    执行者（``running=True``：worker 重启时上一个进程留下的记录，或不是当前正在执行的那一个）时按同样的规则判断。"""
    deadline, _ = intent_deadline(row, intent_days, running=running)
    return deadline is not None and deadline <= now


def expired_subject(row: Any) -> str:
    """一个已经到期的意图在错误说明里算哪一类：有过结果的是“结果”，否则是“运行意图”。"""
    return "result" if row["first_finished_at"] is not None else "intent"


class _CommitThenRaise(Exception):  # noqa: N818 - 只在 ``ServiceStore.write`` 内部用的控制信号
    """写事务里发现意图已经到期：这个事务里已经做的过期处理（退回没用的预留、取消待执行的任务、清掉正文）要先提交，再把
    ``error`` 交给调用方——拒绝本身不能把过期处理一起回滚（E3C 收尾复核 R3）。调用方要在任何其他写入之前抛它。"""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass(frozen=True)
class Quota:
    total: int
    used: int
    reserved: int

    @property
    def available(self) -> int:
        return max(0, self.total - self.used - self.reserved)

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "used": self.used,
            "reserved": self.reserved,
            "available": self.available,
        }


def _authorization(start: StartRequest, principal_id: str, now: str) -> str:
    """保存在意图上的开始授权：同意身份、知悉项、授权时间、状态（还没用 / 已用 / 作废）与这次操作的键（服务库里的写法）。"""
    return json.dumps(
        {
            "mode": "start_when_ready",
            "consent_fingerprint": start.consent_fingerprint,
            "acknowledged": sorted(start.acknowledged),
            "authorized_at": now,
            "state": "pending",
            "key_digest": start_key_digest(principal_id, start.key),
        },
        sort_keys=True,
    )


def _gone(row: Any) -> ServiceFailure:
    """删除中、已删除、已过期的意图对写操作的统一回答。"""
    stage = row["stage"]
    if stage == "expired":
        return ServiceFailure("expired", subject=expired_subject(row))
    return ServiceFailure("deleting" if stage == "deleting" else "deleted")


class ServiceStore:
    def __init__(
        self,
        path: Path | str,
        clock: Clock,
        *,
        runs_total: int = 3,
        intent_days: int = 30,
        profile_days: int = 30,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.runs_total = runs_total
        self.intent_days = intent_days
        self.profile_days = profile_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('store_version', ?)",
                (STORE_VERSION,),
            )
            got = con.execute("SELECT value FROM meta WHERE key = 'store_version'").fetchone()[0]
            for old, columns, new in _UPGRADES:
                if got == old:
                    self._migrate(con, columns, new)
                    got = new
            if got != STORE_VERSION:
                raise RuntimeError(f"服务库版本 {got} 与代码 {STORE_VERSION} 不同")
        finally:
            con.close()

    @staticmethod
    def _migrate(con: sqlite3.Connection, columns: dict[str, tuple[str, ...]], to: str) -> None:
        """就地升一级：在一个事务里加空列、改版本号。用户的草稿、确认、运行、次数事件与会话都保留，不要求删库重建。v2 → v3 加显示
        绑定的列（已有对象没有绑定，显示为 ``no_binding``，不在读取时补写）；v3 → v4 加开始授权的列（已有意图没有授权，照旧流程）。"""
        con.execute("BEGIN IMMEDIATE")
        try:
            for table, names in columns.items():
                have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                for column in names:
                    if column not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            con.execute("UPDATE meta SET value = ? WHERE key = 'store_version'", (to,))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ 连接与事务

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA secure_delete=ON")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """短写事务（``BEGIN IMMEDIATE``）：开头就拿写锁，并发提交排队，不会读到一半被别人改。事务里不做网络或文件操作。
        出错就回滚；只有 ``_CommitThenRaise``（到期的过期处理）先提交、做一次 WAL 检查点（清掉的正文不留在 WAL 里），再抛出它带的错误。"""
        con = self._connect()
        refused: _CommitThenRaise | None = None
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except _CommitThenRaise as stop:
                con.execute("COMMIT")
                refused = stop
            except BaseException:
                con.execute("ROLLBACK")
                raise
            else:
                con.execute("COMMIT")
        finally:
            con.close()
        if refused is not None:
            self.checkpoint()
            raise refused.error from None

    def now(self) -> str:
        return iso(self.clock())

    def checkpoint(self) -> None:
        """删除之后：把 WAL 里的旧页写回并截断（不让删掉的内容留在 WAL 文件里）。"""
        con = self._connect()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()

    # ------------------------------------------------------------------ 邀请与会话

    def create_principal(self, label: str) -> tuple[str, str]:
        """新建一个服务身份与它的专属链接秘密。秘密只在这里返回一次，库里只存摘要。"""
        principal = opaque("pr")
        secret = new_secret()
        now = self.now()
        with self.write() as con:
            con.execute(
                "INSERT INTO principals (principal_id, label, created_at) VALUES (?, ?, ?)",
                (principal, label[:120], now),
            )
            con.execute(
                "INSERT INTO invites (invite_digest, principal_id, generation, created_at) VALUES (?, ?, 1, ?)",
                (digest(secret), principal, now),
            )
        return principal, secret

    def reissue_invite(self, principal_id: str) -> str:
        """重发链接：沿用原身份与次数；旧链接与全部旧会话失效。"""
        secret = new_secret()
        now = self.now()
        with self.write() as con:
            row = con.execute(
                "SELECT revoked_at FROM principals WHERE principal_id = ?", (principal_id,)
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise KeyError(principal_id)
            gen = con.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM invites WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()[0]
            con.execute(
                "UPDATE invites SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
                (now, principal_id),
            )
            con.execute(
                "UPDATE sessions SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
                (now, principal_id),
            )
            con.execute(
                "INSERT INTO invites (invite_digest, principal_id, generation, created_at) VALUES (?, ?, ?, ?)",
                (digest(secret), principal_id, gen + 1, now),
            )
        return secret

    def revoke_principal(self, principal_id: str) -> None:
        now = self.now()
        with self.write() as con:
            con.execute(
                "UPDATE principals SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
                (now, principal_id),
            )
            con.execute(
                "UPDATE invites SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
                (now, principal_id),
            )
            con.execute(
                "UPDATE sessions SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
                (now, principal_id),
            )

    def principal_for_invite(self, secret: str) -> str | None:
        with self.read() as con:
            row = con.execute(
                "SELECT i.principal_id FROM invites i JOIN principals p USING (principal_id) "
                "WHERE i.invite_digest = ? AND i.revoked_at IS NULL AND p.revoked_at IS NULL",
                (digest(secret),),
            ).fetchone()
        return None if row is None else row[0]

    def create_session(self, principal_id: str, days: int) -> tuple[str, str]:
        """新会话：固定有效期，不因查询延长。返回（会话秘密，到期时间）。"""
        secret = new_secret()
        now = self.now()
        expires = days_after(now, days)
        with self.write() as con:
            con.execute(
                "INSERT INTO sessions (session_digest, principal_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (digest(secret), principal_id, now, expires),
            )
        return secret, expires

    def session(self, secret: str) -> tuple[str, str, int] | None:
        """有效会话的（身份，到期时间，请求开始时的数据代次）；到期、撤销、身份撤销都是 None。"""
        now = self.now()
        with self.read() as con:
            row = con.execute(
                "SELECT s.principal_id, s.expires_at, p.data_generation FROM sessions s "
                "JOIN principals p USING (principal_id) WHERE s.session_digest = ? AND s.revoked_at IS NULL "
                "AND p.revoked_at IS NULL AND s.expires_at > ?",
                (digest(secret), now),
            ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def revoke_session(self, secret: str) -> None:
        with self.write() as con:
            con.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_digest = ? AND revoked_at IS NULL",
                (self.now(), digest(secret)),
            )

    def _check_generation(
        self, con: sqlite3.Connection, principal_id: str, generation: int
    ) -> None:
        """写回事务里核对数据代次：请求开始之后用户“删除了全部”，这次写入就失效（不写回任何正文）。"""
        row = con.execute(
            "SELECT data_generation FROM principals WHERE principal_id = ?", (principal_id,)
        ).fetchone()
        if row is None or row[0] != generation:
            raise ServiceFailure("deleted")

    # ------------------------------------------------------------------ 次数

    def quota(self, principal_id: str, con: sqlite3.Connection | None = None) -> Quota:
        """公开的次数。已经到期、只等清理的意图的预留不算预留（过期一定会退回它）；只读，不写库。"""

        def count(c: sqlite3.Connection) -> Quota:
            rows = dict(
                c.execute(
                    "SELECT kind, COUNT(*) FROM quota_events WHERE principal_id = ? GROUP BY kind",
                    (principal_id,),
                ).fetchall()
            )
            reserve, consume, release = (
                rows.get("reserve", 0),
                rows.get("consume", 0),
                rows.get("release", 0),
            )
            used = consume + rows.get("admin_debit", 0) - rows.get("admin_credit", 0)
            held = max(0, reserve - consume - release)
            if held:
                now = self.now()
                open_reserved = c.execute(
                    "SELECT i.* FROM intents i JOIN quota_events q ON q.intent_id = i.intent_id AND q.kind = 'reserve' "
                    "WHERE i.principal_id = ? AND NOT EXISTS (SELECT 1 FROM quota_events x WHERE x.intent_id = "
                    "i.intent_id AND x.kind IN ('consume', 'release'))",
                    (principal_id,),
                ).fetchall()
                held -= sum(1 for r in open_reserved if intent_due(r, now, self.intent_days))
            return Quota(total=self.runs_total, used=max(0, used), reserved=max(0, held))

        if con is not None:
            return count(con)
        with self.read() as c:
            return count(c)

    def consume(self, intent_id: str, note: str = "") -> bool:
        """预留转为已使用（准备首次启动 E1 时）。重复调用不重复扣；没有预留或已经释放就拒绝。

        首次转为已使用之前，在同一个事务里核对期限（E3C 收尾复核 R3）：已经到期就按过期处理并提交（预留只退一次、取消待执行的
        任务），然后拒绝——过期的运行不进入 E1、不扣次数、不发请求。这时意图虽然已标为运行中，E1 还没有启动，所以按“没有执行者”
        的同一条期限规则判断；已经转为已使用的（恢复、重复调用）照旧，正常执行中的运行不受影响。"""
        with self.write() as con:
            kinds = {
                r[0]
                for r in con.execute(
                    "SELECT kind FROM quota_events WHERE intent_id = ?", (intent_id,)
                )
            }
            if "consume" in kinds:
                return False
            if "reserve" not in kinds or "release" in kinds:
                raise RuntimeError(f"{intent_id} 没有有效的预留，不能开始")
            row = con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
            now = self.now()
            if intent_due(row, now, self.intent_days, running=True):
                self._expire_row(con, row, now)
                raise _CommitThenRaise(RuntimeError(f"{intent_id} 已经过期，不能开始"))
            principal = row["principal_id"]
            con.execute(
                "INSERT INTO quota_events (principal_id, intent_id, kind, at, note) VALUES (?, ?, 'consume', ?, ?)",
                (principal, intent_id, self.now(), note[:200]),
            )
            return True

    def _consumed(self, con: sqlite3.Connection, intent_id: str) -> bool:
        """这个意图是否已经开始过（服务库里权威的启动记录：预留在首次启动 E1 之前转为已使用）。"""
        return (
            con.execute(
                "SELECT 1 FROM quota_events WHERE intent_id = ? AND kind = 'consume' LIMIT 1",
                (intent_id,),
            ).fetchone()
            is not None
        )

    def _release(self, con: sqlite3.Connection, intent_id: str, note: str) -> bool:
        kinds = {
            r[0]
            for r in con.execute("SELECT kind FROM quota_events WHERE intent_id = ?", (intent_id,))
        }
        if "reserve" not in kinds or kinds & {"consume", "release"}:
            return False
        principal = con.execute(
            "SELECT principal_id FROM intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()[0]
        con.execute(
            "INSERT INTO quota_events (principal_id, intent_id, kind, at, note) VALUES (?, ?, 'release', ?, ?)",
            (principal, intent_id, self.now(), note[:200]),
        )
        return True

    def release(self, intent_id: str, note: str = "") -> bool:
        """释放预留（开始前取消、启动前检查失败）。已经开始的不退。返回这次是否真的退回了。"""
        with self.write() as con:
            return self._release(con, intent_id, note)

    def quota_state(self, intent_id: str) -> dict[str, bool]:
        """一个意图的次数事件：预留过没有、转为已使用没有、退回了没有（说明文字按它写，不按停止原因猜）。"""
        with self.read() as con:
            kinds = {
                r[0]
                for r in con.execute(
                    "SELECT kind FROM quota_events WHERE intent_id = ?", (intent_id,)
                )
            }
        return {
            "reserved": "reserve" in kinds,
            "used": "consume" in kinds,
            "released": "release" in kinds,
        }

    def admin_adjust(self, principal_id: str, *, credit: bool, note: str) -> None:
        """管理员修正误扣：追加一条记录，不改历史。"""
        with self.write() as con:
            con.execute(
                "INSERT INTO quota_events (principal_id, intent_id, kind, at, note) VALUES (?, NULL, ?, ?, ?)",
                (principal_id, "admin_credit" if credit else "admin_debit", self.now(), note[:200]),
            )

    def quota_events(self, principal_id: str) -> list[dict[str, Any]]:
        with self.read() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT event_id, intent_id, kind, at, note FROM quota_events WHERE principal_id = ? ORDER BY event_id",
                    (principal_id,),
                )
            ]

    # ------------------------------------------------------------------ 草稿、审阅与确认

    def create_draft(
        self, principal_id: str, make_body: Callable[[str], str], days: int, *, generation: int
    ) -> sqlite3.Row:
        draft_id = opaque("draft")
        now = self.now()
        with self.write() as con:
            self._check_generation(con, principal_id, generation)
            con.execute(
                "INSERT INTO drafts (draft_id, principal_id, revision, body, created_at, updated_at, expires_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?)",
                (draft_id, principal_id, make_body(draft_id), now, now, days_after(now, days)),
            )
            return con.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()

    def draft(self, principal_id: str, draft_id: str) -> sqlite3.Row:
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM drafts WHERE draft_id = ? AND principal_id = ?",
                (draft_id, principal_id),
            ).fetchone()
        return self._live_draft(row)

    def _live_draft(self, row: sqlite3.Row | None) -> sqlite3.Row:
        if row is None:
            raise ServiceFailure("not_found")
        if row["deleted_at"] is not None:
            raise ServiceFailure("not_found")
        if row["expires_at"] <= self.now():
            raise ServiceFailure("expired", subject="draft")
        return row

    def _draft_in(self, con: sqlite3.Connection, principal_id: str, draft_id: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM drafts WHERE draft_id = ? AND principal_id = ?", (draft_id, principal_id)
        ).fetchone()
        return self._live_draft(row)

    def save_draft(
        self,
        principal_id: str,
        draft_id: str,
        expected: int,
        make_body: Callable[[int], str],
        days: int,
        *,
        generation: int,
    ) -> sqlite3.Row:
        """带版本保存：只有服务端当前修订号等于 ``expected`` 才写入（新修订号 = expected + 1）；否则冲突，不覆盖。旧修订的审阅当场
        作废（它们不能再用来确认）。"""
        now = self.now()
        with self.write() as con:
            self._check_generation(con, principal_id, generation)
            self._draft_in(con, principal_id, draft_id)
            cur = con.execute(
                "UPDATE drafts SET body = ?, revision = revision + 1, updated_at = ?, expires_at = ? "
                "WHERE draft_id = ? AND principal_id = ? AND revision = ? AND deleted_at IS NULL",
                (
                    make_body(expected + 1),
                    now,
                    days_after(now, days),
                    draft_id,
                    principal_id,
                    expected,
                ),
            )
            if cur.rowcount != 1:
                raise ServiceFailure("revision_conflict")
            con.execute(
                "UPDATE reviews SET body = NULL, display_body = NULL, deleted_at = ? WHERE draft_id = ? AND principal_id = ? "
                "AND draft_revision < ? AND deleted_at IS NULL",
                (now, draft_id, principal_id, expected + 1),
            )
            return con.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()

    def delete_draft(self, principal_id: str, draft_id: str) -> None:
        """删草稿：它的审阅与由它确认的画像一起清掉（不只删草稿、留下同一份答案的副本）。已经开始检查的运行用自己工作目录里的
        副本，不受影响。"""
        now = self.now()
        with self.write() as con:
            cur = con.execute(
                "UPDATE drafts SET body = NULL, deleted_at = ? WHERE draft_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (now, draft_id, principal_id),
            )
            if cur.rowcount != 1:
                raise ServiceFailure("not_found")
            for table in ("reviews", "profiles"):
                con.execute(
                    f"UPDATE {table} SET body = NULL, display_body = NULL, deleted_at = COALESCE(deleted_at, ?) "
                    "WHERE draft_id = ? AND principal_id = ?",
                    (now, draft_id, principal_id),
                )
        self.checkpoint()

    def save_review(
        self,
        principal_id: str,
        *,
        review_id: str,
        draft_id: str,
        revision: int,
        draft_fingerprint: str,
        pack_fingerprint: str,
        body: str,
        generation: int,
        display_body: str | None = None,
    ) -> None:
        """写回一份审阅。事务里重新核对：没有“删除全部”、草稿还在、没删、没过期、修订号还是审阅的那一版。"""
        with self.write() as con:
            self._check_generation(con, principal_id, generation)
            draft = self._draft_in(con, principal_id, draft_id)
            if draft["revision"] != revision:
                raise ServiceFailure("revision_conflict")
            con.execute(
                "INSERT INTO reviews (review_id, principal_id, draft_id, draft_revision, draft_fingerprint, "
                "pack_fingerprint, body, created_at, display_body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(review_id) DO UPDATE SET draft_revision = excluded.draft_revision, "
                "draft_fingerprint = excluded.draft_fingerprint, pack_fingerprint = excluded.pack_fingerprint, "
                "body = excluded.body, created_at = excluded.created_at, deleted_at = NULL, "
                "display_body = excluded.display_body "
                "WHERE reviews.principal_id = excluded.principal_id",
                (
                    review_id,
                    principal_id,
                    draft_id,
                    revision,
                    draft_fingerprint,
                    pack_fingerprint,
                    body,
                    self.now(),
                    display_body,
                ),
            )

    def review(self, principal_id: str, review_id: str) -> sqlite3.Row:
        """本人的一份审阅。草稿改了新修订之后旧审阅已经作废（正文清掉）：报“审阅过时”（``revision_conflict``），不当成别人的编号。"""
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM reviews WHERE review_id = ? AND principal_id = ?",
                (review_id, principal_id),
            ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        if row["deleted_at"] is not None:
            raise ServiceFailure("revision_conflict")
        return row

    def review_draft_id(self, principal_id: str, review_id: str) -> str | None:
        """本人一份审阅所属的草稿编号，审阅作废了也照样给（显示读取用它按草稿的状态回答删除与过期）。"""
        with self.read() as con:
            row = con.execute(
                "SELECT draft_id FROM reviews WHERE review_id = ? AND principal_id = ?",
                (review_id, principal_id),
            ).fetchone()
        return None if row is None else row["draft_id"]

    def save_profile(
        self,
        principal_id: str,
        *,
        confirmed_id: str,
        draft_id: str,
        revision: int,
        review_id: str,
        fingerprint: str,
        body: str,
        generation: int,
        display_body: str | None = None,
    ) -> sqlite3.Row:
        """写回一份确认画像。事务里重新核对代次、审阅与草稿（都还在、没删、没过期、是同一修订）。同一份画像重复确认原样返回，**不续期**；
        已经过期的不能靠再确认一次复活。返回画像这一行（含到期时间）。"""
        now = self.now()
        with self.write() as con:
            self._check_generation(con, principal_id, generation)
            draft = self._draft_in(con, principal_id, draft_id)
            rev = con.execute(
                "SELECT * FROM reviews WHERE review_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (review_id, principal_id),
            ).fetchone()
            if (
                rev is None
                or rev["draft_id"] != draft_id
                or rev["draft_revision"] != revision
                or draft["revision"] != revision
            ):
                raise ServiceFailure("revision_conflict")
            old = con.execute(
                "SELECT * FROM profiles WHERE confirmed_id = ?", (confirmed_id,)
            ).fetchone()
            if old is not None:
                if old["principal_id"] != principal_id:
                    raise ServiceFailure("not_found")
                if old["content_fingerprint"] == fingerprint and old["deleted_at"] is None:
                    if old["expires_at"] <= now:
                        raise ServiceFailure("expired", subject="profile")
                    return old  # 同一份确认重复提交：原样返回，不续期
                if old["content_fingerprint"] == fingerprint and old["expires_at"] <= now:
                    raise ServiceFailure("expired", subject="profile")
                con.execute("DELETE FROM profiles WHERE confirmed_id = ?", (confirmed_id,))
            con.execute(
                "INSERT INTO profiles (confirmed_id, principal_id, draft_id, draft_revision, review_id, "
                "content_fingerprint, body, created_at, expires_at, display_body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    confirmed_id,
                    principal_id,
                    draft_id,
                    revision,
                    review_id,
                    fingerprint,
                    body,
                    now,
                    days_after(now, self.profile_days),
                    display_body,
                ),
            )
            return con.execute(
                "SELECT * FROM profiles WHERE confirmed_id = ?", (confirmed_id,)
            ).fetchone()

    def profile(self, principal_id: str, confirmed_id: str) -> sqlite3.Row:
        """一份还能用来准备新模拟的确认画像：在、没删、没过期。"""
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM profiles WHERE confirmed_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (confirmed_id, principal_id),
            ).fetchone()
        if row is None or row["body"] is None:
            raise ServiceFailure("not_found")
        if row["expires_at"] <= self.now():
            raise ServiceFailure("expired", subject="profile")
        return row

    def profiles(self, principal_id: str) -> list[sqlite3.Row]:
        """本人还能用来准备新模拟的确认画像（在、没删、没过期），新的在前。只读，不续期。"""
        with self.read() as con:
            return list(
                con.execute(
                    "SELECT * FROM profiles WHERE principal_id = ? AND deleted_at IS NULL AND body IS NOT NULL "
                    "AND expires_at > ? ORDER BY created_at DESC, confirmed_id",
                    (principal_id, self.now()),
                )
            )

    # ------------------------------------------------------------------ 运行意图

    def create_intent(
        self,
        principal_id: str,
        *,
        confirmed_id: str,
        confirmed_fingerprint: str,
        execution_fingerprint: str,
        execution_body: str,
        run_options: str,
        workflow_key: Callable[[str], str],
        generation: int,
        start: StartRequest | None = None,
    ) -> tuple[sqlite3.Row, bool]:
        """创建运行意图（带这次的有效设置）并排队预检查。事务里核对代次与确认画像（在、没删、没过期、是同一份）。同一身份已经有尚未
        结束的意图：确认画像与有效设置都相同就返回原意图（``False``），不同就冲突（先取消原来的准备再重新创建）。

        ``start``（一次开始，DECISIONS/0048）：同一个键、同一份请求返回它当初对应的那个意图（哪怕已经结束、取消或删除：重试不另起
        运行），同一个键、不同请求是冲突；新的键遇到尚未结束的同一份准备，就把授权附在它上面（它已经有有效授权或已经开始，就只记下
        这个键）；授权保存在意图上，检查通过之后由 worker 按它开始。"""
        now = self.now()
        with self.write() as con:
            self._check_generation(con, principal_id, generation)
            if start is not None:
                seen = self._start_seen(con, principal_id, start)
                if seen is not None:
                    return seen, False
            prof = con.execute(
                "SELECT * FROM profiles WHERE confirmed_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (confirmed_id, principal_id),
            ).fetchone()
            if (
                prof is None
                or prof["body"] is None
                or prof["content_fingerprint"] != confirmed_fingerprint
            ):
                raise ServiceFailure("not_found")
            if prof["expires_at"] <= now:
                raise ServiceFailure("expired", subject="profile")
            old = con.execute(
                "SELECT * FROM intents WHERE principal_id = ? AND stage IN ({})".format(
                    ",".join("?" * len(OPEN_STAGES))
                ),
                (principal_id, *OPEN_STAGES),
            ).fetchone()
            if old is not None and intent_due(old, now, self.intent_days):
                self._expire_row(con, old, now)  # 已经到期的旧准备先按过期处理，不挡新的
                old = None
            if old is not None:
                same = (old["confirmed_id"], old["execution_fingerprint"]) == (
                    confirmed_id,
                    execution_fingerprint,
                )
                if not same:
                    raise ServiceFailure("intent_conflict")
                if start is not None:
                    old = self._authorize(con, old, start, now)
                return old, False
            if self.quota(principal_id, con).available < 1:
                raise ServiceFailure("quota_exhausted")
            intent = opaque("intent")
            con.execute(
                "INSERT INTO intents (intent_id, principal_id, confirmed_id, confirmed_fingerprint, "
                "execution_fingerprint, execution_body, run_options, workflow_key, stage, waiting_for, created_at, "
                "updated_at, last_activity_at, start_authorization) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'checking', 'service', ?, ?, ?, ?)",
                (
                    intent,
                    principal_id,
                    confirmed_id,
                    confirmed_fingerprint,
                    execution_fingerprint,
                    execution_body,
                    run_options,
                    workflow_key(intent),
                    now,
                    now,
                    now,
                    None if start is None else _authorization(start, principal_id, now),
                ),
            )
            if start is not None:
                self._remember_start(con, principal_id, intent, start, now)
            self._enqueue(con, "preflight", intent)
            row = con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent,)).fetchone()
            return row, True

    def _start_seen(
        self, con: sqlite3.Connection, principal_id: str, start: StartRequest
    ) -> sqlite3.Row | None:
        """这个一次开始的键已经对应过的意图：同一份请求就是它（重试），不同请求是冲突。"""
        seen = con.execute(
            "SELECT * FROM submissions WHERE key_digest = ?",
            (start_key_digest(principal_id, start.key),),
        ).fetchone()
        if seen is None:
            return None
        if seen["request_fingerprint"] != start.request_fingerprint:
            raise ServiceFailure("idempotency_conflict")
        return con.execute(
            "SELECT * FROM intents WHERE intent_id = ?", (seen["intent_id"],)
        ).fetchone()

    def intent_for_start(self, principal_id: str, start: StartRequest) -> sqlite3.Row | None:
        """只读：这个一次开始的键已经对应过的意图（重试时在任何别的核对之前找回它；同一个键、不同请求是冲突）。"""
        with self.read() as con:
            return self._start_seen(con, principal_id, start)

    def _remember_start(
        self,
        con: sqlite3.Connection,
        principal_id: str,
        intent_id: str,
        start: StartRequest,
        now: str,
    ) -> None:
        con.execute(
            "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
            (
                start_key_digest(principal_id, start.key),
                principal_id,
                intent_id,
                start.request_fingerprint,
                now,
            ),
        )

    def _authorize(
        self, con: sqlite3.Connection, row: sqlite3.Row, start: StartRequest, now: str
    ) -> sqlite3.Row:
        """把一次开始的授权附在尚未结束的同一份准备上。已经有有效授权、已经开始，或检查没有通过：只记下这个键（重试找回同一个
        意图），授权不变——检查没通过的要先取消再重新准备，不替用户接受。检查已经通过、正等确认的：附上授权并排队，worker 接着
        按它开始。"""
        intent_id = row["intent_id"]
        self._remember_start(con, row["principal_id"], intent_id, start, now)
        auth = start_authorization(row)
        open_auth = auth is not None and auth["state"] in ("pending", "used")
        if (
            open_auth
            or row["run_id"] is not None
            or row["stage"] not in ("checking", "awaiting_confirmation")
        ):
            return row
        self.touch(
            con,
            intent_id,
            start_authorization=_authorization(start, row["principal_id"], now),
            last_activity_at=now,
        )
        if row["stage"] == "awaiting_confirmation":
            self._enqueue(con, "preflight", intent_id)
        return con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()

    def void_start(self, intent_id: str, reason: str) -> bool:
        """开始授权作废（条件变了、检查没有通过、付费调用关了、次数不够……）：只对还没用的授权，写下原因（给用户的说明在读取时按
        原因生成）。授权作废后不会再自动开始，要用户重新确认（新的一次开始）。返回是否真的作废了一份授权。"""
        now = self.now()
        with self.write() as con:
            row = con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
            auth = None if row is None else start_authorization(row)
            if auth is None or auth["state"] != "pending":
                return False
            void = {**auth, "state": "void", "void_reason": reason, "voided_at": now}
            self.touch(con, intent_id, start_authorization=json.dumps(void, sort_keys=True))
            return True

    def requeue_pending_starts(self) -> list[str]:
        """worker 启动时的兜底：检查已经通过、开始授权还没用、却没有待办检查任务的意图重新排队（上一个进程在检查与自动开始之间
        出了问题）。只排一个任务，开始仍由 worker 按授权与当时的条件核对；返回排队的意图。"""
        out: list[str] = []
        with self.write() as con:
            rows = con.execute(
                "SELECT * FROM intents WHERE stage = 'awaiting_confirmation' AND run_id IS NULL "
                "AND start_authorization IS NOT NULL"
            ).fetchall()
            for row in rows:
                auth = start_authorization(row)
                if auth is None or auth["state"] != "pending":
                    continue
                busy = con.execute(
                    "SELECT 1 FROM jobs WHERE intent_id = ? AND kind = 'preflight' AND state IN ('pending', 'active')",
                    (row["intent_id"],),
                ).fetchone()
                if busy is None:
                    self._enqueue(con, "preflight", row["intent_id"])
                    out.append(row["intent_id"])
        return out

    def intent(self, principal_id: str, intent_id: str) -> sqlite3.Row:
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE intent_id = ? AND principal_id = ?",
                (intent_id, principal_id),
            ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        return row

    def intent_by_run(self, principal_id: str, run_id: str) -> sqlite3.Row:
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE run_id = ? AND principal_id = ?",
                (run_id, principal_id),
            ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        return row

    def intents(self, principal_id: str) -> list[sqlite3.Row]:
        with self.read() as con:
            return list(
                con.execute(
                    "SELECT * FROM intents WHERE principal_id = ? ORDER BY created_at DESC, intent_id",
                    (principal_id,),
                )
            )

    def open_intent(self, principal_id: str) -> str | None:
        """尚未结束、也没有到期的那一个意图（换设备后从这里接着走）。只读。"""
        now = self.now()
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE principal_id = ? AND stage IN ({})".format(
                    ",".join("?" * len(OPEN_STAGES))
                ),
                (principal_id, *OPEN_STAGES),
            ).fetchone()
        if row is None or intent_due(row, now, self.intent_days):
            return None
        return row["intent_id"]

    def drafts(self, principal_id: str) -> list[sqlite3.Row]:
        """本人还在保存期内的草稿（不含正文），新的在前。"""
        with self.read() as con:
            return list(
                con.execute(
                    "SELECT draft_id, revision, updated_at, expires_at FROM drafts "
                    "WHERE principal_id = ? AND deleted_at IS NULL AND expires_at > ? "
                    "ORDER BY updated_at DESC, draft_id",
                    (principal_id, self.now()),
                )
            )

    def intent_row(self, intent_id: str) -> sqlite3.Row | None:
        """worker 用（不按身份过滤）。"""
        with self.read() as con:
            return con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()

    def touch(self, con: sqlite3.Connection, intent_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        con.execute(
            f"UPDATE intents SET {cols}, updated_at = ? WHERE intent_id = ?",
            (*fields.values(), self.now(), intent_id),
        )

    def _owned(
        self, con: sqlite3.Connection, principal_id: str, column: str, value: str
    ) -> sqlite3.Row:
        row = con.execute(
            f"SELECT * FROM intents WHERE {column} = ? AND principal_id = ?", (value, principal_id)
        ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        return row

    def ensure_not_due(self, principal_id: str, intent_id: str) -> None:
        """写操作开头用：意图已经到期就按过期处理（在一个写事务里）并拒绝；没到期什么也不改。"""
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            self._refuse_if_due(con, row)

    def _refuse_if_due(self, con: sqlite3.Connection, row: sqlite3.Row) -> None:
        """写入之前先核对截止时间：已经到期就在这个事务里按过期处理（退回没用的预留、取消待执行的任务、清掉内容），**提交**之后
        再拒绝（``expired``）——不能让拒绝把过期处理回滚，留下一个页面显示过期、库里仍在排队的运行（E3C 收尾复核 R3）。调用方都在
        任何其他写入之前调用它。"""
        now = self.now()
        if intent_due(row, now, self.intent_days):
            self._expire_row(con, row, now)
            raise _CommitThenRaise(ServiceFailure("expired", subject=expired_subject(row)))

    def submit_intent(
        self,
        principal_id: str,
        intent_id: str,
        *,
        confirm_with: str,
        key: str,
        run_id: str,
        execution_fingerprint: str,
        authorization: str | None = None,
    ) -> tuple[str, str]:
        """确认开始：一个短事务里先核对截止时间，再核对意图、有效设置与幂等键、预留 1 次、写下稳定运行编号、排队。返回（回执状态，
        运行编号）。

        * ``authorization``：worker 按一次开始的授权开始时给出那份授权的同意身份——授权必须还没用、就是这一份，在同一个事务里标为
          已用；用户自己确认开始（没有它）时，带着开始授权的意图拒绝（``start_already_authorized``：不需要、也不接受第二次确认）；

        * 同一个键、同一份请求（同一份预检查、同一份有效设置）→ ``duplicate_same_run``；同一个键、不同请求 → 冲突；
        * 同一意图已经确认过：即使换了键，也返回原运行（同一份预检查）——一次确认只能产生一次运行；
        * 已经到期 → ``expired``（不刷新业务时间、不预留）；确认的不是当前预检查或设置 → ``preflight_stale``；次数不够 → ``quota_exhausted``。"""
        request_fp = digest(
            json.dumps(
                {
                    "intent": intent_id,
                    "confirm_with": confirm_with,
                    "execution": execution_fingerprint,
                },
                sort_keys=True,
            )
        )
        key_digest = digest(f"{principal_id}|{key}")
        now = self.now()
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            seen = con.execute(
                "SELECT * FROM submissions WHERE key_digest = ?", (key_digest,)
            ).fetchone()
            if seen is not None and (seen["intent_id"], seen["request_fingerprint"]) != (
                intent_id,
                request_fp,
            ):
                raise ServiceFailure("idempotency_conflict")
            if row["stage"] in GONE_STAGES:
                raise _gone(row)
            self._refuse_if_due(con, row)
            auth = start_authorization(row)
            if authorization is None and auth is not None:
                raise ServiceFailure("start_already_authorized")
            if authorization is not None and (
                auth is None
                or auth["state"] != "pending"
                or auth["consent_fingerprint"] != authorization
            ):
                raise ServiceFailure("preflight_stale")
            if row["run_id"] is not None:
                # 已经确认过：同一份预检查与设置返回原运行（不管键是不是新的）；换了就是旧确认
                if (
                    row["preflight_fingerprint"] != confirm_with
                    or row["execution_fingerprint"] != execution_fingerprint
                ):
                    raise ServiceFailure("preflight_stale")
                if seen is None:
                    con.execute(
                        "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
                        (key_digest, principal_id, intent_id, request_fp, now),
                    )
                return "duplicate_same_run", row["run_id"]
            if (
                row["stage"] != "awaiting_confirmation"
                or row["preflight_fingerprint"] != confirm_with
                or row["execution_fingerprint"] != execution_fingerprint
            ):
                raise ServiceFailure("preflight_stale")
            if self.quota(principal_id, con).available < 1:
                raise ServiceFailure("quota_exhausted")
            con.execute(
                "INSERT INTO quota_events (principal_id, intent_id, kind, at, note) VALUES (?, ?, 'reserve', ?, '确认开始')",
                (principal_id, intent_id, now),
            )
            con.execute(
                "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
                (key_digest, principal_id, intent_id, request_fp, now),
            )
            used: dict[str, Any] = {}
            if auth is not None:
                used["start_authorization"] = json.dumps(
                    {**auth, "state": "used", "used_at": now}, sort_keys=True
                )
            self.touch(
                con,
                intent_id,
                stage="queued",
                waiting_for="service",
                run_id=run_id,
                last_activity_at=now,
                explanation=None,
                **used,
            )
            self._enqueue(con, "run", intent_id)
            return "accepted", run_id

    def recheck_intent(
        self, principal_id: str, intent_id: str, *, execution_fingerprint: str, execution_body: str
    ) -> None:
        """服务的资料包或规划器设定在用户确认开始之前变了：用同一份运行选项重新算出的有效设置替换旧的，回到“排队检查”重新做
        运行前检查（不扣次数）；用户要看到新的运行前说明再确认。只对还没有确认开始的意图。"""
        now = self.now()
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            self._refuse_if_due(con, row)
            if row["stage"] != "awaiting_confirmation" or row["run_id"] is not None:
                raise ServiceFailure("preflight_stale")
            self.touch(
                con,
                intent_id,
                stage="checking",
                waiting_for="service",
                execution_fingerprint=execution_fingerprint,
                execution_body=execution_body,
                preflight_fingerprint=None,
                preflight_body=None,
                explanation=None,
                last_activity_at=now,
            )
            self._enqueue(con, "preflight", intent_id)

    def block_intent(self, principal_id: str, intent_id: str, explanation: str) -> None:
        """还没有确认开始的意图不能再按原来的选项准备（例如维护者收窄了允许范围）：标为“检查没有通过”，等用户取消后重新准备。"""
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            self._refuse_if_due(con, row)
            if row["stage"] != "awaiting_confirmation" or row["run_id"] is not None:
                return
            self.touch(
                con,
                intent_id,
                stage="blocked",
                waiting_for="user",
                preflight_fingerprint=None,
                explanation=explanation,
            )

    def cancel_intent(self, principal_id: str, intent_id: str) -> sqlite3.Row:
        """开始前取消：检查中、等确认、阻断、排队（还没有启动）的意图可以取消；排队的释放预留。已经开始过的不能取消
        （``cancel_not_allowed``，包括恢复之后又在排队的；可以删除）。已经到期的按过期处理后拒绝。"""
        now = self.now()
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            if row["stage"] == "cancelled":
                return row
            if row["stage"] in GONE_STAGES:
                raise _gone(row)
            self._refuse_if_due(con, row)
            if row["stage"] not in ("checking", "awaiting_confirmation", "blocked", "queued"):
                raise ServiceFailure("cancel_not_allowed")
            if self._consumed(con, intent_id):
                # 恢复排队的运行也是 queued：是否已经开始看次数记录（首次启动时预留转为已使用），不看阶段；不退次数、不清进度
                raise ServiceFailure("cancel_not_allowed")
            self._release(con, intent_id, "开始前取消")
            con.execute(
                "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state = 'pending'",
                (now, intent_id),
            )
            fields: dict[str, Any] = {c: None for c in _CANCEL_CONTENT}
            auth = start_authorization(row)
            if auth is not None and auth["state"] == "pending":
                fields["start_authorization"] = json.dumps(
                    {**auth, "state": "void", "void_reason": "cancelled", "voided_at": now},
                    sort_keys=True,
                )
            self.touch(
                con,
                intent_id,
                stage="cancelled",
                waiting_for="none",
                stop_requested=1,
                last_activity_at=now,
                **fields,
            )
            return con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()

    def request_resume(self, principal_id: str, run_id: str) -> sqlite3.Row:
        """用户点“恢复”：只在服务端判定能继续（``resume_allowed``）时排队；已经到期的按过期处理后拒绝，不新增发送或扣次。"""
        with self.write() as con:
            row = self._owned(con, principal_id, "run_id", run_id)
            if row["stage"] in GONE_STAGES:
                raise _gone(row)
            self._refuse_if_due(con, row)
            if row["stage"] != "needs_attention" or not row["resume_allowed"]:
                raise ServiceFailure("not_resumable")
            self.touch(
                con,
                row["intent_id"],
                stage="queued",
                waiting_for="service",
                resume_allowed=0,
                last_activity_at=self.now(),
            )
            self._enqueue(con, "resume", row["intent_id"])
            return con.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (row["intent_id"],)
            ).fetchone()

    # ------------------------------------------------------------------ 删除

    def request_delete(self, principal_id: str, intent_id: str) -> sqlite3.Row:
        """删除一次运行：立即不可查询（``deleting``）、停止批准新的模型请求；还没有启动的释放预留（开始前取消）；已经开始的不退。"""
        now = self.now()
        with self.write() as con:
            row = self._owned(con, principal_id, "intent_id", intent_id)
            if row["stage"] in ("deleting", "deleted"):
                return row
            self._release(con, intent_id, "开始前删除")
            con.execute(
                "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state = 'pending'",
                (now, intent_id),
            )
            self.touch(
                con,
                intent_id,
                stage="deleting",
                waiting_for="service",
                stop_requested=1,
                delete_requested_at=now,
                resume_allowed=0,
            )
            self._enqueue(con, "delete", intent_id)
            return con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()

    def delete_all(self, principal_id: str) -> list[str]:
        """删除本人全部业务内容：草稿、审阅、确认与全部运行；保留次数事件与最小墓碑。数据代次加一：之前已经开始、还没有写回的审阅、
        确认、保存与创建全部失效。返回进入删除的意图。"""
        now = self.now()
        with self.write() as con:
            con.execute(
                "UPDATE principals SET data_generation = data_generation + 1 WHERE principal_id = ?",
                (principal_id,),
            )
            con.execute(
                "UPDATE drafts SET body = NULL, deleted_at = COALESCE(deleted_at, ?) WHERE principal_id = ?",
                (now, principal_id),
            )
            for table in ("reviews", "profiles"):
                con.execute(
                    f"UPDATE {table} SET body = NULL, display_body = NULL, deleted_at = COALESCE(deleted_at, ?) WHERE principal_id = ?",
                    (now, principal_id),
                )
            ids = [
                r[0]
                for r in con.execute(
                    "SELECT intent_id FROM intents WHERE principal_id = ? AND stage NOT IN ('deleting', 'deleted')",
                    (principal_id,),
                )
            ]
        for intent in ids:
            self.request_delete(principal_id, intent)
        self.checkpoint()
        return ids

    def purge_intent(
        self, intent_id: str, *, stage: str, cost_summary: dict[str, Any] | None
    ) -> None:
        """清掉一个意图的个人内容，只留最小记录（不透明身份、阶段、时间、费用摘要、防重记录）。"""
        now = self.now()
        with self.write() as con:
            fields: dict[str, Any] = {c: None for c in INTENT_CONTENT}
            fields.update(stage=stage, waiting_for="none", stop_requested=1, resume_allowed=0)
            if cost_summary is not None:  # 没有新的摘要时保留已经记下的那一份（不清掉、不改成 0）
                fields["cost_summary"] = json.dumps(cost_summary, ensure_ascii=False)
            if stage == "deleted":
                fields["deleted_at"] = now
            self.touch(con, intent_id, **fields)
            con.execute("DELETE FROM send_permits WHERE intent_id = ?", (intent_id,))
            con.execute(
                "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state = 'pending' AND kind != 'delete'",
                (now, intent_id),
            )
        self.checkpoint()

    # ------------------------------------------------------------------ 发送许可与停止

    def stop_requested(self, intent_id: str) -> bool:
        with self.read() as con:
            row = con.execute(
                "SELECT stage, stop_requested FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return row is None or bool(row["stop_requested"]) or row["stage"] in GONE_STAGES

    def permit_send(self, intent_id: str, permit_key: str) -> bool:
        """一次服务尝试的发送许可（包括重试）。与删除标记在同一个库里串行裁决：删除之后不再批准；之前批准的算在途。"""
        with self.write() as con:
            row = con.execute(
                "SELECT stage, stop_requested FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None or row["stop_requested"] or row["stage"] in GONE_STAGES:
                return False
            con.execute(
                "INSERT OR IGNORE INTO send_permits (permit_key, intent_id, granted_at) VALUES (?, ?, ?)",
                (permit_key, intent_id, self.now()),
            )
            return True

    # ------------------------------------------------------------------ 任务队列（worker）

    def _enqueue(self, con: sqlite3.Connection, kind: str, intent_id: str) -> None:
        con.execute(
            "INSERT INTO jobs (kind, intent_id, state, created_at) VALUES (?, ?, 'pending', ?)",
            (kind, intent_id, self.now()),
        )

    def enqueue(self, kind: str, intent_id: str) -> None:
        with self.write() as con:
            self._enqueue(con, kind, intent_id)

    def take_job(self) -> sqlite3.Row | None:
        """最早的待办任务，标为进行中。删除任务优先（让删除尽快收尾）。

        领取时在同一个事务里核对期限（E3C 收尾复核 R3）：任务的意图已经到期，就按过期处理（预留只退一次、取消它的任务、清掉正文）
        并提交，不交给 worker，接着看下一个——不靠定时维护刚好先跑。领取的那一刻没有人在执行它，所以按“没有执行者”的规则判断。"""
        now = self.now()
        expired = False
        with self.write() as con:
            while True:
                row = con.execute(
                    "SELECT * FROM jobs WHERE state = 'pending' ORDER BY kind != 'delete', job_id LIMIT 1"
                ).fetchone()
                if row is None:
                    job = None
                    break
                intent = con.execute(
                    "SELECT * FROM intents WHERE intent_id = ?", (row["intent_id"],)
                ).fetchone()
                if intent is not None and intent_due(intent, now, self.intent_days, running=True):
                    self._expire_row(con, intent, now)  # 也取消了这个任务
                    expired = True
                    continue
                con.execute(
                    "UPDATE jobs SET state = 'active', started_at = ? WHERE job_id = ?",
                    (now, row["job_id"]),
                )
                job = con.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
                ).fetchone()
                break
        if expired:
            self.checkpoint()
        return job

    def finish_job(self, job_id: int, note: str = "") -> None:
        with self.write() as con:
            con.execute(
                "UPDATE jobs SET state = 'done', finished_at = ?, note = ? WHERE job_id = ?",
                (self.now(), note[:200], job_id),
            )

    def active_jobs(self) -> list[sqlite3.Row]:
        with self.read() as con:
            return list(con.execute("SELECT * FROM jobs WHERE state = 'active' ORDER BY job_id"))

    def pending_jobs(self) -> list[sqlite3.Row]:
        with self.read() as con:
            return list(
                con.execute(
                    "SELECT * FROM jobs WHERE state IN ('pending', 'active') ORDER BY job_id"
                )
            )

    def requeue_active(self) -> list[sqlite3.Row]:
        """worker 启动时（过期已经先处理过）：上一个进程留下的进行中任务退回待办（恢复由原流程与 E1 的核对负责，不重置任何计数）。"""
        with self.write() as con:
            rows = list(con.execute("SELECT * FROM jobs WHERE state = 'active'"))
            con.execute("UPDATE jobs SET state = 'pending' WHERE state = 'active'")
            return rows

    def cleanup_candidates(self) -> list[sqlite3.Row]:
        """worker 的维护线程用：不再需要工作目录的意图（取消、过期、删除中、已删除）。"""
        with self.read() as con:
            return list(
                con.execute(
                    "SELECT intent_id, stage, cost_summary FROM intents "
                    "WHERE stage IN ('cancelled', 'expired', 'deleting', 'deleted') ORDER BY intent_id"
                )
            )

    def save_cost_summary(self, intent_id: str, summary: dict[str, Any]) -> None:
        """删除或过期清理之前先记下最小费用摘要（中途崩溃也不丢；不确定的金额照原样）。"""
        with self.write() as con:
            con.execute(
                "UPDATE intents SET cost_summary = ? WHERE intent_id = ?",
                (json.dumps(summary, ensure_ascii=False), intent_id),
            )

    def profile_body(self, confirmed_id: str) -> str | None:
        """worker 用：确认画像正文（不按身份过滤；已删除的返回 None）。正在检查的意图在画像到期之前就创建了，所以这里不看到期时间。"""
        with self.read() as con:
            row = con.execute(
                "SELECT body FROM profiles WHERE confirmed_id = ? AND deleted_at IS NULL",
                (confirmed_id,),
            ).fetchone()
        return None if row is None else row[0]

    def consumed(self, intent_id: str) -> bool:
        """这个意图的次数是否已经转为已使用（E1 已经启动过）。"""
        return self.quota_state(intent_id)["used"]

    def register_run(
        self, intent_id: str, *, key: str, run_id: str, preflight_fingerprint: str
    ) -> bool:
        """LangGraph 的提交节点登记运行（服务端 SQLite 是唯一的权威登记）：键、运行编号与预检查身份必须就是确认开始时写下的那一份。
        对不上返回 False（调用方停在运行绑定冲突，不改记录）；重复登记同一份是幂等的。"""
        with self.write() as con:
            row = con.execute(
                "SELECT workflow_key, run_id, preflight_fingerprint, registered_at, stage FROM intents "
                "WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None or (
                row["workflow_key"],
                row["run_id"],
                row["preflight_fingerprint"],
            ) != (
                key,
                run_id,
                preflight_fingerprint,
            ):
                return False
            if row["stage"] in GONE_STAGES:
                return False
            if row["registered_at"] is None:
                self.touch(con, intent_id, registered_at=self.now())
            return True

    def mark_started(self, intent_id: str) -> bool:
        """worker 开始执行运行任务：排队 → 运行中。已经取消、删除、过期的不开始。期限与状态转换在同一个事务里：已经到期就按过期
        处理（预留只退一次、取消任务）并提交，不开始（E3C 收尾复核 R3）。"""
        now = self.now()
        with self.write() as con:
            row = con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
            if row is None or row["stage"] != "queued":
                return False
            due = intent_due(row, now, self.intent_days)
            if due:
                self._expire_row(con, row, now)
            else:
                self.touch(con, intent_id, stage="running", waiting_for="service")
        if due:
            self.checkpoint()
        return not due

    # ------------------------------------------------------------------ worker 写回的公开状态

    def update_intent(self, intent_id: str, *, only_stages: tuple[str, ...], **fields: Any) -> bool:
        """只在意图还处于 ``only_stages`` 时写回（删除、取消与过期优先：被删的意图不会被监测线程或完成写回复活）。"""
        with self.write() as con:
            row = con.execute(
                "SELECT stage FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None or row["stage"] not in only_stages:
                return False
            self.touch(con, intent_id, **fields)
            return True

    def finish_intent(
        self,
        intent_id: str,
        *,
        result_body: str,
        result_fingerprint: str,
        days: int,
        result_display: str | None = None,
    ) -> bool:
        """交付结果：只有仍在运行中的意图才发布（删除优先于对外发布）。保存期从首次结束起算，之后再完成也不续期。"""
        now = self.now()
        with self.write() as con:
            row = con.execute(
                "SELECT stage, first_finished_at FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None or row["stage"] != "running":
                return False
            first = row["first_finished_at"] or now
            self.touch(
                con,
                intent_id,
                stage="finished",
                waiting_for="none",
                result_body=result_body,
                result_fingerprint=result_fingerprint,
                result_display=result_display,
                first_finished_at=first,
                result_expires_at=days_after(first, days),
                resume_allowed=0,
            )
            return True

    # ------------------------------------------------------------------ 过期

    def _expire_row(self, con: sqlite3.Connection, row: sqlite3.Row, now: str) -> None:
        """在当前写事务里把一个到期的意图按过期处理：退回还没用的预留（只退一次）、清掉内容、不再批准发送、取消未执行的任务。"""
        intent_id = row["intent_id"]
        self._release(con, intent_id, "过期")
        fields: dict[str, Any] = {c: None for c in INTENT_CONTENT}
        self.touch(
            con,
            intent_id,
            stage="expired",
            waiting_for="none",
            stop_requested=1,
            resume_allowed=0,
            **fields,
        )
        con.execute(
            "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state IN ('pending', 'active')",
            (now, intent_id),
        )

    def expire_due(self, *, active: str | None = None) -> list[str]:
        """把到期的意图、草稿、审阅与确认画像标为过期（正文清掉），返回过期的意图编号（文件由 worker 清理）。查看、轮询与心跳不续期。

        * 运行意图：结果按首次结束起算，其他按最后一次有效业务操作；“运行中”的只要不是当前正在执行的那一个（``active``）就是上一个
          进程留下、没有执行者的记录，同样按这条规则处理——不把库里的 running 字样当作还有执行者的证明；
        * 草稿按最后一次编辑；它的审阅一起清掉；
        * 确认画像按首次确认起 ``profile_days`` 天；还有“检查中”的意图要读它时先留着（检查把它复制进运行自己的工作目录之后再清）。"""
        now = self.now()
        out: list[str] = []
        with self.write() as con:
            rows = list(
                con.execute(
                    "SELECT * FROM intents WHERE stage NOT IN ('deleted', 'expired', 'deleting')"
                )
            )
            for r in rows:
                if r["intent_id"] == active:
                    continue
                if not intent_due(r, now, self.intent_days, running=True):
                    continue
                self._expire_row(con, r, now)
                out.append(r["intent_id"])
            gone = [
                (x[0], x[1])
                for x in con.execute(
                    "SELECT draft_id, principal_id FROM drafts WHERE deleted_at IS NULL AND expires_at <= ?",
                    (now,),
                )
            ]
            for draft_id, principal_id in gone:
                con.execute(
                    "UPDATE drafts SET body = NULL, deleted_at = ? WHERE draft_id = ?",
                    (now, draft_id),
                )
                con.execute(
                    "UPDATE reviews SET body = NULL, display_body = NULL, deleted_at = ? WHERE draft_id = ? AND principal_id = ? "
                    "AND deleted_at IS NULL",
                    (now, draft_id, principal_id),
                )
            con.execute(
                "UPDATE profiles SET body = NULL, display_body = NULL, deleted_at = ? WHERE deleted_at IS NULL AND expires_at <= ? "
                "AND NOT EXISTS (SELECT 1 FROM intents i WHERE i.confirmed_id = profiles.confirmed_id "
                "AND i.stage = 'checking')",
                (now, now),
            )
        if out or gone:
            self.checkpoint()
        return out
