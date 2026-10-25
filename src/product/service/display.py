"""服务的显示出口（``GET /api/v1/display``、``GET /api/v1/display/geography``）与即时回复的显示附层。

* 读取只鉴权、读取与纯函数渲染：原读取函数给出 ``source``（与对应的业务接口完全相同），显示文字来自两处——当场生成的部分（服务
  视图、能力清单、会话）在记录器里重新生成一次拿到绑定；生成时就保存的私有绑定（审阅、确认画像、预检查、进度、结果、停下的说明）
  从服务库读。不保存答案、不预检查、不恢复、不重放 E1、不调用模型；读取时不补写绑定。
* 无权、删除、过期与原读取接口相同（他人的编号与不存在的一样 404）；审阅还核对所属草稿的期限与修订。
* 即时回复（错误、确认结果）随回复带中英两种文字（``DisplayAttachment``）；未登录的错误也有，不依赖先登录成功。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from common.product_contracts import (
    DISPLAY_SCHEMA_VERSION,
    DisplayAttachment,
    DisplayEnvelope,
    GeographyDisplayBundle,
    LocalizedText,
    MetricDisplaySpec,
    ProductModel,
    ProfileReview,
    ReviewReply,
)
from product.display import messages
from product.display.catalog import current_version, registry, resources
from product.display.document import binding_set, capture, json_fingerprint, view_set
from product.display.render import BindingSet, display_fields
from product.display.text import Recorder
from product.service import actions
from product.service.actions import ServiceContext
from product.service.errors import ServiceFailure

KINDS_WITHOUT_ID = ("capabilities", "session", "runs")

#: 每种显示对象里照原文、不翻译的位置（用户原文与识别别名，相对 ``source``）
USER_TEXT: dict[str, tuple[str, ...]] = {
    "capabilities": messages.CAPABILITIES,
    "draft": messages.under("/draft", messages.DRAFT),
    "review": messages.under("/review", messages.REVIEW),
    "profile": messages.under("/confirmed", messages.PROFILE),
}

METRIC_NOTES = (
    LocalizedText(
        zh="同一结果里所有国家、两种地图共用这一固定尺度 [0, 1]，不按国家或城市重新拉伸；超出这个范围是契约错误，不截断",
        en="Every country and both map modes in one result share this fixed [0, 1] scale; it is never stretched per country or "
        "city, and a value outside it is a contract error rather than something to clip",
    ),
    LocalizedText(
        zh="C = 0 或没有评价时不画最低分的颜色；有模拟缺失（M）但仍有完整者均值时照常着色，同时标出缺失人数",
        en="When C = 0 or nothing was evaluated, do not draw the lowest colour; when there are simulation gaps (M) but a mean "
        "of the complete clones exists, colour it and show the number missing next to it",
    ),
    LocalizedText(
        zh="均值的分母是 mean_denominator（完成且评价完整的分身数 C）；模拟缺失范围的分母是 C + M，它不是置信区间",
        en="The mean's denominator is mean_denominator (C, the clones that finished with a complete evaluation); the "
        "simulation-gap range uses C + M and is not a confidence interval",
    ),
    LocalizedText(
        zh="只在这份结果之内比较；0.7 不表示现实里幸福 70%，也不是城市推荐或排名",
        en="Compare only within this result; 0.7 does not mean being 70% happy in real life, and it is not a city "
        "recommendation or ranking",
    ),
)


def _dump(model: ProductModel) -> Any:
    return model.model_dump(mode="json")


def _pack(ctx: ServiceContext) -> str:
    return ctx.pack.fingerprint


def _build(
    kind: str,
    ident: str | None,
    source: ProductModel,
    sets: list[BindingSet | None],
    locale: str,
    metrics: tuple[MetricDisplaySpec, ...] = (),
) -> DisplayEnvelope:
    body = _dump(source)
    live = [s for s in sets if s is not None]
    fields, status, _ = display_fields(body, live, locale, user_text=USER_TEXT.get(kind, ()))
    version = next((s.resource_version for s in live if s.resource_version), None)
    return DisplayEnvelope(
        display_schema_version=DISPLAY_SCHEMA_VERSION,
        kind=kind,  # type: ignore[arg-type]
        id=ident,
        source=source,  # type: ignore[arg-type]
        source_view_fingerprint=json_fingerprint(body),
        locale=locale,  # type: ignore[arg-type]
        resource_version=version,
        status=status,  # type: ignore[arg-type]
        fields=tuple(fields),  # type: ignore[arg-type]
        metrics=metrics,
    )


def _view(source: ProductModel, rec: Recorder, ctx: ServiceContext, kind: str) -> BindingSet:
    return view_set(
        _dump(source), rec, pack_fingerprint=_pack(ctx), user_text=USER_TEXT.get(kind, ())
    )


def envelope(
    ctx: ServiceContext,
    principal_id: str,
    session_secret: str,
    session_expires: str,
    kind: str,
    ident: str | None,
    locale: str,
) -> DisplayEnvelope:
    """一次显示读取。``kind`` 与 ``id`` 已经由接口校验过枚举与格式。"""
    if (ident is None) != (kind in KINDS_WITHOUT_ID):
        raise ServiceFailure("invalid_request")
    if kind == "capabilities":
        cap, rec = capture(actions.capabilities, ctx)
        return _build(kind, None, cap, [_view(cap, rec, ctx, kind)], locale)
    if kind == "session":
        sv, rec = capture(actions.session_view, ctx, principal_id, session_secret, session_expires)
        return _build(kind, None, sv, [_view(sv, rec, ctx, kind)], locale)
    if kind == "runs":
        rl, rec = capture(actions.runs, ctx, principal_id)
        return _build(kind, None, rl, [_view(rl, rec, ctx, kind)], locale)
    assert ident is not None
    if kind == "draft":
        dv, rec = capture(actions.get_draft, ctx, principal_id, ident)
        return _build(kind, ident, dv, [_view(dv, rec, ctx, kind)], locale)
    if kind == "review":
        return _review(ctx, principal_id, ident, locale)
    if kind == "profile":
        row = ctx.store.profile(principal_id, ident)
        pv, rec = capture(actions.profile_view, ctx, principal_id, ident)
        stored = binding_set(row["display_body"], _dump(pv.confirmed), prefix="/confirmed")
        return _build(kind, ident, pv, [_view(pv, rec, ctx, kind), stored], locale)
    if kind == "intent":
        row = ctx.store.intent(principal_id, ident)
        iv, rec = capture(actions.view, ctx, principal_id, row)
        body = _dump(iv)
        sets: list[BindingSet | None] = [_view(iv, rec, ctx, kind)]
        for part, column in (
            ("preflight", "preflight_display"),
            ("progress", "progress_display"),
            ("explanation", "explanation_display"),
        ):
            if body.get(part) is not None:
                sets.append(binding_set(row[column], body[part], prefix=f"/{part}"))
        return _build(kind, ident, iv, sets, locale)
    if kind == "progress":
        pv, rec = capture(actions.progress, ctx, principal_id, ident)
        row = ctx.store.intent_by_run(principal_id, ident)
        if row["progress_body"] is not None:
            sets = [binding_set(row["progress_display"], _dump(pv))]
        else:
            sets = [_view(pv, rec, ctx, kind)]  # 还没有快照：按这次运行保存的设置当场生成的最小快照
        return _build(kind, ident, pv, sets, locale)
    if kind == "result":
        res, _ = capture(actions.result, ctx, principal_id, ident)
        row = ctx.store.intent_by_run(principal_id, ident)
        stored = binding_set(row["result_display"], _dump(res))
        return _build(kind, ident, res, [stored], locale, metrics=(metric_spec(row),))
    raise ServiceFailure("invalid_request")


def _review(ctx: ServiceContext, principal_id: str, review_id: str, locale: str) -> DisplayEnvelope:
    """审阅：不只看审阅记录有没有被删——所属草稿要还在、没过期，而且还是审阅的那一版（改了新修订，旧审阅作废）。草稿删了或过期
    就与草稿读取一样回答（404 / 410），草稿还在而审阅作废了才是 ``revision_conflict``。"""
    try:
        row = ctx.store.review(principal_id, review_id)
    except ServiceFailure as exc:
        if exc.code == "revision_conflict":
            draft_id = ctx.store.review_draft_id(principal_id, review_id)
            assert draft_id is not None
            ctx.store.draft(principal_id, draft_id)
        raise
    draft = ctx.store.draft(principal_id, row["draft_id"])
    if draft["revision"] != row["draft_revision"] or row["body"] is None:
        raise ServiceFailure("revision_conflict")
    review = ProfileReview.model_validate_json(row["body"])
    reply, rec = capture(
        lambda: ReviewReply(meta=ctx.meta(), review=review, reviewed_revision=row["draft_revision"])
    )
    stored = binding_set(row["display_body"], _dump(review), prefix="/review")
    return _build("review", review_id, reply, [_view(reply, rec, ctx, "review"), stored], locale)


def metric_spec(row: Any) -> MetricDisplaySpec:
    """结果色阶的显示约定，附这次运行绑定的协议、评分与评价（公开引用，从确认的预检查读）。"""
    refs: list[str] = []
    if row["preflight_body"] is not None:
        binding = (json.loads(row["preflight_body"]) or {}).get("binding") or {}
        if binding:
            refs = [
                f"protocol:{binding.get('protocol_id')}",
                f"scoring:{binding.get('scoring_version')}@{str(binding.get('scoring_content_fingerprint'))[:12]}",
                f"evaluation:{binding.get('evaluation_spec_id')}",
            ]
    return MetricDisplaySpec(
        key="mean_j",
        minimum="0",
        maximum="1",
        unit="model_score",
        direction="higher_model_evaluation",
        denominator_field="mean_denominator",
        comparison_scope="within_result_only",
        evaluation_refs=tuple(refs),
        notes=METRIC_NOTES,
    )


# ---------------------------------------------------------------------- 即时回复的显示附层


def attachment(
    reply: ProductModel, rec: Recorder, *, pack_fingerprint: str, user_text: tuple[str, ...] = ()
) -> DisplayAttachment:
    """对一份即时回复（去掉 ``display`` 之后）生成中英两种显示文字。英文不照抄原文片段参数（可能是用户填的值）：即时回复的译文里
    不放用户输入，那个位置照实报缺失，页面显示中文原文。"""
    body = reply.model_dump(mode="json", exclude={"display"})
    bset = view_set(body, rec, pack_fingerprint=pack_fingerprint, user_text=user_text)
    translations = {}
    for locale in ("zh", "en"):
        fields, status, _ = display_fields(
            body, [bset], locale, user_text=user_text, raw_params=False
        )
        translations[locale] = {"status": status, "fields": fields}
    return DisplayAttachment.model_validate(
        {
            "display_schema_version": DISPLAY_SCHEMA_VERSION,
            "resource_version": bset.resource_version,
            "source_view_fingerprint": json_fingerprint(body),
            "translations": translations,
        }
    )


def with_display(
    reply: ProductModel, rec: Recorder, *, pack_fingerprint: str, user_text: tuple[str, ...] = ()
) -> Any:
    return reply.model_copy(
        update={
            "display": attachment(
                reply, rec, pack_fingerprint=pack_fingerprint, user_text=user_text
            )
        }
    )


# ---------------------------------------------------------------------- 地理显示清单


def geography(
    ctx: ServiceContext, pack_fingerprint: str, version: str | None
) -> GeographyDisplayBundle:
    """按资料包与资源版本读地理清单。资料包没有登记：404；明确请求的版本不存在：404（不回退当前版本）；登记了但这个版本没有
    地理资源：``unavailable``、城市为空（仍可从城市列表访问）。省略版本时用登记的当前版本。"""
    entry = registry().get(pack_fingerprint)
    if entry is None:
        raise ServiceFailure("not_found")
    chosen = version or current_version(pack_fingerprint)
    res = None if chosen is None else resources(pack_fingerprint, chosen)
    if res is None:
        raise ServiceFailure("not_found")
    geo = res.geography
    if not geo:
        return GeographyDisplayBundle(
            display_schema_version=DISPLAY_SCHEMA_VERSION,
            resource_version=chosen,
            pack_fingerprint=pack_fingerprint,
            status="unavailable",
        )
    return GeographyDisplayBundle.model_validate(
        {
            **geo,
            "display_schema_version": DISPLAY_SCHEMA_VERSION,
            "resource_version": chosen,
            "pack_fingerprint": pack_fingerprint,
        }
    )


#: 显示出口的查询参数类型（受控枚举；非法值按 ``invalid_request`` 拒绝）
DisplayKindParam = Literal[
    "capabilities", "session", "draft", "review", "profile", "intent", "progress", "result", "runs"
]
LocaleParam = Literal["zh", "en"]
