"""服务的业务操作（API 调用它们；它们调用 E3 的正式入口与服务库，不调用 ``chdir``、不推进流程、不调用规划器）。

* 草稿：服务器分配编号与修订号；带版本保存，旧版本冲突，不覆盖新答案。
* 审阅：``product.answers.review_draft``，绑定草稿编号、修订号、内容与所用资料包。
* 确认画像：只收审阅引用与知悉项；服务器重新校验后调用 ``product.answers.confirm_profile``（不接受浏览器构造的确认画像）。
* 运行意图：一份确认画像 + 用户选的运行选项（每城分身数、年数、费用上限，可以省略）→ 服务端按 ``RunPolicy`` 校验并保存这次的有效
  设置（``ExecutionSpec``）；排队做运行前检查（worker 做，不扣次数）。之后的检查、确认、执行、进度、结果与恢复都用这一份设置，
  不回退到服务现在的默认值。
* 确认开始：复用 ``product.bridge.submission_problems`` 的核对，稳定运行编号用 ``product.bridge.run_id_for``——与之后 LangGraph 登记
  用的编号一致；预留次数、排队在服务库的一个短事务里完成。
* 一次开始（DECISIONS/0048）：设置预览只读（有效设置、范围说明、必要知悉项与同意身份）；准备模拟时带上 ``start``，服务端重新核对预览
  并把授权保存在意图上；检查通过之后 worker 用 ``start_authorized`` 走与“确认开始”同一套核对与同一个提交事务（授权同时标为已用）。

会产生或恢复正文的写入都带着请求开始时读到的数据代次（``generation``），服务库在写回事务里核对（见 ``product.service.store``）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.product_contracts import (
    START_SCOPE_VERSION,
    AgeAnswer,
    AllocationAnswer,
    CashAnswer,
    CitiesAnswer,
    ConfirmedProfile,
    ConfirmedProfileSummary,
    ConfirmedProfileView,
    ConfirmedRef,
    CostStatus,
    DeletionReceipt,
    DraftMigrateRequest,
    DraftSaveRequest,
    DraftSummary,
    DraftView,
    FreeTextAnswer,
    HealthAnswers,
    HealthItemAnswer,
    OccupationAnswer,
    PreflightRef,
    ProductCapabilities,
    ProductMeta,
    ProductPreflight,
    ProductResult,
    ProfileConfirmReply,
    ProfileConfirmRequest,
    ProfileList,
    ProfileReview,
    ReviewReply,
    RunIntentCreateRequest,
    RunIntentView,
    RunList,
    RunProgress,
    RunReceipt,
    RunStartPreview,
    RunStartPreviewRequest,
    RunSubmission,
    RunSubmitReply,
    RunSubmitRequest,
    SessionView,
    StartAcknowledgement,
    UserInputDraft,
    content_fingerprint,
)
from product.answers import (
    ConfirmationRefusedError,
    ack_display,
    confirm_profile,
    confirmation_problems,
    draft_fingerprint,
    issue,
    review_draft,
)
from product.bridge import run_id_for, submission_problems
from product.capabilities import build_capabilities
from product.display import Code, Num, t
from product.display import messages as display_messages
from product.display.document import binding_document, capture
from product.execution import ExecutionSpec, RunOptions, RunOptionsError, RunPolicy, service_pack
from product.interests import confirmed_scope_lines
from product.migration import MigrationError, migrate
from product.pack import DataPack, load_pack
from product.pack_facts import pack_lines
from product.service.clock import Clock, system_clock
from product.service.config import REPO, ServiceConfig, check_data_dir
from product.service.errors import ServiceFailure
from product.service.explain import service_explanation
from product.service.store import (
    INTENT_CONTENT,
    ServiceStore,
    StartRequest,
    expired_subject,
    intent_due,
    start_authorization,
)
from product.service.tokens import csrf_for, digest, server_key
from product.service.views import (
    draft_view,
    intent_view,
    quota_view,
    rules_view,
    run_summary,
    settings_from_spec,
    stage_text,
)

KEPT_AFTER_DELETE = {
    "deletion.kept.identity": "不透明的服务身份与运行编号",
    "deletion.kept.quota": "使用次数的记录（删除不会重置次数）",
    "deletion.kept.cost": "必要的费用摘要（发送不确定的金额照原样保留，不改成 0）",
    "deletion.kept.tombstone": "删除时间与防止旧请求复活的记录",
}


def kept_after_delete() -> tuple[str, ...]:
    return tuple(t(k, v) for k, v in KEPT_AFTER_DELETE.items())


@dataclass
class ServiceContext:
    """一个服务实例共用的东西：配置、服务库、时钟、HMAC 密钥、当前的资料包、运行选项政策与默认设置（只用于显示规则与预填草稿；
    每次运行用它自己保存的设置）。"""

    config: ServiceConfig
    store: ServiceStore
    clock: Clock
    repo: Path
    data_root: Path
    key: bytes
    pack: DataPack
    policy: RunPolicy
    default_spec: ExecutionSpec

    @classmethod
    def open(
        cls, config: ServiceConfig, *, clock: Clock = system_clock, repo: Path = REPO
    ) -> ServiceContext:
        data_root = config.data_root(repo)
        check_data_dir(data_root)  # 先查路径长度，再建服务库与密钥：放不下运行归档就不启动
        store = ServiceStore(
            data_root / "service.sqlite",
            clock,
            runs_total=config.runs_total,
            intent_days=config.intent_retention_days,
            profile_days=config.profile_retention_days,
        )
        key = server_key(data_root / "server.key")
        pack = load_pack((repo / config.pack_path).resolve())
        if config.profile != "test" and pack.body.status != "real_data_frozen":
            # 生产入口显式配置 E3B 资料包，不回退到合成资料包
            raise ValueError(f"{config.pack_path} 不是冻结的真实资料包：服务不使用合成资料包")
        policy = RunPolicy.from_config(config, pack, repo)
        # 启动时就按默认选项算一遍：规划器设定、报价与资料包对不上就不启动服务
        default_spec = policy.derive(policy.default_options())
        return cls(
            config=config,
            store=store,
            clock=clock,
            repo=repo,
            data_root=data_root,
            key=key,
            pack=pack,
            policy=policy,
            default_spec=default_spec,
        )

    def meta(self) -> ProductMeta:
        return self.pack.meta()

    def person_ref(self, principal_id: str) -> str:
        """分身用的人物编号：由服务身份派生的不透明编号，不是访问凭证（浏览器从不提交它）。"""
        return "p" + digest(principal_id)[:10]

    def workflow_key(self, principal_id: str, intent_id: str) -> str:
        """内部幂等键：由服务身份与意图身份决定，浏览器控制不了，也跨不了别人的命名空间。"""
        return "svc-" + digest(f"{principal_id}|{intent_id}")[:40]

    def run_dir(self, intent_id: str) -> Path:
        return self.data_root / "runs" / intent_id

    def csrf(self, session_secret: str) -> str:
        return csrf_for(self.key, session_secret)


def intent_spec(row: Mapping[str, Any]) -> ExecutionSpec:
    """这次运行意图保存的有效设置。缺了或坏了就明确拒绝——不回退到服务现在的默认值。"""
    try:
        return ExecutionSpec.model_validate_json(row["execution_body"])
    except (TypeError, ValueError) as exc:
        raise ServiceFailure("run_settings_unavailable") from exc


def intent_options(row: Mapping[str, Any]) -> RunOptions:
    try:
        return RunOptions.model_validate_json(row["run_options"])
    except (TypeError, ValueError) as exc:
        raise ServiceFailure("run_settings_unavailable") from exc


# ---------------------------------------------------------------------- 会话与能力


def session_view(
    ctx: ServiceContext, principal_id: str, session_secret: str, expires: str
) -> SessionView:
    return SessionView(
        meta=ctx.meta(),
        session_expires_at=expires,
        csrf_token=ctx.csrf(session_secret),
        quota=quota_view(ctx.store.quota(principal_id)),
        rules=rules_view(ctx.config, ctx.policy, ctx.default_spec),
        drafts=tuple(
            DraftSummary(
                draft_id=r["draft_id"],
                revision=r["revision"],
                updated_at=r["updated_at"],
                expires_at=r["expires_at"],
            )
            for r in ctx.store.drafts(principal_id)
        ),
        open_intent_id=ctx.store.open_intent(principal_id),
    )


def capabilities(ctx: ServiceContext) -> ProductCapabilities:
    return build_capabilities(ctx.pack)


# ---------------------------------------------------------------------- 草稿、审阅与确认


def blank_draft(ctx: ServiceContext, draft_id: str, revision: int = 1) -> UserInputDraft:
    """一份空白草稿：全部未答（未答不是 0）；城市预先选好服务允许的全部都会区（用户可以去掉，不能加别的）。"""
    b = ctx.pack.body
    return UserInputDraft(
        meta=ctx.meta(),
        draft_id=draft_id,
        revision=revision,
        cities=CitiesAnswer(state="provided", city_refs=tuple(ctx.policy.cities_allowed)),
        occupation=OccupationAnswer(state="unanswered"),
        age=AgeAnswer(state="unanswered"),
        cash=CashAnswer(state="unanswered"),
        six_dimensions=AllocationAnswer(),
        material=AllocationAnswer(),
        health=HealthAnswers(
            questionnaire_version=b.health_questionnaire.version,
            items=tuple(
                HealthItemAnswer(question_id=q.question_id)
                for q in b.health_questionnaire.questions
            ),
        ),
        free_text=FreeTextAnswer(state="unanswered"),
    )


def create_draft(ctx: ServiceContext, principal_id: str, *, generation: int) -> DraftView:
    row = ctx.store.create_draft(
        principal_id,
        lambda draft_id: blank_draft(ctx, draft_id).model_dump_json(),
        ctx.config.draft_retention_days,
        generation=generation,
    )
    return draft_view(ctx.meta(), row, ctx.pack)


def get_draft(ctx: ServiceContext, principal_id: str, draft_id: str) -> DraftView:
    return draft_view(ctx.meta(), ctx.store.draft(principal_id, draft_id), ctx.pack)


def save_draft(
    ctx: ServiceContext,
    principal_id: str,
    draft_id: str,
    req: DraftSaveRequest,
    *,
    generation: int,
) -> DraftView:
    if req.draft.draft_id != draft_id or req.draft.revision != req.expected_revision:
        raise ServiceFailure("invalid_request")
    row = ctx.store.save_draft(
        principal_id,
        draft_id,
        req.expected_revision,
        lambda revision: req.draft.model_copy(update={"revision": revision}).model_dump_json(),
        ctx.config.draft_retention_days,
        generation=generation,
    )
    return draft_view(ctx.meta(), row, ctx.pack)


def migrate_draft(
    ctx: ServiceContext,
    principal_id: str,
    draft_id: str,
    req: DraftMigrateRequest,
    *,
    generation: int,
) -> DraftView:
    """按迁移说明改写草稿（``product.migration``）：换成当前版本、清空新问卷不再收集的答案（要用户明确同意），写成新的修订；
    之后要重新审阅与确认。修订号不是当前的就冲突，不改。已经确认的画像与已经开始的运行不受影响。"""
    row = ctx.store.draft(principal_id, draft_id)
    if row["revision"] != req.expected_revision:
        raise ServiceFailure("revision_conflict")
    try:
        migrated = migrate(
            ctx.pack,
            UserInputDraft.model_validate_json(row["body"]),
            clear_removed=req.clear_removed,
            allocation_choice=req.allocation_choice,
        )
    except MigrationError as exc:
        raise ServiceFailure(exc.code) from exc
    row = ctx.store.save_draft(
        principal_id,
        draft_id,
        req.expected_revision,
        lambda revision: migrated.model_copy(update={"revision": revision}).model_dump_json(),
        ctx.config.draft_retention_days,
        generation=generation,
    )
    return draft_view(ctx.meta(), row, ctx.pack)


def delete_draft(ctx: ServiceContext, principal_id: str, draft_id: str) -> DeletionReceipt:
    ctx.store.delete_draft(principal_id, draft_id)
    return DeletionReceipt(
        meta=ctx.meta(),
        scope="draft",
        target_ref=draft_id,
        status="deleted",
        note_zh="草稿、它的审阅与由它确认的画像已经删除；已经开始检查或运行的模拟用自己保存的副本，不受影响",
        kept_zh=(),
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def review(
    ctx: ServiceContext, principal_id: str, draft_id: str, *, generation: int
) -> ReviewReply:
    """审阅。写回时服务库在事务里重新核对草稿（还在、没过期、修订号没变）与数据代次：审阅进行中草稿被删或“删除全部”，就不写回。"""
    row = ctx.store.draft(principal_id, draft_id)
    draft = UserInputDraft.model_validate_json(row["body"])

    def reviewed() -> tuple[ProfileReview, list[tuple[str, str]]]:
        # 知悉项说明与审阅在同一次生成里：每一项的文字与它的绑定跟着审阅一起保存（显示附层的 acknowledgements）
        r = review_draft(ctx.pack, draft)
        return r, [(k, ack_display(k)) for k in r.pending_confirmations]

    (result, acks), rec = capture(reviewed)
    display = binding_document(
        result.model_dump(mode="json"),
        rec,
        pack_fingerprint=ctx.pack.fingerprint,
        user_text=display_messages.REVIEW,
        acknowledgements=[
            {"ack_id": k, "zh": text, "candidate": rec.get(text)} for k, text in acks
        ],
    )
    ctx.store.save_review(
        principal_id,
        review_id=result.review_id,
        draft_id=draft_id,
        revision=row["revision"],
        draft_fingerprint=draft_fingerprint(draft),
        pack_fingerprint=ctx.pack.fingerprint,
        body=result.model_dump_json(),
        generation=generation,
        display_body=json.dumps(display, ensure_ascii=False),
    )
    return ReviewReply(meta=ctx.meta(), review=result, reviewed_revision=row["revision"])


def _review_stale(ctx: ServiceContext) -> ProfileConfirmReply:
    return ProfileConfirmReply(
        meta=ctx.meta(),
        status="refused",
        issues=(
            issue(
                "review_stale",
                "review_id",
                t(
                    "issue.review_stale.message",
                    "确认引用的审阅不是当前这一版草稿的审阅（草稿或资料包在审阅之后变了）",
                ),
                t("issue.review_stale.impact", "没有确认"),
                t("issue.review_stale.next", "重新审阅当前的草稿后再确认"),
            ),
        ),
    )


def confirm(
    ctx: ServiceContext, principal_id: str, req: ProfileConfirmRequest, *, generation: int
) -> ProfileConfirmReply:
    """确认画像。服务器按保存的草稿与审阅重新校验：审阅之后草稿改过、资料包换过或引用对不上都拒绝。写回事务里再核对一次草稿、审阅
    与数据代次（确认进行中被删除或改动就不写回）。同一份画像重复确认不续期。"""
    row = ctx.store.draft(principal_id, req.draft_id)  # 草稿删了或过期，就没有可以确认的东西
    try:
        rev = ctx.store.review(principal_id, req.review_id)
    except ServiceFailure as exc:
        if exc.code == "revision_conflict":
            return _review_stale(ctx)  # 草稿改了新修订，旧审阅已经作废
        raise
    draft = UserInputDraft.model_validate_json(row["body"])
    stale = (
        rev["draft_id"] != req.draft_id
        or rev["draft_revision"] != req.draft_revision
        or row["revision"] != req.draft_revision
        or rev["draft_fingerprint"] != draft_fingerprint(draft)
        or rev["pack_fingerprint"] != ctx.pack.fingerprint
    )
    if stale:
        return _review_stale(ctx)
    try:
        confirmed, rec = capture(
            confirm_profile,
            ctx.pack,
            draft,
            person_ref=ctx.person_ref(principal_id),
            acknowledged=req.acknowledged,
        )
    except ConfirmationRefusedError as exc:
        return ProfileConfirmReply(meta=ctx.meta(), status="refused", issues=exc.issues)
    display = binding_document(
        confirmed.model_dump(mode="json"),
        rec,
        pack_fingerprint=ctx.pack.fingerprint,
        user_text=display_messages.PROFILE,
    )
    try:
        ctx.store.save_profile(
            principal_id,
            confirmed_id=confirmed.confirmed_id,
            draft_id=req.draft_id,
            revision=req.draft_revision,
            review_id=req.review_id,
            fingerprint=confirmed.content_fingerprint,
            body=confirmed.model_dump_json(),
            generation=generation,
            display_body=json.dumps(display, ensure_ascii=False),
        )
    except ServiceFailure as exc:
        if exc.code == "revision_conflict":
            return _review_stale(ctx)  # 确认进行中草稿又被改了
        raise
    saved = ctx.store.profile(principal_id, confirmed.confirmed_id)
    return ProfileConfirmReply(
        meta=ctx.meta(),
        status="confirmed",
        confirmed=confirmed,
        profile_summary=profile_summary(saved),
    )


def profile_summary(row: Mapping[str, Any]) -> ConfirmedProfileSummary:
    """服务库里这份确认画像的日期：首次确认时间与可以用来准备新模拟的截止时间（重复确认、查询都不续期）。"""
    return ConfirmedProfileSummary(
        confirmed_id=row["confirmed_id"],
        content_fingerprint=row["content_fingerprint"],
        draft_id=row["draft_id"],
        draft_revision=row["draft_revision"],
        first_confirmed_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def profiles(ctx: ServiceContext, principal_id: str) -> ProfileList:
    """本人还能用来准备新模拟的确认画像摘要（刷新、换设备后读回日期）。"""
    return ProfileList(
        meta=ctx.meta(),
        profiles=tuple(profile_summary(r) for r in ctx.store.profiles(principal_id)),
    )


def profile_view(ctx: ServiceContext, principal_id: str, confirmed_id: str) -> ConfirmedProfileView:
    """本人的一份确认画像与它的日期。别人的、不存在的、已删除的都是 ``not_found``；过期的是 ``expired``（正文不再返回）。"""
    row = ctx.store.profile(principal_id, confirmed_id)
    return ConfirmedProfileView(
        meta=ctx.meta(),
        summary=profile_summary(row),
        confirmed=ConfirmedProfile.model_validate_json(row["body"]),
    )


# ---------------------------------------------------------------------- 运行意图


def view(ctx: ServiceContext, principal_id: str, row: Mapping[str, Any]) -> RunIntentView:
    return intent_view(
        ctx.meta(),
        _expired_view(ctx, row),
        ctx.store.quota(principal_id),
        started=ctx.store.quota_state(row["intent_id"])["used"],
        live_calls_enabled=ctx.config.live_calls_enabled,
        intent_days=ctx.config.intent_retention_days,
    )


def _expired_view(ctx: ServiceContext, row: Mapping[str, Any]) -> Mapping[str, Any]:
    """API 访问时就按到期规则隐藏（不等 worker 的清理跑过）：到期的显示为已过期，内容不再返回。只读，不写库。"""
    if not intent_due(row, ctx.store.now(), ctx.config.intent_retention_days):
        return row
    data = dict(row)
    data.update({c: None for c in INTENT_CONTENT}, stage="expired")
    return data


def _usable_profile(ctx: ServiceContext, principal_id: str, confirmed_id: str) -> ConfirmedProfile:
    """一份还能用来准备新模拟的确认画像（在、没删、没过期，而且是按当前问卷确认的）。"""
    row = ctx.store.profile(principal_id, confirmed_id)
    confirmed = ConfirmedProfile.model_validate_json(row["body"])
    if confirmation_problems(ctx.pack, confirmed):
        raise ServiceFailure("preflight_stale")
    return confirmed


def _settings(
    ctx: ServiceContext,
    confirmed: ConfirmedProfile,
    req: RunIntentCreateRequest | RunStartPreviewRequest,
) -> tuple[RunOptions, ExecutionSpec]:
    """城市来自确认画像；每城分身数、年数与费用上限按服务政策校验（越界就拒绝），省略的用默认值。"""
    try:
        options = ctx.policy.resolve(
            tuple(confirmed.cities),
            clones_per_city=req.clones_per_city,
            years=req.years,
            max_cost_cny=req.max_cost_cny,
        )
        return options, ctx.policy.derive(options)
    except RunOptionsError as exc:
        raise ServiceFailure("run_options_invalid", field=exc.field) from exc


# ---------------------------------------------------------------------- 一次开始（DECISIONS/0048）

START_SCOPE = {
    "start.scope.check": "开始之前，服务在后台做运行前检查（不使用次数、不调用模型）；关闭页面、刷新或断线都不影响",
    "start.scope.gaps": "检查可能发现某些城市缺少某项评分要读的资料：这类普通资料缺口不会再让你确认，结果里照实标为不完整或缺失"
    "（不补 0）",
    "start.scope.stop": "检查没有通过，或资料、报价、规模、费用上限与要知悉的事项变了：服务停下并说明原因，不替你重新确认",
    "start.scope.once": "这次确认只开始一次：停下之后的恢复要你自己点，结束之后再模拟要重新开始",
}


def start_acknowledgements(spec: ExecutionSpec) -> tuple[StartAcknowledgement, ...]:
    """开始之前要知悉的几项（同意身份只记编号；文字随语言变）。用真实模型时多一项费用上限。"""
    acks = [
        StartAcknowledgement(
            ack_id="ack:start.one_run",
            text_zh=t(
                "start.ack.one_run",
                "真正开始（第一次进入模拟）时用掉 1 次启动机会；开始之前检查没有通过、取消或过期都不扣",
            ),
        ),
        StartAcknowledgement(
            ack_id="ack:start.auto_start",
            text_zh=t(
                "start.ack.auto_start",
                "检查通过就按这些设置自动开始，不再询问；检查没有通过或条件变了就停下说明",
            ),
        ),
        StartAcknowledgement(
            ack_id="ack:start.scope",
            text_zh=t(
                "start.ack.scope",
                "我知道这次模拟的范围与限制：结果只说明在这些设定下的生活，不是真实城市的排名",
            ),
        ),
    ]
    if spec.planner_kind == "llm":
        acks.append(
            StartAcknowledgement(
                ack_id="ack:start.cost_cap",
                text_zh=t(
                    "start.ack.cost_cap",
                    "费用上限 {amount} {currency} 是停机上限，不是预计费用：不够发下一次请求时就停下；用量不确定的请求按预留保留",
                    amount=Num(f"{spec.max_cost}"),
                    currency=Code(f"{spec.currency}"),
                ),
            )
        )
    return tuple(acks)


def consent_fingerprint(
    confirmed_id: str,
    confirmed_fingerprint: str,
    spec: ExecutionSpec,
    acks: tuple[StartAcknowledgement, ...],
) -> str:
    """一次开始的同意身份：确认画像、资料包、有效设置（含规划器、模型、报价与费用上限）、范围说明的业务版本与知悉项编号。不含任何
    文字，所以换语言不改它；这几样任何一样变了，旧的同意就不再成立。"""
    return content_fingerprint(
        {
            "scope_version": START_SCOPE_VERSION,
            "confirmed_id": confirmed_id,
            "confirmed_fingerprint": confirmed_fingerprint,
            "pack_fingerprint": spec.pack_fingerprint,
            "settings_fingerprint": spec.fingerprint,
            "acknowledged": sorted(a.ack_id for a in acks),
        }
    )


def _start_refusal(
    ctx: ServiceContext, principal_id: str, confirmed: ConfirmedProfile, spec: ExecutionSpec
) -> str | None:
    """此刻能不能按这份预览开始（只读）：付费调用没开、已经有一次条件不同的准备、次数不够。"""
    if spec.planner_kind == "llm" and not ctx.config.live_calls_enabled:
        return "paid_calls_disabled"
    open_id = ctx.store.open_intent(principal_id)
    same = None
    if open_id is not None:
        row = ctx.store.intent(principal_id, open_id)
        if (row["confirmed_id"], row["execution_fingerprint"]) != (
            confirmed.confirmed_id,
            spec.fingerprint,
        ):
            return "intent_conflict"
        same = row
    if (same is None or same["run_id"] is None) and ctx.store.quota(principal_id).available < 1:
        return "quota_exhausted"
    return None


def start_preview(
    ctx: ServiceContext, principal_id: str, req: RunStartPreviewRequest
) -> RunStartPreview:
    """设置预览（只读：不编译生活输入、不排队、不预留次数、不调用模型、不写库）。"""
    confirmed = _usable_profile(ctx, principal_id, req.confirmed_id)
    _, spec = _settings(ctx, confirmed, req)
    acks = start_acknowledgements(spec)
    refusal = _start_refusal(ctx, principal_id, confirmed, spec)
    return RunStartPreview(
        meta=ctx.meta(),
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id, content_fingerprint=confirmed.content_fingerprint
        ),
        settings=settings_from_spec(spec),
        planner=spec.planner_and_cost(),
        scope_version=START_SCOPE_VERSION,
        scope_zh=tuple(t(k, v) for k, v in START_SCOPE.items())
        + pack_lines("limits", ctx.pack.body.limits)
        + confirmed_scope_lines(ctx.pack, confirmed),
        acknowledgements=acks,
        start_allowed=refusal is None,
        start_refusal=refusal,  # type: ignore[arg-type]
        quota=quota_view(ctx.store.quota(principal_id)),
        consent_fingerprint=consent_fingerprint(
            confirmed.confirmed_id, confirmed.content_fingerprint, spec, acks
        ),
    )


def _start_request(req: RunIntentCreateRequest) -> StartRequest | None:
    if req.start is None:
        return None
    request = {
        "confirmed_id": req.confirmed_id,
        "clones_per_city": req.clones_per_city,
        "years": req.years,
        "max_cost_cny": req.max_cost_cny,
        "consent_fingerprint": req.start.consent_fingerprint,
        "acknowledged": sorted(req.start.acknowledged),
    }
    return StartRequest(
        consent_fingerprint=req.start.consent_fingerprint,
        acknowledged=tuple(sorted(req.start.acknowledged)),
        key=req.start.idempotency_key,
        request_fingerprint=content_fingerprint(request),
    )


def create_intent(
    ctx: ServiceContext, principal_id: str, req: RunIntentCreateRequest, *, generation: int
) -> tuple[RunIntentView, bool]:
    """准备一次模拟。城市来自确认画像；每城分身数、年数与费用上限按服务政策校验（越界在预留与任何调用之前就拒绝），省略的用默认值。
    这次的有效设置在这里就保存下来。

    带 ``start``（一次开始）：同一个键的重试先找回原来的意图（在任何别的核对之前，所以条件后来变了也找得回）；新的请求按当前的设置
    预览核对同意身份与知悉项，付费调用没开就拒绝；授权保存在意图上，检查通过之后由 worker 按它开始。"""
    start = _start_request(req)
    if start is not None:
        seen = ctx.store.intent_for_start(principal_id, start)
        if seen is not None:
            return view(ctx, principal_id, seen), False
    confirmed = _usable_profile(ctx, principal_id, req.confirmed_id)
    options, spec = _settings(ctx, confirmed, req)
    if start is not None:
        acks = start_acknowledgements(spec)
        expected = consent_fingerprint(
            confirmed.confirmed_id, confirmed.content_fingerprint, spec, acks
        )
        if start.consent_fingerprint != expected:
            raise ServiceFailure("start_consent_stale")
        if set(start.acknowledged) != {a.ack_id for a in acks}:
            raise ServiceFailure("start_acknowledgement_missing")
        if spec.planner_kind == "llm" and not ctx.config.live_calls_enabled:
            raise ServiceFailure("paid_calls_disabled")
    row, created = ctx.store.create_intent(
        principal_id,
        confirmed_id=confirmed.confirmed_id,
        confirmed_fingerprint=confirmed.content_fingerprint,
        execution_fingerprint=spec.fingerprint,
        execution_body=spec.model_dump_json(),
        run_options=options.model_dump_json(),
        workflow_key=lambda intent_id: ctx.workflow_key(principal_id, intent_id),
        generation=generation,
        start=start,
    )
    return view(ctx, principal_id, row), created


def get_intent(ctx: ServiceContext, principal_id: str, intent_id: str) -> RunIntentView:
    return view(ctx, principal_id, ctx.store.intent(principal_id, intent_id))


def _run_confirmed(ctx: ServiceContext, intent_id: str, confirmed_id: str) -> ConfirmedProfile:
    """这次运行自己保存的确认画像副本（排队检查时复制进工作目录；画像到期或草稿删了也不影响已经准备的运行）。"""
    path = ctx.run_dir(intent_id) / "ws" / "workflow" / "confirmed" / f"{confirmed_id}.json"
    try:
        return ConfirmedProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ServiceFailure("run_settings_unavailable") from exc


def submit(
    ctx: ServiceContext, principal_id: str, intent_id: str, req: RunSubmitRequest
) -> RunSubmitReply:
    """确认开始。先核对截止时间；再按保存的运行选项与服务现在的政策重算有效设置——资料包、规划器设定或报价变了，就回到排队检查
    （用户要看新的运行前说明再确认，不扣次数）；选项已经不被允许，就标为检查没通过。核对在事务之外做（读运行自己的资料快照、复用
    提交核对）；接受、预留、写运行编号与排队在一个短事务里。带着一次开始授权的意图由服务按授权开始，这里拒绝
    （``start_already_authorized``）。"""
    ctx.store.ensure_not_due(principal_id, intent_id)
    row = ctx.store.intent(principal_id, intent_id)
    if start_authorization(row) is not None:
        raise ServiceFailure("start_already_authorized")
    spec = intent_spec(row) if row["execution_body"] is not None else None
    if row["run_id"] is None and row["stage"] == "awaiting_confirmation":
        if spec is None:
            raise ServiceFailure("run_settings_unavailable")
        try:
            current = ctx.policy.derive(intent_options(row))
        except RunOptionsError as exc:
            explanation = service_explanation("options_not_allowed").model_dump_json()
            ctx.store.block_intent(principal_id, intent_id, explanation)
            raise ServiceFailure("run_options_invalid", field=exc.field) from exc
        if current.fingerprint != spec.fingerprint:
            ctx.store.recheck_intent(
                principal_id,
                intent_id,
                execution_fingerprint=current.fingerprint,
                execution_body=current.model_dump_json(),
            )
            raise ServiceFailure("preflight_stale")
        if spec.planner_kind == "llm" and not ctx.config.live_calls_enabled:
            raise ServiceFailure("paid_calls_disabled")
        if row["preflight_fingerprint"] != req.confirm_with or row["preflight_body"] is None:
            raise ServiceFailure("preflight_stale")
        run_id = _run_id(ctx, row, spec)
    else:
        run_id = row["run_id"] or ""
    status, run_id = ctx.store.submit_intent(
        principal_id,
        intent_id,
        confirm_with=req.confirm_with,
        key=req.idempotency_key,
        run_id=run_id,
        execution_fingerprint=row["execution_fingerprint"],
    )
    receipt = RunReceipt(
        meta=ctx.meta(),
        status=status,  # type: ignore[arg-type]
        run_id=run_id,
        idempotency_key=req.idempotency_key,
        service="hosted_service",
    )
    return RunSubmitReply(
        meta=ctx.meta(),
        receipt=receipt,
        intent=view(ctx, principal_id, ctx.store.intent(principal_id, intent_id)),
    )


def _run_id(ctx: ServiceContext, row: Mapping[str, Any], spec: ExecutionSpec) -> str:
    """提交前的最后核对（用户确认与按授权开始共用）：读这次运行自己的资料快照与确认画像副本，复用提交核对，得出稳定运行编号。"""
    intent_id = row["intent_id"]
    pre = ProductPreflight.model_validate_json(row["preflight_body"])
    confirmed = _run_confirmed(ctx, intent_id, row["confirmed_id"])
    try:
        pack = service_pack(ctx.run_dir(intent_id) / "ws" / spec.pack_path, spec)
    except (OSError, ValueError) as exc:
        raise ServiceFailure("data_pack_unavailable") from exc
    submission = RunSubmission(
        meta=pack.meta(),
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id,
            content_fingerprint=confirmed.content_fingerprint,
        ),
        preflight=PreflightRef(
            preflight_id=pre.preflight_id, preflight_fingerprint=pre.preflight_fingerprint
        ),
        idempotency_key=row["workflow_key"],
    )
    if submission_problems(pack, confirmed, pre, submission):
        raise ServiceFailure("preflight_stale")
    return run_id_for(submission)


def start_authorized(ctx: ServiceContext, row: Mapping[str, Any]) -> str | None:
    """worker 用：检查通过之后按一次开始的授权开始（DECISIONS/0048）。核对与用户点“确认开始”相同——有效设置按保存的选项与服务现在的
    政策重算、同意身份按这次的设置与现在的知悉规则重算、付费开关、预检查是不是授权的那一份设置、提交核对——接受、预留 1 次、写运行
    编号、排队与把授权标为已用在服务库的同一个短事务里（次数要到第一次进入 E1 才转为已使用）。成功返回 ``None``；授权不再成立返回
    作废原因（调用方作废授权，不替用户接受）：设置变了就回到排队检查（用户看新的运行前说明后重新确认），选项不再允许就标为检查没通过。
    意图已经取消、删除、过期或已经开始：什么也不做，返回 ``None``。"""
    auth = start_authorization(row)
    if auth is None or auth["state"] != "pending":
        return None
    try:
        return _start_checked(ctx, row, auth)
    except ServiceFailure as exc:
        after = ctx.store.intent_row(row["intent_id"])
        if (
            after is None
            or after["stage"] != "awaiting_confirmation"
            or after["run_id"] is not None
        ):
            return None  # 取消、删除、过期或已经开始：意图自己的阶段说明一切
        return {
            "quota_exhausted": "quota_exhausted",
            "data_pack_unavailable": "data_pack_unavailable",
        }.get(exc.code, "authorization_mismatch")


def _start_checked(ctx: ServiceContext, row: Mapping[str, Any], auth: dict[str, Any]) -> str | None:
    """``start_authorized`` 的核对与提交（服务库拒绝时抛出 ``ServiceFailure``，由调用方按意图现在的阶段处理）。"""
    principal_id, intent_id = row["principal_id"], row["intent_id"]
    spec = intent_spec(row)
    try:
        current = ctx.policy.derive(intent_options(row))
    except RunOptionsError:
        explanation = service_explanation("options_not_allowed").model_dump_json()
        ctx.store.block_intent(principal_id, intent_id, explanation)
        return "options_not_allowed"
    if current.fingerprint != spec.fingerprint:
        ctx.store.recheck_intent(
            principal_id,
            intent_id,
            execution_fingerprint=current.fingerprint,
            execution_body=current.model_dump_json(),
        )
        return "conditions_changed"
    expected = consent_fingerprint(
        row["confirmed_id"], row["confirmed_fingerprint"], spec, start_acknowledgements(spec)
    )
    if auth["consent_fingerprint"] != expected:
        return "consent_changed"
    if spec.planner_kind == "llm" and not ctx.config.live_calls_enabled:
        return "paid_calls_disabled"
    if row["preflight_fingerprint"] is None or row["preflight_body"] is None:
        return "authorization_mismatch"
    pre = ProductPreflight.model_validate_json(row["preflight_body"])
    planned = spec.planner_and_cost()
    if (
        pre.status != "ready"
        or (pre.clones_per_city, pre.years) != (len(spec.repeat_indexes), spec.years)
        or sorted(c.city_ref for c in pre.cities) != sorted(spec.cities)
        or (pre.planner.planner_kind, pre.planner.planner_id, pre.planner.call_limit)
        != (planned.planner_kind, planned.planner_id, planned.call_limit)
    ):
        return "authorization_mismatch"  # 检查出来的不是授权的那一份设置：不按它开始
    ctx.store.submit_intent(
        principal_id,
        intent_id,
        confirm_with=row["preflight_fingerprint"],
        key="start-authorization:" + auth["key_digest"][:40],
        run_id=_run_id(ctx, row, spec),
        execution_fingerprint=row["execution_fingerprint"],
        authorization=auth["consent_fingerprint"],
    )
    return None


def cancel(ctx: ServiceContext, principal_id: str, intent_id: str) -> RunIntentView:
    return view(ctx, principal_id, ctx.store.cancel_intent(principal_id, intent_id))


# ---------------------------------------------------------------------- 运行：列表、进度、结果、恢复、删除


def runs(ctx: ServiceContext, principal_id: str) -> RunList:
    """本人的运行意图（包括还没有确认开始的那一个），新的在前；已删除的不列出。"""
    rows = [
        _expired_view(ctx, r) for r in ctx.store.intents(principal_id) if r["stage"] != "deleted"
    ]
    return RunList(
        meta=ctx.meta(),
        runs=tuple(
            run_summary(
                r,
                started=ctx.store.quota_state(r["intent_id"])["used"],
                intent_days=ctx.config.intent_retention_days,
            )
            for r in rows
        ),
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def _visible_run(ctx: ServiceContext, principal_id: str, run_id: str) -> Mapping[str, Any]:
    row = _expired_view(ctx, ctx.store.intent_by_run(principal_id, run_id))
    if row["stage"] == "deleting":
        raise ServiceFailure("deleting")
    if row["stage"] == "deleted":
        raise ServiceFailure("deleted")
    if row["stage"] == "expired":
        raise ServiceFailure("expired", subject=expired_subject(row))
    return row


def progress(ctx: ServiceContext, principal_id: str, run_id: str) -> RunProgress:
    """只读 worker 写下的公开进度快照：查询不会恢复运行、调用规划器或重放历史。排队中或刚开始（第一份快照还没写下）时按这次运行
    保存的设置给一份最小的快照；其他没有快照的阶段没有进度，不编一个。"""
    row = _visible_run(ctx, principal_id, run_id)
    if row["progress_body"] is not None:
        return RunProgress.model_validate_json(row["progress_body"])
    if row["stage"] not in ("queued", "running"):
        raise ServiceFailure("result_unavailable")
    spec = intent_spec(row)
    queued = row["stage"] == "queued"
    return RunProgress(
        meta=ctx.meta(),
        run_id=run_id,
        stage="queued" if queued else "running",
        stage_zh=stage_text("queued")
        if queued
        else t("progress.placeholder.started", "模拟刚开始，第一份进度还没有写下"),
        clones=(),
        cost=CostStatus(
            kind="offline_mock_zero_cost"
            if spec.planner_kind == "mock"
            else "estimated_from_quote",
            calls_used=0,
            call_limit=spec.global_call_limit,
            estimated_cost=None if spec.planner_kind == "mock" else "0",
            note=t("progress.placeholder.cost_queued", "还没有开始：没有发出任何模型请求")
            if queued
            else t("progress.placeholder.cost_started", "刚开始：费用以下一份进度快照为准"),
            currency=spec.currency if spec.planner_kind == "llm" else None,
            price_version=spec.price_version,
            max_cost=spec.max_cost if spec.planner_kind == "llm" else None,
        ),
        resumable=False,
        resume_note=t("progress.placeholder.resume_queued", "还没有开始，没有可以恢复的部分")
        if queued
        else t("progress.placeholder.resume_running", "运行中，不需要恢复"),
        report_available=False,
        updated_at=row["updated_at"],
    )


def result(ctx: ServiceContext, principal_id: str, run_id: str) -> ProductResult:
    row = _visible_run(ctx, principal_id, run_id)
    if row["stage"] != "finished" or row["result_body"] is None:
        raise ServiceFailure("result_unavailable")
    return ProductResult.model_validate_json(row["result_body"])


def resume(ctx: ServiceContext, principal_id: str, run_id: str) -> RunIntentView:
    row = ctx.store.request_resume(principal_id, run_id)
    return view(ctx, principal_id, row)


def delete_run(ctx: ServiceContext, principal_id: str, run_id: str) -> DeletionReceipt:
    row = ctx.store.intent_by_run(principal_id, run_id)
    row = ctx.store.request_delete(principal_id, row["intent_id"])
    return DeletionReceipt(
        meta=ctx.meta(),
        scope="run",
        target_ref=run_id,
        status="deleted" if row["stage"] == "deleted" else "deleting",
        note_zh=t(
            "deletion.run.note",
            "这次运行已经不能查看，正在安全收尾：不再批准新的模型请求，已经发出的请求可能完成并记账（不能撤回），收尾后清掉内容",
        ),
        kept_zh=kept_after_delete(),
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def delete_all(ctx: ServiceContext, principal_id: str) -> DeletionReceipt:
    ctx.store.delete_all(principal_id)
    return DeletionReceipt(
        meta=ctx.meta(),
        scope="all_data",
        target_ref=None,
        status="deleting",
        note_zh=t(
            "deletion.all.note",
            "你的草稿、审阅、确认与全部运行都在删除：运行中的模拟不再批准新的模型请求，收尾后清掉内容；之前已经开始、还没有完成的"
            "审阅或确认不会再写回。专属链接仍然有效",
        ),
        kept_zh=kept_after_delete(),
        quota=quota_view(ctx.store.quota(principal_id)),
    )
