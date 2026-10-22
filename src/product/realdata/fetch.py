"""带缓存的获取。

* 原始文件按“请求内容”寻址：同一个 URL、参数与请求体得到同一个缓存键，存在 ``<缓存根>/<来源>/<键前 16 位>.bin``，旁边的
  ``.meta.json`` 记 URL、状态、SHA-256、大小与获取时间。缓存根默认是 ``data/raw/intake``（``.gitignore`` 里，不进仓库）。
* 三种模式：``offline`` 只读缓存（构建与测试默认；缓存里没有就记 ``not_cached``，不联网）；``cache_first`` 缓存没有才联网；
  ``refresh`` 总是联网。联网只在显式的获取命令里发生。
* 只用不含个人信息的通用 User-Agent；不带密钥、不注册、不付费。访问被拒或需要凭证的来源照实记录，不绕过。
* 获取时间与是否命中缓存是运行元数据，不进内容身份；原始文件的 SHA-256 进。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from common.intake_contracts import FetchRecord, canonical_json

USER_AGENT = "homo-spatialis-intake/0.1 (open-data build; no-reply)"
DEFAULT_CACHE = Path("data/raw/intake")
Mode = Literal["offline", "cache_first", "refresh"]


Expect = Literal["any", "json", "zip", "xlsx", "text"]


def content_problem(content: bytes, expect: Expect) -> str | None:
    """内容是不是预期的格式。HTTP 200 也可能是一页“需要密钥”或反爬挑战的网页——那不是资料，不能当成功缓存。"""
    head = content[:512].lstrip()
    if expect == "json" and head[:1] not in (b"[", b"{"):
        return "预期 JSON，收到的不是（多半是“需要密钥”或挑战页面）"
    if expect in ("zip", "xlsx") and not content.startswith(b"PK"):
        return f"预期 {expect} 压缩文件，收到的不是"
    if expect == "text" and head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "预期纯文本资料，收到的是网页"
    return None


@dataclass(frozen=True)
class Request:
    """一次请求：来源、URL、查询参数、POST 表单（Overpass 用）、预期格式。"""

    source_id: str
    url: str
    params: tuple[tuple[str, str], ...] = ()
    form: tuple[tuple[str, str], ...] = ()
    timeout: float = 120.0
    expect: Expect = "any"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def method(self) -> str:
        return "POST" if self.form else "GET"

    @property
    def key(self) -> str:
        content = {
            "method": self.method,
            "url": self.url,
            "params": [list(p) for p in self.params],
            "form": [list(p) for p in self.form],
        }
        return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()

    def display_url(self) -> str:
        if not self.params:
            return self.url
        q = "&".join(f"{k}={v}" for k, v in self.params)
        return f"{self.url}?{q}"[:400]


@dataclass
class Fetched:
    """获取结果：记录（运行元数据）与原始字节（失败时为空）。"""

    record: FetchRecord
    content: bytes | None

    @property
    def ok(self) -> bool:
        return self.record.status == "ok" and self.content is not None


def _paths(cache: Path, req: Request) -> tuple[Path, Path]:
    base = Path(cache) / req.source_id / req.key[:16]
    return base.with_suffix(".bin"), base.with_suffix(".meta.json")


def read_cached(req: Request, cache: Path = DEFAULT_CACHE) -> Fetched:
    """只读缓存。缓存里有且 SHA-256 对得上才算命中。"""
    data_path, meta_path = _paths(cache, req)
    if not data_path.exists() or not meta_path.exists():
        return Fetched(
            FetchRecord(
                source_id=req.source_id,
                request_key=req.key,
                url=req.display_url(),
                status="not_cached",
                note="缓存里没有这份原始文件；离线模式不联网（先运行显式的获取命令）",
            ),
            None,
        )
    content = data_path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(content).hexdigest()
    problem = content_problem(content, req.expect)
    if digest != meta.get("sha256") or problem is not None:
        return Fetched(
            FetchRecord(
                source_id=req.source_id,
                request_key=req.key,
                url=req.display_url(),
                status="error",
                note=problem or "缓存文件与它记下的 SHA-256 不一致：不使用（重新获取）",
            ),
            None,
        )
    return Fetched(
        FetchRecord(
            source_id=req.source_id,
            request_key=req.key,
            url=req.display_url(),
            status="ok",
            http_status=meta.get("http_status"),
            raw_sha256=digest,
            bytes=len(content),
            retrieved_at=meta.get("retrieved_at"),
            from_cache=True,
        ),
        content,
    )


def _store(cache: Path, req: Request, content: bytes, http_status: int, ctype: str) -> FetchRecord:
    data_path, meta_path = _paths(cache, req)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    now = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    data_path.write_bytes(content)
    meta = {
        "source_id": req.source_id,
        "url": req.display_url(),
        "method": req.method,
        "http_status": http_status,
        "content_type": ctype,
        "sha256": digest,
        "bytes": len(content),
        "retrieved_at": now,
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return FetchRecord(
        source_id=req.source_id,
        request_key=req.key,
        url=req.display_url(),
        status="ok",
        http_status=http_status,
        raw_sha256=digest,
        bytes=len(content),
        retrieved_at=now,
        from_cache=False,
    )


def fetch(
    req: Request, cache: Path = DEFAULT_CACHE, mode: Mode = "offline", *, attempts: int = 3
) -> Fetched:
    """按模式获取。联网失败照实记录（HTTP 错误、被拒、超时），不抛给上层；上层把它记进覆盖报告。"""
    if mode != "refresh":
        hit = read_cached(req, cache)
        if hit.ok or mode == "offline":
            return hit
    import httpx  # 只在联网时导入，离线构建与测试不需要网络栈

    last: FetchRecord | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=req.timeout
            ) as client:
                if req.form:
                    r = client.post(req.url, data=dict(req.form))
                else:
                    r = client.get(req.url, params=list(req.params) or None)
        except httpx.TimeoutException as exc:
            last = FetchRecord(
                source_id=req.source_id,
                request_key=req.key,
                url=req.display_url(),
                status="timeout",
                note=f"超时：{type(exc).__name__}",
            )
        except httpx.HTTPError as exc:
            last = FetchRecord(
                source_id=req.source_id,
                request_key=req.key,
                url=req.display_url(),
                status="error",
                note=f"连接错误：{type(exc).__name__}",
            )
        else:
            ctype = r.headers.get("content-type", "")
            problem = content_problem(r.content, req.expect) if r.status_code == 200 else None
            if problem is not None:
                return Fetched(
                    FetchRecord(
                        source_id=req.source_id,
                        request_key=req.key,
                        url=req.display_url(),
                        status="blocked",
                        http_status=r.status_code,
                        note=f"{problem}：不缓存、不绕过",
                    ),
                    None,
                )
            if r.status_code == 200 and r.content:
                return Fetched(_store(cache, req, r.content, r.status_code, ctype), r.content)
            blocked = r.status_code in (202, 401, 403, 429) or (
                r.status_code == 200 and not r.content
            )
            last = FetchRecord(
                source_id=req.source_id,
                request_key=req.key,
                url=req.display_url(),
                status="blocked" if blocked else "http_error",
                http_status=r.status_code,
                note=(
                    "来源拒绝或要求验证（例如反爬挑战、需要凭证、限流）：不绕过"
                    if blocked
                    else f"HTTP {r.status_code}"
                ),
            )
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt + 1 < attempts:
            time.sleep(min(30.0, 3.0 * (2**attempt)))
    assert last is not None
    return Fetched(last, None)
