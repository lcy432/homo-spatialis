"""服务的业务操作（API 调用它们；它们调用 E3 的正式入口与服务库，不调用 ``chdir``、不推进流程、不调用规划器）。

* 草稿：服务器分配编号与修订号；带版本保存，旧版本冲突，不覆盖新答案。
* 审阅：``product.answers.review_draft``，绑定草稿编号、修订号、内容与所用资料包。
* 确认画像：只收审阅引用与知悉项；服务器重新校验后调用 ``product.answers.confirm_profile``（不接受浏览器构造的确认画像）。
* 运行意图：一份确认画像 + 服务端的正式运行设置；排队做运行前检查（worker 做，不扣次数）。
* 确认开始：复用 ``product.bridge.submission_problems`` 的核对，稳定运行编号用 ``product.bridge.run_id_for``——与之后 LangGraph 登记
  用的编号一致；预留次数、排队在服务库的一个短事务里完成。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.product_contracts import (
    AgeAnswer,
    AllocationAnswer,
    CashAnswer,
    CitiesAnswer,
    ConfirmedProfile,
    ConfirmedRef,
    CostStatus,
    DeletionReceipt,
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
    ReviewReply,
    RunIntentCreateRequest,
    RunIntentView,
    RunList,
    RunProgress,
    RunReceipt,
    RunSubmission,
    RunSubmitReply,
    RunSubmitRequest,
    SessionView,
    UserInputDraft,
)
from product.answers import (
    ConfirmationRefusedError,
    confirm_profile,
    confirmation_problems,
    draft_fingerprint,
    issue,
    review_draft,
)
from product.bridge import run_id_for, submission_problems
from product.capabilities import build_capabilities
from product.execution import ExecutionSpec, build_execution_spec, service_pack
from product.pack import DataPack, load_pack
from product.service.clock import Clock, system_clock
from product.service.config import REPO, ServiceConfig
from product.service.errors import ServiceFailure
from product.service.store import INTENT_CONTENT, ServiceStore, intent_due
from product.service.tokens import csrf_for, digest, server_key
from product.service.views import (
    STAGE_ZH,
    draft_view,
    intent_view,
    quota_view,
    rules_view,
    run_summary,
)

KEPT_AFTER_DELETE = (
    "不透明的服务身份与运行编号",
    "使用次数的记录（删除不会重置次数）",
    "必要的费用摘要（发送不确定的金额照原样保留，不改成 0）",
    "删除时间与防止旧请求复活的记录",
)


@dataclass
class ServiceContext:
    """一个服务实例共用的东西：配置、服务库、时钟、HMAC 密钥、当前的资料包与正式运行设置。"""

    config: ServiceConfig
    store: ServiceStore
    clock: Clock
    repo: Path
    data_root: Path
    key: bytes
    pack: DataPack
    spec: ExecutionSpec

    @classmethod
    def open(
        cls, config: ServiceConfig, *, clock: Clock = system_clock, repo: Path = REPO
    ) -> ServiceContext:
        data_root = config.data_root(repo)
        store = ServiceStore(data_root / "service.sqlite", clock, runs_total=config.runs_total)
        key = server_key(data_root / "server.key")
        pack_file = (repo / config.pack_path).resolve()
        file_pack = load_pack(pack_file)
        if config.profile != "test" and file_pack.body.status != "real_data_frozen":
            # 生产入口显式配置 E3B 资料包，不回退到合成资料包
            raise ValueError(f"{config.pack_path} 不是冻结的真实资料包：服务不使用合成资料包")
        spec = build_execution_spec(
            pack=file_pack,
            pack_path=config.pack_path,
            planner=config.planner,
            cities=config.cities,
            repeat_indexes=tuple(config.repeat_indexes),
            years=config.years,
            flash_config=repo / config.flash_config,
            max_cost_cny=config.max_cost_cny,
        )
        return cls(
            config=config,
            store=store,
            clock=clock,
            repo=repo,
            data_root=data_root,
            key=key,
            pack=service_pack(pack_file, spec),
            spec=spec,
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


# ---------------------------------------------------------------------- 会话与能力


def session_view(
    ctx: ServiceContext, principal_id: str, session_secret: str, expires: str
) -> SessionView:
    return SessionView(
        meta=ctx.meta(),
        session_expires_at=expires,
        csrf_token=ctx.csrf(session_secret),
        quota=quota_view(ctx.store.quota(principal_id)),
        rules=rules_view(ctx.config, ctx.spec),
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
    """一份空白草稿：全部未答（未答不是 0）；城市按服务规则预先选好全部六个都会区。"""
    b = ctx.pack.body
    return UserInputDraft(
        meta=ctx.meta(),
        draft_id=draft_id,
        revision=revision,
        cities=CitiesAnswer(state="provided", city_refs=tuple(ctx.spec.cities)),
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


def create_draft(ctx: ServiceContext, principal_id: str) -> DraftView:
    row = ctx.store.create_draft(
        principal_id,
        lambda draft_id: blank_draft(ctx, draft_id).model_dump_json(),
        ctx.config.draft_retention_days,
    )
    return draft_view(ctx.meta(), row)


def get_draft(ctx: ServiceContext, principal_id: str, draft_id: str) -> DraftView:
    return draft_view(ctx.meta(), ctx.store.draft(principal_id, draft_id))


def save_draft(
    ctx: ServiceContext, principal_id: str, draft_id: str, req: DraftSaveRequest
) -> DraftView:
    if req.draft.draft_id != draft_id or req.draft.revision != req.expected_revision:
        raise ServiceFailure("invalid_request")
    row = ctx.store.save_draft(
        principal_id,
        draft_id,
        req.expected_revision,
        lambda revision: req.draft.model_copy(update={"revision": revision}).model_dump_json(),
        ctx.config.draft_retention_days,
    )
    return draft_view(ctx.meta(), row)


def delete_draft(ctx: ServiceContext, principal_id: str, draft_id: str) -> DeletionReceipt:
    ctx.store.delete_draft(principal_id, draft_id)
    return DeletionReceipt(
        meta=ctx.meta(),
        scope="draft",
        target_ref=draft_id,
        status="deleted",
        note_zh="草稿与它的审阅已经删除；已经确认的画像与运行不受影响",
        kept_zh=(),
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def review(ctx: ServiceContext, principal_id: str, draft_id: str) -> ReviewReply:
    row = ctx.store.draft(principal_id, draft_id)
    draft = UserInputDraft.model_validate_json(row["body"])
    result = review_draft(ctx.pack, draft)
    ctx.store.save_review(
        principal_id,
        review_id=result.review_id,
        draft_id=draft_id,
        revision=row["revision"],
        draft_fingerprint=draft_fingerprint(draft),
        pack_fingerprint=ctx.pack.fingerprint,
        body=result.model_dump_json(),
    )
    return ReviewReply(meta=ctx.meta(), review=result, reviewed_revision=row["revision"])


def confirm(
    ctx: ServiceContext, principal_id: str, req: ProfileConfirmRequest
) -> ProfileConfirmReply:
    """确认画像。服务器按保存的草稿与审阅重新校验：审阅之后草稿改过、资料包换过或引用对不上都拒绝。"""
    rev = ctx.store.review(principal_id, req.review_id)
    row = ctx.store.draft(principal_id, req.draft_id)
    draft = UserInputDraft.model_validate_json(row["body"])
    stale = (
        rev["draft_id"] != req.draft_id
        or rev["draft_revision"] != req.draft_revision
        or row["revision"] != req.draft_revision
        or rev["draft_fingerprint"] != draft_fingerprint(draft)
        or rev["pack_fingerprint"] != ctx.pack.fingerprint
    )
    if stale:
        return ProfileConfirmReply(
            meta=ctx.meta(),
            status="refused",
            issues=(
                issue(
                    "review_stale",
                    "review_id",
                    "确认引用的审阅不是当前这一版草稿的审阅（草稿或资料包在审阅之后变了）",
                    "没有确认",
                    "重新审阅当前的草稿后再确认",
                ),
            ),
        )
    try:
        confirmed = confirm_profile(
            ctx.pack,
            draft,
            person_ref=ctx.person_ref(principal_id),
            acknowledged=req.acknowledged,
        )
    except ConfirmationRefusedError as exc:
        return ProfileConfirmReply(meta=ctx.meta(), status="refused", issues=exc.issues)
    ctx.store.save_profile(
        principal_id,
        confirmed_id=confirmed.confirmed_id,
        draft_id=req.draft_id,
        revision=req.draft_revision,
        review_id=req.review_id,
        fingerprint=confirmed.content_fingerprint,
        body=confirmed.model_dump_json(),
    )
    return ProfileConfirmReply(meta=ctx.meta(), status="confirmed", confirmed=confirmed)


# ---------------------------------------------------------------------- 运行意图


def _confirmed(ctx: ServiceContext, principal_id: str, confirmed_id: str) -> ConfirmedProfile:
    row = ctx.store.profile(principal_id, confirmed_id)
    return ConfirmedProfile.model_validate_json(row["body"])


def view(ctx: ServiceContext, principal_id: str, row: Mapping[str, Any]) -> RunIntentView:
    return intent_view(ctx.meta(), _expired_view(ctx, row), ctx.store.quota(principal_id))


def _expired_view(ctx: ServiceContext, row: Mapping[str, Any]) -> Mapping[str, Any]:
    """API 访问时就按到期规则隐藏（不等 worker 的清理跑过）：到期的显示为已过期，内容不再返回。只读，不写库。"""
    if not intent_due(row, ctx.store.now(), ctx.config.intent_retention_days):
        return row
    data = dict(row)
    data.update({c: None for c in INTENT_CONTENT}, stage="expired")
    return data


def create_intent(
    ctx: ServiceContext, principal_id: str, req: RunIntentCreateRequest
) -> tuple[RunIntentView, bool]:
    confirmed = _confirmed(ctx, principal_id, req.confirmed_id)
    if set(confirmed.cities) != set(ctx.spec.cities):
        raise ServiceFailure("run_scale_fixed")
    if confirmation_problems(ctx.pack, confirmed):
        raise ServiceFailure("preflight_stale")
    row, created = ctx.store.create_intent(
        principal_id,
        confirmed_id=confirmed.confirmed_id,
        confirmed_fingerprint=confirmed.content_fingerprint,
        execution_fingerprint=ctx.spec.fingerprint,
        workflow_key=lambda intent_id: ctx.workflow_key(principal_id, intent_id),
    )
    return view(ctx, principal_id, row), created


def get_intent(ctx: ServiceContext, principal_id: str, intent_id: str) -> RunIntentView:
    return view(ctx, principal_id, ctx.store.intent(principal_id, intent_id))


def submit(
    ctx: ServiceContext, principal_id: str, intent_id: str, req: RunSubmitRequest
) -> RunSubmitReply:
    """确认开始。核对在事务之外做（读快照里的资料包、复用提交核对）；接受、预留、写运行编号与排队在一个短事务里。"""
    row = ctx.store.intent(principal_id, intent_id)
    if row["run_id"] is None and row["stage"] == "awaiting_confirmation":
        if row["execution_fingerprint"] != ctx.spec.fingerprint:
            # 服务的运行设置或资料包更新了：尚未确认的意图要按新设置重新检查（不扣次数）
            ctx.store.update_intent(
                intent_id,
                only_stages=("awaiting_confirmation",),
                stage="checking",
                execution_fingerprint=ctx.spec.fingerprint,
                preflight_fingerprint=None,
                preflight_body=None,
                last_activity_at=ctx.store.now(),
            )
            ctx.store.enqueue("preflight", intent_id)
            raise ServiceFailure("preflight_stale")
        if ctx.spec.planner_kind == "llm" and not ctx.config.live_calls_enabled:
            raise ServiceFailure("paid_calls_disabled")
        if row["preflight_fingerprint"] != req.confirm_with or row["preflight_body"] is None:
            raise ServiceFailure("preflight_stale")
        pre = ProductPreflight.model_validate_json(row["preflight_body"])
        confirmed = _confirmed(ctx, principal_id, row["confirmed_id"])
        try:
            pack = service_pack(ctx.run_dir(intent_id) / "ws" / ctx.spec.pack_path, ctx.spec)
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
        run_id = run_id_for(submission)
    else:
        run_id = row["run_id"] or ""
    status, run_id = ctx.store.submit_intent(
        principal_id,
        intent_id,
        confirm_with=req.confirm_with,
        key=req.idempotency_key,
        run_id=run_id,
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
        runs=tuple(run_summary(r) for r in rows),
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def _visible_run(ctx: ServiceContext, principal_id: str, run_id: str) -> Mapping[str, Any]:
    row = _expired_view(ctx, ctx.store.intent_by_run(principal_id, run_id))
    if row["stage"] == "deleting":
        raise ServiceFailure("deleting")
    if row["stage"] == "deleted":
        raise ServiceFailure("deleted")
    if row["stage"] == "expired":
        raise ServiceFailure("expired")
    return row


def _run_spec(ctx: ServiceContext, intent_id: str) -> ExecutionSpec:
    """这次运行绑定的正式运行设置（排队检查时写进工作目录的本次输入；只读，绝对路径）。读不到时用服务现在的设置。"""
    path = ctx.run_dir(intent_id) / "ws" / "workflow" / "requests" / "svc.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("execution")
        return ExecutionSpec.model_validate(raw) if raw else ctx.spec
    except (OSError, ValueError):
        return ctx.spec


def progress(ctx: ServiceContext, principal_id: str, run_id: str) -> RunProgress:
    """只读 worker 写下的公开进度快照：查询不会恢复运行、调用规划器或重放历史。排队中或刚开始（第一份快照还没写下）时给一份
    最小的快照；其他没有快照的阶段没有进度，不编一个。"""
    row = _visible_run(ctx, principal_id, run_id)
    if row["progress_body"] is not None:
        return RunProgress.model_validate_json(row["progress_body"])
    if row["stage"] not in ("queued", "running"):
        raise ServiceFailure("result_unavailable")
    spec = _run_spec(ctx, row["intent_id"])
    queued = row["stage"] == "queued"
    return RunProgress(
        meta=ctx.meta(),
        run_id=run_id,
        stage="queued" if queued else "running",
        stage_zh=STAGE_ZH["queued"] if queued else "模拟刚开始，第一份进度还没有写下",
        clones=(),
        cost=CostStatus(
            kind="offline_mock_zero_cost"
            if spec.planner_kind == "mock"
            else "estimated_from_quote",
            calls_used=0,
            call_limit=spec.global_call_limit,
            estimated_cost=None if spec.planner_kind == "mock" else "0",
            note="还没有开始：没有发出任何模型请求"
            if queued
            else "刚开始：费用以下一份进度快照为准",
            currency=spec.currency,
            price_version=spec.price_version,
            max_cost=spec.max_cost,
        ),
        resumable=False,
        resume_note="还没有开始，没有可以恢复的部分" if queued else "运行中，不需要恢复",
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
        note_zh=(
            "这次运行已经不能查看，正在安全收尾：不再批准新的模型请求，已经发出的请求可能完成并记账（不能撤回），收尾后清掉内容"
        ),
        kept_zh=KEPT_AFTER_DELETE,
        quota=quota_view(ctx.store.quota(principal_id)),
    )


def delete_all(ctx: ServiceContext, principal_id: str) -> DeletionReceipt:
    ctx.store.delete_all(principal_id)
    return DeletionReceipt(
        meta=ctx.meta(),
        scope="all_data",
        target_ref=None,
        status="deleting",
        note_zh=(
            "你的草稿、审阅、确认与全部运行都在删除：运行中的模拟不再批准新的模型请求，收尾后清掉内容。专属链接仍然有效"
        ),
        kept_zh=KEPT_AFTER_DELETE,
        quota=quota_view(ctx.store.quota(principal_id)),
    )
