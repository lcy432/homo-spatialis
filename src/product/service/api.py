"""托管服务的 HTTP 接口（FastAPI，DECISIONS/0043）。只做认证、请求与查询：长任务都交给独立的 worker，不在 HTTP 请求或
``BackgroundTasks`` 里跑；这个进程不调用 ``chdir``、不推进 LangGraph、不调用规划器。

* 身份：专属链接的秘密经 ``POST /api/v1/session``（同源 JSON）换成会话；会话秘密放在 HttpOnly、SameSite=Strict、仅本主机的 Cookie
  里（生产加 Secure 与 ``__Host-`` 前缀），固定 7 天，查询不续期。服务端只存两种秘密的摘要；服务端身份是唯一的身份。
* 修改请求：Origin 必须是本站，并带会话绑定的 ``X-CSRF-Token``；CORS 不开放；请求体最大 256 KiB；邀请交换每个来源地址每分钟
  10 次，其他修改每个身份每分钟 60 次，超出返回 429。
* 错误：统一的安全错误（``ServiceError``）；不回显用户输入、原始校验异常、路径或堆栈。别人的编号与不存在一样返回 ``not_found``。
* 日志：只记方法、路由模板、状态码、错误代码与支持编号；不记 Cookie、邀请秘密、CSRF token、请求体或原始异常信息。
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.product_contracts import (
    ConfirmedProfileView,
    DeletionReceipt,
    DisplayEnvelope,
    DraftSaveRequest,
    DraftView,
    GeographyDisplayBundle,
    ProductCapabilities,
    ProductResult,
    ProfileConfirmReply,
    ProfileConfirmRequest,
    ProfileList,
    ReviewReply,
    RunIntentCreateRequest,
    RunIntentView,
    RunList,
    RunProgress,
    RunSubmitReply,
    RunSubmitRequest,
    ServiceError,
    SessionExchangeRequest,
    SessionView,
)
from product.display import recording
from product.service import actions
from product.service import display as service_display
from product.service.actions import ServiceContext
from product.service.clock import parse
from product.service.errors import ERRORS, ServiceFailure, error_body
from product.service.tokens import same, support_ref

API = "/api/v1"
API_VERSION = "e3c-service-api-v1.1"
log = logging.getLogger("product.service.api")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: 公开的错误响应（OpenAPI 用）：每个接口都可能返回这些
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ServiceError, "description": "统一的安全错误"}
    for status in sorted({v[0] for v in ERRORS.values()})
}


def _also(status: int, model: type, description: str) -> dict[int | str, dict[str, Any]]:
    """错误响应之外再声明一个实际会返回的成功状态（例如重复操作返回 200、新建返回 202）。"""
    return {**ERROR_RESPONSES, status: {"model": model, "description": description}}


def cookie_name(ctx: ServiceContext) -> str:
    # ``__Host-`` 前缀要求 Secure、Path=/、不写 Domain（只属于本主机）；本地 HTTP 模式不能用 Secure，就不用这个前缀
    return "__Host-hs_session" if ctx.config.secure_cookies else "hs_session"


#: 运行选项的字段：请求格式不对时回 ``run_options_invalid`` 并写出是哪一项（不回显用户填的值）
OPTION_FIELDS = ("clones_per_city", "years", "max_cost_cny")


def _error_response(
    code: str,
    *,
    status: int | None = None,
    support: str | None = None,
    retry_after: int | None = None,
    field: str | None = None,
    subject: str | None = None,
    pack: str | None = None,
) -> JSONResponse:
    """统一的安全错误。给了 ``pack``（服务的资料包身份）就带上中英两种显示文字（固定双语目录生成，未登录时也有）。"""
    with recording() as rec:
        body = error_body(
            code, support_ref=support, retry_after=retry_after, field=field, subject=subject
        )
    if status is not None:
        body = body.model_copy(update={"status": status})
    if pack is not None:
        body = service_display.with_display(body, rec, pack_fingerprint=pack)
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(body.model_dump(mode="json"), status_code=body.status, headers=headers)


class RateLimiter:
    """每个键在最近 60 秒里最多 ``per_minute`` 次（单进程内存计数；服务只有一个 HTTP 进程）。超出时返回需要等待的秒数。"""

    def __init__(self, per_minute: int, now: Callable[[], float]) -> None:
        self.per_minute = per_minute
        self.now = now
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        t = self.now()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= t - 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return max(1, int(q[0] + 60 - t) + 1)
            q.append(t)
            if len(self._hits) > 10_000:  # 丢掉早已过期的键，不让内存无限增长
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= t - 60]:
                    del self._hits[k]
            return None


class Guard:
    """最外层的 ASGI 包装：请求体上限（先完整读入，超过就 413，不交给应用）、安全响应头、未处理异常的安全错误。"""

    def __init__(self, app: ASGIApp, *, limit: int, secure: bool, pack: str | None = None) -> None:
        self.app = app
        self.limit = limit
        self.secure = secure
        self.pack = pack

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or ())
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.limit):
            await _send(send, _error_response("payload_too_large", pack=self.pack), self._extra())
            return
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return  # 客户端断开
            body += message.get("body", b"")
            if len(body) > self.limit:
                await _send(
                    send, _error_response("payload_too_large", pack=self.pack), self._extra()
                )
                return
            more = bool(message.get("more_body"))
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = dict(message)
                message["headers"] = [*message.get("headers", []), *self._extra()]
            await send(message)

        try:
            await self.app(scope, replay, guarded_send)
        except Exception as exc:  # 未处理的程序错误：只回安全错误，不回显异常
            ref = support_ref()
            # 只记异常类型与代码位置（文件名:行号 函数），不记异常信息——它可能带着用户输入
            where = " < ".join(
                f"{f.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                for f in reversed(traceback.extract_tb(exc.__traceback__)[-8:])
            )
            log.error("internal_error %s %s at %s", ref, type(exc).__name__, where)
            if started:
                raise
            await _send(
                send, _error_response("internal_error", support=ref, pack=self.pack), self._extra()
            )

    def _extra(self) -> list[tuple[bytes, bytes]]:
        out = [
            (b"cache-control", b"private, no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]
        if self.secure:
            out.append((b"strict-transport-security", b"max-age=31536000"))
        return out


async def _send(send: Send, response: JSONResponse, extra: list[tuple[bytes, bytes]]) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [*response.raw_headers, *extra],
        }
    )
    await send({"type": "http.response.body", "body": response.body})


@dataclass(frozen=True)
class Caller:
    principal_id: str
    session_secret: str
    expires: str
    #: 请求开始时的数据代次：写回正文时服务库核对它（中途“删除全部”的请求不再写回）
    generation: int


def create_app(ctx: ServiceContext) -> FastAPI:
    config = ctx.config
    docs = config.expose_docs
    app = FastAPI(
        title="Homo Spatialis 产品服务",
        version=API_VERSION,
        description="E3C 托管服务的公开接口（同源 HTTPS；会话 Cookie + CSRF；长任务由后台 worker 执行，网页轮询）。",
        docs_url=f"{API}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{API}/openapi.json" if docs else None,
    )
    cookie = cookie_name(ctx)
    pack = ctx.pack.fingerprint
    now = lambda: ctx.clock().timestamp()  # noqa: E731
    invite_limit = RateLimiter(config.invite_rate_per_minute, now)
    write_limit = RateLimiter(config.write_rate_per_minute, now)

    # ------------------------------------------------------------------ 错误

    @app.exception_handler(ServiceFailure)
    async def _failure(_: Request, exc: ServiceFailure) -> JSONResponse:
        return _error_response(
            exc.code,
            support=exc.support_ref,
            retry_after=exc.retry_after,
            field=exc.field,
            subject=exc.subject,
            pack=pack,
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 不回显原始校验异常（它可能带着用户输入）；运行选项格式不对时只说是哪一项
        if request.url.path == f"{API}/run-intents" and request.method == "POST":
            for err in exc.errors():
                loc = tuple(err.get("loc") or ())
                if len(loc) >= 2 and loc[0] == "body" and loc[1] in OPTION_FIELDS:
                    return _error_response("run_options_invalid", field=str(loc[1]), pack=pack)
        return _error_response("invalid_request", pack=pack)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response("not_found", pack=pack)
        return _error_response("invalid_request", status=exc.status_code, pack=pack)

    # ------------------------------------------------------------------ 身份与修改保护

    def _origin(request: Request) -> None:
        if request.headers.get("origin") != config.site_origin:
            raise ServiceFailure("origin_rejected")
        site = request.headers.get("sec-fetch-site")
        if site is not None and site != "same-origin":
            raise ServiceFailure("origin_rejected")

    def caller(request: Request) -> Caller:
        secret = request.cookies.get(cookie)
        if not secret:
            raise ServiceFailure("unauthenticated")
        got = ctx.store.session(secret)
        if got is None:
            raise ServiceFailure("unauthenticated")
        return Caller(principal_id=got[0], session_secret=secret, expires=got[1], generation=got[2])

    def writer(request: Request) -> Caller:
        _origin(request)
        who = caller(request)
        if not same(request.headers.get("x-csrf-token", ""), ctx.csrf(who.session_secret)):
            raise ServiceFailure("csrf_failed")
        retry = write_limit.hit(who.principal_id)
        if retry is not None:
            raise ServiceFailure("rate_limited", retry_after=retry)
        return who

    def exchange_guard(request: Request) -> None:
        # 先计数再看别的：猜邀请的每一次尝试都算数（包括来源不对、格式不对的）
        retry = invite_limit.hit(request.client.host if request.client else "unknown")
        if retry is not None:
            raise ServiceFailure("rate_limited", retry_after=retry)
        _origin(request)
        kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind != "application/json":
            raise ServiceFailure("invalid_request")

    read = Depends(caller)
    write = Depends(writer)

    # ------------------------------------------------------------------ 会话与能力

    @app.post(
        f"{API}/session",
        response_model=SessionView,
        responses=ERROR_RESPONSES,
        dependencies=[Depends(exchange_guard)],
        summary="用专属链接的秘密换会话（同源 JSON）",
    )
    def exchange(body: SessionExchangeRequest, request: Request, response: Response) -> SessionView:
        principal = ctx.store.principal_for_invite(body.invite_token)
        if principal is None:
            raise ServiceFailure("unauthenticated")
        old = request.cookies.get(cookie)
        if old:
            ctx.store.revoke_session(old)
        secret, expires = ctx.store.create_session(principal, config.session_days)
        response.set_cookie(
            cookie,
            secret,
            max_age=int((parse(expires) - ctx.clock()).total_seconds()),
            expires=parse(expires),
            path="/",
            secure=config.secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return actions.session_view(ctx, principal, secret, expires)

    @app.get(
        f"{API}/session",
        response_model=SessionView,
        responses=ERROR_RESPONSES,
        summary="当前会话、CSRF token、次数与服务规则",
    )
    def session(who: Caller = read) -> SessionView:
        return actions.session_view(ctx, who.principal_id, who.session_secret, who.expires)

    @app.delete(
        f"{API}/session", status_code=204, responses=ERROR_RESPONSES, summary="退出这个设备"
    )
    def logout(who: Caller = write) -> Response:
        ctx.store.revoke_session(who.session_secret)
        out = Response(status_code=204)
        out.delete_cookie(
            cookie, path="/", secure=config.secure_cookies, httponly=True, samesite="strict"
        )
        return out

    @app.get(
        f"{API}/capabilities",
        response_model=ProductCapabilities,
        responses=ERROR_RESPONSES,
        summary="这个资料包支持的城市、职业、问题与说明",
    )
    def capabilities(_: Caller = read) -> ProductCapabilities:
        return actions.capabilities(ctx)

    # ------------------------------------------------------------------ 草稿、审阅与确认

    @app.post(
        f"{API}/drafts",
        status_code=201,
        response_model=DraftView,
        responses=ERROR_RESPONSES,
        summary="新建草稿（编号与修订号由服务器分配）",
    )
    def create_draft(who: Caller = write) -> DraftView:
        return actions.create_draft(ctx, who.principal_id, generation=who.generation)

    @app.get(
        f"{API}/drafts/{{draft_id}}",
        response_model=DraftView,
        responses=ERROR_RESPONSES,
        summary="读取草稿",
    )
    def get_draft(draft_id: str, who: Caller = read) -> DraftView:
        return actions.get_draft(ctx, who.principal_id, draft_id)

    @app.put(
        f"{API}/drafts/{{draft_id}}",
        response_model=DraftView,
        responses=ERROR_RESPONSES,
        summary="带版本保存草稿（旧版本返回冲突，不覆盖）",
    )
    def save_draft(draft_id: str, body: DraftSaveRequest, who: Caller = write) -> DraftView:
        return actions.save_draft(ctx, who.principal_id, draft_id, body, generation=who.generation)

    @app.delete(
        f"{API}/drafts/{{draft_id}}",
        response_model=DeletionReceipt,
        responses=ERROR_RESPONSES,
        summary="删除草稿",
    )
    def delete_draft(draft_id: str, who: Caller = write) -> DeletionReceipt:
        return actions.delete_draft(ctx, who.principal_id, draft_id)

    @app.post(
        f"{API}/drafts/{{draft_id}}/review",
        response_model=ReviewReply,
        responses=ERROR_RESPONSES,
        summary="审阅已保存的那一版草稿",
    )
    def review(draft_id: str, who: Caller = write) -> ReviewReply:
        return actions.review(ctx, who.principal_id, draft_id, generation=who.generation)

    @app.post(
        f"{API}/profiles/confirm",
        response_model=ProfileConfirmReply,
        responses=ERROR_RESPONSES,
        summary="确认审阅过的画像（服务端重新校验并生成确认画像）",
    )
    def confirm(body: ProfileConfirmRequest, who: Caller = write) -> ProfileConfirmReply:
        with recording() as rec:
            reply = actions.confirm(ctx, who.principal_id, body, generation=who.generation)
        # 确认被拒时可能没有可以再读的对象：中英两种文字随回复一起给
        from product.display import messages

        return service_display.with_display(
            reply,
            rec,
            pack_fingerprint=pack,
            user_text=messages.under("/confirmed", messages.PROFILE),
        )

    @app.get(
        f"{API}/profiles",
        response_model=ProfileList,
        responses=ERROR_RESPONSES,
        summary="本人还能用来准备新模拟的确认画像摘要（首次确认与截止时间；查询不续期）",
    )
    def list_profiles(who: Caller = read) -> ProfileList:
        return actions.profiles(ctx, who.principal_id)

    @app.get(
        f"{API}/profiles/{{confirmed_id}}",
        response_model=ConfirmedProfileView,
        responses=ERROR_RESPONSES,
        summary="读取本人的一份确认画像与它的日期（别人的与不存在的一样 404）",
    )
    def get_profile(confirmed_id: str, who: Caller = read) -> ConfirmedProfileView:
        return actions.profile_view(ctx, who.principal_id, confirmed_id)

    # ------------------------------------------------------------------ 运行意图

    @app.post(
        f"{API}/run-intents",
        status_code=202,
        response_model=RunIntentView,
        responses=_also(
            200, RunIntentView, "同一份确认画像、同样的有效设置已经有未结束的意图：返回原意图"
        ),
        summary="准备一次模拟：排队做运行前检查（不使用次数）；新建 202，返回已有 200",
    )
    def create_intent(
        body: RunIntentCreateRequest, response: Response, who: Caller = write
    ) -> RunIntentView:
        view, created = actions.create_intent(
            ctx, who.principal_id, body, generation=who.generation
        )
        response.status_code = 202 if created else 200
        return view

    @app.get(
        f"{API}/run-intents/{{intent_id}}",
        response_model=RunIntentView,
        responses=ERROR_RESPONSES,
        summary="读取一次模拟的流程状态（只读快照）",
    )
    def get_intent(intent_id: str, who: Caller = read) -> RunIntentView:
        return actions.get_intent(ctx, who.principal_id, intent_id)

    @app.post(
        f"{API}/run-intents/{{intent_id}}/submit",
        status_code=202,
        response_model=RunSubmitReply,
        responses=_also(
            200, RunSubmitReply, "重复提交（duplicate_same_run）：返回原运行，不再预留、不再排队"
        ),
        summary="确认开始：预留 1 次并排队（accepted 202；重复提交 duplicate_same_run 200）",
    )
    def submit(
        intent_id: str, body: RunSubmitRequest, response: Response, who: Caller = write
    ) -> RunSubmitReply:
        reply = actions.submit(ctx, who.principal_id, intent_id, body)
        response.status_code = 202 if reply.receipt.status == "accepted" else 200
        return reply

    @app.post(
        f"{API}/run-intents/{{intent_id}}/cancel",
        response_model=RunIntentView,
        responses=ERROR_RESPONSES,
        summary="开始前取消（释放预留；已经开始的不能取消，可以删除）",
    )
    def cancel(intent_id: str, who: Caller = write) -> RunIntentView:
        return actions.cancel(ctx, who.principal_id, intent_id)

    # ------------------------------------------------------------------ 运行

    @app.get(f"{API}/runs", response_model=RunList, responses=ERROR_RESPONSES, summary="我的运行")
    def runs(who: Caller = read) -> RunList:
        return actions.runs(ctx, who.principal_id)

    @app.get(
        f"{API}/runs/{{run_id}}/progress",
        response_model=RunProgress,
        responses=ERROR_RESPONSES,
        summary="进度快照（只读：不恢复、不调用规划器、不重放）",
    )
    def progress(run_id: str, who: Caller = read) -> RunProgress:
        return actions.progress(ctx, who.principal_id, run_id)

    @app.get(
        f"{API}/runs/{{run_id}}/result",
        response_model=ProductResult,
        responses=ERROR_RESPONSES,
        summary="白名单结果（C / E / M 与未知照 E1 原样）",
    )
    def result(run_id: str, who: Caller = read) -> ProductResult:
        return actions.result(ctx, who.principal_id, run_id)

    @app.post(
        f"{API}/runs/{{run_id}}/resume",
        status_code=202,
        response_model=RunIntentView,
        responses=ERROR_RESPONSES,
        summary="恢复（只在服务端判定安全时允许，不额外使用次数）",
    )
    def resume(run_id: str, who: Caller = write) -> RunIntentView:
        return actions.resume(ctx, who.principal_id, run_id)

    @app.delete(
        f"{API}/runs/{{run_id}}",
        status_code=202,
        response_model=DeletionReceipt,
        responses=_also(200, DeletionReceipt, "已经处理完的删除（deleted）：返回同一份回执"),
        summary="删除一次运行（运行中也可以：不再批准新的模型请求）；进行中 202，已处理 200",
    )
    def delete_run(run_id: str, response: Response, who: Caller = write) -> DeletionReceipt:
        receipt = actions.delete_run(ctx, who.principal_id, run_id)
        response.status_code = 202 if receipt.status == "deleting" else 200
        return receipt

    @app.delete(
        f"{API}/me/data",
        status_code=202,
        response_model=DeletionReceipt,
        responses=ERROR_RESPONSES,
        summary="删除我的全部内容（保留次数记录与最小墓碑）",
    )
    def delete_all(who: Caller = write) -> DeletionReceipt:
        return actions.delete_all(ctx, who.principal_id)

    # ------------------------------------------------------------------ 显示附层（只读）

    @app.get(
        f"{API}/display",
        response_model=DisplayEnvelope,
        responses=ERROR_RESPONSES,
        summary="一个公共消息与它的中英显示文字（同次返回；只读，不续期、不调用模型）",
    )
    def display(
        kind: service_display.DisplayKindParam,
        id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$"),
        locale: service_display.LocaleParam = "zh",
        who: Caller = read,
    ) -> DisplayEnvelope:
        return service_display.envelope(
            ctx, who.principal_id, who.session_secret, who.expires, kind, id, locale
        )

    @app.get(
        f"{API}/display/geography",
        response_model=GeographyDisplayBundle,
        responses=ERROR_RESPONSES,
        summary="一个资料包、一个显示资源版本的地理显示清单（不授予运行资格；缺坐标的为空）",
    )
    def geography(
        pack_fingerprint: str = Query(pattern=r"^[0-9a-f]{64}$"),
        resource_version: str | None = Query(
            default=None, pattern=r"^e3-display-res-v1-[0-9a-f]{16}$"
        ),
        _: Caller = read,
    ) -> GeographyDisplayBundle:
        return service_display.geography(ctx, pack_fingerprint, resource_version)

    app.add_middleware(Guard, limit=config.max_body_bytes, secure=config.secure_cookies, pack=pack)
    return app


def openapi_document(ctx: ServiceContext) -> dict[str, Any]:
    """导出给 E5 的 OpenAPI 文档（生产不在线公开 ``/docs``，文件放在 contracts/product/v3/）。"""
    return create_app(ctx).openapi()
