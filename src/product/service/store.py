"""服务端 SQLite：托管服务的权威记录（DECISIONS/0043）。

* 邀请与会话只存秘密的 SHA-256 摘要；重发链接沿用原身份与次数，旧链接与旧会话一起失效。
* 次数以不可重复的事件记账（``quota_events``）：预留、转为已使用、释放各一次（同一意图唯一），管理员修正也追加记录，不改历史。
* “接受确认、预留次数、保存稳定运行编号、创建队列任务”在一个 ``BEGIN IMMEDIATE`` 短事务里完成；事务里不做任何网络或文件操作。
* 发送许可与删除标记在同一个库里串行裁决：删除之前已经拿到许可的服务尝试算在途，删除之后不再批准。
* 删除只留最小记录（不透明身份、次数事件、必要的费用摘要、删除时间与防重记录）；个人正文从表里清掉，``secure_delete`` 打开，
  删除后做 WAL 检查点。这不是磁盘级的安全擦除。

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

STORE_VERSION = "e3c-service-store-v1"
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
    revoked_at TEXT
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
    deleted_at TEXT
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
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    confirmed_id TEXT NOT NULL,
    confirmed_fingerprint TEXT NOT NULL,
    execution_fingerprint TEXT NOT NULL,
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
    deleted_at TEXT
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

#: 删除时从意图上清掉的个人内容（预检查与结果里有答案与生活故事）
INTENT_CONTENT = (
    "preflight_body",
    "progress_body",
    "result_body",
    "explanation",
    "snapshot",
)


def intent_due(row: Any, now: str, intent_days: int) -> bool:
    """一个意图是否已经到期（API 访问时与 worker 清理时用同一条规则）：有结果的按首次结束起 30 天，其他按最后一次有效业务操作
    起 30 天；运行中、删除中与已经结束的不在这里到期。查看、轮询与心跳都不改这两个时间。"""
    if row["stage"] in ("running", "deleting", "deleted", "expired"):
        return False
    if row["result_expires_at"] is not None:
        return row["result_expires_at"] <= now
    return days_after(row["last_activity_at"], intent_days) <= now


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


class ServiceStore:
    def __init__(self, path: Path | str, clock: Clock, *, runs_total: int = 3) -> None:
        self.path = Path(path)
        self.clock = clock
        self.runs_total = runs_total
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('store_version', ?)",
                (STORE_VERSION,),
            )
            got = con.execute("SELECT value FROM meta WHERE key = 'store_version'").fetchone()
            if got[0] != STORE_VERSION:
                raise RuntimeError(f"服务库版本 {got[0]} 与代码 {STORE_VERSION} 不同")
        finally:
            con.close()

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
        """短写事务（``BEGIN IMMEDIATE``）：开头就拿写锁，并发提交排队，不会读到一半被别人改。事务里不做网络或文件操作。"""
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

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

    def session(self, secret: str) -> tuple[str, str] | None:
        """有效会话的（身份，到期时间）；到期、撤销、身份撤销都是 None。"""
        now = self.now()
        with self.read() as con:
            row = con.execute(
                "SELECT s.principal_id, s.expires_at FROM sessions s JOIN principals p USING (principal_id) "
                "WHERE s.session_digest = ? AND s.revoked_at IS NULL AND p.revoked_at IS NULL AND s.expires_at > ?",
                (digest(secret), now),
            ).fetchone()
        return None if row is None else (row[0], row[1])

    def revoke_session(self, secret: str) -> None:
        with self.write() as con:
            con.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_digest = ? AND revoked_at IS NULL",
                (self.now(), digest(secret)),
            )

    # ------------------------------------------------------------------ 次数

    def quota(self, principal_id: str, con: sqlite3.Connection | None = None) -> Quota:
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
            return Quota(
                total=self.runs_total,
                used=max(0, used),
                reserved=max(0, reserve - consume - release),
            )

        if con is not None:
            return count(con)
        with self.read() as c:
            return count(c)

    def consume(self, intent_id: str, note: str = "") -> bool:
        """预留转为已使用（准备首次启动 E1 时）。重复调用不重复扣；没有预留或已经释放就拒绝。"""
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
            principal = con.execute(
                "SELECT principal_id FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()[0]
            con.execute(
                "INSERT INTO quota_events (principal_id, intent_id, kind, at, note) VALUES (?, ?, 'consume', ?, ?)",
                (principal, intent_id, self.now(), note[:200]),
            )
            return True

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
        """释放预留（开始前取消、启动前检查失败）。已经开始的不退。"""
        with self.write() as con:
            return self._release(con, intent_id, note)

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
        self, principal_id: str, make_body: Callable[[str], str], days: int
    ) -> sqlite3.Row:
        draft_id = opaque("draft")
        now = self.now()
        with self.write() as con:
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
            raise ServiceFailure("expired")
        return row

    def save_draft(
        self,
        principal_id: str,
        draft_id: str,
        expected: int,
        make_body: Callable[[int], str],
        days: int,
    ) -> sqlite3.Row:
        """带版本保存：只有服务端当前修订号等于 ``expected`` 才写入（新修订号 = expected + 1）；否则冲突，不覆盖。"""
        now = self.now()
        with self.write() as con:
            row = con.execute(
                "SELECT * FROM drafts WHERE draft_id = ? AND principal_id = ?",
                (draft_id, principal_id),
            ).fetchone()
            self._live_draft(row)
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
            return con.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()

    def delete_draft(self, principal_id: str, draft_id: str) -> None:
        with self.write() as con:
            cur = con.execute(
                "UPDATE drafts SET body = NULL, deleted_at = ? WHERE draft_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (self.now(), draft_id, principal_id),
            )
            if cur.rowcount != 1:
                raise ServiceFailure("not_found")
            con.execute(
                "UPDATE reviews SET body = NULL, deleted_at = ? WHERE draft_id = ? AND principal_id = ?",
                (self.now(), draft_id, principal_id),
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
    ) -> None:
        with self.write() as con:
            con.execute(
                "INSERT INTO reviews (review_id, principal_id, draft_id, draft_revision, draft_fingerprint, "
                "pack_fingerprint, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(review_id) DO UPDATE SET draft_revision = excluded.draft_revision, "
                "draft_fingerprint = excluded.draft_fingerprint, pack_fingerprint = excluded.pack_fingerprint, "
                "body = excluded.body, created_at = excluded.created_at, deleted_at = NULL "
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
                ),
            )

    def review(self, principal_id: str, review_id: str) -> sqlite3.Row:
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM reviews WHERE review_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (review_id, principal_id),
            ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        return row

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
    ) -> None:
        with self.write() as con:
            old = con.execute(
                "SELECT principal_id, content_fingerprint, deleted_at FROM profiles WHERE confirmed_id = ?",
                (confirmed_id,),
            ).fetchone()
            if old is not None:
                if old["principal_id"] != principal_id:
                    raise ServiceFailure("not_found")
                if old["content_fingerprint"] == fingerprint and old["deleted_at"] is None:
                    return  # 同一份确认重复提交：原样返回
                con.execute("DELETE FROM profiles WHERE confirmed_id = ?", (confirmed_id,))
            con.execute(
                "INSERT INTO profiles (confirmed_id, principal_id, draft_id, draft_revision, review_id, "
                "content_fingerprint, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    confirmed_id,
                    principal_id,
                    draft_id,
                    revision,
                    review_id,
                    fingerprint,
                    body,
                    self.now(),
                ),
            )

    def profile(self, principal_id: str, confirmed_id: str) -> sqlite3.Row:
        with self.read() as con:
            row = con.execute(
                "SELECT * FROM profiles WHERE confirmed_id = ? AND principal_id = ? AND deleted_at IS NULL",
                (confirmed_id, principal_id),
            ).fetchone()
        if row is None:
            raise ServiceFailure("not_found")
        return row

    # ------------------------------------------------------------------ 运行意图

    def create_intent(
        self,
        principal_id: str,
        *,
        confirmed_id: str,
        confirmed_fingerprint: str,
        execution_fingerprint: str,
        workflow_key: Callable[[str], str],
    ) -> tuple[sqlite3.Row, bool]:
        """创建运行意图并排队预检查。同一身份已经有尚未结束的意图：条件相同就返回原意图（``False``），不同就冲突。"""
        now = self.now()
        with self.write() as con:
            old = con.execute(
                "SELECT * FROM intents WHERE principal_id = ? AND stage IN ({})".format(
                    ",".join("?" * len(OPEN_STAGES))
                ),
                (principal_id, *OPEN_STAGES),
            ).fetchone()
            if old is not None:
                same = (old["confirmed_id"], old["execution_fingerprint"]) == (
                    confirmed_id,
                    execution_fingerprint,
                )
                if not same:
                    raise ServiceFailure("intent_conflict")
                return old, False
            if self.quota(principal_id, con).available < 1:
                raise ServiceFailure("quota_exhausted")
            intent = opaque("intent")
            con.execute(
                "INSERT INTO intents (intent_id, principal_id, confirmed_id, confirmed_fingerprint, "
                "execution_fingerprint, workflow_key, stage, waiting_for, created_at, updated_at, last_activity_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'checking', 'service', ?, ?, ?)",
                (
                    intent,
                    principal_id,
                    confirmed_id,
                    confirmed_fingerprint,
                    execution_fingerprint,
                    workflow_key(intent),
                    now,
                    now,
                    now,
                ),
            )
            self._enqueue(con, "preflight", intent)
            row = con.execute("SELECT * FROM intents WHERE intent_id = ?", (intent,)).fetchone()
            return row, True

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
        with self.read() as con:
            row = con.execute(
                "SELECT intent_id FROM intents WHERE principal_id = ? AND stage IN ({})".format(
                    ",".join("?" * len(OPEN_STAGES))
                ),
                (principal_id, *OPEN_STAGES),
            ).fetchone()
        return None if row is None else row[0]

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

    def submit_intent(
        self,
        principal_id: str,
        intent_id: str,
        *,
        confirm_with: str,
        key: str,
        run_id: str,
    ) -> tuple[str, str]:
        """确认开始：一个短事务里核对意图与幂等键、预留 1 次、写下稳定运行编号、排队。返回（回执状态，运行编号）。

        * 同一个键、同一份请求 → ``duplicate_same_run``（不再预留、不再排队）；同一个键、不同请求 → 冲突；
        * 同一意图已经确认过：即使换了键，也返回原运行（同一份预检查）——一次确认只能产生一次运行；
        * 确认的不是当前预检查 → ``preflight_stale``；次数不够 → ``quota_exhausted``。"""
        request_fp = digest(json.dumps({"intent": intent_id, "confirm_with": confirm_with}))
        key_digest = digest(f"{principal_id}|{key}")
        now = self.now()
        with self.write() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE intent_id = ? AND principal_id = ?",
                (intent_id, principal_id),
            ).fetchone()
            if row is None:
                raise ServiceFailure("not_found")
            seen = con.execute(
                "SELECT * FROM submissions WHERE key_digest = ?", (key_digest,)
            ).fetchone()
            if seen is not None and (seen["intent_id"], seen["request_fingerprint"]) != (
                intent_id,
                request_fp,
            ):
                raise ServiceFailure("idempotency_conflict")
            if row["stage"] in GONE_STAGES:
                raise ServiceFailure("deleting" if row["stage"] == "deleting" else "deleted")
            if row["run_id"] is not None:
                # 已经确认过：同一份预检查返回原运行（不管键是不是新的）；换了预检查就是旧确认
                if row["preflight_fingerprint"] != confirm_with:
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
            self.touch(
                con,
                intent_id,
                stage="queued",
                waiting_for="service",
                run_id=run_id,
                last_activity_at=now,
                explanation=None,
            )
            self._enqueue(con, "run", intent_id)
            return "accepted", run_id

    def cancel_intent(self, principal_id: str, intent_id: str) -> sqlite3.Row:
        """开始前取消：检查中、等确认、阻断、排队（还没有启动）的意图可以取消；排队的释放预留。已经开始的不能取消（可以删除）。"""
        now = self.now()
        with self.write() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE intent_id = ? AND principal_id = ?",
                (intent_id, principal_id),
            ).fetchone()
            if row is None:
                raise ServiceFailure("not_found")
            if row["stage"] == "cancelled":
                return row
            if row["stage"] not in ("checking", "awaiting_confirmation", "blocked", "queued"):
                raise ServiceFailure("not_resumable" if row["stage"] != "deleting" else "deleting")
            self._release(con, intent_id, "开始前取消")
            con.execute(
                "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state = 'pending'",
                (now, intent_id),
            )
            # 取消的意图不再需要运行前说明（它含答案的摘要）；工作目录由 worker 清理
            fields: dict[str, Any] = {c: None for c in INTENT_CONTENT}
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
        with self.write() as con:
            row = con.execute(
                "SELECT * FROM intents WHERE run_id = ? AND principal_id = ?",
                (run_id, principal_id),
            ).fetchone()
            if row is None:
                raise ServiceFailure("not_found")
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
            row = con.execute(
                "SELECT * FROM intents WHERE intent_id = ? AND principal_id = ?",
                (intent_id, principal_id),
            ).fetchone()
            if row is None:
                raise ServiceFailure("not_found")
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
        """删除本人全部业务内容：草稿、审阅、确认与全部运行；保留次数事件与最小墓碑。返回进入删除的意图。"""
        now = self.now()
        with self.write() as con:
            for table in ("drafts", "reviews", "profiles"):
                con.execute(
                    f"UPDATE {table} SET body = NULL, deleted_at = COALESCE(deleted_at, ?) WHERE principal_id = ?",
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
        """最早的待办任务，标为进行中。删除任务优先（让删除尽快收尾）。"""
        with self.write() as con:
            row = con.execute(
                "SELECT * FROM jobs WHERE state = 'pending' ORDER BY kind != 'delete', job_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            con.execute(
                "UPDATE jobs SET state = 'active', started_at = ? WHERE job_id = ?",
                (self.now(), row["job_id"]),
            )
            return con.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()

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
        """worker 启动时：上一个进程留下的进行中任务退回待办（恢复由原流程与 E1 的核对负责，不重置任何计数）。"""
        with self.write() as con:
            rows = list(con.execute("SELECT * FROM jobs WHERE state = 'active'"))
            con.execute("UPDATE jobs SET state = 'pending' WHERE state = 'active'")
            return rows

    # ------------------------------------------------------------------ worker 写回的公开状态

    def update_intent(self, intent_id: str, *, only_stages: tuple[str, ...], **fields: Any) -> bool:
        """只在意图还处于 ``only_stages`` 时写回（删除或取消优先：被删的意图不会被监测线程或完成写回复活）。"""
        with self.write() as con:
            row = con.execute(
                "SELECT stage FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None or row["stage"] not in only_stages:
                return False
            self.touch(con, intent_id, **fields)
            return True

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
        """worker 用：确认画像正文（不按身份过滤；已删除的返回 None）。"""
        with self.read() as con:
            row = con.execute(
                "SELECT body FROM profiles WHERE confirmed_id = ? AND deleted_at IS NULL",
                (confirmed_id,),
            ).fetchone()
        return None if row is None else row[0]

    def consumed(self, intent_id: str) -> bool:
        """这个意图的次数是否已经转为已使用（E1 已经启动过）。"""
        with self.read() as con:
            return (
                con.execute(
                    "SELECT 1 FROM quota_events WHERE intent_id = ? AND kind = 'consume'",
                    (intent_id,),
                ).fetchone()
                is not None
            )

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
        """worker 开始执行运行任务：排队 → 运行中。已经取消、删除的不开始。"""
        return self.update_intent(
            intent_id, only_stages=("queued",), stage="running", waiting_for="service"
        )

    def finish_intent(
        self, intent_id: str, *, result_body: str, result_fingerprint: str, days: int
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
                first_finished_at=first,
                result_expires_at=days_after(first, days),
                resume_allowed=0,
            )
            return True

    # ------------------------------------------------------------------ 过期

    def expire_due(self, intent_days: int) -> list[str]:
        """把到期的意图与草稿标为过期（内容清掉），返回过期的意图编号（文件由 worker 清理）。查看、轮询与心跳不续期：结果按首次
        结束起算，其他意图按最后一次有效业务操作，草稿按最后一次编辑。运行中的意图不在这里过期（它的最后操作是确认开始）。"""
        now = self.now()
        out: list[str] = []
        with self.write() as con:
            rows = list(
                con.execute(
                    "SELECT intent_id, stage, result_expires_at, last_activity_at "
                    "FROM intents WHERE stage NOT IN ('deleted', 'expired', 'deleting', 'running')"
                )
            )
            for r in rows:
                if not intent_due(r, now, intent_days):
                    continue
                self._release(con, r["intent_id"], "过期")
                fields: dict[str, Any] = {c: None for c in INTENT_CONTENT}
                self.touch(
                    con,
                    r["intent_id"],
                    stage="expired",
                    waiting_for="none",
                    stop_requested=1,
                    resume_allowed=0,
                    **fields,
                )
                con.execute(
                    "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE intent_id = ? AND state = 'pending'",
                    (now, r["intent_id"]),
                )
                out.append(r["intent_id"])
            con.execute(
                "UPDATE drafts SET body = NULL, deleted_at = ? WHERE deleted_at IS NULL AND expires_at <= ?",
                (now, now),
            )
        if out:
            self.checkpoint()
        return out
