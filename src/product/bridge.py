"""离线运行桥接（E3-A 模块 E）：提交 → 运行 → 进度 → 结果，全部经过 E1 已有的运行器。

* **提交**（``submit``）：核对确认画像与预检查的内容身份，按幂等键登记在本地的提交记录里。同一个键、同一份预检查 → 返回原运行 ID
  （``duplicate_same_run``，不再生成一批分身）；同一个键配了另一份预检查 → 拒绝；确认或预检查失效 → 拒绝。这是本地离线桥接，
  **不是**托管服务：持久化、并发、认证与断线恢复的线上实现留 E3-C / E5-C。
* **运行**（``run_offline``）：用运行配置声明的离线规划器（产品 mock，零网络、零费用），交给 ``orchestration.life_runner.start_run``：
  年度调用、执行前检查与有限修订、有限记忆、归档与冻结、E1-6A 评价与 E1-6B 汇总都沿用已有入口，不另写一条生活链。
* **进度**（``progress``）：从运行归档的进度记录投影成产品的 ``RunProgress``（断线后用运行 ID 查询，不重发规划）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentlayer.life_mock import mock_planner_for
from common.life_planning_contracts import CLONE_RUN_STATUS_TEXT
from common.life_planning_contracts import RunProgress as E1RunProgress
from common.product_contracts import (
    CloneProgress,
    ConfirmedProfile,
    CostStatus,
    ProductPreflight,
    RunProgress,
    RunReceipt,
    RunSubmission,
    SampleLabel,
)
from orchestration.life_archive import RunArchive
from orchestration.life_runner import RunOutcome, RunWorld, start_run
from product.answers import confirmation_problems, issue
from product.pack import DataPack
from product.preflight import preflight_problems
from product.public_errors import progress_pause
from product.results import STAGE_TEXT

REGISTRY = "submissions.json"


def run_id_for(submission: RunSubmission) -> str:
    """运行 ID 由幂等键与预检查身份确定（同一提交得到同一 ID）。"""
    digest = hashlib.sha256(
        f"{submission.idempotency_key}|{submission.preflight.preflight_fingerprint}".encode()
    ).hexdigest()
    return f"run-e3a-{digest[:16]}"


def submit(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    pre: ProductPreflight,
    submission: RunSubmission,
    registry_dir: Path,
    *,
    sample: SampleLabel | None = None,
) -> RunReceipt:
    """登记一次提交（本地离线）。不运行生活；运行由调用方在拿到 ``accepted`` 之后做。"""
    reasons = []
    if (submission.confirmed.confirmed_id, submission.confirmed.content_fingerprint) != (
        confirmed.confirmed_id,
        confirmed.content_fingerprint,
    ):
        reasons.append(
            issue(
                "submission_confirmed_mismatch",
                "confirmed",
                "提交引用的确认画像不是当前这一份",
                "不能运行",
                "重新确认后再提交",
            )
        )
    if (submission.preflight.preflight_id, submission.preflight.preflight_fingerprint) != (
        pre.preflight_id,
        pre.preflight_fingerprint,
    ):
        reasons.append(
            issue(
                "submission_preflight_mismatch",
                "preflight",
                "提交引用的预检查不是当前这一份（输入、配置或城市变了要重新检查）",
                "不能运行",
                "重新预检查后再提交",
            )
        )
    reasons += confirmation_problems(pack, confirmed)
    reasons += preflight_problems(pack, confirmed, pre)
    path = Path(registry_dir) / REGISTRY
    registry: dict[str, dict[str, str]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    key = submission.idempotency_key
    previous = registry.get(key)
    if previous is not None and previous["preflight_fingerprint"] != pre.preflight_fingerprint:
        reasons.append(
            issue(
                "idempotency_key_reused",
                "idempotency_key",
                "这个幂等键已经用于另一份预检查：不能用同一个键提交不同的运行",
                "不能运行",
                "为新的预检查生成新的幂等键",
            )
        )
    if reasons:
        return RunReceipt(
            meta=pack.meta(sample),
            status="rejected",
            idempotency_key=key,
            reasons=tuple(reasons),
            service="offline_bridge",
        )
    if previous is not None:
        return RunReceipt(
            meta=pack.meta(sample),
            status="duplicate_same_run",
            run_id=previous["run_id"],
            idempotency_key=key,
            service="offline_bridge",
        )
    rid = run_id_for(submission)
    registry[key] = {"run_id": rid, "preflight_fingerprint": pre.preflight_fingerprint}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )
    return RunReceipt(
        meta=pack.meta(sample),
        status="accepted",
        run_id=rid,
        idempotency_key=key,
        service="offline_bridge",
    )


def run_offline(
    world: RunWorld,
    run_dir: Path,
    run_id: str,
    *,
    stop_after_calls: int | None = None,
) -> RunOutcome:
    """用运行配置声明的产品 mock 跑完整批（零网络）。真实模型规划器在这里被拒绝：本轮不新增任何真实调用。"""
    spec = world.config.planner
    if spec.identity.kind != "mock":
        raise ValueError("E3-A 的离线桥接只接受 mock 规划器：本轮不新增真实模型调用")
    planner = mock_planner_for(spec)
    return start_run(
        world, planner, Path(run_dir), run_id=run_id, stop_after_calls=stop_after_calls
    )


def progress(pack: DataPack, run_dir: Path, *, sample: SampleLabel | None = None) -> RunProgress:
    """从运行归档的进度记录投影出产品进度（断线后按运行 ID 查询；不重发规划）。"""
    archive = RunArchive(Path(run_dir), create=False)
    p = E1RunProgress.model_validate(archive.read_json("progress.json"))
    report = (Path(run_dir) / "reports" / "arm.main.json").exists()
    resumable = p.status in ("running", "interrupted", "halted")
    return RunProgress(
        meta=pack.meta(sample),
        run_id=p.run_id,
        stage=p.status,  # type: ignore[arg-type]
        stage_zh=STAGE_TEXT.get(p.status, p.status),
        clones=tuple(
            CloneProgress(
                clone_ref=c.clone_id,
                city_ref=str(c.city),
                status=c.status,
                status_zh=CLONE_RUN_STATUS_TEXT[c.status],
                years_committed=c.years_committed,
            )
            for c in p.clones
        ),
        # E3-A 收尾 F5：按进度的结构化状态生成说明，不透传 pause_reason 原文（那里可能有异常内容）
        pause=progress_pause(p),
        cost=CostStatus(
            kind="offline_mock_zero_cost",
            calls_used=p.calls_used,
            call_limit=p.call_limit,
            note="离线 mock：没有网络调用，费用为 0（真实模型运行会写“按报价估算”，以供应商账单为准）",
        ),
        resumable=resumable,
        resume_note=(
            "可以从年度边界或两份候选之间恢复（E1 的恢复入口逐条核对归档，尝试次数与调用次数不清零）"
            if resumable
            else "这次运行已经结束，没有可以恢复的部分"
        ),
        report_available=report,
    )
