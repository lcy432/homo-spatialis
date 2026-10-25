"""服务的显示出口（``GET /api/v1/display``、``GET /api/v1/display/geography``）与即时回复的显示附层。

* 读取只鉴权、读取与纯函数渲染：原读取函数给出 ``source``（与对应的业务接口完全相同），显示文字来自两处——当场生成的部分（服务
  视图、能力清单、会话）在记录器里重新生成一次，只绑定这次当场生成的文字对象；生成时就保存的私有绑定（审阅、确认画像、预检查、
  进度、结果、停下的说明）从服务库读，内嵌的这些历史正文只用它们自己保存的绑定与资源版本，当场视图不接管。不保存答案、不预检查、
  不恢复、不重放 E1、不调用模型；读取时不补写绑定。
* ``resource_version``：历史对象（审阅、画像、运行意图、进度、结果）是它绑定的版本（运行意图与进度看这次运行预检查时绑定的版本；
  还没有绑定就是空），只有当场生成的对象（能力清单、会话、草稿、运行列表）是当前登记的版本；每段文字实际用的版本写在字段上。
* 显示资源坏了、登记表读不出来：显示附层照实标缺失（``resources_invalid``），原业务回复、状态码与中文原文不变。
* 无权、删除、过期与原读取接口相同（他人的编号与不存在的一样 404）；审阅还核对所属草稿的期限与修订。
* 即时回复（错误、确认结果）随回复带中英两种文字（``DisplayAttachment``）；未登录的错误也有，不依赖先登录成功。
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Literal

from common.product_contracts import (
    DISPLAY_SCHEMA_VERSION,
    DisplayAcknowledgement,
    DisplayAnswerState,
    DisplayAttachment,
    DisplayEnvelope,
    DisplayText,
    GeographyDisplayBundle,
    LocalizedText,
    MetricDisplaySpec,
    ProductModel,
    ProfileReview,
    ReviewReply,
)
from product.display import messages
from product.display.catalog import registry
from product.display.document import (
    binding_set,
    capture,
    current_or_unusable,
    doc_version,
    json_fingerprint,
    load_or_unusable,
    view_set,
)
from product.display.render import BindingSet, display_fields, field_key, resolve_text
from product.display.text import Recorder
from product.interests import answer_state_texts
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
        zh="mean_j 的合法数值域是 [0, 1]（minimum / maximum）；超出这个范围是契约错误，不截断",
        en="The valid range of mean_j is [0, 1] (minimum / maximum); a value outside it is a contract error, not something "
        "to clip",
    ),
    LocalizedText(
        zh="图例窗口按 ADR 0045 在同一份结果之内适度放大：同一结果的所有国家、两种地图共用一个窗口，图例写明窗口两端；不暗示最低的"
        "城市是零分、最高的是满分，分数与比较结论不变",
        en="Per ADR 0045 the legend window may zoom within one result: all countries and both map modes of that result share "
        "one window and the legend states its two ends; it must not suggest that the lowest city scored zero or the "
        "highest scored full marks, and the scores and comparisons do not change",
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
    *,
    version: str | None,
    metrics: tuple[MetricDisplaySpec, ...] = (),
    acknowledgements: tuple[DisplayAcknowledgement, ...] = (),
    answer_states: tuple[DisplayAnswerState, ...] = (),
) -> DisplayEnvelope:
    body = _dump(source)
    live = [s for s in sets if s is not None]
    fields, status, _ = display_fields(body, live, locale, user_text=USER_TEXT.get(kind, ()))
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
        acknowledgements=acknowledgements,
        answer_states=answer_states,
    )


def _view(
    source: ProductModel,
    rec: Recorder,
    ctx: ServiceContext,
    kind: str,
    exclude: tuple[str, ...] = (),
) -> BindingSet:
    """当场生成的部分：只绑定这次生成的文字对象；``exclude`` 是内嵌的历史正文的位置（它们只用自己保存的绑定）。"""
    return view_set(
        _dump(source),
        rec,
        pack_fingerprint=_pack(ctx),
        user_text=USER_TEXT.get(kind, ()),
        exclude=exclude,
    )


def _same_pack_meta(view: BindingSet, body: Any, inner: str) -> BindingSet:
    """保存的草稿里自带的资料包说明（``<inner>/meta/data_pack``）：与外层当前的资料包块完全相同时，那就是同一个资料包的同一段说明，
    按明确的位置对（``/meta/data_pack/…`` → ``<inner>/meta/data_pack/…``）带过外层的绑定；不同就不带（照实缺失）。"""
    stored = ((body.get(inner.strip("/")) or {}).get("meta") or {}).get("data_pack")
    if stored is None or stored != (body.get("meta") or {}).get("data_pack"):
        return view
    extra = tuple(
        {**b, "pointer": inner + b["pointer"]}
        for b in view.bindings
        if b["pointer"].startswith("/meta/data_pack/")
    )
    return dataclasses.replace(view, bindings=view.bindings + extra)


def _same(stored: str | None, current: Any) -> bool:
    return stored is not None and json.loads(stored) == current


def _pair(
    zh: str | None, candidate: dict[str, Any] | None, bset: BindingSet, key: str
) -> DisplayText:
    """一段不在消息位置上的显示文字（中文与英文）。没有中文就是旧对象没有保存它：照实缺失。"""
    if zh is None:
        reason = "binding_unverified" if not bset.verified else "no_binding"
        return DisplayText(
            key=key, status="missing", missing_reason=reason, resource_version=bset.resource_version
        )
    found, en, reason = resolve_text(zh, candidate, bset, "en")
    return DisplayText(
        key=found or key,
        zh=zh,
        en=en,
        status="provided" if en is not None else "missing",
        missing_reason=reason,  # type: ignore[arg-type]
        resource_version=bset.resource_version,
    )


def _answer_states(ctx: ServiceContext, version: str | None) -> tuple[DisplayAnswerState, ...]:
    """兴趣与口味的每种正式作答状态的名称与含义（按状态，不按列表顺序）；英文用 ``version`` 那一版资源。"""
    texts, rec = capture(answer_state_texts)
    res, invalid = load_or_unusable(_pack(ctx), version)
    bset = BindingSet(bindings=(), resources=res, resource_version=version, invalid=invalid)
    out = []
    for answer, state, name, meaning in texts:
        key = f"answer_state:{answer}.{state}"
        out.append(
            DisplayAnswerState(
                answer=answer,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                name=_pair(name, rec.get(name), bset, key + ".name"),
                meaning=_pair(meaning, rec.get(meaning), bset, key + ".meaning"),
            )
        )
    return tuple(out)


def _acknowledgements(
    ctx: ServiceContext, review: ProfileReview, doc_json: str | None
) -> tuple[DisplayAcknowledgement, ...]:
    """审阅的知悉项说明：与 ``pending_confirmations`` 一一对应、同样的顺序；文字与绑定是生成审阅时保存的（旧审阅没有就照实缺失）。"""
    doc = json.loads(doc_json) if doc_json else None
    entries = {a["ack_id"]: a for a in (doc or {}).get("acknowledgements", ())}
    bset = binding_set(doc, _dump(review)) if doc is not None else None
    if bset is None:
        bset = BindingSet(bindings=(), resources=None, resource_version=None)
    refs = {s.ref: i for i, s in enumerate(review.statements)}
    out = []
    for ack in review.pending_confirmations:
        pointer = None
        if ack.startswith("ack:retained:"):
            i = refs.get(ack.split(":", 2)[2])
            pointer = None if i is None else f"/review/statements/{i}"
        entry = entries.get(ack) or {}
        cand = entry.get("candidate")
        out.append(
            DisplayAcknowledgement(
                ack_id=ack,
                text=_pair(
                    entry.get("zh"),
                    cand,
                    bset,
                    field_key(cand) if cand else ack,
                ),
                statement_pointer=pointer,
            )
        )
    return tuple(out)


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
        view = _view(cap, rec, ctx, kind)
        return _build(
            kind,
            None,
            cap,
            [view],
            locale,
            version=view.resource_version,
            answer_states=_answer_states(ctx, view.resource_version),
        )
    if kind == "session":
        sv, rec = capture(actions.session_view, ctx, principal_id, session_secret, session_expires)
        view = _view(sv, rec, ctx, kind)
        return _build(kind, None, sv, [view], locale, version=view.resource_version)
    if kind == "runs":
        rl, rec = capture(actions.runs, ctx, principal_id)
        view = _view(rl, rec, ctx, kind)
        return _build(kind, None, rl, [view], locale, version=view.resource_version)
    assert ident is not None
    if kind == "draft":
        dv, rec = capture(actions.get_draft, ctx, principal_id, ident)
        view = _same_pack_meta(_view(dv, rec, ctx, kind), _dump(dv), "/draft")
        return _build(
            kind,
            ident,
            dv,
            [view],
            locale,
            version=view.resource_version,
            answer_states=_answer_states(ctx, view.resource_version),
        )
    if kind == "review":
        return _review(ctx, principal_id, ident, locale)
    if kind == "profile":
        row = ctx.store.profile(principal_id, ident)
        pv, rec = capture(actions.profile_view, ctx, principal_id, ident)
        stored = binding_set(row["display_body"], _dump(pv.confirmed), prefix="/confirmed")
        view = _view(pv, rec, ctx, kind, exclude=("/confirmed",))
        return _build(
            kind, ident, pv, [stored, view], locale, version=doc_version(row["display_body"])
        )
    if kind == "intent":
        row = ctx.store.intent(principal_id, ident)
        iv, rec = capture(actions.view, ctx, principal_id, row)
        body = _dump(iv)
        sets: list[BindingSet | None] = []
        historical: list[str] = []
        for part, column, stored_body in (
            ("preflight", "preflight_display", "preflight_body"),
            ("progress", "progress_display", "progress_body"),
            ("explanation", "explanation_display", "explanation"),
        ):
            if body.get(part) is not None and _same(row[stored_body], body[part]):
                historical.append(f"/{part}")
                sets.append(binding_set(row[column], body[part], prefix=f"/{part}"))
        sets.append(_view(iv, rec, ctx, kind, exclude=tuple(historical)))
        bound = doc_version(row["preflight_display"]) if body.get("preflight") is not None else None
        return _build(kind, ident, iv, sets, locale, version=bound)
    if kind == "progress":
        pv, rec = capture(actions.progress, ctx, principal_id, ident)
        row = ctx.store.intent_by_run(principal_id, ident)
        if row["progress_body"] is not None:
            sets = [binding_set(row["progress_display"], _dump(pv))]
            bound = doc_version(row["progress_display"])
        else:
            sets = [_view(pv, rec, ctx, kind)]  # 还没有快照：按这次运行保存的设置当场生成的最小快照
            bound = doc_version(row["preflight_display"])
        return _build(kind, ident, pv, sets, locale, version=bound)
    if kind == "result":
        res, _ = capture(actions.result, ctx, principal_id, ident)
        row = ctx.store.intent_by_run(principal_id, ident)
        stored = binding_set(row["result_display"], _dump(res))
        return _build(
            kind,
            ident,
            res,
            [stored],
            locale,
            version=doc_version(row["result_display"]),
            metrics=(metric_spec(row),),
        )
    raise ServiceFailure("invalid_request")


def _review(ctx: ServiceContext, principal_id: str, review_id: str, locale: str) -> DisplayEnvelope:
    """审阅：不只看审阅记录有没有被删——所属草稿要还在、没过期，而且还是审阅的那一版（改了新修订，旧审阅作废）。草稿删了或过期
    就与草稿读取一样回答（404 / 410），草稿还在而审阅作废了才是 ``revision_conflict``。审阅正文、知悉项说明与作答状态都用生成
    审阅时绑定的那一版资源。"""
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
    bound = doc_version(row["display_body"])
    return _build(
        "review",
        review_id,
        reply,
        [stored, _view(reply, rec, ctx, "review", exclude=("/review",))],
        locale,
        version=bound,
        acknowledgements=_acknowledgements(ctx, review, row["display_body"]),
        answer_states=_answer_states(ctx, bound),
    )


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
    """给即时回复附上中英文字。这一步出任何问题都只让 ``display`` 为空（页面照中文原文显示），原回复、状态码与业务结果不变——
    最外层的错误回复与已经保存的确认都不能因为显示附层失败而变成 500。"""
    try:
        made = attachment(reply, rec, pack_fingerprint=pack_fingerprint, user_text=user_text)
    except Exception:
        return reply
    return reply.model_copy(update={"display": made})


# ---------------------------------------------------------------------- 地理显示清单


def geography(
    ctx: ServiceContext, pack_fingerprint: str, version: str | None
) -> GeographyDisplayBundle:
    """按资料包与资源版本读地理清单。资料包没有登记：404；明确请求的版本不存在：404（不回退当前版本）；登记了但这个版本没有
    地理资源：``unavailable``、城市为空（仍可从城市列表访问）；这一版资源校验没通过：同样 ``unavailable``，并写明原因。省略版本时
    用登记的当前版本。登记表读不出来时按没有登记处理（明确请求的版本照样可以读）。"""
    try:
        entry = registry().get(pack_fingerprint)
    except Exception:
        entry = None
    if version is None:
        if entry is None:
            raise ServiceFailure("not_found")
        chosen, _ = current_or_unusable(pack_fingerprint)
    else:
        chosen = version
    if chosen is None:
        raise ServiceFailure("not_found")
    res, invalid = load_or_unusable(pack_fingerprint, chosen)
    if invalid:
        return GeographyDisplayBundle(
            display_schema_version=DISPLAY_SCHEMA_VERSION,
            resource_version=chosen,
            pack_fingerprint=pack_fingerprint,
            status="unavailable",
            notes=(
                LocalizedText(
                    zh="这一版显示资源校验没有通过，暂时不能提供地理清单；城市仍可从列表访问",
                    en="This display resource version failed its integrity check, so no geography list can be "
                    "provided for now; the cities are still available from the list",
                ),
            ),
        )
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
